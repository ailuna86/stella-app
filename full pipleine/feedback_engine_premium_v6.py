#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feedback_engine_premium_v6.py
VA English — Premium Feedback Engine V6
June 2026

PURPOSE
───────
This is a runnable engine, not a specification.
It produces two outputs from one PE V4.4 JSON file:
    1. FEEDBACK_BUNDLE_V6 JSON  — structured data for the frontend
    2. Markdown report (.md)    — student-readable feedback

HOW IT DIFFERS FROM THE SPEC FILES
────────────────────────────────────
feedback_generator_spec_v6*.py  — design documents; define data contracts
feedback_engine_premium_v6.py  — delivers actual student-facing content

Key addition: A2–B1 LANGUAGE CALIBRATION
All error explanations (what / why_score / how_to_fix) and action instructions
are written for A2–B1 English learners:
    • Short sentences (≤ 12 words each)
    • No jargon: no "syntactic", "defective", "finite verb", "evaluability"
    • Concrete before abstract
    • Verbs for instructions: "Check", "Write", "Look at", "Change"
    • Examples built into the instruction where possible

USAGE
─────
    python feedback_engine_premium_v6.py \\
        -i priority_out_4_4.json \\
        -o feedback_bundles.json \\
        --markdown feedback_report.md

    python feedback_engine_premium_v6.py -i priority_out_4_4.json --validate --summary

MARKDOWN STRUCTURE (per essay)
───────────────────────────────
    ## Essay {id}
    ### ★ Your Main Focus: {skill}
    {summary}
    {evidence items — quote / what / how_to_fix}
    **Your next step:** {first_action}

    ### Also Watch
    {watch_also list}

    ### Your Full Picture
    {all skills, ranked, with error types + sample quotes}

    ### Band Progress  (if available)
    {band_context}
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

# Import all logic from V6.2 spec — override only the language layer
from feedback_generator_spec_v6_2 import (
    # Gates
    check_eci_block, compute_eci, eci_tier, band_context_available,
    evidence_family_allowed, has_active_flag,
    # Logic
    build_band_context, build_learning_intelligence,
    fill_summary, compute_band_gain,
    # V6.2 builders (reused for non-language parts)
    build_short_report, build_detailed_report,
    # Maps
    RUBRIC_LABELS, FAMILY_TO_TARGET_V62, TARGET_TITLE_V62,
    _family_action_title, _rubric_label_v61, _rubric_score,
    _focus_label, FOCUS_NOTES, META_SKILLS,
    _family_name, _pressure_to_priority,
    rubric_label, repair_label, repair_micro_action, confidence_wording,
    ENGINE_CHAIN_TEMPLATE,
    validate_feedback_bundle as _validate_v62,
    FEEDBACK_GENERATOR_VERSION as _V62_VERSION,
    DETAILED_REPORT_INTRO,
)
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


FEEDBACK_ENGINE_VERSION = "feedback_engine_premium_v6.0"
FEEDBACK_BUNDLE_SCHEMA  = "FEEDBACK_BUNDLE_V6_ENGINE"


# =============================================================================
# A2–B1 LANGUAGE MAPS
# =============================================================================
# All student-facing strings calibrated for A2–B1 level:
#   • ≤ 12 words per sentence
#   • No technical jargon
#   • Verbs for instructions
#   • One idea per sentence

# ── WHAT: plain description of the error ─────────────────────────────────────
# Maps (family_upper, repair_op_upper) → A2-B1 explanation
# Fallback: family only, then generic

WHAT_A2B1: Dict[Tuple[str, str], str] = {
    # Sentence structure / clarity
    ("CLAUSE_STRUCTURE",    "FIX_CLAUSE"):          "This sentence is hard to understand.",
    ("CONSTRUCTION",        "FIX_CLAUSE"):          "This sentence is not complete.",
    ("WORD_ORDER",          "FIX_WORD_ORDER"):      "The word order is not natural here.",
    ("SENTENCE_STRUCTURE",  "FIX_CLAUSE"):          "This sentence is unclear.",
    ("SENTENCE_FRAGMENT",   "FIX_CLAUSE"):          "This is not a complete sentence.",
    # Subject–verb agreement
    ("SUBJECT_VERB_AGREEMENT", "FIX_SVA"):          "The verb does not match the subject.",
    ("SUBJECT_VERB_AGREEMENT", "FIX_VERB_FORM"):    "The verb does not match the subject.",
    # Verb forms
    ("VERB_FORM",   "FIX_VERB_FORM"):               "The verb form is wrong.",
    ("VERB_PATTERN","FIX_VERB_FORM"):               "The verb pattern is wrong.",
    ("VERB_TENSE",  "FIX_VERB_TENSE"):              "The verb tense is wrong.",
    ("MODAL_CONTROL","FIX_MODAL"):                  "After a modal verb, use the base form.",
    ("COMPARATIVE_FORM","FIX_COMPARATIVE"):         "This comparison is not written correctly.",
    ("COMPARATIVE_FORM","FIX_VERB_FORM"):           "This comparison is not written correctly.",
    # Articles and nouns
    ("ARTICLE",         "FIX_ARTICLE"):             "The article (a/an/the) is wrong here.",
    ("ARTICLE_NOUN",    "FIX_ARTICLE"):             "The article or noun form is wrong.",
    ("NOUN_NUMBER",     "FIX_NOUN"):                "Check singular or plural here.",
    ("DETERMINER",      "FIX_ARTICLE"):             "The determiner (this/these/a/the) is wrong.",
    # Prepositions
    ("PREPOSITION",         "FIX_PREPOSITION"):     "The preposition is wrong.",
    ("PREPOSITION_PATTERN", "FIX_PREPOSITION"):     "The preposition pattern is wrong.",
    # Punctuation
    ("PUNCTUATION",         "FIX_PUNCTUATION"):     "The punctuation is wrong here.",
    ("GRAMMAR_PUNCTUATION", "FIX_PUNCTUATION"):     "The punctuation is wrong here.",
    ("COMMA_SPLICE",        "FIX_PUNCTUATION"):     "Do not join two sentences with only a comma.",
    # Vocabulary
    ("COLLOCATION",         "FIX_COLLOCATION"):     "These words do not go together in English.",
    ("SEMANTIC_COMBINATION","FIX_COLLOCATION"):     "These words do not go together in English.",
    ("WORD_CHOICE",         "FIX_WORD_CHOICE"):     "This word does not fit the meaning here.",
    ("LEXICAL_PRECISION",   "FIX_WORD_CHOICE"):     "A more precise word is needed here.",
    ("WORD_FORM",           "FIX_WORD_FORM"):       "This is the wrong form of the word.",
    ("SPELLING",            "FIX_SPELLING"):        "This word is spelled wrong.",
    ("SPELLING_WORD_FORM",  "FIX_SPELLING"):        "Check the spelling and word form.",
    # Coherence
    ("MISSING_TRANSITION",  "FIX_TRANSITION"):      "A linking word is missing here.",
    ("DISCOURSE_CONNECTOR", "FIX_TRANSITION"):      "A linking word is missing or wrong.",
    ("COHESION",            "FIX_TRANSITION"):      "The connection between ideas is unclear.",
    # Task response
    ("IDEA_DEVELOPMENT",    "FIX_IDEA"):            "This idea needs more support.",
    ("POSITION_CLARITY",    "FIX_POSITION"):        "Your position is not clear here.",
}

WHAT_A2B1_FAMILY_FALLBACK: Dict[str, str] = {
    "CLAUSE_STRUCTURE":     "This sentence is hard to understand.",
    "CONSTRUCTION":         "This sentence is not complete.",
    "WORD_ORDER":           "The word order is not natural here.",
    "SENTENCE_STRUCTURE":   "This sentence is unclear.",
    "SUBJECT_VERB_AGREEMENT": "The verb does not match the subject.",
    "VERB_FORM":            "The verb form is wrong.",
    "VERB_PATTERN":         "The verb pattern is wrong.",
    "VERB_TENSE":           "The verb tense is wrong.",
    "MODAL_CONTROL":        "After a modal verb, use the base form.",
    "COMPARATIVE_FORM":     "This comparison is not written correctly.",
    "ARTICLE":              "The article is wrong here.",
    "ARTICLE_NOUN":         "The article or noun form is wrong.",
    "NOUN_NUMBER":          "Check singular or plural here.",
    "PREPOSITION":          "The preposition is wrong.",
    "PUNCTUATION":          "The punctuation is wrong here.",
    "GRAMMAR_PUNCTUATION":  "The punctuation is wrong here.",
    "COMMA_SPLICE":         "Two sentences are joined incorrectly.",
    "COLLOCATION":          "These words do not go together in English.",
    "SEMANTIC_COMBINATION": "These words do not go together in English.",
    "WORD_CHOICE":          "This word does not fit the meaning here.",
    "LEXICAL_PRECISION":    "A more precise word is needed here.",
    "WORD_FORM":            "This is the wrong form of the word.",
    "SPELLING":             "This word is spelled wrong.",
    "MISSING_TRANSITION":   "A linking word is missing here.",
    "COHESION":             "The connection between ideas is unclear.",
    "IDEA_DEVELOPMENT":     "This idea needs more support.",
    "POSITION_CLARITY":     "Your position is not clear here.",
}

# ── WHY_SCORE: why this affects the IELTS score ──────────────────────────────
# Maps rubric_code → plain A2-B1 sentence

WHY_SCORE_A2B1: Dict[str, str] = {
    "GRA": "Grammar mistakes reduce your Grammar score.",
    "LR":  "This kind of mistake reduces your Vocabulary score.",
    "CC":  "This makes your essay harder to follow. It reduces your Coherence score.",
    "TR":  "This makes your argument weaker. It reduces your Task Response score.",
    "META": "When sentences are unclear, it is harder to score your grammar and ideas.",
}

WHY_SCORE_FAMILY_OVERRIDE: Dict[str, str] = {
    "CLAUSE_STRUCTURE":     "Unclear sentences affect Grammar, Vocabulary, and Coherence scores.",
    "CONSTRUCTION":         "Unclear sentences affect Grammar, Vocabulary, and Coherence scores.",
    "SENTENCE_STRUCTURE":   "Unclear sentences affect Grammar, Vocabulary, and Coherence scores.",
    "COLLOCATION":          "Unnatural word combinations reduce your Vocabulary score.",
    "SEMANTIC_COMBINATION": "Unnatural word combinations reduce your Vocabulary score.",
    "SPELLING":             "Spelling mistakes reduce your Vocabulary score.",
    "WORD_FORM":            "Wrong word forms reduce your Vocabulary score.",
    "MISSING_TRANSITION":   "Missing links between ideas reduce your Coherence score.",
    "COMMA_SPLICE":         "Punctuation errors reduce your Grammar score.",
}

# ── HOW_TO_FIX: simple action instruction ────────────────────────────────────
# Maps repair_op_upper → A2-B1 instruction

HOW_TO_FIX_A2B1: Dict[str, str] = {
    "FIX_CLAUSE":       "Write this as one short, clear sentence. Use simple words.",
    "FIX_WORD_ORDER":   "Put the subject first, then the verb, then the object.",
    "FIX_SVA":          "Check: does the verb end in -s for he/she/it? For they/we/I use the base verb.",
    "FIX_VERB_FORM":    "After 'can/will/must/should/may', use the base verb (no -s, -ing, or -ed).",
    "FIX_VERB_TENSE":   "Decide: is this in the past, present, or future? Use the right tense.",
    "FIX_MODAL":        "After 'can/will/must/should/may', use the base verb. Example: 'can go', not 'can going'.",
    "FIX_COMPARATIVE":  "Use 'more [adjective] than' or '[adjective]-er than'. Example: 'more expensive than'.",
    "FIX_ARTICLE":      "Check: use 'a' for singular countable nouns (first mention), 'the' for known nouns.",
    "FIX_NOUN":         "Check: is this singular (one) or plural (more than one)? Add -s for plural.",
    "FIX_PREPOSITION":  "Look up which preposition goes with this word. Use a dictionary.",
    "FIX_PUNCTUATION":  "Use a full stop (.) to end each sentence. Do not join two sentences with only a comma.",
    "FIX_COLLOCATION":  "Look up this phrase in a dictionary. Use a natural English combination.",
    "FIX_WORD_CHOICE":  "Think about the exact meaning. Choose a more precise, common word.",
    "FIX_WORD_FORM":    "Check: do you need a verb, noun, adjective, or adverb? Use the right form.",
    "FIX_SPELLING":     "Look up the correct spelling. Write the word again correctly.",
    "FIX_TRANSITION":   "Add a linking word. Try: 'However', 'Therefore', 'In addition', 'For example'.",
    "FIX_IDEA":         "Add a reason or an example. Why is this true? Give evidence.",
    "FIX_POSITION":     "Write one clear sentence that says what you think. Example: 'I believe that...'.",
}

HOW_TO_FIX_FAMILY_FALLBACK: Dict[str, str] = {
    "CLAUSE_STRUCTURE":     "Write this as one short, clear sentence.",
    "CONSTRUCTION":         "Write this as one short, clear sentence.",
    "SUBJECT_VERB_AGREEMENT": "Check that the verb matches the subject.",
    "VERB_FORM":            "Use the correct verb form after modal verbs.",
    "COMPARATIVE_FORM":     "Use 'more [adjective] than' for comparisons.",
    "ARTICLE":              "Check if you need 'a', 'an', 'the', or no article.",
    "PREPOSITION":          "Look up the correct preposition in a dictionary.",
    "COLLOCATION":          "Look up this phrase and use a natural combination.",
    "WORD_CHOICE":          "Choose a more precise, common word.",
    "WORD_FORM":            "Check which form (verb/noun/adjective) is needed here.",
    "SPELLING":             "Look up the correct spelling and write it again.",
    "MISSING_TRANSITION":   "Add a linking word like 'However' or 'Therefore'.",
    "COMMA_SPLICE":         "Use a full stop between sentences, not a comma.",
}

# ── FOCUS NOTES — A2-B1 version ──────────────────────────────────────────────
FOCUS_NOTES_A2B1: Dict[str, str] = {
    "PRIMARY FOCUS": (
        "This is the most important thing to practise right now. "
        "Start here. It will help your score the most."
    ),
    "WORK ON NEXT": (
        "This is important too. Practise this after your main focus is better."
    ),
    "MONITOR": (
        "This is a real problem, but it is not the most important right now. "
        "Be aware of it as you practise."
    ),
    "DIAGNOSTIC": (
        "This is not something to practise directly. "
        "It will get better when your main focus improves."
    ),
}

# ── SECTION HEADINGS — A2-B1 version ─────────────────────────────────────────
HEADINGS_A2B1 = {
    "short_report":       "Your Focus Right Now",
    "primary_focus":      "★ Your Main Focus",
    "watch_also":         "Also Watch These Areas",
    "detailed_report":    "Your Full Picture",
    "band_context":       "Your Band Progress",
    "exercise_recs":      "What To Practise Next",
    "blocked":            "Feedback Not Available",
}

DETAILED_INTRO_A2B1 = (
    "Below you can see all the problems found in your essay, from most important to least important. "
    "The ★ problem is the one to work on first. "
    "The others are real problems too — it is good to know about them. "
    "Work through them in order as your writing gets better."
)


# =============================================================================
# A2–B1 EVIDENCE ITEM BUILDER
# =============================================================================

def _what_a2b1(family: str, repair_op: str, raw_problem: str = "") -> str:
    key = (family.upper(), repair_op.upper())
    if key in WHAT_A2B1:
        return WHAT_A2B1[key]
    fallback = WHAT_A2B1_FAMILY_FALLBACK.get(family.upper())
    if fallback:
        return fallback
    # Last resort: clean up the raw problem statement if available
    if raw_problem:
        p = raw_problem.strip().rstrip(".")
        if len(p) < 60:
            return p + "."
    return "There is an error here."


def _why_score_a2b1(family: str, rubric: str) -> str:
    override = WHY_SCORE_FAMILY_OVERRIDE.get(family.upper())
    if override:
        return override
    return WHY_SCORE_A2B1.get(rubric.upper(), "This kind of mistake can reduce your score.")


def _how_to_fix_a2b1(family: str, repair_op: str) -> str:
    direct = HOW_TO_FIX_A2B1.get(repair_op.upper())
    if direct:
        return direct
    fallback = HOW_TO_FIX_FAMILY_FALLBACK.get(family.upper())
    if fallback:
        return fallback
    return "Check this carefully and correct it."


def build_evidence_item_a2b1(ev_row: Dict[str, Any], rubric: str) -> Dict[str, Any]:
    """
    Build one evidence item with A2-B1 student-facing language.
    Overrides V6.2's mechanical WHAT/WHY/HOW templates.
    """
    family     = (ev_row.get("family") or "").upper()
    repair_op  = (ev_row.get("repair_operation") or "").upper()
    raw_prob   = ev_row.get("problem_statement") or ev_row.get("problem") or ""
    quote      = (ev_row.get("quote") or "").strip()
    local_q    = (ev_row.get("local_quote") or "").strip()

    return {
        "quote":       quote,
        "local_quote": local_q or None,
        "what":        _what_a2b1(family, repair_op, raw_prob),
        "why_score":   _why_score_a2b1(family, rubric),
        "how_to_fix":  _how_to_fix_a2b1(family, repair_op),
        "family":      family,
        "repair_op":   repair_op,
        "row_id":      ev_row.get("row_id"),
    }


# =============================================================================
# WEAKNESS PROFILE ITEM BUILDER — A2-B1
# =============================================================================

def build_weakness_profile_item_a2b1(
    sp:               Dict[str, Any],
    rank:             int,
    primary_skill:    str,
    primary_pressure: float,
    bands:            Dict[str, Any],
) -> Dict[str, Any]:
    """
    Same grouping logic as V6.2, but:
    - sample_evidence uses A2-B1 language (build_evidence_item_a2b1)
    - focus_note uses FOCUS_NOTES_A2B1
    """
    skill     = sp.get("skill") or ""
    rubric    = sp.get("rubric") or ""
    s_label   = sp.get("student_label") or skill.replace("_", " ").title()
    pressure  = float(sp.get("dependency_adjusted_pressure") or sp.get("pressure") or 0)
    p_level   = sp.get("priority_level") or _pressure_to_priority(pressure)

    f_label = _focus_label(skill, rank, pressure, primary_pressure, primary_skill)
    f_note  = FOCUS_NOTES_A2B1.get(f_label, "")

    # Safe evidence, indexed by family
    raw_examples = sp.get("examples") or []
    safe_rows: Dict[str, list] = {}
    for e in raw_examples:
        if (
            e.get("display_safety_status") == "student_safe"
            and evidence_family_allowed(skill, e.get("family") or "")
        ):
            fam = e.get("family") or ""
            safe_rows.setdefault(fam, []).append(e)

    any_safe = bool(safe_rows)

    # Grouped error families with A2-B1 samples
    dom_fams = sp.get("dominant_families") or []
    error_families = []
    total_errors   = 0

    for d in dom_fams:
        if isinstance(d, dict):
            fam = d.get("family") or ""
            cnt = d.get("count")
        else:
            fam = str(d)
            cnt = None

        if not fam:
            continue
        if cnt is not None:
            total_errors += int(cnt)

        rows_for_fam = safe_rows.get(fam) or []
        sample_rows  = rows_for_fam[:2]
        sample_ev    = []
        first_action = ""
        for ev_row in sample_rows:
            built = build_evidence_item_a2b1(ev_row, rubric)
            sample_ev.append({
                "quote":      built["quote"],
                "what":       built["what"],
                "how_to_fix": built["how_to_fix"],
            })
            if not first_action:
                first_action = built["how_to_fix"]

        error_families.append({
            "family":          fam,
            "family_plain":    _family_action_title(fam),
            "count":           cnt,
            "sample_evidence": sample_ev,
            "action":          first_action,
        })

    # Evidence note — A2-B1
    if f_label == "DIAGNOSTIC":
        evidence_note = (
            "You do not need to practise this directly. "
            "It will improve when your main focus gets better."
        )
    elif any_safe:
        evidence_note = "Examples from your essay are shown below."
    else:
        evidence_note = "Problems were found here, but no clear examples are available yet."

    return {
        "rank":             rank,
        "is_primary_focus": skill.upper() == primary_skill.upper(),
        "is_diagnostic":    skill.upper() in META_SKILLS,
        "skill":            skill,
        "student_label":    s_label,
        "rubric":           rubric,
        "rubric_plain":     _rubric_label_v61(rubric),
        "rubric_score":     _rubric_score(rubric, bands),
        "pressure":         round(pressure, 3),
        "priority_level":   p_level,
        "focus_label":      f_label,
        "focus_note":       f_note,
        "error_families":   error_families,
        "total_errors":     total_errors,
        "evidence_mode":    "full" if any_safe else "label_only",
        "evidence_note":    evidence_note,
    }


def build_detailed_report_a2b1(
    essay_result:     Dict[str, Any],
    primary_skill:    str,
    primary_pressure: float,
    bands:            Dict[str, Any],
) -> Dict[str, Any]:
    skill_profiles = essay_result.get("skill_profiles") or []
    sorted_profiles = sorted(
        [sp for sp in skill_profiles
         if float(sp.get("dependency_adjusted_pressure") or sp.get("pressure") or 0) > 0],
        key=lambda sp: float(
            sp.get("dependency_adjusted_pressure") or sp.get("pressure") or 0),
        reverse=True,
    )
    weakness_profile = [
        build_weakness_profile_item_a2b1(sp, rank + 1, primary_skill, primary_pressure, bands)
        for rank, sp in enumerate(sorted_profiles)
    ]
    seen: set = set()
    error_family_summary = []
    for item in weakness_profile:
        for fam in item["error_families"]:
            key = (fam["family"], item["rubric"])
            if key not in seen:
                seen.add(key)
                error_family_summary.append({
                    "family":       fam["family"],
                    "family_plain": fam["family_plain"],
                    "count":        fam.get("count"),
                    "skill":        item["skill"],
                    "rubric":       item["rubric"],
                    "rubric_plain": item["rubric_plain"],
                    "focus_label":  item["focus_label"],
                })
    return {
        "heading":                          HEADINGS_A2B1["detailed_report"],
        "intro":                            DETAILED_INTRO_A2B1,
        "weakness_profile":                 weakness_profile,
        "error_family_summary":             error_family_summary,
        "_gold_full_error_table_available": True,
    }


# =============================================================================
# GENERATE BUNDLE — A2-B1 ENGINE
# =============================================================================

def generate_feedback_bundle(essay_result: Dict[str, Any]) -> Dict[str, Any]:
    essay_id     = str(essay_result.get("essay_id") or "unknown")
    now          = datetime.now(timezone.utc).isoformat()
    engine_chain = ENGINE_CHAIN_TEMPLATE.replace("v6.0", "v6.engine")

    # ── Gate 1 ───────────────────────────────────────────────────────────────
    if check_eci_block(essay_result):
        return {
            "schema_version": FEEDBACK_BUNDLE_SCHEMA,
            "essay_id":       essay_id,
            "generated_at":   now,
            "engine_chain":   engine_chain,
            "status":         "blocked_eci",
            "downstream": {
                "primary_rubric":             None,
                "primary_pressure":           0.0,
                "dominant_families":          [],
                "eci":                        0.0,
                "eci_tier":                   "blocked",
                "training_target_count":      0,
                "has_student_safe_evidence":  False,
                "word_count":  (essay_result.get("metadata") or {}).get("word_count"),
                "overall_band": (essay_result.get("bands_if_available") or {}).get("overall"),
                "task_type":   (essay_result.get("metadata") or {}).get("task_type"),
                "gain_estimate":              None,
                "fastest_route_target_id":    None,
            },
        }

    # ── Gate 2 ───────────────────────────────────────────────────────────────
    eci  = compute_eci(essay_result)
    tier = eci_tier(eci)
    if tier == "blocked":
        return {
            "schema_version": FEEDBACK_BUNDLE_SCHEMA,
            "essay_id":       essay_id,
            "generated_at":   now,
            "engine_chain":   engine_chain,
            "status":         "blocked_eci",
            "downstream": {
                "primary_rubric": (essay_result.get("primary_limiter") or {}).get("rubric"),
                "primary_pressure": 0.0,
                "dominant_families": [],
                "eci":              round(eci, 4),
                "eci_tier":         "blocked",
                "training_target_count": 0,
                "has_student_safe_evidence": False,
                "word_count":   (essay_result.get("metadata") or {}).get("word_count"),
                "overall_band": (essay_result.get("bands_if_available") or {}).get("overall"),
                "task_type":    (essay_result.get("metadata") or {}).get("task_type"),
                "gain_estimate": None,
                "fastest_route_target_id": None,
            },
        }

    pl      = essay_result.get("primary_limiter") or {}
    meta    = essay_result.get("metadata") or {}
    bands   = essay_result.get("bands_if_available") or {}
    sl_list = essay_result.get("secondary_limiters") or []
    targets = essay_result.get("fine_grained_training_targets") or []

    rubric       = pl.get("rubric") or ""
    skill        = pl.get("skill") or ""
    label        = pl.get("student_label") or skill.replace("_", " ").title()
    pressure     = float(pl.get("dependency_adjusted_pressure") or 0.0)
    dom_fams     = [d["family"] for d in (pl.get("dominant_families") or []) if d.get("family")]
    top_family   = dom_fams[0] if dom_fams else ""
    overall_band = bands.get("overall")

    # ── Gates 3 + 5: evidence filter ─────────────────────────────────────────
    safe_evidence = [
        e for e in (pl.get("evidence") or [])
        if e.get("display_safety_status") == "student_safe"
        and evidence_family_allowed(skill, e.get("family") or "")
    ]
    if has_active_flag(essay_result, "single_family_dominance"):
        safe_evidence = safe_evidence[:2]

    evidence_items = [build_evidence_item_a2b1(e, rubric) for e in safe_evidence]
    evidence_mode  = "full" if evidence_items else "label_only"

    # ── Gate 6: band ─────────────────────────────────────────────────────────
    band_ctx: Optional[Dict[str, Any]] = None
    gain_est, gain_conf = "marginal", "speculative"
    if band_context_available(essay_result) and overall_band is not None:
        gain_est, gain_conf = compute_band_gain(overall_band, pressure)
        band_ctx = build_band_context(overall_band, rubric, pressure)

    # ── Primary feedback ──────────────────────────────────────────────────────
    conf_env  = pl.get("confidence_envelope") or {}
    conf_band = conf_env.get("confidence_band") or ("high" if tier == "high" else "medium")

    summary = fill_summary(
        rubric=rubric, overall_band=overall_band, tier=tier,
        skill_label=label, top_family=top_family,
        error_count=len(evidence_items),
    )

    primary_feedback: Dict[str, Any] = {
        "skill":              skill,
        "student_label":      label,
        "rubric":             rubric,
        "rubric_plain":       rubric_label(rubric),
        "priority_level":     pl.get("priority_level") or _pressure_to_priority(pressure),
        "eci_tier":           tier,
        "confidence_band":    conf_band,
        "summary":            summary,
        "evidence_items":     evidence_items,
        "evidence_mode":      evidence_mode,
        "confidence_wording": confidence_wording(tier, len(evidence_items)),
    }

    # ── Training targets ──────────────────────────────────────────────────────
    eligible_targets = [
        t for t in targets
        if (t.get("target_validation") or {}).get("display_safe")
        and float((t.get("target_validation") or {}).get("family_purity") or 0) >= 0.70
    ]
    if has_active_flag(essay_result, "primary_target_low_evidence"):
        eligible_targets = []

    exercise_recs = [
        {
            "target_id":      t.get("target_id") or "",
            "skill":          t.get("skill") or "",
            "family":         t.get("family") or "",
            "action_title":   _family_action_title(t.get("family") or ""),
            "example":        t.get("example_quote") or "",
            "correction":     t.get("example_correction"),
            "priority_level": t.get("priority_level") or "medium",
            "first_action":   _how_to_fix_a2b1(
                t.get("family") or "", t.get("repair_operation") or ""),
        }
        for t in eligible_targets
    ]

    # ── Learning Intelligence ─────────────────────────────────────────────────
    li_block = build_learning_intelligence(
        primary_limiter      = pl,
        fine_grained_targets = eligible_targets,
        rubric               = rubric,
        pressure             = pressure,
        gain_estimate        = gain_est,
        gain_confidence      = gain_conf,
    )

    # ── Secondary feedback ────────────────────────────────────────────────────
    secondary_feedback: Optional[Dict[str, Any]] = None
    if tier == "high" and sl_list:
        sl           = sl_list[0]
        sl_pressure  = float(sl.get("pressure") or 0.0)
        sl_skill     = sl.get("skill") or ""
        sl_rubric    = sl.get("rubric") or ""
        sl_label_str = sl_skill.replace("_", " ").title()
        sl_fams      = sl.get("evidence") or []
        sl_top_fam   = _family_name(sl_fams[0]) if sl_fams else ""

        if sl_pressure >= 0.5 * pressure and sl_skill.upper() != skill.upper():
            note = f"Also watch your {sl_label_str}"
            note += f" — especially {sl_top_fam}." if sl_top_fam else "."
            secondary_feedback = {
                "skill":         sl_skill,
                "student_label": sl_label_str,
                "rubric":        sl_rubric,
                "note":          note,
            }

    # ── Detailed report (A2-B1) ───────────────────────────────────────────────
    detailed_report = build_detailed_report_a2b1(essay_result, skill, pressure, bands)

    # ── Short report ──────────────────────────────────────────────────────────
    short_report = build_short_report(
        primary_feedback   = primary_feedback,
        secondary_feedback = secondary_feedback,
        band_ctx           = band_ctx,
        li_block           = li_block,
        detailed_report    = detailed_report,
        primary_skill      = skill,
    )
    short_report["heading"] = HEADINGS_A2B1["short_report"]

    # ── Downstream ────────────────────────────────────────────────────────────
    fastest_target_id = (li_block.get("fastest_improvement_route") or {}).get("target_id")
    downstream = {
        "primary_rubric":             rubric,
        "primary_pressure":           round(pressure, 4),
        "dominant_families":          dom_fams,
        "eci":                        round(eci, 4),
        "eci_tier":                   tier,
        "training_target_count":      len(eligible_targets),
        "has_student_safe_evidence":  len(safe_evidence) > 0,
        "word_count":                 meta.get("word_count"),
        "overall_band":               overall_band,
        "task_type":                  meta.get("task_type"),
        "gain_estimate":              gain_est,
        "fastest_route_target_id":    fastest_target_id,
    }

    bundle: Dict[str, Any] = {
        "schema_version":        FEEDBACK_BUNDLE_SCHEMA,
        "essay_id":              essay_id,
        "generated_at":          now,
        "engine_chain":          engine_chain,
        "status":                "ok" if tier == "high" else "partial",
        "short_report":          short_report,
        "primary_feedback":      primary_feedback,
        "learning_intelligence": li_block,
        "detailed_report":       detailed_report,
        "downstream":            downstream,
    }
    if secondary_feedback:
        bundle["secondary_feedback"] = secondary_feedback
    if band_ctx:
        bundle["band_context"] = band_ctx
    if exercise_recs:
        bundle["exercise_recommendations"] = exercise_recs

    return bundle


# =============================================================================
# MARKDOWN RENDERER
# =============================================================================

class MarkdownRenderer:
    """
    Renders a FEEDBACK_BUNDLE_V6_ENGINE bundle to a readable markdown report.
    Language is A2-B1: short sentences, no jargon, concrete instructions.
    """

    def render(self, bundle: Dict[str, Any]) -> str:
        essay_id = bundle.get("essay_id", "?")
        status   = bundle.get("status", "?")
        lines: List[str] = [f"## Essay {essay_id}\n"]

        if status == "blocked_eci":
            lines += [
                f"### {HEADINGS_A2B1['blocked']}",
                "",
                "There is not enough evidence to generate safe feedback for this essay.",
                "Please resubmit with a longer or clearer essay.",
                "",
            ]
            return "\n".join(lines)

        sr = bundle.get("short_report") or {}
        dr = bundle.get("detailed_report") or {}
        bc = bundle.get("band_context")
        er = bundle.get("exercise_recommendations") or []

        # ── Short report ─────────────────────────────────────────────────────
        lines += self._render_short_report(sr)
        lines.append("---\n")

        # ── Detailed report ───────────────────────────────────────────────────
        lines += self._render_detailed_report(dr)
        lines.append("---\n")

        # ── Band progress ─────────────────────────────────────────────────────
        if bc:
            lines += self._render_band_context(bc)
            lines.append("---\n")

        # ── Exercise recommendations ──────────────────────────────────────────
        if er:
            lines += self._render_exercises(er)
            lines.append("---\n")

        return "\n".join(lines)

    def _render_short_report(self, sr: Dict[str, Any]) -> List[str]:
        lines: List[str] = []
        pf = sr.get("primary_focus") or {}
        lines += [
            f"### {HEADINGS_A2B1['primary_focus']}: {pf.get('skill_label', '')} "
            f"({pf.get('rubric_plain', '')})",
            "",
            pf.get("summary", ""),
            "",
        ]

        # Evidence items (top 2)
        for ev in (pf.get("top_evidence") or []):
            lines += [
                f"> ❌ You wrote: **\"{ev['quote']}\"**",
                f">",
                f"> {ev['what']}",
                "",
            ]

        first_action = pf.get("first_action") or ""
        if first_action:
            lines += [f"**Your next step:** {first_action}", ""]

        # Watch also
        watch = sr.get("watch_also") or []
        if watch:
            lines += [f"### {HEADINGS_A2B1['watch_also']}", ""]
            for w in watch:
                label = w.get("focus_label", "")
                tag   = {"WORK ON NEXT": "⚠️", "MONITOR": "👁", "DIAGNOSTIC": "ℹ️"}.get(label, "•")
                lines.append(f"{tag} {w['note']}")
            lines.append("")

        return lines

    def _render_detailed_report(self, dr: Dict[str, Any]) -> List[str]:
        lines: List[str] = [
            f"### {HEADINGS_A2B1['detailed_report']}",
            "",
            dr.get("intro", DETAILED_INTRO_A2B1),
            "",
        ]
        for item in (dr.get("weakness_profile") or []):
            lines += self._render_weakness_item(item)
        return lines

    def _render_weakness_item(self, item: Dict[str, Any]) -> List[str]:
        lines: List[str] = []
        rank   = item.get("rank", "?")
        label  = item.get("focus_label", "")
        skill  = item.get("student_label", "")
        rubric = item.get("rubric_plain", "")
        score  = item.get("rubric_score")
        note   = item.get("focus_note", "")
        is_diag = item.get("is_diagnostic", False)

        badge = {"PRIMARY FOCUS": "★ PRIMARY FOCUS", "WORK ON NEXT": "⚠️ WORK ON NEXT",
                 "MONITOR": "👁 MONITOR", "DIAGNOSTIC": "ℹ️ DIAGNOSTIC"}.get(label, label)

        score_str = f" — Band {score}" if score is not None else ""
        lines += [
            f"#### {rank}. {skill} ({rubric}{score_str})  `{badge}`",
            "",
            note,
            "",
        ]

        if is_diag:
            return lines

        for ef in (item.get("error_families") or []):
            cnt_str = f" ({ef['count']} times found)" if ef.get("count") else ""
            lines += [
                f"**{ef['family_plain']}**{cnt_str}",
                "",
            ]
            for ev in (ef.get("sample_evidence") or []):
                lines += [
                    f"> ❌ **\"{ev['quote']}\"**",
                    f">",
                    f"> {ev['what']}",
                    f">",
                    f"> ✏️ {ev['how_to_fix']}",
                    "",
                ]
            if not ef.get("sample_evidence"):
                lines += [
                    f"*{item.get('evidence_note', 'No confirmed examples available.')}*",
                    "",
                ]

        return lines

    def _render_band_context(self, bc: Dict[str, Any]) -> List[str]:
        return [
            f"### {HEADINGS_A2B1['band_context']}",
            "",
            f"Current band: **{bc.get('overall')}** → "
            f"Target band: **{bc.get('target_band')}**",
            "",
            f"{bc.get('gain_estimate', '')} improvement — {bc.get('gain_confidence', '')}.",
            "",
        ]

    def _render_exercises(self, recs: List[Dict[str, Any]]) -> List[str]:
        lines: List[str] = [
            f"### {HEADINGS_A2B1['exercise_recs']}",
            "",
        ]
        for r in recs:
            title = r.get("action_title") or r.get("family") or r.get("target_id") or ""
            ex    = r.get("example") or ""
            act   = r.get("first_action") or ""
            lines += [
                f"- **{title}**",
                f"  Example from your essay: *\"{ex}\"*" if ex else "",
                f"  {act}" if act else "",
                "",
            ]
        return [ln for ln in lines if ln != ""]


# =============================================================================
# VALIDATION
# =============================================================================

def validate_feedback_bundle(bundle: Dict[str, Any]) -> List[str]:
    violations: List[str] = []
    if bundle.get("schema_version") != FEEDBACK_BUNDLE_SCHEMA:
        violations.append(f"schema_version wrong: {bundle.get('schema_version')}")

    status = bundle.get("status")
    if status not in ("ok", "partial", "blocked_eci"):
        violations.append(f"status invalid: {status}")
    if not bundle.get("downstream"):
        violations.append("downstream missing")

    if status == "blocked_eci":
        return violations

    pf = bundle.get("primary_feedback") or {}
    if not pf.get("eci_tier"):
        violations.append("primary_feedback.eci_tier missing")
    if pf.get("eci_tier") not in ("high", "medium"):
        violations.append("primary_feedback.eci_tier invalid")
    for i, ev in enumerate(pf.get("evidence_items") or []):
        for field in ("what", "why_score", "how_to_fix"):
            if not ev.get(field):
                violations.append(f"evidence_item[{i}].{field} missing (A2-B1 required)")

    if status in ("ok", "partial"):
        if not bundle.get("short_report"):
            violations.append("short_report missing")
        if not bundle.get("detailed_report"):
            violations.append("detailed_report missing")
        if not bundle.get("learning_intelligence"):
            violations.append("learning_intelligence missing")

    dr = bundle.get("detailed_report") or {}
    wp = dr.get("weakness_profile") or []
    if status in ("ok", "partial") and not wp:
        violations.append("weakness_profile empty")
    primary_count = sum(1 for item in wp if item.get("focus_label") == "PRIMARY FOCUS")
    if status in ("ok", "partial") and primary_count != 1:
        violations.append(f"exactly 1 PRIMARY FOCUS required, found {primary_count}")

    return violations


# =============================================================================
# CLI RUNNER
# =============================================================================

def main(argv=None):
    import argparse, json

    p = argparse.ArgumentParser(description="VA English Premium Feedback Engine V6")
    p.add_argument("--input",    "-i", required=True,
                   help="PE V4.4 JSON output file")
    p.add_argument("--output",   "-o", default=None,
                   help="JSON bundle output file (optional)")
    p.add_argument("--markdown", "-m", default=None,
                   help="Markdown report output file (optional)")
    p.add_argument("--validate", action="store_true",
                   help="Validate every bundle and report violations")
    p.add_argument("--summary",  action="store_true",
                   help="Print a brief console summary per essay")
    args = p.parse_args(argv)

    with open(args.input, encoding="utf-8") as f:
        pe_output = json.load(f)

    results = (
        pe_output.get("results")
        if isinstance(pe_output.get("results"), list)
        else [pe_output]
    )

    renderer         = MarkdownRenderer()
    bundles          = []
    md_parts: List[str] = ["# VA English — Feedback Report\n"]
    statuses         = {}
    violations_total = 0

    for r in results:
        b = generate_feedback_bundle(r)
        bundles.append(b)

        s = b.get("status", "?")
        statuses[s] = statuses.get(s, 0) + 1

        if args.validate:
            viols = validate_feedback_bundle(b)
            if viols:
                violations_total += len(viols)
                print(f"[VALIDATION] essay {b.get('essay_id')}: {viols}")

        md_parts.append(renderer.render(b))

        if args.summary and s != "blocked_eci":
            sr  = b.get("short_report") or {}
            dr  = b.get("detailed_report") or {}
            pf2 = (sr.get("primary_focus") or {})
            print(f"\n── Essay {b['essay_id']} [{s}] ──")
            print(f"  Focus: {pf2.get('skill_label')} ({pf2.get('rubric_plain')})")
            print(f"  Summary: {pf2.get('summary')}")
            for item in (dr.get("weakness_profile") or []):
                fams = item.get("error_families") or []
                print(f"  #{item['rank']} [{item['focus_label']}] {item['student_label']}")
                for ef in fams:
                    cnt   = f" ×{ef['count']}" if ef.get("count") else ""
                    samp  = ef.get("sample_evidence") or []
                    ex    = f'\n         e.g. "{samp[0]["quote"]}"' if samp else ""
                    print(f"      • {ef['family_plain']}{cnt}{ex}")

    print(f"\n[ENGINE v6] {len(bundles)} essays: {statuses}")
    if args.validate:
        print(f"[ENGINE v6] Validation: {violations_total} total violations.")

    if args.output:
        out = {"schema_version": FEEDBACK_BUNDLE_SCHEMA, "bundles": bundles}
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"[ENGINE v6] JSON → {args.output}")

    md_text = "\n---\n".join(md_parts)
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(md_text)
        print(f"[ENGINE v6] Markdown → {args.markdown}")

    if not args.output and not args.markdown:
        print(md_text)


if __name__ == "__main__":
    raise SystemExit(main())
