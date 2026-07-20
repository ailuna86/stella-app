#!/usr/bin/env python3
"""
priority_engine_v4_4_selfcontained.py
VA English Learning Assistant — Priority Engine V4.4 (Self-Contained)
June 2026

PREAMBLE: Changes vs priority_engine_v4_4_standalone.py
==========================================================

WHAT THIS FILE IS:
  priority_engine_v4_4_standalone.py (the previous file) was NOT truly standalone.
  It imported from priority_engine_v4_3_standalone.py and priority_engine_v4_1_standalone.py
  using class inheritance. To run it, all five PE files had to be in the same directory:
    priority_engine_v4_standalone.py      (V4.0 — actual base class)
    priority_engine_v4_1_standalone.py    (V4.1 — band/row extraction fixes)
    priority_engine_v4_2_standalone.py    (V4.2 — merge_inputs)
    priority_engine_v4_3_standalone.py    (V4.3 — META fix, band weights, ECI gate)
    priority_engine_v4_4_standalone.py    (V4.4 — all-META fallback fix)

  This file merges the entire V4.0 → V4.1 → V4.2 → V4.3 → V4.4 inheritance chain
  into a single Python file. No imports from any other PE version file are required.
  All logic is present here verbatim; no behaviour has changed.

ENGINE VERSION: priority_engine_v4.4.0  (unchanged from priority_engine_v4_4_standalone.py)

WHAT IS MERGED IN (all changes are additive — nothing removed):

  From V4.0 (priority_engine_v4_standalone.py):
    - Full base PriorityEngine class: Registry, Row, Essay dataclasses, all methods,
      8 compression passes, saturation decay, repair pattern bonus, V4 targets,
      band-relative strengths, LI payload, QA flags, self-validate.

  From V4.1 (priority_engine_v4_1_standalone.py):
    - extract_bands(): reads score_profile.rubrics.{rubric}.band (scorer v2.1.3 schema).
    - extract_rows(): synthesises lightweight rows from rubric_impact_map when no full
      row data is present (scorer-only input).
    - extract_metadata(): pulls task_type from gate_applications; pulls word_count,
      score_ready, scorer_confidence from score_profile.
    - normalize_result(): backfills task_type from metadata to task_profile.
    - qa_flags(): adds synthesised_rows_from_scorer warning flag.
    - _TASK_TYPE_RE regex for gate explanation parsing.

  From V4.2 (priority_engine_v4_2_standalone.py):
    - merge_inputs(): merges scorer fields onto detector results by essay_id.
    - MERGED_SCHEMA_VERSION = "MERGED_DETECTOR_SCORER_V1".
    - analyze_payload() surfaces _merge_summary in input_summary.

  From V4.3 (priority_engine_v4_3_standalone.py):
    - _FAMILY_RUBRIC_REMAP: CLAUSE_STRUCTURE/VERB_PATTERN → grammar (BUG-001).
    - _META_RUBRIC_VALUES: set of META rubric string variants.
    - _BAND_SENSITIVITY_WEIGHTS + _band_bucket(): GRA weight reduced at high bands (BUG-002).
    - _load_fp_patterns() / _row_is_fp(): FP suppression from fp_suppression_patterns.json (BUG-003).
    - _inject_tr_cc_rows(): synthetic TR/CC rows when rubric band gap ≥ 0.5 (BUG-004).
    - _fix_meta_primary() + _reselect_primary(): promote non-META primary limiter.
    - _apply_practice_discounts(): mastery discount from Practice Engine feedback.
    - _apply_eci_gate(): ECI hard block (clears targets + evidence).
    - _update_debug_counts(): V4.3 debug fields.
    - extract_rows() now applies FP suppression + family remap after row extraction.
    - __init__() loads FP patterns.
    - _ECI_BLOCK_FLAG_TYPE, TR_CC_SYNTHESIS_THRESHOLD, mastery discount constants.

  From V4.4 (priority_engine_v4_4_standalone.py):
    - _inject_all_meta_eci_block(): synthetic ECI block when ALL skill_profiles are META.
    - _reselect_primary() V4.4 fix: empty eligible list → inject ECI block instead of
      silently returning (was V4.3 BUG for essay 57).
    - _update_debug_counts(): adds debug_counts.all_meta_fallback_block.
    - analyze_payload(): stamps ENGINE_VERSION = "priority_engine_v4.4.0".

WHAT IS NOT CHANGED:
  - All compression passes, saturation logic, pressure formula, ECI formula.
  - Output schema (PRIORITY_ENGINE_OUTPUT_V4).
  - Registry loading (reads same JSON files from same paths).
  - All QA flag logic, strengths, pattern intelligence, band unlock.

CLI (identical to V4.3 / V4.4):
  # Full run (merged detector + scorer + practice feedback):
  python priority_engine_v4_4_selfcontained.py \\
      -i detector.json -s scorer.json -p practice_result.json \\
      -o out.json --knowledge "."

  # Merged input, no practice history:
  python priority_engine_v4_4_selfcontained.py \\
      -i detector.json -s scorer.json -o out.json --knowledge "."

  # Single input (scorer-only — V4.1 synthesised-row path):
  python priority_engine_v4_4_selfcontained.py \\
      -i scorer.json -o out.json --knowledge "."

  # Validate registry only:
  python priority_engine_v4_4_selfcontained.py \\
      -i any.json --validate-registry --knowledge "."
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Version constants
# ─────────────────────────────────────────────────────────────────────────────

ENGINE_VERSION          = "priority_engine_v4.4.0"
OUTPUT_SCHEMA_VERSION   = "PRIORITY_ENGINE_OUTPUT_V4"
MERGED_SCHEMA_VERSION   = "MERGED_DETECTOR_SCORER_V1"   # V4.2

DEFAULT_WINDOWS_KNOWLEDGE_PATH = (
    r"C:\Users\Ailuna Shamurzaeva\OneDrive\Desktop\AGART"
    r"\VA English, IELTS\premium version\priority engine"
)
DEFAULT_MANIFEST_NAME = "priority_engine_registry_manifest.json"

# ─────────────────────────────────────────────────────────────────────────────
# V4.0 — Rubric + severity lookup tables
# ─────────────────────────────────────────────────────────────────────────────

RUBRIC_ALIASES: Dict[str, str] = {
    "task_response": "TR", "argumentation": "TR", "TR": "TR", "tr": "TR",
    "coherence_cohesion": "CC", "cohesion": "CC", "CC": "CC", "cc": "CC",
    "lexical_resource": "LR", "LR": "LR", "lr": "LR",
    "grammar": "GRA", "grammatical_range_accuracy": "GRA",
    "grammatical_range_and_accuracy": "GRA", "GRA": "GRA", "gra": "GRA",
    "cross_rubric_gate": "META", "cross_rubric_meta": "META",
    "META": "META", "meta": "META",
}
RUBRIC_LONG: Dict[str, str] = {
    "TR": "task_response", "CC": "coherence_cohesion",
    "LR": "lexical_resource", "GRA": "grammar", "META": "meta",
}
SEVERITY_WEIGHTS: Dict[str, float] = {
    "low": 0.5, "minor": 0.5, "medium": 1.0, "moderate": 1.0,
    "high": 1.4, "severe": 1.8, "critical": 2.0,
}
LOCAL_SKILLS = {
    "LEXICAL_CONTROL", "LEXICAL_PRECISION", "LEXICAL_FORM_CONTROL",
    "COLLOCATION_CONTROL", "REGISTER_CONTROL", "SEMANTIC_PHRASE_CONTROL",
    "GRAMMAR_CONTROL", "SENTENCE_CONSTRUCTION", "LOCAL_LANGUAGE_STABILITY",
    "MEANING_RECOVERABILITY", "SEMANTIC_EVALUABILITY",
}
DISCOURSE_SKILLS = {
    "TASK_FULFILMENT", "POSITION_CONTROL", "IDEA_DEVELOPMENT",
    "SUPPORT_DEVELOPMENT", "REASONING_CHAIN_CONTROL", "EXAMPLE_USAGE",
    "COHERENCE_CONTROL", "COHESIVE_DEVICE_CONTROL", "DISCOURSE_EVALUABILITY",
}

FALLBACK_FAMILY_MAP: Dict[str, Dict[str, str]] = {
    "SPELLING":              {"core_skill": "LEXICAL_FORM_CONTROL",    "rubric": "LR"},
    "WORD_FORM":             {"core_skill": "LEXICAL_FORM_CONTROL",    "rubric": "LR"},
    "COLLOCATION":           {"core_skill": "COLLOCATION_CONTROL",     "rubric": "LR"},
    "WORD_CHOICE":           {"core_skill": "LEXICAL_PRECISION",       "rubric": "LR"},
    "LEXICAL_PRECISION":     {"core_skill": "LEXICAL_PRECISION",       "rubric": "LR"},
    "SEMANTIC_COMBINATION":  {"core_skill": "SEMANTIC_PHRASE_CONTROL", "rubric": "LR"},
    "REGISTER":              {"core_skill": "REGISTER_CONTROL",        "rubric": "LR"},
    "REPETITION":            {"core_skill": "LEXICAL_CONTROL",         "rubric": "LR"},
    "ARTICLE_DETERMINER":    {"core_skill": "GRAMMAR_CONTROL",         "rubric": "GRA"},
    "NOUN_NUMBER_COUNTABILITY": {"core_skill": "GRAMMAR_CONTROL",      "rubric": "GRA"},
    "SUBJECT_VERB_AGREEMENT": {"core_skill": "GRAMMAR_CONTROL",        "rubric": "GRA"},
    "VERB_FORM":             {"core_skill": "GRAMMAR_CONTROL",         "rubric": "GRA"},
    "VERB_TENSE":            {"core_skill": "GRAMMAR_CONTROL",         "rubric": "GRA"},
    "PREPOSITION_PATTERN":   {"core_skill": "GRAMMAR_CONTROL",         "rubric": "GRA"},
    "COMPARATIVE_FORM":      {"core_skill": "GRAMMAR_CONTROL",         "rubric": "GRA"},
    "GRAMMAR_PUNCTUATION":   {"core_skill": "GRAMMAR_CONTROL",         "rubric": "GRA"},
    "CLAUSE_STRUCTURE":      {"core_skill": "SENTENCE_CONSTRUCTION",   "rubric": "GRA"},
    "VERB_PATTERN":          {"core_skill": "SENTENCE_CONSTRUCTION",   "rubric": "GRA"},
    "CONSTRUCTION":          {"core_skill": "SENTENCE_CONSTRUCTION",   "rubric": "GRA"},
    "CONDITIONAL_STRUCTURE": {"core_skill": "SENTENCE_CONSTRUCTION",   "rubric": "GRA"},
    "PROMPT_COVERAGE":       {"core_skill": "TASK_FULFILMENT",         "rubric": "TR"},
    "PROMPT_RELEVANCE":      {"core_skill": "TASK_FULFILMENT",         "rubric": "TR"},
    "TASK_COMPLETENESS":     {"core_skill": "TASK_FULFILMENT",         "rubric": "TR"},
    "POSITION_CLARITY":      {"core_skill": "POSITION_CONTROL",        "rubric": "TR"},
    "UNSUPPORTED_CLAIM":     {"core_skill": "SUPPORT_DEVELOPMENT",     "rubric": "TR"},
    "WEAK_EXAMPLE":          {"core_skill": "EXAMPLE_USAGE",           "rubric": "TR"},
    "REASONING_CHAIN":       {"core_skill": "REASONING_CHAIN_CONTROL", "rubric": "TR"},
    "INCOMPLETE_ARGUMENT":   {"core_skill": "IDEA_DEVELOPMENT",        "rubric": "TR"},
    "LOGICAL_PROGRESSION":   {"core_skill": "COHERENCE_CONTROL",       "rubric": "CC"},
    "TOPIC_SHIFT":           {"core_skill": "COHERENCE_CONTROL",       "rubric": "CC"},
    "REFERENCE_BREAK":       {"core_skill": "COHESIVE_DEVICE_CONTROL", "rubric": "CC"},
    "TRANSITION":            {"core_skill": "COHESIVE_DEVICE_CONTROL", "rubric": "CC"},
    "MISSING_TRANSITION":    {"core_skill": "COHESIVE_DEVICE_CONTROL", "rubric": "CC"},
    "PARAGRAPH_STRUCTURE":   {"core_skill": "COHERENCE_CONTROL",       "rubric": "CC"},
}

FALLBACK_ONTOLOGY: Dict[str, Dict[str, Any]] = {
    "TASK_FULFILMENT":          {"ielts_rubric": "TR",  "student_label": "Task fulfilment",              "fundamental": True},
    "POSITION_CONTROL":         {"ielts_rubric": "TR",  "student_label": "Position control",             "fundamental": False},
    "IDEA_DEVELOPMENT":         {"ielts_rubric": "TR",  "student_label": "Idea development",             "fundamental": True},
    "SUPPORT_DEVELOPMENT":      {"ielts_rubric": "TR",  "student_label": "Support development",          "fundamental": False},
    "REASONING_CHAIN_CONTROL":  {"ielts_rubric": "TR",  "student_label": "Reasoning chain control",      "fundamental": False},
    "EXAMPLE_USAGE":            {"ielts_rubric": "TR",  "student_label": "Example use",                  "fundamental": False},
    "COHERENCE_CONTROL":        {"ielts_rubric": "CC",  "student_label": "Coherence and progression",    "fundamental": True},
    "COHESIVE_DEVICE_CONTROL":  {"ielts_rubric": "CC",  "student_label": "Linking and reference control","fundamental": False},
    "LEXICAL_CONTROL":          {"ielts_rubric": "LR",  "student_label": "Vocabulary control",           "fundamental": True},
    "LEXICAL_PRECISION":        {"ielts_rubric": "LR",  "student_label": "Vocabulary precision",         "fundamental": False},
    "COLLOCATION_CONTROL":      {"ielts_rubric": "LR",  "student_label": "Collocation control",          "fundamental": False},
    "LEXICAL_FORM_CONTROL":     {"ielts_rubric": "LR",  "student_label": "Spelling and word form",       "fundamental": False},
    "REGISTER_CONTROL":         {"ielts_rubric": "LR",  "student_label": "Register control",             "fundamental": False},
    "SEMANTIC_PHRASE_CONTROL":  {"ielts_rubric": "LR",  "student_label": "Meaningful phrase construction","fundamental": True},
    "GRAMMAR_CONTROL":          {"ielts_rubric": "GRA", "student_label": "Grammar accuracy",             "fundamental": True},
    "SENTENCE_CONSTRUCTION":    {"ielts_rubric": "GRA", "student_label": "Sentence construction",        "fundamental": True},
    "MEANING_RECOVERABILITY":   {"ielts_rubric": "cross_rubric_meta",  "student_label": "Meaning recoverability",  "fundamental": True},
    "SEMANTIC_EVALUABILITY":    {"ielts_rubric": "cross_rubric_gate",  "student_label": "Semantic evaluability",   "fundamental": True},
    "DISCOURSE_EVALUABILITY":   {"ielts_rubric": "cross_rubric_meta",  "student_label": "Discourse evaluability",  "fundamental": True},
}

# V3 fallback targets (used if 20_fine_grained_targets_v4.json is not available)
FINE_TARGETS_V3: List[Dict[str, Any]] = [
    {"id": "ARTICLE_NOUN_CONTROL", "skills": ["GRAMMAR_CONTROL"], "allowed_families": ["ARTICLE_DETERMINER", "NOUN_NUMBER_COUNTABILITY"], "keywords": [" a ", " an ", " the ", "people", "children", "costs"], "label": "Article + noun-number control", "practice_focus": "Drill articles and singular/plural noun forms in the exact noun phrases flagged.", "dependency_prerequisites": []},
    {"id": "VERB_FORM_PATTERN_CONTROL", "skills": ["GRAMMAR_CONTROL", "SENTENCE_CONSTRUCTION"], "allowed_families": ["VERB_FORM", "VERB_PATTERN", "VERB_TENSE", "SUBJECT_VERB_AGREEMENT"], "keywords": ["to ", "has to", "have to", "make", "makes", "working"], "label": "Verb form and verb-pattern control", "practice_focus": "Rewrite each damaged clause with subject + correct verb form + complement.", "dependency_prerequisites": []},
    {"id": "CLAUSE_BOUNDARY_CONTROL", "skills": ["SENTENCE_CONSTRUCTION"], "allowed_families": ["CLAUSE_STRUCTURE", "FRAGMENT", "RUN_ON", "CONSTRUCTION", "CONDITIONAL_STRUCTURE"], "keywords": ["if", "which", "because", "while"], "label": "Clause boundary and complex-sentence control", "practice_focus": "Simplify broken complex sentences, then recombine them with one clear connector.", "dependency_prerequisites": []},
    {"id": "QUANTITY_EXPRESSIONS", "skills": ["COLLOCATION_CONTROL", "LEXICAL_PRECISION", "GRAMMAR_CONTROL"], "allowed_families": ["COLLOCATION", "WORD_CHOICE", "QUANTIFIER_USAGE", "COMPARATIVE_FORM"], "keywords": ["amount", "number", "many", "much", "few", "little", "fewer", "more", "less"], "label": "Quantity and comparison expressions", "practice_focus": "Practise number of + countable nouns, amount of + uncountable nouns, fewer/less, more/greater.", "dependency_prerequisites": []},
    {"id": "CHANGE_COST_EXPRESSIONS", "skills": ["COLLOCATION_CONTROL", "LEXICAL_PRECISION"], "allowed_families": ["COLLOCATION", "WORD_CHOICE"], "keywords": ["increase", "decrease", "cost", "money", "spend", "budget", "pension", "health care"], "label": "Change, cost and spending collocations", "practice_focus": "Practise phrases such as increase in spending, healthcare costs, pension costs, government spending.", "dependency_prerequisites": []},
    {"id": "ABSTRACT_NOUN_COLLOCATIONS", "skills": ["COLLOCATION_CONTROL", "SEMANTIC_PHRASE_CONTROL"], "allowed_families": ["COLLOCATION", "SEMANTIC_COMBINATION", "WORD_CHOICE"], "keywords": ["ability", "degradation", "society", "economy", "tradition", "culture", "opportunity"], "label": "Abstract noun + verb/noun collocations", "practice_focus": "Replace translated phrases with natural academic combinations.", "dependency_prerequisites": []},
    {"id": "FORMAL_REGISTER_CONTROL", "skills": ["REGISTER_CONTROL"], "allowed_families": ["REGISTER"], "keywords": ["can't", "don't", "let's", "things", "a lot", "kids"], "label": "Formal academic register", "practice_focus": "Replace conversational wording and contractions with neutral academic forms.", "dependency_prerequisites": []},
    {"id": "SPELLING_WORD_FORM_ACCURACY", "skills": ["LEXICAL_FORM_CONTROL"], "allowed_families": ["SPELLING", "WORD_FORM"], "keywords": [], "label": "Spelling and word-form accuracy", "practice_focus": "Create a personal correction list and rewrite each misspelled word in 3 correct sentences.", "dependency_prerequisites": []},
    {"id": "CLAIM_REASON_EXAMPLE_CHAIN", "skills": ["IDEA_DEVELOPMENT", "SUPPORT_DEVELOPMENT", "REASONING_CHAIN_CONTROL"], "allowed_families": ["UNSUPPORTED_CLAIM", "REASONING_CHAIN", "INCOMPLETE_ARGUMENT", "CLAIM_SUPPORT_LINK"], "keywords": ["because", "therefore", "for example", "this means"], "label": "Claim → reason → example → explanation chain", "practice_focus": "For each body paragraph, write one claim, one reason, one specific example, and one explanation sentence.", "dependency_prerequisites": []},
    {"id": "SPECIFIC_EXAMPLE_ELABORATION", "skills": ["EXAMPLE_USAGE", "SUPPORT_DEVELOPMENT"], "allowed_families": ["WEAK_EXAMPLE", "EXAMPLE_INTEGRATION"], "keywords": ["for example", "such as"], "label": "Specific example elaboration", "practice_focus": "Turn broad/personal examples into task-relevant examples with who/where/why/result.", "dependency_prerequisites": ["CLAIM_REASON_EXAMPLE_CHAIN"]},
    {"id": "POSITION_AND_TASK_COVERAGE", "skills": ["TASK_FULFILMENT", "POSITION_CONTROL"], "allowed_families": ["PROMPT_COVERAGE", "TASK_COMPLETENESS", "POSITION_CLARITY", "POSITION_RESPONSE", "PROMPT_RELEVANCE"], "keywords": [], "label": "Position and required task coverage", "practice_focus": "Underline task parts, write a one-sentence answer, then check each paragraph against the required components.", "dependency_prerequisites": []},
    {"id": "REFERENCE_AND_TRANSITION_CONTROL", "skills": ["COHERENCE_CONTROL", "COHESIVE_DEVICE_CONTROL"], "allowed_families": ["REFERENCE_BREAK", "REFERENCE_COHESION", "TRANSITION", "MISSING_TRANSITION", "LOGICAL_PROGRESSION", "TOPIC_SHIFT"], "keywords": ["this", "they", "it", "also", "however", "on the other hand"], "label": "Reference and transition control", "practice_focus": "Replace vague this/they/it and choose connectors by logic: cause, contrast, result, example.", "dependency_prerequisites": []},
    {"id": "MEANING_RECOVERY_FIRST", "skills": ["MEANING_RECOVERABILITY", "SEMANTIC_EVALUABILITY", "SEMANTIC_PHRASE_CONTROL"], "allowed_families": ["SEMANTIC_COMBINATION", "CLAUSE_STRUCTURE", "CONSTRUCTION"], "keywords": [], "label": "Meaning recovery before discourse work", "practice_focus": "Recover the intended proposition first, then repair grammar and vocabulary around that proposition.", "dependency_prerequisites": []},
]

# V3 evidence integrity constants
V3_COST_CORE_WORDS = {
    "cost", "costs", "expense", "expenses", "spending", "expenditure", "budget",
    "funding", "funds", "money", "price", "prices", "tax", "taxes", "pension",
    "pensions", "healthcare", "health", "care", "financial", "finance",
    "pay", "payment",
}
V3_GENERIC_CHANGE_WORDS = {
    "increase", "decrease", "decline", "rise", "fall", "reduce", "reduction",
    "change", "changes", "changed", "changing",
}
V3_VALID_DETERMINER_RE = re.compile(
    r"^\s*(a\s+(few|little)\b|a\s+(number|variety|range|series|group|set|pair|couple"
    r"|majority|minority|proportion|percentage|lot)\s+of\b"
    r"|a\s+(large|small|great)\s+(amount|deal)\s+of\b"
    r"|as\s+a\s+(result|consequence)\b|a\s+(result|consequence)\b)", re.I
)
V3_SEMANTIC_FAMILIES = {
    "SEMANTIC_COMBINATION", "CLAUSE_STRUCTURE", "CONSTRUCTION",
    "PREDICATE_ARGUMENT", "WORD_ORDER", "VERB_PATTERN",
}

# ─────────────────────────────────────────────────────────────────────────────
# V4.1 — Regex for extracting task_type from gate application explanation strings
# ─────────────────────────────────────────────────────────────────────────────

_TASK_TYPE_RE = re.compile(r"task type ['\"]([a-z_]+)['\"]", re.I)

# ─────────────────────────────────────────────────────────────────────────────
# V4.3 — Family → Rubric remapping, META blocking
# ─────────────────────────────────────────────────────────────────────────────

_FAMILY_RUBRIC_REMAP: Dict[str, str] = {
    # These families are clearly GRA but land in META via SEMANTIC_EVALUABILITY
    "CLAUSE_STRUCTURE": "grammar",
    "VERB_PATTERN":     "grammar",
    # SEMANTIC_COMBINATION stays META but is secondary-only
}
_META_RUBRIC_VALUES = {"META", "meta"}

# V4.3 — Band-sensitivity weights (BUG-002)
def _band_bucket(overall_band: float) -> str:
    if overall_band <= 5.0:
        return "low"
    if overall_band <= 6.5:
        return "mid"
    return "high"

_BAND_SENSITIVITY_WEIGHTS: Dict[str, Dict[str, float]] = {
    "grammar":            {"low": 1.00, "mid": 0.90, "high": 0.70},
    "lexical_resource":   {"low": 1.00, "mid": 1.10, "high": 1.30},
    "task_response":      {"low": 1.00, "mid": 1.20, "high": 1.40},
    "coherence_cohesion": {"low": 1.00, "mid": 1.10, "high": 1.20},
    "META":               {"low": 0.50, "mid": 0.50, "high": 0.50},
}
_RUBRIC_LONG_MAP: Dict[str, str] = {
    "GRA": "grammar", "GRAMMAR": "grammar",
    "LR": "lexical_resource", "LEXICAL_RESOURCE": "lexical_resource",
    "TR": "task_response", "TASK_RESPONSE": "task_response",
    "CC": "coherence_cohesion", "COHERENCE_COHESION": "coherence_cohesion",
    "META": "META",
}

def _resolve_rubric_key(rubric_raw: str) -> str:
    return _RUBRIC_LONG_MAP.get(rubric_raw.upper(), rubric_raw.lower())

# V4.3 — FP suppression (BUG-003)
FP_PATTERNS_FILENAME = "fp_suppression_patterns.json"

def _load_fp_patterns(knowledge_path: str) -> List[str]:
    path = os.path.join(knowledge_path, FP_PATTERNS_FILENAME)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return [str(p).lower().strip() for p in (data.get("patterns") or []) if p]
    except Exception:
        return []

def _row_is_fp(row: Dict[str, Any], patterns: List[str]) -> bool:
    if not patterns:
        return False
    quote     = str(row.get("quote") or "").lower().strip()
    local_q   = str(row.get("local_quote") or "").lower().strip()
    prob_stmt = str(row.get("problem_statement") or "").lower().strip()
    for pat in patterns:
        if not pat:
            continue
        if pat in quote or pat in local_q or pat in prob_stmt:
            return True
    return False

# V4.3 — TR/CC synthesis threshold (BUG-004)
TR_CC_SYNTHESIS_THRESHOLD = 0.5
_TR_SYNTH_FAMILIES = ["PROMPT_COVERAGE", "WEAK_EXAMPLE", "POSITION_CLARITY"]
_CC_SYNTH_FAMILIES = ["PARAGRAPH_STRUCTURE", "LOGICAL_PROGRESSION", "MISSING_TRANSITION"]

# V4.3 — Practice Engine mastery discount
def _mastery_discount(mastery: float) -> float:
    if mastery < 0.50:
        return 0.00
    if mastery < 0.70:
        return 0.25
    if mastery < 0.85:
        return 0.50
    return 0.70

def _load_practice_discounts(practice_payload: Optional[Dict[str, Any]]) -> Dict[str, float]:
    if not practice_payload:
        return {}
    discounts: Dict[str, float] = {}
    skills = (
        _get_path(practice_payload, "backend_payload.adaptive_tutor.ranked_next_best_skills") or []
    )
    for s in (skills if isinstance(skills, list) else []):
        family  = s.get("family") or s.get("source_family")
        mastery = _safe_float(s.get("mastery"), None)
        if family and mastery is not None:
            if family not in discounts or mastery > discounts[family]:
                discounts[family] = mastery
    return discounts

# V4.3 — ECI block flag type
_ECI_BLOCK_FLAG_TYPE = "eci_safety_block"

# ─────────────────────────────────────────────────────────────────────────────
# V4.2 — merge_inputs
# ─────────────────────────────────────────────────────────────────────────────

_SCORER_OVERLAY_FIELDS = ["score_profile", "rubric_impact_map", "score_explanation_payload", "score_status"]
_SCORER_OVERLAY_PATHS  = [("qa", "scoring_record")]

def _essay_id_from_result(result: Dict[str, Any], idx: int) -> str:
    return str(_get_path(result, "identity.essay_id") or result.get("essay_id") or idx + 1)

def merge_inputs(
    detector_payload: Dict[str, Any],
    scorer_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Merge scorer fields onto detector results by essay_id.
    Returns a merged payload with MERGED_DETECTOR_SCORER_V1 schema.
    """
    scorer_results_raw = (
        scorer_payload.get("results")
        if isinstance(scorer_payload.get("results"), list)
        else [scorer_payload]
    )
    scorer_by_id: Dict[str, Dict[str, Any]] = {}
    for i, sr in enumerate(scorer_results_raw):
        if isinstance(sr, dict):
            scorer_by_id[_essay_id_from_result(sr, i)] = sr

    detector_results_raw = (
        detector_payload.get("results")
        if isinstance(detector_payload.get("results"), list)
        else [detector_payload]
    )

    merged_results: List[Dict[str, Any]] = []
    matched = 0
    unmatched = 0

    for i, dr in enumerate(detector_results_raw):
        if not isinstance(dr, dict):
            continue
        eid = _essay_id_from_result(dr, i)
        sr  = scorer_by_id.get(eid)
        if sr is None:
            unmatched += 1
            merged_results.append(dr)
            continue
        merged = copy.deepcopy(dr)
        for fld in _SCORER_OVERLAY_FIELDS:
            if fld in sr:
                if fld == "score_profile" or fld not in merged:
                    merged[fld] = sr[fld]
        for parent_key, child_key in _SCORER_OVERLAY_PATHS:
            if parent_key in sr and isinstance(sr[parent_key], dict):
                if parent_key not in merged or not isinstance(merged[parent_key], dict):
                    merged[parent_key] = {}
                if child_key in sr[parent_key]:
                    merged[parent_key][child_key] = sr[parent_key][child_key]
        merged["_v4_2_merged"]           = True
        merged["_v4_2_scorer_id_matched"] = eid
        matched += 1
        merged_results.append(merged)

    merged_payload = copy.deepcopy(detector_payload)
    if isinstance(detector_payload.get("results"), list):
        merged_payload["results"] = merged_results
    else:
        merged_payload = merged_results[0] if merged_results else merged_payload

    merged_payload["schema_version"]  = MERGED_SCHEMA_VERSION
    merged_payload["_merge_summary"]  = {
        "detector_schema":       detector_payload.get("schema_version"),
        "scorer_schema":         scorer_payload.get("schema_version"),
        "detector_essay_count":  len(detector_results_raw),
        "scorer_essay_count":    len(scorer_results_raw),
        "matched_essays":        matched,
        "unmatched_essays":      unmatched,
        "merged_at":             _now_iso(),
    }
    return merged_payload

# ─────────────────────────────────────────────────────────────────────────────
# V4.4 — all-META fallback ECI block
# ─────────────────────────────────────────────────────────────────────────────

_ALL_META_BLOCK_REASON = "all_meta_no_eligible_rubric"

def _inject_all_meta_eci_block(essay_r: Dict[str, Any]) -> None:
    """
    Called when _reselect_primary() finds zero non-META skill_profiles.
    Injects a synthetic ECI safety block so downstream systems suppress
    student-facing output rather than emitting a META primary limiter.
    """
    qa_flags = essay_r.setdefault("qa_flags", [])
    already_injected = any(
        isinstance(f, dict)
        and f.get("flag_type") == _ECI_BLOCK_FLAG_TYPE
        and f.get("flag_reason") == _ALL_META_BLOCK_REASON
        for f in qa_flags
    )
    if not already_injected:
        qa_flags.append({
            "flag_type":   _ECI_BLOCK_FLAG_TYPE,
            "flag_reason": _ALL_META_BLOCK_REASON,
            "flag_detail": (
                "All skill_profiles have rubric=META after FAMILY_RUBRIC_REMAP. "
                "No IELTS rubric (GRA/LR/TR/CC) can be selected as primary_limiter. "
                "This essay has insufficient non-META evidence for reliable prioritisation."
            ),
            "severity": "critical",
            "source":   "v4_4_all_meta_fallback",
        })
    pl = essay_r.setdefault("primary_limiter", {})
    pl["rubric"]                  = None
    pl["_v4_4_all_meta_fallback"] = True
    essay_r["_v4_4_all_meta_detected"] = True

# ─────────────────────────────────────────────────────────────────────────────
# Shared utility functions (used by merge_inputs and the class)
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _safe_float(x: Any, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        if x is None:
            return default
        v = float(x)
        return default if math.isnan(v) else v
    except Exception:
        return default

def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default

def _clamp(x: Any, lo: float, hi: float, default: float = 0.0) -> float:
    v = _safe_float(x, default)
    if v is None:
        v = default
    return max(lo, min(hi, v))

def _get_path(obj: Any, path: str, default: Any = None) -> Any:
    cur = obj
    for part in (path.split(".") if path else []):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur

def _norm_rubric(x: Any) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip()
    return RUBRIC_ALIASES.get(s, RUBRIC_ALIASES.get(s.lower()))

def _norm_family(family: Any, issue_code: Any = None) -> str:
    if family:
        return str(family).strip().upper()
    if issue_code:
        s = str(issue_code).strip().upper()
        for pref in ("TR_", "G_", "L_", "C_", "A_"):
            if s.startswith(pref):
                s = s[len(pref):]
        aliases = {
            "SV_AGREEMENT": "SUBJECT_VERB_AGREEMENT",
            "NOUN_NUMBER": "NOUN_NUMBER_COUNTABILITY",
            "PRECISION": "LEXICAL_PRECISION",
            "RANGE": "GRAMMATICAL_RANGE",
            "COMMA_TRANSITION": "GRAMMAR_PUNCTUATION",
            "SPACING": "GRAMMAR_PUNCTUATION",
        }
        return aliases.get(s, s)
    return "UNKNOWN"

def _read_json_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _write_json_file(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# V3 integrity helpers
def _v3_row_text(row: "Row") -> str:
    return " ".join(
        str(x or "") for x in [row.quote, row.local_quote, row.problem_statement, row.explanation]
    ).lower()

def _v3_has_cost_domain(rows: "List[Row]") -> bool:
    text = " ".join(_v3_row_text(r) for r in rows)
    return any(re.search(r"\b" + re.escape(w) + r"\b", text) for w in V3_COST_CORE_WORDS)

def _v3_article_row_invalid(row: "Row") -> bool:
    txt = (row.quote or row.local_quote or "").strip()
    return bool(V3_VALID_DETERMINER_RE.search(txt))

def _v3_surface_contradiction(row: "Row") -> bool:
    txt = (row.quote or row.local_quote or "").strip()
    if not txt:
        return True
    low = txt.lower()
    if row.family == "ARTICLE_DETERMINER" and _v3_article_row_invalid(row):
        return True
    if row.family == "SUBJECT_VERB_AGREEMENT":
        if re.search(r"\b(this|that|it)\s+\w+s\b", low):
            return True
    if row.family == "VERB_FORM":
        if (re.search(r"\b(may|will|can|could|should|must|would)\s+[a-z]+\b", low)
                and not re.search(r"\b(may|will|can|could|should|must|would)\s+(being|been|is|are|was|were|[a-z]+ing|[a-z]+ed)\b", low)):
            return True
    return False

def _v3_row_safety(row: "Row") -> dict:
    reasons = []
    if not row.local_quote:
        reasons.append("missing_full_sentence_or_local_quote")
    if row.review_only or row.support_only:
        reasons.append("support_or_review_only")
    if row.confidence < 0.60:
        reasons.append("low_confidence")
    if _v3_surface_contradiction(row):
        reasons.append("surface_pattern_contradiction_or_valid_construction")
    status = "student_safe" if not reasons else "teacher_debug_only"
    return {
        "display_safety_status": status,
        "display_safety_reasons": reasons,
        "full_sentence_available": bool(row.local_quote),
    }

def _v3_target_family_allowed(target_id: str, row: "Row", allowed_families: List[str]) -> bool:
    tid = str(target_id or "").upper()
    if tid.startswith("SKILL_SEMANTIC_EVALUABILITY") or tid == "SKILL_SEMANTIC_EVALUABILITY":
        return row.family in V3_SEMANTIC_FAMILIES or row.skill == "SEMANTIC_EVALUABILITY"
    if not allowed_families:
        return True
    return row.family in set(allowed_families)

def _v3_target_purity(target_id: str, rows: "List[Row]", allowed_families: List[str]) -> float:
    if not rows:
        return 0.0
    return sum(
        1 for r in rows
        if _v3_target_family_allowed(target_id, r, allowed_families)
        and _v3_row_safety(r)["display_safety_status"] == "student_safe"
    ) / max(1, len(rows))

# Public aliases (backwards compat with any external code that called the bare names)
now_iso    = _now_iso
safe_float = _safe_float
safe_int   = _safe_int
get_path   = _get_path
read_json_file  = _read_json_file
write_json_file = _write_json_file

# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Registry:
    knowledge_path: str
    manifest_path: Optional[str] = None
    resources: Dict[str, Any] = field(default_factory=dict)
    loaded: Dict[str, str] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)
    manifest: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, knowledge_path: Optional[str] = None, manifest_path: Optional[str] = None) -> "Registry":
        kp  = knowledge_path or DEFAULT_WINDOWS_KNOWLEDGE_PATH
        reg = cls(kp, manifest_path)
        reg.manifest = reg._load_manifest()
        for item in reg.manifest.get("resources", []):
            rid, fn = item.get("id"), item.get("filename")
            if not rid or not fn:
                continue
            data = reg._load_resource(fn)
            if data is None:
                reg.missing.append(fn)
            else:
                reg.resources[rid] = data
                reg.loaded[rid]    = fn
        return reg

    def _load_manifest(self) -> Dict[str, Any]:
        candidates = []
        if self.manifest_path:
            candidates.append(self.manifest_path)
        if self.knowledge_path:
            candidates.append(os.path.join(self.knowledge_path, DEFAULT_MANIFEST_NAME))
        candidates.append(os.path.join(os.getcwd(), DEFAULT_MANIFEST_NAME))
        candidates.append(os.path.join(os.path.dirname(__file__), DEFAULT_MANIFEST_NAME))
        for p in candidates:
            if p and os.path.exists(p):
                try:
                    return _read_json_file(p)
                except Exception:
                    pass
        return {
            "schema_version": "BUILTIN_MINIMAL_MANIFEST",
            "resources": [
                {"id": "core_skill_ontology",           "filename": "02_core_skill_ontology_priority_engine.json"},
                {"id": "skill_dependency_graph",         "filename": "03_skill_dependency_graph.json"},
                {"id": "band_skill_matrix",              "filename": "04_band_skill_matrix_writing_task2.json"},
                {"id": "family_to_skill_map",            "filename": "05_error_family_to_core_skill_map.json"},
                {"id": "priority_engine_rules",          "filename": "06_priority_engine_rules.json"},
                {"id": "strength_rules",                 "filename": "07_strength_rules.json"},
                {"id": "diagnostic_signatures",          "filename": "08_diagnostic_signatures.json"},
                {"id": "remediation_map",                "filename": "09_remediation_map.json"},
                {"id": "skill_observability_catalog",    "filename": "13_skill_observability_catalog.json"},
                {"id": "detector_scorer_field_map",      "filename": "14_detector_scorer_to_priority_field_map.json"},
                {"id": "priority_skill_inference_rules", "filename": "15_priority_skill_inference_rules.json"},
                {"id": "priority_input_contract",        "filename": "17_priority_engine_input_contract.json"},
                {"id": "v4_fine_targets",                "filename": "20_fine_grained_targets_v4.json"},
                {"id": "v4_repair_pattern_labels",       "filename": "21_repair_pattern_labels.json"},
                {"id": "v4_band_strength_thresholds",    "filename": "22_band_relative_strength_thresholds.json"},
                {"id": "v4_task_type_notes",             "filename": "23_task_type_specific_notes.json"},
                {"id": "v4_saturation_params",           "filename": "24_pressure_saturation_params.json"},
            ],
        }

    def _load_resource(self, filename: str) -> Optional[Any]:
        if not self.knowledge_path:
            return None
        if os.path.isdir(self.knowledge_path):
            p = os.path.join(self.knowledge_path, filename)
            if os.path.exists(p):
                try:
                    return _read_json_file(p)
                except Exception:
                    return None
        if os.path.isfile(self.knowledge_path) and zipfile.is_zipfile(self.knowledge_path):
            try:
                with zipfile.ZipFile(self.knowledge_path) as z:
                    with z.open(filename) as f:
                        return json.load(f)
            except Exception:
                return None
        return None

    def get(self, rid: str, default: Any = None) -> Any:
        return self.resources.get(rid, default)


# ─────────────────────────────────────────────────────────────────────────────
# Row / Essay dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Row:
    row_id:                  str
    essay_id:                str
    family:                  str
    skill:                   str
    rubric:                  str
    quote:                   str  = ""
    local_quote:             str  = ""
    sentence_index:          Optional[int] = None
    paragraph_index:         Optional[int] = None
    paragraph_position:      str  = "body"
    severity:                str  = "medium"
    confidence:              float = 0.75
    layer:                   str  = ""
    source:                  str  = ""
    chargeable:              bool  = True
    review_only:             bool  = False
    support_only:            bool  = False
    score_charge_weight:     Optional[float] = None
    recoverability_gain:     float = 0.0
    evaluability_gain:       float = 0.0
    clarity_gain:            float = 0.0
    dominant_repair_score:   float = 0.0
    repair_operation:        str  = ""
    root_or_secondary:       str  = "root"
    secondary_families:      List[str] = field(default_factory=list)
    dependent_symptom_rows:  List[str] = field(default_factory=list)
    problem_statement:       str  = ""
    explanation:             str  = ""
    pressure:                float = 0.0
    root_status:             str  = "root"
    suppression_reason:      Optional[str] = None
    cluster_key:             str  = ""


@dataclass
class Essay:
    essay_id:         str
    student_id:       Optional[str]
    variant:          str
    metadata:         Dict[str, Any]
    bands:            Dict[str, Optional[float]]
    semantic:         Dict[str, Any]
    task_profile:     Dict[str, Any]
    layer0:           Dict[str, Any]
    rows:             List[Row]
    raw:              Dict[str, Any]
    strengths_profile: List[Dict[str, Any]] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Priority Engine V4.4 (self-contained — all V4.0→V4.4 logic merged)
# ─────────────────────────────────────────────────────────────────────────────

class PriorityEngine:
    """
    Single class containing the full V4.0→V4.4 logic.
    No inheritance from other PE version files.
    """

    def __init__(self, registry: Optional[Registry] = None, fp_patterns: Optional[List[str]] = None):
        self.registry            = registry or Registry.load(None)
        self.ontology            = self.registry.get("core_skill_ontology", FALLBACK_ONTOLOGY) or FALLBACK_ONTOLOGY
        self.family_map          = self.registry.get("family_to_skill_map", FALLBACK_FAMILY_MAP) or FALLBACK_FAMILY_MAP
        self.rules               = self.registry.get("priority_engine_rules", {}) or {}
        self.inference_rules     = self.registry.get("priority_skill_inference_rules", {}) or {}
        self.strength_rules      = self.registry.get("strength_rules", {}) or {}
        self.diagnostic_signatures = self.registry.get("diagnostic_signatures", {}) or {}
        self.remediation         = self.registry.get("remediation_map", {}) or {}
        self.dependency_graph    = self.registry.get("skill_dependency_graph", {}) or {}
        self.band_matrix         = self.registry.get("band_skill_matrix", {}) or {}
        self.observability       = self.registry.get("skill_observability_catalog", {}) or {}
        self.display_rules       = (self.rules.get("display_rules") or {}) if isinstance(self.rules, dict) else {}
        self._v4_fine_targets    = self._load_v4_fine_targets()
        self._v4_repair_labels   = self._load_repair_labels()
        self._v4_band_thresholds = self.registry.get("v4_band_strength_thresholds", {}) or {}
        self._v4_task_notes      = self.registry.get("v4_task_type_notes", {}) or {}
        self._v4_sat             = self._load_saturation_params()
        # V4.3: FP suppression patterns
        if fp_patterns is not None:
            self._fp_patterns = fp_patterns
        else:
            self._fp_patterns = _load_fp_patterns(getattr(self.registry, "knowledge_path", "") or "")

    @classmethod
    def from_knowledge_path(cls, knowledge_path: Optional[str] = None, manifest_path: Optional[str] = None) -> "PriorityEngine":
        return cls(Registry.load(knowledge_path, manifest_path))

    # ── V4 registry loaders ──────────────────────────────────────────────────

    def _load_v4_fine_targets(self) -> List[Dict[str, Any]]:
        data = self.registry.get("v4_fine_targets")
        if isinstance(data, dict):
            targets = data.get("targets") or data.get("fine_grained_targets") or []
            if targets:
                return targets
        if isinstance(data, list):
            return data
        return FINE_TARGETS_V3

    def _load_repair_labels(self) -> Dict[str, Dict[str, Any]]:
        data = self.registry.get("v4_repair_pattern_labels", {}) or {}
        out: Dict[str, Dict[str, Any]] = {}
        patterns = data.get("patterns", []) if isinstance(data, dict) else []
        for p in patterns:
            op = p.get("repair_operation", "")
            if op:
                out[op] = p
        return out

    def _load_saturation_params(self) -> Dict[str, Any]:
        data = self.registry.get("v4_saturation_params", {}) or {}
        defaults: Dict[str, Any] = {
            "global_threshold": 4, "global_decay": 0.60, "per_family": {},
            "repair_concentration_min_sentences": 3, "repair_concentration_bonus": 0.15,
            "repair_concentration_max": 0.30, "repair_concentration_enabled": True,
            "position_intro_mult": 1.15, "position_conclusion_mult": 1.15, "position_enabled": True,
            "discourse_dampening_factor": 0.65, "semantic_trust_threshold": 0.78,
            "affected_discourse_ratio_threshold": 0.35,
        }
        if not isinstance(data, dict):
            return defaults
        sat = data.get("saturation", {}) or {}
        glb = sat.get("global", {}) or {}
        per = sat.get("per_family_overrides", {}) or {}
        rpc = data.get("repair_pattern_concentration", {}) or {}
        epw = data.get("essay_position_weights", {}) or {}
        dd  = data.get("discourse_dampening", {}) or {}
        defaults["global_threshold"]                  = _safe_int(glb.get("saturation_threshold"), 4)
        defaults["global_decay"]                      = _safe_float(glb.get("decay_factor"), 0.60)
        defaults["per_family"]                        = per
        defaults["repair_concentration_min_sentences"]= _safe_int(rpc.get("min_distinct_sentences"), 3)
        defaults["repair_concentration_bonus"]        = _safe_float(rpc.get("concentration_bonus"), 0.15)
        defaults["repair_concentration_max"]          = _safe_float(rpc.get("max_bonus_per_row"), 0.30)
        defaults["repair_concentration_enabled"]      = bool(rpc.get("enabled", True))
        defaults["position_intro_mult"]               = _safe_float(epw.get("introduction_multiplier"), 1.15)
        defaults["position_conclusion_mult"]          = _safe_float(epw.get("conclusion_multiplier"), 1.15)
        defaults["position_enabled"]                  = bool(epw.get("enabled", True))
        defaults["discourse_dampening_factor"]        = _safe_float(dd.get("discourse_dampening_factor"), 0.65)
        defaults["semantic_trust_threshold"]          = _safe_float(dd.get("semantic_trust_threshold"), 0.78)
        defaults["affected_discourse_ratio_threshold"]= _safe_float(dd.get("affected_discourse_ratio_threshold"), 0.35)
        return defaults

    # ── Normalization ────────────────────────────────────────────────────────

    def normalize_payload(self, payload: Dict[str, Any]) -> List[Essay]:
        results = payload.get("results") if isinstance(payload.get("results"), list) else [payload]
        return [self.normalize_result(r, payload, i) for i, r in enumerate(results) if isinstance(r, dict)]

    def normalize_result(self, result: Dict[str, Any], root: Dict[str, Any], idx: int) -> Essay:
        """V4.1: backfills task_type into task_profile from metadata when extracted from gate_applications."""
        essay_id   = str(_get_path(result, "identity.essay_id") or result.get("essay_id") or idx + 1)
        student_id = _get_path(result, "identity.student_id") or result.get("student_id")
        variant    = (
            "va23_scored_single_result"            if result.get("score_report")
            else "recent_detector_v14_detector_only" if (root.get("schema_version") == "DETECTOR_OUTPUT_V1.1" or result.get("scorer_payload"))
            else "generic_detector_scorer"
        )
        metadata         = self.extract_metadata(result)
        bands            = self.extract_bands(result)
        semantic         = self.extract_semantic(result)
        task_profile     = result.get("task_profile") or _get_path(result, "layer0_idea_map.task_schema_profile", {}) or {}
        layer0           = result.get("layer0_idea_map") or result.get("essay_map") or {}
        strengths_profile= self.extract_strengths_profile(result)
        raw_rows         = self.extract_rows(result)
        para_count       = _safe_int(metadata.get("paragraph_count"), 0)
        rows             = [self.normalize_row(x, essay_id, para_count) for x in raw_rows if isinstance(x, dict)]
        essay            = Essay(essay_id, student_id, variant, metadata, bands, semantic, task_profile, layer0, rows, result, strengths_profile)
        # V4.1: backfill task_type from metadata to task_profile
        if not essay.task_profile.get("task_type") and essay.metadata.get("task_type"):
            essay.task_profile["task_type"]            = essay.metadata["task_type"]
            essay.task_profile["task_type_confidence"] = 0.75
            essay.task_profile["task_type_source"]     = "scorer_gate_application"
        return essay

    def extract_bands(self, result: Dict[str, Any]) -> Dict[str, Optional[float]]:
        """V4.1: reads score_profile.rubrics.{rubric}.band first, then V4.0 legacy paths."""
        out = {"task_response": None, "coherence_cohesion": None, "lexical_resource": None, "grammar": None, "overall": None}
        # V4.1: scorer v2.1.3 schema
        score_profile = result.get("score_profile") or {}
        rubrics_block = score_profile.get("rubrics") or {}
        if isinstance(rubrics_block, dict):
            for rubric_name, rubric_data in rubrics_block.items():
                if not isinstance(rubric_data, dict):
                    continue
                r = _norm_rubric(rubric_name)
                if r and r in RUBRIC_LONG and r != "META":
                    band_val = rubric_data.get("band_rounded") or rubric_data.get("band")
                    if band_val is not None:
                        out[RUBRIC_LONG[r]] = _safe_float(band_val, None)
        overall_sp = score_profile.get("overall_band_estimate")
        if overall_sp is not None:
            out["overall"] = _safe_float(overall_sp, None)
        # V4.0 legacy fallback paths
        raw = (
            _get_path(result, "score_report.official_criteria_bands", {})
            or _get_path(result, "headline.by_rubric", {})
            or {}
        )
        if isinstance(raw, dict):
            for k, v in raw.items():
                r = _norm_rubric(k)
                if r in RUBRIC_LONG and r != "META" and out.get(RUBRIC_LONG[r]) is None:
                    out[RUBRIC_LONG[r]] = _safe_float(v, None)
        overall_legacy = (
            _get_path(result, "score_report.overall_band_estimate")
            or _get_path(result, "headline.overall")
        )
        if overall_legacy is not None and out["overall"] is None:
            out["overall"] = _safe_float(overall_legacy, None)
        return out

    def extract_rows(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        V4.1+V4.3 merged:
          1. V4.0 path search → V4.1 synthesised rows from rubric_impact_map.
          2. V4.3 FP suppression.
          3. V4.3 family → rubric remap.
        """
        rows: Optional[List[Dict[str, Any]]] = None

        # V4.0 standard paths
        for path in ["student_rows", "scorer_payload.chargeable_detector_rows",
                     "candidate_lists.survived_candidates", "candidate_lists.raw_candidates"]:
            candidate = _get_path(result, path) if "." in path else result.get(path)
            if isinstance(candidate, list) and candidate:
                rows = candidate
                break

        if rows is None:
            for key in ["stage_layer3_local_language", "stage_layer2_sentence_discourse", "stage_layer1_wide_discourse"]:
                if isinstance(result.get(key), list) and result[key]:
                    rows = result[key]
                    break

        if rows is None:
            # V4.1: synthesise from rubric_impact_map (scorer v2.1.3 — no full row text)
            rim = result.get("rubric_impact_map") or []
            if rim:
                per_rubric = _get_path(result, "score_explanation_payload.per_rubric_summary") or {}
                rubric_families: Dict[str, List[str]] = {}
                for rub_name, rub_data in (per_rubric.items() if isinstance(per_rubric, dict) else []):
                    rubric_families[rub_name] = rub_data.get("dominant_families") or []
                rubric_row_count: Dict[str, int] = {}
                synth_rows: List[Dict[str, Any]] = []
                for entry in rim:
                    if not isinstance(entry, dict):
                        continue
                    row_id     = str(entry.get("row_id", ""))
                    rubric_raw = str(entry.get("rubric", "grammar"))
                    impact     = _safe_float(entry.get("impact_weight"), 0.9) or 0.9
                    fams       = rubric_families.get(rubric_raw, [])
                    idx        = rubric_row_count.get(rubric_raw, 0)
                    family     = fams[idx % len(fams)] if fams else "UNKNOWN"
                    rubric_row_count[rubric_raw] = idx + 1
                    severity   = "high" if impact >= 0.97 else "medium" if impact >= 0.90 else "low"
                    synth_rows.append({
                        "row_id": row_id, "rubric": rubric_raw, "family": family,
                        "score_charge_weight": impact, "chargeable": True,
                        "chargeable_for_scoring": True, "confidence": 0.70,
                        "severity": severity, "source": "scorer_rubric_impact_map",
                        "root_or_secondary": "root", "quote": "", "local_quote": "",
                        "problem_statement": f"Scorer-impact row: rubric={rubric_raw}, family={family}, impact={impact}",
                        "explanation": "Synthesised from scorer rubric_impact_map. Full detector row not available.",
                    })
                if synth_rows:
                    result["_v4_1_synthesised_rows"] = True
                    rows = synth_rows

        if rows is None:
            rows = []

        # V4.3: FP suppression + family → rubric remap
        remapped: List[Dict[str, Any]] = []
        fp_suppressed = 0
        for row in rows:
            row = dict(row)
            if _row_is_fp(row, self._fp_patterns):
                row["fp_suppressed"]          = True
                row["chargeable"]             = False
                row["chargeable_for_scoring"] = False
                fp_suppressed += 1
                remapped.append(row)
                continue
            family = str(row.get("family") or "")
            if family in _FAMILY_RUBRIC_REMAP:
                row["rubric"] = _FAMILY_RUBRIC_REMAP[family]
            remapped.append(row)

        result["_v4_3_fp_suppressed"] = fp_suppressed
        return remapped

    def extract_metadata(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """V4.1: adds task_type extraction from gate_applications + score_ready/confidence from score_profile."""
        gm           = result.get("generated_metadata") or result.get("meta") or {}
        score_global = _get_path(result, "score_report.metric_profile.global", {}) or {}
        score_profile= result.get("score_profile") or {}

        score_ready = (
            score_profile.get("score_ready")
            if score_profile.get("score_ready") is not None
            else _get_path(result, "task_profile.score_ready", True)
        )
        task_type = (
            _get_path(result, "task_profile.task_type")
            or _get_path(result, "essay_map.prompt.task_type")
        )
        # V4.1: extract task_type from gate_applications explanation strings
        if not task_type:
            gate_apps = _get_path(result, "qa.scoring_record.gate_applications") or []
            for gate in (gate_apps if isinstance(gate_apps, list) else []):
                expl = gate.get("student_explanation") or ""
                m    = _TASK_TYPE_RE.search(expl)
                if m:
                    task_type = m.group(1).lower()
                    break
        # V4.1: pull word_count from scorer gate actual_value
        word_count = gm.get("word_count") or score_global.get("n_words")
        if word_count is None:
            for gate in (_get_path(result, "qa.scoring_record.gate_applications") or []):
                if gate.get("trigger_metric") == "word_count":
                    word_count = gate.get("actual_value")
                    break
        return {
            "word_count":       word_count,
            "paragraph_count":  gm.get("paragraph_count") or gm.get("n_paragraphs") or score_global.get("n_paragraphs"),
            "sentence_count":   gm.get("sentence_count")  or gm.get("n_sentences")  or score_global.get("n_sentences"),
            "task_type":        task_type,
            "prompt_present":   bool(_get_path(result, "intake_record.prompt_text") or _get_path(result, "meta.prompt_present") or _get_path(result, "task_profile.prompt_id")),
            "score_ready":      bool(score_ready),
            "scorer_confidence":_safe_float(score_profile.get("confidence"), None),
            "score_status":     score_profile.get("score_status"),
        }

    def extract_semantic(self, result: Dict[str, Any]) -> Dict[str, Any]:
        sem      = _get_path(result, "layer0_5_semantic_recoverability.semantic_summary", {}) or _get_path(result, "essay_map.semantic_summary", {}) or {}
        shared   = _get_path(result, "detector_metric_profile.shared", {}) or {}
        global_m = _get_path(result, "score_report.metric_profile.global", {}) or {}
        def f(*xs):
            for x in xs:
                if x is not None:
                    return x
            return None
        return {
            "mean_recoverability":     f(sem.get("mean_recoverability"),     shared.get("mean_recoverability"),     global_m.get("semantic_recoverability")),
            "mean_semantic_trust":     f(sem.get("mean_semantic_trust"),     sem.get("mean_evaluability"), shared.get("mean_semantic_trust"), global_m.get("proposition_stability")),
            "mean_local_corruption":   f(sem.get("mean_local_corruption"),   shared.get("mean_local_corruption"),   global_m.get("local_damage_index")),
            "affected_discourse_ratio":f(sem.get("affected_discourse_ratio"),shared.get("affected_discourse_ratio")),
            "blocked_sentence_count":  f(sem.get("blocked_sentence_count"),  sem.get("blocked_discourse_sentences"),shared.get("blocked_sentence_count")),
            "limited_sentence_count":  f(sem.get("limited_sentence_count"),  sem.get("limited_discourse_sentences"),shared.get("limited_sentence_count")),
            "sentence_assessments":    _get_path(result, "layer0_5_semantic_recoverability.sentence_assessments", {}) or _get_path(result, "essay_map.discourse_eligibility", {}) or {},
        }

    def extract_strengths_profile(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        sp = (
            _get_path(result, "evaluator_payload.strengths_profile")
            or _get_path(result, "scorer_payload.lr_positive_signals")
            or _get_path(result, "strengths_profile")
            or []
        )
        if isinstance(sp, list):   return sp
        if isinstance(sp, dict):   return [sp]
        return []

    def normalize_row(self, row: Dict[str, Any], essay_id: str, para_count: int) -> Row:
        fam   = _norm_family(row.get("family") or row.get("family_candidate"),
                             row.get("issue_code") or ((row.get("candidate_issue_codes") or [None])[0]
                                                        if isinstance(row.get("candidate_issue_codes"), list)
                                                        else row.get("issue_code")))
        fmap  = self.family_map.get(fam, {}) if isinstance(self.family_map, dict) else {}
        rub   = _norm_rubric(row.get("rubric") or row.get("rubric_candidate") or row.get("category") or fmap.get("rubric")) or "UNKNOWN"
        skill = fmap.get("core_skill") or fmap.get("skill") or self.skill_from_rubric(rub)
        raw_evidence = row.get("raw_evidence") if isinstance(row.get("raw_evidence"), dict) else row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        source       = row.get("source_engine") or row.get("engine") or ",".join(row.get("source_engines", []) if isinstance(row.get("source_engines"), list) else [])
        support_only = bool(raw_evidence.get("support_only")) or str(source).endswith("_support")
        chargeable   = bool(row.get("chargeable_for_scoring", row.get("chargeable", not support_only)))
        review_only  = bool(row.get("review_only", False)) or not chargeable
        quote        = str(row.get("quote") or row.get("surface_quote") or "")
        local_quote  = str(row.get("local_quote") or row.get("expanded_quote") or "")
        rid          = str(row.get("row_id") or row.get("candidate_id") or row.get("detection_id")
                          or f"row_{abs(hash(json.dumps(row, sort_keys=True, default=str))) % 10**12}")
        repair_op    = str(row.get("repair_operation") or row.get("dominant_repair_operation")
                          or _get_path(row, "raw_evidence.dominant_repair_operation") or "")
        para_idx     = row.get("paragraph_index")
        para_pos     = "body"
        if para_idx is not None and para_count >= 2:
            if _safe_int(para_idx) == 0:
                para_pos = "intro"
            elif _safe_int(para_idx) >= para_count - 1:
                para_pos = "conclusion"
        return Row(
            row_id=rid, essay_id=essay_id, family=fam, skill=skill, rubric=rub,
            quote=quote, local_quote=local_quote,
            sentence_index=(row.get("sentence_index") if row.get("sentence_index") is not None else row.get("global_sentence_index")),
            paragraph_index=row.get("paragraph_index"), paragraph_position=para_pos,
            severity=str(row.get("severity") or "medium").lower(),
            confidence=_clamp(row.get("confidence"), 0, 1, 0.75),
            layer=str(row.get("layer") or ""), source=str(source),
            chargeable=chargeable, review_only=review_only, support_only=support_only,
            score_charge_weight=_safe_float(row.get("score_charge_weight"), None),
            recoverability_gain=_safe_float(row.get("recoverability_gain"), 0) or 0,
            evaluability_gain=_safe_float(row.get("evaluability_gain"), 0) or 0,
            clarity_gain=_safe_float(row.get("clarity_gain"), 0) or 0,
            dominant_repair_score=_safe_float(row.get("dominant_repair_score"), 0) or 0,
            repair_operation=repair_op,
            root_or_secondary=str(row.get("root_or_secondary") or "root"),
            secondary_families=[_norm_family(x) for x in (row.get("secondary_families") or [])],
            dependent_symptom_rows=list(row.get("dependent_symptom_rows") or []),
            problem_statement=str(row.get("problem_statement") or row.get("rationale") or ""),
            explanation=str(row.get("explanation") or row.get("raw_explanation") or ""),
            cluster_key=self.cluster_key(quote, local_quote, row.get("sentence_index"), fam),
        )

    def skill_from_rubric(self, rub: str) -> str:
        return {"TR": "IDEA_DEVELOPMENT", "CC": "COHERENCE_CONTROL", "LR": "LEXICAL_CONTROL", "GRA": "GRAMMAR_CONTROL"}.get(rub, "UNKNOWN_SKILL")

    def cluster_key(self, quote: str, local_quote: str, sentence_index: Any, family: str) -> str:
        text = (quote or local_quote or "").lower().strip()
        text = re.sub(r"\s+", " ", text)[:90]
        return f"s{sentence_index}:{text}" if text else f"s{sentence_index}:{family}"

    # ── Essay analysis ───────────────────────────────────────────────────────

    def analyze_essay(self, essay: Essay) -> Dict[str, Any]:
        self.compute_pressures(essay)
        self.compress_roots(essay)
        family_profiles  = self.family_profiles(essay)
        skill_profiles   = self.skill_profiles(essay, family_profiles)
        self.apply_dependency_propagation(skill_profiles)
        rubric_profiles  = self.rubric_profiles(essay, skill_profiles)
        primary, secondary = self.select_limiters(essay, skill_profiles)
        primary          = self.confidence_envelope(primary, skill_profiles)
        secondary        = [self.confidence_envelope(s, skill_profiles) for s in secondary]
        fine_targets     = self.training_targets(essay, skill_profiles, primary, secondary)
        strengths        = self.strengths(essay, skill_profiles, rubric_profiles)
        pattern_intel    = self.pattern_intelligence(essay, skill_profiles, family_profiles, fine_targets)
        band_unlock      = self.band_unlock(essay, primary, secondary, fine_targets)
        display          = self.display(essay, strengths, fine_targets, skill_profiles)
        alt_hyp          = self.alternative_hypothesis(essay, primary, skill_profiles)
        li_payload       = self.learning_intelligence_ingestion_payload(essay, primary, secondary, fine_targets, strengths)
        qa               = self.qa_flags(essay, primary, fine_targets, skill_profiles)
        result = {
            "schema_version":          OUTPUT_SCHEMA_VERSION,
            "essay_id":                essay.essay_id,
            "input_variant":           essay.variant,
            "metadata":                essay.metadata,
            "bands_if_available":      essay.bands,
            "semantic_summary":        {k: v for k, v in essay.semantic.items() if k != "sentence_assessments"},
            "primary_limiter":         primary,
            "secondary_limiters":      secondary,
            "skill_profiles":          skill_profiles,
            "family_profiles":         family_profiles,
            "rubric_profiles":         rubric_profiles,
            "fine_grained_training_targets": fine_targets,
            "strengths":               strengths,
            "pattern_intelligence":    pattern_intel,
            "band_unlock":             band_unlock,
            "root_vs_symptom":         self.root_summary(essay),
            "display_decisions":       display,
            "priority_contract_audit": {
                "target_audit": essay.raw.get("_priority_v4_target_audit", []),
                "presentation_safety_policy": "unsafe detector rows retained for teacher/debug only",
            },
            "feedback_generator_payload": self.feedback_generator_payload(essay, primary, secondary, fine_targets, strengths),
            "alternative_hypothesis":     alt_hyp,
            "learning_intelligence_ingestion_payload": li_payload,
            "qa_flags":    qa,
            "debug_counts": {
                "normalized_rows":              len(essay.rows),
                "root_rows":                    sum(r.root_status == "root" for r in essay.rows),
                "symptom_or_collapsed_rows":    sum(r.root_status != "root" for r in essay.rows),
                "student_safe_root_rows":       sum(r.root_status == "root" and _v3_row_safety(r)["display_safety_status"] == "student_safe" for r in essay.rows),
            },
        }
        self.self_validate(result, essay)
        return result

    # ── Pressure computation (V4) ────────────────────────────────────────────

    def compute_pressures(self, essay: Essay) -> None:
        sat      = self._v4_sat
        affected = _safe_float(essay.semantic.get("affected_discourse_ratio"), 0) or 0
        mean_eval= _safe_float(essay.semantic.get("mean_semantic_trust"), None)

        for r in essay.rows:
            base = r.score_charge_weight if r.score_charge_weight is not None else SEVERITY_WEIGHTS.get(r.severity, 1.0)
            conf = _clamp(r.confidence, 0.45, 1.0, 0.75)
            gain = 0.50 * r.recoverability_gain + 0.35 * r.evaluability_gain + 0.15 * r.clarity_gain
            if r.dominant_repair_score:
                gain = max(gain, 0.35 * r.dominant_repair_score)
            pressure = base * conf + gain
            if r.layer == "layer3_local_language" and affected >= 0.35 and r.skill in LOCAL_SKILLS:
                pressure += 0.25
            if r.review_only:  pressure *= 0.25
            if r.support_only: pressure *= 0.15
            ddf       = sat.get("discourse_dampening_factor", 0.65)
            adr_thresh= sat.get("affected_discourse_ratio_threshold", 0.35)
            st_thresh = sat.get("semantic_trust_threshold", 0.78)
            if r.skill in DISCOURSE_SKILLS and (affected >= adr_thresh or (mean_eval is not None and mean_eval < st_thresh)):
                if "layer1_wide_discourse" not in r.layer and "discourse_tr_pass" not in r.source and r.confidence < 0.82:
                    pressure *= ddf
            r.pressure    = round(max(0.0, pressure), 4)
            r.root_status = "root" if r.root_or_secondary != "secondary" else "secondary"
            r.suppression_reason = None

        if sat.get("position_enabled", True):
            intro_m = sat.get("position_intro_mult", 1.15) or 1.15
            concl_m = sat.get("position_conclusion_mult", 1.15) or 1.15
            for r in essay.rows:
                if r.rubric == "TR":
                    if   r.paragraph_position == "intro":      r.pressure = round(r.pressure * intro_m, 4)
                    elif r.paragraph_position == "conclusion":  r.pressure = round(r.pressure * concl_m, 4)

        if sat.get("repair_concentration_enabled", True):
            bonus_val  = sat.get("repair_concentration_bonus", 0.15) or 0.15
            bonus_max  = sat.get("repair_concentration_max", 0.30) or 0.30
            min_sents  = sat.get("repair_concentration_min_sentences", 3) or 3
            op_sents: Dict[str, set] = defaultdict(set)
            op_rows:  Dict[str, List[Row]] = defaultdict(list)
            for r in essay.rows:
                if r.repair_operation:
                    op_sents[r.repair_operation].add(r.sentence_index)
                    op_rows[r.repair_operation].append(r)
            for op, sents in op_sents.items():
                if len(sents) >= min_sents:
                    for r in op_rows[op]:
                        r.pressure = round(min(r.pressure + bonus_val, r.pressure + bonus_max), 4)

        family_root_rows: Dict[str, List[Row]] = defaultdict(list)
        for r in essay.rows:
            family_root_rows[r.family].append(r)
        for fam, rows in family_root_rows.items():
            fam_cfg   = sat.get("per_family", {}).get(fam, {})
            threshold = _safe_int(fam_cfg.get("saturation_threshold"), sat.get("global_threshold", 4))
            decay     = _safe_float(fam_cfg.get("decay_factor"), sat.get("global_decay", 0.60))
            rows_sorted = sorted(rows, key=lambda r: r.pressure, reverse=True)
            for i, r in enumerate(rows_sorted):
                if i >= threshold:
                    r.pressure = round(r.pressure * decay, 4)

    # ── Compression (8 passes) ───────────────────────────────────────────────

    def discourse_eligibility(self, essay: Essay, sent: Any) -> str:
        data = essay.semantic.get("sentence_assessments") or {}
        v = data.get(str(sent)) if isinstance(data, dict) else None
        if isinstance(v, dict):
            return str(v.get("discourse_evaluation_allowed") or v.get("eligibility") or "full")
        if isinstance(v, str):
            return {"none": "blocked", "limited": "limited", "full": "full"}.get(v, v)
        return "unknown"

    def compress_roots(self, essay: Essay) -> None:
        # Pass 1: dependent rows
        dep_ids = set()
        for r in essay.rows:
            dep_ids.update(str(x) for x in r.dependent_symptom_rows)
        for r in essay.rows:
            if r.row_id in dep_ids:
                r.root_status = "symptom"; r.suppression_reason = "dependent_symptom_rows"
                r.pressure    = round(r.pressure * 0.25, 4)

        # Pass 2: semantic gate
        for r in essay.rows:
            elig = self.discourse_eligibility(essay, r.sentence_index)
            if r.skill in DISCOURSE_SKILLS and elig in {"blocked", "none", "false"}:
                if "layer1_wide_discourse" not in r.layer and "discourse_tr_pass" not in r.source:
                    r.root_status = "symptom"
                    r.suppression_reason = r.suppression_reason or "semantic_gate_blocked_discourse"
                    r.pressure    = round(r.pressure * 0.35, 4)

        # Pass 3: local-before-discourse same sentence
        local_by_sent: Dict[str, float] = defaultdict(float)
        for r in essay.rows:
            if r.skill in LOCAL_SKILLS and r.root_status == "root":
                local_by_sent[str(r.sentence_index)] += r.pressure
        for r in essay.rows:
            if r.skill in DISCOURSE_SKILLS and local_by_sent.get(str(r.sentence_index), 0) >= max(0.8, r.pressure * 0.85):
                if "layer1_wide_discourse" not in r.layer and r.confidence < 0.82:
                    r.root_status = "symptom"
                    r.suppression_reason = r.suppression_reason or "local_language_root_same_sentence"
                    r.pressure    = round(r.pressure * 0.50, 4)

        # Pass 4: cluster dedup (same quote/sentence)
        clusters: Dict[str, List[Row]] = defaultdict(list)
        for r in essay.rows:
            clusters[r.cluster_key].append(r)
        for _, rows in clusters.items():
            roots = [r for r in rows if r.root_status == "root"]
            if len(roots) <= 1:
                continue
            roots.sort(key=lambda r: (r.pressure + (0.15 if r.skill in LOCAL_SKILLS else 0), r.confidence), reverse=True)
            for r in roots[1:]:
                if r.skill == roots[0].skill or (r.skill in DISCOURSE_SKILLS and roots[0].skill in LOCAL_SKILLS):
                    r.root_status = "collapsed_duplicate"
                    r.suppression_reason = "same_quote_sentence_cluster"
                    r.pressure    = round(r.pressure * 0.30, 4)

        # Pass 5: detector-marked secondary
        for r in essay.rows:
            if r.root_or_secondary == "secondary" and r.root_status == "root":
                r.root_status = "secondary"
                r.suppression_reason = "detector_marked_secondary"
                r.pressure    = round(r.pressure * 0.45, 4)

        # Pass 6 (V4): paragraph-cluster collapse
        para_fam: Dict[str, List[Row]] = defaultdict(list)
        for r in essay.rows:
            if r.root_status == "root" and r.paragraph_index is not None:
                para_fam[f"{r.paragraph_index}:{r.family}"].append(r)
        for _, rows in para_fam.items():
            roots = [r for r in rows if r.root_status == "root"]
            if len(roots) <= 2:
                continue
            roots.sort(key=lambda r: r.pressure, reverse=True)
            for r in roots[2:]:
                r.root_status = "paragraph_cluster_collapsed"
                r.suppression_reason = "paragraph_cluster_same_family"
                r.pressure    = round(r.pressure * 0.40, 4)

        # Pass 7 (V4): repair-op dedup same sentence
        op_sent: Dict[str, List[Row]] = defaultdict(list)
        for r in essay.rows:
            if r.root_status == "root" and r.repair_operation and r.sentence_index is not None:
                op_sent[f"{r.sentence_index}:{r.repair_operation}"].append(r)
        for _, rows in op_sent.items():
            roots = [r for r in rows if r.root_status == "root"]
            if len(roots) <= 1:
                continue
            roots.sort(key=lambda r: r.pressure, reverse=True)
            for r in roots[1:]:
                r.root_status = "repair_op_duplicate"
                r.suppression_reason = "repair_operation_same_sentence_dedup"
                r.pressure    = round(r.pressure * 0.35, 4)

        # Pass 8 (V4): task-type context gate
        task_type = essay.task_profile.get("task_type") or essay.metadata.get("task_type")
        task_conf = _safe_float(essay.task_profile.get("task_type_confidence"), 0.0) or 0.0
        if task_type and task_type != "unknown" and task_conf >= 0.80:
            task_notes = (self._v4_task_notes.get("task_types") or {}).get(str(task_type), {})
            required   = set(task_notes.get("required_components", []))
            if required:
                for r in essay.rows:
                    if r.rubric == "TR" and r.root_status == "root":
                        if r.repair_operation in {"replace_connector", "correct_spelling", "correct_noun_number"}:
                            r.pressure = round(r.pressure * 0.50, 4)
                            r.suppression_reason = r.suppression_reason or "task_type_context_gate_non_tr_repair"

    # ── Profile methods ──────────────────────────────────────────────────────

    def family_profiles(self, essay: Essay) -> List[Dict[str, Any]]:
        grp: Dict[str, List[Row]] = defaultdict(list)
        for r in essay.rows: grp[r.family].append(r)
        out = []
        for fam, rows in grp.items():
            roots = [r for r in rows if r.root_status == "root"]
            p = sum(r.pressure for r in roots)
            out.append({"family": fam, "skill": (roots[0].skill if roots else rows[0].skill), "rubric": (roots[0].rubric if roots else rows[0].rubric), "pressure": round(p, 3), "root_count": len(roots), "raw_count": len(rows), "examples": self.examples(roots, 2)})
        return sorted(out, key=lambda x: x["pressure"], reverse=True)

    def skill_profiles(self, essay: Essay, family_profiles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        grp: Dict[str, List[Row]] = defaultdict(list)
        for r in essay.rows: grp[r.skill].append(r)
        out = []
        for skill, rows in grp.items():
            roots      = [r for r in rows if r.root_status == "root"]
            sentences  = {r.sentence_index for r in roots if r.sentence_index is not None}
            pressure   = sum(r.pressure for r in roots) + (min(0.4, 0.08 * len(sentences)) if len(sentences) >= 2 else 0)
            fams       = Counter(r.family for r in roots)
            out.append({
                "skill": skill, "student_label": self.skill_label(skill), "rubric": self.skill_rubric(skill, roots or rows),
                "pressure": round(pressure, 3), "dependency_adjusted_pressure": round(pressure, 3),
                "root_row_count": len(roots), "raw_row_count": len(rows),
                "symptom_row_count": len(rows) - len(roots), "distinct_sentence_count": len(sentences),
                "dominant_families": [{"family": f, "count": c} for f, c in fams.most_common(5)],
                "examples": self.examples(roots, 3), "priority_level": self.priority_level(pressure),
                "observability": self.skill_observability(skill)
            })
        out.extend(self.meta_profiles(essay, {p["skill"] for p in out}))
        return sorted(out, key=lambda x: x.get("dependency_adjusted_pressure", x["pressure"]), reverse=True)

    def meta_profiles(self, essay: Essay, existing: set) -> List[Dict[str, Any]]:
        out = []
        rec     = _safe_float(essay.semantic.get("mean_recoverability"), None)
        trust   = _safe_float(essay.semantic.get("mean_semantic_trust"), None)
        affected= _safe_float(essay.semantic.get("affected_discourse_ratio"), None)
        blocked = _safe_float(essay.semantic.get("blocked_sentence_count"), 0) or 0
        if rec is not None and rec > 1: rec = rec / 3.0
        if trust is not None and trust > 1: trust = trust / 3.0
        if rec is not None and rec < 0.78 and "MEANING_RECOVERABILITY" not in existing:
            out.append(self.meta_profile("MEANING_RECOVERABILITY", (0.78 - rec) * 5, {"mean_recoverability": round(rec, 3)}))
        if (affected is not None and affected >= 0.25 or blocked > 0) and "DISCOURSE_EVALUABILITY" not in existing:
            out.append(self.meta_profile("DISCOURSE_EVALUABILITY", min(3.0, (affected or 0) * 3 + blocked * 0.12), {"affected_discourse_ratio": affected, "blocked_sentence_count": blocked}))
        if trust is not None and trust < 0.78 and "SEMANTIC_EVALUABILITY" not in existing:
            out.append(self.meta_profile("SEMANTIC_EVALUABILITY", (0.78 - trust) * 5, {"mean_semantic_trust": round(trust, 3)}))
        return out

    def meta_profile(self, skill: str, p: float, evidence: Dict[str, Any]) -> Dict[str, Any]:
        return {"skill": skill, "student_label": self.skill_label(skill), "rubric": "META", "pressure": round(max(0, p), 3), "dependency_adjusted_pressure": round(max(0, p), 3), "root_row_count": 0, "raw_row_count": 0, "symptom_row_count": 0, "distinct_sentence_count": None, "dominant_families": [], "examples": [], "priority_level": self.priority_level(p), "observability": self.skill_observability(skill), "metric_evidence": evidence}

    def apply_dependency_propagation(self, profiles: List[Dict[str, Any]]) -> None:
        by = {p["skill"]: p for p in profiles}
        for edge in (self.dependency_graph.get("edges", []) if isinstance(self.dependency_graph, dict) else []):
            src, tgt = edge.get("from"), edge.get("to")
            if not src or not tgt or src not in by or tgt not in by: continue
            typ    = edge.get("type", "support")
            weight = _safe_float(edge.get("weight"), None)
            if weight is None:
                weight = {"gate": 0.20, "risk": 0.18, "precision_support": 0.12, "support": 0.10, "prerequisite": 0.15}.get(typ, 0.08)
            transfer = by[src]["pressure"] * min(0.35, max(0.0, weight))
            by[tgt]["dependency_adjusted_pressure"] = round(by[tgt].get("dependency_adjusted_pressure", by[tgt]["pressure"]) + transfer, 3)
            by[tgt].setdefault("dependency_sources", []).append({"from": src, "type": typ, "added_pressure": round(transfer, 3)})
        profiles.sort(key=lambda x: x.get("dependency_adjusted_pressure", x["pressure"]), reverse=True)

    def rubric_profiles(self, essay: Essay, skills: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        grp: Dict[str, List] = defaultdict(list)
        for p in skills:
            if p["rubric"] != "META": grp[p["rubric"]].append(p)
        out = []
        for rub, arr in grp.items():
            pressure = sum(x.get("dependency_adjusted_pressure", x["pressure"]) for x in arr)
            out.append({"rubric": rub, "rubric_name": RUBRIC_LONG.get(rub, rub), "pressure": round(pressure, 3), "band_if_available": essay.bands.get(RUBRIC_LONG.get(rub, "")), "top_skills": [x["skill"] for x in sorted(arr, key=lambda y: y.get("dependency_adjusted_pressure", y["pressure"]), reverse=True)[:3]], "priority_level": self.priority_level(pressure)})
        return sorted(out, key=lambda x: x["pressure"], reverse=True)

    def select_limiters(self, essay: Essay, skills: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        eligible = [p for p in skills if p.get("dependency_adjusted_pressure", p["pressure"]) > 0.1]
        eligible.sort(key=lambda p: (p.get("dependency_adjusted_pressure", p["pressure"]), 0.15 if self.is_fundamental(p["skill"]) else 0), reverse=True)
        if not eligible: return {}, []
        return self.limiter(eligible[0], 1), [self.limiter(p, i + 2) for i, p in enumerate(eligible[1:4])]

    def limiter(self, p: Dict[str, Any], rank: int) -> Dict[str, Any]:
        skill = p["skill"]; fams = [x["family"] for x in p.get("dominant_families", [])[:2]]
        return {"rank": rank, "skill": skill, "student_label": p.get("student_label"), "rubric": p.get("rubric"), "pressure": p.get("pressure"), "dependency_adjusted_pressure": p.get("dependency_adjusted_pressure"), "priority_level": p.get("priority_level"), "reason": self.reason_for_skill(skill, fams), "evidence": p.get("examples", []), "dominant_families": p.get("dominant_families", [])}

    def reason_for_skill(self, skill: str, fams: List[str]) -> str:
        if skill in {"GRAMMAR_CONTROL", "SENTENCE_CONSTRUCTION"}: return "Repeated sentence-level accuracy problems reduce clarity and GRA control" + (f"; dominant families: {', '.join(fams)}." if fams else ".")
        if skill in {"LEXICAL_CONTROL", "LEXICAL_PRECISION", "COLLOCATION_CONTROL", "LEXICAL_FORM_CONTROL", "REGISTER_CONTROL", "SEMANTIC_PHRASE_CONTROL"}: return "Repeated vocabulary-control problems affect precision, naturalness and LR" + (f"; dominant families: {', '.join(fams)}." if fams else ".")
        if skill in {"MEANING_RECOVERABILITY", "SEMANTIC_EVALUABILITY"}: return "Meaning stability is limiting how safely higher-level discourse can be evaluated."
        if skill == "DISCOURSE_EVALUABILITY": return "Discourse priorities must be treated cautiously because local language damage affects evaluability."
        if skill in {"IDEA_DEVELOPMENT", "SUPPORT_DEVELOPMENT", "REASONING_CHAIN_CONTROL", "EXAMPLE_USAGE"}: return "Claims need clearer support, examples or reasoning links."
        if skill in {"TASK_FULFILMENT", "POSITION_CONTROL"}: return "Task answer, position or required components need clearer control."
        if skill in {"COHERENCE_CONTROL", "COHESIVE_DEVICE_CONTROL"}: return "Progression, reference or linking creates reader effort."
        return "This skill creates repeated detector/scorer pressure."

    def confidence_envelope(self, lim: Dict[str, Any], skill_profiles: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not lim: return lim
        skill = lim.get("skill", "")
        sp = next((p for p in skill_profiles if p["skill"] == skill), None)
        if not sp: return lim
        root_count = sp.get("root_row_count", 0)
        examples   = sp.get("examples", [])
        confs      = [e.get("confidence", 0.75) for e in examples if isinstance(e, dict)]
        mean_conf  = round(sum(confs) / len(confs), 3) if confs else 0.75
        band       = "high" if (mean_conf >= 0.80 and root_count >= 3) else "medium" if (mean_conf >= 0.65 or root_count >= 2) else "low"
        lim["confidence_envelope"] = {"mean_evidence_confidence": mean_conf, "evidence_row_count": root_count, "confidence_band": band, "note": "low band: treat this limiter as indicative only; do not surface to student as certain finding." if band == "low" else None}
        return lim

    # ── Training targets ─────────────────────────────────────────────────────

    def training_targets(self, essay: Essay, skills: List[Dict[str, Any]], primary: Dict[str, Any], secondary: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        targets_list = self._v4_fine_targets
        target_scores:   Dict[str, float]      = defaultdict(float)
        target_evidence: Dict[str, List[Row]]  = defaultdict(list)
        target_families: Dict[str, Counter]    = defaultdict(Counter)
        rows      = [r for r in essay.rows if r.root_status == "root"]
        safe_rows = [r for r in rows if _v3_row_safety(r)["display_safety_status"] == "student_safe"]

        for r in safe_rows:
            txt = (r.quote + " " + r.local_quote + " " + r.problem_statement + " " + r.explanation).lower()
            for t in targets_list:
                tid     = t["id"]
                allowed = t.get("allowed_families", t.get("families", []))
                if not _v3_target_family_allowed(tid, r, allowed): continue
                skill_hit = r.skill in t.get("skills", [])
                fam_hit   = r.family in set(allowed)
                key_hit   = any(k and k.lower() in txt for k in t.get("keywords", []))
                if tid == "CHANGE_COST_EXPRESSIONS" and not any(w in txt for w in V3_COST_CORE_WORDS): continue
                if skill_hit or fam_hit or key_hit:
                    mult     = 1.0 + (0.30 if fam_hit else 0) + (0.10 if key_hit else 0)
                    roi_mult = _safe_float(t.get("roi_multiplier"), 1.0) or 1.0
                    target_scores[tid]            += r.pressure * mult * roi_mult
                    target_evidence[tid].append(r)
                    target_families[tid][r.family] += 1

        out = []; audit = []
        for tid, score in sorted(target_scores.items(), key=lambda x: x[1], reverse=True):
            t_def   = next((x for x in targets_list if x["id"] == tid), None)
            if not t_def: continue
            ev      = target_evidence[tid]
            allowed = t_def.get("allowed_families", t_def.get("families", []))
            purity  = _v3_target_purity(tid, ev, allowed)
            if tid == "CHANGE_COST_EXPRESSIONS":
                if not _v3_has_cost_domain(ev):
                    audit.append({"target_id": tid, "decision": "suppressed", "reason": "cost_spending_domain_missing"}); continue
                redir  = next((x for x in targets_list if x["id"] == "ABSTRACT_NOUN_COLLOCATIONS"), t_def)
                t_def  = redir; tid = redir["id"]
            if purity < 0.70 and tid not in {"SKILL_SEMANTIC_EVALUABILITY", "MEANING_RECOVERY_FIRST"}:
                audit.append({"target_id": tid, "decision": "suppressed", "reason": "family_purity_below_0_70", "purity": round(purity, 3)}); continue
            if len({r.row_id for r in ev}) < (2 if score >= 2.5 else 1):
                audit.append({"target_id": tid, "decision": "downgraded", "reason": "limited_evidence", "evidence_count": len(ev)})
            chosen = self.pick_rows(ev, 5); ex3 = self.pick_rows(ev, 3)
            seq    = t_def.get("practice_sequence") or (self.practice_sequence(t_def.get("skills", [""])[0]) if t_def.get("skills") else [])
            out.append({
                "rank": len(out) + 1, "target_id": tid, "learning_target": t_def.get("label", tid),
                "parent_skills": t_def.get("skills", []), "pressure": round(score, 3),
                "roi": self.roi_label(score, ev, t_def), "why_this_priority": self.target_reason(t_def, ev),
                "practice_focus": t_def.get("practice_focus", t_def.get("practice", "")),
                "practice_sequence": seq, "dependency_prerequisites": t_def.get("dependency_prerequisites", []),
                "dominant_families": [{"family": f, "count": c} for f, c in target_families[tid].most_common(4)],
                "example_quotes": [e.quote for e in ex3 if e.quote],
                "evidence_row_ids": [e.row_id for e in chosen],
                "evidence_examples": self.examples(ev, 3),
                "target_validation": {"family_purity": round(purity, 3), "student_safe_evidence_count": len(ev), "policy": "V4 display-safety + target-family compatibility + saturation-adjusted pressure"}
            })
            if len(out) >= 5: break

        if primary and all(primary.get("skill", "") not in t.get("parent_skills", []) for t in out[:3]):
            skill  = primary.get("skill", "")
            rows2  = [r for r in safe_rows if r.skill == skill or (skill == "SEMANTIC_EVALUABILITY" and r.family in V3_SEMANTIC_FAMILIES)]
            if rows2:
                out.insert(0, {"rank": 1, "target_id": f"SKILL_{skill}", "learning_target": self.skill_label(skill), "parent_skills": [skill], "pressure": round(sum(r.pressure for r in rows2), 3), "roi": self.roi_label(sum(r.pressure for r in rows2), rows2, {}), "why_this_priority": primary.get("reason"), "practice_focus": self.generic_practice(skill), "practice_sequence": self.practice_sequence(skill), "dependency_prerequisites": [], "dominant_families": primary.get("dominant_families", []), "example_quotes": [e.quote for e in self.pick_rows(rows2, 3) if e.quote], "evidence_row_ids": [e.row_id for e in self.pick_rows(rows2, 5)], "evidence_examples": self.examples(rows2, 3), "target_validation": {"family_purity": round(_v3_target_purity(f"SKILL_{skill}", rows2, []), 3), "student_safe_evidence_count": len(rows2), "policy": "V4 primary limiter target insertion"}})

        out = self._dependency_sequence_targets(out)
        dedup = []; seen: set = set()
        for t in out:
            if t["target_id"] in seen: continue
            seen.add(t["target_id"]); dedup.append(t)
        for i, t in enumerate(dedup[:5], 1): t["rank"] = i
        essay.raw["_priority_v4_target_audit"] = audit
        return dedup[:5]

    def _dependency_sequence_targets(self, targets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        target_ids = {t["target_id"]: i for i, t in enumerate(targets)}
        moved = True; iterations = 0
        while moved and iterations < 10:
            moved = False; iterations += 1
            for i, t in enumerate(targets):
                for prereq_id in t.get("dependency_prerequisites", []):
                    if prereq_id in target_ids:
                        prereq_pos = next((j for j, x in enumerate(targets) if x["target_id"] == prereq_id), None)
                        if prereq_pos is not None and prereq_pos > i:
                            prereq = targets.pop(prereq_pos)
                            targets.insert(i, prereq)
                            target_ids = {t["target_id"]: j for j, t in enumerate(targets)}
                            moved = True; break
                if moved: break
        return targets

    def target_reason(self, t: Dict[str, Any], rows: List[Row]) -> str:
        fams = ", ".join(f for f, _ in Counter(r.family for r in rows).most_common(2))
        return f"This target is selected because repeated detector rows map to {t.get('label', t.get('id', ''))} " + (f"({fams})." if fams else ".")

    def practice_sequence(self, skill: str) -> List[str]:
        entry = self.remediation.get(skill) if isinstance(self.remediation, dict) else None
        if isinstance(entry, dict):
            return entry.get("practice_sequence") or ([entry.get("first_action")] if entry.get("first_action") else [])
        return [self.generic_practice(skill)]

    def generic_practice(self, skill: str) -> str:
        if skill in {"GRAMMAR_CONTROL", "SENTENCE_CONSTRUCTION"}: return "Target the highest-frequency grammar/sentence family first with correction and rewriting drills."
        if skill in {"LEXICAL_CONTROL", "LEXICAL_PRECISION", "COLLOCATION_CONTROL"}: return "Replace unnatural phrases with precise topic collocations and reuse them in new sentences."
        if skill in {"IDEA_DEVELOPMENT", "SUPPORT_DEVELOPMENT", "REASONING_CHAIN_CONTROL"}: return "Practise claim → reason → example → explanation paragraph expansion."
        if skill in {"COHERENCE_CONTROL", "COHESIVE_DEVICE_CONTROL"}: return "Practise paragraph function labels and reference/connector repair."
        return "Practise corrections using the recurring examples from this essay."

    # ── Strengths ────────────────────────────────────────────────────────────

    def strengths(self, essay: Essay, skills: List[Dict[str, Any]], rubrics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        skill_pressure = {p["skill"]: p.get("dependency_adjusted_pressure", p["pressure"]) for p in skills}
        band_floor     = self._get_band_floor(essay)
        if essay.strengths_profile:
            for sp_item in essay.strengths_profile[:5]:
                if not isinstance(sp_item, dict): continue
                conf_val = str(sp_item.get("confidence", "medium")).lower()
                if not self._band_allows_detector_hit(band_floor, conf_val): continue
                skill_ref = sp_item.get("skill") or sp_item.get("category") or "LEXICAL_CONTROL"
                out.append(self.strength(f"DETECTOR_{sp_item.get('id', skill_ref)}", sp_item.get("label") or sp_item.get("description") or f"Controlled use of {skill_ref}", skill_ref, f"Detector strengths_profile: {sp_item.get('evidence_type', 'lr_positive_signal')} (confidence: {conf_val})", sp_item.get("coaching_note") or "Build on this controlled area as a model for weaker areas.", conf_val))
        para         = _safe_int(essay.metadata.get("paragraph_count"), 0)
        has_para_err = any(r.family == "PARAGRAPH_STRUCTURE" and r.root_status == "root" for r in essay.rows)
        min_para     = 3 if band_floor in {"band_floor_4", "band_floor_unknown"} else 4
        if para >= min_para and not has_para_err:
            out.append(self.strength("BASIC_STRUCTURE_PRESENT", "You have a usable essay structure.", "COHERENCE_CONTROL", f"Paragraph count is {para}, no root paragraph-structure problem detected.", "Keep the structure and improve content inside each body paragraph.", "high" if essay.metadata.get("prompt_present") else "medium"))
        layer0 = essay.layer0 or {}
        arg    = _get_path(layer0, "argument_skeleton", {}) or {}
        if arg.get("has_claim") and (arg.get("has_support") or arg.get("chain_complete")):
            if band_floor in {"band_floor_4", "band_floor_5", "band_floor_5_5", "band_floor_unknown"}:
                out.append(self.strength("ARGUMENT_SKELETON_PRESENT", "You are attempting a complete argument.", "IDEA_DEVELOPMENT", "Idea map found claim/support or chain-completion evidence.", "Upgrade with clearer reasons and more specific examples.", "medium"))
        pos       = layer0.get("position") or layer0.get("position_signal") or _get_path(essay.raw, "essay_map.position")
        pos_thresh= 1.5 if band_floor in {"band_floor_5_5", "band_floor_6", "band_floor_6_5"} else 2.5
        if pos and str(pos).lower() in {"clear", "present", "true"} and skill_pressure.get("TASK_FULFILMENT", 0) < pos_thresh and skill_pressure.get("POSITION_CONTROL", 0) < pos_thresh:
            out.append(self.strength("CLEAR_POSITION", "Your position is generally clear.", "POSITION_CONTROL", "Detector/idea map found a position signal.", "Keep the position explicit and link body paragraphs back to it.", "medium"))
        rec = _safe_float(essay.semantic.get("mean_recoverability"), None)
        if rec is not None:
            recn = rec / 3 if rec > 1 else rec
            rec_thresh = {"band_floor_4": 0.65, "band_floor_5": 0.72, "band_floor_5_5": 0.78, "band_floor_6": 0.85, "band_floor_6_5": 0.90}.get(band_floor, 0.78)
            if recn >= rec_thresh:
                out.append(self.strength("MEANING_RECOVERABLE", "Your ideas are usually understandable.", "MEANING_RECOVERABILITY", f"Mean recoverability is {recn:.2f}.", "Focus on precision and naturalness rather than rebuilding the whole essay.", "high"))
        gap_thresh = {"band_floor_5": 1.2, "band_floor_5_5": 1.5, "band_floor_6": 2.0, "band_floor_6_5": 2.0}.get(band_floor, 1.2)
        real = [r for r in rubrics if r["pressure"] > 0]
        if len(real) >= 2:
            lo = min(real, key=lambda x: x["pressure"]); hi = max(real, key=lambda x: x["pressure"])
            if hi["pressure"] - lo["pressure"] >= gap_thresh:
                out.append(self.strength("RELATIVE_RUBRIC_STRENGTH", f"{lo['rubric_name'].replace('_', ' ').title()} is relatively less pressured.", lo.get("top_skills", [None])[0] or lo["rubric"], f"Rubric pressure: low={lo['pressure']} vs high={hi['pressure']}.", "Use it as a base while you fix the highest-pressure target first.", "medium"))
        seen: set = set(); final = []
        for s in out:
            if s["id"] not in seen:
                final.append(s); seen.add(s["id"])
            if len(final) >= 3: break
        return final

    def _get_band_floor(self, essay: Essay) -> str:
        overall = essay.bands.get("overall")
        if overall is None: return "band_floor_unknown"
        if overall < 5:   return "band_floor_4"
        if overall < 5.5: return "band_floor_5"
        if overall < 6:   return "band_floor_5_5"
        if overall < 6.5: return "band_floor_6"
        return "band_floor_6_5"

    def _band_allows_detector_hit(self, band_floor: str, confidence: str) -> bool:
        thresholds = self._v4_band_thresholds.get("band_floors", {}).get(band_floor, {})
        allowed    = thresholds.get("strengths_allowed", [])
        for item in allowed:
            if item.get("id") in {"DETECTOR_POSITIVE_HIT", "BASIC_STRUCTURE_PRESENT"}:
                min_conf  = str(item.get("min_confidence_required", "low")).lower()
                conf_rank = {"low": 0, "medium": 1, "high": 2}
                if conf_rank.get(confidence, 0) >= conf_rank.get(min_conf, 0):
                    return True
        return band_floor in {"band_floor_4", "band_floor_5", "band_floor_unknown"}

    def strength(self, sid, label, skill, evidence, how, conf):
        return {"id": sid, "strength": label, "skill": skill, "evidence_source": evidence, "how_to_use_for_next_band": how, "confidence": conf}

    # ── Pattern intelligence ─────────────────────────────────────────────────

    def pattern_intelligence(self, essay: Essay, skills: List[Dict[str, Any]], families: List[Dict[str, Any]], targets: List[Dict[str, Any]]) -> Dict[str, Any]:
        root_total   = sum(f["pressure"] for f in families) or 0.0001
        top_f        = families[0] if families else None
        top_skill    = skills[0] if skills else None
        concentration= round(sum(f["pressure"] for f in families[:2]) / root_total, 3) if families else 0
        dominant     = f"Most pressure is concentrated in {top_f['family']} / {top_f['skill']} patterns." if top_f else None
        route        = (f"Fastest route: {targets[0]['learning_target']} because it explains repeated high-pressure rows." if targets else (f"Fastest route: {top_skill['student_label']} because it is the highest-pressure skill." if top_skill else "No stable route could be inferred from detector rows."))
        repair_summary = self._repair_pattern_summary(essay)
        task_note      = self._task_type_note(essay)
        return {"dominant_failure_pattern": dominant, "error_concentration": {"top_two_family_pressure_share": concentration, "interpretation": "concentrated" if concentration >= 0.55 else "distributed"}, "fastest_improvement_route": route, "improvement_potential": "high" if concentration >= 0.55 else "medium", "repair_pattern_summary": repair_summary, "task_type_specific_note": task_note}

    def _repair_pattern_summary(self, essay: Essay) -> List[Dict[str, Any]]:
        sat       = self._v4_sat
        min_sents = sat.get("repair_concentration_min_sentences", 3)
        op_sentences: Dict[str, set] = defaultdict(set)
        op_rows:  Dict[str, List[Row]] = defaultdict(list)
        for r in essay.rows:
            if r.root_status == "root" and r.repair_operation:
                op_sentences[r.repair_operation].add(r.sentence_index)
                op_rows[r.repair_operation].append(r)
        patterns = []
        for op, sents in op_sentences.items():
            if len(sents) >= min_sents:
                label_data = self._v4_repair_labels.get(op) or {}
                if not label_data:
                    for k, v in self._v4_repair_labels.items():
                        if op.startswith(k): label_data = v; break
                fallback = (self.registry.get("v4_repair_pattern_labels") or {}).get("generic_fallback", {})
                patterns.append({"repair_operation": op, "distinct_sentence_count": len(sents), "pattern_label": label_data.get("pattern_label") or fallback.get("pattern_label") or "Recurring pattern", "coaching_note": label_data.get("coaching_note") or fallback.get("coaching_note") or "", "target_ids": label_data.get("target_ids", []), "total_pressure": round(sum(r.pressure for r in op_rows[op]), 3)})
        return sorted(patterns, key=lambda x: x["total_pressure"], reverse=True)[:5]

    def _task_type_note(self, essay: Essay) -> Optional[str]:
        task_type = essay.task_profile.get("task_type") or essay.metadata.get("task_type")
        task_conf = _safe_float(essay.task_profile.get("task_type_confidence"), 0.0) or 0.0
        score_ready = bool(essay.metadata.get("score_ready", True))
        if not task_type or task_type == "unknown": return None
        policy_conf = 0.85 if not score_ready else 0.80
        if task_conf < policy_conf: return None
        task_types = (self._v4_task_notes.get("task_types") or {}) if isinstance(self._v4_task_notes, dict) else {}
        return task_types.get(str(task_type), {}).get("tr_framing_note")

    # ── Alternative hypothesis ───────────────────────────────────────────────

    def alternative_hypothesis(self, essay: Essay, primary: Dict[str, Any], skill_profiles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not primary or not skill_profiles: return None
        primary_skill  = primary.get("skill", "")
        primary_rubric = primary.get("rubric", "")
        candidates     = [p for p in skill_profiles if p["skill"] != primary_skill and p["rubric"] != "META" and p["rubric"] != primary_rubric and p.get("dependency_adjusted_pressure", p["pressure"]) > 0.5]
        if not candidates: return None
        alt = candidates[0]
        return {"alternative_skill": alt["skill"], "alternative_rubric": alt["rubric"], "alternative_pressure": alt.get("dependency_adjusted_pressure", alt["pressure"]), "note": f"If {primary_skill} is addressed first, {alt['skill']} may become the binding limiter. Monitor after corrections.", "confidence": "medium"}

    # ── Learning Intelligence ingestion payload ──────────────────────────────

    def learning_intelligence_ingestion_payload(self, essay: Essay, primary: Dict[str, Any], secondary: List[Dict[str, Any]], targets: List[Dict[str, Any]], strengths: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "li_contract_version": "LI_INGESTION_V1.1",
            "source_engine": ENGINE_VERSION,
            "essay_id": essay.essay_id,
            "student_id": essay.student_id,
            "created_at": _now_iso(),
            "skill_pressure_vector": {},
            "top_skills_by_pressure": [{"skill": primary.get("skill"), "rubric": primary.get("rubric"), "pressure": primary.get("dependency_adjusted_pressure") or primary.get("pressure"), "rank": 1}] + [{"skill": s.get("skill"), "rubric": s.get("rubric"), "pressure": s.get("dependency_adjusted_pressure") or s.get("pressure"), "rank": s.get("rank", i + 2)} for i, s in enumerate(secondary)],
            "recommended_targets": [{"target_id": t.get("target_id"), "learning_target": t.get("learning_target"), "pressure": t.get("pressure"), "roi": t.get("roi"), "rank": t.get("rank")} for t in targets[:3]],
            "confirmed_strengths": [{"id": s.get("id"), "skill": s.get("skill"), "confidence": s.get("confidence")} for s in strengths],
            "overall_band_estimate": essay.bands.get("overall"),
            "semantic_health": {"mean_recoverability": essay.semantic.get("mean_recoverability"), "affected_discourse_ratio": essay.semantic.get("affected_discourse_ratio")},
            "behavioral_event_type": "essay_analysis_completed",
            "note": "This payload is for ingestion by Learning Intelligence Engine only. Do not surface to student."
        }

    # ── QA flags (V4.0 base + V4.1 additions) ───────────────────────────────

    def qa_flags(self, essay: Essay, primary: Dict[str, Any], targets: List[Dict[str, Any]], skill_profiles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        flags = []
        if essay.bands.get("overall") is None:
            flags.append({"flag_type": "missing_bands", "severity": "warning", "message": "Scorer bands unavailable. Band-relative thresholds and band_unlock use conservative fallbacks."})
        root_count = sum(1 for r in essay.rows if r.root_status == "root")
        if root_count < 3:
            flags.append({"flag_type": "low_root_row_count", "severity": "warning", "message": f"Only {root_count} root rows after compression. Priority output may be unreliable; check detector input."})
        semantic_gate_count = sum(1 for r in essay.rows if r.suppression_reason == "semantic_gate_blocked_discourse")
        if semantic_gate_count >= 5:
            flags.append({"flag_type": "high_semantic_gate_suppression", "severity": "info", "message": f"{semantic_gate_count} discourse rows suppressed by semantic gate. Meaning recovery should be primary target."})
        if targets and targets[0].get("target_validation", {}).get("student_safe_evidence_count", 0) < 2:
            flags.append({"flag_type": "primary_target_low_evidence", "severity": "warning", "message": f"Primary target {targets[0].get('target_id')} has fewer than 2 student-safe evidence rows. Treat as indicative."})
        # V4.1: omit task_type_unknown if we extracted task_type from gate_applications
        if not essay.task_profile.get("task_type") and not essay.metadata.get("task_type"):
            flags.append({"flag_type": "task_type_unknown", "severity": "info", "message": "task_type not detected. task_type_specific_note and task-type gate (pass 8) are inactive."})
        if skill_profiles:
            root_rows = [r for r in essay.rows if r.root_status == "root"]
            if root_rows:
                total_p = sum(r.pressure for r in root_rows) or 1
                fam_pressure: Dict[str, float] = defaultdict(float)
                for r in root_rows: fam_pressure[r.family] += r.pressure
                top_fam = max(fam_pressure, key=lambda k: fam_pressure[k])
                if fam_pressure[top_fam] / total_p > 0.60:
                    flags.append({"flag_type": "single_family_dominance", "severity": "info", "message": f"Family {top_fam} contributes {round(fam_pressure[top_fam]/total_p*100)}% of total pressure. Saturation decay applied but output may still be skewed."})
        # V4.1: add synthesised-rows warning
        if essay.raw.get("_v4_1_synthesised_rows"):
            flags.append({"flag_type": "synthesised_rows_from_scorer", "severity": "warning", "message": "All rows were synthesised from scorer rubric_impact_map because the input is a scorer-only output (no full detector row data). Families are inferred from dominant_families per rubric. All rows are teacher_debug_only (no quote/local_quote). For student-safe output, feed detector output or a merged detector+scorer payload."})
        return flags

    # ── Self-validation ──────────────────────────────────────────────────────

    def self_validate(self, result: Dict[str, Any], essay: Essay) -> None:
        flags: List[Dict[str, Any]] = result.get("qa_flags", [])
        primary = result.get("primary_limiter") or {}
        if primary and not primary.get("skill"):
            flags.append({"flag_type": "self_validate_error", "severity": "error", "message": "primary_limiter missing skill field after analysis. Output integrity issue."})
        targets = result.get("fine_grained_training_targets", [])
        if not isinstance(targets, list):
            flags.append({"flag_type": "self_validate_error", "severity": "error", "message": "fine_grained_training_targets is not a list."})
        for t in (targets if isinstance(targets, list) else []):
            if isinstance(t, dict) and t.get("pressure", 1) == 0:
                flags.append({"flag_type": "self_validate_warning", "severity": "warning", "message": f"Target {t.get('target_id')} has pressure=0. May be spurious."})
        sp = result.get("skill_profiles", [])
        if len(sp) >= 2:
            pressures = [p.get("dependency_adjusted_pressure", p.get("pressure", 0)) for p in sp]
            if pressures != sorted(pressures, reverse=True):
                flags.append({"flag_type": "self_validate_warning", "severity": "warning", "message": "skill_profiles not sorted by dependency_adjusted_pressure. Possible ordering issue."})
        result["qa_flags"] = flags

    # ── Band unlock, display, feedback, root_summary ─────────────────────────

    def band_unlock(self, essay: Essay, primary: Dict[str, Any], secondary: List[Dict[str, Any]], targets: List[Dict[str, Any]]) -> Dict[str, Any]:
        overall      = essay.bands.get("overall")
        current_band = str(int(math.floor(overall))) if isinstance(overall, (int, float)) else None
        band_info    = (self.band_matrix.get("bands", {}) or {}).get(current_band, {}) if current_band and isinstance(self.band_matrix, dict) else {}
        local        = {"rubric": primary.get("rubric"), "skill": primary.get("skill"), "target": targets[0]["learning_target"] if targets else None}
        global_unlock= targets[0] if targets else None
        return {"local_unlock": local, "global_unlock": {"skill": primary.get("skill"), "target": global_unlock.get("learning_target") if global_unlock else None}, "band_matrix_reference": {"current_band_floor": current_band, "core_bottlenecks": band_info.get("core_bottlenecks"), "unlock_to_next": band_info.get(f"unlock_to_{int(current_band)+1}") if current_band and current_band.isdigit() else None}, "caution": "Band unlock is inferred from scorer/detector pressure, not an independent band prediction."}

    def display(self, essay: Essay, strengths: List[Dict[str, Any]], targets: List[Dict[str, Any]], skills: List[Dict[str, Any]]) -> Dict[str, Any]:
        roots  = sorted([r for r in essay.rows if r.root_status == "root"],  key=lambda r: r.pressure, reverse=True)
        hidden = sorted([r for r in essay.rows if r.root_status != "root"], key=lambda r: r.pressure, reverse=True)
        return {"show_in_short_feedback": {"max_strengths": strengths[:3], "max_learning_priorities": targets[:3], "representative_error_rows": self.examples(roots, 3)}, "show_in_detailed_feedback": {"skill_profiles": skills, "representative_error_rows": self.examples(roots, 12), "fine_grained_training_targets": targets}, "hide_or_collapse_from_student": self.examples(hidden, 12), "teacher_debug_only": {"collapsed_row_ids": [r.row_id for r in hidden[:100]]}}

    def feedback_generator_payload(self, essay: Essay, primary: Dict[str, Any], secondary: List[Dict[str, Any]], targets: List[Dict[str, Any]], strengths: List[Dict[str, Any]]) -> Dict[str, Any]:
        safe_rows = []; unsafe_rows = []
        task_note = self._task_type_note(essay)
        for r in essay.rows:
            d = _v3_row_safety(r)
            item = {"row_id": r.row_id, "quote": r.quote, "local_quote": r.local_quote, "full_sentence": r.local_quote or r.quote, "family": r.family, "skill": r.skill, "rubric": r.rubric, "sentence_index": r.sentence_index, "paragraph_index": r.paragraph_index, "paragraph_position": r.paragraph_position, "repair_operation": r.repair_operation, "problem_statement": r.problem_statement, "pressure": r.pressure, **d}
            (safe_rows if d["display_safety_status"] == "student_safe" and r.root_status == "root" else unsafe_rows).append(item)
        return {"contract_version": "PRIORITY_TO_FEEDBACK_V4", "essay_id": essay.essay_id, "primary_limiter": primary, "secondary_limiters": secondary, "validated_training_targets": targets, "validated_strengths": strengths, "student_safe_rows": safe_rows[:50], "teacher_debug_only_rows": unsafe_rows[:50], "task_type_specific_note": task_note, "requirements": {"full_sentence_required_for_student_examples": True, "feedback_engine_must_not_create_new_errors": True, "feedback_engine_may_hide_unsafe_evidence": True}}

    def root_summary(self, essay: Essay) -> Dict[str, Any]:
        roots    = [r for r in essay.rows if r.root_status == "root"]
        symptoms = [r for r in essay.rows if r.root_status != "root"]
        return {"root_row_count": len(roots), "symptom_or_collapsed_row_count": len(symptoms), "compression_ratio": round(len(symptoms) / max(1, len(essay.rows)), 3), "suppression_reasons": [{"reason": k, "count": v} for k, v in Counter(r.suppression_reason or r.root_status for r in symptoms).most_common()], "principle_used": "V4: 8 compression passes; saturation decay applied before compression; repair-op dedup and paragraph-cluster collapse added."}

    # ── Helpers ──────────────────────────────────────────────────────────────

    def pick_rows(self, rows: List[Row], n: int) -> List[Row]:
        rows = sorted(rows, key=lambda r: r.pressure, reverse=True); out = []; seen: set = set()
        for r in rows:
            key = (r.quote, r.family, r.sentence_index)
            if key in seen: continue
            seen.add(key); out.append(r)
            if len(out) >= n: break
        return out

    def examples(self, rows: List[Row], n: int) -> List[Dict[str, Any]]:
        return [{**{"row_id": r.row_id, "skill": r.skill, "family": r.family, "rubric": r.rubric, "quote": r.quote, "local_quote": r.local_quote, "full_sentence": r.local_quote or r.quote, "sentence_index": r.sentence_index, "paragraph_index": r.paragraph_index, "paragraph_position": r.paragraph_position, "repair_operation": r.repair_operation, "pressure": r.pressure, "root_status": r.root_status, "suppression_reason": r.suppression_reason, "problem_statement": r.problem_statement, "explanation": r.explanation}, **_v3_row_safety(r)} for r in self.pick_rows(rows, n)]

    def skill_label(self, skill: str) -> str:
        d = self.ontology.get(skill, {}) if isinstance(self.ontology, dict) else {}
        return d.get("student_label") or d.get("name") or skill.replace("_", " ").title()

    def skill_rubric(self, skill: str, rows: List[Row]) -> str:
        d  = self.ontology.get(skill, {}) if isinstance(self.ontology, dict) else {}
        r  = d.get("ielts_rubric"); nr = _norm_rubric(r)
        if nr: return nr
        return Counter([x.rubric for x in rows]).most_common(1)[0][0] if rows else "UNKNOWN"

    def is_fundamental(self, skill: str) -> bool:
        d = self.ontology.get(skill, {}) if isinstance(self.ontology, dict) else {}
        return bool(d.get("fundamental", False))

    def skill_observability(self, skill: str) -> Dict[str, Any]:
        arr = self.observability.get("skills") if isinstance(self.observability, dict) else None
        if isinstance(arr, list):
            for x in arr:
                if x.get("skill_id") == skill:
                    return {"observability_from_detector_scorer": x.get("observability_from_detector_scorer"), "confidence": x.get("confidence")}
        return {"observability_from_detector_scorer": "family_or_metric_inference", "confidence": "medium"}

    def priority_level(self, p: float) -> str:
        return "very_high" if p >= 4 else "high" if p >= 2.5 else "medium" if p >= 1.2 else "low" if p > 0 else "none"

    def roi_label(self, score: float, rows: List[Row], target: Dict[str, Any]) -> str:
        sent = len({r.sentence_index for r in rows if r.sentence_index is not None})
        return "high" if score >= 3 and sent >= 2 else "medium" if score >= 1.2 else "low"

    def batch_summary(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        prim    = Counter((r.get("primary_limiter") or {}).get("skill") for r in results if r.get("primary_limiter"))
        targets = Counter((r.get("fine_grained_training_targets") or [{}])[0].get("target_id") for r in results if r.get("fine_grained_training_targets"))
        return {"essay_count": len(results), "primary_limiter_distribution": [{"skill": k, "count": v} for k, v in prim.most_common()], "top_training_target_distribution": [{"target_id": k, "count": v} for k, v in targets.most_common()]}

    # ── V4.3: TR/CC row injection ────────────────────────────────────────────

    def _inject_tr_cc_rows(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        If TR or CC rubric band is significantly below overall band, inject
        synthetic rows for those rubrics into each result in the payload.
        Called BEFORE the core pipeline so rows are visible during analysis.
        """
        import copy as _copy
        results_raw = payload.get("results")
        if not isinstance(results_raw, list):
            return payload
        payload = _copy.deepcopy(payload)
        for result in payload["results"]:
            if not isinstance(result, dict):
                continue
            score_profile = result.get("score_profile") or {}
            rubrics_block = score_profile.get("rubrics") or {}
            rubric_impact = result.get("rubric_impact_map") or []
            bands: Dict[str, Optional[float]] = {"task_response": None, "coherence_cohesion": None, "overall": None}
            if isinstance(rubrics_block, dict):
                for rn, rd in rubrics_block.items():
                    if not isinstance(rd, dict): continue
                    b = _safe_float(rd.get("band_rounded") or rd.get("band"), None)
                    if rn.lower() in ("task_response", "tr"):        bands["task_response"] = b
                    elif rn.lower() in ("coherence_cohesion", "cc"): bands["coherence_cohesion"] = b
                    elif rn.lower() in ("overall", "overall_band_estimate"):
                        bands["overall"] = _safe_float(score_profile.get("overall_band_estimate"), None) or b
            overall = bands["overall"]
            if overall is None:
                continue
            impact_by_rubric: Dict[str, float] = {}
            for entry in (rubric_impact if isinstance(rubric_impact, list) else []):
                if not isinstance(entry, dict): continue
                rub = str(entry.get("rubric") or "").lower()
                imp = _safe_float(entry.get("impact_weight"), 0.85) or 0.85
                if rub not in impact_by_rubric:
                    impact_by_rubric[rub] = imp
            to_inject = []
            for rubric_key, rubric_label, families in [
                ("task_response",      "task_response",      _TR_SYNTH_FAMILIES),
                ("coherence_cohesion", "coherence_cohesion", _CC_SYNTH_FAMILIES),
            ]:
                band_val = bands.get(rubric_key)
                if band_val is not None and (overall - band_val) >= TR_CC_SYNTHESIS_THRESHOLD:
                    to_inject.append((rubric_key, rubric_label, families))
            if not to_inject:
                continue
            row_list = None
            for path_key in ["student_rows", "candidate_lists.survived_candidates"]:
                candidate = result.get(path_key) if "." not in path_key else _get_path(result, path_key)
                if isinstance(candidate, list):
                    row_list = candidate; break
            if row_list is None:
                result.setdefault("student_rows", [])
                row_list = result["student_rows"]
            for rubric_key, rubric_label, families in to_inject:
                impact = impact_by_rubric.get(rubric_label, 0.85)
                for fam in families:
                    row_list.append({
                        "rubric": rubric_label, "family": fam,
                        "score_charge_weight": impact, "chargeable": True, "chargeable_for_scoring": True,
                        "confidence": 0.65, "severity": "medium" if impact >= 0.85 else "low",
                        "source": "v4_3_tr_cc_synthesis", "root_or_secondary": "root",
                        "quote": "", "local_quote": "",
                        "problem_statement": (f"Synthesised: rubric {rubric_label.upper()} band is {(bands[rubric_key] or 0):.1f} vs overall {overall:.1f} (delta={overall - (bands[rubric_key] or 0):.1f}). Family: {fam}."),
                        "explanation": "Row synthesised by PE V4.3 because rubric band gap exceeds threshold. No direct detector evidence; represents rubric pressure signal.",
                        "_v4_3_tr_cc_injected": True,
                    })
        return payload

    # ── V4.3: Band weight helpers ────────────────────────────────────────────

    @staticmethod
    def _get_rubric_from_profile(prof: Dict[str, Any]) -> str:
        return str(prof.get("rubric") or "UNKNOWN")

    def _apply_band_weights(self, essay_r: Dict[str, Any], overall_band: float) -> None:
        bucket = _band_bucket(overall_band)
        for prof in essay_r.get("skill_profiles") or []:
            rubric_raw = self._get_rubric_from_profile(prof)
            rubric_key = _resolve_rubric_key(rubric_raw)
            weight_map = _BAND_SENSITIVITY_WEIGHTS.get(rubric_key, {})
            weight     = weight_map.get(bucket, 1.0)
            if weight != 1.0:
                current = _safe_float(prof.get("pressure"), 0.0) or 0.0
                prof["pressure"]            = round(current * weight, 4)
                prof["_v4_3_band_weight"]   = weight
                prof["_v4_3_band_bucket"]   = bucket

    def _fix_meta_primary(self, essay_r: Dict[str, Any]) -> None:
        pl = essay_r.get("primary_limiter") or {}
        if pl.get("rubric") in _META_RUBRIC_VALUES:
            essay_r["_v4_3_meta_primary_detected"] = True

    # ── V4.4: _reselect_primary (all-META fallback) ──────────────────────────

    def _reselect_primary(self, essay_r: Dict[str, Any]) -> None:
        """
        V4.4 version: adds all-META degenerate case guard.
        When every skill_profile has rubric==META, calls _inject_all_meta_eci_block().
        """
        profs = essay_r.get("skill_profiles") or []
        if not profs:
            return
        eligible = [p for p in profs if p.get("rubric") not in _META_RUBRIC_VALUES]
        # V4.4 fix: all-META degenerate case
        if not eligible:
            _inject_all_meta_eci_block(essay_r)
            return
        eligible_sorted = sorted(eligible, key=lambda p: _safe_float(p.get("pressure"), 0.0) or 0.0, reverse=True)
        best = eligible_sorted[0]
        pl   = essay_r.get("primary_limiter") or {}
        old_rubric = pl.get("rubric")
        new_rubric = best.get("rubric")
        if old_rubric == new_rubric:
            pl["dependency_adjusted_pressure"] = round(_safe_float(best.get("pressure"), pl.get("dependency_adjusted_pressure") or 0.0) or 0.0, 4)
        else:
            pl["rubric"] = new_rubric
            pl["dependency_adjusted_pressure"] = round(_safe_float(best.get("pressure"), 0.0) or 0.0, 4)
            pl["skill"]  = (best.get("skill") if isinstance(best.get("skill"), str) else (best.get("skill") or {}).get("skill_id", "UNKNOWN") if isinstance(best.get("skill"), dict) else "UNKNOWN")
            examples     = best.get("examples") or []
            pl["evidence"] = [{"family": e.get("family", ""), "problem_statement": e.get("problem_statement", ""), "quote": e.get("quote", ""), "local_quote": e.get("local_quote", ""), "confidence": e.get("confidence", 0.65)} for e in examples[:5]]
            pl["_v4_3_primary_reselected"] = True
            pl["_v4_3_old_rubric"]         = old_rubric
            essay_r["primary_limiter"]     = pl
        secondaries = eligible_sorted[1:4]
        new_sl = []
        for s_prof in secondaries:
            new_sl.append({"rubric": s_prof.get("rubric"), "skill": (s_prof.get("skill") if isinstance(s_prof.get("skill"), str) else (s_prof.get("skill") or {}).get("skill_id", "UNKNOWN") if isinstance(s_prof.get("skill"), dict) else "UNKNOWN"), "pressure": round(_safe_float(s_prof.get("pressure"), 0.0) or 0.0, 4), "evidence": [(e.get("family", "")) for e in (s_prof.get("examples") or [])[:3]]})
        if new_sl:
            essay_r["secondary_limiters"] = new_sl

    # ── V4.3: Practice discounts ─────────────────────────────────────────────

    def _apply_practice_discounts(self, essay_r: Dict[str, Any], discounts: Dict[str, float]) -> None:
        if not discounts:
            return
        families_discounted = []
        for prof in essay_r.get("skill_profiles") or []:
            examples       = prof.get("examples") or []
            family_counts: Dict[str, int] = {}
            for ex in examples:
                fam = ex.get("family")
                if fam: family_counts[fam] = family_counts.get(fam, 0) + 1
            if not family_counts: continue
            dominant_family = max(family_counts, key=family_counts.__getitem__)
            mastery = discounts.get(dominant_family)
            if mastery is None: continue
            discount = _mastery_discount(mastery)
            if discount > 0:
                current = _safe_float(prof.get("pressure"), 0.0) or 0.0
                prof["pressure"]                  = round(current * (1.0 - discount), 4)
                prof["_v4_3_practice_discount"]   = discount
                prof["_v4_3_practice_mastery"]    = mastery
                families_discounted.append(dominant_family)
        if families_discounted:
            essay_r["_v4_3_practice_feedback_applied"] = True
            essay_r["_v4_3_families_discounted"]       = families_discounted

    # ── V4.3: ECI gate ───────────────────────────────────────────────────────

    def _apply_eci_gate(self, essay_r: Dict[str, Any]) -> None:
        qa_flags = essay_r.get("qa_flags") or []
        blocked  = any(isinstance(f, dict) and f.get("flag_type") == _ECI_BLOCK_FLAG_TYPE for f in qa_flags)
        if blocked:
            essay_r["fine_grained_training_targets"] = []
            pl = essay_r.get("primary_limiter") or {}
            pl["evidence"] = []; pl["_v4_3_eci_blocked"] = True
            essay_r["primary_limiter"] = pl
            essay_r["_v4_3_eci_hard_block"] = True

    # ── V4.4: _update_debug_counts ───────────────────────────────────────────

    def _update_debug_counts(self, essay_r: Dict[str, Any]) -> None:
        """V4.4: adds all_meta_fallback_block tracking field."""
        dc = essay_r.setdefault("debug_counts", {})
        dc["fp_suppressed_rows"]        = essay_r.pop("_v4_3_fp_suppressed", 0)
        dc["tr_cc_injected"]            = essay_r.get("_v4_3_tr_cc_injected_count", 0)
        dc["meta_primary_detected"]     = essay_r.pop("_v4_3_meta_primary_detected", False)
        dc["primary_reselected"]        = (essay_r.get("primary_limiter") or {}).get("_v4_3_primary_reselected", False)
        dc["eci_hard_block"]            = essay_r.pop("_v4_3_eci_hard_block", False)
        dc["practice_feedback_applied"] = essay_r.pop("_v4_3_practice_feedback_applied", False)
        dc["families_discounted"]       = essay_r.pop("_v4_3_families_discounted", [])
        dc["all_meta_fallback_block"]   = essay_r.pop("_v4_4_all_meta_detected", False)  # V4.4
        total_rows = dc.get("root_rows", 0) or 0
        fp_sup     = dc["fp_suppressed_rows"]
        dc["ssr_fp"] = round(fp_sup / total_rows, 4) if total_rows > 0 else 0.0

    # ── V4.4: merged analyze_payload ────────────────────────────────────────

    def analyze_payload(
        self,
        payload: Dict[str, Any],
        practice_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        V4.4 self-contained analyze_payload.
        Inlines the V4.0 base pipeline (no super() call), then applies
        V4.2 merge summary, V4.3 post-processing, and V4.4 stamps.
        """
        # V4.3 pre-processing: TR/CC row injection
        payload = self._inject_tr_cc_rows(payload)
        # Load practice discounts (V4.3)
        practice_discounts = _load_practice_discounts(practice_payload)

        # V4.0 base pipeline (inlined)
        essays       = self.normalize_payload(payload)
        results_list = [self.analyze_essay(e) for e in essays]
        result = {
            "schema_version":  OUTPUT_SCHEMA_VERSION,
            "engine_version":  ENGINE_VERSION,   # V4.4
            "created_at":      _now_iso(),
            "registry": {
                "knowledge_path_requested":   self.registry.knowledge_path,
                "manifest_schema_version":    self.registry.manifest.get("schema_version"),
                "loaded_resource_ids":        sorted(self.registry.loaded.keys()),
                "missing_files":              self.registry.missing,
                "v4_fine_targets_source":     "registry_20_fine_grained_targets_v4" if self.registry.get("v4_fine_targets") else "v3_fallback_hardcoded",
            },
            "input_summary": {
                "input_schema_version": payload.get("schema_version"),
                "detected_essay_count": len(essays),
            },
            "results":       results_list,
            "batch_summary": self.batch_summary(results_list),
            "qa": {"premium_boundary": "detector/scorer evidence only; LRET and independent essay evaluation ignored"},
        }

        # V4.2: surface merge summary if present
        if "_merge_summary" in payload:
            result["input_summary"]["merge_summary"] = payload["_merge_summary"]

        # V4.3 + V4.4: post-process per-essay results
        for essay_r in result.get("results") or []:
            bands        = essay_r.get("bands_if_available") or {}
            overall_band = _safe_float(bands.get("overall"), None)

            self._fix_meta_primary(essay_r)
            if overall_band is not None:
                self._apply_band_weights(essay_r, overall_band)
            self._reselect_primary(essay_r)   # V4.4 version (all-META guard)

            if practice_discounts:
                self._apply_practice_discounts(essay_r, practice_discounts)
                self._reselect_primary(essay_r)

            self._apply_eci_gate(essay_r)
            self._update_debug_counts(essay_r)   # V4.4 version

        # V4.3: practice feedback summary in input_summary
        if practice_discounts:
            result.setdefault("input_summary", {})["practice_feedback"] = {
                "families_with_mastery": {k: round(v, 3) for k, v in practice_discounts.items()},
                "discount_applied":       True,
            }

        return result


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="VA IELTS Priority Engine V4.4 — self-contained, no cross-file imports"
    )
    p.add_argument("--input",    "-i", required=True, help="Detector/scorer JSON input")
    p.add_argument("--output",   "-o", default=None,  help="Priority output JSON path")
    p.add_argument("--scorer",   "-s", default=None,
                   help=(
                       "Optional scorer batch output JSON (SCORER_BATCH_OUTPUT_V1.1). "
                       "When supplied, scorer fields (bands, rubric_impact_map, "
                       "score_explanation_payload) are merged onto detector results by essay_id."
                   ))
    p.add_argument("--practice-result", "-p", default=None, dest="practice_result",
                   help=(
                       "Optional Practice Engine session result JSON (PE V5.x output). "
                       "Per-family mastery levels are read and applied as pressure discounts."
                   ))
    p.add_argument("--knowledge", "-k", default=None,
                   help="Folder or zip containing registry JSON files.")
    p.add_argument("--manifest",  "-m", default=None,
                   help="Optional registry manifest path (overrides default discovery).")
    p.add_argument("--stdout", action="store_true",
                   help="Print JSON output to stdout (implied when --output is omitted).")
    p.add_argument("--validate-registry", action="store_true",
                   help="Only validate/load registry resources, then exit.")
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)

    reg = Registry.load(args.knowledge, args.manifest)

    if args.validate_registry:
        print(json.dumps({
            "engine_version":          ENGINE_VERSION,
            "knowledge_path":          reg.knowledge_path,
            "manifest_schema_version": reg.manifest.get("schema_version"),
            "loaded":                  reg.loaded,
            "missing":                 reg.missing,
        }, ensure_ascii=False, indent=2))
        return 0

    detector_payload = _read_json_file(args.input)

    # V4.2 merge step
    if args.scorer:
        scorer_payload = _read_json_file(args.scorer)
        payload        = merge_inputs(detector_payload, scorer_payload)
        print(
            f"[V4.4] Merged {payload['_merge_summary']['matched_essays']} essays "
            f"({payload['_merge_summary']['unmatched_essays']} unmatched).",
            flush=True,
        )
    else:
        payload = detector_payload

    # Load optional practice result (V4.3)
    practice_payload: Optional[Dict[str, Any]] = None
    if args.practice_result:
        practice_payload = _read_json_file(args.practice_result)
        families = _load_practice_discounts(practice_payload)
        print(
            f"[V4.4] Practice feedback loaded: {len(families)} families with mastery data.",
            flush=True,
        )

    engine = PriorityEngine(reg)
    out    = engine.analyze_payload(payload, practice_payload=practice_payload)

    if args.output:
        _write_json_file(args.output, out)
    if args.stdout or not args.output:
        print(json.dumps(out, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
