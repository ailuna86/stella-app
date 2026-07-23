#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
det_vip_v18d_1.py — VA Premium Detector v18d.1

v18d.1 changes vs v18d.0:
- SCORER_PAYLOAD_EXPANSION: scorer_payload now includes three new blocks:
  • `metadata` — word_count, sentence_count, paragraph_count,
    error_free_sentence_ratio. Critical for scorer density formulas and
    Gate 1 (word-count gate). Previously absent, causing scorer to default
    to wc=250 and silencing all word-count gates.
  • `task_profile` — compact scorer-facing structure: task_type,
    covered_required_components, total_required_components,
    missing_required_components, hard_fail_components,
    task_completeness_confidence, score_ready.
    Required by scorer Gate 3 (task-schema TR gates).
  • `lr_positive_signals` — ocd_positive_hits (from strengths_profile when
    available, else 0) and LR11_dynamic_multiword_density (proxy from
    collocation family damage density). Required by scorer Gate 7
    (high-band LR eligibility).
  All additions are purely additive; existing keys are unchanged.
- detector_metric_profile.shared expanded with word_count, sentence_count,
  paragraph_count so scorer can also inherit these from the DMP path.
- No logic changes to detection, arbitration, or scoring layers.

det_vip_v13.py — VA Premium Detector v13.0

v13 changes vs v12:
- FIX CRITICAL: Model names corrected — gpt-4o-mini (CHEAP) and gpt-4o (STRONG).
  v12 used "gpt-5-mini"/"gpt-5" which are not valid OpenAI API strings; all LLM calls
  silently failed (calls_succeeded: 0/9 per essay). LLM engine is now live.
  Set VIP_CHEAP_MODEL / VIP_STRONG_MODEL env vars to override (e.g. to use gpt-5 once available).
- SVA rule-based engine disabled: rule-based SVA (rules_registry / rules_support) had 74% FP
  rate (49 FPs / 66 charged). Disabled in false_positive_veto(). LT-corroborated and LLM-
  detected SVA still active — those sources are reliable.
- Strengths detection added: new detect_strengths() runs rule-based + LLM analysis and
  populates evaluator_payload.strengths_profile with lexical, grammatical, cohesion and
  task-response strengths. Complies with DETECTOR_OUTPUT_V1.1 contract (added to
  evaluator_payload which has no additionalProperties constraint).
- CLAUSE_STRUCTURE dangling-subordinate rule fixed: regex was r",\\s*[A-Z]" — missed main
  clauses starting with lowercase pronouns (they/we/people). Changed to r",\\s*[a-zA-Z]".

v11 changes vs v10:
- Fix A: det_vip_v10/resources/ added to canonical resource search path
- Fix B: POSSESSIVE_FORM gate (clear pronoun error OR morphology loaded required)
- Fix B: SVA gate (passive/modal constructions demoted to review_only)
- Fix C: Stage 7 LLM judge now ACTIVE — possible_fp→suppress, uncertain→demote
- C1: modal/passive distinction in false_positive_veto (narrow to bare infinitive)
- C2: L3 quote precision instruction in system prompt
- C2: L1/L2 system prompt removes GRAMMATICAL_RANGE, adds discourse family targeting
- C3: resource_confirmation_bonus block in score_family_in_cluster (+morphology,+prep,+colloc,+register)
- C4: OPERATION_FAMILY_OVERRIDE in select_root_family (generic family → specific via repair op)
- C6: Quality gate for 0.72-threshold LLM rows (quote substance + minimal repair required)

Original v10 changes from v9.4:
- FIX 1: APP_VERSION updated; dead duplicate definitions of arbitrate/infer_task_schema/
  l3_va25_support/va25_resource_status/detector_only_qa_scan/shape_response removed.
  Only the v9.3 final versions are kept.
- FIX 2: DecisionRegistries dataclass gains proper rule_registry and task_schema_registry fields.
  setattr hacks removed; load_decision_registries() populates them as proper fields.
- FIX 3: FAMILY_TO_DEFAULT_V9_OPERATION and OPERATION_FAMILY_LOCKS completed with 14 missing families.
- FIX 4: V93_FAMILY_SPECIFICITY completed with 17 missing entries.
- FIX 5: _v93_quote_issue_compatibility extended with CC/TR/grammar family branches.
- FIX 6: infer_task_schema wires hard_fail_if_missing and task_completeness_confidence.
- FIX 7: l3_universal_rules FP guards for for+base-verb and a/an+plural.
- FIX 8: l3_llm_local and layer1_2_llm_discourse include GRAMMATICAL_RANGE.
- FIX 9: V93_FAMILY_TO_OPERATION completed with 18 missing entries.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import re
import sys
import concurrent.futures
import threading
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from fastapi import FastAPI
    from pydantic import BaseModel, Field
except Exception:
    FastAPI = None
    BaseModel = object
    Field = None

try:
    import spacy  # type: ignore
except Exception:
    spacy = None

try:
    import language_tool_python  # type: ignore
except Exception:
    language_tool_python = None

APP_NAME = "VA Premium Detector v9"
APP_VERSION = "det_vip_v18d.2_tr567_direct_score_ready_fix"
DET_VIP_PATCH_VERSION = "v18d.3-topic-alignment-risk-safety-net"

# ── v18b P0 / P1 fix gates ────────────────────────────────────────────────────
# P0-FIX-2: REGISTER contraction whitelist (0 TP / 11 FP vs benchmark).
# These extremely common contractions appear in student essays but the benchmark
# doesn't evaluate REGISTER, so they show as pure FPs.  Add items here to skip them.
_REGISTER_CONTRACTION_WHITELIST: frozenset = frozenset({
    "don't", "didn't", "couldn't", "hasn't", "it's", "isn't", "that's",
    "won't", "wouldn't", "shouldn't", "doesn't", "haven't", "hadn't",
    "aren't", "weren't", "wasn't",
    "i'm", "i've", "i'll", "i'd", "we're", "we've", "we'll",
    "they're", "they've", "there's", "what's", "who's",
    "he's", "she's", "you're", "you've", "you'll", "you'd",
})

# P0-FIX-4: LT SPELLING whitelist — proper nouns / technical terms that LT flags
# as spelling errors but are valid in context (e.g. cultural names, loanwords).
_LT_SPELLING_WHITELIST: frozenset = frozenset({
    "hanfu", "hanbok", "qipao", "cheongsam", "kebaya", "sari",  # cultural garment terms
    "anime", "manga", "vlog", "vlogger", "selfie", "hashtag",   # modern tech/media
    "fintech", "edtech", "healthtech", "insurtech",              # tech compound words
    "globalisation", "globalise", "globalised",                  # BrE (covered by locale but belt+braces)
})
QA_CONTRACT_VERSION = "QA-CONTRACT-1.0+DETECTOR"
LLM_POLICY_VERSION = "v9.3-specific-family-local_first-full_diagnostic"
RESOURCE_VERSION = "canonical-resources+json-rule-registry-v9.3-full-diagnostic"

MAX_ALLOWED_WORDS = int(os.environ.get("VIP_MAX_ALLOWED_WORDS", "300"))
# v14: gpt-4o-mini (CHEAP / fast detection) + gpt-5o-mini (STRONG / Stage-7 audit judge).
# gpt-5 was tried in v13 Stage-7 but 0% of calls succeeded — gpt-5o-mini is the working alias.
# Override via env vars if your API key resolves different aliases:
#   export VIP_CHEAP_MODEL=gpt-4o-mini
#   export VIP_STRONG_MODEL=gpt-5o-mini
CHEAP_MODEL  = os.environ.get("VIP_CHEAP_MODEL",  "gpt-4o-mini")    # L0/L0.5/L1-2/L3 detection + strengths + semantic passes
STRONG_MODEL = os.environ.get("VIP_STRONG_MODEL", "gpt-4o-mini")    # Stage-7 audit judge
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
ENABLE_LLM_DEFAULT = os.environ.get("VIP_ENABLE_LLM", "1").lower() in {"1", "true", "yes"}
FINAL_ARBITRATION_CHUNK = int(os.environ.get("VIP_FINAL_ARBITRATION_CHUNK", "20"))
LOCAL_LLM_MAX_ITEMS_PER_SENT = int(os.environ.get("VIP_LOCAL_LLM_MAX_ITEMS_PER_SENT", "6"))
SPACY_MODEL = os.environ.get("VIP_SPACY_MODEL", os.environ.get("SPACY_MODEL", "en_core_web_sm"))
LT_LANGUAGE = os.environ.get("VIP_LT_LANGUAGE", "en-US")

TASK_SCHEMA_VERSION = "task-schema-v1.1"
TAXONOMY_VERSION = "va-taxonomy-v9.0"
RUBRIC_VERSION = "ielts-writing-task2-diagnostic-v1.0"
SCORING_VERSION = "detector-only-no-score-report-v9.0"

DEFAULT_CANONICAL_RESOURCE_DIR = os.environ.get(
    "VA_CANONICAL_RESOURCE_DIR",
    os.environ.get(
        "VIP_CANONICAL_RESOURCE_DIR",
        r"C:\Users\Ailuna Shamurzaeva\OneDrive\Desktop\AGART\VA English, IELTS\va_resources_canonical\final_app_registries_v3_CONSOLIDATED_CANONICAL\final_app_registries_v3_CONSOLIDATED_CANONICAL",
    ),
)
VIP_RESOURCE_DIRS_ENV = os.environ.get("VIP_RESOURCE_DIRS", "")

DEFAULT_V9_REGISTRY_DIR = os.environ.get(
    "VIP_DETECTOR_REGISTRY_DIR",
    r"C:\Users\Ailuna Shamurzaeva\OneDrive\Desktop\AGART\VA English, IELTS\premium version\detector\det_vip_v10",
)

# v9.7: single contract file replaces the 4 separate B2C schema files.
# detector_contract_v1_1_detailed.json contains input_contract, output_contract,
# shared_definitions (Identity, Run, DiagnosticRow, QA, etc.) in one document.
DETECTOR_CONTRACT_DIR = Path(os.environ.get("VA_CONTRACT_DIR", DEFAULT_V9_REGISTRY_DIR))
DETECTOR_CONTRACT_FILENAME = "detector_contract_v1_1_detailed.json"
DETECTOR_CONTRACT_PATH = DETECTOR_CONTRACT_DIR / DETECTOR_CONTRACT_FILENAME

def _load_json_file_safe(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {"loaded": False, "path": str(path), "error": "file_not_found"}
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"loaded": True, "path": str(path), "schema_version": data.get("schema_version") or data.get("$id") or data.get("title"), "data": data}
    except Exception as e:
        return {"loaded": False, "path": str(path), "error": str(e)[:500]}

def b2c_schema_status() -> Dict[str, Any]:
    """Load and report status of the unified detector contract file (v1.1)."""
    contract_s = _load_json_file_safe(DETECTOR_CONTRACT_PATH)
    data = contract_s.get("data") or {}
    return {
        "contract_dir": str(DETECTOR_CONTRACT_DIR),
        "contract_file": DETECTOR_CONTRACT_FILENAME,
        "contract_path": str(DETECTOR_CONTRACT_PATH),
        "loaded": contract_s.get("loaded", False),
        "schema_version": data.get("schema_version"),
        "engine_id": data.get("engine_id"),
        "input_contract_id": (data.get("input_contract") or {}).get("contract_id"),
        "output_contract_id": (data.get("output_contract") or {}).get("contract_id"),
        "error": contract_s.get("error"),
        "all_required_loaded": contract_s.get("loaded", False),
        "policy": "v9.7: single detector_contract_v1_1_detailed.json replaces 4 separate B2C schema files.",
    }

# FIX 2: V9_REGISTRY_FILES now includes rule_registry and task_schema_registry
V9_REGISTRY_FILES = {
    "family_lock_registry": "family_lock_registry_v2.json",
    "repair_operation_registry": "repair_operation_registry_v2.json",
    "rule_registry": "rule_registry_v1.json",
    "task_schema_registry": "task_schema_registry_v1.json",
    "survival_gate_policy": "survival_gate_policy_v1.json",
    "dominant_repair_scoring_guide": "dominant_repair_scoring_guide_v1.json",
    "suppression_taxonomy": "suppression_taxonomy_v1.json",
    "severity_framework": "severity_framework_v1.json",
}

# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------
GRAMMAR_FAMILIES = {
    "ARTICLE_DETERMINER", "NOUN_NUMBER_COUNTABILITY", "POSSESSIVE_FORM",
    "SUBJECT_VERB_AGREEMENT", "VERB_TENSE", "VERB_FORM", "VERB_PATTERN",
    "PREPOSITION_PATTERN", "PRONOUN_AGREEMENT", "PRONOUN_CASE",
    "ADJECTIVE_ADVERB_FORM", "COMPARATIVE_FORM", "PARALLELISM",
    "CLAUSE_STRUCTURE", "FRAGMENT", "RUN_ON", "CONDITIONAL_STRUCTURE",
    "CONSTRUCTION", "QUANTIFIER_USAGE", "GRAMMAR_PUNCTUATION", "WORD_ORDER",
    # GRAMMATICAL_RANGE removed in v10 — it is a GRA metric signal, not a detectable error
}
LEXICAL_FAMILIES = {
    "SPELLING", "WORD_FORM", "COLLOCATION", "WORD_CHOICE", "REDUNDANCY",
    "REGISTER", "REPETITION", "LEXICAL_PRECISION", "SEMANTIC_COMBINATION",
}
CC_FAMILIES = {
    "TRANSITION", "MISSING_TRANSITION", "LOGICAL_PROGRESSION",
    "REFERENCE_COHESION", "PARAGRAPH_STRUCTURE", "TOPIC_CONTINUITY",
    "EXAMPLE_INTEGRATION", "TOPIC_SHIFT", "REFERENCE_BREAK", "CHAIN_BREAK",
    "MECHANICAL_COHESION",
}
TR_FAMILIES = {
    "PROMPT_COVERAGE", "PROMPT_RELEVANCE", "POSITION_RESPONSE",
    "TASK_COMPLETENESS", "OVERGENERALIZATION", "UNSUPPORTED_CLAIM",
    "WEAK_EXAMPLE", "CIRCULAR_REASONING", "OFF_TOPIC", "INCOMPLETE_ARGUMENT",
    "CLAIM_SUPPORT_LINK", "REASONING_CHAIN", "POSITION_CLARITY",
    "COUNTERARGUMENT_BALANCE", "GENRE_MISMATCH",
}
FAMILY_TO_RUBRIC = {
    **{f: "grammar" for f in GRAMMAR_FAMILIES},
    **{f: "lexical_resource" for f in LEXICAL_FAMILIES},
    **{f: "coherence_cohesion" for f in CC_FAMILIES},
    **{f: "task_response" for f in TR_FAMILIES},
}
RUBRIC_SHORT = {
    "grammar": "GRA",
    "lexical_resource": "LR",
    "coherence_cohesion": "CC",
    "task_response": "TR",
}
ISSUE_CODE_BY_FAMILY = {f: ("G_"+f if f in GRAMMAR_FAMILIES else "L_"+f if f in LEXICAL_FAMILIES else "C_"+f if f in CC_FAMILIES else "TR_"+f) for f in FAMILY_TO_RUBRIC}
ISSUE_CODE_BY_FAMILY.update({
    "NOUN_NUMBER_COUNTABILITY": "G_NOUN_NUMBER",
    "SUBJECT_VERB_AGREEMENT": "G_SV_AGREEMENT",
    "VERB_TENSE": "G_TENSE",
    "GRAMMAR_PUNCTUATION": "G_PUNCTUATION",
    "LEXICAL_PRECISION": "L_PRECISION",
})

# FIX 3: OPERATION_FAMILY_LOCKS completed with missing families
OPERATION_FAMILY_LOCKS: Dict[str, List[str]] = {
    "spelling_surface": ["SPELLING"],
    "word_form_derivation": ["WORD_FORM", "ADJECTIVE_ADVERB_FORM"],
    "lexical_selection": ["LEXICAL_PRECISION", "WORD_CHOICE", "REGISTER"],
    "semantic_combination": ["COLLOCATION", "SEMANTIC_COMBINATION", "WORD_CHOICE"],
    "collocation_frame": ["COLLOCATION", "SEMANTIC_COMBINATION"],
    "repetition_control": ["REPETITION", "REDUNDANCY"],
    "article_np_licensing": ["ARTICLE_DETERMINER"],
    "plural_countability": ["NOUN_NUMBER_COUNTABILITY", "QUANTIFIER_USAGE"],
    "sva_control": ["SUBJECT_VERB_AGREEMENT"],
    "finite_verb_control": ["VERB_FORM", "VERB_TENSE", "SUBJECT_VERB_AGREEMENT"],
    "verb_pattern": ["VERB_PATTERN", "CONSTRUCTION", "CLAUSE_STRUCTURE"],
    "preposition_governance": ["PREPOSITION_PATTERN"],
    "pronoun_role": ["POSSESSIVE_FORM", "PRONOUN_CASE", "PRONOUN_AGREEMENT"],
    "comparative_degree": ["COMPARATIVE_FORM", "PARALLELISM"],
    "clause_skeleton": ["CLAUSE_STRUCTURE", "CONSTRUCTION", "WORD_ORDER", "FRAGMENT"],
    "clause_boundary": ["RUN_ON", "FRAGMENT", "GRAMMAR_PUNCTUATION"],
    "punctuation_surface": ["GRAMMAR_PUNCTUATION", "RUN_ON", "FRAGMENT"],
    "paragraph_structure": ["PARAGRAPH_STRUCTURE"],
    "transition_use": ["TRANSITION", "MISSING_TRANSITION", "LOGICAL_PROGRESSION", "MECHANICAL_COHESION"],
    "reference_management": ["REFERENCE_COHESION", "REFERENCE_BREAK"],
    "example_integration": ["EXAMPLE_INTEGRATION", "WEAK_EXAMPLE", "CLAIM_SUPPORT_LINK"],
    "task_completeness": ["TASK_COMPLETENESS", "PROMPT_COVERAGE", "PROMPT_RELEVANCE", "GENRE_MISMATCH"],
    "position_clarity": ["POSITION_CLARITY", "POSITION_RESPONSE"],
    "support_depth": ["UNSUPPORTED_CLAIM", "WEAK_EXAMPLE", "REASONING_CHAIN"],
    "reasoning_chain": ["REASONING_CHAIN", "INCOMPLETE_ARGUMENT", "CLAIM_SUPPORT_LINK"],
    # FIX 3 additions (grammatical_range lock removed in v10):
    "quantifier_governance": ["QUANTIFIER_USAGE"],
    "topic_continuity": ["TOPIC_CONTINUITY", "TOPIC_SHIFT", "CHAIN_BREAK"],
    "mechanical_cohesion": ["MECHANICAL_COHESION"],
    "off_topic_genre": ["OFF_TOPIC", "GENRE_MISMATCH"],
    "overgeneralization": ["OVERGENERALIZATION"],
    "circular_reasoning": ["CIRCULAR_REASONING"],
}
FAMILY_DEFAULT_OPERATION: Dict[str, str] = {}
for op, fams in OPERATION_FAMILY_LOCKS.items():
    for f in fams:
        FAMILY_DEFAULT_OPERATION.setdefault(f, op)

TASK_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "opinion_agree_disagree": {"prompt_cues": ["to what extent", "agree or disagree", "what is your opinion"], "required_components": ["clear_position", "reasons_for_position", "support_or_example", "conclusion_alignment"], "optional_components": ["concession"], "position_requirement": "clear", "support_requirement": "reasons linked to position", "conclusion_requirement": "aligned"},
    "both_views_opinion": {"prompt_cues": ["discuss both views", "discuss both opinions", "discuss both viewpoints"], "required_components": ["view_1_covered", "view_2_covered", "own_opinion", "support_for_view_1", "support_for_view_2", "conclusion_alignment"], "optional_components": ["comparison"], "position_requirement": "own opinion explicit", "support_requirement": "both views supported", "conclusion_requirement": "aligned"},
    "advantages_disadvantages_outweigh": {"prompt_cues": ["advantages outweigh", "disadvantages outweigh"], "required_components": ["advantages_covered", "disadvantages_covered", "comparative_judgement", "advantages_supported", "disadvantages_supported", "conclusion_alignment"], "optional_components": ["relative_weighting"], "position_requirement": "outweigh judgement", "support_requirement": "both sides supported", "conclusion_requirement": "judgement aligned"},
    "advantages_disadvantages_neutral": {"prompt_cues": ["advantages and disadvantages", "advantages/disadvantages"], "required_components": ["advantages_covered", "disadvantages_covered", "advantages_supported", "disadvantages_supported", "summary_conclusion"], "optional_components": ["overall_judgement"], "position_requirement": "not always required", "support_requirement": "both sides supported", "conclusion_requirement": "summary"},
    "problems_solutions": {"prompt_cues": ["problems and solutions", "what problems", "how can they be solved", "what measures"], "required_components": ["problems_covered", "solutions_covered", "problem_solution_link", "feasibility_or_effectiveness", "conclusion_alignment"], "optional_components": ["responsible_agent"], "position_requirement": "selected problems/solutions", "support_requirement": "solutions address problems", "conclusion_requirement": "covers both"},
    "causes_solutions": {"prompt_cues": ["causes and solutions", "why is this happening", "what can be done", "how can this be addressed"], "required_components": ["causes_covered", "solutions_covered", "cause_solution_link", "conclusion_alignment"], "optional_components": ["most_effective_solution"], "position_requirement": "not stance-based", "support_requirement": "solutions address causes", "conclusion_requirement": "covers both"},
    "causes_effects": {"prompt_cues": ["why is this", "what are the effects", "reasons for this", "what effects"], "required_components": ["causes_covered", "effects_covered", "cause_effect_link", "conclusion_alignment"], "optional_components": ["evaluation"], "position_requirement": "not stance-based", "support_requirement": "causal mechanism", "conclusion_requirement": "covers both"},
    "positive_negative_development": {"prompt_cues": ["positive or negative development", "positive or negative trend"], "required_components": ["judgement", "reason_for_judgement", "impact_explanation", "support_or_example", "conclusion_alignment"], "optional_components": ["opposite_side"], "position_requirement": "positive/negative/mixed judgement", "support_requirement": "reasons and impact", "conclusion_requirement": "aligned"},
    "causes_positive_negative_development": {"prompt_cues": ["why is this happening", "positive or negative development"], "required_components": ["causes_or_reasons", "positive_negative_judgement", "justification_for_judgement", "support_or_examples", "conclusion_alignment"], "optional_components": ["solution_suggestion"], "position_requirement": "judgement required", "support_requirement": "why + judgement", "conclusion_requirement": "covers both"},
    "direct_two_question": {"prompt_cues": ["why", "what", "how"], "required_components": ["answer_question_1", "answer_question_2", "support_question_1", "support_question_2", "conclusion_covers_both"], "optional_components": ["priority"], "position_requirement": "answer both questions", "support_requirement": "support both answers", "conclusion_requirement": "covers both"},
    "prompt_missing_or_unsupported": {"prompt_cues": [], "required_components": [], "optional_components": [], "position_requirement": "not verified", "support_requirement": "not verified", "conclusion_requirement": "not verified"},
}

RESOURCE_MANIFEST = {
    "lexical_registry": "lexical_registry.json",
    "morphology_registry": "morphology_registry.json",
    "noun_governance_registry": "noun_governance_registry.json",
    "verb_governance_registry": "verb_governance_registry.json",
    "preposition_governance_registry": "preposition_governance_registry.tsv",
    "verb_complement_registry": "verb_complement_registry.tsv",
    "clause_frame_registry": "clause_frame_registry.json",
    "discourse_registry": "discourse_registry.json",
    "positive_collocations_registry": "positive_collocations_registry.tsv",
    "irregular_noun_registry": "irregular_noun_registry.json",
    "locale_variants": "locale_variants.tsv",
    "contractions": "contraction_full_forms.tsv",
}

TRANSITION_OPENERS = {"however", "therefore", "moreover", "furthermore", "in addition", "for example", "for instance", "on the other hand", "in conclusion", "as a result", "firstly", "secondly", "finally", "overall", "to conclude", "also", "even"}
MODALS = {"can", "could", "may", "might", "must", "should", "will", "would", "shall"}

# ---------------------------------------------------------------------------
# v9 Decision registry loading
# ---------------------------------------------------------------------------
def _candidate_registry_dirs(extra_dirs: Optional[Sequence[str]] = None) -> List[Path]:
    dirs: List[Path] = []
    if extra_dirs:
        dirs.extend(Path(x) for x in extra_dirs if str(x).strip())
    env = os.environ.get("VIP_DETECTOR_REGISTRY_DIRS", "")
    if env:
        parts = env.split(";") if ";" in env else env.split(os.pathsep)
        dirs.extend(Path(x) for x in parts if x.strip())
    if DEFAULT_V9_REGISTRY_DIR:
        dirs.append(Path(DEFAULT_V9_REGISTRY_DIR))
    dirs.extend([Path.cwd(), Path.cwd() / "registries", Path("/mnt/data")])
    seen, out = set(), []
    for d in dirs:
        sd = str(d)
        if sd not in seen:
            seen.add(sd); out.append(d)
    return out

# FIX 2: DecisionRegistries now has proper fields for rule_registry and task_schema_registry
@dataclass
class DecisionRegistries:
    registry_dirs_checked: List[str]
    family_lock_registry: Dict[str, Any]
    dominant_repair_scoring_guide: Dict[str, Any]
    suppression_taxonomy: Dict[str, Any]
    severity_framework: Dict[str, Any]
    mapping_by_operation: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    mapping_by_op_target: Dict[Tuple[str, str], Dict[str, Any]] = field(default_factory=dict)
    allowed_operations: List[str] = field(default_factory=list)
    audit: Dict[str, Any] = field(default_factory=dict)
    rule_registry: Dict[str, Any] = field(default_factory=dict)
    task_schema_registry: Dict[str, Any] = field(default_factory=dict)


def _v92_load_json_any(fname: str, dirs: List[Path]) -> Tuple[Dict[str, Any], Optional[Path], Optional[str]]:
    for d in dirs:
        p = d / fname
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8")), p, None
            except Exception as e:
                return {}, p, str(e)[:300]
    return {}, None, "missing"

def load_decision_registries(extra_dirs: Optional[Sequence[str]] = None) -> DecisionRegistries:
    dirs = _candidate_registry_dirs(extra_dirs)
    loaded: Dict[str, Any] = {}
    used: Dict[str, Any] = {}
    missing: List[str] = []
    for key, fname in V9_REGISTRY_FILES.items():
        data, path, err = _v92_load_json_any(fname, dirs)
        loaded[key] = data
        if err:
            if key == "family_lock_registry":
                data2, path2, err2 = _v92_load_json_any("family_lock_registry_v1.json", dirs)
                if not err2:
                    loaded[key] = data2; path = path2; err = None
            if err:
                missing.append(fname)
        used[key] = {"loaded": not bool(err), "path": str(path) if path else None, "error": err, "version": loaded.get(key, {}).get("version")}
    fl = loaded.get("family_lock_registry") or {}
    locks = fl.get("locks") or fl.get("mappings") or []
    by_op: Dict[str, Dict[str, Any]] = {}
    by_opt: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for m in locks:
        if not isinstance(m, dict):
            continue
        op = str(m.get("operation") or m.get("repair_operation") or "").upper().strip()
        target = str(m.get("repair_target") or "").lower().strip()
        fam = str(m.get("primary_family") or m.get("family") or "").upper().strip()
        rub = str(m.get("rubric") or FAMILY_TO_RUBRIC.get(fam, "unknown"))
        mm = dict(m)
        mm.setdefault("repair_operation", op)
        mm.setdefault("operation", op)
        mm.setdefault("family", fam)
        mm.setdefault("primary_family", fam)
        mm.setdefault("rubric", rub)
        mm.setdefault("family_lock_confidence", 1.0)
        if op:
            by_op.setdefault(op, mm)
            by_opt[(op, target)] = mm
    for opm in (loaded.get("repair_operation_registry", {}) or {}).get("operations", []) or []:
        if not isinstance(opm, dict):
            continue
        op = str(opm.get("operation") or "").upper().strip()
        target = str(opm.get("repair_target") or "").lower().strip()
        fam = str(opm.get("primary_family") or "").upper().strip()
        if op and fam:
            mm = {"operation": op, "repair_operation": op, "repair_target": target, "family": fam, "primary_family": fam, "rubric": opm.get("rubric") or FAMILY_TO_RUBRIC.get(fam, "unknown"), "family_lock_confidence": 1.0, "source": "repair_operation_registry_v2"}
            by_op.setdefault(op, mm); by_opt.setdefault((op, target), mm)
    audit = {
        "registry_dirs_checked": [str(d) for d in dirs],
        "registries_used": used,
        "missing": missing,
        "quality_status": "ready" if used.get("family_lock_registry", {}).get("loaded") and used.get("rule_registry", {}).get("loaded") and used.get("task_schema_registry", {}).get("loaded") else "degraded_decision_registry_loading",
        "policy": {
            "v9_3_registry_driven": True,
            "va25_python_import_removed": True,
            "family_source": "family_lock_registry_v2",
            "rule_source": "rule_registry_v1",
            "task_schema_source": "task_schema_registry_v1",
            "llm_assigns_final_family": False,
        },
    }
    # FIX 2: populate proper dataclass fields instead of setattr
    return DecisionRegistries(
        registry_dirs_checked=[str(d) for d in dirs],
        family_lock_registry=loaded.get("family_lock_registry") or {},
        dominant_repair_scoring_guide=loaded.get("dominant_repair_scoring_guide") or {},
        suppression_taxonomy=loaded.get("suppression_taxonomy") or {},
        severity_framework=loaded.get("severity_framework") or {},
        mapping_by_operation=by_op,
        mapping_by_op_target=by_opt,
        allowed_operations=sorted(by_op),
        audit=audit,
        rule_registry=loaded.get("rule_registry") or {},
        task_schema_registry=loaded.get("task_schema_registry") or {},
    )

# FIX 3: FAMILY_TO_DEFAULT_V9_OPERATION — all 57 families covered
FAMILY_TO_DEFAULT_V9_OPERATION = {
    "SPELLING": "FIX_SPELLING",
    "WORD_FORM": "FIX_WORD_FORM",
    "COLLOCATION": "REPLACE_COLLOCATION",
    "WORD_CHOICE": "REPLACE_WORD",
    "REDUNDANCY": "FIX_REDUNDANCY",
    "REGISTER": "FIX_REGISTER",
    "LEXICAL_PRECISION": "IMPROVE_PRECISION",
    "SEMANTIC_COMBINATION": "FIX_SEMANTIC_COMBINATION",
    "ARTICLE_DETERMINER": "REPLACE_ARTICLE",
    "NOUN_NUMBER_COUNTABILITY": "CHANGE_NOUN_FORM",
    "POSSESSIVE_FORM": "CHANGE_PRONOUN_FORM",
    "SUBJECT_VERB_AGREEMENT": "FIX_SVA",
    "VERB_TENSE": "CHANGE_VERB_FORM",
    "VERB_FORM": "CHANGE_VERB_FORM",
    "VERB_PATTERN": "CHANGE_VERB_PATTERN",
    "PREPOSITION_PATTERN": "REPLACE_PREPOSITION",
    "PRONOUN_CASE": "CHANGE_PRONOUN_FORM",
    "PRONOUN_AGREEMENT": "CHANGE_PRONOUN_FORM",
    "ADJECTIVE_ADVERB_FORM": "CHANGE_ADJECTIVE_FORM",
    "COMPARATIVE_FORM": "FIX_COMPARATIVE_FORM",
    "PARALLELISM": "FIX_PARALLELISM",
    "CLAUSE_STRUCTURE": "REWRITE_CLAUSE",
    "FRAGMENT": "RESTRUCTURE_SENTENCE",
    "RUN_ON": "RESTRUCTURE_SENTENCE",
    "CONDITIONAL_STRUCTURE": "FIX_CONDITIONAL_STRUCTURE",
    "CONSTRUCTION": "RESTRUCTURE_SENTENCE",
    "WORD_ORDER": "FIX_WORD_ORDER",
    "GRAMMAR_PUNCTUATION": "FIX_PUNCTUATION",
    "TRANSITION": "ADD_TRANSITION",
    "MISSING_TRANSITION": "ADD_TRANSITION",
    "LOGICAL_PROGRESSION": "REORDER_IDEAS",
    "REFERENCE_COHESION": "FIX_REFERENCE",
    "REFERENCE_BREAK": "FIX_REFERENCE",
    "PARAGRAPH_STRUCTURE": "RESTRUCTURE_PARAGRAPH",
    "EXAMPLE_INTEGRATION": "ADD_EXAMPLE",
    "WEAK_EXAMPLE": "ADD_EXAMPLE",
    "UNSUPPORTED_CLAIM": "ADD_SUPPORT",
    "INCOMPLETE_ARGUMENT": "ADD_EXPLANATION",
    "CLAIM_SUPPORT_LINK": "ADD_EXPLANATION",
    "POSITION_CLARITY": "ADD_POSITION",
    "PROMPT_COVERAGE": "ADD_TOPIC_DEVELOPMENT",
    "TASK_COMPLETENESS": "ADD_TOPIC_DEVELOPMENT",
    "COUNTERARGUMENT_BALANCE": "ADD_COUNTERARGUMENT",
    # FIX 3 additions (GRAMMATICAL_RANGE removed in v10):
    "REPETITION": "REDUCE_REPETITION",
    "QUANTIFIER_USAGE": "FIX_QUANTIFIER_USAGE",
    "TOPIC_CONTINUITY": "REORDER_IDEAS",
    "TOPIC_SHIFT": "REORDER_IDEAS",
    "CHAIN_BREAK": "FIX_REFERENCE",
    "MECHANICAL_COHESION": "FIX_TRANSITION",
    "CIRCULAR_REASONING": "ADD_EXPLANATION",
    "OFF_TOPIC": "ADD_TOPIC_DEVELOPMENT",
    "GENRE_MISMATCH": "ADD_TOPIC_DEVELOPMENT",
    "OVERGENERALIZATION": "ADD_SUPPORT",
    "POSITION_RESPONSE": "ADD_POSITION",
    "PROMPT_RELEVANCE": "ADD_TOPIC_DEVELOPMENT",
    "REASONING_CHAIN": "ADD_EXPLANATION",
}

LEGACY_OPERATION_TO_V9 = {
    "spelling_surface": "FIX_SPELLING",
    "word_form_derivation": "FIX_WORD_FORM",
    "lexical_selection": "REPLACE_WORD",
    "semantic_combination": "FIX_SEMANTIC_COMBINATION",
    "collocation_frame": "REPLACE_COLLOCATION",
    "repetition_control": "FIX_REDUNDANCY",
    "article_np_licensing": "REPLACE_ARTICLE",
    "plural_countability": "CHANGE_NOUN_FORM",
    "sva_control": "FIX_SVA",
    "finite_verb_control": "CHANGE_VERB_FORM",
    "verb_pattern": "CHANGE_VERB_PATTERN",
    "preposition_governance": "REPLACE_PREPOSITION",
    "pronoun_role": "CHANGE_PRONOUN_FORM",
    "comparative_degree": "FIX_COMPARATIVE_FORM",
    "clause_skeleton": "REWRITE_CLAUSE",
    "clause_boundary": "RESTRUCTURE_SENTENCE",
    "punctuation_surface": "FIX_PUNCTUATION",
    "paragraph_structure": "RESTRUCTURE_PARAGRAPH",
    "transition_use": "ADD_TRANSITION",
    "reference_management": "FIX_REFERENCE",
    "example_integration": "ADD_EXAMPLE",
    "task_completeness": "ADD_TOPIC_DEVELOPMENT",
    "position_clarity": "ADD_POSITION",
    "support_depth": "ADD_SUPPORT",
    "reasoning_chain": "ADD_EXPLANATION",
    "replace_word": "REPLACE_WORD",
    "replace_phrase": "REPLACE_WORD",
    "change_form": "FIX_WORD_FORM",
    "rewrite_clause": "REWRITE_CLAUSE",
    "restructure_paragraph": "RESTRUCTURE_PARAGRAPH",
    "add_support": "ADD_SUPPORT",
    "clarify_reference": "FIX_REFERENCE",
    "delete_word": "DELETE_WORD",
    "insert_word": "INSERT_WORD",
    "none": "UNKNOWN_REPAIR",
}

V92_OPERATION_ALIASES = {
    "spelling_surface": "FIX_SPELLING",
    "word_form_derivation": "FIX_WORD_FORM",
    "lexical_selection": "REPLACE_WORD_CHOICE",
    "semantic_combination": "FIX_SEMANTIC_COMBINATION",
    "collocation_frame": "FIX_COLLOCATION",
    "repetition_control": "REDUCE_REPETITION",
    "article_np_licensing": "REPLACE_ARTICLE",
    "plural_countability": "CHANGE_NOUN_NUMBER",
    "sva_control": "FIX_SVA",
    "finite_verb_control": "CHANGE_VERB_FORM",
    "modal_frame": "CHANGE_VERB_FORM",
    "infinitive_frame": "CHANGE_VERB_FORM",
    "verb_pattern": "FIX_VERB_PATTERN",
    "preposition_governance": "FIX_PREPOSITION_PATTERN",
    "pronoun_role": "FIX_POSSESSIVE_FORM",
    "comparative_degree": "FIX_COMPARATIVE_FORM",
    "conditional_frame": "FIX_CONDITIONAL_STRUCTURE",
    "clause_skeleton": "REWRITE_CLAUSE",
    "clause_boundary": "RESTRUCTURE_SENTENCE",
    "punctuation_surface": "FIX_PUNCTUATION",
    "paragraph_structure": "RESTRUCTURE_PARAGRAPH",
    "transition_use": "FIX_TRANSITION",
    "reference_management": "FIX_REFERENCE",
    "example_integration": "ADD_EXAMPLE",
    "task_completeness": "ADD_TOPIC_DEVELOPMENT",
    "position_clarity": "ADD_POSITION",
    "support_depth": "ADD_SUPPORT",
    "reasoning_chain": "ADD_EXPLANATION",
    "REPLACE_WORD": "REPLACE_WORD_CHOICE",
    "REPLACE_COLLOCATION": "FIX_COLLOCATION",
    "FIX_REDUNDANCY": "REMOVE_REDUNDANCY",
    "CHANGE_NOUN_FORM": "CHANGE_NOUN_NUMBER",
    "REPLACE_PREPOSITION": "FIX_PREPOSITION_PATTERN",
    "CHANGE_VERB_PATTERN": "FIX_VERB_PATTERN",
    "FIX_CONDITIONAL": "FIX_CONDITIONAL_STRUCTURE",
    "FIX_COMMA_TRANSITION": "FIX_PUNCTUATION",
    "FIX_GRAMMAR_PUNCTUATION": "FIX_PUNCTUATION",
}

FAMILY_TO_V92_OPERATION = {
    "SPELLING": "FIX_SPELLING",
    "WORD_FORM": "FIX_WORD_FORM",
    "WORD_CHOICE": "REPLACE_WORD_CHOICE",
    "LEXICAL_PRECISION": "IMPROVE_LEXICAL_PRECISION",
    "REGISTER": "FIX_REGISTER",
    "COLLOCATION": "FIX_COLLOCATION",
    "SEMANTIC_COMBINATION": "FIX_SEMANTIC_COMBINATION",
    "REDUNDANCY": "REMOVE_REDUNDANCY",
    "REPETITION": "REDUCE_REPETITION",
    "ARTICLE_DETERMINER": "REPLACE_ARTICLE",
    "NOUN_NUMBER_COUNTABILITY": "CHANGE_NOUN_NUMBER",
    "POSSESSIVE_FORM": "FIX_POSSESSIVE_FORM",
    "PRONOUN_CASE": "CHANGE_PRONOUN_CASE",
    "PRONOUN_AGREEMENT": "FIX_PRONOUN_AGREEMENT",
    "SUBJECT_VERB_AGREEMENT": "FIX_SVA",
    "VERB_FORM": "CHANGE_VERB_FORM",
    "VERB_TENSE": "CHANGE_VERB_TENSE",
    "VERB_PATTERN": "FIX_VERB_PATTERN",
    "PREPOSITION_PATTERN": "FIX_PREPOSITION_PATTERN",
    "ADJECTIVE_ADVERB_FORM": "FIX_ADJECTIVE_ADVERB_FORM",
    "COMPARATIVE_FORM": "FIX_COMPARATIVE_FORM",
    "PARALLELISM": "FIX_PARALLELISM",
    "CONDITIONAL_STRUCTURE": "FIX_CONDITIONAL_STRUCTURE",
    "CLAUSE_STRUCTURE": "REWRITE_CLAUSE",
    "CONSTRUCTION": "RESTRUCTURE_SENTENCE",
    "WORD_ORDER": "FIX_WORD_ORDER",
    "FRAGMENT": "FIX_FRAGMENT",
    "RUN_ON": "FIX_RUN_ON",
    "GRAMMAR_PUNCTUATION": "FIX_PUNCTUATION",
    # GRAMMATICAL_RANGE removed in v10
    "QUANTIFIER_USAGE": "FIX_QUANTIFIER_USAGE",
    "TRANSITION": "FIX_TRANSITION",
    "MISSING_TRANSITION": "ADD_TRANSITION",
    "LOGICAL_PROGRESSION": "REORDER_IDEAS",
    "REFERENCE_COHESION": "FIX_REFERENCE",
    "REFERENCE_BREAK": "FIX_REFERENCE",
    "PARAGRAPH_STRUCTURE": "RESTRUCTURE_PARAGRAPH",
    "TOPIC_CONTINUITY": "REORDER_IDEAS",
    "TOPIC_SHIFT": "REORDER_IDEAS",
    "CHAIN_BREAK": "FIX_REFERENCE",
    "MECHANICAL_COHESION": "FIX_TRANSITION",
    "EXAMPLE_INTEGRATION": "ADD_EXAMPLE",
    "TASK_COMPLETENESS": "ADD_TOPIC_DEVELOPMENT",
    "PROMPT_COVERAGE": "ADD_TOPIC_DEVELOPMENT",
    "PROMPT_RELEVANCE": "ADD_TOPIC_DEVELOPMENT",
    "POSITION_RESPONSE": "ADD_POSITION",
    "POSITION_CLARITY": "ADD_POSITION",
    "UNSUPPORTED_CLAIM": "ADD_SUPPORT",
    "OVERGENERALIZATION": "ADD_SUPPORT",
    "WEAK_EXAMPLE": "ADD_EXAMPLE",
    "INCOMPLETE_ARGUMENT": "ADD_EXPLANATION",
    "REASONING_CHAIN": "ADD_EXPLANATION",
    "CLAIM_SUPPORT_LINK": "ADD_EXPLANATION",
    "CIRCULAR_REASONING": "ADD_EXPLANATION",
    "OFF_TOPIC": "ADD_TOPIC_DEVELOPMENT",
    "GENRE_MISMATCH": "ADD_TOPIC_DEVELOPMENT",
    "COUNTERARGUMENT_BALANCE": "ADD_COUNTERARGUMENT",
}

def normalise_v9_operation(operation: str, family_hint: str = "") -> str:
    raw = str(operation or "").strip()
    if not raw or raw.lower() in {"none", "unknown", "unknown_repair", "null"}:
        return FAMILY_TO_V92_OPERATION.get(str(family_hint or "").upper(), "UNKNOWN_REPAIR")
    if raw in V92_OPERATION_ALIASES:
        return V92_OPERATION_ALIASES[raw]
    low = raw.lower()
    if low in V92_OPERATION_ALIASES:
        return V92_OPERATION_ALIASES[low]
    up = raw.upper()
    if up in V92_OPERATION_ALIASES:
        return V92_OPERATION_ALIASES[up]
    if up == "CHANGE_VERB_PATTERN": return "FIX_VERB_PATTERN"
    if up == "CHANGE_NOUN_FORM": return "CHANGE_NOUN_NUMBER"
    if up == "REPLACE_PREPOSITION": return "FIX_PREPOSITION_PATTERN"
    return up

def family_from_registry(operation: str, repair_target: str, registries: DecisionRegistries, fallback_family: str = "") -> Tuple[str, str, str, float, str]:
    op = normalise_v9_operation(operation, fallback_family)
    target = str(repair_target or "").lower().strip()
    m = registries.mapping_by_op_target.get((op, target)) or registries.mapping_by_operation.get(op)
    if m:
        fam = str(m.get("primary_family") or m.get("family") or fallback_family or "UNKNOWN_FAMILY").upper()
        rub = str(m.get("rubric") or FAMILY_TO_RUBRIC.get(fam, "unknown"))
        return fam, rub, RUBRIC_SHORT.get(rub, "X"), float(m.get("family_lock_confidence", 1.0) or 1.0), "family_lock_registry_v2"
    fam = str(fallback_family or "UNKNOWN_FAMILY").upper()
    return fam, FAMILY_TO_RUBRIC.get(fam, "unknown"), RUBRIC_SHORT.get(FAMILY_TO_RUBRIC.get(fam, "unknown"), "X"), 0.45 if fam in FAMILY_TO_RUBRIC else 0.0, "fallback_family_no_v2_lock"

def dominant_score(rec_gain: Any, eval_gain: Any, clarity_gain: Any) -> float:
    return round(0.50 * clamp(safe_float(rec_gain, 0.0)) + 0.30 * clamp(safe_float(eval_gain, 0.0)) + 0.20 * clamp(safe_float(clarity_gain, 0.0)), 4)

def severity_from_score(score: float, severity_registry: Dict[str, Any]) -> str:
    levels = severity_registry.get("levels") if isinstance(severity_registry, dict) else None
    if isinstance(levels, list):
        for lv in levels:
            try:
                lo, hi = float(lv.get("min", 0)), float(lv.get("max", 1))
                if lo <= score <= hi:
                    return str(lv.get("label") or lv.get("severity") or "moderate").lower()
            except Exception:
                continue
    if score >= 0.80: return "critical"
    if score >= 0.60: return "major"
    if score >= 0.30: return "moderate"
    return "minor"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class ResourceBundle:
    manifest: Dict[str, str]
    dirs_checked: List[str]
    resources: Dict[str, Any] = field(default_factory=dict)
    audit: Dict[str, Any] = field(default_factory=dict)
    valid_words: set = field(default_factory=set)
    locale_variants: set = field(default_factory=set)
    count_nouns: set = field(default_factory=set)
    mass_nouns: set = field(default_factory=set)
    irregular_singular_by_plural: Dict[str, str] = field(default_factory=dict)
    positive_collocations: set = field(default_factory=set)
    form_to_lemma: Dict[str, str] = field(default_factory=dict)
    collocation_index: Dict[str, List[str]] = field(default_factory=dict)

@dataclass
class Candidate:
    candidate_id: str
    run_id: str
    submission_id: str
    essay_id: str
    layer: str
    stage: str
    source_engine: str
    quote: str
    local_quote: str
    span_start: int
    span_end: int
    sentence_id: Optional[str]
    paragraph_id: Optional[str]
    sentence_index: Optional[int]
    paragraph_index: Optional[int]
    rubric_candidate: str
    family_candidate: str
    operation: str
    problem_axis: str
    problem_statement: str
    explanation: str
    confidence: float
    repair_operation: str = "UNKNOWN_REPAIR"
    repair_target: str = "unspecified"
    repair_hypothesis: str = ""
    recoverability_gain: float = 0.0
    evaluability_gain: float = 0.0
    clarity_gain: float = 0.0
    dominant_repair_score: float = 0.0
    family_lock_status: str = "not_locked"
    family_lock_confidence: float = 0.0
    raw_evidence: Dict[str, Any] = field(default_factory=dict)
    root_or_secondary: str = "root"
    secondary_families: List[str] = field(default_factory=list)
    model_used: Optional[str] = None

@dataclass
class DiagnosticRow:
    row_id: str
    candidate_id: Optional[str]
    run_id: str
    submission_id: str
    essay_id: str
    layer: str
    operation: str
    rubric: str
    family: str
    issue_code: str
    quote: str
    local_quote: str
    span_start: int
    span_end: int
    sentence_id: Optional[str]
    paragraph_id: Optional[str]
    sentence_index: Optional[int]
    paragraph_index: Optional[int]
    root_or_secondary: str
    secondary_families: List[str]
    problem_statement: str
    explanation: str
    repair_operation: str
    repair_target: str
    repair_hypothesis: str
    recoverability_gain: float
    evaluability_gain: float
    clarity_gain: float
    dominant_repair_score: float
    family_lock_status: str
    family_lock_confidence: float
    severity: str
    chargeable_for_scoring: bool
    score_charge_weight: float
    student_visible: bool
    arbitration_status: str
    arbitration_reasons: List[str]
    qa_flags: List[str]
    confidence: float
    source_engines: List[str]
    model_used: Optional[str] = None
    repair_materialisation: Dict[str, Any] = field(default_factory=dict)
    dependent_symptom_rows: List[str] = field(default_factory=list)

class LLMTracker:
    def __init__(self) -> None:
        self.by_model: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"calls_attempted":0,"calls_succeeded":0,"calls_failed":0,"empty_json":0,"input_tokens_est":0,"output_tokens_est":0,"estimated_cost_usd":0.0,"latencies_ms":[],"tags":Counter(),"disabled_after_failures":False})
        self.strong_failures = 0
        self._lock = threading.Lock()  # v17: thread-safe for parallel passes
    def record(self, model: str, tag: str, ok: bool, prompt: str, output: str, ms: float, empty_json: bool=False) -> None:
        with self._lock:
            d = self.by_model[model]
            d["calls_attempted"] += 1; d["tags"][tag] += 1
            if ok: d["calls_succeeded"] += 1
            else: d["calls_failed"] += 1
            if empty_json: d["empty_json"] += 1
            d["input_tokens_est"] += max(1, len(prompt)//4)
            d["output_tokens_est"] += max(0, len(output)//4)
            d["latencies_ms"].append(round(ms,2))
            d["estimated_cost_usd"] += (len(prompt)//4)*0.00000015 + (len(output)//4)*0.0000006
    def asdict(self) -> Dict[str, Any]:
        out = {}
        for model, d in self.by_model.items():
            lat = d["latencies_ms"]
            out[model] = {k: (dict(v) if isinstance(v, Counter) else round(v,6) if isinstance(v,float) else v) for k,v in d.items() if k != "latencies_ms"}
            out[model]["mean_latency_ms"] = round(sum(lat)/len(lat),2) if lat else 0
        return out

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    return f"{prefix}_{hashlib.sha1(raw.encode('utf-8', errors='ignore')).hexdigest()[:16]}"

def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()

def words(text: str) -> List[str]:
    return re.findall(r"[A-Za-z][A-Za-z'\-]*", text or "")

def clamp(x: float, lo: float=0.0, hi: float=1.0) -> float:
    return max(lo, min(hi, x))

def safe_float(x: Any, default: float=0.0) -> float:
    try: return float(x)
    except Exception: return default

def edit_distance_limited(a: str, b: str, limit: int = 2) -> int:
    a = (a or "").lower()
    b = (b or "").lower()
    if a == b:
        return 0
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        row_min = current[0]
        for j, cb in enumerate(b, 1):
            insert_cost = current[j - 1] + 1
            delete_cost = previous[j] + 1
            replace_cost = previous[j - 1] + (ca != cb)
            val = min(insert_cost, delete_cost, replace_cost)
            current.append(val)
            if val < row_min:
                row_min = val
        if row_min > limit:
            return limit + 1
        previous = current
    return previous[-1] if previous[-1] <= limit else limit + 1

def paragraph_split(text: str) -> List[Tuple[str,int,int]]:
    paras = []
    for m in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|$)", text or "", flags=re.S):
        para = m.group(0).strip()
        if para:
            paras.append((para, m.start(), m.end()))
    if not paras and text.strip():
        paras.append((text.strip(), 0, len(text)))
    return paras

def sentence_split_with_spans(text: str, base: int=0) -> List[Tuple[str,int,int]]:
    out = []
    for m in re.finditer(r"[^.!?]+(?:[.!?]+|$)", text, flags=re.S):
        raw = m.group(0)
        s = raw.strip()
        if not s: continue
        start = base + m.start() + (len(raw)-len(raw.lstrip()))
        end = base + m.end()
        out.append((s, start, end))
    return out

def segment_essay(essay_id: str, text: str) -> Dict[str, Any]:
    paragraphs = []
    sentences = []
    for pi, (para, ps, pe) in enumerate(paragraph_split(text), start=1):
        pid = stable_id("p", essay_id, pi, ps, pe)
        role = "introduction" if pi == 1 else "conclusion" if pi > 1 and pe >= len(text)-5 else "body"
        paragraphs.append({"paragraph_id": pid, "paragraph_index": pi, "char_start": ps, "char_end": pe, "text": para, "role": role})
        for sent_text, ss, se in sentence_split_with_spans(para, ps):
            si = len(sentences)+1
            sid = stable_id("s", essay_id, si, ss, se)
            sentences.append({"sentence_id": sid, "paragraph_id": pid, "sentence_index": si, "global_sentence_index": si, "paragraph_index": pi, "char_start": ss, "char_end": se, "text": sent_text})
    return {"segmentation_id": stable_id("seg", essay_id, len(text), len(sentences)), "paragraphs": paragraphs, "sentences": sentences}

def expand_to_token(text: str, start: int, end: int) -> Tuple[str,int,int]:
    start = max(0, min(len(text), start)); end = max(start, min(len(text), end))
    if start < len(text) and (end-start <= 1 or text[start:end].strip() == ""):
        if start < len(text) and text[start:start+1].isspace() and start+1 < len(text) and text[start+1] in ",.;:!?":
            return text[start:start+2], start, start+2
        left = start
        while left > 0 and re.match(r"[A-Za-z'\-]", text[left-1]): left -= 1
        right = max(end, start+1)
        while right < len(text) and re.match(r"[A-Za-z'\-]", text[right]): right += 1
        if right > left:
            return text[left:right], left, right
    return text[start:end], start, end

def quote_overlap(a: str, b: str) -> bool:
    la, lb = normalize_space(a).lower(), normalize_space(b).lower()
    return bool(la and lb and (la in lb or lb in la or len(set(words(la)) & set(words(lb))) >= 2))

# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------
def _split_resource_env(value: str) -> List[str]:
    if not value:
        return []
    if ";" in value:
        return [p.strip() for p in value.split(";") if p.strip()]
    return [p.strip() for p in value.split(os.pathsep) if p.strip()]

def _candidate_resource_dirs(resource_dirs: Optional[Sequence[str]]=None) -> List[Path]:
    dirs: List[Path] = []
    if resource_dirs:
        dirs.extend(Path(d) for d in resource_dirs if str(d).strip())
    for env_name in ["VA_CANONICAL_RESOURCE_DIR", "VIP_CANONICAL_RESOURCE_DIR", "VA_RESOURCE_DIRS", "VIP_RESOURCE_DIRS"]:
        dirs.extend(Path(p) for p in _split_resource_env(os.environ.get(env_name, "")))
    if DEFAULT_CANONICAL_RESOURCE_DIR:
        dirs.append(Path(DEFAULT_CANONICAL_RESOURCE_DIR))
    expanded: List[Path] = []
    for d in dirs:
        expanded.append(d)
        expanded.append(d / "final_app_registries_v3_CONSOLIDATED_CANONICAL")
        expanded.append(d / "resources")
        if d.name == "final_app_registries_v3_CONSOLIDATED_CANONICAL":
            expanded.append(d.parent)
    # v11 Fix A: also search the registry dir's resources/ subfolder
    if DEFAULT_V9_REGISTRY_DIR:
        expanded.append(Path(DEFAULT_V9_REGISTRY_DIR) / "resources")
        expanded.append(Path(DEFAULT_V9_REGISTRY_DIR))
    expanded.extend([Path.cwd()/"resources", Path.cwd(), Path("/mnt/data/resources"), Path("/mnt/data")])
    seen, out = set(), []
    for d in expanded:
        s = str(d)
        if s not in seen:
            seen.add(s); out.append(d)
    return out

def _load_tsv(path: Path) -> List[Dict[str,str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        sample = f.read(4096); f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters="\t,") if sample.strip() else csv.excel_tab
        return list(csv.DictReader(f, dialect=dialect))

def _iter_strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k,v in obj.items():
            if isinstance(k, str): yield k
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for x in obj: yield from _iter_strings(x)

def _manifest_from_dirs(dirs: List[Path]) -> Dict[str, str]:
    manifest = dict(RESOURCE_MANIFEST)
    for d in dirs:
        mp = d / "FINAL_REGISTRY_MANIFEST.json"
        if not mp.exists():
            continue
        try:
            data = json.loads(mp.read_text(encoding="utf-8"))
            core = data.get("core_files", {}) if isinstance(data, dict) else {}
            if isinstance(core, dict):
                manifest.update({str(k): str(v) for k, v in core.items() if isinstance(v, str)})
                manifest["__manifest_file__"] = str(mp)
                manifest["__manifest_version__"] = str(data.get("version", ""))
                break
        except Exception:
            continue
    return manifest

def load_resources(resource_dirs: Optional[Sequence[str]]=None) -> ResourceBundle:
    dirs = _candidate_resource_dirs(resource_dirs)
    manifest = _manifest_from_dirs(dirs)
    runtime_manifest = {k:v for k,v in manifest.items() if not k.startswith("__")}
    rb = ResourceBundle(dict(manifest), [str(d) for d in dirs])
    used, missing = {}, []
    for key, fname in runtime_manifest.items():
        found = None
        for d in dirs:
            p = d / fname
            if p.exists(): found = p; break
        if not found:
            used[key] = {"loaded": False, "count": 0, "path": None}; missing.append(fname); continue
        try:
            if found.suffix.lower() == ".tsv": data = _load_tsv(found)
            else: data = json.loads(found.read_text(encoding="utf-8"))
            rb.resources[key] = data
            used[key] = {"loaded": True, "count": len(data) if hasattr(data, "__len__") else 1, "path": str(found)}
        except Exception as e:
            used[key] = {"loaded": False, "count": 0, "path": str(found), "error": str(e)}; missing.append(fname)
    for key in ["lexical_registry", "morphology_registry", "noun_governance_registry", "irregular_noun_registry"]:
        for s in _iter_strings(rb.resources.get(key)):
            if re.fullmatch(r"[A-Za-z][A-Za-z'\-]{1,40}", s): rb.valid_words.add(s.lower())
    loc = rb.resources.get("locale_variants")
    if isinstance(loc, list):
        for row in loc:
            if isinstance(row, dict):
                for v in row.values():
                    if isinstance(v, str) and re.fullmatch(r"[A-Za-z][A-Za-z'\-]+", v): rb.locale_variants.add(v.lower())
    rb.locale_variants.update({"ageing", "aging", "colour", "color", "labour", "labor", "centre", "center", "neighbour", "neighbor"})
    pc = rb.resources.get("positive_collocations_registry")
    if isinstance(pc, list):
        for row in pc:
            if isinstance(row, dict):
                phrase = normalize_space(" ".join(str(v) for v in row.values() if isinstance(v, str)).lower())
                if phrase: rb.positive_collocations.add(phrase)
    noun = rb.resources.get("noun_governance_registry")
    if isinstance(noun, dict):
        for n, meta in noun.items():
            nn = str(n).lower()
            if isinstance(meta, dict):
                cls = str(meta.get("countability") or meta.get("type") or meta.get("class") or "").lower()
                if "mass" in cls or "uncount" in cls: rb.mass_nouns.add(nn)
                if "count" in cls or meta.get("countable") is True: rb.count_nouns.add(nn)
    irr = rb.resources.get("irregular_noun_registry")
    if isinstance(irr, dict):
        for singular, plural in irr.items():
            if isinstance(plural, str): rb.irregular_singular_by_plural[plural.lower()] = str(singular).lower()
            elif isinstance(plural, list):
                for p in plural: rb.irregular_singular_by_plural[str(p).lower()] = str(singular).lower()
    major = ["lexical_registry","morphology_registry","noun_governance_registry","verb_governance_registry","preposition_governance_registry","verb_complement_registry","clause_frame_registry","discourse_registry","irregular_noun_registry"]
    loaded_major = sum(1 for k in major if used.get(k,{}).get("loaded"))

    # Build form_to_lemma from morphology_registry
    morph = rb.resources.get("morphology_registry")
    if isinstance(morph, list):
        for entry in morph:
            if not isinstance(entry, dict):
                continue
            lemma = str(entry.get("lemma") or "").lower().strip()
            for form in (entry.get("all_forms") or []):
                if isinstance(form, str) and form.strip():
                    rb.form_to_lemma[form.lower().strip()] = lemma

    # Build collocation_index from positive_collocations_registry
    pc_data = rb.resources.get("positive_collocations_registry")
    if isinstance(pc_data, list):
        for row in pc_data:
            if not isinstance(row, dict):
                continue
            hw = str(row.get("headword") or "").lower().strip()
            col = str(row.get("collocate") or "").lower().strip()
            if hw and col:
                rb.collocation_index.setdefault(hw, [])
                if col not in rb.collocation_index[hw]:
                    rb.collocation_index[hw].append(col)

    rb.audit = {"manifest": manifest, "resource_dirs_checked": [str(d) for d in dirs], "resources_used": used, "missing": missing, "resource_ready": loaded_major >= max(3, len(major)//2), "quality_status": "ready" if loaded_major >= max(3, len(major)//2) else "degraded_resource_loading", "derived": {"valid_words_count": len(rb.valid_words), "locale_variants": len(rb.locale_variants), "positive_collocations": len(rb.positive_collocations), "count_nouns": len(rb.count_nouns), "mass_nouns": len(rb.mass_nouns), "form_to_lemma_count": len(rb.form_to_lemma), "collocation_index_headwords": len(rb.collocation_index)}, "policy": {"resources_are_universal_only": True, "absence_from_positive_collocations_is_never_error": True}}
    return rb

# ---------------------------------------------------------------------------
# External engines
# ---------------------------------------------------------------------------
_SPACY_NLP = None; _SPACY_STATUS = "not_initialized"; _SPACY_ERROR = ""
_LT_TOOL = None; _LT_STATUS = "not_initialized"; _LT_ERROR = ""
_OPENAI_CLIENT = None; _OPENAI_STATUS = "not_initialized"; _OPENAI_ERROR = ""

def get_spacy():
    global _SPACY_NLP, _SPACY_STATUS, _SPACY_ERROR
    if _SPACY_NLP is not None or _SPACY_STATUS in {"failed", "unavailable"}: return _SPACY_NLP
    if spacy is None:
        _SPACY_STATUS = "unavailable"; _SPACY_ERROR = "spacy not installed"; return None
    try:
        _SPACY_NLP = spacy.load(SPACY_MODEL); _SPACY_STATUS = SPACY_MODEL
    except Exception as e:
        try:
            _SPACY_NLP = spacy.blank("en"); _SPACY_NLP.add_pipe("sentencizer"); _SPACY_STATUS = "blank_en_sentencizer"; _SPACY_ERROR = f"{SPACY_MODEL} unavailable: {e}"
        except Exception as e2:
            _SPACY_STATUS = "failed"; _SPACY_ERROR = str(e2); _SPACY_NLP = None
    return _SPACY_NLP

def get_lt():
    global _LT_TOOL, _LT_STATUS, _LT_ERROR
    if _LT_TOOL is not None or _LT_STATUS in {"failed", "unavailable"}: return _LT_TOOL
    if language_tool_python is None:
        _LT_STATUS = "unavailable"; _LT_ERROR = "language_tool_python not installed"; return None
    try:
        _LT_TOOL = language_tool_python.LanguageTool(LT_LANGUAGE); _LT_STATUS = LT_LANGUAGE
    except Exception as e:
        _LT_STATUS = "failed"; _LT_ERROR = str(e); _LT_TOOL = None
    return _LT_TOOL

def get_openai_client():
    global _OPENAI_CLIENT, _OPENAI_STATUS, _OPENAI_ERROR
    if _OPENAI_CLIENT is not None or _OPENAI_STATUS in {"failed", "unavailable"}: return _OPENAI_CLIENT
    if not OPENAI_API_KEY:
        _OPENAI_STATUS = "unavailable"; _OPENAI_ERROR = "OPENAI_API_KEY missing"; return None
    try:
        from openai import OpenAI  # type: ignore
        _OPENAI_CLIENT = OpenAI(api_key=OPENAI_API_KEY); _OPENAI_STATUS = "enabled"
    except Exception as e:
        _OPENAI_STATUS = "failed"; _OPENAI_ERROR = str(e)
    return _OPENAI_CLIENT

def extract_json(text: str) -> Any:
    if not text: return None
    t = re.sub(r"^```(?:json)?\s*", "", text.strip()); t = re.sub(r"\s*```$", "", t)
    try: return json.loads(t)
    except Exception:
        m = re.search(r"(\{.*\}|\[.*\])", t, flags=re.S)
        if m:
            try: return json.loads(m.group(1))
            except Exception: return None
    return None

def llm_json(prompt: str, system: str, model: str, tag: str, tracker: LLMTracker, enabled: bool=True, max_tokens: int=1000) -> Any:
    if not enabled:
        tracker.record(model, tag, False, prompt, "", 0, empty_json=True); return None
    if model == STRONG_MODEL and tracker.strong_failures >= 2:
        tracker.by_model[model]["disabled_after_failures"] = True
        model = CHEAP_MODEL
    client = get_openai_client()
    if client is None:
        tracker.record(model, tag, False, prompt, _OPENAI_ERROR, 0, empty_json=True); return None
    t0 = time.perf_counter(); content = ""
    try:
        resp = client.chat.completions.create(model=model, temperature=0, max_tokens=max_tokens, messages=[{"role":"system","content":system}, {"role":"user","content":prompt}])
        content = resp.choices[0].message.content or ""
        data = extract_json(content); ok = data is not None
        if not ok and model == STRONG_MODEL: tracker.strong_failures += 1
        tracker.record(model, tag, ok, prompt, content, (time.perf_counter()-t0)*1000, empty_json=not ok)
        return data
    except Exception as e:
        if model == STRONG_MODEL: tracker.strong_failures += 1
        tracker.record(model, tag, False, prompt, str(e), (time.perf_counter()-t0)*1000, empty_json=True)
        return None


# ---------------------------------------------------------------------------
# Candidate helpers
# ---------------------------------------------------------------------------
def make_candidate(run_id: str, submission_id: str, essay_id: str, layer: str, source: str, quote: str, local_quote: str, span_start: int, span_end: int, sent: Optional[Dict[str, Any]], family: str, operation: Optional[str], problem: str, explanation: str, confidence: float, evidence: Optional[Dict[str,Any]]=None, model_used: Optional[str]=None, repair_operation: str="UNKNOWN_REPAIR", repair_hypothesis: str="", root_or_secondary: str="root", secondary_families: Optional[List[str]]=None, repair_target: str="unspecified", recoverability_gain: float=0.0, evaluability_gain: float=0.0, clarity_gain: float=0.0) -> Candidate:
    family = str(family or "UNKNOWN_FAMILY").upper().strip()
    op = normalise_v9_operation(repair_operation or operation or FAMILY_DEFAULT_OPERATION.get(family, "UNKNOWN_REPAIR"), family)
    operation = op
    rubric = FAMILY_TO_RUBRIC.get(family, "unknown")
    quote = normalize_space(quote) if str(quote).strip() else str(quote or "")
    rec = clamp(safe_float(recoverability_gain, 0.0)); ev = clamp(safe_float(evaluability_gain, 0.0)); cl = clamp(safe_float(clarity_gain, 0.0))
    ds = dominant_score(rec, ev, cl)
    cid = stable_id("cand", run_id, essay_id, layer, source, sent.get("sentence_index") if sent else None, span_start, span_end, family, op, quote, explanation[:40])
    return Candidate(cid, run_id, submission_id, essay_id, layer, "detection", source, quote, local_quote or quote, int(span_start), int(span_end), sent.get("sentence_id") if sent else None, sent.get("paragraph_id") if sent else None, sent.get("sentence_index") if sent else None, sent.get("paragraph_index") if sent else None, rubric, family, operation, operation, problem, explanation, clamp(confidence), op, repair_target or "unspecified", repair_hypothesis, rec, ev, cl, ds, "not_locked", 0.0, evidence or {}, root_or_secondary, secondary_families or [], model_used)

def row_from_candidate(c: Candidate, status: str, reasons: List[str], qa_flags: Optional[List[str]]=None, chargeable: bool=False, severity_registry: Optional[Dict[str, Any]]=None) -> DiagnosticRow:
    fam = c.family_candidate
    sev = severity_from_score(c.dominant_repair_score, severity_registry or {})
    return DiagnosticRow(
        row_id=stable_id("row", c.candidate_id, status, fam), candidate_id=c.candidate_id, run_id=c.run_id, submission_id=c.submission_id, essay_id=c.essay_id, layer=c.layer, operation=c.operation, rubric=FAMILY_TO_RUBRIC.get(fam, c.rubric_candidate), family=fam, issue_code=ISSUE_CODE_BY_FAMILY.get(fam, fam), quote=c.quote, local_quote=c.local_quote, span_start=c.span_start, span_end=c.span_end, sentence_id=c.sentence_id, paragraph_id=c.paragraph_id, sentence_index=c.sentence_index, paragraph_index=c.paragraph_index, root_or_secondary=c.root_or_secondary, secondary_families=c.secondary_families, problem_statement=c.problem_statement, explanation=c.explanation, repair_operation=c.repair_operation, repair_target=c.repair_target, repair_hypothesis=c.repair_hypothesis, recoverability_gain=c.recoverability_gain, evaluability_gain=c.evaluability_gain, clarity_gain=c.clarity_gain, dominant_repair_score=c.dominant_repair_score, family_lock_status=c.family_lock_status, family_lock_confidence=c.family_lock_confidence, severity=sev, chargeable_for_scoring=chargeable and status == "accepted", score_charge_weight=1.0 if chargeable and status == "accepted" else 0.0, student_visible=chargeable and status == "accepted", arbitration_status=status, arbitration_reasons=reasons, qa_flags=qa_flags or [], confidence=c.confidence, source_engines=[c.source_engine], model_used=c.model_used)

# ---------------------------------------------------------------------------
# Layer 0 / 0.5 — LLM-active
# ---------------------------------------------------------------------------
# FIX 6: infer_task_schema — single authoritative version with hard_fail_if_missing wired
def _v92_task_schema_registry() -> Dict[str, Any]:
    reg = load_decision_registries()
    return reg.task_schema_registry or {}

def infer_task_schema(prompt_text: str, essay_text: str, tracker: LLMTracker, llm_enabled: bool) -> Dict[str, Any]:
    registry = _v92_task_schema_registry()
    schemas = registry.get("schemas") or TASK_SCHEMAS
    p = (prompt_text or "").lower()
    if not p.strip():
        # Fallback: infer task type from essay text when prompt is missing
        e = essay_text.lower()
        inferred = None
        inferred_conf = 0.0

        # Strong essay-text signals
        if re.search(r'\b(advantage|disadvantage|benefit|drawback|outweigh)\b', e) and \
           re.search(r'\b(on the other hand|however|while|whereas)\b', e):
            inferred = "advantages_disadvantages_neutral"
            inferred_conf = 0.55
        elif re.search(r'\b(i (think|believe|agree|disagree)|in my (opinion|view)|i (would )?argue)\b', e):
            inferred = "opinion_agree_disagree"
            inferred_conf = 0.60
        elif re.search(r'\b(some people|others (think|believe|argue)|on one hand|on the other hand)\b', e) and \
             re.search(r'\b(i (think|believe|feel|consider))\b', e):
            inferred = "both_views_opinion"
            inferred_conf = 0.55
        elif re.search(r'\b(cause|reason|because|due to)\b', e) and \
             re.search(r'\b(solution|solve|measure|address|can be done)\b', e):
            inferred = "causes_solutions"
            inferred_conf = 0.55
        elif re.search(r'\b(cause|reason|because)\b', e) and \
             re.search(r'\b(effect|impact|result|consequence)\b', e):
            inferred = "causes_effects"
            inferred_conf = 0.55
        elif re.search(r'\b(problem|challenge|issue)\b', e) and \
             re.search(r'\b(solution|solve|address|measure)\b', e):
            inferred = "problems_solutions"
            inferred_conf = 0.50

        if inferred and llm_enabled:
            # Confirm with LLM using essay text
            data_fb = llm_json(
                f"Classify this IELTS Task 2 essay (no prompt available). Essay excerpt:\n{essay_text[:600]}\n"
                f"Return JSON {{\"task_type\": one of {list(schemas)}, \"confidence\":0-1}}",
                "Classify IELTS task type from essay text only. JSON only.",
                CHEAP_MODEL, "L0_task_schema_fallback", tracker, llm_enabled, 300
            )
            if isinstance(data_fb, dict) and data_fb.get("task_type") in schemas:
                inferred = data_fb.get("task_type")
                inferred_conf = max(inferred_conf, safe_float(data_fb.get("confidence"), inferred_conf))

        if inferred:
            schema_fb = schemas[inferred]
            return {
                "task_profile_id": stable_id("task", "essay_fallback", essay_text[:80]),
                "prompt_id": None,
                "task_type": inferred,
                "task_type_source": "essay_text_fallback_no_prompt",
                "task_type_confidence": inferred_conf,
                "task_schema_id": inferred,
                "required_components": schema_fb.get("required_components", []),
                "optional_components": schema_fb.get("optional_components", []),
                "prompt_part_hits": {},
                "missing_required_components": schema_fb.get("required_components", []),
                "hard_fail_missing_components": [],
                "task_completeness_confidence": 0.0,
                "task_schema_gate_reasons": ["prompt_missing_inferred_from_essay"],
                "score_ready": False,
                "task_registry_version": registry.get("version"),
            }

        # True fallback: no inference possible
        return {"task_profile_id": stable_id("task", "missing"), "prompt_id": None, "task_type": "prompt_missing_or_unsupported", "task_type_source": "prompt_missing_or_unsupported", "task_type_confidence": 0.0, "task_schema_id": "prompt_missing_or_unsupported", "required_components": [], "optional_components": [], "prompt_part_hits": {}, "missing_required_components": [], "task_schema_gate_reasons": ["prompt_missing"], "score_ready": False, "hard_fail_missing_components": [], "task_completeness_confidence": 0.0, "task_registry_version": registry.get("version")}
    best, score = None, 0
    for name, schema in schemas.items():
        if name in ("prompt_missing_or_unsupported", "prompt_missing_unknown"): continue
        cues = schema.get("prompt_cues", []) or []
        s = sum(2 if cue in p else 0 for cue in cues)
        if name == "both_views_opinion" and "both" in p and "opinion" in p: s += 2
        if name == "advantages_disadvantages_outweigh" and "advantage" in p and "disadvantage" in p and "outweigh" in p: s += 3
        if name == "causes_solutions" and re.search(r"\b(cause|reason|why|problem)s?\b", p) and re.search(r"\b(solution|measure|what can be done|address)\b", p): s += 3
        if name == "causes_effects" and re.search(r"\b(cause|reason|why)s?\b", p) and re.search(r"\b(effect|impact|consequence)s?\b", p): s += 3
        if name == "opinion_agree_disagree" and ("agree or disagree" in p or "to what extent" in p): s += 3
        if name == "direct_two_question" and prompt_text.count("?") >= 2: s += 3
        if s > score:
            best, score = name, s
    if not best:
        data = llm_json(f"Classify this IELTS Writing Task 2 prompt. Prompt:\n{prompt_text}\nReturn JSON {{\"task_type\": one of {list(schemas)}, \"confidence\":0-1}}", "Classify prompt from prompt only. JSON only.", CHEAP_MODEL, "L0_task_schema", tracker, llm_enabled, 300)
        if isinstance(data, dict) and data.get("task_type") in schemas:
            best = str(data.get("task_type")); score = max(1, int(10*safe_float(data.get("confidence"),0.5)))
    if not best:
        best = "opinion_agree_disagree"
    schema = schemas[best]
    comp_hits = {c: False for c in schema.get("required_components", [])}
    e = essay_text.lower()
    cue_map = schema.get("component_detection_cues", {}) if isinstance(schema, dict) else {}
    for comp in comp_hits:
        cues = cue_map.get(comp, [])
        if cues:
            comp_hits[comp] = any(str(cue).lower() in e for cue in cues)
        elif "advantage" in comp: comp_hits[comp] = bool(re.search(r"\b(advantage|benefit|positive)\b", e))
        elif "disadvantage" in comp or "problem" in comp: comp_hits[comp] = bool(re.search(r"\b(disadvantage|drawback|problem|negative|challenge)\b", e))
        elif "solution" in comp: comp_hits[comp] = bool(re.search(r"\b(solution|solve|measure|can be done|should|address)\b", e))
        elif "cause" in comp or "reason" in comp: comp_hits[comp] = bool(re.search(r"\b(cause|reason|because|why|due to)\b", e))
        elif "effect" in comp or "impact" in comp or "consequence" in comp: comp_hits[comp] = bool(re.search(r"\b(effect|impact|result|consequence|lead to)\b", e))
        elif "position" in comp or "opinion" in comp or "judgement" in comp: comp_hits[comp] = bool(re.search(r"\b(i think|i believe|in my opinion|agree|disagree|outweigh|positive|negative|i argue)\b", e))
        elif "conclusion" in comp: comp_hits[comp] = bool(re.search(r"\b(in conclusion|to conclude|overall|to sum up|in summary)\b", e))
        elif "view_1" in comp: comp_hits[comp] = bool(re.search(r"\b(some people|one view|on the one hand|some argue)\b", e))
        elif "view_2" in comp: comp_hits[comp] = bool(re.search(r"\b(others|other people|on the other hand|however)\b", e))
        else: comp_hits[comp] = bool(words(essay_text))
    missing = [k for k, v in comp_hits.items() if not v]
    # [v18d.2] hard_fail_if_missing no longer blocks score_ready.
    # score_ready is True whenever the prompt is present and a task type is
    # confirmed — hard_fail components that are missing are handled by the
    # scorer's gate caps (task_schema_incomplete_gate / task_schema_hard_fail_gate).
    # Blocking score_ready based on keyword-match failures is too conservative:
    # essays often express required components with vocabulary outside the fixed
    # keyword list, causing false negatives.
    hard_fail_missing = [c for c in schema.get("hard_fail_if_missing", []) if not comp_hits.get(c, False)]
    score_ready = (best != "prompt_missing_or_unsupported")
    task_completeness_confidence = 1.0 if not missing else max(0.2, 1.0 - 0.15 * len(missing))
    return {
        "task_profile_id": stable_id("task", prompt_text, best),
        "prompt_id": stable_id("prompt", prompt_text),
        "task_type": best,
        "task_type_source": "task_schema_registry_v1",
        "task_type_confidence": clamp(score/3 if score <= 3 else 1.0),
        "task_schema_id": best,
        "required_components": schema.get("required_components", []),
        "optional_components": schema.get("optional_components", []),
        "prompt_part_hits": comp_hits,
        "missing_required_components": missing,
        "task_schema_gate_reasons": [f"missing:{m}" for m in missing],
        "score_ready": score_ready,
        "hard_fail_missing_components": hard_fail_missing,
        "task_completeness_confidence": task_completeness_confidence,
        "task_registry_version": registry.get("version"),
        "task_registry_source": (load_decision_registries().audit.get("registries_used", {}).get("task_schema_registry", {}) or {}).get("path"),
    }



# --- V3 Section 3 addition (topic-alignment safety net) -------------------
def detect_topic_alignment_risk(prompt_text: str, essay_text: str, tracker: LLMTracker, llm_enabled: bool) -> Dict[str, Any]:
    """Cheap, independent tripwire for genuinely off-topic essays.

    Per GOLD_PIPELINE_SPEC_V3_TASK_RELEVANCE.md Section 3: the Evaluator's
    real task-relevance fix (arg_claim_relevance / maintain_task_focus,
    v8.4) depends on an LLM call succeeding. This is a second, independent,
    cheap-model check that flows through errormap into the Scorer as a hard
    ceiling on task_response -- it protects the score even if the
    Evaluator's own LLM check is unavailable or wrong. One boolean flag,
    one consumer (Scorer's ceiling logic), nothing else -- this is not a
    second relevance-scoring implementation and must not grow into one.

    Fails safe: any missing input, disabled LLM, or unparseable response
    yields risk_flag=False (never blocks/penalizes an essay on an
    inconclusive check).
    """
    if not (prompt_text or "").strip() or not (essay_text or "").strip():
        return {"checked": False, "risk_flag": False, "confidence": 0.0, "reason": "prompt_or_essay_missing"}
    if not llm_enabled:
        return {"checked": False, "risk_flag": False, "confidence": 0.0, "reason": "llm_disabled_fail_safe"}
    data = llm_json(
        f"Prompt (assigned IELTS Writing Task 2 topic):\n{prompt_text}\n\n"
        f"Essay (first 900 characters):\n{essay_text[:900]}\n\n"
        "Does the essay actually address the topic of the prompt? Answer strictly "
        "based on subject matter, not writing quality. "
        'Return JSON {"same_topic": "yes"|"partial"|"no", "confidence": 0-1, "reason": "one short sentence"}.',
        "You are a fast topic-match checker. Judge topical relevance only, not essay quality. JSON only.",
        CHEAP_MODEL, "topic_alignment_risk_check", tracker, llm_enabled, 150,
    )
    if not isinstance(data, dict) or data.get("same_topic") not in ("yes", "partial", "no"):
        return {"checked": False, "risk_flag": False, "confidence": 0.0, "reason": "llm_call_failed_or_unparseable_fail_safe"}
    same_topic = data.get("same_topic")
    confidence = safe_float(data.get("confidence"), 0.5)
    return {
        "checked": True,
        "same_topic": same_topic,
        "risk_flag": same_topic in ("no", "partial"),
        "confidence": round(confidence, 3),
        "reason": str(data.get("reason") or "")[:300],
    }

def layer0_idea_map(prompt_text: str, essay_text: str, segmentation: Dict[str, Any], task_profile: Dict[str, Any], tracker: LLMTracker, llm_enabled: bool) -> Dict[str, Any]:
    sent_block = "\n".join(f"S{s['sentence_index']}: {s['text']}" for s in segmentation["sentences"][:35])
    prompt = f"Prompt: {prompt_text or '[missing]'}\nTask profile: {json.dumps(task_profile, ensure_ascii=False)}\nSentences:\n{sent_block}\nReturn JSON with topic_map, proposition_map, argument_map, paragraph_role_map, position_signal, idea_sequence. Do not detect local grammar/LR errors."
    data = llm_json(prompt, "You are VA Layer 0. Map ideas/propositions only. JSON only.", CHEAP_MODEL, "L0_idea_map", tracker, llm_enabled, 1400)
    if isinstance(data, dict):
        data.setdefault("task_schema_profile", task_profile); data.setdefault("layer", "layer0"); data["llm_status"] = "active"; data["model_used"] = CHEAP_MODEL
        return data
    return {"task_schema_profile": task_profile, "topic_map": {}, "proposition_map": [{"sentence_index": s["sentence_index"], "proposition": s["text"], "role": "unknown", "confidence": 0.3} for s in segmentation["sentences"]], "argument_map": {}, "paragraph_role_map": [], "position_signal": "unknown", "idea_sequence": [], "layer": "layer0", "llm_status": "fallback", "model_used": None}

def heuristic_semantic_score(text: str) -> Tuple[float,float,float,str,str]:
    low = text.lower(); w = words(text)
    corruption = 0.12
    if len(w) < 4: corruption += 0.18
    if len(w) > 32: corruption += 0.10
    if re.search(r"\b(the way be|still do|can be help|will ill|this is advantages|for take care|as it possible|good ability to|nothing scaring|be fewer)\b", low): corruption += 0.55
    if re.search(r"\b(and|but|so)\b", low) and len(re.findall(r"\b(is|are|was|were|be|been|do|does|did|have|has|had|can|will|would|should|may|might|must)\b", low)) <= 1 and len(w) >= 10: corruption += 0.20
    if text.count(",") >= 3: corruption += 0.12
    corruption = clamp(corruption)
    rec = clamp(0.92 - corruption)
    trust = clamp(0.90 - corruption*0.95)
    allowed = "blocked" if rec < 0.35 else "limited" if rec < 0.70 else "full"
    root = "local_language" if corruption >= 0.35 else "none"
    return rec, trust, corruption, allowed, root

def layer0_5_semantic(segmentation: Dict[str, Any], tracker: LLMTracker, llm_enabled: bool) -> Dict[str, Any]:
    sentences = segmentation["sentences"]
    sent_block = "\n".join(f"S{s['sentence_index']}: {s['text']}" for s in sentences)
    data = llm_json(
        "Assess semantic recoverability for each sentence. Be strict, not charitable. Broken local language should raise local_corruption.\n" + sent_block + "\nReturn JSON {items:[{sentence_index,recoverability_score,semantic_trust_score,local_corruption_score,discourse_evaluation_allowed:'full|limited|blocked',proposition_recovered,root_cause_hint}]}",
        "You are Layer 0.5 semantic recoverability/evaluability gate. JSON only.", CHEAP_MODEL, "L0_5_semantic_batch", tracker, llm_enabled, 2200)
    by_idx = {}
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        for it in data["items"]:
            try: by_idx[int(it.get("sentence_index"))] = it
            except Exception: pass
    assessments = {}; blocked = limited = 0
    for s in sentences:
        rec0, trust0, corr0, allowed0, root0 = heuristic_semantic_score(s["text"])
        it = by_idx.get(s["sentence_index"], {})
        rec = min(rec0, clamp(safe_float(it.get("recoverability_score"), rec0))) if it else rec0
        trust = min(trust0, clamp(safe_float(it.get("semantic_trust_score"), trust0))) if it else trust0
        corr = max(corr0, clamp(safe_float(it.get("local_corruption_score"), corr0))) if it else corr0
        allowed = str(it.get("discourse_evaluation_allowed") or allowed0) if it else allowed0
        if corr >= 0.45 and allowed == "full": allowed = "limited"
        if corr >= 0.70: allowed = "blocked"
        if allowed == "blocked": blocked += 1
        elif allowed == "limited": limited += 1
        assessments[str(s["sentence_index"])] = {"sentence_index": s["sentence_index"], "recoverability_score": round(rec,3), "proposition_stability_score": round(trust,3), "semantic_trust_score": round(trust,3), "local_corruption_score": round(corr,3), "discourse_evaluation_allowed": allowed, "proposition_recovered": str(it.get("proposition_recovered") or (s["text"] if rec > 0.65 else "")), "root_cause_hint": str(it.get("root_cause_hint") or root0), "model_used": CHEAP_MODEL if it else None}
    n = max(1, len(sentences))
    return {"layer": "layer0_5", "sentence_assessments": assessments, "semantic_summary": {"mean_recoverability": round(sum(a["recoverability_score"] for a in assessments.values())/n,3), "mean_semantic_trust": round(sum(a["semantic_trust_score"] for a in assessments.values())/n,3), "mean_local_corruption": round(sum(a["local_corruption_score"] for a in assessments.values())/n,3), "blocked_sentence_count": blocked, "limited_sentence_count": limited, "affected_discourse_ratio": round((blocked+limited)/n,3)}, "llm_status": "active" if by_idx else "fallback"}

# FIX 8: layer1_2_llm_discourse includes GRAMMATICAL_RANGE in allowed families
def layer1_2_llm_discourse(run_id: str, submission_id: str, essay_id: str, prompt_text: str, essay_text: str, segmentation: Dict[str, Any], semantic: Dict[str, Any], tracker: LLMTracker, llm_enabled: bool) -> List[Candidate]:
    cands: List[Candidate] = []
    if len(segmentation["paragraphs"]) <= 1 and len(segmentation["sentences"]) >= 6:
        s0 = segmentation["sentences"][0]
        cands.append(make_candidate(run_id, submission_id, essay_id, "layer1_wide_discourse", "rules_support", "whole essay", essay_text[:400], 0, len(essay_text), s0, "PARAGRAPH_STRUCTURE", "paragraph_structure", "Paragraphing problem", "The response appears to be one long paragraph, which weakens IELTS coherence/organization.", 0.74, {"paragraph_count": len(segmentation["paragraphs"])}, None, "restructure_paragraph", "Split into introduction/body/conclusion paragraphs."))
    sent_block = "\n".join(f"S{s['sentence_index']}: {s['text']}" for s in segmentation["sentences"][:35])
    sem_summary = semantic.get("semantic_summary", {})
    # v10: GRAMMATICAL_RANGE removed — L1/L2 only produces TR/CC families
    allowed_families = sorted(TR_FAMILIES | CC_FAMILIES)
    data = llm_json(
        f"Prompt: {prompt_text or '[missing]'}\n"
        f"Semantic summary: {json.dumps(sem_summary)}\n"
        f"Sentences:\n{sent_block}\n"
        f"Detect ONLY independent CC/TR discourse issues. For each issue:\n"
        f"- TRANSITION: flag if a sentence starts abruptly with no connector where one is logically required "
        f"(contrast, result, example, concession). Quote the sentence opening.\n"
        f"- PROMPT_COVERAGE: flag if the essay ignores a component of the prompt entirely. "
        f"Quote \'entire response\' or the missed topic keyword.\n"
        f"- WEAK_EXAMPLE: flag if a claim is followed by a vague or generic example with no specific detail "
        f"(e.g. \'for example, many people\'). Quote the example sentence.\n"
        f"- POSITION_CLARITY: flag if the writer\'s position is absent or contradictory in the opening/conclusion.\n"
        f"- PARAGRAPH_STRUCTURE: flag if a paragraph mixes unrelated ideas or lacks a topic sentence.\n"
        f"Do NOT flag local grammar, word choice, or sentence structure errors as TR/CC.\n"
        f"Return JSON {{{{candidates:[{{{{sentence_index,quote,family,operation,problem,explanation,confidence,repair_hypothesis}}}}]}}}}.\n"
        f"Allowed families: {allowed_families}",
        "You are VA Layer 1/2 discourse detector. L1=whole text TR/CC, L2=sentence/paragraph discourse.\n"
        "QUOTING RULE: for sentence-level issues quote the sentence or its key opening phrase. "
        "For document-level issues (missing position, off-topic, task incompleteness, weak examples) "
        "use quote=\'entire response\' or the relevant paragraph opening.\n"
        "Do NOT flag GRAMMATICAL_RANGE. Do NOT flag local grammar or word choice errors.\n"
        "JSON only.",
        CHEAP_MODEL, "L1_L2_discourse", tracker, llm_enabled, 1600)
    items = data.get("candidates", []) if isinstance(data, dict) else []
    for it in items[:12] if isinstance(items, list) else []:
        op_raw = str(it.get("repair_operation") or it.get("operation") or "ADD_EXPLANATION")
        fam = str(it.get("family") or "UNKNOWN_FAMILY").upper()
        # v10: GRAMMATICAL_RANGE no longer allowed from L1/L2
        if fam not in TR_FAMILIES and fam not in CC_FAMILIES:
            fam = "UNKNOWN_FAMILY"
        si = int(safe_float(it.get("sentence_index"), 1)) if it.get("sentence_index") is not None else None
        sent = next((s for s in segmentation["sentences"] if s["sentence_index"] == si), segmentation["sentences"][0] if segmentation["sentences"] else None)
        quote = normalize_space(str(it.get("quote") or (sent or {}).get("text", "")))
        if not quote: continue
        rel = (sent or {}).get("text", "").lower().find(quote.lower()) if sent else -1
        st = (sent or {}).get("char_start", 0) + (rel if rel >= 0 else 0)
        en = st + len(quote)
        layer = "layer1_wide_discourse" if fam in TR_FAMILIES else "layer2_sentence_discourse"
        # Hard gate: L1/L2 cannot produce GRA/LR families
        if fam in GRAMMAR_FAMILIES or fam in LEXICAL_FAMILIES:
            continue  # drop silently
        cands.append(make_candidate(run_id, submission_id, essay_id, layer, "llm", quote, (sent or {}).get("text", quote), st, en, sent, fam, op_raw, str(it.get("problem") or "Discourse issue"), str(it.get("explanation") or "LLM identified a discourse issue."), clamp(safe_float(it.get("confidence"), 0.65)), {"llm_item": it, "prompt_missing": not bool(prompt_text)}, CHEAP_MODEL, op_raw, str(it.get("repair_hypothesis") or "Clarify the relationship between ideas."), "root", [], str(it.get("repair_target") or "unspecified"), safe_float(it.get("recoverability_gain"), 0.35), safe_float(it.get("evaluability_gain"), 0.50), safe_float(it.get("clarity_gain"), 0.40)))
    return cands


# ---------------------------------------------------------------------------
# VA25 support bridge (registry-driven, no Python import)
# ---------------------------------------------------------------------------
VA25_LOCAL_RULE_SOURCE = "rule_registry_v1.json"
_VA25_IMPORT_STATUS = "json_rule_registry_active"
_VA25_IMPORT_ERROR = ""
_VA25_RESOURCE_AUDIT: Dict[str, Any] = {}

V92_FUNCTION_WORDS = {"the","a","an","and","but","or","for","yet","so","at","by","in","of","on","to","up","as","if","it","be","is","are","was","were","been","being","have","has","had","do","does","did","will","would","shall","should","may","might","must","can","could","this","that","these","those","they","them","their","there","he","she","his","her","we","our","you","your","my","me","us","its","not","no","all","any","both","each","few","more","most","other","such","than","then","only","also","just","very","here","where","when","which","who","what","how","from","with","about","into","through","during","before","after","between","among","while","although","because","since","unless","until","whether","though","even","too","i"}
V92_AUXILIARIES = {"am","is","are","was","were","be","been","being","have","has","had","do","does","did"}

_V92_RULE_CACHE: Optional[Dict[str, Any]] = None
_V92_RESOURCE_CACHE: Optional[ResourceBundle] = None

def _v92_resources() -> ResourceBundle:
    global _V92_RESOURCE_CACHE
    if _V92_RESOURCE_CACHE is None:
        _V92_RESOURCE_CACHE = load_resources()
    return _V92_RESOURCE_CACHE

def _v92_token_list(text: str) -> List[str]:
    return re.findall(r"[A-Za-z']+", text or "")

def _v92_is_plural_like(tok: str, res: ResourceBundle) -> bool:
    t = (tok or "").lower().strip("'")
    if not t or t in {"is","was","has","does"}: return False
    if t in res.irregular_singular_by_plural: return True
    if t.endswith("ies") and len(t) > 4: return True
    if t.endswith("s") and len(t) > 3 and not t.endswith(("ss","us","is")):
        sg = t[:-1]
        return sg in res.count_nouns or sg in res.valid_words or t in res.valid_words
    return False

def _v92_is_singular_count_noun_like(tok: str, res: ResourceBundle) -> bool:
    t = (tok or "").lower().strip("'")
    if not t or _v92_is_plural_like(t, res): return False
    if t in res.mass_nouns: return False
    return t in res.count_nouns or (t in res.valid_words and not t.endswith(("ing","ed","ly")))

def _v92_is_base_verb_like(tok: str) -> bool:
    t = (tok or "").lower().strip("'")
    if not t or t in V92_FUNCTION_WORDS: return False
    if t.endswith(("ing","ed")): return False
    return bool(re.fullmatch(r"[a-z]{3,}", t))

def _v92_is_nonbase_after_aux(tok: str) -> bool:
    t = (tok or "").lower().strip("'")
    if not t or t in V92_FUNCTION_WORDS: return False
    if t.endswith(("ing","ed")): return True
    return t in {"been","gone","done","made","taken","given","seen","known","written","chosen","broken","spoken","driven","eaten","fallen","found","left","lost","met","paid","read","run","said","sent","spent","taught","thought","told","won","went","gave","took","saw","became","come"}

def _v92_is_predicative_adjective_like(tok: str) -> bool:
    t = (tok or "").lower().strip("'")
    return bool(t and re.search(r"(able|ible|al|ive|ous|ful|less|ic|ical|ary|ory)$", t))

def _v92_add_rule_candidate(out: List[Candidate], run_id: str, sub_id: str, essay_id: str, sent: Dict[str, Any], start: int, end: int, rule: Dict[str, Any], quote: Optional[str]=None, repair: str="") -> None:
    q = quote if quote is not None else sent["text"][start:end]
    fam = str(rule.get("family") or "UNKNOWN_FAMILY").upper()
    op = normalise_v9_operation(str(rule.get("operation") or ""), fam)
    target = str(rule.get("repair_target") or "unspecified")
    conf = clamp(safe_float(rule.get("confidence_default"), 0.78))
    c = make_candidate(run_id, sub_id, essay_id, "layer3_local_language", "rules_registry", q, sent["text"], sent["char_start"]+start, sent["char_start"]+end, sent, fam, op, str(rule.get("description") or fam), str(rule.get("description") or fam), conf, {"rule_id": rule.get("rule_id"), "rule_source": rule.get("source"), "rule_registry_v1": True}, None, op, repair or str(rule.get("repair_template") or ""), "root", [], target, 0.55 if rule.get("severity_default") == "high" else 0.40, 0.45, 0.45)
    c.family_lock_status = "rule_id_primary_family_authority"
    out.append(c)

def _v92_nearest_spelling_candidate(token: str, res: ResourceBundle) -> Optional[str]:
    low = token.lower()
    if len(low) < 5 or re.search(r"[^a-z-]", low) or "-" in low: return None
    if low in res.valid_words or low in res.locale_variants: return None
    candidates = [w for w in res.valid_words if isinstance(w, str) and w[:1] == low[:1] and abs(len(w)-len(low)) <= 2 and 4 <= len(w) <= 24]
    best, best_d = None, 3
    for w in candidates[:50000]:
        d = edit_distance_limited(low, w, 2)
        if d < best_d:
            best, best_d = w, d
            if d == 1: break
    if not best: return None
    if best_d == 1 and (low[-1:] == best[-1:] or low[:3] == best[:3]): return best
    if best_d == 2 and (low[:4] == best[:4] or low[-4:] == best[-4:]) and re.search(r"(ment|tion|ance|ence|ies)$", best): return best
    return None

def l3_va25_support(run_id: str, submission_id: str, essay_id: str, segmentation: Dict[str, Any]) -> List[Candidate]:
    """Registry-driven VA25.1w local support. No Python import; no essay-specific fixtures."""
    res = _v92_resources()
    reg = load_decision_registries()
    rules = (reg.rule_registry or {}).get("rules", [])
    by_id = {r.get("rule_id"): r for r in rules if isinstance(r, dict)}
    out: List[Candidate] = []
    for sent in segmentation.get("sentences", []):
        txt = sent.get("text", "")
        low = txt.lower()
        toks = _v92_token_list(txt)
        r = by_id.get("L_SPELLING_NEAREST_VALID_WORD")
        if r:
            for m in re.finditer(r"\b[A-Za-z]{5,}\b", txt):
                tok = m.group(0)
                if tok[:1].isupper() and tok.lower() not in res.valid_words: continue
                suggestion = _v92_nearest_spelling_candidate(tok, res)
                if suggestion:
                    _v92_add_rule_candidate(out, run_id, submission_id, essay_id, sent, m.start(), m.end(), r, tok, suggestion)
        for rule in rules:
            if not isinstance(rule, dict): continue
            rid = str(rule.get("rule_id") or "")
            if rid == "L_SPELLING_NEAREST_VALID_WORD": continue
            pattern = str(rule.get("pattern") or "")
            ptype = str(rule.get("pattern_type") or "")
            conds = set(rule.get("conditions") or [])
            if not pattern or ptype.startswith("algorithmic") or "validator" in pattern or pattern.startswith("a/an +") or pattern.startswith("token ending"):
                continue
            try:
                matches = list(re.finditer(pattern, txt, flags=re.I))
            except Exception:
                try: matches = list(re.finditer(pattern, low, flags=re.I))
                except Exception: matches = []
            for m in matches[:8]:
                ok = True
                if "second_token_is_nonbase_after_aux" in conds:
                    ok = len(m.groups()) >= 2 and _v92_is_nonbase_after_aux(m.group(2))
                if "second_token_is_predicative_adjective_like" in conds:
                    ok = len(m.groups()) >= 2 and _v92_is_predicative_adjective_like(m.group(2))
                if "second_token_base_verb_like" in conds:
                    ok = len(m.groups()) >= 2 and _v92_is_base_verb_like(m.group(2))
                if "not_auxiliary_or_modal" in conds:
                    ok = ok and (len(m.groups()) >= 2 and m.group(2).lower() not in MODALS | V92_AUXILIARIES)
                if "head_is_plural_like" in conds:
                    head = m.group(m.lastindex or 0).split()[-1] if (m.lastindex or 0) else m.group(0).split()[-1]
                    ok = _v92_is_plural_like(head, res)
                if "head_is_singular_count_noun_like" in conds:
                    head = m.group(m.lastindex or 0).split()[-1] if (m.lastindex or 0) else m.group(0).split()[-1]
                    ok = _v92_is_singular_count_noun_like(head, res)
                if "next_token_is_noun_head_or_singular_count_noun" in conds:
                    ok = len(m.groups()) >= 2 and _v92_is_singular_count_noun_like(m.group(2), res)
                if ok:
                    _v92_add_rule_candidate(out, run_id, submission_id, essay_id, sent, m.start(), m.end(), rule)
        r = by_id.get("G_ARTICLE_A_AN_WITH_PLURAL")
        if r:
            toks2 = list(re.finditer(r"\b[A-Za-z']+\b", txt))
            for i, mt in enumerate(toks2[:-1]):
                if mt.group(0).lower() in {"a", "an"}:
                    for j in range(i+1, min(len(toks2), i+4)):
                        if _v92_is_plural_like(toks2[j].group(0), res):
                            _v92_add_rule_candidate(out, run_id, submission_id, essay_id, sent, mt.start(), toks2[j].end(), r)
                            break
        r = by_id.get("G_MASS_NOUN_PLURALIZATION")
        if r:
            for m in re.finditer(r"\b[A-Za-z']+\b", txt):
                tok = m.group(0).lower()
                if tok.endswith("s") and tok[:-1] in res.mass_nouns:
                    _v92_add_rule_candidate(out, run_id, submission_id, essay_id, sent, m.start(), m.end(), r)
        # v10: G_FOR_BASE_VERB_PURPOSE_PATTERN removed — rule removed from rule_registry_v1.json
        # The for+base-verb rule generated too many false positives. No path generates it in v10.
    # Hard gate: L3 cannot produce CC/TR families; v12 also removes POSSESSIVE_FORM
    out = [c for c in out if not (
        c.family_candidate in CC_FAMILIES or c.family_candidate in TR_FAMILIES
        or c.family_candidate == "POSSESSIVE_FORM"  # v12: rule disabled
    )]
    return out

def va25_resource_status() -> Dict[str, Any]:
    res = _v92_resources()
    reg = load_decision_registries()
    rule_count = len((reg.rule_registry or {}).get("rules", []) or [])
    return {
        "status": "json_rule_registry_active",
        "quality_status": "ready" if rule_count and res.audit.get("quality_status") != "missing_core_resources" else "degraded",
        "resource_ready_for_benchmark": bool(rule_count),
        "va25_python_import_removed": True,
        "rule_registry_loaded": bool(rule_count),
        "rule_count": rule_count,
        "rule_registry_source": (reg.audit.get("registries_used", {}).get("rule_registry", {}) or {}).get("path"),
        "canonical_resource_status": res.audit.get("quality_status"),
        "canonical_resource_dirs_checked": res.audit.get("dirs_checked") or res.dirs_checked,
        "canonical_resource_summary": res.audit.get("summary", {}),
    }


# ---------------------------------------------------------------------------
# Layer 3 local detection: LLM + rules + LT + spaCy
# ---------------------------------------------------------------------------
# v9.7: l3_llm_local — paragraph-batched, hard layer gate applied
def l3_llm_local(run_id: str, submission_id: str, essay_id: str, segmentation: Dict[str, Any], semantic: Dict[str, Any], tracker: LLMTracker, llm_enabled: bool) -> List[Candidate]:
    cands: List[Candidate] = []
    # v18: restricted; broad GRA now from l2_spacy_pass
    allowed_families = ["LEXICAL_PRECISION", "SEMANTIC_COMBINATION"]
    # v12: per-family few-shot system prompt
    system = "\n".join([
        "You are VA Layer 3 lexical precision detector. Detect LEXICAL_PRECISION and SEMANTIC_COMBINATION errors ONLY — never GRA, CC, or TR.",
        "",
        "QUOTING RULE: quote the MINIMUM span containing the error:",
        "  - Wrong verb form: quote only the verb ('spent' not the clause)",
        "  - Wrong article: quote article + noun ('a childrens')",
        "  - Collocation: quote the collocating phrase ('do a decision')",
        "  - Preposition: quote governor + preposition ('depend of')",
        "  - Clause structure: quote the defective clause segment",
        "  - Never quote the whole sentence",
        "",
        "FEW-SHOT EXAMPLES BY FAMILY:",
        "",
        "COLLOCATION (wrong verb/adj/noun collocate — combination unnatural in English):",
        "  'do a mistake' COLLOCATION correct: 'make a mistake'",
        "  'make a research' COLLOCATION correct: 'conduct research'",
        "  'take a decision' COLLOCATION correct: 'make a decision'",
        "  'give a contribution' COLLOCATION correct: 'make a contribution'",
        "  'rise awareness' COLLOCATION correct: 'raise awareness'",
        "  'do an effort' COLLOCATION correct: 'make an effort'",
        "  'strong knowledge' COLLOCATION correct: 'extensive knowledge'",
        "  NOT collocation: 'do their homework', 'make a plan', 'give advice'",
        "",
        "LEXICAL_PRECISION (grammatically correct but semantically imprecise or wrong register):",
        "  'very big problem' LEXICAL_PRECISION prefer: 'significant challenge'",
        "  'things' used vaguely for academic concepts LEXICAL_PRECISION",
        "  'go up' in academic writing LEXICAL_PRECISION prefer: 'increase'",
        "  NOT lexical precision: wrong form (WORD_FORM) or wrong collocate (COLLOCATION)",
        "",
        "CLAUSE_STRUCTURE (clause defective — missing subject, verb, or coherent predicate):",
        "  'it can be help to government' CLAUSE_STRUCTURE: 'help' should be 'helpful'",
        "  'this is advantages' CLAUSE_STRUCTURE: should be 'these are advantages'",
        "  'For the reason that many people.' CLAUSE_STRUCTURE: fragment",
        "  NOT clause structure: wrong verb tense in complete clause (that is VERB_TENSE)",
        "",
        "REGISTER (word or phrase too informal for IELTS academic writing):",
        "  'a lot of problems' REGISTER prefer: 'numerous problems'",
        "  'kids' REGISTER prefer: 'children'",
        "  'really important' REGISTER prefer: 'particularly significant'",
        "",
        "SEMANTIC_COMBINATION (words individually correct but semantically incompatible):",
        "  'mental pollution' SEMANTIC_COMBINATION",
        "  'moral damage' SEMANTIC_COMBINATION prefer: 'moral harm'",
        "",
        "VERB_PATTERN (wrong grammatical pattern after a specific verb):",
        "  'avoid to do' VERB_PATTERN correct: 'avoid doing'",
        "  'suggest to implement' VERB_PATTERN correct: 'suggest implementing'",
        "  'insist to go' VERB_PATTERN correct: 'insist on going'",
        "",
        "Return JSON only.",
    ])

    # Group by paragraph
    by_para: Dict[int, List[Dict[str, Any]]] = {}
    for s in segmentation["sentences"]:
        by_para.setdefault(s["paragraph_index"], []).append(s)

    for para_idx, sents in sorted(by_para.items()):
        sent_block = "\n".join(f"S{s['sentence_index']}: {s['text']}" for s in sents)
        sem_gates = {str(s["sentence_index"]): semantic.get("sentence_assessments", {}).get(str(s["sentence_index"]), {}) for s in sents}
        prompt = (f"Paragraph {para_idx} sentences:\n{sent_block}\n"
                  f"Semantic gates: {json.dumps(sem_gates, ensure_ascii=False)}\n"
                  f"Allowed families: {allowed_families} — ONLY these two.\n"
                  f"Minimum confidence: 0.90. Flag ONLY when clearly wrong.\n"
                  f"For each error return: sentence_index, quote, repair_operation, repair_target, "
                  f"family, problem, explanation, confidence, repair_hypothesis, "
                  f"recoverability_gain, evaluability_gain, clarity_gain\n"
                  f"Return JSON {{{{candidates:[...]}}}}. candidates:[] if no errors.")
        data = llm_json(prompt, system, CHEAP_MODEL, "L3_local_llm_para", tracker, llm_enabled, 1800)
        items = data.get("candidates", []) if isinstance(data, dict) else []
        if not isinstance(items, list):
            continue
        # Build lookup by sentence_index for this paragraph
        by_si = {s["sentence_index"]: s for s in sents}
        items = [it for it in items if safe_float(it.get("confidence"), 0) >= 0.90]
        for it in items[:5]:  # v18: max 5
            op_raw = str(it.get("repair_operation") or it.get("operation") or "UNKNOWN_REPAIR")
            fam = str(it.get("family") or "UNKNOWN_FAMILY").upper()
            if fam not in GRAMMAR_FAMILIES and fam not in LEXICAL_FAMILIES:
                fam = "UNKNOWN_FAMILY"
            # Hard gate: L3 cannot produce CC/TR families (no exception in v10 — GRAMMATICAL_RANGE removed)
            if fam in CC_FAMILIES or fam in TR_FAMILIES:
                continue  # drop silently - layer contamination
            # Look up the sentence for this item
            si_raw = it.get("sentence_index")
            try:
                si = int(si_raw) if si_raw is not None else sents[0]["sentence_index"]
            except (ValueError, TypeError):
                si = sents[0]["sentence_index"]
            sent = by_si.get(si) or sents[0]
            quote = normalize_space(str(it.get("quote") or ""))
            if not quote: continue
            rel = sent["text"].lower().find(quote.lower())
            if rel < 0:
                qwords = [w for w in words(quote) if len(w) > 2]
                rel = -1
                for qw in qwords:
                    m = re.search(re.escape(qw), sent["text"], flags=re.I)
                    if m: rel = m.start(); break
            if rel < 0: continue
            st = sent["char_start"] + rel; en = min(sent["char_end"], st + len(quote))
            cands.append(make_candidate(run_id, submission_id, essay_id, "layer3_local_language", "llm", quote, sent["text"], st, en, sent, fam, op_raw, str(it.get("problem") or "Local language issue"), str(it.get("explanation") or "LLM identified a local GRA/LR issue."), clamp(safe_float(it.get("confidence"), 0.70)), {"llm_item": it}, CHEAP_MODEL, op_raw, str(it.get("repair_hypothesis") or ""), str(it.get("root_or_secondary") or "root"), [str(x).upper() for x in (it.get("secondary_families") or []) if str(x).upper() in FAMILY_TO_RUBRIC], str(it.get("repair_target") or "unspecified"), safe_float(it.get("recoverability_gain"), 0.45), safe_float(it.get("evaluability_gain"), 0.35), safe_float(it.get("clarity_gain"), 0.35)))
    return cands

# FIX 7: l3_universal_rules with FP guards for for+base-verb and a/an+plural
FOR_FIXED_PHRASES = {
    "for example", "for instance", "for free", "for good", "for now", "for once",
    "for sure", "for real", "for fun", "for long", "for years", "for months",
    "for decades", "for centuries", "for granted",
}

def l3_universal_rules(run_id: str, submission_id: str, essay_id: str, segmentation: Dict[str, Any], semantic: Dict[str, Any]) -> List[Candidate]:
    cands: List[Candidate] = []
    for sent in segmentation["sentences"]:
        txt = sent["text"]; low = txt.lower()
        def add(mstart: int, mend: int, fam: str, op: str, prob: str, exp: str, conf: float, repair_op: str="none", repair: str=""):
            cands.append(make_candidate(run_id, submission_id, essay_id, "layer3_local_language", "rules_support", txt[mstart:mend], txt, sent["char_start"]+mstart, sent["char_start"]+mend, sent, fam, op, prob, exp, conf, {"support_only": True}, None, repair_op, repair))
        for m in re.finditer(r"\b(has|have|had)\s+to\s+([A-Za-z]+ed)\b", txt, flags=re.I):
            add(m.start(), m.end(), "VERB_FORM", "finite_verb_control", "Wrong verb form after 'have/has to'", "After 'have/has to', the base verb form is required.", 0.92, "change_form", "Use the base verb form after 'to'.")
        for m in re.finditer(r"\bmore\s+[A-Za-z]+er\b", txt, flags=re.I):
            add(m.start(), m.end(), "COMPARATIVE_FORM", "comparative_degree", "Double comparative", "Use either 'more + adjective' or '-er', not both.", 0.94, "delete_word", "Remove 'more' or use the base adjective.")
        # v12 SVA fix: quote only the finite verb (not subject+verb bigram)
        for m in re.finditer(r"\b(this|that|it)\s+(make|lead|give|take|create|show|cause)\b", txt, flags=re.I):
            add(m.start(2), m.end(2), "SUBJECT_VERB_AGREEMENT", "sva_control", "Subject-verb agreement", "Singular demonstrative/pronoun subject requires singular verb form.", 0.88, "change_form", "Use the singular verb form.")
        # Additional SVA: "there is/are" mismatch with plural/singular head noun
        for m in re.finditer(r"\bthere\s+(is)\s+(many|several|various|a number of|multiple)\b", txt, flags=re.I):
            add(m.start(1), m.end(1), "SUBJECT_VERB_AGREEMENT", "sva_control", "Subject-verb agreement after existential 'there'", "'There is' requires a singular noun head; 'there are' is needed before plural.", 0.90, "change_form", "Use 'there are'.")
        for m in re.finditer(r"\bthere\s+(are)\s+(one|a single|each|every)\b", txt, flags=re.I):
            add(m.start(1), m.end(1), "SUBJECT_VERB_AGREEMENT", "sva_control", "Subject-verb agreement after existential 'there'", "'There are' should be 'there is' with a singular quantifier.", 0.88, "change_form", "Use 'there is'.")
        for m in re.finditer(r"\b(a|an)\s+([A-Za-z]+s|children|people)\b", txt, flags=re.I):
            if txt[max(0,m.start()-5):m.end()].lower().startswith("a few"): continue
            # FIX 7: guard for valid "a + [collective noun] of" constructions
            matched_phrase = m.group(0).lower()
            if re.match(r"\ba (number|group|series|range|variety|lot|kind|type|sort|set|collection|pair|couple|bit|great deal|good deal|matter|question|process)\b", matched_phrase):
                continue
            add(m.start(), m.end(), "ARTICLE_DETERMINER", "article_np_licensing", "Article/number mismatch", "A/an is incompatible with a plural noun in this context.", 0.86, "delete_word", "Remove 'a/an' or use a singular noun.")
        # v10: for+base-verb rule removed from l3_universal_rules (too many false positives)
        for m in re.finditer(r"\b(can|could|will|would|should|may|might|must)\s+be\s+(help|ill|advantage|advantages)\b", txt, flags=re.I):
            add(m.start(), m.end(), "CONSTRUCTION", "clause_skeleton", "Malformed modal + be complement", "The complement after modal + be is not in a grammatical form for the intended meaning.", 0.86, "rewrite_clause", "Use a compatible adjective/noun or a lexical verb pattern.")

        # ── v12 CLAUSE_STRUCTURE rules ────────────────────────────────────
        # Bare complement after MAKE/FIND/KEEP: "make it possible" is OK; "it possible" alone is CLAUSE_STRUCTURE
        # Subject + transitive verb + object + adjective → valid; missing main verb → error
        # Pattern: sentence with no finite verb (fragment check — simple heuristic)
        toks_in_sent = re.findall(r"\b[A-Za-z']+\b", txt)
        has_finite = bool(re.search(
            r"\b(is|are|was|were|be|been|being|have|has|had|do|does|did|"
            r"make|makes|made|go|goes|went|get|gets|got|seem|seems|seemed|"
            r"become|becomes|became|need|needs|needed|want|wants|wanted|"
            r"show|shows|showed|lead|leads|led|give|gives|gave|cause|causes|caused)\b",
            txt, re.I
        ) or re.search(r"\b(can|could|may|might|must|should|will|would)\s+\w+", txt, re.I))
        # Comma splice: two independent clauses joined by comma with no coordinator
        if re.search(r"\b(I|we|they|people|government|it|this|that|he|she)\b[^,;.!?]{10,},"
                     r"\s*(I|we|they|people|government|it|this|that|he|she)\b", txt, re.I):
            add(0, min(len(txt), 60), "CLAUSE_STRUCTURE", "clause_skeleton",
                "Comma splice — two independent clauses joined without coordinator",
                "Two independent clauses are joined with only a comma. Use a coordinator "
                "('and', 'but', 'so') or a semicolon, or split into two sentences.",
                0.72, "rewrite_clause", "Add a coordinator or split into two sentences.")
        # Missing copula: "X ADJ" where X is a subject pronoun and no verb present nearby
        for m in re.finditer(r"\b(it|this|that|which)\s+(difficult|hard|possible|important|"
                              r"necessary|clear|obvious|true|false|good|bad|wrong|right|"
                              r"better|worse|likely|unlikely|certain|uncertain)\b", txt, re.I):
            # Only flag if no copula precedes in the sentence
            preceding = txt[:m.start()].lower()
            if not re.search(r"\b(is|are|was|were|seems?|becomes?|gets?|find|make|makes|made|keep|keeps|kept)\s*$", preceding.rstrip()):
                if not re.search(r"\b(is|are|was|were|be)\b", txt[max(0,m.start()-10):m.end()], re.I):
                    add(m.start(), m.end(), "CLAUSE_STRUCTURE", "clause_skeleton",
                        "Missing copula or governing verb",
                        "A copula ('is'/'are') or governing verb ('find', 'make', 'keep') appears to be missing before the adjective complement.",
                        0.78, "rewrite_clause", "Add the missing verb: 'it is difficult' or 'find it difficult'.")
        # Subordinate clause as standalone sentence (starts with Although/Because/Since/While + no main clause)
        # v13 fix: was r",\s*[A-Z]" — missed main clauses starting with lowercase pronouns
        # (they/we/people). Changed to r",\s*[a-zA-Z]" to catch all continuations after comma.
        if re.match(r"^\s*(Although|Because|Since|While|Whereas|Even though|Despite the fact that)\b",
                    txt, re.I):
            if not re.search(r",\s*[a-zA-Z]", txt) and "." not in txt[:-1]:
                add(0, min(len(txt), 50), "CLAUSE_STRUCTURE", "clause_skeleton",
                    "Dangling subordinate clause — no main clause",
                    "This sentence begins with a subordinating conjunction but has no accompanying main clause. "
                    "Add a main clause after the comma.",
                    0.82, "rewrite_clause", "Complete the sentence with a main clause after the subordinate clause.")

        # ── v12 REGISTER rules ────────────────────────────────────────────
        # Contraction detector (informal in IELTS academic writing)
        CONTRACTIONS = re.compile(
            r"\b(can't|won't|wouldn't|couldn't|shouldn't|don't|doesn't|didn't|"
            r"haven't|hasn't|hadn't|isn't|aren't|weren't|wasn't|"
            r"I'm|I've|I'll|I'd|we're|we've|we'll|they're|they've|"
            r"it's|that's|there's|what's|who's|he's|she's|"
            r"you're|you've|you'll|you'd)\b", re.I
        )
        for m in CONTRACTIONS.finditer(txt):
            # P0-FIX-2 (v18b): skip whitelisted contractions (0 TP / 11 FP vs benchmark)
            if m.group(0).lower() in _REGISTER_CONTRACTION_WHITELIST:
                continue
            add(m.start(), m.end(), "REGISTER", "register_formality",
                "Contraction in formal IELTS writing",
                f"The contraction '{m.group(0)}' is informal. IELTS Task 2 requires formal academic register.",
                0.92, "replace_word", f"Replace with the full form.")
        # Informal lexical items
        INFORMAL_ITEMS = {
            "kids": "children", "guys": "people/individuals", "gonna": "going to",
            "wanna": "want to", "gotta": "have to", "kinda": "somewhat",
            "sorta": "somewhat", "a lot of": None, "lots of": None,
            "super": None, "huge": None, "awesome": None, "stuff": "things/aspects",
            "things": None,  # too broad — skip
        }
        INFORMAL_RE = re.compile(
            r"\b(kids|guys|gonna|wanna|gotta|kinda|sorta|awesome|stuff)\b", re.I
        )
        for m in INFORMAL_RE.finditer(txt):
            word = m.group(0).lower()
            suggestion = {"kids": "children", "guys": "people or individuals",
                          "gonna": "going to", "wanna": "want to", "gotta": "have to",
                          "kinda": "somewhat", "sorta": "somewhat",
                          "awesome": "excellent or impressive", "stuff": "aspects or factors"}.get(word, "a more formal alternative")
            add(m.start(), m.end(), "REGISTER", "register_formality",
                "Informal word in academic writing",
                f"'{m.group(0)}' is informal/colloquial. IELTS Task 2 requires formal vocabulary.",
                0.88, "replace_word", f"Replace with '{suggestion}'.")
        if semantic.get("sentence_assessments", {}).get(str(sent["sentence_index"]), {}).get("local_corruption_score", 0) >= 0.58:
            cands.append(make_candidate(run_id, submission_id, essay_id, "layer3_local_language", "semantic_gate_support", txt[:120], txt, sent["char_start"], min(sent["char_end"], sent["char_start"]+120), sent, "CLAUSE_STRUCTURE", "clause_skeleton", "High local repair burden", "Semantic gate indicates clause/predicate-level local corruption; discourse should not absorb this as CC/TR unless independent evidence exists.", 0.75, {"semantic_gate": semantic.get("sentence_assessments", {}).get(str(sent["sentence_index"]), {}), "support_only": True}, None, "rewrite_clause", "Rewrite the clause with a clear subject, verb and complement."))
    # Hard gate: L3 cannot produce CC/TR; v12 also removes POSSESSIVE_FORM
    cands = [c for c in cands if not (
        c.family_candidate in CC_FAMILIES or c.family_candidate in TR_FAMILIES
        or c.family_candidate == "POSSESSIVE_FORM"  # v12: rule disabled
    )]
    return cands

def map_lt_family(match: Any) -> Tuple[str,str,str,str,float]:
    msg = (getattr(match, "message", "") or "").lower(); cat = str(getattr(match, "category", "") or "").upper(); rule = str(getattr(match, "ruleId", "") or "").upper()
    if "british english" in msg or "american english" in msg:
        return "SPELLING", "spelling_surface", "Locale variant", getattr(match, "message", "Locale variant"), 0.20
    if "spelling" in msg or "possible typo" in msg or "MORFOLOGIK" in rule or cat == "TYPOS":
        return "SPELLING", "spelling_surface", "Possible spelling issue", getattr(match, "message", "Possible spelling issue"), 0.86
    if "plural noun" in msg or "article" in msg or "determiner" in msg or "a child" in msg:
        return "ARTICLE_DETERMINER", "article_np_licensing", "Article/determiner issue", getattr(match, "message", "Article issue"), 0.80
    if "singular verb" in msg or "verb tense" in msg or "expects a singular verb" in msg or "agreement" in msg:
        return "SUBJECT_VERB_AGREEMENT", "sva_control", "Subject-verb agreement issue", getattr(match, "message", "Agreement issue"), 0.78
    if "comparative" in msg or "without 'more'" in msg:
        return "COMPARATIVE_FORM", "comparative_degree", "Comparative form issue", getattr(match, "message", "Comparative issue"), 0.82
    if "preposition" in msg:
        return "PREPOSITION_PATTERN", "preposition_governance", "Preposition pattern issue", getattr(match, "message", "Preposition issue"), 0.76
    if "possessive" in msg or "pronoun" in msg:
        # v12: POSSESSIVE_FORM disabled; pronoun LT matches → PRONOUN_CASE
        return "PRONOUN_CASE", "pronoun_role", "Pronoun case/form issue", getattr(match, "message", "Pronoun issue"), 0.76
    if "space" in msg or "comma" in msg or "punctuation" in msg or cat in {"TYPOGRAPHY", "PUNCTUATION"}:
        return "GRAMMAR_PUNCTUATION", "punctuation_surface", "Punctuation/spacing issue", getattr(match, "message", "Punctuation issue"), 0.70
    return "GRAMMAR_PUNCTUATION", "punctuation_surface", "LanguageTool weak signal", getattr(match, "message", "LanguageTool issue"), 0.45

def l3_lt_support(run_id: str, submission_id: str, essay_id: str, segmentation: Dict[str, Any], resources: ResourceBundle) -> List[Candidate]:
    lt = get_lt(); out: List[Candidate] = []
    if lt is None: return out
    for sent in segmentation["sentences"]:
        try: matches = lt.check(sent["text"])
        except Exception: matches = []
        for m in matches[:15]:
            off = int(getattr(m, "offset", 0) or 0); length = int(getattr(m, "errorLength", 0) or 1)
            quote, st0, en0 = expand_to_token(sent["text"], off, off+length)
            fam, op, prob, exp, conf = map_lt_family(m)
            rep = (list(getattr(m, "replacements", []) or [])[:1] or [""])[0]
            evidence = {"lt_rule_id": getattr(m, "ruleId", ""), "lt_category": getattr(m, "category", ""), "replacements": list(getattr(m, "replacements", []) or [])[:5], "support_only": True}
            if fam == "SPELLING" and quote.lower() in resources.locale_variants:
                evidence["locale_variant"] = True; conf = 0.20
            out.append(make_candidate(run_id, submission_id, essay_id, "layer3_local_language", "LanguageTool_support", quote, sent["text"], sent["char_start"]+st0, sent["char_start"]+en0, sent, fam, op, prob, exp, conf, evidence, None, "replace_word" if fam in {"SPELLING", "WORD_FORM"} else "none", rep))
    return out

def l3_spacy_support(run_id: str, submission_id: str, essay_id: str, segmentation: Dict[str, Any]) -> List[Candidate]:
    nlp = get_spacy(); out: List[Candidate] = []
    if nlp is None: return out
    base_verbs = {"make", "go", "have", "do", "lead", "create", "show", "cause", "need", "help"}
    for sent in segmentation["sentences"]:
        try: doc = nlp(sent["text"])
        except Exception: continue
        toks = list(doc)
        for tok in toks:
            if getattr(tok, "dep_", "") not in {"nsubj", "nsubjpass"}: continue
            head = tok.head
            head_text = getattr(head, "text", "")
            head_lower = head_text.lower()
            head_pos = getattr(head, "pos_", "")
            if head_pos not in {"VERB", "AUX"}: continue
            if head_lower not in base_verbs: continue
            left_tokens = [t.text.lower() for t in toks[max(0, head.i-2):head.i]]
            if any(t in MODALS for t in left_tokens): continue
            subj = tok.text.lower()
            if subj in {"this", "that", "it", "he", "she"}:
                out.append(make_candidate(run_id, submission_id, essay_id, "layer3_local_language", "spaCy_support", head_text, sent["text"], sent["char_start"]+head.idx, sent["char_start"]+head.idx+len(head_text), sent, "SUBJECT_VERB_AGREEMENT", "sva_control", "spaCy SVA support", "Dependency parse suggests a singular subject with a base-form finite verb. This is support evidence only.", 0.64, {"subject": tok.text, "verb": head_text, "dep": tok.dep_, "head_pos": head_pos, "support_only": True}, None, "change_form", "Use the singular finite verb if no modal/licensing context exists."))
    return out


# ---------------------------------------------------------------------------
# Arbitration helpers
# ---------------------------------------------------------------------------
def meaningful_quote(q: str) -> bool:
    qq = q or ""
    if len(qq.strip()) < 2: return False
    if len(words(qq)) == 0 and not re.search(r"\s[,.;:!?]|[,.;:!?]\s", qq): return False
    if len(qq.strip()) == 1 and qq.strip().isalpha(): return False
    return True

def false_positive_veto(c: Candidate, resources: ResourceBundle) -> Optional[str]:
    # v12: POSSESSIVE_FORM rule disabled — trigger logic is fundamentally wrong
    if c.family_candidate == "POSSESSIVE_FORM":
        return "possessive_form_rule_disabled_v12"

    # v13: SVA rule-based engine disabled — 74% FP rate (49/66 charges were FPs in v12).
    # Rule patterns fire on grammatically correct subject+verb pairs (e.g. "that government",
    # "it hard", "That said"). LT-corroborated and LLM-detected SVA remain active.
    if (c.family_candidate == "SUBJECT_VERB_AGREEMENT"
            and c.source_engine in {"rules_support", "rules_registry", "rules_va25_support"}):
        return "sva_rule_engine_disabled_v13"

    # v14: NNC rule-based engine disabled — 18 rule FPs in v13 on "many people/few minutes/
    # several effects". Rules fire on NNC proximity patterns without number-conflict confirmation.
    # LLM-detected and LT-corroborated NNC remain active.
    if (c.family_candidate == "NOUN_NUMBER_COUNTABILITY"
            and c.source_engine in {"rules_support", "rules_registry", "rules_va25_support"}):
        return "nnc_rule_engine_disabled_v14"

    q = normalize_space(c.quote).lower()
    e = c.raw_evidence or {}
    if not meaningful_quote(c.quote): return "bad_quote_too_short_or_empty"
    if e.get("locale_variant") or (c.family_candidate == "SPELLING" and q in resources.locale_variants): return "locale_variant_not_error"
    if c.family_candidate == "SPELLING" and resources.valid_words and q in resources.valid_words: return "valid_word_resource_veto"
    # P0-FIX-4 (v18b): LT spelling whitelist for proper nouns / technical loanwords.
    if c.family_candidate == "SPELLING" and q in _LT_SPELLING_WHITELIST: return "lt_spelling_whitelisted_v18b"
    if c.source_engine == "spaCy_support" and c.confidence < 0.70: return "spacy_support_not_chargeable_alone"
    if c.source_engine == "LanguageTool_support" and c.confidence < 0.60: return "lt_weak_signal_not_chargeable"
    # C1 v11: modal/passive distinction — narrow to bare infinitive only, exclude passive
    if c.family_candidate in {"SUBJECT_VERB_AGREEMENT", "VERB_FORM", "VERB_PATTERN"}:
        local_low = c.local_quote.lower()
        q_low = q
        if re.search(r'\b(can|could|may|might|must|should|will|would)\s+' + re.escape(q_low) + r'\b', local_low):
            # Modal + bare infinitive IS valid — protect it
            # But NOT if it is modal + be + past_participle (passive may still be error)
            if not re.search(r'\b(can|could|may|might|must|should|will|would)\s+be\s+\w+ed\b', local_low):
                return "modal_base_form_valid"
    if c.family_candidate == "SUBJECT_VERB_AGREEMENT" and q in {"hard", "possible", "important", "good", "bad"}: return "adjective_complement_not_sva"
    if c.family_candidate == "VERB_TENSE" and re.search(r'\bthis can [a-z]+', c.local_quote.lower()): return "modal_can_not_tense_error"

    # Fix B v11: SVA gate — exclude passive voice and modal chains
    if c.family_candidate == "SUBJECT_VERB_AGREEMENT":
        local_low = c.local_quote.lower()
        # Passive construction: be + past_participle — do not flag as SVA
        if re.search(r'\b(is|are|was|were|be|been|being)\s+\w+(ed|en)\b', local_low):
            return "sva_passive_construction_not_error"
        # Modal + verb chain: SVA doesn't apply to bare infinitives after modals
        if re.search(r'\b(can|could|may|might|must|should|will|would)\s+' + re.escape(q) + r'\b', local_low):
            return "sva_modal_chain_not_error"
        # Without morphology registry we cannot confirm — demote to review_only via returning None
        # (reviewed later in Stage 6 via is_low_threshold tag)

    # Fix B v11: POSSESSIVE_FORM gate — check collocation registry before charging
    if c.family_candidate == "POSSESSIVE_FORM":
        q_words = [w.lower() for w in words(q) if len(w) > 1]
        # If the suspected possessive phrase is a known collocation → veto
        for hw, collocates in resources.collocation_index.items():
            if hw in q_words:
                for col in collocates:
                    if col in q_words:
                        return "possessive_form_valid_collocation"
        # Without morphology registry, single-word POSSESSIVE quotes are too risky
        if len(q_words) <= 1 and not resources.form_to_lemma:
            return "possessive_form_single_word_no_morphology_veto"
    if c.source_engine == "LanguageTool_support" and c.family_candidate == "SPELLING" and len(q) <= 2: return "lt_spelling_quote_not_expanded"

    # Morphology registry veto: flagged form IS a valid form for its lemma
    if c.family_candidate in {"VERB_FORM", "VERB_TENSE", "WORD_FORM"}:
        q_lower = q
        if q_lower in resources.form_to_lemma:
            # It's a known valid word form - need more context to be sure it's wrong
            # Only veto if it also appears in valid_words (double-check)
            if q_lower in resources.valid_words:
                return "morphology_registry_valid_form_veto"

    # Structured positive collocation veto: student's headword+collocate IS in registry
    if c.family_candidate in {"COLLOCATION", "SEMANTIC_COMBINATION"}:
        q_words = [w.lower() for w in words(q) if len(w) > 2]
        for hw, collocates in resources.collocation_index.items():
            if hw in q_words:
                if any(col in q_words for col in collocates):
                    return "positive_collocation_registry_veto"

    # v18c: ARTICLE_DETERMINER — veto common valid phrases over-flagged by LLM
    if c.family_candidate == "ARTICLE_DETERMINER":
        q_low = q.lower()
        # "a few [noun/time]" is always standard English — never an article error
        if re.match(r'^a few\b', q_low):
            return "art_det_a_few_standard_english_v18c"
        # "as a result" — standard transitional phrase.
        # v18d R3: also check local_quote because the detector sometimes extracts
        # only "a result" (without "as") as the quote span — the full phrase is
        # visible in local_quote / surrounding context.
        _local_low = (c.local_quote or "").lower()
        if re.search(r'\bas a result\b', q_low) or re.search(r'\bas a result\b', _local_low):
            return "art_det_as_a_result_standard_v18d"
        # LLM-only ART detection with low confidence — high FP risk, veto
        if (c.source_engine in {"llm", "slr_lexical_pass", "lr_focused_pass"}
                and c.confidence < 0.75):
            return "art_det_llm_low_confidence_v18c"

    # v18c: VERB_FORM / VERB_PATTERN — veto when quote starts with a modal
    # (LLM sometimes quotes the full modal+infinitive phrase; the construction is valid)
    if c.family_candidate in {"VERB_FORM", "VERB_PATTERN"}:
        _MODALS = {"can", "could", "may", "might", "must", "should", "will", "would"}
        q_parts = q.lower().split()
        if q_parts and q_parts[0] in _MODALS:
            return "verb_form_modal_leads_quote_valid_v18c"

    # v1.4.13 Gold pipeline fix (stress-test Problem 4): rule_registry's
    # "unnecessary comma between subject and finite verb" CLAUSE_STRUCTURE rule
    # fires on ANY comma immediately preceding a finite verb/aux/modal, including
    # the CLOSING comma of a legitimate non-restrictive parenthetical inserted
    # between subject and verb — e.g. "every student, regardless of background,
    # is afforded the same formative exposure ..." is grammatically correct;
    # "regardless of background" is a bracketed interrupter, not a clause defect.
    # Veto only when the flagged comma is paired with an EARLIER comma bracketing
    # a short (<=10 word), comma-free, lowercase-led interrupter phrase that does
    # not itself contain a finite verb (which would instead indicate a genuine
    # comma splice / run-on and should still be charged).
    if (c.family_candidate == "CLAUSE_STRUCTURE"
            and c.source_engine == "rules_registry"
            and re.match(r"^.+,\s*(is|are|was|were|am|has|have|had|does|do|did|"
                          r"can|could|will|would|should|must|may|might|shall)\b", q, flags=re.I)):
        local_full = c.local_quote or ""
        flagged = c.quote or ""
        idx = local_full.find(flagged) if flagged else -1
        if idx != -1:
            before = local_full[:idx]
            prior_comma = before.rfind(",")
            if prior_comma != -1:
                interrupter = before[prior_comma + 1:].strip()
                if (interrupter and "," not in interrupter and interrupter[:1].islower()
                        and 1 <= len(interrupter.split()) <= 10
                        and not re.search(r"\b(is|are|was|were|am|has|have|had|does|do|did)\b",
                                           interrupter, flags=re.I)):
                    return "clause_structure_bracketed_parenthetical_not_error_v1_4_13"

    return None

def repair_is_concrete(repair: str) -> bool:
    r = normalize_space(repair)
    if not r: return False
    if len(r.split()) > 10 and re.search(r"\b(use|replace|rewrite|insert|remove|correct|grammatical|verb form|phrase)\b", r.lower()): return False
    if r.lower().startswith(("use ", "replace ", "rewrite ", "insert ", "remove ", "correct ")) and len(r.split()) > 4: return False
    return True

def materialise_repair(row: DiagnosticRow) -> Dict[str, Any]:
    sent = row.local_quote or ""
    quote = row.quote or ""
    repair = row.repair_hypothesis or ""
    flags = []
    if not sent or not quote or quote not in sent:
        return {"repair_materialised": False, "repair_confidence": 0.0, "original_sentence": sent, "revised_sentence_hypothesis": "", "repair_safety_flags": ["quote_not_in_sentence"]}
    if row.repair_operation in {"replace_word", "replace_phrase", "change_form", "REPLACE_WORD", "REPLACE_COLLOCATION", "FIX_SEMANTIC_COMBINATION", "IMPROVE_PRECISION", "FIX_REGISTER", "FIX_WORD_FORM", "FIX_SPELLING", "CHANGE_VERB_FORM", "CHANGE_NOUN_FORM", "CHANGE_PRONOUN_FORM", "CHANGE_ADJECTIVE_FORM", "CHANGE_ADVERB_FORM", "FIX_SVA", "REPLACE_PREPOSITION", "REPLACE_ARTICLE", "REPLACE_DETERMINER", "REPLACE_WORD_CHOICE", "CHANGE_NOUN_NUMBER", "CHANGE_VERB_TENSE", "FIX_ADJECTIVE_ADVERB_FORM"} and repair_is_concrete(repair):
        revised = sent.replace(quote, repair, 1)
        if revised == sent or len(revised) < 3: flags.append("no_effect")
        if re.search(r"\b(use|replace|rewrite|correct)\b", revised.lower()): flags.append("instruction_text_leaked")
        return {"repair_materialised": not flags, "repair_confidence": 0.8 if not flags else 0.2, "original_sentence": sent, "revised_sentence_hypothesis": revised if not flags else "", "repair_safety_flags": flags}
    if row.repair_operation in {"delete_word", "DELETE_WORD", "DELETE_ARTICLE", "DELETE_PREPOSITION"}:
        revised = normalize_space(sent.replace(quote, "", 1))
        return {"repair_materialised": True, "repair_confidence": 0.65, "original_sentence": sent, "revised_sentence_hypothesis": revised, "repair_safety_flags": []}
    return {"repair_materialised": False, "repair_confidence": 0.0, "original_sentence": sent, "revised_sentence_hypothesis": "", "repair_safety_flags": ["hint_only_repair"]}


# ---------------------------------------------------------------------------
# v9.7 cluster-based arbitration helpers (Changes 3 + 4)
# ---------------------------------------------------------------------------

def cluster_candidates(raw: List[Candidate]) -> List[List[Candidate]]:
    """
    Group candidates into span-proximity clusters.
    Cluster key: (essay_id, sentence_index, span_bucket, rubric_group)
    span_bucket = span_start // 8
    rubric_group: 'local' for GRA/LR, 'discourse' for CC/TR
    """
    from collections import defaultdict as _defaultdict
    buckets: Dict[Tuple, List[Candidate]] = _defaultdict(list)
    for c in raw:
        rubric = FAMILY_TO_RUBRIC.get(c.family_candidate, "unknown")
        rubric_group = "local" if rubric in {"grammar", "lexical_resource"} else "discourse"
        bucket = (
            c.essay_id,
            c.sentence_index if c.sentence_index is not None else -1,
            c.span_start // 8,
            rubric_group,
        )
        buckets[bucket].append(c)
    return list(buckets.values())


# Family depth bonuses for scoring
DEEP_LR_FAMILIES = {"COLLOCATION", "SEMANTIC_COMBINATION", "WORD_CHOICE", "WORD_FORM"}
SHALLOW_LR_FAMILIES = {"LEXICAL_PRECISION", "REPETITION", "REDUNDANCY"}
SPECIFIC_GRA_FAMILIES = {"VERB_FORM", "VERB_TENSE", "SUBJECT_VERB_AGREEMENT",
                          "PREPOSITION_PATTERN", "NOUN_NUMBER_COUNTABILITY",
                          "COMPARATIVE_FORM", "POSSESSIVE_FORM", "PRONOUN_CASE",
                          "PRONOUN_AGREEMENT", "CONDITIONAL_STRUCTURE", "SPELLING"}
# GRAMMATICAL_RANGE removed from BROAD_GRA_FAMILIES in v10 (it is a metric signal, not a detectable error)
BROAD_GRA_FAMILIES = {"CLAUSE_STRUCTURE", "CONSTRUCTION"}

SOURCE_BONUS = {
    "rules_registry":    12,
    "rules_support":     10,
    "rules_va25_support": 10,
    "LanguageTool_support": 8,
    "spaCy_support":      5,
    "llm":                3,   # L3 LLM
    "semantic_gate_support": 4,
}
# L1/L2 LLM gets higher bonus in its own layer
SOURCE_BONUS_DISCOURSE = {
    "llm": 6,
}


def score_family_in_cluster(
    family: str,
    candidates_with_family: List[Candidate],
    all_cluster_candidates: List[Candidate],
    resources: "ResourceBundle",
) -> float:
    """Score a family within a cluster using va16-style evidence accumulation."""
    score = 0.0
    rubric = FAMILY_TO_RUBRIC.get(family, "unknown")
    is_discourse = rubric in {"coherence_cohesion", "task_response"}
    # GRAMMATICAL_RANGE is removed from v10 taxonomy — it cannot appear in clusters.
    # If encountered (legacy candidate), penalise it to never win as root.
    if family == "GRAMMATICAL_RANGE":
        score -= 50  # effectively excludes it

    for c in candidates_with_family:
        src = c.source_engine
        base = clamp(c.confidence) * 100
        # Source bonus
        if is_discourse:
            sb = SOURCE_BONUS_DISCOURSE.get(src, SOURCE_BONUS.get(src, 2))
        else:
            sb = SOURCE_BONUS.get(src, 2)
        # Layer bonus
        lb = 0
        if c.layer == "layer3_local_language":
            lb += 7
        elif c.layer == "layer1_wide_discourse":
            lb += 4
        elif c.layer == "layer2_sentence_discourse":
            lb -= 2
        # Family depth bonus
        db = 0
        if family in DEEP_LR_FAMILIES:
            db += 10
        elif family in SHALLOW_LR_FAMILIES:
            db -= 6
        elif family in SPECIFIC_GRA_FAMILIES:
            db += 8
        elif family in BROAD_GRA_FAMILIES:
            db -= 3

        score += base + sb + lb + db

    # Positive collocation boost: if quote matches a known collocation, COLLOCATION wins
    if family == "COLLOCATION":
        for c in candidates_with_family:
            q = normalize_space(c.quote).lower()
            if q in resources.positive_collocations:
                score += 20  # strong boost for confirmed collocate pattern
                break

    # Morphology registry boost for WORD_FORM / VERB_FORM
    if family in {"WORD_FORM", "VERB_TENSE", "VERB_FORM"}:
        for c in candidates_with_family:
            q_words = words(c.quote)
            for w in q_words:
                if w.lower() in resources.form_to_lemma:
                    score += 15  # morphology-backed evidence
                    break

    # ── C3 v11: Resource confirmation bonuses ──────────────────────────────
    # These are the highest-weight signals: resource evidence beats LLM opinion.
    resource_bonus = 0.0

    # 1. Morphology confirms verb/word form → VERB_FORM / WORD_FORM decisive
    if family in {"VERB_FORM", "WORD_FORM", "VERB_TENSE"}:
        for c in candidates_with_family:
            q_words = [w.lower() for w in words(c.quote)]
            for w in q_words:
                if w in resources.form_to_lemma:
                    resource_bonus += 20.0
                    break
            # Extra: have/has/had + wrong form pattern
            if re.search(r'\b(have|has|had)\s+\w+(ed|en)\b', c.quote.lower()):
                resource_bonus += 10.0
            if resource_bonus > 0:
                break

    # 2. Preposition governance registry confirms prep mismatch
    if family == "PREPOSITION_PATTERN":
        for c in candidates_with_family:
            q_words_set = {w.lower() for w in words(c.quote)}
            if hasattr(resources, 'governed_prepositions') and resources.governed_prepositions:
                for governor in resources.governed_prepositions:
                    if governor in q_words_set:
                        resource_bonus += 18.0
                        break
            if resource_bonus > 0:
                break

    # 3. Positive collocation registry: headword present → COLLOCATION bonus
    if family == "COLLOCATION":
        for c in candidates_with_family:
            q_words_set = {w.lower() for w in words(c.quote) if len(w) > 2}
            for hw in resources.collocation_index:
                if hw in q_words_set:
                    resource_bonus += 15.0
                    break
            if resource_bonus > 0:
                break

    # 4. Contraction in quote → REGISTER confirmed
    if family == "REGISTER":
        for c in candidates_with_family:
            if re.search(
                r"\b(can't|won't|wouldn't|couldn't|shouldn't|don't|doesn't|didn't|"
                r"haven't|hasn't|I'm|I've|I'll|it's|they're|we're|that's|isn't|aren't)\b",
                c.quote, re.I
            ):
                resource_bonus += 22.0
                break

    # 5. LanguageTool rule ID confirms specific family
    for c in candidates_with_family:
        lt_rule = (c.raw_evidence or {}).get("lt_rule_id", "")
        if lt_rule:
            if "MORFOLOGIK" in lt_rule.upper() and family == "SPELLING":
                resource_bonus += 20.0
            elif "AGREEMENT" in lt_rule.upper() and family == "SUBJECT_VERB_AGREEMENT":
                resource_bonus += 18.0
            elif "COMPARATIVE" in lt_rule.upper() and family == "COMPARATIVE_FORM":
                resource_bonus += 20.0
            elif "POSSESSIVE" in lt_rule.upper() and family == "POSSESSIVE_FORM":
                resource_bonus += 15.0

    score += resource_bonus
    return score


def select_root_family(cluster: List[Candidate], resources: "ResourceBundle") -> Tuple[str, Dict[str, float]]:
    """
    Select root family for a cluster using scored evidence.
    Returns (root_family, {family: score}) dict.
    """
    # Collect all families proposed in this cluster
    family_candidates: Dict[str, List[Candidate]] = {}
    for c in cluster:
        fam = c.family_candidate
        if fam in FAMILY_TO_RUBRIC:
            family_candidates.setdefault(fam, []).append(c)

    if not family_candidates:
        return "UNKNOWN_FAMILY", {}

    # Score each family
    scores: Dict[str, float] = {}
    for fam, cands in family_candidates.items():
        scores[fam] = score_family_in_cluster(fam, cands, cluster, resources)

    # Root = highest score
    root = max(scores, key=lambda f: scores[f])
    root_score = scores[root]

    # Same-repair GRA/LR conflict resolution within 15% of each other
    rubric_of_root = FAMILY_TO_RUBRIC.get(root, "")
    if rubric_of_root in {"grammar", "lexical_resource"}:
        other_rubric = "lexical_resource" if rubric_of_root == "grammar" else "grammar"
        competing = [(f, s) for f, s in scores.items()
                     if FAMILY_TO_RUBRIC.get(f) == other_rubric
                     and s >= root_score * 0.85]
        if competing:
            competitor, comp_score = max(competing, key=lambda x: x[1])
            # Tie-break: morphological evidence → GRA wins; semantic/lexical → LR wins
            has_morph = any(
                w.lower() in resources.form_to_lemma
                for c in cluster for w in words(c.quote)
            )
            if has_morph and rubric_of_root == "grammar":
                pass  # GRA root wins
            elif has_morph and rubric_of_root == "lexical_resource":
                root = competitor  # GRA competitor wins
                root_score = comp_score
            else:
                pass  # LR root wins by default if no morph evidence

    # ── C4 v11: Operation → family coherence override ─────────────────────
    # If root is a generic family but the best candidate's repair operation
    # maps definitively to a specific family present in the cluster, override.
    OPERATION_FAMILY_OVERRIDE = {
        "FIX_PREPOSITION_PATTERN":   "PREPOSITION_PATTERN",
        "FIX_COLLOCATION":           "COLLOCATION",
        "FIX_SEMANTIC_COMBINATION":  "SEMANTIC_COMBINATION",
        "FIX_SVA":                   "SUBJECT_VERB_AGREEMENT",
        "CHANGE_VERB_FORM":          "VERB_FORM",
        "CHANGE_VERB_TENSE":         "VERB_TENSE",
        "FIX_VERB_PATTERN":          "VERB_PATTERN",
        "CHANGE_NOUN_NUMBER":        "NOUN_NUMBER_COUNTABILITY",
        "REPLACE_ARTICLE":           "ARTICLE_DETERMINER",
        "FIX_COMPARATIVE_FORM":      "COMPARATIVE_FORM",
        "FIX_SPELLING":              "SPELLING",
        "FIX_WORD_FORM":             "WORD_FORM",
        "FIX_REGISTER":              "REGISTER",
        "FIX_CONDITIONAL_STRUCTURE": "CONDITIONAL_STRUCTURE",
        "CHANGE_PRONOUN_CASE":       "PRONOUN_CASE",
        "FIX_POSSESSIVE_FORM":       "POSSESSIVE_FORM",
        "FIX_PARALLELISM":           "PARALLELISM",
        "REPLACE_PREPOSITION":       "PREPOSITION_PATTERN",
        "REPLACE_WORD_CHOICE":       "WORD_CHOICE",
    }
    C4_GENERIC_FAMILIES = {"CLAUSE_STRUCTURE", "CONSTRUCTION", "FRAGMENT", "RUN_ON", "UNKNOWN_FAMILY"}

    if root in C4_GENERIC_FAMILIES:
        # Find best candidate by confidence
        best_cand = max(cluster, key=lambda c: c.confidence, default=None)
        if best_cand:
            op = (best_cand.repair_operation or "").upper().strip()
            op_family = OPERATION_FAMILY_OVERRIDE.get(op)
            if op_family and op_family in family_candidates and op_family != root:
                root = op_family
                root_score = scores.get(root, root_score)

    return root, scores


# ---------------------------------------------------------------------------
# V9.3 arbitration — single authoritative version
# ---------------------------------------------------------------------------
V93_PATCH_ACTIVE = True
V93_POLICY = {
    "response_mode": "full_only",
    "rooting": "specific_validated_family_priority_not_recoverability_score",
    "recoverability_role": "severity_only",
    "family_source_priority": ["rule_id", "specific_LT_message", "specific_LLM_family_after_QA", "family_lock_registry_v2"],
    "suppression_policy": "suppress_only_invalid_quote_clear_FP_duplicate; advisory_kept_visible",
    "layer_isolation": "local_language_preempts_sentence_TR_CC_chargeability_but_preserves_advisory_rows",
}

LOCAL_RUBRICS_V93 = {"grammar", "lexical_resource"}
DISCOURSE_RUBRICS_V93 = {"coherence_cohesion", "task_response"}
INDEPENDENT_LOCAL_FAMILIES_V93 = {"SPELLING", "WORD_FORM", "GRAMMAR_PUNCTUATION"}

# FIX 4: V93_FAMILY_SPECIFICITY — all 17 missing entries added
V93_FAMILY_SPECIFICITY = {
    "SPELLING": 98,
    "SUBJECT_VERB_AGREEMENT": 96,
    "VERB_FORM": 95,
    "VERB_TENSE": 94,
    "VERB_PATTERN": 93,
    "PREPOSITION_PATTERN": 92,
    "ARTICLE_DETERMINER": 91,
    "NOUN_NUMBER_COUNTABILITY": 90,
    "PRONOUN_CASE": 89,
    "PRONOUN_AGREEMENT": 88,
    "POSSESSIVE_FORM": 87,
    "COMPARATIVE_FORM": 86,
    "ADJECTIVE_ADVERB_FORM": 85,
    "CONDITIONAL_STRUCTURE": 84,
    "WORD_FORM": 83,
    "PARALLELISM": 84,
    "REDUNDANCY": 72,
    "REGISTER": 70,
    "REPETITION": 68,
    "COLLOCATION": 78,
    "SEMANTIC_COMBINATION": 77,
    "WORD_CHOICE": 74,
    "LEXICAL_PRECISION": 72,
    "CLAUSE_STRUCTURE": 70,
    "CONSTRUCTION": 68,
    "WORD_ORDER": 66,
    "RUN_ON": 64,
    "FRAGMENT": 63,
    "GRAMMAR_PUNCTUATION": 58,
    # GRAMMATICAL_RANGE removed in v10
    "QUANTIFIER_USAGE": 80,
    "REFERENCE_COHESION": 45,
    "REFERENCE_BREAK": 45,
    "TRANSITION": 44,
    "MISSING_TRANSITION": 44,
    "LOGICAL_PROGRESSION": 42,
    "PARAGRAPH_STRUCTURE": 42,
    "TOPIC_CONTINUITY": 43,
    "TOPIC_SHIFT": 43,
    "CHAIN_BREAK": 43,
    "MECHANICAL_COHESION": 40,
    "EXAMPLE_INTEGRATION": 45,
    "TASK_COMPLETENESS": 40,
    "PROMPT_COVERAGE": 40,
    "PROMPT_RELEVANCE": 40,
    "POSITION_RESPONSE": 40,
    "POSITION_CLARITY": 40,
    "UNSUPPORTED_CLAIM": 38,
    "WEAK_EXAMPLE": 38,
    "REASONING_CHAIN": 38,
    "CLAIM_SUPPORT_LINK": 38,
    "INCOMPLETE_ARGUMENT": 42,
    "COUNTERARGUMENT_BALANCE": 41,
    "CIRCULAR_REASONING": 40,
    "OFF_TOPIC": 39,
    "GENRE_MISMATCH": 39,
    "OVERGENERALIZATION": 38,
}

GENERIC_OPERATIONS_V93 = {"REPLACE_WORD", "INSERT_WORD", "DELETE_WORD", "UNKNOWN_REPAIR", "NONE", "REPLACE_PHRASE", "CHANGE_FORM"}

# FIX 9: V93_FAMILY_TO_OPERATION — all missing entries added
V93_FAMILY_TO_OPERATION: Dict[str, str] = {}
V93_FAMILY_TO_OPERATION.update(FAMILY_TO_V92_OPERATION)
V93_FAMILY_TO_OPERATION.update({
    "SPELLING": "FIX_SPELLING",
    "WORD_FORM": "FIX_WORD_FORM",
    "WORD_CHOICE": "REPLACE_WORD_CHOICE",
    "LEXICAL_PRECISION": "IMPROVE_PRECISION",
    "COLLOCATION": "FIX_COLLOCATION",
    "SEMANTIC_COMBINATION": "FIX_SEMANTIC_COMBINATION",
    "ARTICLE_DETERMINER": "REPLACE_ARTICLE",
    "NOUN_NUMBER_COUNTABILITY": "CHANGE_NOUN_NUMBER",
    "SUBJECT_VERB_AGREEMENT": "FIX_SVA",
    "VERB_FORM": "CHANGE_VERB_FORM",
    "VERB_TENSE": "CHANGE_VERB_TENSE",
    "VERB_PATTERN": "FIX_VERB_PATTERN",
    "PREPOSITION_PATTERN": "FIX_PREPOSITION_PATTERN",
    "PRONOUN_CASE": "CHANGE_PRONOUN_CASE",
    "PRONOUN_AGREEMENT": "FIX_PRONOUN_AGREEMENT",
    "POSSESSIVE_FORM": "FIX_POSSESSIVE_FORM",
    "COMPARATIVE_FORM": "FIX_COMPARATIVE_FORM",
    "GRAMMAR_PUNCTUATION": "FIX_PUNCTUATION",
    # FIX 9 additions (GRAMMATICAL_RANGE removed in v10):
    "REPETITION": "REDUCE_REPETITION",
    "QUANTIFIER_USAGE": "FIX_QUANTIFIER_USAGE",
    "TOPIC_CONTINUITY": "REORDER_IDEAS",
    "TOPIC_SHIFT": "REORDER_IDEAS",
    "CHAIN_BREAK": "FIX_REFERENCE",
    "MECHANICAL_COHESION": "FIX_TRANSITION",
    "CIRCULAR_REASONING": "ADD_EXPLANATION",
    "OFF_TOPIC": "ADD_TOPIC_DEVELOPMENT",
    "GENRE_MISMATCH": "ADD_TOPIC_DEVELOPMENT",
    "OVERGENERALIZATION": "ADD_SUPPORT",
    "POSITION_RESPONSE": "ADD_POSITION",
    "PROMPT_RELEVANCE": "ADD_TOPIC_DEVELOPMENT",
    "REASONING_CHAIN": "ADD_EXPLANATION",
    "PARALLELISM": "FIX_PARALLELISM",
    "WORD_ORDER": "FIX_WORD_ORDER",
    "CONDITIONAL_STRUCTURE": "FIX_CONDITIONAL_STRUCTURE",
    "ADJECTIVE_ADVERB_FORM": "FIX_ADJECTIVE_ADVERB_FORM",
})

PREPOSITIONS_V93 = set("about above across after against along among around as at before behind below beneath beside between beyond by despite during except for from in inside into like near of off on onto outside over past regarding since through throughout to toward towards under until up upon via with within without".split())
MODALS_AUX_V93 = set("am is are was were be been being have has had do does did will would can could should may might must to".split())
ARTICLES_V93 = {"a", "an", "the"}
PRONOUN_FORMS_V93 = set("i me my mine you your yours he him his she her hers it its we us our ours they them their theirs this that these those".split())

def _v93_add_history(obj: Any, stage: str, decision: str, reason: str = "", extra: Optional[Dict[str, Any]] = None) -> None:
    try:
        ev = getattr(obj, "raw_evidence", None)
        if ev is None:
            return
        hist = ev.setdefault("candidate_history", [])
        item = {"stage": stage, "decision": decision, "reason": reason}
        if extra:
            item.update(extra)
        hist.append(item)
    except Exception:
        pass

def _v93_quote_exists(c: Candidate) -> Tuple[bool, str]:
    q = normalize_space(c.quote)
    local = normalize_space(c.local_quote)
    if not q:
        return False, "empty_quote"
    if q.lower() in {"whole response", "entire response", "position / thesis", "response length"}:
        return True, "document_marker_quote"
    if len(q.strip()) <= 1:
        return False, "one_character_quote"
    if local and (q.lower() in local.lower() or local.lower() in q.lower()):
        return True, "quote_found_in_local_context"
    qw = {w.lower() for w in words(q) if len(w) > 2}
    lw = {w.lower() for w in words(local) if len(w) > 2}
    if qw and lw and (qw & lw):
        return True, "quote_word_overlap_local_context"
    return False, "quote_not_found_in_local_context"


# FIX 5: _v93_quote_issue_compatibility — extended with CC/TR/grammar family branches
def _v93_quote_issue_compatibility(c: Candidate) -> Tuple[bool, List[str]]:
    fam = str(c.family_candidate or "").upper()
    q = normalize_space(c.quote)
    ql = q.lower()
    toks = [w.lower() for w in words(q)]
    if fam not in FAMILY_TO_RUBRIC:
        return False, ["unknown_family"]
    if fam == "SPELLING":
        if len(toks) == 1 and len(toks[0]) >= 3:
            return True, ["single_token_spelling_quote"]
        return False, ["spelling_requires_single_word_quote"]
    if fam == "SUBJECT_VERB_AGREEMENT":
        if any(w in ql for w in ["this ", "that ", "there ", "he ", "she ", "it ", "they ", "government", "people"]) or len(toks) >= 2:
            return True, ["sva_subject_verb_like_quote"]
        return False, ["sva_requires_subject_verb_context"]
    if fam in {"VERB_FORM", "VERB_TENSE", "VERB_PATTERN"}:
        if any(w in toks for w in MODALS_AUX_V93) or re.search(r"\b(for|to)\s+[a-z]+\b", ql) or re.search(r"\b[a-z]+(ed|ing)\b", ql):
            return True, ["verb_frame_like_quote"]
        return False, ["verb_family_requires_verb_or_aux_context"]
    if fam == "PREPOSITION_PATTERN":
        if any(w in PREPOSITIONS_V93 for w in toks):
            return True, ["preposition_present_in_quote"]
        return False, ["preposition_family_requires_preposition_quote"]
    if fam == "ARTICLE_DETERMINER":
        if any(w in ARTICLES_V93 for w in toks) or len(toks) <= 4:
            return True, ["article_or_np_quote"]
        return False, ["article_family_requires_article_or_short_np"]
    if fam == "NOUN_NUMBER_COUNTABILITY":
        if toks:
            return True, ["noun_number_quote_nonempty"]
        return False, ["noun_number_requires_nominal_quote"]
    if fam in {"PRONOUN_CASE", "PRONOUN_AGREEMENT", "POSSESSIVE_FORM"}:
        if any(w in PRONOUN_FORMS_V93 for w in toks):
            return True, ["pronoun_present_in_quote"]
        return False, ["pronoun_family_requires_pronoun_quote"]
    if fam in {"GRAMMAR_PUNCTUATION", "RUN_ON", "FRAGMENT"}:
        if any(ch in q for ch in ",.;:!?/") or len(toks) >= 2:
            return True, ["punctuation_or_boundary_context"]
        return False, ["punctuation_family_requires_punctuation_or_clause_context"]
    if fam in {"COLLOCATION", "SEMANTIC_COMBINATION", "WORD_CHOICE", "LEXICAL_PRECISION", "REGISTER", "REDUNDANCY", "REPETITION", "WORD_FORM"}:
        if len(toks) >= 1:
            return True, ["lexical_quote_nonempty"]
        return False, ["lexical_family_requires_word_quote"]
    # GRAMMATICAL_RANGE removed in v10 — it cannot appear in candidates
    # FIX 5: PARALLELISM — requires list-like or coordinated structure
    if fam == "PARALLELISM":
        if re.search(r'\band\b|\bor\b|\bnor\b|\bboth\b|\beither\b', ql) and len(toks) >= 3:
            return True, ["coordinated_structure_for_parallelism"]
        return False, ["parallelism_requires_coordinated_structure"]
    # FIX 5: QUANTIFIER_USAGE — requires quantifier word in quote
    QUANTIFIERS = {"few", "little", "much", "many", "some", "any", "several", "plenty", "lot", "lots", "amount", "number", "majority", "minority", "each", "every", "all", "both", "neither", "enough", "less", "fewer", "more", "most", "least"}
    if fam == "QUANTIFIER_USAGE":
        if any(w in QUANTIFIERS for w in toks):
            return True, ["quantifier_present_in_quote"]
        return False, ["quantifier_family_requires_quantifier_word"]
    # FIX 5: WORD_ORDER — requires at least a phrase of 2+ content words
    if fam == "WORD_ORDER":
        content_toks = [t for t in toks if t not in V92_FUNCTION_WORDS and len(t) > 2]
        if len(content_toks) >= 1 and len(toks) >= 2:
            return True, ["word_order_phrase_context"]
        return False, ["word_order_requires_phrase_context"]
    # FIX 5: CONDITIONAL_STRUCTURE — requires conditional marker
    if fam == "CONDITIONAL_STRUCTURE":
        if re.search(r'\b(if|unless|were|would|could|should|provided|assuming|given that)\b', ql):
            return True, ["conditional_marker_in_quote"]
        return False, ["conditional_structure_requires_conditional_marker"]
    # FIX 5: ADJECTIVE_ADVERB_FORM — requires adjective or adverb token
    if fam == "ADJECTIVE_ADVERB_FORM":
        if re.search(r'\b\w+(ly|er|est|ful|less|ous|ive|al|ic)\b', ql) or len(toks) >= 2:
            return True, ["adjective_adverb_form_context"]
        return False, ["adjective_adverb_requires_modifier_context"]
    # FIX 5: CC families — need a phrase or sentence, not a single word
    CC_DETECTABLE = {"TRANSITION", "MISSING_TRANSITION", "LOGICAL_PROGRESSION", "REFERENCE_COHESION",
        "REFERENCE_BREAK", "PARAGRAPH_STRUCTURE", "TOPIC_CONTINUITY", "TOPIC_SHIFT",
        "CHAIN_BREAK", "MECHANICAL_COHESION", "EXAMPLE_INTEGRATION"}
    if fam in CC_DETECTABLE:
        if len(toks) >= 2 or c.layer in {"layer1_wide_discourse", "layer2_sentence_discourse"}:
            return True, ["cc_family_phrase_or_discourse_context"]
        return False, ["cc_family_requires_phrase_or_discourse_context"]
    # FIX 5: TR families — work at sentence/clause/document level
    TR_DETECTABLE = {"PROMPT_COVERAGE", "PROMPT_RELEVANCE", "POSITION_RESPONSE", "TASK_COMPLETENESS",
        "OVERGENERALIZATION", "UNSUPPORTED_CLAIM", "WEAK_EXAMPLE", "CIRCULAR_REASONING", "OFF_TOPIC",
        "INCOMPLETE_ARGUMENT", "CLAIM_SUPPORT_LINK", "REASONING_CHAIN", "POSITION_CLARITY",
        "COUNTERARGUMENT_BALANCE", "GENRE_MISMATCH"}
    if fam in TR_DETECTABLE:
        if len(toks) >= 3 or c.layer == "layer1_wide_discourse":
            return True, ["tr_family_sentence_or_document_context"]
        return False, ["tr_family_requires_sentence_or_document_context"]
    # FIX 5: REDUNDANCY, REPETITION, REGISTER — nonempty quote
    if fam in {"REDUNDANCY", "REPETITION", "REGISTER"}:
        if len(toks) >= 1:
            return True, ["lexical_discourse_family_nonempty"]
        return False, ["lexical_discourse_requires_nonempty_quote"]
    # discourse / TR fallback
    if FAMILY_TO_RUBRIC.get(fam) in DISCOURSE_RUBRICS_V93:
        if len(toks) >= 3 or c.layer in {"layer1_wide_discourse", "layer2_sentence_discourse"}:
            return True, ["discourse_quote_context"]
        return False, ["discourse_family_requires_sentence_or_document_context"]
    return True, ["default_compatible"]

def _v93_repair_validity(c: Candidate) -> Tuple[bool, List[str]]:
    fam = str(c.family_candidate or "").upper()
    q = normalize_space(c.quote)
    r = normalize_space(c.repair_hypothesis)
    if not r:
        if c.source_engine in {"rules_va25_support", "rules_support", "LanguageTool_support", "spelling_registry_validator", "rules_registry"}:
            return True, ["repair_template_implied_by_rule_or_LT"]
        return False, ["missing_minimal_repair"]
    if fam in FAMILY_TO_RUBRIC and FAMILY_TO_RUBRIC.get(fam) in LOCAL_RUBRICS_V93:
        if len(r) > max(40, len(q) * 4) or len(words(r)) > max(8, len(words(q)) + 5):
            return False, ["local_repair_not_minimal_full_sentence_or_instruction"]
    if re.search(r"\b(rewrite|replace|change|use|should|need to)\b", r.lower()) and len(words(r)) > 6:
        return False, ["repair_is_instruction_not_repair_text"]
    return True, ["minimal_repair_ok"]

def _v93_specific_operation_for_candidate(c: Candidate) -> Tuple[str, str, List[str]]:
    fam = str(c.family_candidate or "").upper()
    old_op = normalise_v9_operation(c.repair_operation or c.operation, fam)
    reasons: List[str] = []
    if old_op in GENERIC_OPERATIONS_V93 or old_op.startswith("REPLACE_WORD"):
        new_op = V93_FAMILY_TO_OPERATION.get(fam, old_op)
        if new_op != old_op:
            reasons.append(f"generic_operation_replaced:{old_op}->{new_op}")
        old_op = new_op
    if fam in V93_FAMILY_TO_OPERATION and old_op in {"UNKNOWN_REPAIR", "REPLACE_WORD", "INSERT_WORD", "DELETE_WORD"}:
        old_op = V93_FAMILY_TO_OPERATION[fam]
        reasons.append(f"family_specific_operation_applied:{fam}->{old_op}")
    target = str(c.repair_target or "").lower().strip()
    if not target or target == "unspecified":
        target = {
            "SPELLING": "spelling", "WORD_FORM": "word_form_derivation", "WORD_CHOICE": "lexical_choice",
            "LEXICAL_PRECISION": "lexical_precision", "COLLOCATION": "collocation",
            "SEMANTIC_COMBINATION": "semantic_combination", "ARTICLE_DETERMINER": "article",
            "NOUN_NUMBER_COUNTABILITY": "noun_number_countability", "SUBJECT_VERB_AGREEMENT": "subject_verb_agreement",
            "VERB_FORM": "verb_form", "VERB_TENSE": "verb_tense", "VERB_PATTERN": "verb_pattern",
            "PREPOSITION_PATTERN": "preposition_pattern", "PRONOUN_CASE": "pronoun_case",
            "PRONOUN_AGREEMENT": "pronoun_agreement", "POSSESSIVE_FORM": "possessive_form",
            "COMPARATIVE_FORM": "comparative_form", "GRAMMAR_PUNCTUATION": "punctuation",
            "CLAUSE_STRUCTURE": "clause_structure", "CONSTRUCTION": "construction",
            # GRAMMATICAL_RANGE removed in v10
            "RUN_ON": "clause_boundary", "FRAGMENT": "clause_boundary",
            "TRANSITION": "transition", "MISSING_TRANSITION": "transition",
            "REFERENCE_COHESION": "reference", "REFERENCE_BREAK": "reference",
            "LOGICAL_PROGRESSION": "logical_progression", "PARAGRAPH_STRUCTURE": "paragraph_structure",
            "TASK_COMPLETENESS": "task_completeness", "PROMPT_COVERAGE": "task_completeness",
            "PROMPT_RELEVANCE": "task_completeness", "POSITION_RESPONSE": "position",
            "POSITION_CLARITY": "position", "UNSUPPORTED_CLAIM": "support",
            "WEAK_EXAMPLE": "example", "REASONING_CHAIN": "reasoning_chain",
            "CLAIM_SUPPORT_LINK": "reasoning_chain",
            # GRAMMATICAL_RANGE removed in v10
            "REPETITION": "repetition", "QUANTIFIER_USAGE": "quantifier_usage",
            "TOPIC_CONTINUITY": "topic_continuity", "TOPIC_SHIFT": "topic_shift",
            "CHAIN_BREAK": "chain_break", "MECHANICAL_COHESION": "mechanical_cohesion",
            "CIRCULAR_REASONING": "reasoning_chain", "OFF_TOPIC": "task_completeness",
            "GENRE_MISMATCH": "task_completeness", "OVERGENERALIZATION": "support",
            "INCOMPLETE_ARGUMENT": "reasoning_chain", "COUNTERARGUMENT_BALANCE": "counterargument",
            "PARALLELISM": "parallelism", "WORD_ORDER": "word_order",
            "CONDITIONAL_STRUCTURE": "conditional_structure", "ADJECTIVE_ADVERB_FORM": "adjective_adverb_form",
            "REGISTER": "register", "REDUNDANCY": "redundancy",
        }.get(fam, target or "unspecified")
        reasons.append(f"repair_target_inferred:{target}")
    return old_op, target, reasons

def _v93_lock_family_from_specific_candidate(c: Candidate, registries: DecisionRegistries) -> Tuple[Candidate, List[str], List[str]]:
    reasons: List[str] = []
    flags: List[str] = []
    fam0 = str(c.family_candidate or "UNKNOWN_FAMILY").upper().strip()
    quote_ok, quote_reason = _v93_quote_exists(c)
    _v93_add_history(c, "quote_validation", "passed" if quote_ok else "failed", quote_reason)
    if not quote_ok:
        flags.append("invalid_quote")
        reasons.append(quote_reason)
        return c, reasons, flags
    comp_ok, comp_reasons = _v93_quote_issue_compatibility(c)
    _v93_add_history(c, "quote_issue_compatibility", "passed" if comp_ok else "failed", ";".join(comp_reasons), {"family": fam0})
    if not comp_ok:
        flags.append("quote_issue_incompatible")
        reasons.extend(comp_reasons)
    repair_ok, repair_reasons = _v93_repair_validity(c)
    _v93_add_history(c, "issue_repair_compatibility", "passed" if repair_ok else "advisory", ";".join(repair_reasons))
    if not repair_ok:
        flags.append("repair_not_minimal_or_missing")
        reasons.extend(repair_reasons)
    op, target, op_reasons = _v93_specific_operation_for_candidate(c)
    c.repair_operation = op
    c.operation = op
    c.problem_axis = op
    c.repair_target = target
    reasons.extend(op_reasons)
    _v93_add_history(c, "repair_operation_derivation", "passed", ";".join(op_reasons) or "operation_specific")
    m = registries.mapping_by_op_target.get((op, target)) or registries.mapping_by_operation.get(op)
    if m:
        reg_fam = str(m.get("primary_family") or m.get("family") or "").upper()
        c.family_lock_status = "family_lock_registry_v2_confirmed" if reg_fam == fam0 else "family_lock_registry_v2_mismatch_preserved_candidate_family"
        c.family_lock_confidence = float(m.get("family_lock_confidence", 1.0) or 1.0)
        if reg_fam and reg_fam != fam0:
            if fam0 in FAMILY_TO_RUBRIC:
                if reg_fam not in c.secondary_families:
                    c.secondary_families.append(reg_fam)
                reasons.append(f"registry_family_mismatch_preserved_specific_candidate:{reg_fam}->{fam0}")
                flags.append("operation_family_registry_mismatch")
            else:
                c.family_candidate = reg_fam
                c.rubric_candidate = FAMILY_TO_RUBRIC.get(reg_fam, "unknown")
                reasons.append(f"invalid_candidate_family_replaced_by_registry:{fam0}->{reg_fam}")
        else:
            reasons.append("operation_family_registry_confirmed")
    else:
        c.family_lock_status = "no_exact_registry_lock_candidate_family_preserved"
        c.family_lock_confidence = 0.65 if fam0 in FAMILY_TO_RUBRIC else 0.0
        if fam0 not in FAMILY_TO_RUBRIC:
            flags.append("unmapped_family_lock")
            reasons.append("family_not_in_taxonomy_and_no_registry_lock")
    fam = str(c.family_candidate or fam0).upper()
    c.rubric_candidate = FAMILY_TO_RUBRIC.get(fam, c.rubric_candidate)
    _v93_add_history(c, "operation_family_compatibility", "passed" if fam in FAMILY_TO_RUBRIC else "failed", c.family_lock_status, {"operation": op, "repair_target": target, "family": fam})
    return c, reasons, flags

def _v93_candidate_dict(c: Candidate) -> Dict[str, Any]:
    d = asdict(c)
    d.setdefault("qa_chain", {})
    d["qa_chain"].update({
        "quote_exists": _v93_quote_exists(c)[0],
        "quote_issue_compatible": _v93_quote_issue_compatibility(c)[0],
        "repair_valid_minimal": _v93_repair_validity(c)[0],
        "operation_specific": c.operation not in GENERIC_OPERATIONS_V93,
        "family_source": c.family_lock_status,
        "routing_policy": "v9.3_specific_candidate_family_first",
    })
    d["candidate_history"] = d.get("raw_evidence", {}).get("candidate_history", [])
    return d

def _v93_row_dict(r: DiagnosticRow) -> Dict[str, Any]:
    d = asdict(r)
    d.setdefault("qa_chain", {})
    d["qa_chain"].update({
        "quote_issue_repair_operation_family_checked": True,
        "recoverability_used_for_routing": False,
        "recoverability_used_for_severity_only": True,
        "family_source": r.family_lock_status,
        "layer_contamination_guard": "local_first_discourse_advisory" if r.rubric in DISCOURSE_RUBRICS_V93 else "local_or_independent",
    })
    return d

def _v93_cluster_key(r: DiagnosticRow) -> Tuple[Any, ...]:
    return (r.essay_id, r.sentence_index, r.family, normalize_space(r.quote).lower())

def _v93_repair_unit_key(r: DiagnosticRow) -> Tuple[Any, ...]:
    return (r.essay_id, r.sentence_index, max(0, r.span_start // 8), max(0, r.span_end // 8))

def _v93_row_priority(r: DiagnosticRow) -> float:
    return V93_FAMILY_SPECIFICITY.get(r.family, 30) + r.confidence * 10 + (5 if "rules" in " ".join(r.source_engines).lower() else 0) + (3 if "language" in " ".join(r.source_engines).lower() else 0)



# ---------------------------------------------------------------------------
# v9.8 survival/arbitration patch
# ---------------------------------------------------------------------------
# Design decision after v9.7 audit:
# - LLM final judge has no deletion authority.
# - GRAMMATICAL_RANGE is not a primary local error family; it is a diagnostic/range signal.
# - family lock requires operation + repair_target, except deterministic rule_id authority.
# - generic operations are normalized before family locking.

GENERIC_OPERATIONS_V98 = GENERIC_OPERATIONS_V93 | {"CORRECT", "REPLACE", "SIMPLIFY", "IMPROVE", "REWRITE", "CHANGE"}
BANNED_PRIMARY_LOCAL_FAMILIES_V98 = {"GRAMMATICAL_RANGE", "MECHANICAL_COHESION", "GENRE_MISMATCH"}

V98_FAMILY_TO_OPERATION = {
    "SPELLING": "FIX_SPELLING",
    "WORD_FORM": "FIX_WORD_FORM",
    "WORD_CHOICE": "REPLACE_WORD_CHOICE",
    "LEXICAL_PRECISION": "IMPROVE_LEXICAL_PRECISION",
    "REGISTER": "FIX_REGISTER",
    "COLLOCATION": "FIX_COLLOCATION",
    "SEMANTIC_COMBINATION": "FIX_SEMANTIC_COMBINATION",
    "REDUNDANCY": "REMOVE_REDUNDANCY",
    "REPETITION": "REDUCE_REPETITION",
    "ARTICLE_DETERMINER": "REPLACE_ARTICLE",
    "NOUN_NUMBER_COUNTABILITY": "CHANGE_NOUN_NUMBER",
    "POSSESSIVE_FORM": "FIX_POSSESSIVE_FORM",
    "PRONOUN_CASE": "CHANGE_PRONOUN_CASE",
    "PRONOUN_AGREEMENT": "FIX_PRONOUN_AGREEMENT",
    "SUBJECT_VERB_AGREEMENT": "FIX_SVA",
    "VERB_FORM": "CHANGE_VERB_FORM",
    "VERB_TENSE": "CHANGE_VERB_TENSE",
    "VERB_PATTERN": "FIX_VERB_PATTERN",
    "PREPOSITION_PATTERN": "FIX_PREPOSITION_PATTERN",
    "ADJECTIVE_ADVERB_FORM": "FIX_ADJ_ADV_FORM",
    "COMPARATIVE_FORM": "FIX_COMPARATIVE_FORM",
    "PARALLELISM": "FIX_PARALLELISM",
    "CLAUSE_STRUCTURE": "REWRITE_CLAUSE",
    "CONSTRUCTION": "RESTRUCTURE_SENTENCE",
    "FRAGMENT": "FIX_FRAGMENT",
    "RUN_ON": "FIX_RUN_ON",
    "CONDITIONAL_STRUCTURE": "FIX_CONDITIONAL_STRUCTURE",
    "QUANTIFIER_USAGE": "FIX_QUANTIFIER_USAGE",
    "GRAMMAR_PUNCTUATION": "FIX_PUNCTUATION",
    "WORD_ORDER": "FIX_WORD_ORDER",
    "TRANSITION": "FIX_TRANSITION",
    "MISSING_TRANSITION": "ADD_TRANSITION",
    "LOGICAL_PROGRESSION": "FIX_LOGICAL_PROGRESSION",
    "REFERENCE_COHESION": "FIX_REFERENCE_COHESION",
    "REFERENCE_BREAK": "FIX_REFERENCE_BREAK",
    "PARAGRAPH_STRUCTURE": "RESTRUCTURE_PARAGRAPH",
    "TOPIC_CONTINUITY": "FIX_TOPIC_CONTINUITY",
    "TOPIC_SHIFT": "FIX_TOPIC_SHIFT",
    "CHAIN_BREAK": "FIX_CHAIN_BREAK",
    "EXAMPLE_INTEGRATION": "FIX_EXAMPLE_INTEGRATION",
    "PROMPT_COVERAGE": "FIX_PROMPT_COVERAGE",
    "PROMPT_RELEVANCE": "FIX_PROMPT_RELEVANCE",
    "POSITION_RESPONSE": "ADD_POSITION",
    "TASK_COMPLETENESS": "FIX_TASK_COMPLETENESS",
    "OVERGENERALIZATION": "FIX_OVERGENERALIZATION",
    "UNSUPPORTED_CLAIM": "ADD_SUPPORT",
    "WEAK_EXAMPLE": "ADD_EXAMPLE",
    "CIRCULAR_REASONING": "FIX_CIRCULAR_REASONING",
    "OFF_TOPIC": "FIX_OFF_TOPIC",
    "INCOMPLETE_ARGUMENT": "ADD_EXPLANATION",
    "CLAIM_SUPPORT_LINK": "FIX_CLAIM_SUPPORT_LINK",
    "REASONING_CHAIN": "FIX_REASONING_CHAIN",
    "POSITION_CLARITY": "CLARIFY_POSITION",
    "COUNTERARGUMENT_BALANCE": "BALANCE_COUNTERARGUMENT",
}

V98_FAMILY_TO_TARGET = {
    "SPELLING": "spelling",
    "WORD_FORM": "word_form_derivation",
    "WORD_CHOICE": "lexical_choice",
    "LEXICAL_PRECISION": "lexical_precision",
    "REGISTER": "register",
    "COLLOCATION": "collocation",
    "SEMANTIC_COMBINATION": "semantic_combination",
    "REDUNDANCY": "redundancy",
    "REPETITION": "repetition",
    "ARTICLE_DETERMINER": "article_determiner",
    "NOUN_NUMBER_COUNTABILITY": "noun_number_countability",
    "POSSESSIVE_FORM": "possessive_form",
    "PRONOUN_CASE": "pronoun_case",
    "PRONOUN_AGREEMENT": "pronoun_agreement",
    "SUBJECT_VERB_AGREEMENT": "subject_verb_agreement",
    "VERB_FORM": "verb_form",
    "VERB_TENSE": "verb_tense",
    "VERB_PATTERN": "verb_pattern",
    "PREPOSITION_PATTERN": "preposition_pattern",
    "ADJECTIVE_ADVERB_FORM": "adjective_adverb_form",
    "COMPARATIVE_FORM": "comparative_form",
    "PARALLELISM": "parallelism",
    "CLAUSE_STRUCTURE": "clause_structure",
    "CONSTRUCTION": "construction",
    "FRAGMENT": "fragment",
    "RUN_ON": "run_on",
    "CONDITIONAL_STRUCTURE": "conditional_structure",
    "QUANTIFIER_USAGE": "quantifier_usage",
    "GRAMMAR_PUNCTUATION": "grammar_punctuation",
    "WORD_ORDER": "word_order",
    "TRANSITION": "transition",
    "MISSING_TRANSITION": "missing_transition",
    "LOGICAL_PROGRESSION": "logical_progression",
    "REFERENCE_COHESION": "reference_cohesion",
    "REFERENCE_BREAK": "reference_break",
    "PARAGRAPH_STRUCTURE": "paragraph_structure",
    "TOPIC_CONTINUITY": "topic_continuity",
    "TOPIC_SHIFT": "topic_shift",
    "CHAIN_BREAK": "chain_break",
    "EXAMPLE_INTEGRATION": "example_integration",
    "PROMPT_COVERAGE": "prompt_coverage",
    "PROMPT_RELEVANCE": "prompt_relevance",
    "POSITION_RESPONSE": "position_response",
    "TASK_COMPLETENESS": "task_completeness",
    "OVERGENERALIZATION": "overgeneralization",
    "UNSUPPORTED_CLAIM": "unsupported_claim",
    "WEAK_EXAMPLE": "weak_example",
    "CIRCULAR_REASONING": "circular_reasoning",
    "OFF_TOPIC": "off_topic",
    "INCOMPLETE_ARGUMENT": "incomplete_argument",
    "CLAIM_SUPPORT_LINK": "claim_support_link",
    "REASONING_CHAIN": "reasoning_chain",
    "POSITION_CLARITY": "position_clarity",
    "COUNTERARGUMENT_BALANCE": "counterargument_balance",
}


def _v98_deterministic_rule_id(c: Candidate) -> Optional[str]:
    ev = c.raw_evidence or {}
    rid = ev.get("rule_id")
    if rid:
        return str(rid)
    if isinstance(ev.get("llm_item"), dict):
        return None
    return None


def _v98_family_from_surface(c: Candidate) -> Tuple[str, List[str]]:
    """Small universal rescue for generic LLM local rows; not essay-specific."""
    q = normalize_space(c.quote).lower()
    r = normalize_space(c.repair_hypothesis).lower()
    text = " ".join([q, r, str(c.problem_statement or "").lower(), str(c.explanation or "").lower()])
    reasons: List[str] = []
    fam = str(c.family_candidate or "").upper()

    # Do not allow broad local GRAMMATICAL_RANGE as primary.
    if fam == "GRAMMATICAL_RANGE" and c.layer == "layer3_local_language":
        reasons.append("v98_demote_grammatical_range_as_local_primary")
        fam = "CLAUSE_STRUCTURE"

    # Clear local patterns override vague LLM family.
    if re.search(r"\bhas\s+to\s+\w+ed\b", text):
        return "VERB_FORM", reasons + ["v98_surface_has_to_base_verb"]
    if re.search(r"\b(a|an)\s+\w+s\b", q) or re.search(r"\ba\s+children\b", q):
        return "ARTICLE_DETERMINER", reasons + ["v98_surface_article_plural_mismatch"]
    if re.search(r"\b(more|most|so)\s+\w+er\b", q) or re.search(r"\ba\s+lot\s+of\b.*\bthan\b", q):
        return "COMPARATIVE_FORM", reasons + ["v98_surface_comparative_frame"]
    if re.search(r"\bfor\s+\w+\b", q) and re.search(r"\b(to\s+\w+|for\s+\w+ing|for\s+(a|the)?\s*\w+)\b", r):
        return "VERB_PATTERN", reasons + ["v98_surface_for_base_verb_pattern"]
    if re.search(r"\b(they|you|we|he|she)\s+\w+\b", q) and any(x in text for x in ["their", "your", "our", "his", "her", "possessive"]):
        return "POSSESSIVE_FORM", reasons + ["v98_surface_pronoun_possessive"]
    if re.search(r"\b(this|that|it|government)\s+\w+\b", q) and "agreement" in text:
        return "SUBJECT_VERB_AGREEMENT", reasons + ["v98_surface_sva"]
    if any(x in q for x in ["it possible", "will ill", "the way be", "be help"]):
        return "CLAUSE_STRUCTURE", reasons + ["v98_surface_missing_predicate_or_copula"]
    if "good ability" in q or "make ability" in q:
        return "COLLOCATION", reasons + ["v98_surface_nonexistent_collocation"]
    if "spelling" in text and len(words(c.quote)) <= 3:
        return "SPELLING", reasons + ["v98_surface_spelling"]
    return fam, reasons


def _v93_specific_operation_for_candidate(c: Candidate) -> Tuple[str, str, List[str]]:  # type: ignore[no-redef]
    fam, family_reasons = _v98_family_from_surface(c)
    if fam and fam != str(c.family_candidate or "").upper():
        old_fam = str(c.family_candidate or "").upper()
        if old_fam and old_fam not in c.secondary_families:
            c.secondary_families.append(old_fam)
        c.family_candidate = fam
        c.rubric_candidate = FAMILY_TO_RUBRIC.get(fam, c.rubric_candidate)
    old_op = normalise_v9_operation(c.repair_operation or c.operation, fam)
    reasons: List[str] = list(family_reasons)
    if old_op in GENERIC_OPERATIONS_V98 or old_op.startswith("REPLACE_WORD") or fam == "GRAMMATICAL_RANGE":
        new_op = V98_FAMILY_TO_OPERATION.get(fam, old_op)
        if new_op != old_op:
            reasons.append(f"v98_generic_operation_replaced:{old_op}->{new_op}")
        old_op = new_op
    target = str(c.repair_target or "").lower().strip()
    if not target or target in {"unspecified", "unknown", "none", "null", "article", "punctuation", "position", "support", "example", "reference"}:
        target = V98_FAMILY_TO_TARGET.get(fam, target or "unspecified")
        reasons.append(f"v98_repair_target_inferred:{target}")
    return old_op, target, reasons


def _v93_lock_family_from_specific_candidate(c: Candidate, registries: DecisionRegistries) -> Tuple[Candidate, List[str], List[str]]:  # type: ignore[no-redef]
    reasons: List[str] = []
    flags: List[str] = []
    fam0 = str(c.family_candidate or "UNKNOWN_FAMILY").upper().strip()

    quote_ok, quote_reason = _v93_quote_exists(c)
    _v93_add_history(c, "quote_validation", "passed" if quote_ok else "failed", quote_reason)
    if not quote_ok:
        flags.append("invalid_quote")
        reasons.append(quote_reason)
        return c, reasons, flags

    # Rescue/derive family + precise operation/target first.
    op, target, op_reasons = _v93_specific_operation_for_candidate(c)
    c.repair_operation = op
    c.operation = op
    c.problem_axis = op
    c.repair_target = target
    reasons.extend(op_reasons)
    fam0 = str(c.family_candidate or fam0).upper().strip()
    _v93_add_history(c, "repair_operation_derivation", "passed", ";".join(op_reasons) or "v98_operation_specific")

    # Family lock policy v2: exact operation+target is authoritative; operation-only is not a hard lock.
    m_exact = registries.mapping_by_op_target.get((op, target))
    m_op_only = registries.mapping_by_operation.get(op)
    m = m_exact
    if m:
        reg_fam = str(m.get("primary_family") or m.get("family") or "").upper()
        if reg_fam and reg_fam != fam0:
            if fam0 and fam0 not in c.secondary_families:
                c.secondary_families.append(fam0)
            c.family_candidate = reg_fam
            c.rubric_candidate = FAMILY_TO_RUBRIC.get(reg_fam, c.rubric_candidate)
            reasons.append(f"v98_registry_exact_lock_overrode_candidate_family:{fam0}->{reg_fam}")
        else:
            reasons.append("v98_registry_exact_lock_confirmed")
        c.family_lock_status = "family_lock_registry_v2_exact_operation_target"
        c.family_lock_confidence = float(m.get("family_lock_confidence", 1.0) or 1.0)
    elif m_op_only:
        # operation-only is useful evidence, but not enough for chargeability by itself.
        reg_fam = str(m_op_only.get("primary_family") or m_op_only.get("family") or "").upper()
        c.family_lock_status = "operation_only_registry_match_review_needed"
        c.family_lock_confidence = 0.55
        flags.append("operation_only_lock_not_authoritative")
        reasons.append(f"v98_operation_only_match_not_hard_lock:{op}->{reg_fam}")
    else:
        c.family_lock_status = "no_v98_registry_lock_review_needed"
        c.family_lock_confidence = 0.35 if fam0 in FAMILY_TO_RUBRIC else 0.0
        flags.append("no_exact_family_lock")
        reasons.append("v98_no_exact_operation_target_lock")

    # GRAMMATICAL_RANGE must not be primary for L3 local rows.
    if c.layer == "layer3_local_language" and str(c.family_candidate or "").upper() in BANNED_PRIMARY_LOCAL_FAMILIES_V98:
        old = str(c.family_candidate).upper()
        c.family_candidate = "CLAUSE_STRUCTURE"
        c.rubric_candidate = "grammar"
        if old not in c.secondary_families:
            c.secondary_families.append(old)
        flags.append("v98_banned_primary_local_family_demoted")
        reasons.append(f"v98_banned_primary_local_family:{old}->CLAUSE_STRUCTURE")

    comp_ok, comp_reasons = _v93_quote_issue_compatibility(c)
    _v93_add_history(c, "quote_issue_compatibility", "passed" if comp_ok else "review", ";".join(comp_reasons), {"family": c.family_candidate})
    if not comp_ok:
        flags.append("quote_issue_incompatible")
        reasons.extend(comp_reasons)

    repair_ok, repair_reasons = _v93_repair_validity(c)
    _v93_add_history(c, "issue_repair_compatibility", "passed" if repair_ok else "review", ";".join(repair_reasons))
    if not repair_ok:
        flags.append("repair_not_minimal_or_missing")
        reasons.extend(repair_reasons)

    fam = str(c.family_candidate or fam0).upper()
    c.rubric_candidate = FAMILY_TO_RUBRIC.get(fam, c.rubric_candidate)
    _v93_add_history(c, "operation_family_compatibility", "passed" if fam in FAMILY_TO_RUBRIC else "failed", c.family_lock_status, {"operation": op, "repair_target": target, "family": fam})
    return c, reasons, flags

def arbitrate(raw: List[Candidate], resources: ResourceBundle, registries: DecisionRegistries, tracker: LLMTracker, llm_enabled: bool) -> Dict[str, Any]:
    """
    v9.8 survival-policy arbitration.

    Pipeline:
    Stage 2: Quote validation (hard)
    Stage 3: Layer-rubric hard gate (hard)
    Stage 4: Span-proximity clustering
    Stage 5: Root family selection via evidence scoring
    Stage 6: Chargeability gate (strict three tiers)
    Stage 7: non-destructive LLM audit only
    """
    suppressed_invalid_quote: List[DiagnosticRow] = []
    suppressed_layer_rubric: List[DiagnosticRow] = []
    suppressed_fp: List[DiagnosticRow] = []
    review_only: List[DiagnosticRow] = []
    chargeable: List[DiagnosticRow] = []

    # ── Stage 2: Quote validation ──────────────────────────────────────
    quote_valid: List[Candidate] = []
    for c in raw:
        if not meaningful_quote(c.quote):
            suppressed_invalid_quote.append(
                row_from_candidate(c, "suppressed_invalid_quote", ["stage2_quote_empty_or_single_char"], [], False, registries.severity_framework))
            continue
        # L3: word-overlap quote validation (v10: replaces strict verbatim match)
        if c.layer == "layer3_local_language":
            q_norm = normalize_space(c.quote).lower()
            lq_norm = normalize_space(c.local_quote or "").lower()
            WHOLE_ESSAY_MARKERS = {"whole essay", "entire response", "entire essay", "whole response"}
            if q_norm in WHOLE_ESSAY_MARKERS:
                pass  # always valid
            elif lq_norm and q_norm not in lq_norm:
                # Word-overlap fallback: >=50% of content words in quote must appear in local_quote
                q_words = [w for w in words(q_norm) if len(w) > 2]
                lq_words = set(words(lq_norm))
                if q_words and sum(1 for w in q_words if w in lq_words) / len(q_words) < 0.50:
                    suppressed_invalid_quote.append(
                        row_from_candidate(c, "suppressed_invalid_quote", ["stage2_l3_quote_insufficient_word_overlap"], [], False, registries.severity_framework))
                    continue
                # else: enough word overlap — allow through
        # L1/L2: sentence/paragraph span or whole-essay marker
        if c.layer in {"layer1_wide_discourse", "layer2_sentence_discourse"}:
            q_norm_d = normalize_space(c.quote).lower()
            WHOLE_ESSAY_MARKERS_D = {"whole essay", "entire response", "entire essay", "whole response", "(whole essay)"}
            if q_norm_d in WHOLE_ESSAY_MARKERS_D or "whole" in q_norm_d:
                pass  # valid whole-essay quote
            elif not c.quote and not c.local_quote:
                suppressed_invalid_quote.append(
                    row_from_candidate(c, "suppressed_invalid_quote", ["stage2_discourse_empty_quote"], [], False, registries.severity_framework))
                continue
        quote_valid.append(c)

    # ── Stage 3: Layer-rubric hard gate ───────────────────────────────
    layer_rubric_valid: List[Candidate] = []
    for c in quote_valid:
        fam = c.family_candidate
        rubric = FAMILY_TO_RUBRIC.get(fam, "unknown")
        layer = c.layer

        # L3 cannot produce CC/TR (v10: no GRAMMATICAL_RANGE exception)
        if layer == "layer3_local_language":
            if rubric in {"coherence_cohesion", "task_response"}:
                suppressed_layer_rubric.append(
                    row_from_candidate(c, "suppressed_layer_rubric", ["stage3_l3_cannot_produce_cc_tr"], [], False, registries.severity_framework))
                continue

        # L1/L2 cannot produce GRA/LR
        if layer in {"layer1_wide_discourse", "layer2_sentence_discourse"}:
            if rubric in {"grammar", "lexical_resource"}:
                suppressed_layer_rubric.append(
                    row_from_candidate(c, "suppressed_layer_rubric", ["stage3_l1_l2_cannot_produce_gra_lr"], [], False, registries.severity_framework))
                continue

        layer_rubric_valid.append(c)

    # ── FP veto before clustering ──────────────────────────────────────
    cluster_input: List[Candidate] = []
    for c in layer_rubric_valid:
        veto = false_positive_veto(c, resources)
        if veto:
            suppressed_fp.append(
                row_from_candidate(c, "false_positive", [veto], [], False, registries.severity_framework))
        else:
            cluster_input.append(c)

    # ── Stage 4: Span-proximity clustering ────────────────────────────
    clusters = cluster_candidates(cluster_input)

    # ── Stage 5+6: Root selection + chargeability gate ─────────────────
    # v16: per-essay caps (reset each call to arbitrate)
    _llm_only_charged = 0
    _dtr_charged = 0
    for cluster in clusters:
        if not cluster:
            continue

        root_family, family_scores = select_root_family(cluster, resources)
        root_score = family_scores.get(root_family, 0)
        root_rubric = FAMILY_TO_RUBRIC.get(root_family, "unknown")

        # Secondary families: anything >= 50% of root score, different family
        secondary = [f for f, s in family_scores.items()
                     if f != root_family and s >= root_score * 0.50]

        # Pick the best candidate from this cluster as the representative row
        def cand_priority(c: Candidate) -> float:
            fam_score = family_scores.get(c.family_candidate, 0)
            src_bonus = SOURCE_BONUS.get(c.source_engine, 2)
            return fam_score + src_bonus + c.confidence * 10

        best = max(cluster, key=cand_priority)

        # Override family to cluster root
        best_dict = best.__dict__.copy()
        best_dict["family_candidate"] = root_family
        best_dict["rubric_candidate"] = root_rubric
        best_dict["secondary_families"] = secondary
        best_dict["family_lock_status"] = "v9.7_cluster_root_selected"
        total_score = sum(family_scores.values())
        best_dict["family_lock_confidence"] = min(1.0, root_score / max(1, total_score))
        best_copy = Candidate(**best_dict)
        # Re-normalise operation for root family
        best_copy.repair_operation = normalise_v9_operation(
            best_copy.repair_operation or "", root_family)
        best_copy.operation = best_copy.repair_operation

        # ── v15: Rule family lock for unambiguous deterministic rules ──
        # When a deterministic rule engine fires at conf >= 0.78, its family
        # wins — LLM cannot reclassify it to a different family.
        # v14: SPELLING, COMPARATIVE_FORM, VERB_FORM
        # v15 added: ARTICLE_DETERMINER (article patterns are unambiguous syntactically),
        #            PREPOSITION_PATTERN (gov+prep pairs are lexically fixed and rule-verifiable)
        _DETERMINISTIC_LOCK_FAMILIES = {
            "SPELLING", "COMPARATIVE_FORM", "VERB_FORM",
            "ARTICLE_DETERMINER", "PREPOSITION_PATTERN",  # v15 additions
        }
        _best_rule_cand = None
        _best_rule_conf = 0.0
        for _rc in cluster:
            if (_rc.source_engine in {"rules_registry", "rules_support", "rules_va25_support"}
                    and _rc.family_candidate in _DETERMINISTIC_LOCK_FAMILIES
                    and _rc.confidence > _best_rule_conf):
                _best_rule_conf = _rc.confidence
                _best_rule_cand = _rc
        if _best_rule_cand is not None and _best_rule_conf >= 0.78:
            _locked_family = _best_rule_cand.family_candidate
            _locked_rubric = FAMILY_TO_RUBRIC.get(_locked_family, root_rubric)
            _bd2 = best_copy.__dict__.copy()
            _bd2["family_candidate"] = _locked_family
            _bd2["rubric_candidate"] = _locked_rubric
            _bd2["family_lock_status"] = "v15_deterministic_rule_lock"
            _bd2["family_lock_confidence"] = _best_rule_conf
            best_copy = Candidate(**_bd2)
            root_family = _locked_family
            root_rubric = _locked_rubric
        # ── Stage 6: Chargeability gate ───────────────────────────────
        engines = {c.source_engine for c in cluster}
        avg_conf = sum(c.confidence for c in cluster) / len(cluster)
        max_conf = max(c.confidence for c in cluster)
        has_rule = bool(engines & {"rules_registry", "rules_support", "rules_va25_support"})
        has_lt = bool(engines & {"LanguageTool_support"})
        has_llm = bool(engines & {"llm"})
        # v15: SLR pass split into collocate + lexical sub-passes
        has_semantic_lr_pass = bool(engines & {"semantic_lr_pass", "slr_collocate_pass", "slr_lexical_pass"})
        has_discourse_tr_pass = bool(engines & {"discourse_tr_pass"})
        has_rule_llm_confirmed = bool(engines & {"rule_llm_confirmed"})  # v16 (legacy)
        has_sva_nnc_confirmed  = bool(engines & {"sva_nnc_llm_confirmed"})   # v17
        has_lr_focused         = bool(engines & {"lr_focused_pass"})          # v17
        has_universal_confirmed= bool(engines & {"universal_confirm"})        # v18
        multi_engine = len(engines) >= 2
        is_deep_lr = root_family in DEEP_LR_FAMILIES
        is_shallow_lr = root_family in SHALLOW_LR_FAMILIES


        # v14: extended repair length gate 8→20 words — 35 TPs were blocked in v13 because
        # LLM repairs like "Kids with better nutrition tend to" exceeded 8-word limit.
        has_minimal_repair = bool(best_copy.repair_hypothesis and
                                   len(best_copy.repair_hypothesis.split()) <= 20 and
                                   not re.search(r'\b(rewrite|replace|change|should|need to)\b',
                                                  best_copy.repair_hypothesis, re.I))

        def make_row(status, reasons, chargeable_flag):
            row = row_from_candidate(
                best_copy, status, reasons, [], chargeable_flag,
                registries.severity_framework)
            row.repair_materialisation = materialise_repair(row)
            # Merge source engines from whole cluster
            row.source_engines = sorted(engines)
            return row

        # Chargeability decision
        is_chargeable = False
        charge_reasons = []

        if has_universal_confirmed:
            is_chargeable = True
            charge_reasons = ["stage6_universal_confirmed"]  # v18 Stage B
        elif has_rule_llm_confirmed:
            is_chargeable = True
            charge_reasons = ["stage6_rule_llm_confirmed"]  # v16 (legacy)
        elif has_sva_nnc_confirmed:
            is_chargeable = True
            charge_reasons = ["stage6_sva_nnc_llm_confirmed"]  # v17
        elif has_rule and max_conf >= 0.78:
            # v17: context validation before unconditional charge
            _ra_ok = True
            _SUBORD_CONJ = {
                "because","although","since","when","while","if","as",
                "after","before","unless","whereas","though","even","until","once",
            }
            if root_family == "CLAUSE_STRUCTURE":
                _q_low = (best_copy.quote or "").lower().strip()
                _first = _q_low.split()[0] if _q_low.split() else ""
                if _first in _SUBORD_CONJ:
                    _ra_ok = False
                    charge_reasons = ["stage6_rule_anchor_blocked_subord_clause_v17"]
            _SPELLING_WHITELIST = {
                "animalist","anesthesia","anesthetics","advancements","advancement",
                "workplaces","workplace","lifestyles","lifestyle","prisonsentences",
                "nowadays","furthermore","however","therefore","nonetheless","nevertheless",
                "importantly","significantly","consequently","additionally","subsequently",
            }
            if root_family == "SPELLING":
                _q_w = re.sub(r"[^a-z]", "", (best_copy.quote or "").lower())
                if _q_w in _SPELLING_WHITELIST:
                    _ra_ok = False
                    charge_reasons = ["stage6_rule_anchor_blocked_spelling_whitelist_v17"]
            if root_family == "ARTICLE_DETERMINER":
                if len((best_copy.quote or "").split()) < 3:
                    _ra_ok = False
                    charge_reasons = ["stage6_rule_anchor_blocked_article_short_quote_v18"]
            if _ra_ok:
                is_chargeable = True
                charge_reasons = ["stage6_rule_anchor_high_conf"]
        elif has_lt and max_conf >= 0.84 and (has_rule or has_llm):
            is_chargeable = True
            charge_reasons = ["stage6_lt_corroborated"]
        elif has_llm and has_rule:
            is_chargeable = True
            charge_reasons = ["stage6_llm_rule_corroborated"]
        elif has_llm and has_lt:
            is_chargeable = True
            charge_reasons = ["stage6_llm_lt_corroborated"]
        elif multi_engine and avg_conf >= 0.72:
            is_chargeable = True
            charge_reasons = ["stage6_multi_engine_cluster"]
        elif has_llm and not has_rule and not has_lt:
            # Semantic LR families — no rule exists, LLM is the anchor.
            # v16: hard cap 4 LLM-only charges per essay to bound FP volume.
            SEMANTIC_LR_CHARGEABLE = {
                "COLLOCATION", "SEMANTIC_COMBINATION", "LEXICAL_PRECISION",
                "WORD_CHOICE", "PREPOSITION_PATTERN", "REGISTER", "WORD_FORM",
                "VERB_PATTERN", "CLAUSE_STRUCTURE",
            }
            quote_has_content = len([w for w in words(best_copy.quote) if len(w) > 2]) >= 2
            has_repair = bool(best_copy.repair_hypothesis and len(best_copy.repair_hypothesis.split()) >= 2)

            if _llm_only_charged >= 4:
                is_chargeable = False
                charge_reasons = ["stage6_llm_only_essay_cap_v16"]
            elif (max_conf >= 0.88 and is_deep_lr and has_minimal_repair and
                    best_copy.layer == "layer3_local_language"):
                is_chargeable = True
                charge_reasons = ["stage6_llm_only_deep_lr_high_conf"]
                _llm_only_charged += 1
            elif (best_copy.layer == "layer3_local_language"
                  and root_family in SEMANTIC_LR_CHARGEABLE
                  and quote_has_content):
                # v17: COLLOCATION/LEXICAL_PRECISION/SEMANTIC_COMBINATION no longer
                # require repair_hypothesis (multiple valid alternatives exist).
                # Threshold lowered to 0.68 for these flexible LR families.
                _LR_FLEXIBLE = {"COLLOCATION", "LEXICAL_PRECISION", "SEMANTIC_COMBINATION"}
                _lr_flexible = root_family in _LR_FLEXIBLE
                _lr_threshold = 0.68 if _lr_flexible else 0.72
                _is_low_threshold_row = False
                REPAIR_INSTRUCTION_VERBS = re.compile(
                    r'\b(rewrite|restructure|rephrase|replace|change the|use a|should be|needs to)\b', re.I
                )
                repair_text = best_copy.repair_hypothesis or ""
                quote_text = best_copy.quote or ""
                q_content_words = [w for w in words(quote_text) if len(w) > 2]
                r_words = words(repair_text)
                if _lr_flexible:
                    quality_ok = (
                        max_conf >= _lr_threshold
                        and len(q_content_words) >= 2
                    )
                else:
                    quality_ok = (
                        max_conf >= _lr_threshold
                        and has_repair
                        and len(q_content_words) >= 2
                        and len(r_words) >= 2
                        and len(r_words) <= len(q_content_words) + 12
                        and not REPAIR_INSTRUCTION_VERBS.search(repair_text)
                        and len(repair_text) < 150
                    )
                # v18: raise floor to 0.90
                if quality_ok and max_conf >= 0.90:
                    is_chargeable = True
                    charge_reasons = ["stage6_llm_only_semantic_family_medium_conf"]
                    _is_low_threshold_row = True
                    _llm_only_charged += 1
                else:
                    is_chargeable = False
                    charge_reasons = ["stage6_llm_quality_gate_failed_review_only"]
            elif (max_conf >= 0.75 and root_family in {"COLLOCATION", "SEMANTIC_COMBINATION"}
                  and quote_has_content):  # v17: no repair:
                is_chargeable = True
                charge_reasons = ["stage6_llm_only_collocation_discourse"]
                _llm_only_charged += 1
            else:
                is_chargeable = False
                charge_reasons = ["stage6_llm_only_review_only"]

        # v18: slr_collocate_pass may charge; slr_lexical must go via universal_confirm
        elif has_semantic_lr_pass and max_conf >= 0.80:
            _slr_collocate_only = (
                bool(engines & {"slr_collocate_pass"})
                and not bool(engines & {"slr_lexical_pass", "semantic_lr_pass"})
            )
            has_repair_slr = bool(best_copy.repair_hypothesis and
                                   len(best_copy.repair_hypothesis.split()) >= 2)
            if (_slr_collocate_only and has_repair_slr
                    and root_family in {"COLLOCATION", "SEMANTIC_COMBINATION"}):
                is_chargeable = True
                charge_reasons = ["stage6_slr_collocate_pass"]
            else:
                is_chargeable = False
                charge_reasons = ["stage6_slr_lexical_review_only_v18"]
        # v16: DTR — raised conf floor to 0.85, hard cap 2 per essay
        elif has_lr_focused and max_conf >= 0.80 and _llm_only_charged < 4:
            _lrf_content = len([w for w in words(best_copy.quote or "") if len(w) > 2]) >= 2
            if _lrf_content and root_family in {"COLLOCATION","LEXICAL_PRECISION","SEMANTIC_COMBINATION","WORD_CHOICE"}:
                is_chargeable = True
                charge_reasons = ["stage6_lr_focused_pass"]
                _llm_only_charged += 1
            else:
                is_chargeable = False
                charge_reasons = ["stage6_lr_focused_pass_quality_fail"]
        elif has_discourse_tr_pass:
            # v18: DTR demoted to review_only (5.5x FP/TP in v17-b)
            is_chargeable = False
            charge_reasons = ["stage6_discourse_tr_review_only_v18"]
        elif has_rule and max_conf < 0.78:
            is_chargeable = False
            charge_reasons = ["stage6_rule_below_conf_threshold"]

        # Shallow LR from single source → always review_only
        if is_shallow_lr and len(engines) == 1:
            is_chargeable = False
            charge_reasons.append("stage6_shallow_lr_single_source")

        # Discourse (L1/L2) without rule → review_only
        # v14: exempt GRA/LR families — 20 real GRA/LR TPs were wrongly blocked in v13
        # because LLM detected them during discourse analysis (L1/L2) but the rubric-based
        # gate treated any no-rule CC/TR as discourse-blocked.  The gate should only fire
        # on true CC/TR families (COHERENCE_CONNECTOR, WEAK_EXAMPLE, etc.).
        _DISCOURSE_GATE_EXEMPT = (
            GRAMMAR_FAMILIES | LEXICAL_FAMILIES
        )
        # v15 bug fix: do NOT override when this row was already charged via
        # stage6_discourse_tr_dedicated_pass — the DTR pass IS its own anchor.
        if (root_rubric in {"coherence_cohesion", "task_response"} and not has_rule
                and root_family not in _DISCOURSE_GATE_EXEMPT
                and "stage6_discourse_tr_dedicated_pass" not in charge_reasons):
            is_chargeable = False
            charge_reasons.append("stage6_discourse_no_rule_corroboration")

        # v16: per-family FP floors for high-FP LLM-only families
        if is_chargeable and not has_rule and not has_lt and not has_rule_llm_confirmed and not has_universal_confirmed:
            if root_family == "REGISTER" and max_conf < 0.90:
                is_chargeable = False
                charge_reasons = ["stage6_register_llm_only_floor_v16"]
            elif root_family == "ARTICLE_DETERMINER" and max_conf < 0.87:
                is_chargeable = False
                charge_reasons = ["stage6_article_det_llm_only_floor_v16"]
            elif root_family == "CLAUSE_STRUCTURE" and max_conf < 0.88:
                is_chargeable = False
                charge_reasons = ["stage6_clause_struct_llm_only_floor_v18"]

        # Fix B v11: POSSESSIVE_FORM and SVA additional precision gate
        # These rules have very high FP rates without resource confirmation.
        if is_chargeable and root_family == "POSSESSIVE_FORM":
            # Only charge POSSESSIVE_FORM if morphology registry is loaded AND confirms the error,
            # OR if the quote contains a clear pronoun misuse (they/we/he/she + noun without apostrophe)
            clear_pronoun_error = bool(
                re.search(r'\b(they|we|he|she|it|you)\s+[A-Za-z]{3,}', best_copy.quote or "", re.I) and
                "'s" not in (best_copy.quote or "")
            )
            has_morphology = bool(resources.form_to_lemma)
            if not clear_pronoun_error and not has_morphology:
                is_chargeable = False
                charge_reasons = ["possessive_form_no_morphology_gate_review"]

        if is_chargeable and root_family == "SUBJECT_VERB_AGREEMENT":
            local_low = (best_copy.local_quote or "").lower()
            # Demote to review if passive or modal construction detected at sentence level
            if re.search(r'\b(is|are|was|were|be|been|being)\s+\w+(ed|en)\b', local_low):
                is_chargeable = False
                charge_reasons = ["sva_passive_gate_demote_review"]
            elif re.search(r'\b(can|could|may|might|must|should|will|would)\b', local_low):
                # Modal present — confirm only if morphology registry loaded
                if not resources.form_to_lemma:
                    is_chargeable = False
                    charge_reasons = ["sva_modal_no_morphology_gate_review"]

        if is_chargeable:
            row = make_row("accepted", charge_reasons, True)
            if locals().get("_is_low_threshold_row"):
                row.qa_flags.append("low_threshold_chargeable")
                _is_low_threshold_row = False
            chargeable.append(row)
        else:
            row = make_row("review_only", charge_reasons, False)
            row.student_visible = True
            review_only.append(row)

    # ── Stage 7: v12 — non-destructive audit (flag only, never suppress) ──
    # v11 active judge suppressed 26 real benchmark errors (31% error rate).
    # v12 reverts to flagging only. Rows tagged llm_judge_possible_fp or
    # llm_judge_uncertain are still chargeable — QA dashboard shows the flags.
    # When POSSESSIVE_FORM and SVA FP rate drops via structural fixes, the judge
    # will have a cleaner signal and can be re-activated with higher precision.
    confirmed_chargeable: List[DiagnosticRow] = list(chargeable)
    suppressed_by_llm: List[DiagnosticRow] = []
    uncertain_to_review: List[DiagnosticRow] = []
    judge_flagged_review: List[DiagnosticRow] = []

    if chargeable and llm_enabled:
        payload = [{"row_id": r.row_id, "quote": r.quote, "sentence": r.local_quote,
                    "rubric": r.rubric, "family": r.family,
                    "problem": r.problem_statement, "repair": r.repair_hypothesis,
                    "source_engines": r.source_engines, "confidence": r.confidence}
                   for r in chargeable]
        data = llm_json(
            "You are a QA auditor for an IELTS error detector.\n"
            "For each row decide: confirm | possible_fp | uncertain.\n"
            "possible_fp = likely false positive (valid English, locale variant, style choice).\n"
            "uncertain = hard to determine from context alone.\n"
            "confirm = clear, genuine IELTS-relevant error.\n"
            "This is audit only — you do NOT suppress rows.\n"
            "Return JSON {decisions:[{row_id, decision, reason}]}.\n"
            "Rows:\n" + json.dumps(payload, ensure_ascii=False),
            "You are the VA detector QA auditor. Audit only, do not suppress. JSON only.",
            STRONG_MODEL, "v12_final_audit_nondestructive", tracker, llm_enabled,
            max_tokens=3000
        )
        decisions = {}
        if isinstance(data, dict):
            for d in (data.get("decisions") or []):
                if isinstance(d, dict) and d.get("row_id"):
                    decisions[str(d["row_id"])] = d
        for row in confirmed_chargeable:
            d = decisions.get(row.row_id)
            if d:
                dec = str(d.get("decision", "confirm")).lower()
                reason = str(d.get("reason", ""))[:200]
                row.arbitration_reasons.append(f"stage7_v12_audit_{dec}:{reason}")
                if dec in {"possible_fp", "uncertain"}:
                    row.qa_flags.append(f"llm_audit_{dec}")
                    judge_flagged_review.append(row)
                    # For possible_fp: lower display weight slightly (still chargeable)
                    if dec == "possible_fp":
                        row.score_charge_weight = max(0.5, row.score_charge_weight * 0.75)
                        row.qa_flags.append("score_weight_reduced")

    # ── Stage 8: Assemble output ───────────────────────────────────────
    all_suppressed = suppressed_invalid_quote + suppressed_layer_rubric + suppressed_fp + suppressed_by_llm
    all_review = review_only + uncertain_to_review

    return {
        "raw_candidates": [asdict(c) for c in raw],
        "survived_candidates": [asdict(r) for r in confirmed_chargeable],
        "suppressed_candidates": [asdict(r) for r in all_suppressed],
        "false_positive_candidates": [asdict(r) for r in suppressed_fp],
        "duplicate_candidates": [],
        "rerouted_candidates": [],
        "uncertain_candidates": [asdict(r) for r in uncertain_to_review],
        "advisory_candidates": [asdict(r) for r in all_review],
        "chargeable_rows": [asdict(r) for r in confirmed_chargeable],
        "review_only_rows": [asdict(r) for r in all_review],
        "suppressed_by_llm_judge": [asdict(r) for r in suppressed_by_llm],
        "llm_audit_flagged_rows": [asdict(r) for r in judge_flagged_review],
        "all_stage_rows": {
            "stage2_invalid_quote": [asdict(r) for r in suppressed_invalid_quote],
            "stage3_layer_rubric_rejected": [asdict(r) for r in suppressed_layer_rubric],
            "stage_fp_veto": [asdict(r) for r in suppressed_fp],
            "stage6_chargeable_pre_judge": [asdict(r) for r in chargeable],
            "stage6_review_only": [asdict(r) for r in review_only],
            "stage7_confirmed": [asdict(r) for r in confirmed_chargeable],
            "stage7_suppressed": [asdict(r) for r in suppressed_by_llm],
            "stage7_uncertain": [asdict(r) for r in uncertain_to_review],
            "stage7_llm_audit_flagged": [asdict(r) for r in judge_flagged_review],
        },
        "v9_8_policy": {
            "cluster_based_arbitration": True,
            "llm_only_l3_default": "review_only",
            "layer_rubric_gate": "hard",
            "llm_judge_role": "non_destructive_audit_v12",
            "final_llm_deletion_authority": False,
            "operation_family_lock": "operation_plus_repair_target_required",
            "grammatical_range_primary_local_allowed": False,
            "paragraph_batched_l3_llm": True,
        }
    }
def final_llm_arbitration(rows: List[DiagnosticRow], tracker: LLMTracker, llm_enabled: bool) -> List[DiagnosticRow]:
    """v9.8: final LLM arbitration is non-destructive; this stub is kept for API compatibility."""
    return rows



def shape_response(full: Dict[str, Any], response_mode: str = "full", max_rows: int = 0) -> Dict[str, Any]:
    """V9.3+: Always return the full diagnostic object."""
    full.setdefault("response_policy", {})
    full["response_policy"].update({
        "mode": "full_only",
        "requested_mode_ignored": response_mode,
        "compact_mode_available": False,
        "max_rows_ignored": max_rows,
        "reason": "detector_QA_requires_all_errors_at_all_stages",
    })
    return full

def compact_essay_output(full: Dict[str, Any], max_rows: int = 20) -> Dict[str, Any]:
    return shape_response(full, "compact_removed", max_rows)

def diagnostic_essay_output(full: Dict[str, Any], max_rows: int = 0) -> Dict[str, Any]:
    return shape_response(full, "diagnostic_alias_full", max_rows)

def qa_only_essay_output(full: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "headline": full.get("headline", {}),
        "identity": full.get("identity", {}),
        "run": full.get("run", {}),
        "qa": full.get("qa", {}),
        "system": full.get("system", {}),
        "internal_runtime_metrics": full.get("internal_runtime_metrics", {}),
    }


# ---------------------------------------------------------------------------
# Payloads / audit / benchmark diagnostics
# ---------------------------------------------------------------------------
def source_contribution(raw: List[Dict[str,Any]], lists: Dict[str,Any]) -> Dict[str, Any]:
    def norm(s: str) -> str:
        sl = (s or "unknown").lower()
        if "llm" in sl or "gpt" in sl: return "LLM"
        if "language" in sl or "lt" in sl: return "LanguageTool"
        if "spacy" in sl: return "spaCy"
        if "va25" in sl or "rule" in sl or "semantic_gate" in sl: return "rules"
        return s or "unknown"
    def count(rows: List[Dict[str,Any]]) -> Dict[str,int]:
        c = Counter()
        for r in rows:
            if "source_engine" in r: c[norm(r.get("source_engine"))] += 1
            else:
                for se in r.get("source_engines", []) or ["unknown"]: c[norm(se)] += 1
        return dict(c)
    return {"raw_candidates": count(raw), "survived_candidates": count(lists.get("survived_candidates", [])), "chargeable_rows": count(lists.get("chargeable_rows", [])), "review_only_rows": count(lists.get("review_only_rows", [])), "false_positive_candidates": count(lists.get("false_positive_candidates", [])), "duplicate_candidates": count(lists.get("duplicate_candidates", [])), "lret_fix_rows": count([r for r in lists.get("chargeable_rows", []) if r.get("rubric") == "lexical_resource"])}

# ── v15: Dedicated semantic LR pass — SPLIT into two focused sub-passes ──────
# v14 had all 4 families in one prompt → LLM confused CLAUSE_STRUCTURE with
# COLLOCATION (5 misclassifications) and VERB_PATTERN with COLLOCATION (2).
# v15 splits: (A) structural collocate errors, (B) word-selection errors.
# Each sub-pass uses a distinct source_engine tag for independent tracking.

def _slr_parse_items(
    items: list, sents: list, allowed: set,
    run_id: str, submission_id: str, essay_id: str,
    source_engine: str,
) -> List[Candidate]:
    """Shared parser for both SLR sub-passes."""
    cands: List[Candidate] = []
    by_si = {s["sentence_index"]: s for s in sents}
    for it in items[:8]:
        fam = str(it.get("family") or "").upper()
        if fam not in allowed:
            continue
        si_raw = it.get("sentence_index")
        try:
            si = int(si_raw) if si_raw is not None else sents[0]["sentence_index"]
        except (ValueError, TypeError):
            si = sents[0]["sentence_index"]
        sent = by_si.get(si) or sents[0]
        quote = normalize_space(str(it.get("quote") or ""))
        if not quote:
            continue
        rel = sent["text"].lower().find(quote.lower())
        if rel < 0:
            for qw in [w for w in words(quote) if len(w) > 2]:
                m = re.search(re.escape(qw), sent["text"], flags=re.I)
                if m:
                    rel = m.start()
                    break
        if rel < 0:
            continue
        st = sent["char_start"] + rel
        en = min(sent["char_end"], st + len(quote))
        conf = clamp(safe_float(it.get("confidence"), 0.78))
        repair = str(it.get("repair_hypothesis") or "")
        # Guard: repair must not be a meta-instruction
        if re.search(r'\b(rewrite|replace|change the|use a|should be|restructure)\b', repair, re.I):
            continue
        cands.append(make_candidate(
            run_id, submission_id, essay_id,
            "layer3_local_language", source_engine,
            quote, sent["text"], st, en, sent,
            fam, "IMPROVE_WORD_CHOICE",
            str(it.get("problem") or "Lexical resource issue"),
            str(it.get("explanation") or f"{source_engine} identified a lexical error."),
            conf, {"slr_item": it, "pass": source_engine}, CHEAP_MODEL,
            "IMPROVE_WORD_CHOICE", repair,
            "root", [], "unspecified",
            0.50, 0.40, 0.35,
        ))
    return cands


# ── v17: Dedicated LR-focused pass ───────────────────────────────────────────
# Targets COLLOCATION, LEXICAL_PRECISION, SEMANTIC_COMBINATION.
# The general LLM pass misses these because it scans for clear grammatical errors.
# This pass uses a prompt specifically tuned for naturalness and lexical precision.
# Source engine: "lr_focused_pass".

def l3_lr_focused_pass(
    run_id: str, submission_id: str, essay_id: str,
    segmentation: Dict[str, Any],
    tracker: "LLMTracker", llm_enabled: bool,
) -> List[Candidate]:
    """v17: Dedicated COLLOCATION / LEXICAL_PRECISION / SEMANTIC_COMBINATION pass.
    Processes each paragraph, asking specifically for unnatural phrases,
    imprecise vocabulary, and semantically odd combinations.
    Source engine: 'lr_focused_pass'.
    """
    if not llm_enabled:
        return []

    cands: List[Candidate] = []
    ALLOWED = {"COLLOCATION", "LEXICAL_PRECISION", "SEMANTIC_COMBINATION"}

    system = "\n".join([
        "You are an IELTS lexical resource expert. Your ONLY job is to find:",
        "",
        "1. COLLOCATION — a wrong verb/adjective/preposition collocate for a fixed phrase:",
        "   'in worldwide nations'   → 'across the world' (wrong prep+noun combo)",
        "   'invite many problems'   → 'cause many problems' (wrong verb collocate)",
        "   'In first point of view' → 'From one point of view' (wrong phrase entirely)",
        "   'In nutshell'            → 'In a nutshell' (missing article in idiom)",
        "   'gain food and water'    → 'access food and water' (wrong verb collocate)",
        "   NOT COLLOCATION: grammar errors, wrong verb form, wrong preposition alone.",
        "",
        "2. LEXICAL_PRECISION — grammatically correct but too vague/imprecise for IELTS Band 7+:",
        "   'people get older'        → 'people age' / 'the population ages' (imprecise verb)",
        "   'artificial things'       → 'artificial content' / 'fabricated imagery' (vague noun)",
        "   'regular self-detox'      → odd phrase, not standard academic English",
        "   NOT LEXICAL_PRECISION: slang, wrong collocate (those are COLLOCATION/WORD_CHOICE).",
        "",
        "3. SEMANTIC_COMBINATION — individually correct words that are semantically incompatible:",
        "   'faster and stricter results' → results cannot be strict",
        "   'closing eyes' to solve English anxiety → idiomatic but semantically odd here",
        "   NOT SEMANTIC_COMBINATION: wrong collocate verbs (COLLOCATION).",
        "",
        "QUOTING RULE: quote the MINIMUM phrase that contains the error (2–6 words).",
        "repair_hypothesis: write ONLY the corrected phrase, no instructions.",
        "confidence: 0.78-0.93. Be strict — only flag clear, unambiguous cases.",
        "Ignore spelling, grammar, verb forms, SVA — those are handled elsewhere.",
        "",
        "Return JSON: {candidates:[{sentence_index,quote,family,problem,explanation,confidence,repair_hypothesis}]}",
        "Return {candidates:[]} if no LR errors found.",
    ])

    by_para: Dict[int, List[Dict[str, Any]]] = {}
    for s in segmentation["sentences"]:
        by_para.setdefault(s["paragraph_index"], []).append(s)

    for para_idx, sents in sorted(by_para.items()):
        sent_block = "\n".join(f"S{s['sentence_index']}: {s['text']}" for s in sents)
        prompt = (
            f"Paragraph {para_idx} — find COLLOCATION, LEXICAL_PRECISION, or SEMANTIC_COMBINATION errors only:\n"
            f"{sent_block}\n"
            f"Return {{candidates:[]}} if no LR errors found."
        )
        data = llm_json(prompt, system, CHEAP_MODEL, "LR_focused", tracker, llm_enabled, 1000)
        items = data.get("candidates", []) if isinstance(data, dict) else []
        if isinstance(items, list):
            cands.extend(_slr_parse_items(
                items, sents, ALLOWED,
                run_id, submission_id, essay_id,
                "lr_focused_pass"
            ))
    return cands


# ── v17: SVA/NNC re-enablement via LLM confirmation ──────────────────────────
# SVA and NNC rule engines were disabled in v13/v14 due to high FP rates.
# v17: instead of blanket disable, route these candidates through dedicated
# LLM confirmation. Confirmed candidates get source_engine="sva_nnc_llm_confirmed"
# which bypasses the false_positive_veto and gets its own chargeability gate.

_SVA_NNC_CONFIRM_FAMILIES = {"SUBJECT_VERB_AGREEMENT", "NOUN_NUMBER_COUNTABILITY"}
_SVA_NNC_CONFIRM_ENGINES  = {"rules_registry", "rules_support", "rules_va25_support"}
_SVA_NNC_MAX = 8

def l3_sva_nnc_confirm(
    run_id: str, submission_id: str, essay_id: str,
    all_raw: List[Candidate],
    tracker: "LLMTracker", llm_enabled: bool,
) -> List[Candidate]:
    """v17: Confirm SVA and NNC rule candidates via LLM.
    Rule engines for these families were disabled (v13/v14) due to FPs.
    This function re-enables them with mandatory LLM validation.
    Confirmed candidates: source_engine='sva_nnc_llm_confirmed'.
    """
    if not llm_enabled:
        return []
    to_confirm = [
        c for c in all_raw
        if c.family_candidate in _SVA_NNC_CONFIRM_FAMILIES
        and c.source_engine in _SVA_NNC_CONFIRM_ENGINES
    ][:_SVA_NNC_MAX]
    if not to_confirm:
        return []

    system = "\n".join([
        "You are a strict IELTS grammar validator for subject-verb agreement (SVA)",
        "and noun number/countability (NNC) errors.",
        "",
        "SVA: confirmed=true ONLY when subject and verb clearly disagree in number.",
        "  REAL:   'The children plays' / 'one of the major drawback is' / 'many type of'",
        "  FALSE:  'that government' / 'it hard' / 'That said' (correct English)",
        "",
        "NNC: confirmed=true ONLY when a countable noun is used without required article",
        "  or a mass noun is incorrectly pluralised.",
        "  REAL:   'one major drawback' (missing article before singular) / 'many informations'",
        "  FALSE:  'many people' / 'few minutes' / 'several effects' (all correct)",
        "",
        "Be very strict. Return confirmed=true only for unambiguous errors.",
        "Return JSON: {results:[{index,confirmed,family,confidence}]}",
        "confidence: 0.85-0.97 for confirmed=true only. Raise bar — be strict.",
    ])

    items_text = []
    for idx, c in enumerate(to_confirm):
        items_text.append(
            f"[{idx}] quote={c.quote!r} | sentence={c.local_quote!r} "
            f"| family={c.family_candidate}"
        )
    prompt = (
        "Validate each SVA/NNC rule-detected candidate.\n"
        "Return confirmed=true only for unambiguous errors.\n\n"
        + "\n".join(items_text)
    )

    data = llm_json(prompt, system, CHEAP_MODEL, "sva_nnc_confirm", tracker, llm_enabled, 600)
    results = data.get("results", []) if isinstance(data, dict) else []

    confirmed_out: List[Candidate] = []
    for item in (results if isinstance(results, list) else []):
        idx = item.get("index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(to_confirm):
            continue
        if not item.get("confirmed", False):
            continue
        c = to_confirm[idx]
        new_fam  = str(item.get("family") or c.family_candidate).upper().strip()
        if new_fam not in FAMILY_TO_RUBRIC:
            new_fam = c.family_candidate
        new_rub  = FAMILY_TO_RUBRIC.get(new_fam, c.rubric_candidate)
        new_conf = clamp(safe_float(item.get("confidence"), c.confidence))
        bd = c.__dict__.copy()
        bd["source_engine"]          = "sva_nnc_llm_confirmed"
        bd["family_candidate"]       = new_fam
        bd["rubric_candidate"]       = new_rub
        bd["confidence"]             = new_conf
        bd["family_lock_status"]     = "v17_sva_nnc_llm_confirmed"
        bd["family_lock_confidence"] = new_conf
        bd["candidate_id"]           = stable_id(
            "cand", run_id, essay_id, "sva_nnc_llm_confirmed",
            c.quote, str(c.span_start), new_fam
        )
        confirmed_out.append(Candidate(**bd))
    return confirmed_out


# ── v16: Stage B — Rule-span LLM confirmation ─────────────────────────────────
# The LLM is NOT detecting errors here — it validates spans already found by
# rule/LT engines and optionally corrects the family assignment.
# Source engine of confirmed candidates: "rule_llm_confirmed".
# Only LT + rules_support candidates are sent (medium-confidence anchors).
# High-confidence rules_registry candidates (conf ≥ 0.78) are already reliable.

_RULE_CONFIRM_ENGINES = {"LanguageTool_support", "rules_support"}
# v18: universal confirm engines
_UNIVERSAL_CONFIRM_ENGINES = {
    "LanguageTool_support", "rules_support",
    "spaCy_support",       # SVA/fragment/splice from l2_spacy_pass
    "collocation_lookup",  # pattern lookup
    "slr_collocate_pass",  # LLM-generated collocates
    "fixed_phrase_pass",   # v18a: IELTS fixed-phrase / idiom lookup
}
_UNIVERSAL_CONFIRM_MAX = 20
_RULE_CONFIRM_MAX = 10   # v17: reduced max (cost control)
# v17: Stage B only confirms candidates from deterministic-lock families.
# CLAUSE_STRUCTURE and GRAMMAR_PUNCTUATION removed — LLM over-confirms these.
_STAGE_B_ALLOWED_FAMILIES = {
    "VERB_FORM", "COMPARATIVE_FORM", "PREPOSITION_PATTERN",
    "ARTICLE_DETERMINER", "SPELLING", "SUBJECT_VERB_AGREEMENT",
    "NOUN_NUMBER_COUNTABILITY", "WORD_FORM",
}

def l2_spacy_pass(
    run_id: str, submission_id: str, essay_id: str,
    segmentation: Dict[str, Any],
) -> List[Candidate]:
    """v18: spaCy dep-parse generator for SVA, fragment, comma-splice.
    Source engine: spaCy_support. Candidates route to l3_universal_confirm().
    """
    nlp = get_spacy()
    if nlp is None:
        return []
    cands: List[Candidate] = []
    for sent in segmentation["sentences"]:
        txt = sent["text"]
        if len(txt.split()) < 4:
            continue
        try:
            doc = nlp(txt)
        except Exception:
            continue

        # SVA: subject-verb number mismatch
        for token in doc:
            if token.dep_ not in ("nsubj", "nsubjpass"):
                continue
            head = token.head
            if head.pos_ != "VERB":
                continue
            # P0-FIX-1 (v18b): skip verbs that are heads of relative clauses —
            # "who wears", "which allows", "that transcends" are correct SVA within RC.
            if head.dep_ == "relcl":
                continue
            # v18d R2: skip wh-word subjects — indirect questions like "what truly
            # matters" are NOT SVA errors; spaCy parses the wh-word as nsubj of the
            # embedded verb, but the verb form is correct in a nominal clause.
            if token.tag_ in ("WP", "WDT", "WRB"):
                continue
            # v18d R2: skip when multiple subjects attach to the same verb head
            # (compound subject "A, B, and C + verb"). spaCy often mis-labels the
            # head noun as singular, generating false plural-subject+VBZ flags.
            if sum(1 for ch in head.children
                   if ch.dep_ in ("nsubj", "nsubjpass")) >= 2:
                continue
            subj_sing = (
                token.tag_ in ("NN", "NNP", "PRP")
                and token.lower_ not in ("they", "we", "you", "i")
            )
            if head.tag_ == "VBZ" and not subj_sing:
                q = re.sub(r"\s+", " ", txt[token.idx: head.idx + len(head.text)]).strip()
                if q and len(q) <= 50:
                    st = sent["char_start"] + token.idx
                    en = sent["char_start"] + head.idx + len(head.text)
                    cands.append(make_candidate(
                        run_id, submission_id, essay_id,
                        "layer3_local_language", "spaCy_support",
                        q, txt, st, en, sent,
                        "SUBJECT_VERB_AGREEMENT", "change_form",
                        "SVA mismatch: plural subject with 3sg verb",
                        "spaCy dep-parse: plural subject + VBZ verb.",
                        0.72, {"spacy_sva": "plural_subj_vbz"}, None, "change_form", "",
                    ))
            elif head.tag_ == "VBP" and subj_sing:
                q = re.sub(r"\s+", " ", txt[token.idx: head.idx + len(head.text)]).strip()
                if q and len(q) <= 50:
                    st = sent["char_start"] + token.idx
                    en = sent["char_start"] + head.idx + len(head.text)
                    cands.append(make_candidate(
                        run_id, submission_id, essay_id,
                        "layer3_local_language", "spaCy_support",
                        q, txt, st, en, sent,
                        "SUBJECT_VERB_AGREEMENT", "change_form",
                        "SVA mismatch: singular subject with plural verb",
                        "spaCy dep-parse: singular subject + VBP verb.",
                        0.68, {"spacy_sva": "sing_subj_vbp"}, None, "change_form", "",
                    ))

        # Fragment: ROOT is non-finite
        roots = [t for t in doc if t.dep_ == "ROOT"]
        if roots:
            root = roots[0]
            if (root.pos_ not in ("VERB", "AUX")
                    or root.tag_ in ("VBG", "VBN", "NN", "NNS")):
                if len(txt.split()) >= 6 and not txt.strip().endswith("?"):
                    q = txt[:60].strip()
                    cands.append(make_candidate(
                        run_id, submission_id, essay_id,
                        "layer3_local_language", "spaCy_support",
                        q, txt, sent["char_start"], sent["char_start"] + len(q), sent,
                        "CLAUSE_STRUCTURE", "add_finite_verb",
                        "Possible sentence fragment",
                        "spaCy ROOT is non-finite.",
                        0.65, {"spacy_fragment": True}, None, "add_finite_verb", "",
                    ))

        # Comma splice: multiple finite verbs with subjects, no conjunction
        if "," in txt:
            verbs_with_subj = [
                t for t in doc
                if t.pos_ in ("VERB", "AUX")
                and t.dep_ in ("ROOT", "conj")
                and any(c.dep_ in ("nsubj", "nsubjpass") for c in t.children)
            ]
            conj_present = any(t.dep_ == "cc" for t in doc)
            if len(verbs_with_subj) >= 2 and not conj_present and ";" not in txt:
                q = txt[:70].strip()
                cands.append(make_candidate(
                    run_id, submission_id, essay_id,
                    "layer3_local_language", "spaCy_support",
                    q, txt, sent["char_start"], sent["char_start"] + len(q), sent,
                    "CLAUSE_STRUCTURE", "restructure_clause",
                    "Possible comma splice",
                    "spaCy: multiple finite verbs with subjects, no coordinator.",
                    0.63, {"spacy_comma_splice": True}, None, "restructure_clause", "",
                ))
    return cands


def l2_collocation_lookup_pass(
    run_id: str, submission_id: str, essay_id: str,
    segmentation: Dict[str, Any],
    resources: "ResourceBundle",
) -> List[Candidate]:
    """v18: curated collocation pattern lookup (~50 patterns).
    Positive collocation registry acts as evidence veto.
    Source engine: collocation_lookup. Routes to l3_universal_confirm().
    """
    WRONG_PATTERNS: List[tuple] = [
        ("do a mistake",          "make a mistake",           0.88),
        ("did a mistake",         "made a mistake",           0.88),
        ("did mistakes",          "made mistakes",            0.82),
        ("do an effort",          "make an effort",           0.85),
        ("do efforts",            "make efforts",             0.82),
        ("do a research",         "conduct research",         0.87),
        ("did a research",        "conducted research",       0.87),
        ("do researches",         "conduct research",         0.85),
        ("make a research",       "conduct research",         0.88),
        ("made a research",       "conducted research",       0.88),
        ("do a decision",         "make a decision",          0.87),
        ("take a decision",       "make a decision",          0.85),
        ("took a decision",       "made a decision",          0.85),
        ("make a harm",           "cause harm",               0.83),
        ("do a harm",             "cause harm",               0.83),
        ("make damages",          "cause damage",             0.82),
        ("do damages",            "cause damage",             0.82),
        ("make a crime",          "commit a crime",           0.83),
        ("do a crime",            "commit a crime",           0.83),
        ("make crimes",           "commit crimes",            0.82),
        ("give a contribution",   "make a contribution",      0.83),
        ("give contributions",    "make contributions",       0.82),
        ("rise awareness",        "raise awareness",          0.88),
        ("arose awareness",       "raised awareness",         0.85),
        ("strong knowledge",      "extensive knowledge",      0.82),
        ("strong experience",     "extensive experience",     0.82),
        ("strong impact",         "significant impact",       0.80),
        ("strong influence",      "significant influence",    0.80),
        ("strong damage",         "severe damage",            0.80),
        ("give attention",        "pay attention",            0.82),
        ("conduct a crime",       "commit a crime",           0.80),
        ("conduct crimes",        "commit crimes",            0.80),
        ("heavy pollution",       "severe pollution",         0.78),
        ("heavy crimes",          "serious crimes",           0.78),
        ("heavy problems",        "serious problems",         0.77),
        ("achieve the goal to",   "achieve the goal of",      0.80),
        ("reach the goal to",     "reach the goal of",        0.78),
        ("play an important role to", "play an important role in", 0.80),
        ("bring advantages",      "offer advantages",         0.78),
        ("bring disadvantages",   "create disadvantages",     0.78),
        ("make a negative affect","have a negative effect",   0.83),
        ("have a negative affect","have a negative effect",   0.83),
        ("give a negative effect","have a negative effect",   0.78),
        ("make a positive affect","have a positive effect",   0.83),
        ("have a positive affect","have a positive effect",   0.83),
        ("have a big effect in",  "have a big effect on",     0.80),
        ("make an unemployment",  "cause unemployment",       0.82),
        ("do pollution",          "cause pollution",          0.83),
        ("do damage to",          "cause damage to",          0.82),
        ("raise a solution",      "find a solution",          0.83),
    ]

    positive_registry: set = getattr(resources, "positive_collocations", set())
    cands: List[Candidate] = []

    for sent in segmentation["sentences"]:
        txt_low = sent["text"].lower()
        txt_orig = sent["text"]
        for wrong, correct, conf in WRONG_PATTERNS:
            import re as _re
            pat = r"\b" + _re.escape(wrong) + r"\b"
            m = _re.search(pat, txt_low)
            if not m:
                continue
            if wrong in positive_registry or tuple(wrong.split()) in positive_registry:
                continue
            quote = txt_orig[m.start(): m.end()]
            st = sent["char_start"] + m.start()
            en = sent["char_start"] + m.end()
            cands.append(make_candidate(
                run_id, submission_id, essay_id,
                "layer3_local_language", "collocation_lookup",
                quote, txt_orig, st, en, sent,
                "COLLOCATION", "change_collocation",
                f"Wrong collocation: '{wrong}' should be '{correct}'",
                f"Pattern lookup: '{wrong}' is a known L2 collocation error.",
                conf, {"lookup_correct": correct, "pattern": wrong},
                None, "change_collocation", correct,
            ))
    return cands


def l2_fixed_phrase_pass(
    run_id: str, submission_id: str, essay_id: str,
    segmentation: Dict[str, Any],
    resources: "ResourceBundle",
) -> List[Candidate]:
    """v18a: IELTS fixed-phrase / idiom / preposition-in-phrase lookup.

    Covers three error classes not in l2_collocation_lookup_pass:
      1. Wrong preposition in fixed phrases (regardless to, in the other hand, ...)
      2. Idiom form errors (in nutshell, for an instance, ...)
      3. Verb + specific-object wrong collocations (invite problems, gain food, ...)

    Source engine: fixed_phrase_pass.  Routes to l3_universal_confirm().
    Context safety: each n-gram is specific enough (≥3 words OR verb+unambiguous noun)
    that context-dependence is not a concern for the included patterns.
    Patterns with broad FP risk (e.g. "based in", "focus in") are excluded.

    Pattern format: (ngram_lower, correct_alternative, confidence, family, description)
    confidence=0.0  → skip (negative guard, not matched)
    """
    import re as _re

    _FIXED_PHRASE_PATTERNS: list = [
        # ── PREPOSITION ERRORS IN FIXED PHRASES ───────────────────────
        # regardless of is the only correct form
        ("regardless to",               "regardless of",                    0.93, "PREPOSITION_PATTERN",  "Fixed phrase: 'regardless OF' is correct"),
        ("regardless than",             "regardless of",                    0.93, "PREPOSITION_PATTERN",  "Fixed phrase: 'regardless OF' is correct"),
        ("regardless from",             "regardless of",                    0.88, "PREPOSITION_PATTERN",  "Fixed phrase: 'regardless OF' is correct"),
        # on the other hand (not 'in'; not plural)
        ("in the other hand",           "on the other hand",                0.93, "PREPOSITION_PATTERN",  "Fixed phrase: 'ON the other hand' (not 'in')"),
        ("on the other hands",          "on the other hand",                0.92, "PREPOSITION_PATTERN",  "Fixed phrase: 'on the other hand' (no plural 's')"),
        # at the same time (not 'in')
        ("in the same time",            "at the same time",                 0.91, "PREPOSITION_PATTERN",  "Fixed phrase: 'AT the same time' (not 'in')"),
        ("in same time",                "at the same time",                 0.89, "PREPOSITION_PATTERN",  "Fixed phrase: 'at the same time'"),
        # in the long/short run
        ("at the long run",             "in the long run",                  0.92, "PREPOSITION_PATTERN",  "Fixed phrase: 'IN the long run' (not 'at')"),
        ("on the long run",             "in the long run",                  0.92, "PREPOSITION_PATTERN",  "Fixed phrase: 'in the long run' (not 'on')"),
        ("in a long run",               "in the long run",                  0.88, "PREPOSITION_PATTERN",  "Fixed phrase: 'in THE long run' (definite article)"),
        ("in long run",                 "in the long run",                  0.90, "PREPOSITION_PATTERN",  "Fixed phrase: 'in THE long run' (article missing)"),
        ("at long run",                 "in the long run",                  0.90, "PREPOSITION_PATTERN",  "Fixed phrase: 'in the long run'"),
        ("on long run",                 "in the long run",                  0.90, "PREPOSITION_PATTERN",  "Fixed phrase: 'in the long run'"),
        # in the long/short term (article required)
        ("in long term",                "in the long term",                 0.89, "PREPOSITION_PATTERN",  "Fixed phrase requires definite article: 'in THE long term'"),
        ("in short term",               "in the short term",                0.89, "PREPOSITION_PATTERN",  "Fixed phrase requires article: 'in THE short term'"),
        ("on long term",                "in the long term",                 0.90, "PREPOSITION_PATTERN",  "Fixed phrase: 'IN the long term' (not 'on')"),
        ("on short term",               "in the short term",                0.90, "PREPOSITION_PATTERN",  "Fixed phrase: 'in the short term' (not 'on')"),
        # opinion fixed phrase
        ("to my opinion",               "in my opinion",                    0.91, "PREPOSITION_PATTERN",  "Fixed phrase: 'IN my opinion' (not 'to')"),
        ("on my opinion",               "in my opinion",                    0.91, "PREPOSITION_PATTERN",  "Fixed phrase: 'in my opinion' (not 'on')"),
        ("with my opinion",             "in my opinion",                    0.88, "PREPOSITION_PATTERN",  "Fixed phrase: 'in my opinion' (not 'with')"),
        ("according to my opinion",     "in my opinion",                    0.88, "PREPOSITION_PATTERN",  "'According to my opinion' — redundant; use 'in my opinion'"),
        ("as per my opinion",           "in my opinion",                    0.87, "PREPOSITION_PATTERN",  "Redundant form; use 'in my opinion'"),
        ("from my opinion",             "in my opinion",                    0.88, "PREPOSITION_PATTERN",  "Fixed phrase: 'IN my opinion' (not 'from')"),
        # point of view
        ("in my point of view",         "from my point of view",            0.88, "PREPOSITION_PATTERN",  "Fixed phrase: 'FROM my point of view' (not 'in')"),
        ("in first point of view",      "from one point of view",           0.93, "PREPOSITION_PATTERN",  "Wrong fixed phrase; use 'from one point of view'"),
        ("in second point of view",     "from another point of view",       0.93, "PREPOSITION_PATTERN",  "Wrong fixed phrase; use 'from another point of view'"),
        ("on first point of view",      "from one point of view",           0.91, "PREPOSITION_PATTERN",  "Wrong preposition; use 'from one point of view'"),
        ("on my point of view",         "from my point of view",            0.91, "PREPOSITION_PATTERN",  "Fixed phrase: 'FROM my point of view'"),
        ("at my point of view",         "from my point of view",            0.88, "PREPOSITION_PATTERN",  "Fixed phrase: 'from my point of view' (not 'at')"),
        # accordingly to (wrong form)
        ("accordingly to",              "according to",                     0.92, "PREPOSITION_PATTERN",  "Wrong form: 'ACCORDING to' (not 'accordingly to')"),
        # due of
        ("due of",                      "due to",                           0.92, "PREPOSITION_PATTERN",  "Fixed phrase: 'due TO' (not 'due of')"),
        # aware
        ("aware from",                  "aware of",                         0.92, "PREPOSITION_PATTERN",  "Fixed phrase: 'aware OF' (not 'from')"),
        ("aware about",                 "aware of",                         0.87, "PREPOSITION_PATTERN",  "Fixed phrase: 'aware OF' (not 'about')"),
        # capable
        ("capable to",                  "capable of",                       0.90, "PREPOSITION_PATTERN",  "Fixed phrase: 'capable OF' (not 'to')"),
        # as a result (not 'in a result')
        ("in a result",                 "as a result",                      0.92, "PREPOSITION_PATTERN",  "Fixed phrase: 'AS a result' (not 'in a result')"),
        # contrast
        ("by the contrast",             "by contrast / in contrast",        0.87, "PREPOSITION_PATTERN",  "Fixed phrase: 'by contrast' (no article)"),
        ("in the contrast",             "by contrast / in contrast",        0.87, "PREPOSITION_PATTERN",  "Fixed phrase: 'in contrast' (no 'the' if not comparative)"),
        ("at contrast",                 "by contrast",                      0.88, "PREPOSITION_PATTERN",  "Fixed phrase: 'BY contrast' (not 'at')"),
        # result to/in
        ("result to",                   "result in",                        0.92, "PREPOSITION_PATTERN",  "Fixed pattern: 'result IN' (not 'result to')"),
        ("resulted to",                 "resulted in",                      0.92, "PREPOSITION_PATTERN",  "Fixed pattern: 'result IN' (not 'to')"),
        ("results to",                  "results in",                       0.90, "PREPOSITION_PATTERN",  "Fixed pattern: 'result IN' (not 'to')"),
        # contribute to
        ("contribute in",               "contribute to",                    0.90, "PREPOSITION_PATTERN",  "Fixed pattern: 'contribute TO' (not 'in')"),
        ("contributed in",              "contributed to",                   0.90, "PREPOSITION_PATTERN",  "Fixed pattern: 'contribute TO'"),
        ("contributing in",             "contributing to",                  0.90, "PREPOSITION_PATTERN",  "Fixed pattern: 'contribute TO'"),
        ("contribute on",               "contribute to",                    0.90, "PREPOSITION_PATTERN",  "Fixed pattern: 'contribute TO' (not 'on')"),
        # interested in
        ("interested on",               "interested in",                    0.92, "PREPOSITION_PATTERN",  "Fixed pattern: 'interested IN' (not 'on')"),
        ("interested at",               "interested in",                    0.88, "PREPOSITION_PATTERN",  "Fixed pattern: 'interested IN' (not 'at')"),
        # depend on
        ("depend of",                   "depend on",                        0.92, "PREPOSITION_PATTERN",  "Fixed pattern: 'depend ON' (not 'of')"),
        ("depends of",                  "depends on",                       0.92, "PREPOSITION_PATTERN",  "Fixed pattern: 'depend ON'"),
        ("dependent of",                "dependent on",                     0.90, "PREPOSITION_PATTERN",  "Fixed pattern: 'dependent ON' (not 'of')"),
        ("dependent from",              "dependent on",                     0.90, "PREPOSITION_PATTERN",  "Fixed pattern: 'dependent ON' (not 'from')"),
        # rely on
        ("rely in",                     "rely on",                          0.90, "PREPOSITION_PATTERN",  "Fixed pattern: 'rely ON' (not 'in')"),
        ("relied in",                   "relied on",                        0.90, "PREPOSITION_PATTERN",  "Fixed pattern: 'rely ON'"),
        ("relying in",                  "relying on",                       0.90, "PREPOSITION_PATTERN",  "Fixed pattern: 'rely ON'"),
        # invest in
        ("invest on",                   "invest in",                        0.90, "PREPOSITION_PATTERN",  "Fixed pattern: 'invest IN' (not 'on')"),
        ("invested on",                 "invested in",                      0.90, "PREPOSITION_PATTERN",  "Fixed pattern: 'invest IN'"),
        ("investing on",                "investing in",                     0.90, "PREPOSITION_PATTERN",  "Fixed pattern: 'invest IN'"),
        # pay attention to
        ("pay attention in",            "pay attention to",                 0.90, "PREPOSITION_PATTERN",  "Fixed phrase: 'pay attention TO' (not 'in')"),
        ("pay attention at",            "pay attention to",                 0.88, "PREPOSITION_PATTERN",  "Fixed phrase: 'pay attention TO'"),
        # participate in
        ("participate on",              "participate in",                   0.90, "PREPOSITION_PATTERN",  "Fixed pattern: 'participate IN' (not 'on')"),
        ("participated on",             "participated in",                  0.90, "PREPOSITION_PATTERN",  "Fixed pattern: 'participate IN'"),
        # take part in
        ("take part on",                "take part in",                     0.92, "PREPOSITION_PATTERN",  "Fixed phrase: 'take part IN' (not 'on')"),
        ("took part on",                "took part in",                     0.92, "PREPOSITION_PATTERN",  "Fixed phrase: 'take part IN'"),
        # suffer from
        ("suffer of",                   "suffer from",                      0.90, "PREPOSITION_PATTERN",  "Fixed pattern: 'suffer FROM' (not 'of')"),
        ("suffered of",                 "suffered from",                    0.90, "PREPOSITION_PATTERN",  "Fixed pattern: 'suffer FROM'"),
        ("suffering of",                "suffering from",                   0.90, "PREPOSITION_PATTERN",  "Fixed pattern: 'suffering FROM'"),
        # worried/concerned about
        ("worried of",                  "worried about",                    0.90, "PREPOSITION_PATTERN",  "Fixed pattern: 'worried ABOUT' (not 'of')"),
        ("concerned of",                "concerned about",                  0.88, "PREPOSITION_PATTERN",  "Fixed pattern: 'concerned ABOUT' (not 'of')"),

        # ── IDIOM / FIXED-PHRASE FORM ERRORS ─────────────────────────
        # in a nutshell
        ("in nutshell",                 "in a nutshell",                    0.93, "COLLOCATION",  "Idiom: 'in A nutshell' (article required)"),
        ("on nutshell",                 "in a nutshell",                    0.90, "COLLOCATION",  "Idiom: 'IN a nutshell'"),
        # for instance (no article)
        ("for an instance",             "for instance",                     0.92, "COLLOCATION",  "Fixed phrase: 'for instance' (no article — 'an' is wrong)"),
        # in spite of (not 'in the spite of' or 'in despite of')
        ("in the spite of",             "in spite of / despite",            0.93, "COLLOCATION",  "Fixed phrase: 'in spite of' (no article 'the')"),
        ("in despite of",               "in spite of / despite",            0.92, "COLLOCATION",  "Wrong form: use 'in spite of' or 'despite' (not 'in despite of')"),
        # cope with (not 'cope up with')
        ("cope up with",                "cope with",                        0.93, "COLLOCATION",  "Collocation: 'cope WITH' (no 'up' — non-standard)"),
        ("coped up with",               "coped with",                       0.93, "COLLOCATION",  "Collocation: 'cope WITH' (no 'up')"),
        ("coping up with",              "coping with",                      0.93, "COLLOCATION",  "Collocation: 'cope WITH' (no 'up')"),
        # firstly and foremost (wrong — should be 'first and foremost')
        ("firstly and foremost",        "first and foremost",               0.93, "COLLOCATION",  "Idiom: 'FIRST and foremost' (not 'firstly')"),
        # last but not the least
        ("last but not the least",      "last but not least",               0.90, "COLLOCATION",  "Idiom: 'last but not least' (no 'the')"),
        # take advantage (not 'take an advantage')
        ("take an advantage",           "take advantage of",                0.90, "COLLOCATION",  "Idiom: 'take advantage of' (no article 'an')"),
        ("take an advantages",          "take advantage of",                0.90, "COLLOCATION",  "Idiom: 'take advantage of' (no article, no plural)"),
        ("take advantages",             "take advantage of",                0.88, "COLLOCATION",  "Idiom: 'take advantage of' (singular 'advantage')"),
        # in worldwide nations (unnatural)
        ("in worldwide nations",        "around the world / worldwide",     0.87, "COLLOCATION",  "'In worldwide nations' is unnatural; use 'around the world'"),
        # draw a conclusion
        ("to draw conclusion",          "to draw a conclusion",             0.87, "COLLOCATION",  "Collocation: 'draw A conclusion' (article required)"),
        ("make conclusion",             "draw a conclusion / to conclude",  0.85, "COLLOCATION",  "Collocation: 'draw a conclusion' or 'to conclude'"),
        ("do a conclusion",             "draw a conclusion / to conclude",  0.87, "COLLOCATION",  "Collocation: 'DRAW a conclusion' (not 'do')"),
        # by these reasons / by this reason
        ("by these reasons",            "for these reasons",                0.85, "COLLOCATION",  "'By these reasons' is non-standard; use 'for these reasons'"),
        ("by this reason",              "for this reason",                  0.85, "COLLOCATION",  "'By this reason' is non-standard; use 'for this reason'"),
        # irreplaceable with → irreplaceable by
        ("irreplaceable with",          "irreplaceable by / cannot be replaced by", 0.88, "COLLOCATION", "Collocation: 'irreplaceable BY' (not 'with')"),

        # ── MAKE/DO CONFUSION (extensions beyond v18 base set) ────────
        ("do progress",                 "make progress",                    0.90, "COLLOCATION",  "Collocation: 'MAKE progress' (not 'do progress')"),
        ("did progress",                "made progress",                    0.88, "COLLOCATION",  "Collocation: 'made progress' (not 'did progress')"),
        ("do a progress",               "make progress",                    0.90, "COLLOCATION",  "Collocation: 'make progress' (no article)"),
        ("make a homework",             "do homework",                      0.88, "COLLOCATION",  "Collocation: 'DO homework' (not 'make'; no article)"),
        ("make homeworks",              "do homework",                      0.88, "COLLOCATION",  "Collocation: 'do homework' (uncountable)"),
        ("do a suggestion",             "make a suggestion",                0.87, "COLLOCATION",  "Collocation: 'MAKE a suggestion' (not 'do')"),
        ("do a question",               "ask a question",                   0.88, "COLLOCATION",  "Collocation: 'ASK a question' (not 'do')"),
        ("make a question",             "ask a question",                   0.87, "COLLOCATION",  "Collocation: 'ask a question' (not 'make')"),
        ("do a speech",                 "give a speech / make a speech",    0.87, "COLLOCATION",  "Collocation: 'GIVE a speech' (not 'do')"),
        ("making business",             "doing business",                   0.90, "COLLOCATION",  "Collocation: 'DOING business' (not 'making business')"),
        ("make business",               "do business",                      0.88, "COLLOCATION",  "Collocation: 'DO business' (not 'make')"),
        ("made business",               "did business",                     0.85, "COLLOCATION",  "Collocation: 'did business' (not 'made business')"),

        # ── WRONG VERB + SPECIFIC OBJECT ──────────────────────────────
        # invite + problem/issue nouns
        ("invite problems",             "cause/create problems",            0.85, "COLLOCATION",  "Collocation: problems are 'caused/created', not 'invited'"),
        ("invite issues",               "cause issues",                     0.83, "COLLOCATION",  "Collocation: 'cause issues' (not 'invite issues')"),
        ("invite harm",                 "cause harm",                       0.83, "COLLOCATION",  "Collocation: 'cause harm' (not 'invite harm')"),
        ("invite risks",                "pose risks",                       0.83, "COLLOCATION",  "Collocation: 'pose risks' (not 'invite risks')"),
        ("invite danger",               "pose danger / create danger",      0.82, "COLLOCATION",  "Collocation: 'pose/create danger' (not 'invite danger')"),
        ("invite disease",              "cause disease",                    0.83, "COLLOCATION",  "Collocation: 'cause disease' (not 'invite disease')"),
        ("invite health problems",      "cause health problems",            0.88, "COLLOCATION",  "Collocation: 'cause health problems' (not 'invite')"),
        # gain + food/water (should be produce/obtain/provide)
        ("gain food and water",         "produce/obtain food and water",    0.90, "COLLOCATION",  "Collocation: food and water are 'produced/obtained', not 'gained'"),
        ("gain food",                   "produce/obtain food",              0.83, "COLLOCATION",  "Collocation: 'produce/obtain food' (not 'gain food')"),
        ("gain water",                  "obtain/access water",              0.83, "COLLOCATION",  "Collocation: 'obtain/access water' (not 'gain water')"),
        # break + perception/esteem/conception
        ("break self-esteem",           "damage/undermine self-esteem",     0.87, "COLLOCATION",  "Collocation: self-esteem is 'damaged/undermined', not 'broken'"),
        ("break their self-esteem",     "damage/undermine their self-esteem", 0.88, "COLLOCATION", "Collocation: self-esteem is 'damaged', not 'broken'"),
        ("break the self-esteem",       "damage/undermine self-esteem",     0.87, "COLLOCATION",  "Collocation: self-esteem is 'damaged', not 'broken'"),
        ("break confidence",            "undermine/damage confidence",      0.82, "COLLOCATION",  "Collocation: confidence is 'undermined/damaged', not 'broken'"),
        ("break conception",            "distort/change conception",        0.85, "COLLOCATION",  "Collocation: 'distort/change a conception' (not 'break')"),
        ("break the conception",        "distort/change the conception",    0.85, "COLLOCATION",  "Collocation: 'distort/change' (not 'break') a conception"),
        ("break perception",            "distort/alter perception",         0.83, "COLLOCATION",  "Collocation: 'distort/alter perception' (not 'break')"),
        # concern paid (wrong — should be 'attention paid' or 'concern raised')
        ("concern has been paid",       "attention has been paid / concern has been raised", 0.90, "COLLOCATION", "'Concern is paid' is wrong; 'attention is paid' or 'concern is raised'"),
        ("concern was paid",            "attention was paid",               0.88, "COLLOCATION",  "Collocation: 'attention is paid', not 'concern is paid'"),
        ("concern is paid",             "attention is paid",                0.88, "COLLOCATION",  "Collocation: 'attention is paid', not 'concern is paid'"),
        # feel + himself/herself + on
        ("feel himself on his dreams",  "imagine himself living his dreams", 0.88, "COLLOCATION", "Non-English construction; intended: 'imagine himself in his dreams'"),
        ("feel herself on her dreams",  "imagine herself living her dreams", 0.88, "COLLOCATION", "Non-English construction"),
        # be professional in careers
        ("be professional in careers",  "pursue professional careers",      0.87, "COLLOCATION",  "'Be professional in careers' is unnatural; 'pursue professional careers'"),
        # duration of life
        ("duration of their life",      "their lifespan / life expectancy", 0.82, "COLLOCATION",  "Wordy: 'lifespan' or 'life expectancy' is standard"),
        ("duration of life",            "lifespan / life expectancy",       0.82, "COLLOCATION",  "Unnatural; prefer 'lifespan' or 'life expectancy'"),
        ("duration of their lives",     "their lifespan",                   0.82, "COLLOCATION",  "Prefer 'lifespan'"),
        # effective nation
        ("effective nation",            "effective governance / safe society", 0.80, "COLLOCATION", "'Effective nation' is unnatural; 'effective governance/society' is standard"),
        # efficient/effective officers (from benchmark)
        ("efficient officers",          "well-trained officers",            0.80, "COLLOCATION",  "'Efficient officers' is unnatural; 'well-trained/professional officers'"),
        # good ability to (from benchmark: 'good ability to older peoples')
        ("good ability to",             "ability to / capacity to",         0.82, "COLLOCATION",  "'Good ability to' is non-standard; 'ability to' or 'capacity to' is correct"),
        # ── EXPANDED REGISTRY v1 (154 new | 15 duplicates skipped | 19 FP-risk rejected)
        # Source: ielts_fixed_phrase_collocation_expanded_registry_v1.json | 2026-06-09

        # A: Wrong preposition in fixed phrases
        ("responsible of", "responsible for", 0.95, "PREPOSITION_PATTERN", "Wrong preposition after responsible"),
        ("typical for", "typical of", 0.9, "PREPOSITION_PATTERN", "Wrong preposition after typical"),
        ("similar with", "similar to", 0.95, "PREPOSITION_PATTERN", "Wrong preposition after similar"),
        ("different with", "different from", 0.95, "PREPOSITION_PATTERN", "Wrong preposition after different"),
        ("in addition of", "in addition to", 0.95, "PREPOSITION_PATTERN", "Wrong preposition in connector"),
        ("according with", "according to", 0.95, "PREPOSITION_PATTERN", "Wrong preposition after according"),
        ("believe on", "believe in", 0.9, "PREPOSITION_PATTERN", "Wrong preposition after believe"),
        ("participate to", "participate in", 0.95, "PREPOSITION_PATTERN", "Wrong preposition after participate"),
        ("impact to society", "impact on society", 0.9, "PREPOSITION_PATTERN", "Wrong preposition after impact"),
        ("effect to society", "effect on society", 0.95, "PREPOSITION_PATTERN", "Wrong preposition after effect"),
        ("influence to people", "influence on people", 0.9, "PREPOSITION_PATTERN", "Wrong preposition after influence"),
        ("lead in problems", "lead to problems", 0.95, "PREPOSITION_PATTERN", "Wrong preposition after lead"),
        ("deal to problems", "deal with problems", 0.95, "PREPOSITION_PATTERN", "Wrong preposition after deal"),
        ("pay attention on", "pay attention to", 0.95, "PREPOSITION_PATTERN", "Wrong preposition after attention"),
        ("take care about", "take care of", 0.95, "PREPOSITION_PATTERN", "Wrong preposition in phrase"),
        ("depend from", "depend on", 0.95, "PREPOSITION_PATTERN", "Wrong preposition after depend"),
        ("consist from", "consist of", 0.95, "PREPOSITION_PATTERN", "Wrong preposition after consist"),
        ("result in to", "result in", 0.9, "PREPOSITION_PATTERN", "Extra preposition after result"),
        ("result from to", "result from", 0.9, "PREPOSITION_PATTERN", "Extra preposition after result"),
        ("provide people with to", "provide people with", 0.88, "PREPOSITION_PATTERN", "Extra preposition after provide"),
        ("protect people from against", "protect people from", 0.88, "PREPOSITION_PATTERN", "Double preposition after protect"),
        ("prevent people to", "prevent people from", 0.95, "PREPOSITION_PATTERN", "Wrong pattern after prevent"),
        ("discourage people to", "discourage people from", 0.95, "PREPOSITION_PATTERN", "Wrong pattern after discourage"),
        ("encourage people for", "encourage people to", 0.9, "PREPOSITION_PATTERN", "Wrong pattern after encourage"),
        ("allow people for", "allow people to", 0.9, "PREPOSITION_PATTERN", "Wrong pattern after allow"),
        ("enable people for", "enable people to", 0.9, "PREPOSITION_PATTERN", "Wrong pattern after enable"),

        # B: Idiom / article / form errors
        ("on the hand", "on the one hand", 0.95, "COLLOCATION", "Missing word in contrast marker"),
        ("on other hand", "on the other hand", 0.95, "COLLOCATION", "Missing article in contrast marker"),
        ("at first of all", "first of all", 0.92, "COLLOCATION", "Malformed sequencing phrase"),
        ("in the conclusion", "in conclusion", 0.9, "COLLOCATION", "Wrong article in conclusion phrase"),
        ("as conclusion", "in conclusion", 0.9, "COLLOCATION", "Malformed conclusion phrase"),
        ("as a conclusion", "in conclusion", 0.88, "COLLOCATION", "Non-standard conclusion phrase"),
        ("make a conclusion", "draw a conclusion", 0.88, "COLLOCATION", "Wrong verb with conclusion"),
        ("draw conclusion", "draw a conclusion", 0.88, "COLLOCATION", "Missing article in fixed phrase"),
        ("come to conclusion", "come to a conclusion", 0.88, "COLLOCATION", "Missing article in fixed phrase"),
        ("in a fact", "in fact", 0.95, "COLLOCATION", "Wrong article in fixed phrase"),
        ("by the way of", "by way of", 0.85, "COLLOCATION", "Wrong article in fixed phrase"),
        ("as the result", "as a result", 0.9, "COLLOCATION", "Wrong article in result phrase"),
        ("as result", "as a result", 0.95, "COLLOCATION", "Missing article in result phrase"),
        ("for this case", "in this case", 0.88, "PREPOSITION_PATTERN", "Wrong preposition in case phrase"),
        ("in this reason", "for this reason", 0.92, "PREPOSITION_PATTERN", "Wrong preposition in reason phrase"),
        ("with this reason", "for this reason", 0.92, "PREPOSITION_PATTERN", "Wrong preposition in reason phrase"),
        ("in my side", "from my perspective", 0.8, "COLLOCATION", "Non-standard viewpoint phrase"),
        ("at the same time with", "at the same time as", 0.9, "PREPOSITION_PATTERN", "Wrong preposition in time phrase"),
        ("in nowadays", "nowadays", 0.95, "PREPOSITION_PATTERN", "Unnecessary preposition before nowadays"),
        ("at nowadays", "nowadays", 0.95, "PREPOSITION_PATTERN", "Unnecessary preposition before nowadays"),
        ("in these days", "these days", 0.9, "PREPOSITION_PATTERN", "Unnecessary preposition before time phrase"),
        ("at these days", "these days", 0.9, "PREPOSITION_PATTERN", "Unnecessary preposition before time phrase"),
        ("in the modern life", "in modern life", 0.82, "COLLOCATION", "Unnecessary article in general phrase"),

        # C: Opinion / viewpoint phrase errors
        ("by my opinion", "in my opinion", 0.95, "PREPOSITION_PATTERN", "Wrong preposition in opinion phrase"),
        ("according to me", "in my opinion", 0.85, "COLLOCATION", "Non-standard opinion phrase"),
        ("as my opinion", "in my opinion", 0.9, "COLLOCATION", "Non-standard opinion phrase"),
        ("for my opinion", "in my opinion", 0.9, "PREPOSITION_PATTERN", "Wrong preposition in opinion phrase"),
        ("in my view point", "from my point of view", 0.9, "COLLOCATION", "Wrong compound form"),
        ("from my point view", "from my point of view", 0.95, "COLLOCATION", "Missing preposition in viewpoint phrase"),
        ("in my perspective", "from my perspective", 0.88, "PREPOSITION_PATTERN", "Wrong preposition in perspective phrase"),
        ("on my perspective", "from my perspective", 0.88, "PREPOSITION_PATTERN", "Wrong preposition in perspective phrase"),
        ("as i concern", "as far as I am concerned", 0.88, "COLLOCATION", "Malformed opinion phrase"),
        ("as far i concerned", "as far as I am concerned", 0.9, "COLLOCATION", "Malformed opinion phrase"),
        ("as far as i concern", "as far as I am concerned", 0.9, "COLLOCATION", "Wrong verb form in phrase"),
        ("i am agree", "I agree", 0.95, "WORD_FORM", "Wrong verb structure with agree"),
        ("i am disagree", "I disagree", 0.95, "WORD_FORM", "Wrong verb structure with disagree"),
        ("i agree with this opinion that", "I agree that", 0.85, "COLLOCATION", "Wordy opinion phrase"),
        ("i support with this idea", "I support this idea", 0.9, "PREPOSITION_PATTERN", "Unnecessary preposition after support"),
        ("i support to this idea", "I support this idea", 0.9, "PREPOSITION_PATTERN", "Unnecessary preposition after support"),
        ("i object this idea", "I object to this idea", 0.9, "PREPOSITION_PATTERN", "Missing preposition after object"),
        ("i oppose against this idea", "I oppose this idea", 0.9, "PREPOSITION_PATTERN", "Unnecessary preposition after oppose"),

        # D: Make / Do confusion collocations
        ("do decisions", "make decisions", 0.95, "COLLOCATION", "Wrong verb with decisions"),
        ("make homework", "do homework", 0.95, "COLLOCATION", "Wrong verb with homework"),
        ("make housework", "do housework", 0.95, "COLLOCATION", "Wrong verb with housework"),
        ("make exercise", "do exercise", 0.9, "COLLOCATION", "Wrong verb with exercise"),
        ("do a choice", "make a choice", 0.95, "COLLOCATION", "Wrong verb with choice"),
        ("do choices", "make choices", 0.95, "COLLOCATION", "Wrong verb with choices"),
        ("make researches", "do research", 0.9, "COLLOCATION", "Wrong verb with research"),
        ("do a plan", "make a plan", 0.92, "COLLOCATION", "Wrong verb with plan"),
        ("do plans", "make plans", 0.92, "COLLOCATION", "Wrong verb with plans"),
        ("make damage", "cause damage", 0.9, "COLLOCATION", "Wrong verb with damage"),
        ("make harm", "cause harm", 0.95, "COLLOCATION", "Wrong verb with harm"),
        ("do harm for", "do harm to", 0.9, "PREPOSITION_PATTERN", "Wrong preposition after harm"),
        ("make benefit", "bring benefits", 0.9, "COLLOCATION", "Wrong verb with benefit"),
        ("do benefit", "bring benefits", 0.9, "COLLOCATION", "Wrong verb with benefit"),
        ("make an effect", "have an effect", 0.9, "COLLOCATION", "Wrong verb with effect"),
        ("do an effect", "have an effect", 0.9, "COLLOCATION", "Wrong verb with effect"),
        ("make influence", "have an influence", 0.9, "COLLOCATION", "Wrong verb with influence"),
        ("do influence", "have an influence", 0.9, "COLLOCATION", "Wrong verb with influence"),
        ("make pressure", "put pressure on", 0.9, "COLLOCATION", "Wrong verb with pressure"),
        ("do pressure", "put pressure on", 0.9, "COLLOCATION", "Wrong verb with pressure"),
        ("make a progress", "make progress", 0.95, "COLLOCATION", "Uncountable noun article error"),
        ("do improvement", "make improvements", 0.9, "COLLOCATION", "Wrong verb with improvement"),
        ("make responsibility", "take responsibility", 0.9, "COLLOCATION", "Wrong verb with responsibility"),

        # E: Wrong verb + specific object
        ("make a solution", "find a solution", 0.92, "COLLOCATION", "Wrong verb with solution"),
        ("do a solution", "find a solution", 0.92, "COLLOCATION", "Wrong verb with solution"),
        ("solve a solution", "find a solution", 0.95, "COLLOCATION", "Wrong verb with solution"),
        ("find the decision", "make the decision", 0.8, "COLLOCATION", "Wrong verb with decision"),
        ("take a choice", "make a choice", 0.95, "COLLOCATION", "Wrong verb with choice"),
        ("take an action", "take action", 0.8, "COLLOCATION", "Unnecessary article in phrase"),
        ("put effort on", "put effort into", 0.9, "PREPOSITION_PATTERN", "Wrong preposition after effort"),
        ("spend effort", "make an effort", 0.88, "COLLOCATION", "Wrong verb with effort"),
        ("spend attention", "pay attention", 0.95, "COLLOCATION", "Wrong verb with attention"),
        ("give attention to", "pay attention to", 0.82, "COLLOCATION", "Wrong verb with attention"),
        ("collect knowledge", "gain knowledge", 0.8, "COLLOCATION", "Wrong verb with knowledge"),
        ("learn knowledge", "gain knowledge", 0.9, "COLLOCATION", "Wrong verb with knowledge"),
        ("take experience", "gain experience", 0.95, "COLLOCATION", "Wrong verb with experience"),
        ("collect experience", "gain experience", 0.9, "COLLOCATION", "Wrong verb with experience"),
        ("do experience", "gain experience", 0.95, "COLLOCATION", "Wrong verb with experience"),
        ("make experience", "gain experience", 0.95, "COLLOCATION", "Wrong verb with experience"),
        ("take benefits from", "benefit from", 0.8, "COLLOCATION", "Wordy benefit phrase"),
        ("bring disadvantages to", "create disadvantages for", 0.8, "COLLOCATION", "Unnatural verb with disadvantages"),
        ("cause advantages", "bring advantages", 0.88, "COLLOCATION", "Wrong verb with advantages"),
        ("cause benefits", "bring benefits", 0.88, "COLLOCATION", "Wrong verb with benefits"),
        ("create harms", "cause harm", 0.92, "COLLOCATION", "Wrong verb and noun form"),
        ("make problems", "create problems", 0.9, "COLLOCATION", "Wrong verb with problems"),

        # F: Wordy / non-standard expressions
        ("amount of people", "number of people", 0.92, "NOUN_NUMBER_COUNTABILITY", "Wrong quantifier with people"),
        ("amount of citizens", "number of citizens", 0.92, "NOUN_NUMBER_COUNTABILITY", "Wrong quantifier with citizens"),
        ("amount of workers", "number of workers", 0.92, "NOUN_NUMBER_COUNTABILITY", "Wrong quantifier with workers"),
        ("little amount of workers", "small number of workers", 0.9, "NOUN_NUMBER_COUNTABILITY", "Wrong quantifier with count noun"),
        ("huge amount of people", "large number of people", 0.9, "NOUN_NUMBER_COUNTABILITY", "Wrong quantifier with people"),
        ("many amount of", "a large amount of", 0.9, "NOUN_NUMBER_COUNTABILITY", "Malformed quantity phrase"),
        ("much people", "many people", 0.95, "NOUN_NUMBER_COUNTABILITY", "Wrong quantifier with people"),
        ("many money", "much money", 0.95, "NOUN_NUMBER_COUNTABILITY", "Wrong quantifier with money"),
        ("many information", "much information", 0.95, "NOUN_NUMBER_COUNTABILITY", "Wrong quantifier with information"),
        ("many knowledge", "much knowledge", 0.95, "NOUN_NUMBER_COUNTABILITY", "Wrong quantifier with knowledge"),
        ("a lot of knowledge and experience than", "more knowledge and experience than", 0.9, "COLLOCATION", "Wrong comparative phrase"),
        ("more better", "better", 0.95, "COMPARATIVE_FORM", "Double comparative"),
        ("more easier", "easier", 0.95, "COMPARATIVE_FORM", "Double comparative"),
        ("more cheaper", "cheaper", 0.95, "COMPARATIVE_FORM", "Double comparative"),
        ("more faster", "faster", 0.95, "COMPARATIVE_FORM", "Double comparative"),
        ("most easiest", "easiest", 0.95, "COMPARATIVE_FORM", "Double superlative"),
        ("most best", "best", 0.95, "COMPARATIVE_FORM", "Double superlative"),
        ("elderly people benefits", "elderly people benefit", 0.8, "NOUN_NUMBER_COUNTABILITY", "Agreement in fixed learner pattern"),
        ("an elderly people", "elderly people", 0.95, "NOUN_NUMBER_COUNTABILITY", "Article with plural phrase"),
        ("many an ageing people", "many elderly people", 0.92, "NOUN_NUMBER_COUNTABILITY", "Malformed noun phrase"),
        ("every countries", "every country", 0.95, "NOUN_NUMBER_COUNTABILITY", "Wrong number after every"),
        ("each countries", "each country", 0.95, "NOUN_NUMBER_COUNTABILITY", "Wrong number after each"),
        ("all over world", "all over the world", 0.95, "COLLOCATION", "Missing article in fixed phrase"),
        ("in all world", "around the world", 0.9, "COLLOCATION", "Non-standard global phrase"),
        ("during they life", "during their life", 0.95, "WORD_FORM", "Pronoun form in fixed phrase"),
        ("during their hole life", "throughout their whole life", 0.9, "COLLOCATION", "Malformed life phrase"),
        ("in the future life", "in the future", 0.85, "COLLOCATION", "Wordy time phrase"),
        ("the young generation people", "young people", 0.85, "COLLOCATION", "Wordy noun phrase"),
        ("old generation people", "older generations", 0.8, "COLLOCATION", "Wordy noun phrase"),
        ("elder generation", "older generation", 0.9, "COLLOCATION", "Wrong adjective form"),
        ("middle-age people", "middle-aged people", 0.95, "COLLOCATION", "Wrong adjective form"),
        ("retired people age", "retirement age", 0.9, "COLLOCATION", "Malformed noun phrase"),
        ("government wallet", "government budget", 0.9, "COLLOCATION", "Non-standard academic phrase"),
        ("feel the labour market", "fill the labour market", 0.9, "COLLOCATION", "Wrong verb in labour phrase"),
        ("open them opportunities", "create opportunities for them", 0.9, "COLLOCATION", "Malformed opportunity phrase"),
        ("affect badly to", "negatively affect", 0.92, "PREPOSITION_PATTERN", "Wrong affect pattern"),
        ("affect positively on", "positively affect", 0.92, "PREPOSITION_PATTERN", "Wrong affect pattern"),
        ("in such of country", "in such a country", 0.92, "COLLOCATION", "Malformed such phrase"),
        ("the nature is beauty", "nature is beautiful", 0.95, "COLLOCATION", "Wrong word form phrase"),
        ("it can be help to", "it can help", 0.92, "COLLOCATION", "Malformed help phrase"),
        ("be agree with", "agree with", 0.95, "WORD_FORM", "Wrong verb structure with agree"),
        ("have no arguments to be agree", "have no reason to agree", 0.88, "WORD_FORM", "Malformed argument phrase"),

    ]

    positive_registry: set = getattr(resources, "positive_collocations", set())
    cands: List[Candidate] = []
    import re as _re2

    for sent in segmentation["sentences"]:
        txt_low = sent["text"].lower()
        txt_orig = sent["text"]
        for wrong, correct, conf, family, description in _FIXED_PHRASE_PATTERNS:
            if conf <= 0.0:
                continue  # skip guard entries
            # positive registry veto
            if wrong in positive_registry or tuple(wrong.split()) in positive_registry:
                continue
            pat = r"\b" + _re2.escape(wrong) + r"\b"
            m = _re2.search(pat, txt_low, _re2.IGNORECASE)
            if not m:
                continue
            quote = txt_orig[m.start(): m.end()]
            st = sent["char_start"] + m.start()
            en = sent["char_start"] + m.end()
            rec_gain = 0.55 if conf >= 0.90 else 0.45
            eva_gain = 0.50 if conf >= 0.90 else 0.40
            cands.append(make_candidate(
                run_id, submission_id, essay_id,
                "layer3_local_language", "fixed_phrase_pass",
                quote, txt_orig, st, en, sent,
                family,
                "change_collocation" if family == "COLLOCATION" else "change_preposition",
                description,
                f"Fixed-phrase lookup: '{wrong}' is a known IELTS error pattern. Correct: '{correct}'",
                conf,
                {"lookup_correct": correct, "pattern": wrong, "fixed_phrase_v18a": True},
                None,
                "change_collocation" if family == "COLLOCATION" else "change_preposition",
                correct,
                "root", [],
                "collocation" if family == "COLLOCATION" else "preposition",
                rec_gain, eva_gain, 0.45,
            ))
    return cands


def l3_rule_span_confirm(
    run_id: str, submission_id: str, essay_id: str,
    rule_candidates: List[Candidate],
    segmentation: Dict[str, Any],
    tracker: "LLMTracker", llm_enabled: bool,
) -> List[Candidate]:
    """Stage B: LLM confirmation + family correction of rule/LT-anchored spans.

    Returns Candidate objects with source_engine='rule_llm_confirmed'.
    These are picked up by stage 6 via stage6_rule_llm_confirmed condition.
    LLM disabled → returns empty list (rule candidates still go through normally).
    """
    # v18 alias: delegate to l3_universal_confirm
    return l3_universal_confirm(
        run_id, submission_id, essay_id,
        rule_candidates, segmentation, tracker, llm_enabled
    )


def l3_universal_confirm(
    run_id: str, submission_id: str, essay_id: str,
    rule_candidates: List[Candidate],
    segmentation: Dict[str, Any],
    tracker: "LLMTracker", llm_enabled: bool,
) -> List[Candidate]:
    """v18 Stage B: Universal LLM confirmation.

    Accepts candidates from rules, LT, spaCy, collocation_lookup, slr_collocate.
    Returns Candidate objects with source_engine='universal_confirm'.
    Includes recoverability_gain + evaluability_gain in prompt context.
    LLM disabled → returns empty list.
    """
    if not llm_enabled:
        return []
    to_confirm = [
        c for c in rule_candidates
        if c.source_engine in _UNIVERSAL_CONFIRM_ENGINES
    ][:_UNIVERSAL_CONFIRM_MAX]
    if not to_confirm:
        return []

    system = "\n".join([
        "You are a strict IELTS grammar and lexical error validator.",
        "You receive candidate errors found by automated rule detectors.",
        "For each: confirmed=true if the flagged text is DEFINITELY wrong in context.",
        "confirmed=false for borderline cases, stylistic choices, locale variants,",
        "or anything a competent native speaker might write.",
        "Be strict — only confirm what a trained IELTS examiner would definitely note.",
        "",
        "Also correct the family if the rule mis-classified. Valid families:",
        "  VERB_FORM, CLAUSE_STRUCTURE, ARTICLE_DETERMINER, PREPOSITION_PATTERN,",
        "  SUBJECT_VERB_AGREEMENT, NOUN_NUMBER_COUNTABILITY, COMPARATIVE_FORM,",
        "  WORD_FORM, COLLOCATION, WORD_CHOICE, LEXICAL_PRECISION, SPELLING,",
        "  CONDITIONAL_STRUCTURE, VERB_PATTERN, CONSTRUCTION",
        "",
        "Return JSON: {results:[{index,confirmed,family,confidence}]}",
        "Omit items where confirmed=false to keep response short.",
    ])

    items_text: List[str] = []
    for idx, c in enumerate(to_confirm):
        _rec = getattr(c, "recoverability_gain", None) or 0.45
        _eva = getattr(c, "evaluability_gain", None) or 0.35
        items_text.append(
            f"[{idx}] quote={c.quote!r} | sentence={c.local_quote!r} "
            f"| family={c.family_candidate} | src={c.source_engine} "
            f"| problem={c.problem_statement[:80]} "
            f"| rec_gain={_rec:.2f} | eva_gain={_eva:.2f}"
        )

    prompt = (
        "Validate each rule-detected candidate. "
        "Confirm only definite, clear IELTS-relevant errors.\n\n"
        + "\n".join(items_text)
    )

    data = llm_json(prompt, system, CHEAP_MODEL, "universal_confirm", tracker, llm_enabled, 1000)
    results = data.get("results", []) if isinstance(data, dict) else []

    confirmed_out: List[Candidate] = []
    for item in (results if isinstance(results, list) else []):
        idx = item.get("index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(to_confirm):
            continue
        if not item.get("confirmed", False):
            continue
        c = to_confirm[idx]
        new_fam = str(item.get("family") or c.family_candidate).upper().strip()
        if new_fam not in FAMILY_TO_RUBRIC:
            new_fam = c.family_candidate
        new_rub  = FAMILY_TO_RUBRIC.get(new_fam, c.rubric_candidate)
        new_conf = clamp(safe_float(item.get("confidence"), c.confidence))
        bd = c.__dict__.copy()
        bd["source_engine"]          = "universal_confirm"
        bd["family_candidate"]       = new_fam
        bd["rubric_candidate"]       = new_rub
        bd["confidence"]             = new_conf
        bd["family_lock_status"]     = "v18_universal_confirm"
        bd["family_lock_confidence"] = new_conf
        bd["candidate_id"]           = stable_id(
            "cand", run_id, essay_id, "universal_confirm",
            c.quote, str(c.span_start), new_fam
        )
        confirmed_out.append(Candidate(**bd))

    return confirmed_out



def l3_slr_collocate_pass(
    run_id: str, submission_id: str, essay_id: str,
    segmentation: Dict[str, Any], semantic: Dict[str, Any],
    tracker: "LLMTracker", llm_enabled: bool,
) -> List[Candidate]:
    """SLR sub-pass A: COLLOCATION + SEMANTIC_COMBINATION only.
    Structural collocate errors. Source engine: 'slr_collocate_pass'."""
    cands: List[Candidate] = []
    ALLOWED = {"COLLOCATION", "SEMANTIC_COMBINATION"}
    system = "\n".join([
        "You are an IELTS error detector specialised in COLLOCATION and SEMANTIC_COMBINATION only.",
        "These are your ONLY two permitted families. Do NOT output any other family.",
        "",
        "COLLOCATION — the verb, adjective, or noun collocate is wrong for this fixed English phrase:",
        "  'do a mistake'       -> 'make a mistake'   (make, not do)",
        "  'make a research'    -> 'conduct research'  (conduct, not make)",
        "  'rise awareness'     -> 'raise awareness'   (raise, not rise)",
        "  'take a decision'    -> 'make a decision'   (make, not take)",
        "  'give a contribution'-> 'make a contribution'",
        "  'strong knowledge'   -> 'extensive knowledge'",
        "  NOT COLLOCATION: 'do their homework', 'make a plan', 'give advice' (correct phrases).",
        "  NOT COLLOCATION: grammar errors, wrong verb form, wrong preposition.",
        "  NOT COLLOCATION: vague words like 'things', 'very big' (those are LEXICAL_PRECISION).",
        "",
        "SEMANTIC_COMBINATION — words individually correct but semantically incompatible:",
        "  'mental pollution'   -> 'psychological damage'",
        "  'moral damage'       -> 'moral harm'",
        "  NOT SEMANTIC_COMBINATION: wrong collocate verb (that is COLLOCATION).",
        "",
        "QUOTING RULE: quote ONLY the collocating phrase (2-5 words). Never quote the full sentence.",
        "repair_hypothesis: write the corrected phrase ONLY. No instructions, no 'change to'.",
        "confidence: 0.78-0.95. Only flag when you are certain the combination is wrong in English.",
        "Return JSON: {candidates:[{sentence_index,quote,family,problem,explanation,confidence,repair_hypothesis}]}",
        "Return {candidates:[]} if no errors found.",
    ])
    by_para: Dict[int, List[Dict[str, Any]]] = {}
    for s in segmentation["sentences"]:
        by_para.setdefault(s["paragraph_index"], []).append(s)
    for para_idx, sents in sorted(by_para.items()):
        sent_block = "\n".join(f"S{s['sentence_index']}: {s['text']}" for s in sents)
        prompt = (
            f"Paragraph {para_idx} — detect COLLOCATION and SEMANTIC_COMBINATION errors only:\n"
            f"{sent_block}\n"
            f"Return JSON {{candidates:[]}} if none found."
        )
        data = llm_json(prompt, system, CHEAP_MODEL, "SLR_collocate", tracker, llm_enabled, 1000)
        items = data.get("candidates", []) if isinstance(data, dict) else []
        if isinstance(items, list):
            cands.extend(_slr_parse_items(items, sents, ALLOWED,
                                          run_id, submission_id, essay_id,
                                          "slr_collocate_pass"))
    return cands


def l3_slr_lexical_pass(
    run_id: str, submission_id: str, essay_id: str,
    segmentation: Dict[str, Any], semantic: Dict[str, Any],
    tracker: "LLMTracker", llm_enabled: bool,
) -> List[Candidate]:
    """SLR sub-pass B: WORD_CHOICE + LEXICAL_PRECISION (v18c: LEXICAL_PRECISION restored).
    v18b gated off LEXICAL_PRECISION due to high FP rate on vague-but-acceptable phrasing.
    v18c restores it with: confidence floor 0.82, tighter negative examples, and
    explicit prohibition on common student phrases that are NOT precision errors.
    Source engine: 'slr_lexical_pass'."""
    cands: List[Candidate] = []
    # v18c: LEXICAL_PRECISION restored with confidence floor (see post-parse filter below).
    ALLOWED = {"WORD_CHOICE", "LEXICAL_PRECISION"}
    system = "\n".join([
        "You are an IELTS error detector for WORD_CHOICE and LEXICAL_PRECISION errors only.",
        "",
        "WORD_CHOICE — wrong word selected (correct grammatical form, wrong meaning/usage):",
        "  'economic' when 'economical' needed  |  'affect' vs 'effect'",
        "  'principal' vs 'principle'  |  'complement' vs 'compliment'",
        "  NOT WORD_CHOICE: vague words (those are LEXICAL_PRECISION).",
        "",
        "LEXICAL_PRECISION — word/phrase is grammatically correct but too vague or",
        "  imprecise for IELTS academic writing at Band 7+:",
        "  'go up' → 'increase'  |  'very big problem' → 'significant challenge'",
        "  'things' for academic concepts → name the concept specifically",
        "  'artificial things' → 'artificial content' / 'fabricated imagery'",
        "  'regular self-detox' → not standard academic phrasing",
        "  DO NOT flag any of these — they are acceptable student writing:",
        "  'convenient foods', 'unhealthy food', 'cooked quickly', 'feel tired'",
        "  'regularly consume', 'money used for', 'both aspects', 'both perspectives'",
        "  'consideration of', standard reporting verbs, common lifestyle vocabulary,",
        "  any word that is informal but clearly understood in academic context.",
        "",
        "QUOTING RULE: quote ONLY the imprecise/wrong word or phrase (1–4 words).",
        "repair_hypothesis: the precise replacement ONLY. No instructions.",
        "confidence: WORD_CHOICE 0.80–0.95 | LEXICAL_PRECISION 0.82–0.95 (strict).",
        "Only flag where a Band 7+ IELTS examiner would DEFINITELY mark the word down.",
        "Return JSON: {candidates:[{sentence_index,quote,family,problem,explanation,confidence,repair_hypothesis}]}",
        "Return {candidates:[]} if no errors found.",
    ])
    by_para: Dict[int, List[Dict[str, Any]]] = {}
    for s in segmentation["sentences"]:
        by_para.setdefault(s["paragraph_index"], []).append(s)
    for para_idx, sents in sorted(by_para.items()):
        sent_block = "\n".join(f"S{s['sentence_index']}: {s['text']}" for s in sents)
        prompt = (
            f"Paragraph {para_idx} — detect LEXICAL_PRECISION and WORD_CHOICE errors only:\n"
            f"{sent_block}\n"
            f"Return JSON {{candidates:[]}} if none found."
        )
        data = llm_json(prompt, system, CHEAP_MODEL, "SLR_lexical", tracker, llm_enabled, 1000)
        items = data.get("candidates", []) if isinstance(data, dict) else []
        if isinstance(items, list):
            parsed = _slr_parse_items(items, sents, ALLOWED,
                                      run_id, submission_id, essay_id,
                                      "slr_lexical_pass")
            # v18c: enforce confidence floor for LEXICAL_PRECISION — stricter than WORD_CHOICE
            for _c in parsed:
                if _c.family_candidate == "LEXICAL_PRECISION" and _c.confidence < 0.82:
                    _c.confidence = 0.0  # zeroed → will not pass chargeability gate
            cands.extend(parsed)
    return cands


# ── v15: Dedicated discourse TR pass (gate bug fixed + prompt tightened) ─────
# v14 bug: discourse gate fired AFTER stage6_discourse_tr_dedicated_pass and
# unconditionally set is_chargeable=False. Fixed in Stage 6 (patch 4 above).
# v15 also tightens: explicit GRA/LR prohibition in prompt + hard CC/TR-only
# parse gate so mislabelled GRA/LR families are silently dropped.
# Source engine: "discourse_tr_pass" -> stage6_discourse_tr_dedicated_pass.

def l2_discourse_tr_pass(
    run_id: str, submission_id: str, essay_id: str,
    segmentation: Dict[str, Any], semantic: Dict[str, Any],
    tracker: "LLMTracker", llm_enabled: bool,
) -> List[Candidate]:
    """Dedicated TR/CC discourse pass: WEAK_EXAMPLE, CLAIM_SUPPORT_LINK,
    UNSUPPORTED_CLAIM, LOGICAL_PROGRESSION. Hard gate: CC/TR families only."""
    cands: List[Candidate] = []
    DTR_ALLOWED = CC_FAMILIES | TR_FAMILIES  # hard gate — no GRA/LR from this pass
    system = "\n".join([
        "You are an IELTS Task Response and Coherence-Cohesion detector.",
        "You detect ONLY discourse-level issues — argument structure, example quality,",
        "idea progression at paragraph level.",
        "CRITICAL: Do NOT flag local grammar errors (wrong verb form, wrong article, spelling,",
        "wrong word form) or vocabulary errors (wrong word, informal word, collocation).",
        "Those are handled by separate detectors. NEVER output COLLOCATION, WORD_CHOICE,",
        "VERB_FORM, ARTICLE_DETERMINER, SPELLING, or any other GRA/LR family.",
        "",
        "Your ONLY permitted families:",
        "  WEAK_EXAMPLE        - claim supported by a vague/generic example with no",
        "                        specific detail: 'For example, many people believe this.'",
        "                        NOT weak: a named statistic, specific country, or study.",
        "  CLAIM_SUPPORT_LINK  - a clear claim is made but the paragraph provides NO",
        "                        supporting evidence, reason, or elaboration at all.",
        "  UNSUPPORTED_CLAIM   - an assertion stated as fact with no reasoning or evidence.",
        "  LOGICAL_PROGRESSION - one sentence directly contradicts or ignores the claim",
        "                        made in the immediately preceding sentence.",
        "",
        "Quote the sentence (or opening clause) where the issue occurs.",
        "repair_hypothesis: one concrete sentence saying what specific evidence is missing.",
        "confidence: 0.85-0.92. Only flag when the issue is clear and significant.",
        "Return JSON: {candidates:[{sentence_index,quote,family,problem,explanation,confidence,repair_hypothesis}]}",
        "Return {candidates:[]} if no issues found.",
    ])
    paragraphs = segmentation.get("paragraphs", [])
    by_para: Dict[int, List[Dict[str, Any]]] = {}
    for s in segmentation["sentences"]:
        by_para.setdefault(s["paragraph_index"], []).append(s)

    for para in paragraphs:
        pidx = para.get("paragraph_index", 0)
        sents = by_para.get(pidx, [])
        if not sents or len(sents) < 2:  # skip single-sentence paragraphs
            continue
        para_text = para.get("text", " ".join(s["text"] for s in sents))
        sent_block = "\n".join(f"S{s['sentence_index']}: {s['text']}" for s in sents)
        prompt = (
            f"Paragraph {pidx} ({len(sents)} sentences):\n{sent_block}\n\n"
            f"Full paragraph:\n{para_text}\n\n"
            f"Detect ONLY: WEAK_EXAMPLE / CLAIM_SUPPORT_LINK / UNSUPPORTED_CLAIM / LOGICAL_PROGRESSION.\n"
            f"Do NOT flag grammar or vocabulary errors.\n"
            f"Return JSON {{candidates:[]}} if none found."
        )
        data = llm_json(prompt, system, CHEAP_MODEL, "DTR_pass", tracker, llm_enabled, 1000)
        items = data.get("candidates", []) if isinstance(data, dict) else []
        if not isinstance(items, list):
            continue
        by_si = {s["sentence_index"]: s for s in sents}
        for it in items[:5]:
            fam = str(it.get("family") or "").upper()
            if fam not in DTR_ALLOWED:  # hard gate — drop GRA/LR mislabels
                continue
            si_raw = it.get("sentence_index")
            try:
                si = int(si_raw) if si_raw is not None else sents[0]["sentence_index"]
            except (ValueError, TypeError):
                si = sents[0]["sentence_index"]
            sent = by_si.get(si) or sents[0]
            quote = normalize_space(str(it.get("quote") or sent.get("text", "")))
            if not quote:
                continue
            rel = sent["text"].lower().find(quote.lower())
            st = sent["char_start"] + (rel if rel >= 0 else 0)
            en = min(sent["char_end"], st + len(quote))
            conf = clamp(safe_float(it.get("confidence"), 0.78))
            repair = str(it.get("repair_hypothesis") or "")
            layer = ("layer2_sentence_discourse" if fam == "LOGICAL_PROGRESSION"
                     else "layer1_wide_discourse")
            cands.append(make_candidate(
                run_id, submission_id, essay_id,
                layer, "discourse_tr_pass",
                quote, sent["text"], st, en, sent,
                fam, "ADD_EXPLANATION",
                str(it.get("problem") or "Discourse / task response issue"),
                str(it.get("explanation") or "DTR pass identified a discourse issue."),
                conf, {"dtr_item": it}, CHEAP_MODEL,
                "ADD_EXPLANATION", repair,
                "root", [], "unspecified",
                0.30, 0.55, 0.40,
            ))
    return cands

# ── v13: Strengths detection ──────────────────────────────────────────────────
# Identifies genuine positive features of the essay across all four IELTS rubrics.
# Results go into evaluator_payload.strengths_profile (evaluator_payload has no
# additionalProperties constraint, so this is contract-compliant with DETECTOR_OUTPUT_V1.1).

def detect_strengths(
    essay_text: str,
    segmentation: Dict[str, Any],
    task_profile: Dict[str, Any],
    semantic: Dict[str, Any],
    tracker: LLMTracker,
    llm_enabled: bool,
) -> Dict[str, Any]:
    """Identify positive writing features across lexical, grammatical, cohesion, and task-response rubrics."""

    sentences = segmentation.get("sentences", [])

    # ── Rule-based strength signals (always run, no API cost) ─────────────────

    # 1. Academic register markers
    ACADEMIC_RE = re.compile(
        r"\b(furthermore|moreover|consequently|nevertheless|nonetheless|"
        r"in addition|in contrast|by contrast|on the other hand|"
        r"it is worth noting|it can be argued|this suggests|this demonstrates|"
        r"to illustrate|for instance|in particular|specifically|"
        r"significant|substantial|fundamental|critical|crucial|"
        r"examine|analyse|consider|demonstrate|indicate|highlight|"
        r"perspective|approach|impact|implications|extent)\b", re.I
    )
    academic_hits: List[str] = []
    for s in sentences:
        hits = ACADEMIC_RE.findall(s["text"])
        academic_hits.extend(hits[:2])
    academic_hits = list(dict.fromkeys(h.lower() for h in academic_hits))  # deduplicate, preserve order

    # 2. Complex grammatical structures
    COMPLEX_STRUCT_RE = re.compile(
        r"\b(although|while|whereas|despite|even though|in spite of|"
        r"not only|both|neither|if|which|who|where|when)\b", re.I
    )
    complex_count = sum(1 for s in sentences if COMPLEX_STRUCT_RE.search(s["text"]))
    n_sent = max(len(sentences), 1)

    # 3. Discourse / cohesion markers
    DISCOURSE_RE = re.compile(
        r"\b(firstly|secondly|thirdly|finally|in conclusion|to begin with|"
        r"in the first place|additionally|as a result|therefore|thus|hence|"
        r"consequently|to sum up|in summary|overall)\b", re.I
    )
    discourse_hits: List[str] = []
    for s in sentences:
        discourse_hits.extend(m.group().lower() for m in DISCOURSE_RE.finditer(s["text"]))
    discourse_hits = list(dict.fromkeys(discourse_hits))

    # 4. Variety of sentence-opening patterns
    openers = [s["text"].split()[0].lower() if s["text"].split() else "" for s in sentences]
    opener_variety = len(set(openers)) / n_sent if n_sent else 0

    # Build rule-based signals
    rule_lexical: List[Dict[str, Any]] = []
    rule_grammatical: List[Dict[str, Any]] = []
    rule_cohesion: List[Dict[str, Any]] = []

    if len(academic_hits) >= 3:
        rule_lexical.append({
            "type": "academic_register",
            "description": "Uses academic vocabulary appropriate for IELTS formal writing",
            "examples": academic_hits[:5],
            "confidence": 0.85,
        })
    if complex_count >= n_sent * 0.35 and n_sent >= 5:
        rule_grammatical.append({
            "type": "complex_sentence_structures",
            "description": "Demonstrates a range of complex grammatical structures",
            "complex_sentence_count": complex_count,
            "ratio": round(complex_count / n_sent, 2),
            "confidence": 0.80,
        })
    if len(discourse_hits) >= 2:
        rule_cohesion.append({
            "type": "cohesive_devices",
            "description": "Uses cohesive devices to organise and connect ideas",
            "examples": discourse_hits[:5],
            "confidence": 0.90,
        })
    if opener_variety >= 0.6 and n_sent >= 5:
        rule_grammatical.append({
            "type": "sentence_variety",
            "description": "Varied sentence openings show structural range",
            "opener_variety_ratio": round(opener_variety, 2),
            "confidence": 0.75,
        })

    # ── LLM-based strength analysis (runs when LLM is live) ──────────────────
    llm_lexical: List[Dict[str, Any]] = []
    llm_grammatical: List[Dict[str, Any]] = []
    llm_cohesion: List[Dict[str, Any]] = []
    llm_task_response: List[Dict[str, Any]] = []
    llm_summary: Dict[str, Any] = {}

    if llm_enabled:
        words_list = essay_text.split()
        essay_trim = " ".join(words_list[:600]) + ("..." if len(words_list) > 600 else "")

        prompt = (
            "Analyse this IELTS Writing Task 2 essay for STRENGTHS ONLY — do not list errors.\n"
            f"Essay:\n{essay_trim}\n\n"
            "Return JSON:\n"
            "{\n"
            '  "overall_strength_band_estimate": <float 5.0–9.0 based on strengths only>,\n'
            '  "key_positive_summary": "<one sentence: the essay\'s main strength>",\n'
            '  "best_sentence_example": "<quote the single most effective sentence verbatim>",\n'
            '  "lexical_strengths": [<up to 3 specific string observations>],\n'
            '  "grammatical_strengths": [<up to 3 specific string observations>],\n'
            '  "cohesion_strengths": [<up to 2 specific string observations>],\n'
            '  "task_response_strengths": [<up to 2 specific string observations>]\n'
            "}\n"
            "Be specific and refer to features that would positively affect IELTS band descriptors."
        )
        system = (
            "You are an experienced IELTS examiner identifying genuine writing strengths. "
            "Respond in JSON only. Be specific, accurate, and constructive."
        )
        data = llm_json(prompt, system, CHEAP_MODEL, "strengths_analysis", tracker, llm_enabled, 700)

        if data and isinstance(data, dict):
            llm_summary = {
                "overall_strength_band_estimate": data.get("overall_strength_band_estimate"),
                "key_positive_summary": str(data.get("key_positive_summary") or ""),
                "best_sentence_example": str(data.get("best_sentence_example") or ""),
                "model_used": CHEAP_MODEL,
            }
            if data.get("lexical_strengths"):
                llm_lexical.append({
                    "type": "llm_lexical_assessment",
                    "items": [str(x) for x in data["lexical_strengths"]],
                    "confidence": 0.78,
                })
            if data.get("grammatical_strengths"):
                llm_grammatical.append({
                    "type": "llm_grammatical_assessment",
                    "items": [str(x) for x in data["grammatical_strengths"]],
                    "confidence": 0.78,
                })
            if data.get("cohesion_strengths"):
                llm_cohesion.append({
                    "type": "llm_cohesion_assessment",
                    "items": [str(x) for x in data["cohesion_strengths"]],
                    "confidence": 0.78,
                })
            if data.get("task_response_strengths"):
                llm_task_response.append({
                    "type": "llm_task_response_assessment",
                    "items": [str(x) for x in data["task_response_strengths"]],
                    "confidence": 0.78,
                })

    # ── Assemble strengths_profile ────────────────────────────────────────────
    strength_categories = {
        "lexical_resource": rule_lexical + llm_lexical,
        "grammar": rule_grammatical + llm_grammatical,
        "coherence_cohesion": rule_cohesion + llm_cohesion,
        "task_response": llm_task_response,
    }
    has_any = any(items for items in strength_categories.values())

    return {
        "strengths_profile_version": "v13.0",
        "has_strengths": has_any,
        "llm_strengths_available": bool(llm_summary),
        "overall_strength_band_estimate": llm_summary.get("overall_strength_band_estimate"),
        "key_positive_summary": llm_summary.get("key_positive_summary", ""),
        "best_sentence_example": llm_summary.get("best_sentence_example", ""),
        "strength_categories": strength_categories,
        "rule_signals": {
            "academic_vocabulary_count": len(academic_hits),
            "complex_sentence_ratio": round(complex_count / n_sent, 2),
            "discourse_marker_count": len(discourse_hits),
            "sentence_opener_variety": round(opener_variety, 2),
        },
        "model_used": llm_summary.get("model_used"),
        "feeds_to": ["EVALUATOR", "FEEDBACK_ENGINE", "WRITING_COACH"],
    }


def build_payloads(
    lists: Dict[str, Any],
    task_profile: Dict[str, Any],
    idea_map: Dict[str, Any],
    semantic: Dict[str, Any],
    segmentation: Dict[str, Any],
    strengths_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    chargeable = lists["chargeable_rows"]; review = lists["review_only_rows"]
    lret_families = {"SPELLING", "WORD_FORM", "COLLOCATION", "WORD_CHOICE", "LEXICAL_PRECISION", "SEMANTIC_COMBINATION", "REGISTER", "REPETITION", "REDUNDANCY"}
    lret_fix = [r for r in chargeable if r.get("layer") == "layer3_local_language" and r.get("rubric") == "lexical_resource" and r.get("family") in lret_families]
    evaluator_payload: Dict[str, Any] = {
        "task_schema_profile": task_profile,
        "idea_structure_profile": idea_map,
        "semantic_recoverability_profile": semantic,
        "all_detector_evidence": chargeable + review,
        "segmentation": segmentation,
    }
    # v13: strengths_profile added to evaluator_payload (contract-compliant: evaluator_payload
    # has no additionalProperties: false constraint in DETECTOR_OUTPUT_V1.1)
    if strengths_profile is not None:
        evaluator_payload["strengths_profile"] = strengths_profile
    # v18d.1: scorer_payload expansion ──────────────────────────────────────────
    _n_sentences   = max(1, len(segmentation.get("sentences") or []))
    _n_paragraphs  = max(1, len(segmentation.get("paragraphs") or []))
    _wc_seg        = max(1, len(words(
        " ".join(s.get("text", "") for s in segmentation.get("sentences", []))
    )))

    # error_free_sentence_ratio: fraction of sentences with no chargeable grammar row
    _gra_damaged_sents = {
        r["sentence_index"]
        for r in chargeable
        if r.get("rubric") == "grammar"
        and r.get("sentence_index") is not None
        and r.get("score_charge_weight", 0.0) >= 0.35
    }
    _efsr = round(max(0.0, 1.0 - len(_gra_damaged_sents) / _n_sentences), 3)

    # compact task_profile block for scorer Gate 3 / task-schema gates
    _req_comps = list(task_profile.get("required_components") or [])
    _hits      = task_profile.get("prompt_part_hits") or {}
    _covered   = [c for c in _req_comps if _hits.get(c, False)]
    _missing   = list(task_profile.get("missing_required_components") or [])
    _hard_fail = list(task_profile.get("hard_fail_missing_components") or [])

    # LR positive signals: ocd hits from strengths_profile; LR11 proxy from damage density
    _LR_POSITIVE_FAMILIES = frozenset({
        "COLLOCATION", "LEXICAL_PRECISION", "SEMANTIC_COMBINATION", "WORD_CHOICE"
    })
    _lr_strengths = ((strengths_profile or {}).get("lexical") or {})
    _ocd_hits = int(
        _lr_strengths.get("multiword_hits")
        or _lr_strengths.get("collocation_positive_count")
        or 0
    )
    _lr_sem_dmg = sum(
        r.get("score_charge_weight", 0.0) for r in chargeable
        if r.get("rubric") == "lexical_resource"
        and r.get("family") in _LR_POSITIVE_FAMILIES
        and r.get("root_or_secondary", "root") == "root"
    )
    _lr11_proxy = round(max(0.0, min(1.0, 0.40 - _lr_sem_dmg / _wc_seg * 100)), 3)

    return {
        "scorer_payload": {
            "chargeable_detector_rows": chargeable,
            "review_only_detector_rows": review,
            "task_schema_profile": task_profile,
            "semantic_recoverability_profile": semantic,
            # v18d.1: metadata block required by scorer for all density / gate calculations
            "metadata": {
                "word_count":                _wc_seg,
                "sentence_count":            _n_sentences,
                "paragraph_count":           _n_paragraphs,
                "error_free_sentence_ratio": _efsr,
            },
            # v18d.1: compact task_profile for scorer Gate 3 task-schema TR gates
            # v18d.2: task_type_source added so scorer gate 8 can distinguish
            #         essay-text fallback (prompt missing) from registry-confirmed.
            "task_profile": {
                "task_type":                    task_profile.get("task_type"),
                "task_type_source":             task_profile.get("task_type_source"),
                "task_schema_id":               task_profile.get("task_schema_id"),
                "required_components":          _req_comps,
                "covered_required_components":  len(_covered),
                "total_required_components":    len(_req_comps),
                "missing_required_components":  _missing,
                "hard_fail_components":         _hard_fail,
                "task_completeness_confidence": task_profile.get("task_completeness_confidence"),
                "score_ready":                  task_profile.get("score_ready", False),
            },
            # v18d.1: Gate 7 LR high-band eligibility signals
            "lr_positive_signals": {
                "ocd_positive_hits":              _ocd_hits,
                "LR11_dynamic_multiword_density": _lr11_proxy,
            },
            "candidate_policy": {
                "raw_candidates_not_safe_for_metrics": True,
                "chargeable_rows_required_for_metrics": True,
            },
        },
        "evaluator_payload": evaluator_payload,
        "lret_fix_payload": {
            "validated_fix_candidates": lret_fix,
            "sentence_contexts": segmentation["sentences"],
            "policy": {
                "fix_only": True,
                "keep_enhance_generated_downstream": True,
                "pure_grammar_excluded": True,
                "should_affect_band": False,
            },
        },
    }

# [v18d.2] Derive TR5/TR6/TR7 directly from layer0_idea_map so the scorer does not
# have to rely on word-count / sem_rec proxies for these critical TR signals.
def _derive_tr567_from_idea_map(idea_map: Dict[str, Any]) -> Dict[str, Any]:
    """Return TR5, TR6, TR7 computed from the layer0 idea map structure.

    TR5 (idea_extension_depth): how well main ideas are extended with sub-points.
    TR6 (support_quality): presence and specificity of concrete supporting evidence.
    TR7 (conclusion_alignment): a proper conclusion paragraph was found.
    """
    if not idea_map or not isinstance(idea_map, dict):
        return {}

    am = idea_map.get("argument_map") or {}
    pm = idea_map.get("proposition_map") or {}
    seq = idea_map.get("idea_sequence") or []
    prm = idea_map.get("paragraph_role_map") or {}

    # TR5 — depth of idea extension
    # Signals: #argument branches, presence of support_or_examples branch, #propositions
    arg_branches = len(am) if isinstance(am, dict) else 0
    has_support_branch = "support_or_examples" in am if isinstance(am, dict) else False
    prop_count = len(pm) if isinstance(pm, dict) else 0
    idea_count = len(seq) if isinstance(seq, list) else 0
    TR5 = min(1.0, max(0.20,
        0.15
        + min(1.0, arg_branches / 3.0) * 0.35
        + (0.20 if has_support_branch else 0.0)
        + min(1.0, prop_count / 4.0) * 0.15
        + min(1.0, idea_count / 4.0) * 0.15
    ))

    # TR6 — support quality (specificity of evidence)
    # Signals: whether support_or_examples has detailed text, specific markers (numbers,
    # named entities, "for example", "for instance", "such as", statistics)
    _support_texts: List[str] = []
    if isinstance(am, dict):
        for v in am.values():
            if isinstance(v, dict):
                for sv in v.values():
                    if isinstance(sv, str) and len(sv) > 20:
                        _support_texts.append(sv.lower())
            elif isinstance(v, str) and len(v) > 20:
                _support_texts.append(v.lower())
    _specificity_markers = re.compile(
        r'\b(\d[\d,.%]+|for (example|instance)|such as|according to|research shows|'
        r'studies show|for instance|specifically|in particular)\b'
    )
    _specific_hits = sum(1 for t in _support_texts if _specificity_markers.search(t))
    TR6 = min(1.0, max(0.20,
        0.20
        + (0.25 if has_support_branch else 0.0)
        + min(1.0, _specific_hits / 3.0) * 0.35
        + (0.10 if len(_support_texts) >= 2 else 0.0)
        + min(1.0, prop_count / 4.0) * 0.10
    ))

    # TR7 — conclusion alignment
    conclusion = prm.get("conclusion") if isinstance(prm, dict) else None
    TR7 = 0.85 if conclusion else 0.25

    return {
        "TR5_idea_extension_depth": round(TR5, 4),
        "TR6_support_quality": round(TR6, 4),
        "TR7_conclusion_alignment": round(TR7, 4),
        "tr567_source": "layer0_idea_map_v18d2",
    }


def benchmark_diagnostics(lists: Dict[str,Any], word_limit_gate: Dict[str,Any]) -> Dict[str,Any]:
    rows = lists.get("chargeable_rows", [])
    bad_quotes = [r.get("row_id") for r in rows if not meaningful_quote(str(r.get("quote", "")))]
    lt_rows = [r for r in rows if "LanguageTool" in (r.get("source_engines") or [])]
    spacy_rows = [r for r in rows if "spaCy" in (r.get("source_engines") or [])]
    llm_rows = [r for r in rows if "llm" in [str(x).lower() for x in (r.get("source_engines") or [])]]
    return {"benchmark_mode_notes": ["Detector-only diagnostics; no scoring/lret enhance", "Word limit over 300 is logged as ceiling gate, not rejected in benchmark mode."], "bad_quote_row_ids": bad_quotes, "bad_quote_count": len(bad_quotes), "lt_chargeable_count": len(lt_rows), "spacy_chargeable_count": len(spacy_rows), "llm_chargeable_count": len(llm_rows), "word_limit_gate": word_limit_gate, "qa_watchlist": {"lt_one_char_quotes_blocked": True, "spacy_modal_sva_protected": True, "llm_rows_allowed_to_survive": True, "unsafe_repair_not_materialised": True}}

def detector_only_qa_scan() -> Dict[str, Any]:
    reg = load_decision_registries()
    va25_status = va25_resource_status()
    return {
        "qa_contract_version": QA_CONTRACT_VERSION,
        "scope": "detector_only",
        "detector_version": APP_VERSION,
        "registry_driven_rules": True,
        "va25_python_import_removed": True,
        "v9_3_patch_active": True,
        "compact_mode_removed": True,
        "full_candidate_lineage_required": True,
        "recoverability_routing_removed": True,
        "quote_issue_repair_operation_family_QA_active": True,
        "layer_contamination_guard_active": True,
        "family_source_policy": "specific_candidate_family_validated_then_registry_confirmed",
        "decision_registry_status": reg.audit.get("quality_status"),
        "rule_registry_loaded": bool((reg.rule_registry or {}).get("rules")),
        "task_schema_registry_loaded": bool((reg.task_schema_registry or {}).get("schemas")),
        "family_lock_registry_loaded": bool(reg.mapping_by_operation),
        "resource_status": va25_status,
        "version_acceptance_status": "EXPERIMENTAL_PREMIUM_ONLY" if reg.audit.get("quality_status") == "ready" else "PATCH_REQUIRED",
        "notes": [
            "v9.5 benchmark acceptance requires diagnostic-mode error-by-error evaluation.",
            "Suppression is intentionally weak; FP control should be measured before tightening.",
            "All 9 Claude fixes applied: see APP_VERSION for confirmation.",
        ]
    }

# ---------------------------------------------------------------------------
# Main detector
# ---------------------------------------------------------------------------
class PremiumDetectorV9:
    def __init__(self, resource_dirs: Optional[Sequence[str]]=None, registry_dirs: Optional[Sequence[str]]=None) -> None:
        self.resources = load_resources(resource_dirs)
        self.decision_registries = load_decision_registries(registry_dirs)

    def analyze(self, essay_id: str, essay_text: str, prompt_text: str="", metadata: Optional[Dict[str,Any]]=None, require_llm: Optional[bool]=None, benchmark_mode: bool=False, allow_over_word_limit: bool=False) -> Dict[str,Any]:
        t0 = time.perf_counter(); metadata = metadata or {}
        llm_enabled = ENABLE_LLM_DEFAULT if require_llm is None else bool(require_llm)
        tracker = LLMTracker()
        wc = len(words(essay_text)); run_id = stable_id("run", essay_id, now_iso(), uuid.uuid4().hex[:8]); submission_id = str(metadata.get("submission_id") or stable_id("sub", essay_id, essay_text[:200], time.time_ns()))
        segmentation = segment_essay(essay_id, essay_text)
        identity = {"student_id": str(metadata.get("student_id") or "anonymous"), "institution_id": metadata.get("institution_id"), "class_id": metadata.get("class_id"), "teacher_id": metadata.get("teacher_id"), "essay_id": essay_id, "submission_id": submission_id, "prompt_id": stable_id("prompt", prompt_text) if prompt_text else None, "batch_id": metadata.get("batch_id"), "draft_id": metadata.get("draft_id"), "parent_submission_id": metadata.get("parent_submission_id")}
        run = {"run_id": run_id, "engine_id": "va-premium-detector", "engine_version": APP_VERSION, "contract_version": "DETECTOR_OUTPUT_V1.1", "taxonomy_version": TAXONOMY_VERSION, "task_schema_version": TASK_SCHEMA_VERSION, "rubric_version": RUBRIC_VERSION, "scoring_version": SCORING_VERSION, "resource_version": RESOURCE_VERSION, "qa_contract_version": QA_CONTRACT_VERSION, "created_at": now_iso(), "runtime_mode": "benchmark" if benchmark_mode else "premium_llm" if llm_enabled else "limited_llm", "llm_model": CHEAP_MODEL if llm_enabled else None}
        intake = {"raw_text": essay_text, "prompt_text": prompt_text, "essay_text": essay_text, "language": "en", "word_count": wc, "paragraph_count_raw": len(segmentation["paragraphs"]), "source": metadata.get("source", "api"), "submitted_at": metadata.get("submitted_at")}
        word_gate = {"gate_name": "word_count_ceiling_max_300", "max_words": MAX_ALLOWED_WORDS, "word_count": wc, "exceeded": wc > MAX_ALLOWED_WORDS, "action": "analyzed_with_ceiling_flag" if benchmark_mode or allow_over_word_limit else "reject"}
        if wc > MAX_ALLOWED_WORDS and not benchmark_mode and not allow_over_word_limit:
            elapsed = (time.perf_counter()-t0)*1000
            return {"headline": {"rejected": True, "reason": "word_limit_exceeded", "quality_status": self.resources.audit.get("quality_status")}, "identity": identity, "run": run, "intake_record": intake, "topic_alignment_risk": {"checked": False, "risk_flag": False, "confidence": 0.0, "reason": "essay_rejected_before_check"}, "detector_metric_profile": {}, "student_rows": [], "lret": {}, "practice_recommendations": [], "revision_evaluation": {}, "progress_tracking": {}, "qa": {"word_limit_gate": word_gate}, "audit": {"resource_audit": self.resources.audit}, "system": {"app_name": APP_NAME, "detector_version": APP_VERSION}, "internal_runtime_metrics": {"elapsed_ms": round(elapsed,2), "elapsed_seconds": round(elapsed/1000,3), "word_count": wc}}
        task_profile = infer_task_schema(prompt_text, essay_text, tracker, llm_enabled)
        idea_map = layer0_idea_map(prompt_text, essay_text, segmentation, task_profile, tracker, llm_enabled)
        topic_alignment_risk = detect_topic_alignment_risk(prompt_text, essay_text, tracker, llm_enabled)
        semantic = layer0_5_semantic(segmentation, tracker, llm_enabled)
        raw: List[Candidate] = []
        # v18: Phase 1 — fast sync passes (rules, LT, spaCy, collocation lookup)
        raw.extend(l3_va25_support(run_id, submission_id, essay_id, segmentation))
        raw.extend(l3_universal_rules(run_id, submission_id, essay_id, segmentation, semantic))
        raw.extend(l3_lt_support(run_id, submission_id, essay_id, segmentation, self.resources))
        raw.extend(l3_spacy_support(run_id, submission_id, essay_id, segmentation))
        raw.extend(l2_spacy_pass(run_id, submission_id, essay_id, segmentation))
        raw.extend(l2_collocation_lookup_pass(run_id, submission_id, essay_id, segmentation, self.resources))
        raw.extend(l2_fixed_phrase_pass(run_id, submission_id, essay_id, segmentation, self.resources))  # v18a
        # P1-FIX-1 (v18b): deduplicate fixed_phrase_pass vs collocation_lookup on same span.
        # 3 of 4 fixed_phrase FPs in v18a evaluation were span duplicates of collocation_lookup.
        # v18d R1: Candidate has span_start/span_end, not char_start/char_end.
        # Using char_start caused 'Candidate has no attribute char_start' on essay 17.
        _collocate_spans = {
            (c.span_start, c.span_end)
            for c in raw if c.source_engine == "collocation_lookup"
        }
        raw = [
            c for c in raw
            if not (
                c.source_engine == "fixed_phrase_pass"
                and any(
                    abs(c.span_start - cs) <= 3 and abs(c.span_end - ce) <= 3
                    for cs, ce in _collocate_spans
                )
            )
        ]

        # v17: Phase 2 — parallel LLM passes (all independent, run concurrently)
        # ThreadPoolExecutor wraps sync LLM calls — LLMTracker is thread-safe (v17).
        _parallel_passes = [
            lambda: layer1_2_llm_discourse(run_id, submission_id, essay_id, prompt_text, essay_text, segmentation, semantic, tracker, llm_enabled),
            lambda: l3_llm_local(run_id, submission_id, essay_id, segmentation, semantic, tracker, llm_enabled),
            lambda: l3_lr_focused_pass(run_id, submission_id, essay_id, segmentation, tracker, llm_enabled),
            lambda: l3_slr_collocate_pass(run_id, submission_id, essay_id, segmentation, semantic, tracker, llm_enabled),
            lambda: l3_slr_lexical_pass(run_id, submission_id, essay_id, segmentation, semantic, tracker, llm_enabled),
            lambda: l2_discourse_tr_pass(run_id, submission_id, essay_id, segmentation, semantic, tracker, llm_enabled),
        ]
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as _pool:
            _futures = [_pool.submit(fn) for fn in _parallel_passes]
            for _fut in concurrent.futures.as_completed(_futures):
                try:
                    raw.extend(_fut.result())
                except Exception as _e:
                    pass  # individual pass failure is non-fatal

        # v18: Phase 3 — universal confirmation (rules, LT, spaCy, collocation, slr_collocate)
        _universal_cands = [c for c in raw
                            if c.source_engine in _UNIVERSAL_CONFIRM_ENGINES]
        raw.extend(l3_universal_confirm(
            run_id, submission_id, essay_id,
            _universal_cands, segmentation, tracker, llm_enabled))
        # v17: SVA/NNC confirmation (re-enabled with LLM guard)
        raw.extend(l3_sva_nnc_confirm(
            run_id, submission_id, essay_id,
            raw, tracker, llm_enabled))
        lists = arbitrate(raw, self.resources, self.decision_registries, tracker, llm_enabled)
        # v13: detect strengths (rule-based + LLM) for evaluator_payload.strengths_profile
        strengths_profile = detect_strengths(essay_text, segmentation, task_profile, semantic, tracker, llm_enabled)
        payloads = build_payloads(lists, task_profile, idea_map, semantic, segmentation, strengths_profile)
        contribution = source_contribution(lists["raw_candidates"], lists)
        elapsed = (time.perf_counter()-t0)*1000; sent_n = max(1, len(segmentation["sentences"])); llm_perf = tracker.asdict()
        elapsed = (time.perf_counter()-t0)*1000; sent_n = max(1, len(segmentation["sentences"])); llm_perf = tracker.asdict()
        runtime = {"elapsed_ms": round(elapsed,2), "elapsed_seconds": round(elapsed/1000,3), "ms_per_word": round(elapsed/max(1,wc),3), "ms_per_sentence": round(elapsed/sent_n,3), "word_count": wc, "sentence_count": sent_n, "llm_calls_attempted_total": sum(v.get("calls_attempted",0) for v in llm_perf.values()), "llm_calls_succeeded_total": sum(v.get("calls_succeeded",0) for v in llm_perf.values()), "estimated_llm_cost_usd_total": round(sum(v.get("estimated_cost_usd",0) for v in llm_perf.values()), 6)}
        generated_metadata = {"word_count": wc, "paragraph_count": len(segmentation["paragraphs"]), "sentence_count": sent_n, "prompt_word_count": len(words(prompt_text or "")), "language": "en", "metadata_generated_by": "backend", "generated_at": now_iso(), "source": metadata.get("source", "api")}
        qa = {"raw_candidate_count": len(lists["raw_candidates"]), "survived_count": len(lists["survived_candidates"]), "chargeable_count": len(lists["chargeable_rows"]), "suppressed_count": len(lists["suppressed_candidates"]), "false_positive_count": len(lists["false_positive_candidates"]), "duplicate_count": len(lists["duplicate_candidates"]), "rerouted_count": len(lists["rerouted_candidates"]), "uncertain_count": len(lists["uncertain_candidates"]), "resource_quality_status": self.resources.audit.get("quality_status"), "decision_registry_status": self.decision_registries.audit.get("quality_status"), "spacy_status": _SPACY_STATUS, "spacy_error": _SPACY_ERROR, "language_tool_status": _LT_STATUS, "language_tool_error": _LT_ERROR, "llm_status": _OPENAI_STATUS if llm_enabled else "disabled", "llm_error": _OPENAI_ERROR, "llm_performance": llm_perf, "source_contribution_audit": contribution, "va25_local_rule_import_status": _VA25_IMPORT_STATUS, "va25_local_rule_import_error": _VA25_IMPORT_ERROR, "va25_local_rule_source": VA25_LOCAL_RULE_SOURCE, "va25_resource_status": va25_resource_status(), "benchmark_diagnostics": benchmark_diagnostics(lists, word_gate)}
        # [v18d.2] Derive TR5/TR6/TR7 from layer0 idea map before building metric_profile.
        _tr567 = _derive_tr567_from_idea_map(idea_map)
        metric_profile = {"metric_profile_id": stable_id("metric", run_id, submission_id), "run_id": run_id, "submission_id": submission_id, "essay_id": essay_id, "task_response": _tr567, "coherence_cohesion": {}, "lexical_resource": {}, "grammar": {}, "shared": {"semantic_recoverability": semantic.get("semantic_summary", {}).get("mean_recoverability"), "affected_discourse_ratio": semantic.get("semantic_summary", {}).get("affected_discourse_ratio"), "word_count": wc, "sentence_count": sent_n, "paragraph_count": len(segmentation["paragraphs"])}, "metric_sources": {"chargeable_row_ids": [r["row_id"] for r in lists["chargeable_rows"]], "global_metric_inputs": [], "task_schema_id": task_profile.get("task_schema_id")}, "detector_only": True, "score_ready": task_profile.get("score_ready", False)}
        return {"schema_version": "DETECTOR_OUTPUT_V1.1", "headline": {"rejected": False, "detector_status": "completed", "quality_status": self.resources.audit.get("quality_status")}, "topic_alignment_risk": topic_alignment_risk, "identity": identity, "run": run, "generated_metadata": generated_metadata, "intake_record": intake, "task_profile": task_profile, "segmentation": segmentation, "layer0_idea_map": idea_map, "layer0_5_semantic_recoverability": semantic, "candidate_lists": lists, **payloads, "detector_metric_profile": metric_profile, "student_rows": lists["chargeable_rows"], "lret": payloads["lret_fix_payload"], "practice_recommendations": [], "revision_evaluation": {}, "progress_tracking": {}, "qa": qa, "audit": {"resource_audit": self.resources.audit, "decision_registry_audit": self.decision_registries.audit, "va25_resource_status": va25_resource_status(), "operation_family_locks": OPERATION_FAMILY_LOCKS, "benchmark_mode": benchmark_mode, "contract": DETECTOR_CONTRACT_FILENAME, "v9_5_fixes": {"fix1_app_version_updated": True, "fix2_decision_registries_proper_fields": True, "fix3_family_to_default_v9_operation_complete": True, "fix4_v93_family_specificity_complete": True, "fix5_quote_issue_compatibility_cc_tr_extended": True, "fix6_infer_task_schema_hard_fail_wired": True, "fix7_l3_universal_rules_fp_guards": True, "fix8_grammatical_range_in_llm_prompts": True, "fix9_v93_family_to_operation_complete": True}}, "system": {"app_name": APP_NAME, "detector_version": APP_VERSION, "qa_contract_version": QA_CONTRACT_VERSION, "llm_policy_version": LLM_POLICY_VERSION}, "internal_runtime_metrics": runtime}


# ---------------------------------------------------------------------------
# FastAPI app + endpoints
# ---------------------------------------------------------------------------
if BaseModel is not object:
    class RuntimeOptions(BaseModel):
        require_llm: Optional[bool] = None
        benchmark_mode: bool = False
        allow_over_word_limit: bool = True
        # Contract v1.1 enum: qa_only | full | to_file
        response_mode: str = "full"
        resource_dirs: List[str] = Field(default_factory=list)
        registry_dirs: List[str] = Field(default_factory=list)

    class EssayInput(BaseModel):
        student_id: Optional[str] = None
        essay_id: Optional[str] = None
        submission_id: Optional[str] = None
        prompt_id: Optional[str] = None
        draft_id: Optional[str] = None
        parent_submission_id: Optional[str] = None
        prompt_text: Optional[str] = ""
        essay_text: str = ""
        # topic_keywords: optional per contract v1.1; used for future TR signal enrichment
        topic_keywords: List[str] = Field(default_factory=list)
        # Note: 'text' deprecated alias removed — contract v1.1 additionalProperties:false on essay items

    class BatchAnalyzeRequest(BaseModel):
        schema_version: str = "DETECTOR_INPUT_V1.1"
        student_id: str = "anonymous"
        batch_id: Optional[str] = None
        source: Optional[str] = "api"
        submitted_at: Optional[str] = None
        runtime_options: RuntimeOptions = Field(default_factory=RuntimeOptions)
        essays: List[EssayInput]

def _b2c_normalize_item_metadata(req: "BatchAnalyzeRequest", item: "EssayInput", index: int) -> Tuple[str, str, str, Dict[str, Any]]:
    student_id = (item.student_id or req.student_id or "anonymous").strip() or "anonymous"
    batch_id = (req.batch_id or stable_id("batch", student_id, now_iso())).strip()
    essay_text = item.essay_text or ""
    essay_id = str(item.essay_id or stable_id("essay", student_id, batch_id, index, essay_text[:120]))
    submission_id = str(item.submission_id or stable_id("sub", student_id, essay_id, batch_id, essay_text[:200], time.time_ns()))
    metadata = {
        "student_id": student_id,
        "institution_id": None,
        "class_id": None,
        "teacher_id": None,
        "submission_id": submission_id,
        "prompt_id": item.prompt_id,
        "batch_id": batch_id,
        "draft_id": item.draft_id,
        "parent_submission_id": item.parent_submission_id,
        "source": req.source or "api",
        "submitted_at": req.submitted_at,
        "metadata_policy": "backend_generated_only",
        "b2c_schema_version": "DETECTOR_INPUT_V1.1",
    }
    return essay_id, essay_text, item.prompt_text or "", metadata

if FastAPI is not None:
    app = FastAPI(title=APP_NAME, version=APP_VERSION)

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return {"ok": True, "detector_version": APP_VERSION, "app_name": APP_NAME, "b2c_schema_version": "DETECTOR_INPUT_V1.1", "b2c_schema_status": b2c_schema_status(), "spacy_status": _SPACY_STATUS, "language_tool_status": _LT_STATUS, "llm_status": _OPENAI_STATUS, "max_words": MAX_ALLOWED_WORDS, "word_limit_policy": "ceiling_only_no_floor", "va25_resource_status": va25_resource_status(), "decision_registry_status": load_decision_registries().audit.get("quality_status")}

    @app.get("/qa_detector")
    def qa_detector() -> Dict[str, Any]:
        return detector_only_qa_scan()

    @app.get("/b2c_schema_status")
    def get_b2c_schema_status() -> Dict[str, Any]:
        return b2c_schema_status()

    @app.get("/contract")
    def get_contract() -> Dict[str, Any]:
        """Return the full unified detector contract (input + output + shared definitions)."""
        status = _load_json_file_safe(DETECTOR_CONTRACT_PATH)
        return status.get("data") if status.get("loaded") else status

    @app.get("/contract/input")
    def get_contract_input() -> Dict[str, Any]:
        """Return the input_contract section only."""
        status = _load_json_file_safe(DETECTOR_CONTRACT_PATH)
        data = status.get("data") or {}
        return data.get("input_contract") or {"error": "contract_not_loaded", "path": str(DETECTOR_CONTRACT_PATH)}

    @app.get("/contract/output")
    def get_contract_output() -> Dict[str, Any]:
        """Return the output_contract section only."""
        status = _load_json_file_safe(DETECTOR_CONTRACT_PATH)
        data = status.get("data") or {}
        return data.get("output_contract") or {"error": "contract_not_loaded", "path": str(DETECTOR_CONTRACT_PATH)}

    @app.post("/analyze")
    def analyze_endpoint(req: EssayInput) -> Dict[str, Any]:  # type: ignore[name-defined]
        pseudo_batch = BatchAnalyzeRequest(student_id=req.student_id or "anonymous", essays=[req])
        essay_id, essay_text, prompt_text, metadata = _b2c_normalize_item_metadata(pseudo_batch, req, 1)
        opts = pseudo_batch.runtime_options
        det = PremiumDetectorV9(opts.resource_dirs or None, opts.registry_dirs or None)
        full = det.analyze(essay_id, essay_text, prompt_text, metadata, opts.require_llm, opts.benchmark_mode, opts.allow_over_word_limit)
        return full

    @app.post("/analyze_batch")
    def analyze_batch_endpoint(req: BatchAnalyzeRequest) -> Dict[str, Any]:  # type: ignore[name-defined]
        """Swagger-safe batch endpoint.

        Transport-only patch for v9.7:
        - runs the detector exactly as before;
        - writes the full diagnostic payload to detector_outputs/;
        - returns only a compact response so Swagger does not freeze.
        """
        t0 = time.perf_counter()
        opts = req.runtime_options
        det = PremiumDetectorV9(opts.resource_dirs or None, opts.registry_dirs or None)
        results: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []
        batch_id = req.batch_id or stable_id("batch", req.student_id or "anonymous", now_iso())
        req.batch_id = batch_id
        for idx, item in enumerate(req.essays, start=1):
            essay_id, essay_text, prompt_text, metadata = _b2c_normalize_item_metadata(req, item, idx)
            if not essay_text.strip():
                failures.append({"essay_id": essay_id, "reason": "missing_essay_text", "index": idx})
                continue
            try:
                full = det.analyze(essay_id, essay_text, prompt_text, metadata, opts.require_llm, opts.benchmark_mode, opts.allow_over_word_limit)
                results.append(full)
            except Exception as exc:
                failures.append({"essay_id": essay_id, "reason": str(exc), "index": idx})
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        return {
            "schema_version": "DETECTOR_OUTPUT_V1.1",
            "batch_id": batch_id,
            "student_id": req.student_id,
            "detector_version": APP_VERSION,
            "elapsed_ms": elapsed_ms,
            "results": results,
            "failures": failures,
            "result_count": len(results),
            "failure_count": len(failures),
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("det_vip_v18d_1:app", host="0.0.0.0", port=8000, reload=False)
