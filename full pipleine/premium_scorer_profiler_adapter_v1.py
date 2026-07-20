#!/usr/bin/env python3
"""
premium_scorer_profiler_adapter_v1.py
=====================================

Deterministic Premium scorer adapter for VA/ST.ELLA.

This module keeps scorer_engine_v2_1_6.py as the base scorer, but injects
Premium detector profiler metrics into detector_metric_profile before scoring.
It then attaches a transparent calibrated score profile using config-driven
caps, offsets, and bounded upward rescue.

It does NOT call an LLM. Any LLM-derived metrics must already exist in
`premium_metric_profile`, produced by premium_metric_profiler_v1.py.

Usage
-----
python premium_scorer_profiler_adapter_v1.py \
  --input detector_batch_profiled.json \
  --output premium_scored.json \
  --scorer-path scorer_engine_v2_1_6.py \
  --config premium_scorer_calibration_config_v1.json \
  --pretty
"""
from __future__ import annotations

import argparse
import copy
import csv
import importlib.util
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Local import works when this file and premium_metric_profiler_v1.py are in the same directory.
try:
    from premium_metric_profiler_v1 import profile_to_dmp, merge_mapped_dmp, validate_profile
except Exception:  # pragma: no cover
    import sys
    sys.path.append(str(Path(__file__).resolve().parent))
    from premium_metric_profiler_v1 import profile_to_dmp, merge_mapped_dmp, validate_profile

ADAPTER_VERSION = "premium_scorer_profiler_adapter_v1"
RUBRICS = ("task_response", "coherence_cohesion", "lexical_resource", "grammar")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clamp(x: Any, lo: float = 0.0, hi: float = 9.0, default: float = 0.0) -> float:
    try:
        return max(lo, min(hi, float(x)))
    except Exception:
        return default


def clamp01(x: Any, default: float = 0.0) -> float:
    return clamp(x, 0.0, 1.0, default)


def round_half(x: Any) -> float:
    try:
        return round(float(x) * 2) / 2
    except Exception:
        return 0.0


def nested_get(d: Any, *keys: str, default: Any = None) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def load_json(path: Optional[str], default: Any = None) -> Any:
    if not path:
        return default
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_scorer_module(path: str):
    p = Path(path)
    if not p.exists():
        # Try same directory as this adapter.
        p = Path(__file__).resolve().parent / path
    spec = importlib.util.spec_from_file_location("base_premium_scorer", str(p))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import scorer module from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    if not hasattr(mod, "score") or not hasattr(mod, "score_batch"):
        raise RuntimeError("Scorer module must expose score() and score_batch().")
    return mod


def default_config() -> Dict[str, Any]:
    cfg_path = Path(__file__).resolve().parent / "premium_scorer_calibration_config_v1.json"
    if cfg_path.exists():
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    return {
        "criterion_offsets": {r: 0.0 for r in RUBRICS},
        "holistic_offset": 0.0,
        "caps": {},
        "upward_rescue": {"enabled": False},
        "rounding": {"criterion_bands_allow_half": True, "overall_band_allow_half": True},
    }


def extract_profile(detector_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    p = detector_payload.get("premium_metric_profile") or nested_get(detector_payload, "scorer_payload", "premium_metric_profile")
    if isinstance(p, dict):
        return validate_profile(p)
    return None


def extract_metadata(detector_payload: Dict[str, Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for c in [
        nested_get(detector_payload, "scorer_payload", "metadata"),
        nested_get(detector_payload, "detector_metric_profile", "shared"),
        detector_payload.get("generated_metadata"),
    ]:
        if not isinstance(c, dict):
            continue
        for src, dst in [("word_count", "word_count"), ("sentence_count", "sentence_count"), ("paragraph_count", "paragraph_count")]:
            if dst not in out and c.get(src) is not None:
                try:
                    out[dst] = int(c[src])
                except Exception:
                    pass
    return out


def inject_profile_metrics(detector_payload: Dict[str, Any], overwrite: bool = True) -> Dict[str, Any]:
    out = copy.deepcopy(detector_payload)
    profile = extract_profile(out)
    if not profile:
        out.setdefault("qa", {})["premium_scorer_profiler_adapter_v1"] = {
            "profile_present": False,
            "mapped_to_dmp": False,
            "warning": "premium_metric_profile absent; base scorer will use existing detector_metric_profile",
        }
        return out
    mapped = profile_to_dmp(profile, extract_metadata(out))
    out.setdefault("scorer_payload", {})["premium_metric_profile_mapped_metrics"] = mapped
    merge_mapped_dmp(out, mapped, overwrite=overwrite)
    out.setdefault("qa", {})["premium_scorer_profiler_adapter_v1"] = {
        "profile_present": True,
        "mapped_to_dmp": True,
        "overwrite_existing_detector_metrics": overwrite,
        "adapter_version": ADAPTER_VERSION,
    }
    return out


def base_bands(score_output: Dict[str, Any]) -> Dict[str, float]:
    rub = nested_get(score_output, "score_profile", "rubrics") or {}
    bands = {r: clamp(nested_get(rub, r, "band"), 0.0, 9.0, 0.0) for r in RUBRICS}
    bands["overall"] = clamp(nested_get(score_output, "score_profile", "overall_band_estimate"), 0.0, 9.0, 0.0)
    return bands


def row_count(detector_payload: Dict[str, Any], rubric: Optional[str] = None) -> int:
    rows = nested_get(detector_payload, "scorer_payload", "chargeable_detector_rows") or detector_payload.get("student_rows") or []
    if not isinstance(rows, list):
        return 0
    if rubric is None:
        return len(rows)
    return sum(1 for r in rows if str(r.get("rubric") or r.get("primary_rubric") or "").lower() == rubric)


def get_profile_metric(profile: Optional[Dict[str, Any]], section: str, key: str, default: float = 0.0) -> float:
    if not isinstance(profile, dict):
        return default
    return clamp01(nested_get(profile, section, key), default)


def apply_offsets(bands: Dict[str, float], config: Dict[str, Any]) -> Tuple[Dict[str, float], List[Dict[str, Any]]]:
    out = dict(bands)
    audit: List[Dict[str, Any]] = []
    offsets = config.get("criterion_offsets") or {}
    for r in RUBRICS:
        off = clamp(offsets.get(r, 0.0), -2.0, 2.0, 0.0)
        if off:
            before = out[r]
            out[r] = round_half(out[r] + off)
            audit.append({"action": "criterion_offset", "rubric": r, "before": before, "after": out[r], "offset": off})
    h_off = clamp(config.get("holistic_offset", 0.0), -2.0, 2.0, 0.0)
    if h_off:
        before = out["overall"]
        out["overall"] = round_half(out["overall"] + h_off)
        audit.append({"action": "holistic_offset", "before": before, "after": out["overall"], "offset": h_off})
    return out, audit


def apply_caps_and_rescue(bands: Dict[str, float], detector_payload: Dict[str, Any], config: Dict[str, Any]) -> Tuple[Dict[str, float], List[Dict[str, Any]]]:
    out = dict(bands)
    audit: List[Dict[str, Any]] = []
    profile = extract_profile(detector_payload)
    caps = config.get("caps") or {}

    def cap_band(key: str, target: str, cap: float, reason: str, extra: Optional[Dict[str, Any]] = None) -> None:
        before = out[target]
        if before > cap:
            out[target] = cap
            audit.append({"action": key, "target": target, "before": before, "after": cap, "reason": reason, "evidence": extra or {}})

    cc_rule = caps.get("cc_zero_error_not_proof") or {}
    if cc_rule.get("enabled", False):
        if out["coherence_cohesion"] >= float(cc_rule.get("if_band_at_least", 5.5)) and row_count(detector_payload, "coherence_cohesion") == 0:
            gp = get_profile_metric(profile, "coherence_cohesion", "global_progression", 0.55)
            pr = get_profile_metric(profile, "coherence_cohesion", "paragraph_role_clarity", 0.55)
            if gp <= float(cc_rule.get("max_global_progression", 0.52)) or pr <= float(cc_rule.get("max_paragraph_role_clarity", 0.52)):
                cap_band("cc_zero_error_not_proof_cap", "coherence_cohesion", float(cc_rule.get("cap", 5.0)), "CC detector silence is not positive CC evidence", {"global_progression": gp, "paragraph_role_clarity": pr})

    gra_rule = caps.get("gra_range_control_cap") or {}
    if gra_rule.get("enabled", False):
        if out["grammar"] >= float(gra_rule.get("if_band_at_least", 6.0)):
            rg = get_profile_metric(profile, "grammar", "grammar_range_control", 0.55)
            cx = get_profile_metric(profile, "grammar", "complex_structure_success", 0.55)
            if rg <= float(gra_rule.get("max_grammar_range_control", 0.54)) or cx <= float(gra_rule.get("max_complex_structure_success", 0.50)):
                cap_band("gra_range_control_cap", "grammar", float(gra_rule.get("cap", 5.0)), "GRA score requires real range/control evidence", {"grammar_range_control": rg, "complex_structure_success": cx})

    lr_rule = caps.get("lr_naturalness_cap") or {}
    if lr_rule.get("enabled", False):
        if out["lexical_resource"] >= float(lr_rule.get("if_band_at_least", 6.0)):
            wp = get_profile_metric(profile, "lexical_resource", "word_choice_precision", 0.55)
            cn = get_profile_metric(profile, "lexical_resource", "collocation_naturalness", 0.55)
            pn = get_profile_metric(profile, "lexical_resource", "phrase_naturalness", 0.55)
            if (wp <= float(lr_rule.get("max_word_choice_precision", 0.50)) or
                cn <= float(lr_rule.get("max_collocation_naturalness", 0.50)) or
                pn <= float(lr_rule.get("max_phrase_naturalness", 0.50))):
                cap_band("lr_naturalness_cap", "lexical_resource", float(lr_rule.get("cap", 5.5)), "LR score requires precision/collocation/naturalness evidence", {"word_choice_precision": wp, "collocation_naturalness": cn, "phrase_naturalness": pn})

    weak_rule = caps.get("weak_writing_global_cap") or {}
    if weak_rule.get("enabled", False):
        weak = get_profile_metric(profile, "shared", "weak_writing_probability", 0.0)
        if out["overall"] >= float(weak_rule.get("if_overall_at_least", 6.5)) and weak >= float(weak_rule.get("min_weak_writing_probability", 0.68)):
            cap_band("weak_writing_global_cap", "overall", float(weak_rule.get("cap", 6.0)), "High weak-writing probability blocks high holistic score", {"weak_writing_probability": weak})

    high_rule = caps.get("high_band_evidence_cap") or {}
    if high_rule.get("enabled", False):
        high = get_profile_metric(profile, "shared", "high_band_readiness", 0.0)
        weak = get_profile_metric(profile, "shared", "weak_writing_probability", 0.0)
        if out["overall"] >= float(high_rule.get("if_overall_at_least", 7.0)):
            if high < float(high_rule.get("min_high_band_readiness", 0.68)) or weak > float(high_rule.get("max_weak_writing_probability", 0.40)):
                cap_band("high_band_evidence_cap", "overall", float(high_rule.get("cap", 6.5)), "Band 7 requires positive high-band evidence and low weak-writing risk", {"high_band_readiness": high, "weak_writing_probability": weak})

    # Upward rescue is bounded and applied after caps only to holistic.
    rescue = config.get("upward_rescue") or {}
    if rescue.get("enabled", False) and profile:
        crit = rescue.get("criteria") or {}
        ok = (
            get_profile_metric(profile, "shared", "confidence", 0.0) >= float(crit.get("min_confidence", 0.78)) and
            get_profile_metric(profile, "shared", "high_band_readiness", 0.0) >= float(crit.get("min_high_band_readiness", 0.72)) and
            get_profile_metric(profile, "shared", "weak_writing_probability", 1.0) <= float(crit.get("max_weak_writing_probability", 0.30)) and
            get_profile_metric(profile, "shared", "semantic_recoverability", 0.0) >= float(crit.get("min_semantic_recoverability", 0.72)) and
            get_profile_metric(profile, "task_response", "idea_development_depth", 0.0) >= float(crit.get("min_argument_development", 0.62)) and
            get_profile_metric(profile, "grammar", "sentence_control_stability", 0.0) >= float(crit.get("min_sentence_control", 0.62))
        )
        if ok:
            before = out["overall"]
            cap = float(rescue.get("cap_band", 7.0))
            delta = float(rescue.get("max_delta", 0.5))
            after = min(cap, round_half(before + delta))
            if after > before:
                out["overall"] = after
                audit.append({"action": "bounded_upward_rescue", "target": "overall", "before": before, "after": after, "reason": "strong detector-side premium profile", "max_delta": delta})

    return out, audit


def recompute_overall_from_criteria(bands: Dict[str, float]) -> float:
    return round_half(sum(bands[r] for r in RUBRICS) / len(RUBRICS))


def calibrated_profile(score_output: Dict[str, Any], detector_payload: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    base = base_bands(score_output)
    bands, audit1 = apply_offsets(base, config)
    # If no explicit holistic offset, tie overall to criterion average before caps/rescue.
    if not config.get("holistic_offset"):
        bands["overall"] = recompute_overall_from_criteria(bands)
    bands, audit2 = apply_caps_and_rescue(bands, detector_payload, config)
    # After rubric caps, holistic should not exceed average+0.5 unless explicit rescue/cap audit did so.
    avg = recompute_overall_from_criteria(bands)
    if bands["overall"] > avg + 0.5:
        before = bands["overall"]
        bands["overall"] = avg + 0.5
        audit2.append({"action": "holistic_consistency_cap", "target": "overall", "before": before, "after": bands["overall"], "reason": "Holistic cannot drift too far above criterion average"})
    confidence = clamp(nested_get(score_output, "score_profile", "confidence"), 0.0, 1.0, 0.60)
    profile = extract_profile(detector_payload)
    if profile:
        confidence = round((confidence * 0.65 + get_profile_metric(profile, "shared", "confidence", confidence) * 0.35), 3)
    return {
        "version": "premium_calibrated_score_profile_v1",
        "created_at": now_iso(),
        "source": "base_scorer_plus_premium_metric_profile_and_config_rules",
        "base_bands": base,
        "calibrated_bands": {k: round(v, 2) for k, v in bands.items()},
        "overall_band_estimate": round(bands["overall"], 2),
        "rubrics": {r: {"band": round(bands[r], 2), "score": round(bands[r] / 9.0, 4)} for r in RUBRICS},
        "confidence": confidence,
        "audit": audit1 + audit2,
        "config_version": config.get("config_version", "unknown"),
        "policy": config.get("policy", {}),
    }


def score_one(detector_payload: Dict[str, Any], scorer_module: Any, config: Dict[str, Any], overwrite_dmp: bool = True, overwrite_score_profile: bool = False) -> Dict[str, Any]:
    enriched = inject_profile_metrics(detector_payload, overwrite=overwrite_dmp)
    scored = scorer_module.score(enriched)
    cps = calibrated_profile(scored, enriched, config)
    scored["premium_calibrated_score_profile"] = cps
    scored.setdefault("qa", {}).setdefault("premium_scorer_profiler_adapter_v1", {})
    scored["qa"]["premium_scorer_profiler_adapter_v1"].update({
        "adapter_version": ADAPTER_VERSION,
        "premium_metric_profile_present": extract_profile(enriched) is not None,
        "calibrated_profile_attached": True,
        "overwrite_score_profile": overwrite_score_profile,
    })
    if overwrite_score_profile:
        # Keep original for audit and expose calibrated values as score_profile.
        scored["base_score_profile_before_premium_calibration"] = scored.get("score_profile")
        new_sp = copy.deepcopy(scored.get("score_profile") or {})
        for r in RUBRICS:
            new_sp.setdefault("rubrics", {}).setdefault(r, {})
            new_sp["rubrics"][r]["band"] = cps["rubrics"][r]["band"]
            new_sp["rubrics"][r]["band_rounded"] = cps["rubrics"][r]["band"]
            new_sp["rubrics"][r]["score"] = cps["rubrics"][r]["score"]
            new_sp["rubrics"][r]["normalized_score"] = cps["rubrics"][r]["score"]
        new_sp["overall_band_estimate"] = cps["overall_band_estimate"]
        new_sp["confidence"] = cps["confidence"]
        new_sp.setdefault("score_ceiling_flags", []).append("premium_profiler_calibration_applied")
        new_sp["scoring_version"] = "premium_scorer_v2_2_profiler_calibrated"
        scored["score_profile"] = new_sp
    return scored


def score_payload(payload: Dict[str, Any], scorer_module: Any, config: Dict[str, Any], overwrite_dmp: bool = True, overwrite_score_profile: bool = False) -> Dict[str, Any]:
    if isinstance(payload.get("results"), list):
        results = []
        errors = []
        for i, item in enumerate(payload["results"]):
            try:
                results.append(score_one(item, scorer_module, config, overwrite_dmp=overwrite_dmp, overwrite_score_profile=overwrite_score_profile))
            except Exception as e:
                essay_id = nested_get(item, "identity", "essay_id") or str(i)
                errors.append({"index": i, "essay_id": essay_id, "error": str(e)})
        return {
            "schema_version": "PREMIUM_SCORER_PROFILED_BATCH_OUTPUT_V1",
            "batch_meta": {
                "input_count": len(payload["results"]),
                "scored_count": len(results),
                "error_count": len(errors),
                "adapter_version": ADAPTER_VERSION,
                "created_at": now_iso(),
            },
            "results": results,
            "errors": errors,
        }
    return score_one(payload, scorer_module, config, overwrite_dmp=overwrite_dmp, overwrite_score_profile=overwrite_score_profile)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run deterministic Premium scorer with detector-side profiler metrics.")
    ap.add_argument("-i", "--input", required=True)
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--scorer-path", default="scorer_engine_v2_1_6.py")
    ap.add_argument("--config", default=None)
    ap.add_argument("--no-overwrite-dmp", action="store_true")
    ap.add_argument("--overwrite-score-profile", action="store_true", help="Replace score_profile with calibrated score_profile; base profile kept under audit key.")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    cfg = load_json(args.config, default_config()) if args.config else default_config()
    mod = load_scorer_module(args.scorer_path)
    out = score_payload(payload, mod, cfg, overwrite_dmp=not args.no_overwrite_dmp, overwrite_score_profile=args.overwrite_score_profile)
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2 if args.pretty else None), encoding="utf-8")


if __name__ == "__main__":
    main()
