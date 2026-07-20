"""
feedback_engine_v6b_adapter.py
================================
Thin override layer on top of feedback_engine_v6_adapter.py.

Strategy: import everything from v6, then re-declare the three functions that
have bugs B3/B4/B5. All other functions, constants, and helpers remain exactly
as in v6.

CHANGES vs feedback_engine_v6_adapter.py
------------------------------------------
B3  (F30-QA)  sanitize_score_summary_v6b()
              v6 writes  report["_fe_v5_headline_sanitized"] = bool.
              The QA report checks for "_fe_v6_essay_specific_headline" which
              v6 never wrote. Result: QA field f30_essay_specific_headline was
              always False in the runner's QA block, making headline success
              invisible.
              Fix: after all v6 sanitization logic runs (unchanged), also write:
                report["_fe_v6_essay_specific_headline"] = (crit_band is not None)
              True  → the F30 essay-specific path fired (band was available)
              False → MSL-title fallback or strip-artifact path was used

B4  (F29-QA)  populate_focus_area_explanations_v6b()
              v6 writes report["_fe_v5_explanations_set"] = count.
              The QA report checks for "_fe_v6_lr_leaks_fixed" which v6 never
              wrote (the logic runs, but the counter was never stored).
              Fix: count how many LR focus-area explanations were replaced
              because _is_internal_text() was True, then write:
                report["_fe_v6_lr_leaks_fixed"] = lr_leaks_fixed
              The counter increments only when:
                - criterion == "lexical_resource"
                - the candidate from MSL or FE priorities was present AND was
                  detected as internal text
                - the student-friendly template was used instead

B5  (F11-QA)  build_broken_sentences_section_v5b()
              v6 returns a dict with keys: schema, count, sentences, student_note.
              The QA report checks for "total_detected" and "families" which
              v6 never wrote.
              Fix: add two fields to the returned dict:
                "total_detected" : int — total broken_sentences_raw entries
                                   before the severity/families filter
                "families"       : list[str] — deduplicated union of all
                                   error_families across the kept sentences
              These fields are passive (QA/analytics only) and do not affect
              the student-facing output.

USAGE (pipeline_runner_v11.py)
-------------------------------
    from feedback_engine_v6b_adapter import (
        enrich_annotated_errors,
        inject_missing_annotated_errors,
        fill_null_corrections_v5,
        enrich_all_corrections,
        enrich_with_sentence_context,
        expand_all_error_instances,
        build_broken_sentences_section_v5b,      # ← v6b override (B5)
        populate_focus_area_explanations_v6b,    # ← v6b override (B4)
        sanitize_score_summary_v6b,              # ← v6b override (B3)
    )

    # Step 7h — broken sentences
    report["broken_sentences"] = build_broken_sentences_section_v5b(
        errormap.get("broken_sentences_raw", []), errormap
    )
    # Step 7i — focus area explanations
    report = populate_focus_area_explanations_v6b(report, fe_bundle, directive)
    # Step 7j — headline sanitize
    report = sanitize_score_summary_v6b(report, fe_bundle, band_scores=band_scores)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Import everything from v6 — unchanged functions are used directly.
from feedback_engine_v6_adapter import *          # noqa: F401, F403

# Import private helpers needed for the overrides.
# These are defined in v6 and are stable across b-patch versions.
from feedback_engine_v6_adapter import (          # noqa: F401
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

_ADAPTER_VERSION_B = "v6b"


# =============================================================================
# B5  build_broken_sentences_section_v5b
#     Adds "total_detected" and "families" to the return dict.
# =============================================================================

def build_broken_sentences_section_v5b(
    broken_sentences_raw: List[Dict[str, Any]],
    errormap_v3: Dict[str, Any],
) -> Dict[str, Any]:
    """
    B5 fix: identical logic to build_broken_sentences_section_v5() plus two
    additional QA fields on the returned dict:

      "total_detected" : int  — number of raw broken-sentence entries BEFORE
                                the severity/family-count filter.
      "families"       : list — deduplicated union of error_families across
                                ALL kept sentences (after the filter).

    Student-facing output (sentences, count, student_note) is unchanged.
    """
    total_detected = len(broken_sentences_raw)  # B5: count raw, before filter

    if not broken_sentences_raw:
        return {
            "schema":         "BROKEN_SENTENCES_V2",
            "count":          0,
            "sentences":      [],
            "student_note":   None,
            "total_detected": 0,    # B5
            "families":       [],   # B5
        }

    em_errors: List[Dict] = errormap_v3.get("errors", [])
    sentences: List[Dict[str, Any]] = []

    for raw in broken_sentences_raw:
        severity     = raw.get("severity", "moderate")
        raw_families = raw.get("error_families", [])
        char_start   = raw.get("char_start", 0)
        char_end     = raw.get("char_end",   0)

        crossref_families = _get_families_from_errormap(
            char_start, char_end, em_errors
        )
        if crossref_families:
            families = crossref_families
        elif raw_families:
            families = raw_families
        else:
            sent_idx = raw.get("sentence_index", -1)
            families = []
            if sent_idx >= 0:
                for err in em_errors:
                    loc = err.get("location", {})
                    if loc.get("sentence_index") == sent_idx:
                        et = err.get("error_type") or ""
                        if et and et not in families:
                            families.append(et)

        if severity == "moderate" and len(families) < 3:
            continue

        sent_text      = raw.get("sentence_text", "")
        explanation    = _build_combo_explanation(families)
        rewrite_prompt = _build_rewrite_prompt(sent_text)

        sentences.append({
            "sentence_index":  raw.get("sentence_index", -1),
            "sentence_text":   sent_text,
            "char_start":      char_start,
            "char_end":        char_end,
            "recoverability":  raw.get("recoverability_score", 0.0),
            "severity":        severity,
            "error_families":  families,
            "explanation":     explanation,
            "rewrite_prompt":  rewrite_prompt,
            "_family_source":  (
                "crossref" if crossref_families else
                ("detector" if raw_families else "sentence_index")
            ),
        })

    count = len(sentences)
    student_note = None
    if count >= 3:
        student_note = (
            f"⚠️  {count} sentences in your essay have overlapping errors that make "
            "them very hard to follow. Focus on these first — fixing them will have "
            "the biggest impact on your band score."
        )
    elif count > 0:
        student_note = (
            f"{count} sentence{'s' if count > 1 else ''} in your essay "
            f"need{'s' if count == 1 else ''} significant rewriting "
            "before other improvements will have full effect."
        )

    # B5: collect all families across kept sentences (deduplicated, sorted)
    all_families: List[str] = []
    seen_families: set = set()
    for s in sentences:
        for f in s.get("error_families", []):
            if f not in seen_families:
                seen_families.add(f)
                all_families.append(f)

    return {
        "schema":         "BROKEN_SENTENCES_V2",
        "count":          count,
        "sentences":      sentences,
        "student_note":   student_note,
        "total_detected": total_detected,   # B5 — QA field
        "families":       all_families,     # B5 — QA field
    }


# =============================================================================
# B4  populate_focus_area_explanations_v6b
#     Adds "_fe_v6_lr_leaks_fixed" counter to the report.
# =============================================================================

def populate_focus_area_explanations_v6b(
    report: Dict[str, Any],
    fe_bundle: Dict[str, Any],
    directive: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    B4 fix: identical logic to populate_focus_area_explanations_v6() plus a
    counter for how many LR explanations were replaced because the FE text
    contained internal diagnostic language.

    New field written to report:
      "_fe_v6_lr_leaks_fixed" : int
        Number of focus areas where criterion == "lexical_resource" AND the
        candidate text from MSL or top_learning_priorities was detected as
        internal text (and was replaced with a student-friendly template).

    "_fe_v5_explanations_set" is still written (unchanged from v6).
    "_adapter_version" is updated to "v6b".
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
    lr_leaks_fixed   = 0   # B4: count LR internal-text replacements

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

        explanation = fa.get("explanation") or ""
        if not explanation:
            fa_rubric = _crit_to_rubric.get(criterion)
            # 1. MSL match (only if not internal text)
            if fa_rubric and fa_rubric == msl_rubric:
                candidate = msl.get("explanation", "")
                if candidate and not _is_internal_text(candidate):
                    explanation = candidate
                elif candidate and _is_internal_text(candidate):
                    # B4: internal text detected — will use template; count if LR
                    if criterion == "lexical_resource":
                        lr_leaks_fixed += 1

            # 2. FE top_learning_priorities match (only if not internal text)
            if not explanation:
                fe_prio = _get_fe_priority_for_criterion(
                    criterion, skill_tag, fe_prios
                )
                if fe_prio:
                    candidate = fe_prio.get("why_this_matters", "")
                    if candidate and not _is_internal_text(candidate):
                        explanation = candidate
                    elif candidate and _is_internal_text(candidate):
                        # B4: another leak detected; count if LR
                        if criterion == "lexical_resource":
                            lr_leaks_fixed += 1

            # 3. Student-friendly template (F29 — always fires when above failed)
            if not explanation:
                explanation = _get_student_explanation(
                    criterion, priority_reason,
                    current_band, target_band,
                    skill_tag, crit_label,
                )

        fa["explanation"] = explanation

        # --- priority_note (unchanged from v6) ---
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

        # --- difficulty (F21, unchanged from v6) ---
        if not fa.get("difficulty"):
            diff = dfa.get("recommended_difficulty", "")
            if diff:
                fa["difficulty"] = diff

        # --- sessions_flagged (unchanged from v6) ---
        if not fa.get("sessions_flagged") and dfa.get("sessions_flagged"):
            fa["sessions_flagged"] = dfa["sessions_flagged"]

        if explanation:
            explanations_set += 1

    report["_fe_v5_explanations_set"] = explanations_set
    report["_fe_v6_lr_leaks_fixed"]   = lr_leaks_fixed   # B4 — QA field
    report["_adapter_version"]         = _ADAPTER_VERSION_B
    return report


# =============================================================================
# B3  sanitize_score_summary_v6b
#     Adds "_fe_v6_essay_specific_headline" flag to the report.
# =============================================================================

def sanitize_score_summary_v6b(
    report: Dict[str, Any],
    fe_bundle: Dict[str, Any],
    band_scores: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    B3 fix: identical sanitization logic to sanitize_score_summary_v6() plus
    an explicit flag for whether the essay-specific (F30) path fired.

    New field written to report:
      "_fe_v6_essay_specific_headline" : bool
        True  → the F30 path ran and used the actual criterion band number
                 (requires band_scores to be supplied with a valid crit_band)
        False → MSL-title fallback or _strip_artifact() path was used

    "_fe_v5_headline_sanitized" is still written (unchanged from v6).
    """
    ss       = report.get("score_summary") or {}
    headline = ss.get("headline_message", "") or ""

    if not headline:
        report["_fe_v6_essay_specific_headline"] = False  # B3
        return report

    original = headline

    sf           = fe_bundle.get("student_feedback", {})
    msl          = sf.get("main_score_limiter", {})
    msl_title    = (msl.get("title") or "").strip().rstrip(".")
    first_action = (msl.get("first_action") or "").strip()
    msl_rubric   = (msl.get("rubric") or "").upper()

    essay_specific = False   # B3: track whether F30 path fires

    # F30: use actual band when available
    if band_scores and msl_rubric:
        crit_key  = _RUBRIC_TO_CRIT_FULL.get(msl_rubric, "")
        crit_band = (
            band_scores.get("criteria_scores", {})
                       .get(crit_key, {})
                       .get("band")
        )
        crit_name = _CRIT_LABEL_SHORT.get(crit_key, msl_rubric.lower())

        if crit_band is not None:
            action = (first_action or "Focus on the patterns that repeat most often.").rstrip(".")
            clean_headline = (
                f"Your {crit_name} is at Band {crit_band:.1f}. "
                f"{action}."
            )
            essay_specific = True   # B3: the F30 path fired
        elif msl_title and first_action:
            clean_headline = f"{msl_title}. {first_action}"
        else:
            clean_headline = _strip_artifact(headline)
    elif msl_title and first_action:
        clean_headline = f"{msl_title}. {first_action}"
    else:
        clean_headline = _strip_artifact(headline)

    ss["headline_message"]   = clean_headline
    ss["_headline_original"] = original
    report["score_summary"]  = ss
    report["_fe_v5_headline_sanitized"]     = (clean_headline != original)
    report["_fe_v6_essay_specific_headline"] = essay_specific   # B3 — QA field
    return report
