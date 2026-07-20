"""
pe_to_priority_directive_v2_v4.py
==================================
NEW FILE — pe_to_priority_directive_v2_v3.py preserved unchanged.

CHANGE vs. v3 (ARCHITECTURE CORRECTION)
-----------------------------------------
PREVIOUS DESIGN (v3 and earlier):
  PE's primary_limiter and secondary_limiters are consumed in ranked order.
  Result: only 2 rubrics typically appear in the directive (LR + GRA from PE top-2).
  TA and CC are never represented even when their bands are low.

CORRECTED DESIGN (v4):
  The directive ALWAYS contains exactly 4 focus areas — one per IELTS rubric:
    task_achievement, coherence_cohesion, lexical_resource, grammatical_range_accuracy.

  The PE is responsible for ranking FAMILIES *within* each rubric (dominant_families
  list per limiter). The adapter groups PE limiters by rubric, picks the top-pressure
  family within each rubric, and synthesises a focus area for any rubric the PE did
  not produce a limiter for (using band score to infer pressure).

  All 4 focus areas are then sorted by priority_pressure descending and assigned
  ranks 1–4.  Rubrics with PE limiters carry explicit pressure; synthesised rubrics
  carry inferred pressure = (9 − current_band) / 9, which means a Band 4 rubric with
  no PE limiter still outranks a Band 6 synthesised rubric.

WHY THIS MATTERS
  With v3, a Band 4 TA essay always got 0 TA focus areas in the directive because PE
  did not nominate TA as its primary/secondary.  V4 guarantees every rubric appears
  and the student always receives family-level guidance for all four criteria.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ── Criterion maps ────────────────────────────────────────────────────────────

_ABBREV_TO_V2: Dict[str, str] = {
    "GRA":                    "grammatical_range_accuracy",
    "grammar":                "grammatical_range_accuracy",
    "grammatical_range_accuracy": "grammatical_range_accuracy",
    "TR":                     "task_achievement",
    "TA":                     "task_achievement",
    "task_response":          "task_achievement",
    "task_achievement":       "task_achievement",
    "CC":                     "coherence_cohesion",
    "coherence_cohesion":     "coherence_cohesion",
    "LR":                     "lexical_resource",
    "lexical_resource":       "lexical_resource",
}

_V2_CRITERIA_ORDERED: Tuple[str, ...] = (
    "task_achievement",
    "coherence_cohesion",
    "lexical_resource",
    "grammatical_range_accuracy",
)

_V2_CRITERIA = frozenset(_V2_CRITERIA_ORDERED)
_META_RUBRICS = frozenset({"META", "EVALUABILITY", "SEMANTIC"})

# Default top family when PE produces no families for a rubric
_DEFAULT_FAMILIES: Dict[str, str] = {
    "task_achievement":           "TASK_COMPLETENESS",
    "coherence_cohesion":         "PARAGRAPH_STRUCTURE",
    "lexical_resource":           "LEXICAL_PRECISION",
    "grammatical_range_accuracy": "GRAMMAR_CONTROL",
}

# Default exercise types per criterion when PE gives nothing
_DEFAULT_EXERCISE_TYPES: Dict[str, List[str]] = {
    "task_achievement":           ["essay_planning", "rewrite"],
    "coherence_cohesion":         ["sentence_transformation", "rewrite"],
    "lexical_resource":           ["gap_fill", "error_correction"],
    "grammatical_range_accuracy": ["error_correction", "rewrite"],
}

_PRIORITY_REASONS = frozenset({
    "recurring_error", "high_impact_gap", "exam_urgency",
    "plateau_break", "new_weakness",
})

_DIFFICULTY_ENUM = frozenset({"foundational", "consolidation", "stretch"})

_SESSION_INTENTS = frozenset({
    "deep_focus", "broad_review", "consolidation", "exam_simulation",
})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _round_half(value: float) -> float:
    return max(0.0, min(9.0, round(value * 2) / 2))


def _resolve_rubric(raw: str) -> Optional[str]:
    """Map any PE rubric abbreviation to a v2 criterion string, or None."""
    if not raw:
        return None
    direct = _ABBREV_TO_V2.get(raw)
    if direct:
        return direct
    upper = raw.upper()
    if upper in _META_RUBRICS:
        return None
    return _ABBREV_TO_V2.get(upper)


def _extract_pressure(limiter: Dict[str, Any]) -> float:
    raw = limiter.get("pressure") or limiter.get("pressure_score") or 0.0
    try:
        return min(1.0, max(0.0, float(raw)))
    except (TypeError, ValueError):
        return 0.0


def _extract_band_from_map(bands_map: Dict[str, Any], criterion: str) -> float:
    """Pull current band for a v2 criterion from any PE bands_if_available dict."""
    for key in (criterion,) + tuple(
        abbr for abbr, v2 in _ABBREV_TO_V2.items() if v2 == criterion
    ):
        val = bands_map.get(key)
        if val is not None:
            try:
                return _round_half(float(val))
            except (TypeError, ValueError):
                pass
    return 0.0


def _derive_priority_reason(limiter: Dict[str, Any]) -> str:
    explicit = limiter.get("priority_reason") or limiter.get("reason")
    if explicit in _PRIORITY_REASONS:
        return explicit
    if limiter.get("exam_urgency"):
        return "exam_urgency"
    pattern = str(limiter.get("pattern_type", "")).lower()
    if "recur" in pattern or limiter.get("recurrence_flag"):
        return "recurring_error"
    if "plateau" in pattern:
        return "plateau_break"
    if "new" in pattern:
        return "new_weakness"
    severity = str(limiter.get("severity_tag", "")).lower()
    if severity == "critical":
        return "high_impact_gap"
    pressure = _extract_pressure(limiter)
    if pressure >= 0.7:
        return "high_impact_gap"
    if pressure >= 0.4:
        return "recurring_error"
    return "new_weakness"


def _derive_difficulty(current_band: float, limiter: Optional[Dict[str, Any]] = None) -> str:
    if limiter:
        explicit = (
            limiter.get("difficulty_recommendation")
            or limiter.get("difficulty_tier")
            or limiter.get("recommended_difficulty")
        )
        if explicit in _DIFFICULTY_ENUM:
            return explicit
    if current_band <= 4.5:
        return "foundational"
    if current_band <= 6.5:
        return "consolidation"
    return "stretch"


def _extract_dominant_families(limiter: Dict[str, Any]) -> List[str]:
    """Return ordered list of family name strings from dominant_families field."""
    raw = limiter.get("dominant_families", [])
    result: List[str] = []
    for item in raw:
        if isinstance(item, dict):
            fam = item.get("family")
            if fam:
                result.append(str(fam))
        elif isinstance(item, str) and item:
            result.append(item)
    return result


def _extract_exercise_types(
    limiter: Dict[str, Any],
    fine_grained: List[Dict[str, Any]],
    criterion: str,
) -> List[str]:
    direct = limiter.get("recommended_exercise_types") or limiter.get("exercise_types")
    if isinstance(direct, list) and direct:
        return [str(e) for e in direct][:4]

    etypes: List[str] = []
    for tgt in fine_grained:
        tgt_v2 = _resolve_rubric(tgt.get("rubric") or tgt.get("criterion", ""))
        if tgt_v2 == criterion:
            etype = tgt.get("exercise_type") or tgt.get("type")
            if etype and etype not in etypes:
                etypes.append(str(etype))

    return (etypes or _DEFAULT_EXERCISE_TYPES.get(criterion, ["error_correction"]))[:4]


def _derive_session_intent(pe_output: Dict[str, Any]) -> str:
    verdict = (
        (pe_output.get("band_unlock") or {}).get("verdict")
        or pe_output.get("session_intent")
        or pe_output.get("recommended_session_type")
        or ""
    )
    if verdict in _SESSION_INTENTS:
        return verdict
    mapping = {
        "focus": "deep_focus", "drill": "deep_focus",
        "review": "broad_review", "mixed": "broad_review",
        "consolidate": "consolidation", "consolidation": "consolidation",
        "exam": "exam_simulation", "simulate": "exam_simulation",
    }
    for key, intent in mapping.items():
        if key in verdict.lower():
            return intent
    primary = pe_output.get("primary_limiter", {}) or {}
    severity = str(primary.get("severity_tag", "")).lower()
    if severity == "critical":
        return "deep_focus"
    if len(pe_output.get("secondary_limiters", [])) > 2:
        return "broad_review"
    return "consolidation"


# ── Core: group limiters by rubric ────────────────────────────────────────────

def _group_limiters_by_criterion(
    pe_output: Dict[str, Any],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Collect all PE limiters (primary + secondary) and group by resolved v2 criterion.
    Limiters with unresolvable or META rubrics are discarded (logged to stderr).
    """
    import sys

    primary = pe_output.get("primary_limiter") or {}
    secondary_list = pe_output.get("secondary_limiters") or []

    all_limiters: List[Tuple[int, Dict[str, Any]]] = []
    if primary:
        all_limiters.append((0, primary))
    for i, lim in enumerate(secondary_list, start=1):
        all_limiters.append((i, lim))

    groups: Dict[str, List[Dict[str, Any]]] = {c: [] for c in _V2_CRITERIA}

    for order, lim in all_limiters:
        raw_rubric = lim.get("rubric", "")
        v2 = _resolve_rubric(raw_rubric)
        if v2 is None:
            print(
                f"[pe_directive_v4] Discarding limiter order={order} "
                f"rubric='{raw_rubric}' (META/unresolvable)",
                file=sys.stderr,
            )
            continue
        groups[v2].append(lim)

    return groups


# ── Core: build one focus area ────────────────────────────────────────────────

def _build_focus_area_from_limiter(
    criterion: str,
    limiter: Dict[str, Any],
    bands_avail: Dict[str, Any],
    fine_grained: List[Dict[str, Any]],
    band_unlock: Dict[str, Any],
    is_primary_pe: bool,
) -> Dict[str, Any]:
    current_band = _extract_band_from_map(bands_avail, criterion)
    if current_band == 0.0:
        # Try limiter's own band field
        direct = limiter.get("current_band") or limiter.get("band")
        if direct is not None:
            try:
                current_band = _round_half(float(direct))
            except (TypeError, ValueError):
                pass

    if is_primary_pe:
        target_raw = band_unlock.get("target_band") or limiter.get("target_band")
    else:
        target_raw = limiter.get("target_band")
    target_band = (
        _round_half(float(target_raw))
        if target_raw is not None
        else _round_half(current_band + 0.5)
    )

    dominant_families = _extract_dominant_families(limiter)
    # skill_tag = top family; validated to be non-empty
    skill_tag = dominant_families[0] if dominant_families else (
        limiter.get("label") or limiter.get("skill") or _DEFAULT_FAMILIES[criterion]
    )

    return {
        "criterion":                  criterion,
        "skill_tag":                  str(skill_tag),
        "dominant_families":          dominant_families,
        "current_band":               current_band,
        "target_band":                target_band,
        "priority_reason":            _derive_priority_reason(limiter),
        "recommended_difficulty":     _derive_difficulty(current_band, limiter),
        "recommended_exercise_types": _extract_exercise_types(limiter, fine_grained, criterion),
        "_pressure":                  _extract_pressure(limiter),
        "_source":                    "pe_limiter",
    }


def _build_synthesised_focus_area(
    criterion: str,
    bands_avail: Dict[str, Any],
) -> Dict[str, Any]:
    """Create a focus area for a rubric that PE produced no limiter for."""
    current_band = _extract_band_from_map(bands_avail, criterion)
    # Inferred pressure: weaker band → higher pressure
    inferred_pressure = (9.0 - current_band) / 9.0 if current_band > 0 else 0.3

    return {
        "criterion":                  criterion,
        "skill_tag":                  _DEFAULT_FAMILIES[criterion],
        "dominant_families":          [_DEFAULT_FAMILIES[criterion]],
        "current_band":               current_band,
        "target_band":                _round_half(current_band + 0.5) if current_band > 0 else 0.0,
        "priority_reason":            "high_impact_gap" if inferred_pressure > 0.6 else "new_weakness",
        "recommended_difficulty":     _derive_difficulty(current_band),
        "recommended_exercise_types": _DEFAULT_EXERCISE_TYPES[criterion],
        "_pressure":                  inferred_pressure,
        "_source":                    "synthesised_from_band",
    }


# ── Public API ────────────────────────────────────────────────────────────────

def pe_output_to_directive(
    pe_output: Dict[str, Any],
    submission_id: str,
    student_id: str,
    session_id: str,
    goal_band: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Convert PRIORITY_ENGINE_OUTPUT_V4 → PriorityDirective v2 (4-rubric design).

    Always returns exactly 4 focus_areas, one per IELTS rubric, ranked by
    priority_pressure descending.  Rubrics without PE limiters are synthesised
    from band scores so every criterion is always actionable.
    """
    # Unwrap batch envelope if present
    if "results" in pe_output and isinstance(pe_output.get("results"), list):
        results = pe_output["results"]
        pe_output = results[0] if results else pe_output

    bands_avail  = pe_output.get("bands_if_available") or {}
    fine_grained = pe_output.get("fine_grained_training_targets") or []
    band_unlock  = pe_output.get("band_unlock") or {}
    pattern_intel = pe_output.get("pattern_intelligence") or {}

    # Which limiter was the PE primary (affects target_band lookup)
    primary = pe_output.get("primary_limiter") or {}
    primary_rubric_v2 = _resolve_rubric(primary.get("rubric", ""))

    groups = _group_limiters_by_criterion(pe_output)

    focus_areas_raw: List[Dict[str, Any]] = []

    for criterion in _V2_CRITERIA_ORDERED:
        limiters = groups[criterion]
        if limiters:
            # Pick the highest-pressure limiter within this rubric
            best = max(limiters, key=_extract_pressure)
            is_primary = (criterion == primary_rubric_v2 and best is primary)
            fa = _build_focus_area_from_limiter(
                criterion, best, bands_avail, fine_grained, band_unlock, is_primary
            )
        else:
            fa = _build_synthesised_focus_area(criterion, bands_avail)

        focus_areas_raw.append(fa)

    # Sort by _pressure descending → assign ranks 1-4
    focus_areas_raw.sort(key=lambda fa: fa["_pressure"], reverse=True)

    focus_areas: List[Dict[str, Any]] = []
    for rank, fa in enumerate(focus_areas_raw, start=1):
        fa_clean = {k: v for k, v in fa.items() if not k.startswith("_")}
        fa_clean["rank"] = rank
        # Reorder keys for readability
        ordered = {
            "rank":                       fa_clean["rank"],
            "criterion":                  fa_clean["criterion"],
            "skill_tag":                  fa_clean["skill_tag"],
            "dominant_families":          fa_clean["dominant_families"],
            "current_band":               fa_clean["current_band"],
            "target_band":                fa_clean["target_band"],
            "priority_reason":            fa_clean["priority_reason"],
            "recommended_difficulty":     fa_clean["recommended_difficulty"],
            "recommended_exercise_types": fa_clean["recommended_exercise_types"],
            "_source":                    fa["_source"],
        }
        focus_areas.append(ordered)

    # Holistic from bands_avail
    band_values = [
        float(v) for v in bands_avail.values()
        if v is not None and str(v).replace(".", "", 1).isdigit()
    ]
    holistic = _round_half(sum(band_values) / len(band_values)) if band_values else 0.0
    gap = _round_half(float(goal_band) - holistic) if goal_band is not None else None

    # Highest-leverage criterion = rank-1 focus area
    leverage_criterion = focus_areas[0]["criterion"] if focus_areas else None

    band_gap_summary = {
        "current_holistic":           holistic,
        "goal_band":                  goal_band,
        "gap":                        gap,
        "highest_leverage_criterion": leverage_criterion,
    }

    eci_blocked = bool(
        pe_output.get("eci_blocked")
        or pattern_intel.get("eci_blocked")
        or band_unlock.get("eci_blocked")
    )
    escalate = bool(
        pe_output.get("escalate_to_human_review")
        or pe_output.get("escalation_flag")
        or pattern_intel.get("escalation_required")
    )

    session_intent = _derive_session_intent(pe_output)

    return {
        "directive_id":     str(uuid.uuid4()),
        "submission_id":    submission_id,
        "student_id":       student_id,
        "session_id":       session_id,
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        "focus_areas":      focus_areas,
        "session_intent":   session_intent,
        "band_gap_summary": band_gap_summary,
        "flags": {
            "skip_practice_this_session": eci_blocked,
            "escalate_to_human_review":   escalate,
        },
        "_pe_v4_passthrough": pe_output,
    }
