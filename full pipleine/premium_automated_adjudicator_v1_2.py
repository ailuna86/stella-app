#!/usr/bin/env python3
"""
Premium Automated Adjudicator v1.2

Final automated score-resolution layer for the Premium Scoring Pipeline.

Pipeline:
    Detector / Profiler
      -> Scorer
      -> Verifier
      -> Automated Adjudicator v1.2

Core product rule:
    Every valid scored essay receives a final released score.
    The module is automated-only.
    If the verifier indicates a completely invalid score contract, the item is routed to automated rescoring before adjudication.

This module:
    - uses only runtime scoring/verifier evidence;
    - does not use external reference labels;
    - does not use record-specific rules;
    - can automatically adjust score family when runtime evidence supports a safer family;
    - preserves original criteria whenever the final overall band is unchanged;
    - adjusts criteria only when an explicit adjudication reason changes the final overall/family.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


VERSION = "premium_automated_adjudicator_v1_2"
SCHEMA_VERSION = "premium_automated_adjudicator_v1_2_batch"

TIER_ORDER = ["A2_OR_LOWER", "B1_WEAK", "B2_MEDIUM", "HIGH_7_PLUS"]
TIER_RANK = {name: i for i, name in enumerate(TIER_ORDER)}
CRITERIA_KEYS = ["task_response", "coherence_cohesion", "lexical_resource", "grammar"]

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": VERSION,
    "score_change_allowed": True,
    "adjudicate_all": False,
    "invalid_contract_statuses": ["invalid_output", "blocked"],
    "selection": {
        "always_select_verifier_statuses": ["review_required"],
        "select_caution_categories": ["tracking_caution", "review_caution"],
        "select_warnings": [
            "high_boundary_low_confidence",
            "large_governor_movement",
            "large_governor_movement_display",
            "possible_high_underrelease",
            "high_boundary_release",
        ],
        "select_review_flags": [
            "large_upward_high_jump",
            "two_tier_upward_jump_to_high",
            "high_release_failed_antiweak_gate",
            "high_release_failed_antib2_gate",
            "high_release_despite_weak_profile_override",
            "weak_profile_override_released_too_high",
            "a2_evidence_released_above_a2",
            "b2_underrelease_guard_triggered_but_released_weak",
            "high_rescue_confirmed_but_not_released_high",
        ],
        "select_when_evidence_released_disagree": True,
    },
    "thresholds": {
        "high_confirm_count_min": 8,
        "high_confirm_count_strong": 10,
        "high_jump_pre_score_max": 6.0,
        "max_high_antiweak_failures": 1,
    },
    "family_target_bands": {
        "A2_OR_LOWER": 3.5,
        "B1_WEAK": 5.0,
        "B2_MEDIUM": 6.5,
        "B2_FLOOR": 5.5,
        "HIGH_7_PLUS": 7.0,
    },
    "downstream_policy": {
        "confirmed": {
            "student_score_release": True,
            "progress_tracking_allowed": True,
            "priority_engine_allowed": True,
            "lie_update_allowed": True,
            "practice_engine_allowed": True,
            "score_confidence": "normal",
        },
        "released_reduced_confidence": {
            "student_score_release": True,
            "progress_tracking_allowed": False,
            "priority_engine_allowed": True,
            "lie_update_allowed": False,
            "practice_engine_allowed": True,
            "score_confidence": "reduced",
        },
        "adjusted": {
            "student_score_release": True,
            "progress_tracking_allowed": False,
            "priority_engine_allowed": True,
            "lie_update_allowed": False,
            "practice_engine_allowed": True,
            "score_confidence": "reduced",
        },
        "released_low_confidence": {
            "student_score_release": True,
            "progress_tracking_allowed": False,
            "priority_engine_allowed": True,
            "lie_update_allowed": False,
            "practice_engine_allowed": True,
            "score_confidence": "low",
        },
        "rescore_required_before_adjudication": {
            "student_score_release": False,
            "progress_tracking_allowed": False,
            "priority_engine_allowed": False,
            "lie_update_allowed": False,
            "practice_engine_allowed": False,
            "score_confidence": "invalid_contract",
        },
    },
}


@dataclass
class FinalScore:
    overall_band: Optional[float]
    criteria_bands: Dict[str, int]
    score_family: str
    score_released: bool = True


@dataclass
class AdjudicationResult:
    essay_id: str
    adjudicator_version: str = VERSION
    selected_for_adjudication: bool = False
    selection_reasons: List[str] = field(default_factory=list)
    adjudication_status: str = "confirmed"
    input_score: Dict[str, Any] = field(default_factory=dict)
    final_score: Dict[str, Any] = field(default_factory=dict)
    score_changed: bool = False
    score_change_allowed: bool = True
    final_score_released: bool = True
    criteria_preserved: bool = True
    confidence: str = "normal"
    reason_codes: List[str] = field(default_factory=list)
    evidence_balance: Dict[str, Any] = field(default_factory=dict)
    downstream_policy: Dict[str, Any] = field(default_factory=dict)
    rescore_required_before_adjudication: bool = False
    audit: Dict[str, Any] = field(default_factory=dict)


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(obj: Any, path: str, pretty: bool = False) -> None:
    with open(path, "w", encoding="utf-8") as f:
        if pretty:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        else:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def as_results(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("results", "essays", "scored", "verified"):
            if isinstance(payload.get(key), list):
                return [x for x in payload[key] if isinstance(x, dict)]
    return []


def record_id(rec: Dict[str, Any]) -> str:
    return str(rec.get("essay_id") or rec.get("id") or rec.get("submission_id") or "")


def as_list(x: Any) -> List[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def as_bool(x: Any, default: bool = False) -> bool:
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        s = x.strip().lower()
        if s == "true":
            return True
        if s == "false":
            return False
    return default


def as_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    if x is None:
        return default
    try:
        return float(x)
    except Exception:
        return default


def as_int(x: Any, default: int = 0) -> int:
    try:
        return int(float(x))
    except Exception:
        return default


def round_half_band(value: float) -> float:
    return math.floor(value * 2.0 + 0.5) / 2.0


def overall_from_criteria(criteria: Dict[str, int]) -> Optional[float]:
    vals = []
    for k in CRITERIA_KEYS:
        if k not in criteria:
            return None
        vals.append(int(criteria[k]))
    return round_half_band(sum(vals) / 4.0)


def family_from_band(band: Optional[float]) -> str:
    if band is None:
        return "UNKNOWN"
    if band <= 3.5:
        return "A2_OR_LOWER"
    if band <= 5.0:
        return "B1_WEAK"
    if band <= 6.5:
        return "B2_MEDIUM"
    return "HIGH_7_PLUS"


def normalize_evidence_tier(value: Any) -> str:
    s = str(value or "").strip()
    if s in TIER_RANK:
        return s
    if s in ("HIGH_BOUNDARY", "HIGH_RESCUE_CANDIDATE", "HIGH_7_PLUS"):
        return "HIGH_7_PLUS"
    if s == "B1_B2_BOUNDARY":
        return "B1_WEAK"
    if s in ("A2", "A2_LOW"):
        return "A2_OR_LOWER"
    if s in ("B1", "B1_LOW"):
        return "B1_WEAK"
    if s in ("B2", "B2_MID"):
        return "B2_MEDIUM"
    if s in ("HIGH", "7_PLUS"):
        return "HIGH_7_PLUS"
    return "UNKNOWN"


def tier_rank(family: str) -> int:
    return TIER_RANK.get(normalize_evidence_tier(family), -1)


def get_criteria_from_verified(verified: Dict[str, Any]) -> Dict[str, int]:
    crit = verified.get("original_criteria_bands")
    if isinstance(crit, dict):
        out: Dict[str, int] = {}
        for k in CRITERIA_KEYS:
            if k in crit:
                out[k] = as_int(crit[k], 0)
        if len(out) == 4:
            return out
    return {}


def get_criteria_from_scored(scored: Optional[Dict[str, Any]]) -> Dict[str, int]:
    if not isinstance(scored, dict):
        return {}
    sp = scored.get("score_profile")
    if not isinstance(sp, dict):
        return {}
    crit = sp.get("official_criteria_bands")
    if not isinstance(crit, dict):
        return {}
    out: Dict[str, int] = {}
    for k in CRITERIA_KEYS:
        if k in crit:
            out[k] = as_int(crit[k], 0)
    return out if len(out) == 4 else {}


def get_input_score(verified: Dict[str, Any], scored: Optional[Dict[str, Any]]) -> FinalScore:
    criteria = get_criteria_from_verified(verified) or get_criteria_from_scored(scored)
    overall = as_float(verified.get("original_overall_band"), None)
    if overall is None and criteria:
        overall = overall_from_criteria(criteria)
    if overall is None and isinstance(scored, dict):
        sp = scored.get("score_profile") or {}
        overall = as_float(sp.get("overall_band_estimate"), None)
    family = normalize_evidence_tier(verified.get("score_group")) if verified.get("score_group") else family_from_band(overall)
    if family == "UNKNOWN":
        family = family_from_band(overall)
    return FinalScore(overall_band=overall, criteria_bands=criteria, score_family=family, score_released=overall is not None and len(criteria) == 4)


def get_tier_governor(scored: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(scored, dict):
        return {}
    tg = scored.get("tier_governor")
    return tg if isinstance(tg, dict) else {}


def tg_value(tg: Dict[str, Any], key: str, default: Any = None) -> Any:
    if not isinstance(tg, dict):
        return default
    if key in tg:
        return tg.get(key)
    features = tg.get("features")
    if isinstance(features, dict) and key in features:
        return features.get(key)
    diagnostics = tg.get("diagnostics")
    if isinstance(diagnostics, dict) and key in diagnostics:
        return diagnostics.get(key)
    return default


def verifier_evidence_family(verified: Dict[str, Any], tg: Dict[str, Any]) -> str:
    return normalize_evidence_tier(verified.get("scorer_evidence_tier") or tg.get("evidence_tier"))


def verifier_released_family(verified: Dict[str, Any], tg: Dict[str, Any], input_score: FinalScore) -> str:
    return normalize_evidence_tier(
        verified.get("scorer_released_score_tier")
        or verified.get("score_group")
        or tg.get("released_score_tier")
        or input_score.score_family
    )


def contract_invalid(verified: Dict[str, Any], input_score: FinalScore, cfg: Dict[str, Any]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    status = str(verified.get("verifier_status") or "")
    if status in set(cfg.get("invalid_contract_statuses", [])):
        reasons.append("verifier_invalid_contract_status")
    if not input_score.score_released:
        reasons.append("missing_valid_input_score")
    checks = verified.get("checks")
    if isinstance(checks, list):
        for chk in checks:
            if isinstance(chk, dict) and chk.get("check_id") == "format_and_math_contract" and chk.get("status") == "fail":
                reasons.append("format_math_contract_failed")
    blocking = as_list(verified.get("blocking_flags"))
    if blocking:
        reasons.append("verifier_blocking_flags_present")
    return bool(reasons), reasons


def select_case(verified: Dict[str, Any], tg: Dict[str, Any], input_score: FinalScore, cfg: Dict[str, Any]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    if cfg.get("adjudicate_all", False):
        reasons.append("adjudicate_all")

    selection = cfg.get("selection", {})
    status = str(verified.get("verifier_status") or "")
    caution = str(verified.get("caution_category") or "")
    warnings = set(str(x) for x in as_list(verified.get("warnings")))
    review_flags = set(str(x) for x in as_list(verified.get("review_flags")))

    if status in set(selection.get("always_select_verifier_statuses", [])):
        reasons.append(f"verifier_status:{status}")
    if caution in set(selection.get("select_caution_categories", [])):
        reasons.append(f"caution_category:{caution}")

    for warning in sorted(warnings.intersection(set(selection.get("select_warnings", [])))):
        reasons.append(f"warning:{warning}")
    for flag in sorted(review_flags.intersection(set(selection.get("select_review_flags", [])))):
        reasons.append(f"review_flag:{flag}")

    if selection.get("select_when_evidence_released_disagree", True):
        ev = verifier_evidence_family(verified, tg)
        rel = verifier_released_family(verified, tg, input_score)
        if ev != "UNKNOWN" and rel != "UNKNOWN" and ev != rel:
            reasons.append(f"tier_family_disagreement:{ev}_vs_{rel}")

    return bool(reasons), reasons


def evidence_balance(verified: Dict[str, Any], tg: Dict[str, Any], input_score: FinalScore) -> Dict[str, Any]:
    ev = verifier_evidence_family(verified, tg)
    rel = verifier_released_family(verified, tg, input_score)
    review_flags = set(str(x) for x in as_list(verified.get("review_flags")))
    warnings = set(str(x) for x in as_list(verified.get("warnings")))

    high_confirmation = as_int(tg_value(tg, "high_rescue_confirmation_count", 0), 0)
    hard_veto_count = as_int(tg_value(tg, "hard_veto_count", 0), 0)
    antiweak_failed = as_int(tg_value(tg, "antiweak_high_gate_failed_count", 0), 0)
    anti_b2_ok = as_bool(tg_value(tg, "anti_b2_overpromotion_gate_ok", True), True)
    weak_override = as_bool(tg_value(tg, "weak_profile_override", False), False)
    b2_floor = as_bool(tg_value(tg, "b2_to_b1_underrelease_guard", False), False)

    return {
        "evidence_family": ev,
        "released_family": rel,
        "input_family": input_score.score_family,
        "review_flags": sorted(review_flags),
        "warnings": sorted(warnings),
        "high_rescue_confirmation_count": high_confirmation,
        "hard_veto_count": hard_veto_count,
        "antiweak_high_gate_failed_count": antiweak_failed,
        "anti_b2_overpromotion_gate_ok": anti_b2_ok,
        "weak_profile_override": weak_override,
        "b2_to_b1_underrelease_guard": b2_floor,
        "pre_governor_overall": as_float(tg_value(tg, "pre_governor_overall"), None),
        "post_governor_overall": as_float(tg_value(tg, "post_governor_overall"), input_score.overall_band),
    }


def choose_target_family_and_band(verified: Dict[str, Any], tg: Dict[str, Any], input_score: FinalScore, cfg: Dict[str, Any]) -> Tuple[str, Optional[float], List[str], str]:
    """Return target family, target overall band, reason codes, confidence."""
    balance = evidence_balance(verified, tg, input_score)
    ev = balance["evidence_family"]
    rel = balance["released_family"]
    flags = set(balance["review_flags"])
    warnings = set(balance["warnings"])
    high_confirm = int(balance["high_rescue_confirmation_count"] or 0)
    hard_veto = int(balance["hard_veto_count"] or 0)
    antiweak_failed = int(balance["antiweak_high_gate_failed_count"] or 0)
    anti_b2_ok = bool(balance["anti_b2_overpromotion_gate_ok"])
    weak_override = bool(balance["weak_profile_override"])
    b2_floor = bool(balance["b2_to_b1_underrelease_guard"])
    pre = balance["pre_governor_overall"]

    tcfg = cfg.get("thresholds", {})
    bands = cfg.get("family_target_bands", {})
    reasons: List[str] = []

    original_family = input_score.score_family
    original_band = input_score.overall_band

    def band_for_family(fam: str, direction: str = "nearest") -> float:
        if fam == "A2_OR_LOWER":
            return float(bands.get("A2_OR_LOWER", 3.5))
        if fam == "B1_WEAK":
            return float(bands.get("B1_WEAK", 5.0))
        if fam == "B2_MEDIUM":
            if direction == "up":
                return float(bands.get("B2_FLOOR", 5.5))
            return float(bands.get("B2_MEDIUM", 6.5))
        if fam == "HIGH_7_PLUS":
            return float(bands.get("HIGH_7_PLUS", 7.0))
        return original_band if original_band is not None else 0.0

    # Hard low-tier conflicts.
    if "a2_evidence_released_above_a2" in flags or (ev == "A2_OR_LOWER" and rel != "A2_OR_LOWER"):
        reasons.append("a2_evidence_conflict")
        return "A2_OR_LOWER", band_for_family("A2_OR_LOWER"), reasons, "low"

    if weak_override and tier_rank(rel) >= tier_rank("B2_MEDIUM"):
        reasons.append("weak_profile_override_blocks_higher_family")
        return "B1_WEAK", band_for_family("B1_WEAK"), reasons, "low"

    # Unsafe high release -> safer B2.
    high_release = original_family == "HIGH_7_PLUS" or rel == "HIGH_7_PLUS"
    if high_release:
        high_is_secure = (
            ev == "HIGH_7_PLUS"
            and high_confirm >= int(tcfg.get("high_confirm_count_min", 8))
            and anti_b2_ok
            and antiweak_failed <= int(tcfg.get("max_high_antiweak_failures", 1))
            and hard_veto == 0
        )
        if "large_upward_high_jump" in flags or "two_tier_upward_jump_to_high" in flags:
            reasons.append("large_upward_high_jump")
            if not high_is_secure:
                reasons.append("high_evidence_not_secure")
                return "B2_MEDIUM", band_for_family("B2_MEDIUM"), reasons, "low"
            return "HIGH_7_PLUS", band_for_family("HIGH_7_PLUS"), reasons, "reduced"

        if "high_release_failed_antib2_gate" in flags or not anti_b2_ok:
            reasons.append("high_release_failed_antib2_gate")
            return "B2_MEDIUM", band_for_family("B2_MEDIUM"), reasons, "low"

        if "high_release_failed_antiweak_gate" in flags or antiweak_failed > int(tcfg.get("max_high_antiweak_failures", 1)):
            reasons.append("high_release_failed_antiweak_gate")
            return "B2_MEDIUM", band_for_family("B2_MEDIUM"), reasons, "low"

        if ev not in ("HIGH_7_PLUS", "UNKNOWN"):
            reasons.append("released_high_but_evidence_family_lower")
            return "B2_MEDIUM", band_for_family("B2_MEDIUM"), reasons, "reduced"

    # Underrelease B1 where B2 evidence exists.
    if (b2_floor and original_family == "B1_WEAK") or "b2_underrelease_guard_triggered_but_released_weak" in flags:
        reasons.append("b2_underrelease_guard")
        return "B2_MEDIUM", band_for_family("B2_MEDIUM", direction="up"), reasons, "reduced"

    # Confirmed high underrelease.
    if "high_rescue_confirmed_but_not_released_high" in flags:
        reasons.append("high_rescue_confirmed_not_released")
        if high_confirm >= int(tcfg.get("high_confirm_count_strong", 10)) and hard_veto == 0:
            return "HIGH_7_PLUS", band_for_family("HIGH_7_PLUS"), reasons, "reduced"
        return original_family, original_band, reasons, "low"

    if "possible_high_underrelease" in warnings and ev == "HIGH_7_PLUS" and original_family == "B2_MEDIUM":
        reasons.append("possible_high_underrelease")
        if high_confirm >= int(tcfg.get("high_confirm_count_strong", 10)) and hard_veto == 0:
            return "HIGH_7_PLUS", band_for_family("HIGH_7_PLUS"), reasons, "reduced"
        return original_family, original_band, reasons, "low"

    # Boundary warnings: keep score but reduce analytics confidence.
    # This must run before generic evidence-family disagreement because HIGH_BOUNDARY is normalized to HIGH_7_PLUS.
    if "high_boundary_low_confidence" in warnings:
        reasons.append("high_boundary_low_confidence")
        return original_family, original_band, reasons, "reduced"

    if "large_governor_movement" in warnings or "large_governor_movement_display" in warnings:
        reasons.append("large_governor_movement")
        return original_family, original_band, reasons, "reduced"

    # Evidence/release disagreement: adjust down readily, adjust up only with strong confirmation.
    if ev != "UNKNOWN" and rel != "UNKNOWN" and ev != rel:
        if tier_rank(ev) < tier_rank(rel):
            reasons.append("evidence_family_lower_than_released_family")
            return ev, band_for_family(ev), reasons, "reduced"
        if tier_rank(ev) > tier_rank(rel):
            reasons.append("evidence_family_higher_than_released_family")
            if ev == "HIGH_7_PLUS" and high_confirm >= int(tcfg.get("high_confirm_count_strong", 10)) and hard_veto == 0:
                return "HIGH_7_PLUS", band_for_family("HIGH_7_PLUS"), reasons, "reduced"
            if ev == "B2_MEDIUM" and original_family == "B1_WEAK":
                return "B2_MEDIUM", band_for_family("B2_MEDIUM", direction="up"), reasons, "reduced"
            return original_family, original_band, reasons, "low"

    return original_family, original_band, reasons, "normal"


def criterion_distance(criteria: Dict[str, int], target_overall: float) -> float:
    cur = overall_from_criteria(criteria)
    if cur is None:
        return 999.0
    return abs(cur - target_overall)


def adjust_criteria_to_target(criteria: Dict[str, int], target_overall: float) -> Dict[str, int]:
    """Minimally adjusts integer criteria so their rounded average equals target_overall."""
    if not criteria or len(criteria) != 4:
        # Safe fallback: all criteria at target floor, clipped to integer band.
        base = max(0, min(9, int(math.floor(target_overall))))
        candidate = {k: base for k in CRITERIA_KEYS}
        # Raise one criterion if needed for .5 overall.
        while overall_from_criteria(candidate) is not None and overall_from_criteria(candidate) < target_overall:
            k = min(CRITERIA_KEYS, key=lambda x: candidate[x])
            candidate[k] = min(9, candidate[k] + 1)
        return candidate

    current = {k: int(criteria[k]) for k in CRITERIA_KEYS}
    if overall_from_criteria(current) == target_overall:
        return current

    best = dict(current)
    best_cost = (criterion_distance(best, target_overall), 0, 0)

    # Breadth search small neighborhood, preserving shape as much as possible.
    candidates = [current]
    seen = {tuple(current[k] for k in CRITERIA_KEYS)}
    for depth in range(1, 7):
        new_candidates = []
        for cand in candidates:
            for k in CRITERIA_KEYS:
                for delta in (-1, 1):
                    nxt = dict(cand)
                    nxt[k] = max(0, min(9, nxt[k] + delta))
                    key = tuple(nxt[x] for x in CRITERIA_KEYS)
                    if key in seen:
                        continue
                    seen.add(key)
                    new_candidates.append(nxt)
                    ov = overall_from_criteria(nxt)
                    if ov is None:
                        continue
                    total_shift = sum(abs(nxt[x] - current[x]) for x in CRITERIA_KEYS)
                    max_shift = max(abs(nxt[x] - current[x]) for x in CRITERIA_KEYS)
                    cost = (abs(ov - target_overall), total_shift, max_shift)
                    if cost < best_cost:
                        best = nxt
                        best_cost = cost
        candidates = new_candidates
        if best_cost[0] == 0:
            break
    return best


def map_downstream(status: str, verifier_status: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    policy = dict(cfg.get("downstream_policy", {}).get(status, {}))
    if not policy:
        policy = dict(cfg.get("downstream_policy", {}).get("released_reduced_confidence", {}))
    # Verifier caution/review never blocks student score in v1.2, but blocks stable analytics.
    if verifier_status in ("caution", "review_required") and status != "rescore_required_before_adjudication":
        policy["progress_tracking_allowed"] = False
        policy["lie_update_allowed"] = False
        policy["score_confidence"] = "low" if verifier_status == "review_required" else policy.get("score_confidence", "reduced")
    return policy


def adjudicate_record(verified: Dict[str, Any], scored: Optional[Dict[str, Any]], cfg: Dict[str, Any]) -> AdjudicationResult:
    essay_id = record_id(verified)
    tg = get_tier_governor(scored)
    input_score = get_input_score(verified, scored)
    invalid, invalid_reasons = contract_invalid(verified, input_score, cfg)

    input_score_dict = {
        "overall_band": input_score.overall_band,
        "criteria_bands": input_score.criteria_bands,
        "score_family": input_score.score_family,
    }

    if invalid:
        status = "rescore_required_before_adjudication"
        policy = map_downstream(status, str(verified.get("verifier_status") or ""), cfg)
        final_score = FinalScore(None, {}, "UNKNOWN", score_released=False)
        return AdjudicationResult(
            essay_id=essay_id,
            selected_for_adjudication=True,
            selection_reasons=invalid_reasons,
            adjudication_status=status,
            input_score=input_score_dict,
            final_score=asdict(final_score),
            score_changed=False,
            score_change_allowed=True,
            final_score_released=False,
            criteria_preserved=True,
            confidence="invalid_contract",
            reason_codes=invalid_reasons,
            evidence_balance=evidence_balance(verified, tg, input_score),
            downstream_policy=policy,
            rescore_required_before_adjudication=True,
            audit={
                "runtime_label_use": False,
                "record_specific_rule_use": False,
                "scored_runtime_diagnostics_loaded": bool(tg),
                "automated_only": True,
            },
        )

    selected, selection_reasons = select_case(verified, tg, input_score, cfg)
    target_family, target_band, reason_codes, confidence = choose_target_family_and_band(verified, tg, input_score, cfg)

    verifier_status = str(verified.get("verifier_status") or "")

    # v1.2 safety rule:
    # Preserve original criteria unless a real adjudication reason changes the final overall/family.
    # This prevents silent criterion-band edits for not-selected or same-overall cases.
    target_band = input_score.overall_band if target_band is None else target_band
    overall_changed = (
        target_band is not None
        and input_score.overall_band is not None
        and float(target_band) != float(input_score.overall_band)
    )
    family_changed = (
        target_family not in ("UNKNOWN", input_score.score_family)
        and target_family != input_score.score_family
    )
    explicit_adjustment = bool(reason_codes) and (overall_changed or family_changed)

    final_criteria = dict(input_score.criteria_bands)
    final_overall = input_score.overall_band
    criteria_preserved = True
    governor_math_repaired = False

    if explicit_adjustment and target_band is not None and input_score.criteria_bands:
        final_criteria = adjust_criteria_to_target(input_score.criteria_bands, float(target_band))
        final_overall = overall_from_criteria(final_criteria)
        criteria_preserved = final_criteria == input_score.criteria_bands
    else:
        final_criteria = dict(input_score.criteria_bands)
        final_overall = input_score.overall_band
        criteria_preserved = True
        # v1.4.13 Gold pipeline fix (stress-test Problem 5): the upstream scorer's
        # tier governor can move overall_band (word-count gates, TR hard-fail caps,
        # band ceilings, etc.) without rebalancing criteria_bands to match. When the
        # adjudicator itself makes no adjudication decision (explicit_adjustment is
        # False, so this branch just passes the scorer's numbers through unchanged),
        # that pre-existing governor drift previously survived straight into the
        # released final_score, leaving criteria_math_valid permanently False even
        # though nothing here looks "adjusted". Repair that drift at this final,
        # authoritative point so every released score is internally consistent.
        # This is a math-consistency repair, not an adjudication decision, so it is
        # tracked separately (governor_math_repaired) from explicit_adjustment.
        if (final_criteria and final_overall is not None
                and overall_from_criteria(final_criteria) != final_overall):
            repaired = adjust_criteria_to_target(final_criteria, float(final_overall))
            if repaired != final_criteria:
                final_criteria = repaired
                governor_math_repaired = True
                criteria_preserved = False

    final_family = family_from_band(final_overall)

    score_changed = (
        final_overall != input_score.overall_band
        or final_family != input_score.score_family
    )

    if score_changed:
        status = "adjusted"
    elif selected and verifier_status == "review_required":
        status = "released_low_confidence"
        confidence = "low"
    elif selected and confidence in ("reduced", "low"):
        status = "released_reduced_confidence" if confidence == "reduced" else "released_low_confidence"
    elif selected:
        status = "released_reduced_confidence"
        confidence = "reduced"
    else:
        status = "confirmed"
        confidence = "normal"

    policy = map_downstream(status, verifier_status, cfg)
    final_score = FinalScore(final_overall, final_criteria, final_family, score_released=True)

    return AdjudicationResult(
        essay_id=essay_id,
        selected_for_adjudication=selected,
        selection_reasons=selection_reasons,
        adjudication_status=status,
        input_score=input_score_dict,
        final_score=asdict(final_score),
        score_changed=score_changed,
        score_change_allowed=True,
        final_score_released=True,
        criteria_preserved=criteria_preserved,
        confidence=policy.get("score_confidence", confidence),
        reason_codes=reason_codes,
        evidence_balance=evidence_balance(verified, tg, input_score),
        downstream_policy=policy,
        rescore_required_before_adjudication=False,
        audit={
            "runtime_label_use": False,
            "record_specific_rule_use": False,
            "scored_runtime_diagnostics_loaded": bool(tg),
            "automated_only": True,
            "criteria_math_valid": overall_from_criteria(final_criteria) == final_overall if final_criteria else False,
            "criteria_preserved": criteria_preserved,
            "explicit_adjustment": explicit_adjustment,
            "governor_math_repaired": governor_math_repaired,
        },
    )


def adjudicate_batch(verified_payload: Any, scored_payload: Optional[Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    verified_records = as_results(verified_payload)
    scored_records = as_results(scored_payload) if scored_payload is not None else []
    scored_index = {record_id(r): r for r in scored_records if record_id(r)}

    results: List[Dict[str, Any]] = []
    for rec in verified_records:
        rid = record_id(rec)
        result = adjudicate_record(rec, scored_index.get(rid), cfg)
        results.append(asdict(result))

    status_counts = Counter(r["adjudication_status"] for r in results)
    family_counts = Counter((r.get("final_score") or {}).get("score_family") for r in results)
    confidence_counts = Counter(r["confidence"] for r in results)
    reason_counts = Counter()
    for r in results:
        reason_counts.update(r.get("reason_codes", []))

    criterion_changed_count = sum(
        1 for r in results
        if (r.get("input_score") or {}).get("criteria_bands") != (r.get("final_score") or {}).get("criteria_bands")
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "adjudicator_version": VERSION,
        "score_change_allowed": True,
        "n_results": len(results),
        "summary": {
            "selected_for_adjudication": sum(1 for r in results if r["selected_for_adjudication"]),
            "not_selected": sum(1 for r in results if not r["selected_for_adjudication"]),
            "score_changed": sum(1 for r in results if r["score_changed"]),
            "criteria_changed": criterion_changed_count,
            "criteria_preserved": sum(1 for r in results if r.get("criteria_preserved")),
            "final_score_released": sum(1 for r in results if r["final_score_released"]),
            "rescore_required_before_adjudication": sum(1 for r in results if r["rescore_required_before_adjudication"]),
            "adjudication_status_counts": dict(status_counts),
            "final_score_family_counts": dict(family_counts),
            "confidence_counts": dict(confidence_counts),
            "reason_code_counts": dict(reason_counts),
            "student_score_release_counts": dict(Counter(str(r["downstream_policy"].get("student_score_release")) for r in results)),
            "progress_tracking_allowed_counts": dict(Counter(str(r["downstream_policy"].get("progress_tracking_allowed")) for r in results)),
            "lie_update_allowed_counts": dict(Counter(str(r["downstream_policy"].get("lie_update_allowed")) for r in results)),
        },
        "results": results,
    }


def load_config(path: Optional[str]) -> Dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if not path:
        return cfg
    loaded = load_json(path)
    if not isinstance(loaded, dict):
        raise ValueError("Config must be a JSON object.")
    for k, v in loaded.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            merged = dict(cfg[k])
            for kk, vv in v.items():
                if isinstance(vv, dict) and isinstance(merged.get(kk), dict):
                    mm = dict(merged[kk])
                    mm.update(vv)
                    merged[kk] = mm
                else:
                    merged[kk] = vv
            cfg[k] = merged
        else:
            cfg[k] = v
    return cfg


def verify_runtime_purity(paths: Iterable[Path]) -> Dict[str, Any]:
    forbidden = [
        "ground" + "_" + "truth",
        "calibration" + "_" + "label",
        "source" + "_" + "label",
        "gold" + "_" + "label",
        "benchmark" + "_" + "original" + "_" + "score",
    ]
    pattern_forbidden = [
        re.compile("BAL" + "_" + "WT2" + "_", re.IGNORECASE),
        re.compile(r"if\s+.*" + "essay" + "_" + "id", re.IGNORECASE),
    ]
    details = []
    ok = True
    for path in paths:
        text = path.read_text(encoding="utf-8")
        hits = [s for s in forbidden if s in text]
        hits.extend([p.pattern for p in pattern_forbidden if p.search(text)])
        if hits:
            ok = False
        details.append({"file": str(path), "status": "PASS" if not hits else "FAIL", "hits": hits})
    return {"ok": ok, "details": details}


def main() -> int:
    ap = argparse.ArgumentParser(description="Premium Automated Adjudicator v1.2")
    ap.add_argument("--verified", help="Verifier batch JSON")
    ap.add_argument("--scored", help="Scorer batch JSON, optional but recommended")
    ap.add_argument("--config", help="Adjudicator config JSON")
    ap.add_argument("--output", help="Output JSON")
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--audit-runtime-only", action="store_true")
    args = ap.parse_args()

    if args.audit_runtime_only:
        result = verify_runtime_purity([Path(__file__)])
        print("Premium Automated Adjudicator v1.2 runtime purity audit\n")
        for item in result["details"]:
            print(f"{item['status']}: {Path(item['file']).name}")
            if item["hits"]:
                print("  hits:", ", ".join(item["hits"]))
        print("\nRESULT:", "PASS" if result["ok"] else "FAIL")
        return 0 if result["ok"] else 2

    if not args.verified or not args.output:
        ap.error("--verified and --output are required unless --audit-runtime-only is used")

    cfg = load_config(args.config)
    verified_payload = load_json(args.verified)
    scored_payload = load_json(args.scored) if args.scored else None
    output = adjudicate_batch(verified_payload, scored_payload, cfg)
    write_json(output, args.output, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
