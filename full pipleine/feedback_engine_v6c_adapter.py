"""
feedback_engine_v6c_adapter.py
================================
NEW FILE — thin override layer on top of feedback_engine_v6b_adapter.py.

Strategy: import everything from v6b (which imports everything from v6),
then re-declare the two functions that have bugs I-5 and I-4b, and add
one new function for I-4b.

All other functions, constants, and helpers remain exactly as in v6b.

CHANGES vs feedback_engine_v6b_adapter.py
------------------------------------------

I-5  priority_reason not propagated into focus_area_feedback dicts.
     populate_focus_area_explanations_v6b reads priority_reason from the
     directive focus area dict, uses it to pick a priority_note sentence,
     but never writes it back to fa[].
     Result: feedback_report["focus_area_feedback"][*]["priority_reason"]
     is always absent. Students and downstream consumers cannot see why
     a criterion was ranked first.

     Fix in populate_focus_area_explanations_v6c():
       Add after dfa.get("priority_reason", ""):
         fa["priority_reason"] = priority_reason    # I-5

I-4b Only the dominant error family's rows appear per focus area.
     generate_feedback_v2 (frozen base) calls focus_areas[:3] and for
     each focus area selects annotated errors only from the top-pressure
     error family. Other error families within the same criterion (rubric)
     are silently dropped — students never see them in the detailed report.

     Fix via new function collect_all_annotated_errors_v6c():
       After generate_feedback_v2 runs, iterate chargeable_detector_rows.
       For each focus area, gather every row whose rubric matches the
       criterion and whose quote is not already present in annotated_errors.
       Append as new annotated_error entries so the full enrichment pipeline
       (enrich_annotated_errors, fill_null_corrections_v5, etc.) covers them.

USAGE (pipeline_runner_v12.py)
-------------------------------
    from feedback_engine_v6c_adapter import (
        # unchanged v6b exports (via *)
        enrich_annotated_errors,
        inject_missing_annotated_errors,
        fill_null_corrections_v5,
        enrich_all_corrections,
        enrich_with_sentence_context,
        expand_all_error_instances,
        build_broken_sentences_section_v5b,
        sanitize_score_summary_v6b,
        # v6c additions / overrides
        populate_focus_area_explanations_v6c,   # I-5 override
        collect_all_annotated_errors_v6c,       # I-4b new function
    )

    # After generate_feedback_v2():
    # I-4a (in runner inline — not this file)
    # I-4b
    feedback_report = collect_all_annotated_errors_v6c(
        feedback_report, chargeable_rows
    )
    # ... enrich_annotated_errors, inject_missing, fill_null, etc. unchanged ...
    # I-5 (replaces populate_focus_area_explanations_v6b call)
    feedback_report = populate_focus_area_explanations_v6c(
        feedback_report, fe_bundle_for_v6c, directive
    )
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

# Import everything from v6b — unchanged functions used directly.
from feedback_engine_v6b_adapter import *          # noqa: F401, F403

# Import the private helpers needed for the v6c overrides.
from feedback_engine_v6b_adapter import (          # noqa: F401
    _is_internal_text,
    _get_student_explanation,
    _get_fe_priority_for_criterion,
    _CRIT_LABEL,
    _PRIORITY_SENTENCES,
    _RUBRIC_TO_CRIT_FULL,
    _CRIT_LABEL_SHORT,
    _strip_artifact,
    _build_combo_explanation,
    _build_rewrite_prompt,
    _get_families_from_errormap,
    _HEADLINE_ARTIFACT_RE,
)

_ADAPTER_VERSION_C = "v6c"

# ---------------------------------------------------------------------------
# Rubric string → canonical criterion name
# (matches the rubric values the detector writes into chargeable_detector_rows)
# ---------------------------------------------------------------------------
_RUBRIC_TO_CRITERION: Dict[str, str] = {
    "grammar":              "grammatical_range_accuracy",
    "grammatical":          "grammatical_range_accuracy",
    "lexical_resource":     "lexical_resource",
    "lexical":              "lexical_resource",
    "coherence_cohesion":   "coherence_cohesion",
    "coherence":            "coherence_cohesion",
    "task_achievement":     "task_achievement",
    "task_response":        "task_achievement",
}

_CRITERION_TO_RUBRIC: Dict[str, str] = {
    "grammatical_range_accuracy": "grammar",
    "lexical_resource":           "lexical_resource",
    "coherence_cohesion":         "coherence_cohesion",
    "task_achievement":           "task_achievement",
}


# =============================================================================
# I-4b  collect_all_annotated_errors_v6c
#        New function — not present in v6b.
#        Adds all chargeable detector rows for each criterion to the
#        corresponding focus_area_feedback block, deduplicating by quote.
# =============================================================================

def collect_all_annotated_errors_v6c(
    report: Dict[str, Any],
    chargeable_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    I-4b fix: ensure every chargeable detector row for a criterion appears
    as an annotated_error in that criterion's focus_area_feedback block.

    generate_feedback_v2 only picks the dominant error family per focus area.
    This function adds the remaining rows from all other families so that
    the full enrichment pipeline (enrich_annotated_errors, fill_null_corrections_v5,
    etc.) can process them.

    Args:
        report:           feedback_report dict (mutated in-place AND returned).
        chargeable_rows:  det_out["results"][0]["scorer_payload"]
                          ["chargeable_detector_rows"]. May be [].

    Returns:
        The same report dict with annotated_errors extended per focus area.

    New audit fields written to report:
        "_i4b_collect_all_applied" : bool   — always True when this ran
        "_i4b_errors_added"        : int    — total new rows appended
        "_i4b_by_criterion"        : dict   — {criterion: count_added}
    """
    if not chargeable_rows:
        report["_i4b_collect_all_applied"] = True
        report["_i4b_errors_added"]        = 0
        report["_i4b_by_criterion"]        = {}
        return report

    # Build index: normalised rubric string → list of rows
    rows_by_rubric: Dict[str, List[Dict]] = defaultdict(list)
    for row in chargeable_rows:
        raw_rubric = (row.get("rubric") or "").lower().strip()
        if raw_rubric:
            rows_by_rubric[raw_rubric].append(row)

    total_added  = 0
    by_criterion: Dict[str, int] = {}

    for fa in report.get("focus_area_feedback", []) or []:
        criterion = fa.get("criterion", "")
        if not criterion:
            continue

        # Canonical rubric key for this criterion
        rubric_key = _CRITERION_TO_RUBRIC.get(criterion, "")
        if not rubric_key:
            continue

        # All rubric strings that map to this criterion
        matching_rows: List[Dict] = []
        for raw_rubric, rows in rows_by_rubric.items():
            mapped = _RUBRIC_TO_CRITERION.get(raw_rubric, "")
            if mapped == criterion:
                matching_rows.extend(rows)

        if not matching_rows:
            continue

        # Build set of quotes already present (to avoid duplicates)
        existing = fa.setdefault("annotated_errors", [])
        existing_quotes: set = set()
        for entry in existing:
            if isinstance(entry, dict):
                q = (entry.get("quote") or entry.get("excerpt") or "").strip()
                if q:
                    existing_quotes.add(q)

        added_here = 0
        for row in matching_rows:
            quote = (row.get("quote") or row.get("local_quote") or "").strip()
            if not quote:
                continue
            # Normalise quote for dedup (strip leading/trailing punctuation)
            quote_norm = quote.strip("\"'…. ")
            if quote_norm in existing_quotes or quote in existing_quotes:
                continue

            # Build a minimal annotated_error dict compatible with the
            # enrichment pipeline (enrich_annotated_errors expects these keys)
            new_entry: Dict[str, Any] = {
                "quote":        quote,
                "excerpt":      quote,             # alias used by some enrichers
                "sentence":     row.get("local_quote") or row.get("sentence", ""),
                "family":       row.get("family", ""),
                "criterion":    criterion,
                "rubric":       rubric_key,
                "correction":   None,              # fill_null_corrections_v5 will populate
                "explanation":  (
                    row.get("problem_statement")
                    or row.get("explanation")
                    or ""
                ),
                "severity":     row.get("severity", "moderate"),
                "repair_op":    row.get("repair_operation", ""),
                "_source":      "collect_all_v6c",
                "_i4b_added":   True,
            }
            existing.append(new_entry)
            existing_quotes.add(quote)
            existing_quotes.add(quote_norm)
            added_here += 1

        fa["annotated_errors"] = existing
        if added_here:
            fa["_i4b_added_errors"] = added_here
        if added_here or criterion not in by_criterion:
            by_criterion[criterion] = by_criterion.get(criterion, 0) + added_here
        total_added += added_here

    report["_i4b_collect_all_applied"] = True
    report["_i4b_errors_added"]        = total_added
    report["_i4b_by_criterion"]        = by_criterion
    return report


# =============================================================================
# I-5  populate_focus_area_explanations_v6c
#       Override of populate_focus_area_explanations_v6b.
#       Adds fa["priority_reason"] = priority_reason to each focus area dict.
#       All other logic is identical to v6b.
# =============================================================================

def populate_focus_area_explanations_v6c(
    report: Dict[str, Any],
    fe_bundle: Dict[str, Any],
    directive: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    I-5 fix: identical logic to populate_focus_area_explanations_v6b plus
    explicitly writing fa["priority_reason"] = priority_reason for each
    focus area dict.

    v6b reads priority_reason from the directive and uses it to select
    priority_note text, but never writes it to the focus area dict itself.
    This means consumers of the feedback report (web app, student view, QA)
    cannot read why a criterion was ranked first without re-reading the
    directive separately.

    New field written to each focus_area_feedback entry:
        "priority_reason" : str
            Value from directive.focus_areas[criterion].priority_reason.
            One of: "exam_urgency", "high_impact_gap", "recurring_error",
                    "plateau_break", "new_weakness", "" (empty if unknown).

    All existing v6b fields are unchanged:
        "explanation", "priority_note", "difficulty", "sessions_flagged"
        "_fe_v5_explanations_set", "_fe_v6_lr_leaks_fixed", "_adapter_version"
    """
    sf       = fe_bundle.get("student_feedback", {})
    msl      = sf.get("main_score_limiter", {})
    fe_prios = sf.get("top_learning_priorities", []) or []

    dir_fas: Dict[str, Dict] = {}
    if directive:
        for dfa in directive.get("focus_areas", []) or []:
            c = dfa.get("criterion", "")
            if c:
                dir_fas[c] = dfa

    msl_rubric = (msl.get("rubric") or "").upper()
    _rubric_to_crit = {
        "GRA": "grammatical_range_accuracy",
        "LR":  "lexical_resource",
        "TA":  "task_achievement",
        "CC":  "coherence_cohesion",
    }
    _crit_to_rubric = {v: k for k, v in _rubric_to_crit.items()}

    explanations_set = 0
    lr_leaks_fixed   = 0

    for fa in report.get("focus_area_feedback", []) or []:
        criterion  = fa.get("criterion", "")
        skill_tag  = fa.get("skill_tag", "")
        crit_label = _CRIT_LABEL.get(criterion, criterion)

        current_band = fa.get("current_band") or (
            (fa.get("score_summary") or {}).get("criteria_bands", {}).get(criterion)
        )
        target_band  = fa.get("target_band")
        dfa          = dir_fas.get(criterion, {})
        if not current_band:
            current_band = dfa.get("current_band")
        if not target_band:
            target_band  = dfa.get("target_band")
        priority_reason = dfa.get("priority_reason", "")

        # ── I-5 FIX ─────────────────────────────────────────────────────────
        # Write priority_reason into the focus area dict so callers can read
        # it directly without re-loading the directive.
        fa["priority_reason"] = priority_reason
        # ────────────────────────────────────────────────────────────────────

        explanation = fa.get("explanation") or ""
        if not explanation:
            fa_rubric = _crit_to_rubric.get(criterion)
            # 1. MSL match (only if not internal text)
            if fa_rubric and fa_rubric == msl_rubric:
                candidate = msl.get("explanation", "")
                if candidate and not _is_internal_text(candidate):
                    explanation = candidate
                elif candidate and _is_internal_text(candidate):
                    if criterion == "lexical_resource":
                        lr_leaks_fixed += 1

            # 2. FE top_learning_priorities match
            if not explanation:
                fe_prio = _get_fe_priority_for_criterion(
                    criterion, skill_tag, fe_prios
                )
                if fe_prio:
                    candidate = fe_prio.get("why_this_matters", "")
                    if candidate and not _is_internal_text(candidate):
                        explanation = candidate
                    elif candidate and _is_internal_text(candidate):
                        if criterion == "lexical_resource":
                            lr_leaks_fixed += 1

            # 3. Student-friendly template
            if not explanation:
                explanation = _get_student_explanation(
                    criterion, priority_reason,
                    current_band, target_band,
                    skill_tag, crit_label,
                )

        fa["explanation"] = explanation

        # priority_note (unchanged from v6b)
        priority_note = fa.get("priority_note") or ""
        if not priority_note:
            fa_rubric = _crit_to_rubric.get(criterion)
            if fa_rubric and fa_rubric == msl_rubric:
                priority_note = msl.get("first_action", "")
            if not priority_note and priority_reason:
                priority_note = _PRIORITY_SENTENCES.get(priority_reason, "")
            if not priority_note:
                priority_note = (
                    f"Work on {crit_label} to move closer to your target band."
                )
        fa["priority_note"] = priority_note

        # difficulty (F21, unchanged from v6b)
        if not fa.get("difficulty"):
            diff = dfa.get("recommended_difficulty", "")
            if diff:
                fa["difficulty"] = diff

        # sessions_flagged (unchanged from v6b)
        if not fa.get("sessions_flagged") and dfa.get("sessions_flagged"):
            fa["sessions_flagged"] = dfa["sessions_flagged"]

        if explanation:
            explanations_set += 1

    report["_fe_v5_explanations_set"] = explanations_set
    report["_fe_v6_lr_leaks_fixed"]   = lr_leaks_fixed
    report["_adapter_version"]         = _ADAPTER_VERSION_C
    return report
