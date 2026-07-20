#!/usr/bin/env python3
"""
Premium Verifier v1.4.3 — Universal Release Gatekeeper.

This verifier is a release-safety layer, not a scorer.
It validates structure, score math, TierGovernor contract, upward leakage,
downward leakage, score jumps, evidence contradictions, task contradictions,
and downstream safety.

Runtime guarantees:
- no external target labels
- no essay-specific release rules
- no score changes
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

VERIFIER_VERSION = "premium_verifier_v1_4_3_universal_gatekeeper"
RUBRICS = ["task_response", "coherence_cohesion", "lexical_resource", "grammar"]

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": VERIFIER_VERSION,
    "score_change_allowed": False,
    "require_tier_governor": True,
    "release_policy": {
        "invalid_output": "block_invalid_output",
        "review_required": "hold_for_review",
        "caution": "release_with_caution",
        "pass": "release"
    },
    "thresholds": {
        "large_governor_jump": 1.0,
        "tier_crossing_jump": 1.0,
        "upward_high_jump_from_max": 6.0,
        "upward_high_jump_to_min": 7.0,
        "downward_large_jump_from_min": 6.5,
        "downward_large_jump_to_max": 5.5,
        "weak_evidence_high_score_weak_probability": 0.48,
        "weak_evidence_high_score_serious_ratio": 0.50,
        "weak_evidence_high_score_local_damage": 0.40,
        "weak_evidence_raw_min_max": 5.0,
        "weak_quality_max": 0.58,
        "high_evidence_low_score_hbr": 0.72,
        "high_evidence_low_score_semantic": 0.66,
        "high_evidence_low_score_quality": 0.62,
        "high_evidence_low_score_local_max": 0.30,
        "high_evidence_low_score_serious_max": 0.35,
        "high_rescue_confirmation_review_min": 10
    },
    "risk_actions": [
        "large_upward_high_jump",
        "large_downward_tier_jump",
        "high_score_with_weak_evidence_contradiction",
        "task_true_fail_high_release",
        "high_rescue_confirmed_but_not_released_high"
    ],
    "display_caution_warnings": [
        "high_boundary_release",
        "b1_boundary_release",
        "large_governor_movement_display"
    ],
    "tracking_caution_warnings": [
        "large_governor_movement",
        "possible_high_underrelease",
        "high_boundary_low_confidence",
        "fallback_or_missing_evidence"
    ]
}


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(obj: Any, path: str, pretty: bool=False) -> None:
    with open(path, "w", encoding="utf-8") as f:
        if pretty:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        else:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def deep_merge(a: Dict[str, Any], b: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = copy.deepcopy(a)
    if not b:
        return out
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def as_results(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("results", "essays", "scored"):
            if isinstance(payload.get(key), list):
                return [x for x in payload[key] if isinstance(x, dict)]
        if "essay_id" in payload:
            return [payload]
    return []


def fnum(x: Any, default: Optional[float]=None) -> Optional[float]:
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def is_int_band(x: Any) -> bool:
    return (isinstance(x, int) and not isinstance(x, bool)) or (isinstance(x, float) and x.is_integer())


def is_half_band(x: Any) -> bool:
    v = fnum(x)
    return v is not None and abs(v * 2 - round(v * 2)) < 1e-9


def round_half(x: float) -> float:
    return round(x * 2.0) / 2.0


def score_group(overall: Optional[float]) -> str:
    if overall is None:
        return "UNKNOWN_SCORE"
    if overall <= 3.5:
        return "A2_OR_LOWER"
    if overall <= 5.0:
        return "B1_WEAK"
    if overall <= 6.5:
        return "B2_MEDIUM"
    return "HIGH_7_PLUS"


def tier_rank(group: str) -> int:
    return {"A2_OR_LOWER": 0, "B1_WEAK": 1, "B2_MEDIUM": 2, "HIGH_7_PLUS": 3}.get(group, -1)


def get_score(record: Dict[str, Any]) -> Tuple[Optional[float], Dict[str, Any]]:
    sp = record.get("score_profile") or {}
    overall = record.get("overall_band") if record.get("overall_band") is not None else sp.get("overall_band_estimate")
    crit = record.get("official_criteria_bands") or record.get("final_criteria_bands") or sp.get("official_criteria_bands") or {}
    return fnum(overall), crit if isinstance(crit, dict) else {}


def recompute(crit: Dict[str, Any]) -> Optional[float]:
    if any(k not in crit or not is_int_band(crit[k]) for k in RUBRICS):
        return None
    return round_half(sum(float(crit[k]) for k in RUBRICS) / 4.0)


def get_tg(record: Dict[str, Any]) -> Dict[str, Any]:
    tg = record.get("tier_governor") or record.get("tier_governor_decision") or {}
    return tg if isinstance(tg, dict) else {}


def diag(tg: Dict[str, Any]) -> Dict[str, Any]:
    d = tg.get("diagnostics") or {}
    return d if isinstance(d, dict) else {}


def feature(tg: Dict[str, Any], name: str, default: Optional[float]=None) -> Optional[float]:
    feats = tg.get("features") or {}
    return fnum(feats.get(name), default)


def text_feature(tg: Dict[str, Any], name: str, default: str="") -> str:
    feats = tg.get("features") or {}
    return str(feats.get(name, default) or default)


def confidence_level(tg: Dict[str, Any]) -> str:
    return str(tg.get("tier_confidence_level") or diag(tg).get("tier_confidence_level") or "low")


def hard_veto_count(tg: Dict[str, Any]) -> int:
    return int(fnum(tg.get("hard_veto_count"), fnum(diag(tg).get("high_hard_veto_count"), 0)) or 0)


def high_rescue_confirmation_count(tg: Dict[str, Any]) -> int:
    return int(fnum(tg.get("high_rescue_confirmation_count"), fnum(diag(tg).get("high_rescue_confirmation_count"), 0)) or 0)


def high_rescue_confirmed(tg: Dict[str, Any]) -> bool:
    return bool(tg.get("high_rescue_confirmed") or diag(tg).get("high_rescue_confirmed"))


def high_rescue_candidate(tg: Dict[str, Any]) -> bool:
    return bool(tg.get("high_rescue_candidate") or diag(tg).get("high_rescue_candidate"))


def caution_category(warnings: List[str], review_flags: List[str], cfg: Dict[str, Any]) -> str:
    if review_flags:
        return "review_caution"
    if any(w in set(cfg.get("tracking_caution_warnings", [])) for w in warnings):
        return "tracking_caution"
    if any(w in set(cfg.get("display_caution_warnings", [])) for w in warnings):
        return "display_caution_only"
    return "none"


def check_one(record: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    eid = str(record.get("essay_id") or record.get("submission_id") or record.get("id") or "")
    overall, crit = get_score(record)
    tg = get_tg(record)

    checks: List[Dict[str, Any]] = []
    blocking: List[str] = []
    review: List[str] = []
    warnings: List[str] = []
    risk: List[str] = []

    # Structural / math contract
    if not crit:
        blocking.append("missing_final_criteria")
    else:
        for k in RUBRICS:
            if not is_int_band(crit.get(k)):
                blocking.append(f"criterion_not_integer__{k}")
    if overall is None or not is_half_band(overall):
        blocking.append("overall_not_half_band")
    recalc = recompute(crit)
    if recalc is None:
        blocking.append("overall_recompute_not_safe")
    elif overall is not None and abs(recalc - overall) > 1e-9:
        blocking.append("overall_recompute_mismatch")

    if blocking:
        risk.append("format_math")
    checks.append({
        "check_id": "format_and_math_contract",
        "family": "format_math",
        "status": "fail" if blocking else "pass",
        "evidence": {"overall": overall, "recomputed": recalc, "criteria": crit}
    })

    # TierGovernor contract and universal risks
    evidence_tier = None
    released_tier = None
    pre = None
    post = overall
    action = None

    if cfg.get("require_tier_governor", True) and not tg:
        review.append("missing_tier_governor_contract")
        risk.append("tier_contract")

    if tg:
        evidence_tier = tg.get("evidence_tier")
        released_tier = tg.get("released_score_tier") or score_group(overall)
        pre = fnum(tg.get("pre_governor_overall"))
        post = fnum(tg.get("post_governor_overall"), overall)
        action = tg.get("action")

        if tg.get("runtime_label_use") is True:
            review.append("runtime_label_use_reported")
            risk.append("tier_contract")
        if tg.get("essay_specific_rule_use") is True:
            review.append("essay_specific_rule_use_reported")
            risk.append("tier_contract")

        if released_tier != score_group(overall):
            review.append("tier_contract_math_mismatch")
            risk.append("tier_contract")

        rng = tg.get("allowed_overall_range") or {}
        rmin = fnum(rng.get("min"))
        rmax = fnum(rng.get("max"))
        if rmin is not None and rmax is not None and overall is not None and not (rmin <= overall <= rmax):
            review.append("released_score_outside_declared_tier_range")
            risk.append("tier_contract")

        # Upward leakage checks
        if overall is not None:
            if evidence_tier == "A2_OR_LOWER" and overall > 3.5:
                review.append("a2_evidence_released_above_a2")
                risk.append("upward_leakage")
            if evidence_tier == "B1_WEAK" and overall >= 5.5:
                if overall >= 6.0:
                    review.append("b1_evidence_released_as_b2_high")
                else:
                    warnings.append("b1_boundary_release")
                risk.append("upward_leakage")
            if evidence_tier == "B1_B2_BOUNDARY" and overall > 5.5:
                review.append("b1_boundary_overpromoted")
                risk.append("upward_leakage")
            if evidence_tier == "B2_MEDIUM" and overall >= 7.0:
                review.append("b2_evidence_released_as_high_without_confirmation")
                risk.append("upward_leakage")
            if evidence_tier == "HIGH_BOUNDARY" and overall >= 7.5:
                review.append("high_boundary_overpromoted")
                risk.append("upward_leakage")
            if evidence_tier == "HIGH_BOUNDARY" and overall == 7.0 and confidence_level(tg) == "low":
                warnings.append("high_boundary_low_confidence")
                risk.append("upward_leakage")

            # Downward leakage checks
            if evidence_tier in ("HIGH_7_PLUS", "HIGH_RESCUE_CANDIDATE") and overall <= 6.0 and hard_veto_count(tg) == 0:
                review.append("high_evidence_released_too_low")
                risk.append("downward_leakage")
            elif evidence_tier in ("HIGH_7_PLUS", "HIGH_RESCUE_CANDIDATE") and overall == 6.5 and confidence_level(tg) != "low":
                warnings.append("possible_high_underrelease")
                risk.append("downward_leakage")
            if evidence_tier == "HIGH_BOUNDARY" and overall <= 5.5 and hard_veto_count(tg) == 0:
                review.append("high_boundary_released_too_low")
                risk.append("downward_leakage")
            elif evidence_tier == "HIGH_BOUNDARY" and overall == 6.5 and confidence_level(tg) == "low":
                warnings.append("high_boundary_low_confidence")
            elif evidence_tier == "HIGH_BOUNDARY" and overall >= 7.0 and not high_rescue_confirmed(tg):
                warnings.append("high_boundary_release")

            # Confirmed rescue mismatch
            if high_rescue_candidate(tg) and high_rescue_confirmation_count(tg) >= int(cfg["thresholds"]["high_rescue_confirmation_review_min"]) and overall < 7.0:
                review.append("high_rescue_confirmed_but_not_released_high")
                risk.append("downward_leakage")

        # Score movement checks
        if pre is not None and post is not None:
            jump = round(float(post) - float(pre), 3)
            crossed_tier = score_group(pre) != score_group(post)
            if abs(jump) >= float(cfg["thresholds"]["large_governor_jump"]):
                if crossed_tier:
                    warnings.append("large_governor_movement")
                    risk.append("score_jump")
                else:
                    warnings.append("large_governor_movement_display")
            if pre <= float(cfg["thresholds"]["upward_high_jump_from_max"]) and post >= float(cfg["thresholds"]["upward_high_jump_to_min"]) and high_rescue_confirmation_count(tg) < int(cfg["thresholds"]["high_rescue_confirmation_review_min"]):
                review.append("large_upward_high_jump")
                risk.append("score_jump")
            if pre >= float(cfg["thresholds"]["downward_large_jump_from_min"]) and post <= float(cfg["thresholds"]["downward_large_jump_to_max"]):
                review.append("large_downward_tier_jump")
                risk.append("score_jump")

        # Evidence contradictions
        if overall is not None:
            weak_contra = [
                feature(tg, "weak_writing_probability", 0.0) >= float(cfg["thresholds"]["weak_evidence_high_score_weak_probability"]),
                feature(tg, "serious_error_sentence_ratio", 0.0) >= float(cfg["thresholds"]["weak_evidence_high_score_serious_ratio"]),
                feature(tg, "local_language_damage_index", 0.0) >= float(cfg["thresholds"]["weak_evidence_high_score_local_damage"]),
                feature(tg, "raw_min_criterion_band", 9.0) <= float(cfg["thresholds"]["weak_evidence_raw_min_max"]),
                feature(tg, "support_quality", 1.0) < float(cfg["thresholds"]["weak_quality_max"]),
                feature(tg, "idea_extension_depth", 1.0) < float(cfg["thresholds"]["weak_quality_max"]),
            ]
            if overall >= 7.0 and sum(bool(x) for x in weak_contra) >= 2:
                review.append("high_score_with_weak_evidence_contradiction")
                risk.append("evidence_contradiction")

            # v1.4.3 independent high-release sanity checks.
            if overall >= 7.0:
                if tg.get("weak_profile_override") is True:
                    review.append("high_release_despite_weak_profile_override")
                    risk.append("evidence_contradiction")
                if int(tg.get("antiweak_high_gate_failed_count") or 0) > 1:
                    review.append("high_release_failed_antiweak_gate")
                    risk.append("evidence_contradiction")
                if tg.get("anti_b2_overpromotion_gate_ok") is False:
                    review.append("high_release_failed_antib2_gate")
                    risk.append("evidence_contradiction")
                if pre is not None and pre <= 5.5:
                    review.append("two_tier_upward_jump_to_high")
                    risk.append("score_jump")
                if evidence_tier not in ("HIGH_7_PLUS", "HIGH_RESCUE_CANDIDATE", "HIGH_BOUNDARY") and not high_rescue_confirmed(tg):
                    review.append("high_score_without_high_evidence_tier")
                    risk.append("upward_leakage")

            if overall is not None and overall >= 6.0 and tg.get("weak_profile_override") is True:
                review.append("weak_profile_override_released_too_high")
                risk.append("upward_leakage")

            if evidence_tier == "B2_MEDIUM" and overall is not None and overall <= 5.0 and tg.get("b2_to_b1_underrelease_guard") is True:
                review.append("b2_underrelease_guard_triggered_but_released_weak")
                risk.append("downward_leakage")

            if overall >= 7.0 and text_feature(tg, "task_schema_status") == "true_fail" and text_feature(tg, "task_resolution_state") == "TASK_TRUE_FAIL_HARD":
                review.append("task_true_fail_high_release")
                risk.append("task_contradiction")

            high_positive = [
                feature(tg, "high_band_readiness", 0.0) >= float(cfg["thresholds"]["high_evidence_low_score_hbr"]),
                feature(tg, "semantic_recoverability", 0.0) >= float(cfg["thresholds"]["high_evidence_low_score_semantic"]),
                feature(tg, "support_quality", 0.0) >= float(cfg["thresholds"]["high_evidence_low_score_quality"]),
                feature(tg, "idea_extension_depth", 0.0) >= float(cfg["thresholds"]["high_evidence_low_score_quality"]),
                feature(tg, "local_language_damage_index", 1.0) <= float(cfg["thresholds"]["high_evidence_low_score_local_max"]),
                feature(tg, "serious_error_sentence_ratio", 1.0) <= float(cfg["thresholds"]["high_evidence_low_score_serious_max"]),
            ]
            if overall <= 6.0 and sum(bool(x) for x in high_positive) >= 5 and hard_veto_count(tg) == 0:
                review.append("high_evidence_released_too_low")
                risk.append("evidence_contradiction")

        checks.append({
            "check_id": "tier_and_universal_risk_contract",
            "family": "tier_contract",
            "status": "fail" if review else "warn" if warnings else "pass",
            "evidence": {
                "evidence_tier": evidence_tier,
                "released_score_tier": released_tier,
                "score_group": score_group(overall),
                "pre_governor_overall": pre,
                "post_governor_overall": post,
                "action": action,
                "tier_confidence_level": confidence_level(tg),
                "high_rescue_candidate": high_rescue_candidate(tg),
                "high_rescue_confirmation_count": high_rescue_confirmation_count(tg),
                "hard_veto_count": hard_veto_count(tg)
            }
        })

    blocking = sorted(set(blocking))
    review = sorted(set(review))
    warnings = sorted(set(warnings))
    risk = sorted(set(risk))
    cat = caution_category(warnings, review, cfg)

    if blocking:
        status = "invalid_output"
        release = cfg["release_policy"]["invalid_output"]
        priority = "critical"
    elif review:
        status = "review_required"
        release = cfg["release_policy"]["review_required"]
        priority = "high"
    elif cat != "none":
        status = "caution"
        release = cfg["release_policy"]["caution"]
        priority = "medium"
    else:
        status = "pass"
        release = cfg["release_policy"]["pass"]
        priority = "none"

    safe_student = status in ("pass", "caution") and cat != "review_caution"
    safe_priority = status in ("pass", "caution")
    safe_tracking = status == "pass" or cat == "display_caution_only"
    safe_lie = safe_tracking

    return {
        "essay_id": eid,
        "verifier_version": VERIFIER_VERSION,
        "verifier_status": status,
        "release_decision": release,
        "score_change_allowed": False,
        "score_change_recommended": False,
        "original_overall_band": overall,
        "original_criteria_bands": crit,
        "score_group": score_group(overall),
        "scorer_evidence_tier": evidence_tier,
        "scorer_released_score_tier": released_tier,
        "tier_governor_action": action,
        "caution_category": cat,
        "risk_families": risk,
        "blocking_flags": blocking,
        "review_flags": review,
        "warnings": warnings,
        "review_priority": priority,
        "human_review_recommended": status == "review_required",
        "safe_for_student_release": safe_student,
        "safe_for_progress_tracking": safe_tracking,
        "safe_for_priority_engine": safe_priority,
        "safe_for_lie_update": safe_lie,
        "checks": checks,
        "short_reason": (
            "invalid structural output" if blocking else
            "universal gatekeeper review required" if review else
            "release with caution" if status == "caution" else
            "safe release"
        )
    }


def verify_batch(payload: Any, cfg: Dict[str, Any]) -> Dict[str, Any]:
    results = [check_one(r, cfg) for r in as_results(payload)]
    return {
        "schema_version": "premium_verifier_v1_4_3_universal_gatekeeper_batch",
        "verifier_version": VERIFIER_VERSION,
        "score_change_allowed": False,
        "n_results": len(results),
        "summary": {
            "status_counts": dict(Counter(r["verifier_status"] for r in results)),
            "release_decision_counts": dict(Counter(r["release_decision"] for r in results)),
            "score_group_counts": dict(Counter(r["score_group"] for r in results)),
            "scorer_evidence_tier_counts": dict(Counter(r.get("scorer_evidence_tier") for r in results)),
            "tier_governor_action_counts": dict(Counter(r.get("tier_governor_action") for r in results)),
            "caution_category_counts": dict(Counter(r.get("caution_category") for r in results)),
            "risk_family_counts": dict(sum((Counter(r["risk_families"]) for r in results), Counter())),
            "blocking_flag_counts": dict(sum((Counter(r["blocking_flags"]) for r in results), Counter())),
            "review_flag_counts": dict(sum((Counter(r["review_flags"]) for r in results), Counter())),
            "warning_counts": dict(sum((Counter(r["warnings"]) for r in results), Counter())),
            "safe_for_student_release_counts": dict(Counter(str(r["safe_for_student_release"]) for r in results)),
            "safe_for_progress_tracking_counts": dict(Counter(str(r["safe_for_progress_tracking"]) for r in results)),
        },
        "results": results
    }


def runtime_purity_audit(paths: List[Path]) -> Dict[str, Any]:
    forbidden = ["BAL"+"_WT2_", "if "+"essay_id", "ground"+"_truth", "calibration"+"_label", "source"+"_label", "gold"+"_label"]
    files = []
    failed = False
    for p in paths:
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        hits = [x for x in forbidden if x in text]
        files.append({"file": str(p), "status": "FAIL" if hits else "PASS", "hits": hits})
        failed = failed or bool(hits)
    return {"audit": "Premium Verifier v1.4.3 runtime purity audit", "result": "FAIL" if failed else "PASS", "files": files}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Premium Verifier v1.4.3 — Universal Release Gatekeeper")
    ap.add_argument("--input")
    ap.add_argument("--output")
    ap.add_argument("--config")
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--audit-runtime-only", action="store_true")
    args = ap.parse_args(argv)

    here = Path(__file__).resolve()
    cfg_path = Path(args.config).resolve() if args.config else here.with_name("premium_verifier_config_v1_4_1.json")

    if args.audit_runtime_only:
        paths = [here]
        if cfg_path.exists():
            paths.append(cfg_path)
        audit = runtime_purity_audit(paths)
        print(audit["audit"])
        print()
        for item in audit["files"]:
            print(f"{item['status']}: {Path(item['file']).name}")
            if item["hits"]:
                print("  hits:", ", ".join(item["hits"]))
        print()
        print("RESULT:", audit["result"])
        return 0 if audit["result"] == "PASS" else 1

    if not args.input or not args.output:
        ap.error("--input and --output are required unless --audit-runtime-only")

    cfg = deep_merge(DEFAULT_CONFIG, load_json(str(cfg_path)) if cfg_path.exists() else None)
    write_json(verify_batch(load_json(args.input), cfg), args.output, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
