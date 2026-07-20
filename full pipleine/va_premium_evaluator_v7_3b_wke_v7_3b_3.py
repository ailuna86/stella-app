"""
VA Premium Evaluator v7.3b — Writing Knowledge Extractor (WKE)
Standalone FastAPI + CLI service.

Core boundary (unchanged from v7):
  Detector detects errors.
  Scorer scores IELTS bands/criteria.
  LRET classifies lexical units.
  Evaluator v7 extracts writing knowledge, competence vectors, evidence, capacity signals,
  and downstream payloads. It does NOT output IELTS scores, performance_score,
  performance_band, or LRET KEEP/FIX/ENHANCE labels.

v7.3b changes vs v7.3a:
  Change A1 — Grammar domain routing fix: GRAMMAR_DOMAINS branch checked FIRST in both
              dimension_template() and _compute_vector(). Resolves monitor-swamp.
  Change A2 — priority_index field added to gap signals.
  Change A3 — CLAUSE_STRUCTURE added to GRAMMAR_ERROR_FAMILY_MAP.
  Change B  — extract_per_skill_grammar_patterns() for all 15 D8 skills.
  Change C  — extract_lexical_features() for D7/D14 text-based measurement.
  Change D  — NOT_OBS_GAP_THRESHOLD=0.15 gate for gap_condition_a.
  Change E  — D13 split: evaluator-accessible (4) vs practice-only (6).
  Change F  — detect_argument_structure() for D11 argument profile.
  Change G  — extract_advanced_lexical_features() for D14 per-skill measurement.
  v7.3b.3   — Fragment filter: articles/demonstratives in _NGRAM_REJECT_START;
               possessives/deictics/copulas in _NGRAM_REJECT_END; _COMPARATIVE_FRAG_END
               gate (NP + VP-3gram+prep); _SV_GATE_AUX (3-gram [N][COPULA/MODAL][*]);
               _CLAUSE_INNER extended: than/as, copulas, possessives, middle-article.
               198 → 158 units. All 13 target NP-boundary fragments removed.
  v7.3b.2   — LRET gate: words and NPs are valid as unigrams/phrases.
               Only SV/modal-truncation VPs removed (edge_function_word +
               no collocation + no LRET signal). 232 → ~198 units,
               calibration unchanged.
  v7.3b.1   — _NGRAM_REJECT_START/END sets; expanded _PREDICATE_VERBS (65 lemmas);
               discourse loop priority fix; NP verb-tainted: 62% → 0%.
  FIX       — extract_lexical_units() rewritten: 2/3-gram only, no cross-clause spans,
              deduplicated by text, covers KEEP/FIX/ENHANCE candidates for LRET.

v7.3a changes vs v7.2:
  BUG FIX 1 — A/D pattern regex: \badvantage\b → \badvantages?\b (plural forms now matched).
              observed_slot_only now fires correctly for A/D essays.
  BUG FIX 2 — Grammar vector now uses detector error rows (when available) to penalise
              control_proxy per matched error family. Grammar skills with errors can no
              longer appear as "current_strength".
  BUG FIX 3 — signal_from_vector() is now depth-aware: DEPTH_1 skills cap at "monitor",
              never "current_strength", regardless of vector average.
  NEW — Independent grammar surface analysis (no detector required):
        extract_grammar_features() detects double-comparatives, informal markers,
        tense inconsistency, SVA red flags, sentence variety, and register signals
        directly from essay text. Grammar skills now have a meaningful rule-based
        vector even in essay-only mode.
  NEW — Comprehensive style & pattern analysis: hedging density, nominalisation proxy,
        passive voice, sentence-opening variety, paragraph topic-sentence quality.

Run API:
    uvicorn va_premium_evaluator_v7_2_wke_standalone:app --reload --port 8008

Run CLI:
    python va_premium_evaluator_v7_2_wke_standalone.py --input request.json --output out.json --pretty
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from pydantic import BaseModel, Field
except Exception:
    BaseModel = object
    def Field(default=None, **kwargs): return default

try:
    from fastapi import FastAPI, HTTPException
except Exception:
    FastAPI = None
    HTTPException = Exception

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

ENGINE_ID       = "VA_PREMIUM_EVALUATOR_WKE_V7_3B"
ENGINE_VERSION  = "7.3b.3-text-extraction+ontology-v3+np-type-fix+lret-gate+frag-filter"
SCHEMA_VERSION  = "WKM_OUTPUT_V7.3B"
DEFAULT_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

FORBIDDEN_OUTPUT_FIELDS = {
    "performance_score", "performance_band", "ielts_band",
    "criterion_score", "overall_band",
}
LRET_FORBIDDEN_LABELS = {"KEEP", "FIX", "ENHANCE", "AVOID"}

# ── Evidence Depth constants ──────────────────────────────────────────────────
DEPTH_0 = 0   # slot fill / cue word only — no skill demonstrated
DEPTH_1 = 1   # single partial / generic demonstration
DEPTH_2 = 2   # at least one clear, structurally complete demonstration
DEPTH_3 = 3   # multiple clear instances, consistent across essay

# Skills blocked from `observed` when the essay uses A/D listing structure
AD_BLOCKED_SKILLS = frozenset({
    "arg_rebuttal_generation", "arg_rebuttal_quality",
    "arg_counterargument_development", "arg_counterargument_generation",
    "counterargument_development", "rebuttal_quality",
    "evaluation_of_alternatives",
})

CANONICAL_BUCKETS = frozenset({
    "single_essay_observable",
    "hybrid_single_essay_plus_multi_essay_tracking",
    "hybrid_essay_observable_plus_practice_required",
    "practice_exercise_required",
})

DOMAIN_CODE_MAP: Dict[str, str] = {
    "D1":  "Task Understanding",
    "D2":  "Content Development",
    "D3":  "Reasoning Competence",
    "D4":  "Information Processing",
    "D5":  "Organization",
    "D6":  "Cohesion",
    "D7":  "Lexical Control",
    "D8":  "Grammar Production",
    "D9":  "Style & Reader Impact",
    "D11": "Argumentation",
    "D13": "Thinking Competence",
    "D14": "Advanced Lexical Competence",
    "D15": "Revision & Self-Editing",
}

GRAMMAR_DOMAINS   = {"grammar production"}
GRAMMAR_KEYWORDS  = {
    "grammar", "article", "agreement", "tense", "preposition", "punctuation",
    "morphology", "noun form", "verb form", "clause", "sentence construction",
    "relative clause", "conditional", "comparison structure",
    "compound sentence", "complex sentence", "simple sentence",
}
SENTENCE_STRUCTURE_OBSERVABLE = {
    "simple_sentence_construction", "complex_sentence_construction",
    "compound_sentence_construction", "sentence_variety",
}
MAX_LEXICAL_UNITS = 240

STOPWORDS = {
    "the","a","an","of","to","in","on","for","with","at","from","by","about","as","is","are","was","were",
    "be","been","being","it","this","that","these","those","and","but","or","if","because","so","then","also",
    "can","could","should","would","have","has","had","do","does","did","will","may","might","must","very","more",
    "their","them","they","we","our","you","your","he","she","his","her","i","my","me","not","no","than","there",
    "some","one","another","first","second","like","all","only","while","however","usually","lot","much","many",
}
TRANSITION_CUES = {
    "however","therefore","moreover","furthermore","consequently","nevertheless","whereas","although",
    "as a result","for example","for instance","in addition","on the other hand","in conclusion","to conclude",
    "overall","firstly","secondly","finally","because","also","while",
}
EXAMPLE_CUES     = {"for example","for instance","such as"}
REASON_CUES      = {"because","since","as","due to","leads to","lead to","because of that","therefore","as a result"}
CONCLUSION_CUES  = {"in conclusion","to conclude","overall","as we can see","finally","to sum up"}
POSITION_CUES    = {"i think","i believe","i argue","in my opinion","my view","this essay","i will explain","i would argue"}
CONTRAST_CUES    = {"however","although","while","on the other hand","whereas","but","yet","despite","nevertheless"}
BASIC_ACADEMIC_WORDS = {
    "society","government","population","development","benefit","problem","advantage","disadvantage",
    "culture","tradition","economic","social","policy","public","education","environment","technology",
    "community","individual","global","national","local","impact","effect","cause","solution","issue",
}
VAGUE_NOUNS = {"thing","things","people","topic","situation","ways","stuff","amount"}
INFORMAL_PHRASES = {"let's talk","as we can see","a lot","big","good","bad"}

# ── v7.3b constants ───────────────────────────────────────────────────────────

NOT_OBS_GAP_THRESHOLD   = 0.15   # Change D: only priority_index > 0.15 → gap_condition_a
DEV_TARGET_PRIORITY_FLOOR = 0.05  # Change E

D13_EVALUATOR_ACCESSIBLE: frozenset = frozenset({
    "th_causal_reasoning", "th_comparison_reasoning",
    "th_tradeoff_analysis", "th_consequence_analysis",
})
D13_PRACTICE_ONLY: frozenset = frozenset({
    "th_solution_generation", "th_alternative_evaluation",
    "th_prioritization", "th_abstraction",
    "th_synthesis", "th_systems_thinking",
})

EVALUATOR_EXCLUDED: frozenset = frozenset({
    # D15 Revision & Self-Editing (13 skills)
    "rev_grammar_problem_detection","rev_lexical_problem_detection",
    "rev_clarity_problem_detection","rev_cohesion_problem_detection",
    "rev_root_cause_identification","rev_severity_estimation",
    "rev_repair_prioritization","rev_grammar_repair","rev_lexical_repair",
    "rev_reasoning_repair","rev_cohesion_repair",
    "rev_self_evaluation_accuracy","rev_confidence_calibration",
    # D13 practice-only (6 skills)
    "th_solution_generation","th_alternative_evaluation","th_prioritization",
    "th_abstraction","th_synthesis","th_systems_thinking",
    # D1 practice-only (2 skills)
    "identify_constraints","identify_evaluation_criteria",
    # Legacy D10 (6 skills — domain eliminated)
    "grammar_error_detection","lexical_error_detection","reasoning_gap_detection",
    "cohesion_issue_detection","repetition_detection","self_correction",
})

# Detector error families merged into lret_payload as fix_candidates
LRET_FIX_FAMILIES: frozenset = frozenset({
    "COLLOCATION", "WORD_FORM", "LEXICAL_PRECISION",
})

# ── v7.3a: Detector error-family → grammar vector penalty map ─────────────────
# Maps detector error family names (upper-case) to (dimension, penalty_per_error).
# penalty_per_error is subtracted from the dimension value per occurrence (capped at 5 hits).
GRAMMAR_ERROR_FAMILY_MAP: Dict[str, Dict[str, Any]] = {
    "COMPARATIVE_FORM":     {"dimension": "control_proxy",      "penalty_per_error": 0.12},
    "ARTICLE_DETERMINER":   {"dimension": "control_proxy",      "penalty_per_error": 0.08},
    "VERB_FORM":            {"dimension": "control_proxy",      "penalty_per_error": 0.10},
    "AGREEMENT":            {"dimension": "control_proxy",      "penalty_per_error": 0.10},
    "COLLOCATION":          {"dimension": "control_proxy",      "penalty_per_error": 0.07},
    "TENSE":                {"dimension": "control_proxy",      "penalty_per_error": 0.09},
    "PREPOSITION":          {"dimension": "control_proxy",      "penalty_per_error": 0.07},
    "PUNCTUATION":          {"dimension": "control_proxy",      "penalty_per_error": 0.05},
    "MORPHOLOGY":           {"dimension": "control_proxy",      "penalty_per_error": 0.09},
    "RELATIVE_CLAUSE":      {"dimension": "complexity",         "penalty_per_error": 0.08},
    "CLAUSE_BOUNDARY":      {"dimension": "structure_presence", "penalty_per_error": 0.10},
    "SENTENCE_FRAGMENT":    {"dimension": "structure_presence", "penalty_per_error": 0.12},
    "RUN_ON":               {"dimension": "structure_presence", "penalty_per_error": 0.10},
    # Cover alternative naming conventions from different detector versions
    "COMPARATIVE":          {"dimension": "control_proxy",      "penalty_per_error": 0.12},
    "ARTICLE":              {"dimension": "control_proxy",      "penalty_per_error": 0.08},
    "SUBJECT_VERB_AGREEMENT":{"dimension": "control_proxy",     "penalty_per_error": 0.10},
    "SVA":                  {"dimension": "control_proxy",      "penalty_per_error": 0.10},
    "VERB_TENSE":           {"dimension": "control_proxy",      "penalty_per_error": 0.09},
    "WORD_FORM":            {"dimension": "control_proxy",      "penalty_per_error": 0.09},
    "CLAUSE_STRUCTURE":     {"dimension": "structure_presence",  "penalty_per_error": 0.10},
}

DOMAIN_PRIORITY = {
    "Task Understanding":                  0.90,
    "Argumentation":                       0.80,
    "Organization":                        0.80,
    "Content Development":                 0.70,
    "Cohesion":                            0.60,
    "Reasoning Competence":                0.60,   # renamed D3
    "Reasoning & Critical Thinking":       0.60,  # legacy alias
    "Lexical Control":                     0.50,
    "Advanced Lexical Competence":         0.50,
    "Writing Structure (Layer-0)":         0.50,
    "Style & Reader Impact":               0.40,
    "Thinking Competence":                 0.35,   # v7.3b Change E
    "Grammar Production":                  0.25,
    "Information Processing":              0.55,
    "Revision & Self-Editing":             0.10,
}

# LLM per-skill structural definitions (injected into prompt for key skills)
SKILL_DEFINITIONS = {
    "arg_claim_generation": {
        "definition": "Student generates at least one claim — an assertion of a position, not just a restatement of the question.",
        "depth_2_requires": "At least one sentence that asserts a specific position the writer holds or describes, beyond restating 'some say X while others say Y'.",
    },
    "arg_claim_precision": {
        "definition": "The claim boundary is clear — the reader can identify exactly what is being asserted and what would falsify it.",
        "depth_2_requires": "A claim where scope and content are specific enough that the reader knows exactly what position is taken. 'There are advantages and disadvantages' is DEPTH_0.",
    },
    "arg_claim_specificity": {
        "definition": "The claim uses specific, content-rich language rather than generic/template phrases.",
        "depth_2_requires": "A claim that names specific mechanisms, parties, or outcomes rather than generic categories ('elderly people cannot work' is minimal; 'the ratio of workers to pensioners falls, increasing per-worker pension contribution' is DEPTH_2).",
    },
    "arg_claim_relevance": {
        "definition": "The claim is relevant to the essay prompt and topic.",
        "depth_2_requires": "At least one claim sentence that directly addresses the essay topic with topic-specific vocabulary.",
    },
    "arg_position_consistency": {
        "definition": "The writer's position is maintained consistently throughout the essay without unexplained contradiction.",
        "depth_2_requires": "A clear position is stated AND used consistently across at least two paragraphs.",
    },
    "arg_reason_generation": {
        "definition": "Student provides explicit reasons that explain why a claim is valid.",
        "depth_2_requires": "At least one sentence with an explicit causal link (because, since, as, due to, leads to) that connects a cause to a meaningful effect beyond just repeating the claim.",
    },
    "arg_reason_quality": {
        "definition": "The reasons provided are logically sound, non-circular, and genuinely explanatory.",
        "depth_2_requires": "A reason that adds information the reader didn't already have from the claim — not 'elderly people can't work because they are old'.",
    },
    "arg_reason_relevance": {
        "definition": "The reasons are relevant to the claim they support.",
        "depth_2_requires": "At least two reasons that logically connect to their respective claims.",
    },
    "arg_support_generation": {
        "definition": "Student provides support (examples, data, elaboration) for at least one claim-reason pair.",
        "depth_2_requires": "At least one sentence that adds new information beyond the claim and its reason — not just restating them.",
    },
    "arg_support_alignment": {
        "definition": "Support is aligned with and relevant to the claims it is meant to support.",
        "depth_2_requires": "Support and its claim occur in the same paragraph and the support clearly relates to the claim topic.",
    },
    "arg_rebuttal_generation": {
        "definition": "Student explicitly acknowledges a specific opposing claim AND provides a specific response to it.",
        "depth_2_requires": "MUST contain: (A) explicit naming of an opposing position AND (B) a direct response to that specific position. 'However, there are advantages too' does NOT qualify — it mentions both sides but does not rebut a specific claim.",
        "depth_0_example": "'However, with all these disadvantages there are some significant advantages as well.' — contrast cue present, no opposing claim named, no specific response.",
    },
    "arg_rebuttal_quality": {
        "definition": "The rebuttal engages substantively with the opposing claim and provides a convincing response.",
        "depth_2_requires": "Same structural requirement as arg_rebuttal_generation plus the response must add new reasoning beyond just asserting the writer's position.",
        "depth_0_example": "Same as arg_rebuttal_generation — if no rebuttal structure exists, quality cannot be assessed.",
    },
    "arg_counterargument_development": {
        "definition": "Student develops a counterargument by engaging with an opposing view, explaining its limitations or incorrectness.",
        "depth_2_requires": "MUST contain: (A) an opposing view named or described AND (B) substantive engagement with why it is limited, incorrect, or qualified. Simply listing disadvantages is NOT counterargument development.",
        "depth_0_example": "'Let's talk about disadvantages first' then 'However, there are advantages' — introduces both sides without engaging either as a counterargument.",
    },
    "arg_counterargument_generation": {
        "definition": "Student generates an opposing position to their own view in order to address it.",
        "depth_2_requires": "An explicit statement of a view contrary to the writer's position, followed by engagement with it. A balanced 'both sides' essay where no position is taken has no counterargument to generate.",
        "depth_0_example": "Any balanced both-sides essay without a clear writer position has no counterargument to generate.",
    },
    "arg_warrant_generation": {
        "definition": "Student states an explicit general principle (Toulmin warrant) that bridges a claim to a reason.",
        "depth_2_requires": "A sentence containing an explicit general rule or principle, such as 'Society has a responsibility to support those who can no longer work.' A causal 'because' connector is NOT a warrant.",
        "depth_0_example": "'Because of that the government needs to increase the money that it spends on medicine.' — causal connector, not a general principle.",
    },
    "arg_warrant_quality": {
        "definition": "The warrant is logically sound and clearly bridges the claim-reason relationship.",
        "depth_2_requires": "Same as arg_warrant_generation — if no warrant exists, quality cannot be assessed.",
        "depth_0_example": "Same as arg_warrant_generation.",
    },
    "arg_conclusion_alignment": {
        "definition": "The conclusion aligns with and synthesises the main argument, not just restating the introduction.",
        "depth_2_requires": "A conclusion that synthesises main points or advances the argument beyond 'as we can see there are advantages and disadvantages'.",
        "depth_0_example": "'As we can see there some significant disadvantages and advantages in this situation.' — one sentence that only restates both sides.",
    },
    "avoid_overgeneralization": {
        "definition": "Student qualifies claims appropriately, avoiding sweeping universal statements without evidence.",
        "depth_2_requires": "Evidence of qualification ('usually', 'in most cases', 'tends to') OR nuanced claim language in at least two instances.",
    },
    "causal_reasoning": {
        "definition": "Student traces cause-effect relationships explicitly and accurately.",
        "depth_2_requires": "At least two causal chains where the cause is clearly distinguished from the effect and the link is logically sound.",
    },
    "counterargument_development": {
        "definition": "See arg_counterargument_development. Assess identically.",
        "depth_2_requires": "Same as arg_counterargument_development.",
    },
    "rebuttal_quality": {
        "definition": "See arg_rebuttal_quality. Assess identically.",
        "depth_2_requires": "Same as arg_rebuttal_quality.",
    },
    "evaluation_of_alternatives": {
        "definition": "Student explicitly considers alternative positions or solutions and evaluates their relative merit.",
        "depth_2_requires": "At least one explicit comparison of two approaches/positions with evaluation of which is better and why.",
        "depth_0_example": "Listing advantages and disadvantages of a single topic without comparing positions.",
    },
}

# ═══════════════════════════════════════════════════════════════════════════════
# REQUEST MODEL
# ═══════════════════════════════════════════════════════════════════════════════

class EvaluatorRequest(BaseModel):
    student_id:            str  = "anonymous"
    essay_id:              str  = "001"
    submission_id:         Optional[str] = None
    batch_id:              Optional[str] = None
    prompt_id:             Optional[str] = None
    prompt_text:           Optional[str] = None
    essay_text:            str
    ontology_dir:          Optional[str] = None
    detector_output:       Optional[Dict[str, Any]] = None
    detector_output_path:  Optional[str] = None
    scorer_output:         Optional[Dict[str, Any]] = None
    scorer_output_path:    Optional[str] = None
    learner_history:       Optional[Dict[str, Any]] = None
    use_llm:               bool = False
    model:                 Optional[str] = None
    max_llm_skills:        int  = 30

# ═══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def clamp(x: Any, lo: float = 0.0, hi: float = 1.0) -> Optional[float]:
    if x is None: return None
    try:
        if math.isnan(float(x)): return None
        return max(lo, min(hi, float(x)))
    except Exception: return None

def safe_ratio(num: float, den: float, default: Optional[float] = None) -> Optional[float]:
    return default if den <= 0 else clamp(num / den)

def load_json_path(path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not path: return None
    p = Path(path)
    if not p.exists(): raise FileNotFoundError(f"Not found: {path}")
    with p.open("r", encoding="utf-8") as f: return json.load(f)

def words(text: str) -> List[str]:
    return re.findall(r"[A-Za-z][A-Za-z''\-]*", text or "")

def lower_words(text: str) -> List[str]:
    return [w.lower() for w in words(text)]

def content_words(text: str) -> List[str]:
    return [w for w in lower_words(text) if w not in STOPWORDS and len(w) > 2]

def split_paragraphs(text: str) -> List[str]:
    text = (text or "").strip()
    if not text: return []
    paras = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    if len(paras) <= 1 and "\n" in text:
        line_paras = [p.strip() for p in text.splitlines() if p.strip()]
        if len(line_paras) > 1: paras = line_paras
    return paras or [text]

def split_sentences(text: str) -> List[str]:
    raw = re.split(r"(?<=[.!?])\s+|\n+", (text or "").strip())
    out: List[str] = []
    for seg in raw:
        seg = re.sub(r"\s+", " ", seg).strip()
        if not seg: continue
        if out and len(seg.split()) < 4: out[-1] += " " + seg
        else: out.append(seg)
    return out

def contains_any(text: str, cues: Iterable[str]) -> bool:
    low = (text or "").lower()
    return any(cue in low for cue in cues)

def evidence_strength_from_count(n: int, confidence: float = 1.0) -> str:
    if n <= 0 or confidence <= 0.15: return "none"
    if n == 1 or confidence < 0.45:  return "low"
    if n <= 3 or confidence < 0.75:  return "medium"
    return "high"

def signal_from_vector(vector: Dict[str, Optional[float]], status: str, bucket: str) -> str:
    """
    v7.3a BUG FIX 3: 'current_strength' requires status == 'observed' (DEPTH_2+).
    'observed_low_evidence' (DEPTH_1) caps at 'monitor' regardless of vector average.
    This prevents weak/partial demonstrations from appearing as confirmed strengths.
    """
    gated = {
        "requires_history","requires_practice_evidence","requires_revision_evidence",
        "detector_required_for_reliable_grammar","observed_limited_without_detector",
        "not_applicable_to_task_type","observed_slot_only",
    }
    if status in gated: return "tracking_needed"
    if status not in {"observed","observed_low_evidence"} or not vector: return "unknown"
    vals = [v for v in vector.values() if isinstance(v, (int, float))]
    if not vals: return "unknown"
    avg = sum(vals) / len(vals)
    # current_strength requires full DEPTH_2+ observation, not just high vector average
    if avg >= 0.72 and status == "observed": return "current_strength"
    if avg >= 0.50: return "monitor"
    return "development_target"

def capacity_state(vector: Dict[str, Optional[float]], status: str, bucket: str) -> str:
    MAP = {
        "not_observed":                        "not_observed",
        "requires_history":                    "requires_history_for_stability",
        "requires_practice_evidence":          "requires_practice_for_competence",
        "requires_revision_evidence":          "requires_revision_cycle",
        "detector_required_for_reliable_grammar": "requires_detector_for_reliable_grammar",
        "not_applicable_to_task_type":         "not_applicable_to_current_task",
        "observed_limited_without_detector":   "limited_product_signal_only",
        "observed_slot_only":                  "slot_present_skill_not_demonstrated",
    }
    if status in MAP: return MAP[status]
    vals = [v for v in vector.values() if isinstance(v, (int, float))]
    if not vals: return "unknown"
    avg = sum(vals) / len(vals)
    if avg >= 0.78: return "strong_current_essay"
    if avg >= 0.58: return "functional_current_essay"
    if avg >= 0.35: return "emerging"
    return "fragile"

def is_weak_observation(obs: Dict[str, Any]) -> bool:
    """Return True if skill is observed but dimension values indicate weak execution."""
    if obs.get("status") not in {"observed", "observed_low_evidence"}: return False
    cv = obs.get("competence_vector", {})
    vals = [v for v in cv.values() if isinstance(v, (int, float))]
    if not vals: return False
    weak = sum(1 for v in vals if v < 0.5)
    # Majority weak OR depth/validity_proxy specifically low
    if weak >= len(vals) / 2: return True
    if "depth" in cv and isinstance(cv["depth"], float) and cv["depth"] < 0.5: return True
    if "validity_proxy" in cv and isinstance(cv["validity_proxy"], float) and cv["validity_proxy"] < 0.55: return True
    return False

# ═══════════════════════════════════════════════════════════════════════════════
# ONTOLOGY LOADING  (unchanged from v7.1)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SkillRecord:
    skill_id: str
    skill_name: str
    domain: str = "Unknown"
    bucket: str = "unknown"
    metrics: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    consumers: List[str] = field(default_factory=list)
    required_inputs: List[str] = field(default_factory=list)
    recommended_owners: List[str] = field(default_factory=list)
    evidence_reliability: str = "unknown"
    score_policy: Dict[str, Any] = field(default_factory=dict)
    source: str = "unknown"

def _find_file(ontology_dir: Optional[str], filename: str) -> Optional[Path]:
    candidates = []
    if ontology_dir: candidates.append(Path(ontology_dir) / filename)
    candidates += [Path.cwd() / filename, Path(__file__).resolve().parent / filename, Path("/mnt/data") / filename]
    for p in candidates:
        if p.exists(): return p
    return None

def load_json_if_exists(ontology_dir: Optional[str], filename: str) -> Tuple[Optional[Dict], Optional[str]]:
    p = _find_file(ontology_dir, filename)
    if not p: return None, None
    with p.open("r", encoding="utf-8") as f: return json.load(f), str(p)

def load_ontology(ontology_dir: Optional[str]) -> Tuple[Dict[str, SkillRecord], Dict[str, str], Dict[str, Any]]:
    """v7.3b ontology loader — uses evaluator_skill_registry_v2.json (ontology v3).
    Falls back to VA_microskill_clustering_v3.json, then legacy second_pass.
    Normalises macro_domain codes via DOMAIN_CODE_MAP.
    Filters EVALUATOR_EXCLUDED skills.
    """
    loaded_files: Dict[str, str] = {}
    skills:       Dict[str, SkillRecord] = {}
    bucket_by_skill: Dict[str, str] = {}

    def _load_from_records(recs: List[Dict], source_tag: str) -> None:
        for rec in recs:
            sid = rec.get("skill_id")
            if not sid: continue
            if sid in EVALUATOR_EXCLUDED: continue
            raw_domain = rec.get("macro_domain", "")
            domain = DOMAIN_CODE_MAP.get(raw_domain, rec.get("macro_domain_name") or raw_domain or "Unknown")
            bucket = rec.get("primary_evaluation_bucket") or "single_essay_observable"
            if bucket not in CANONICAL_BUCKETS:
                bucket = "single_essay_observable"
            existing = skills.get(sid) or SkillRecord(skill_id=sid, skill_name=rec.get("skill_name") or sid)
            existing.skill_name           = rec.get("skill_name") or existing.skill_name
            existing.domain               = domain
            existing.bucket               = bucket
            existing.dependencies         = rec.get("dependencies") or existing.dependencies
            existing.consumers            = rec.get("recommended_system_owners") or existing.consumers
            existing.recommended_owners   = rec.get("recommended_system_owners") or []
            existing.required_inputs      = rec.get("required_inputs") or []
            existing.evidence_reliability = rec.get("evidence_reliability") or "medium"
            existing.metrics              = rec.get("metrics") or existing.metrics
            existing.score_policy         = rec.get("score_policy") or {}
            existing.source               = source_tag
            bucket_by_skill[sid]          = bucket
            skills[sid]                   = existing

    # 1. Primary: evaluator_skill_registry_v2.json (ontology v3)
    v2, path = load_json_if_exists(ontology_dir, "evaluator_skill_registry_v2.json")
    if path:
        loaded_files["evaluator_skill_registry_v2.json"] = path
        _load_from_records(v2.get("micro_skill_records", []), "canonical_VA_microskill_clustering_v3")

    # 2. Fallback: VA_microskill_clustering_v3.json
    if not skills:
        v3, path = load_json_if_exists(ontology_dir, "VA_microskill_clustering_v3.json")
        if path:
            loaded_files["VA_microskill_clustering_v3.json"] = path
            for bucket, domains in (v3.get("clusters_by_bucket_and_domain") or {}).items():
                if bucket not in CANONICAL_BUCKETS: continue
                if isinstance(domains, dict):
                    for dname, ids in domains.items():
                        for sid in ids:
                            if sid in EVALUATOR_EXCLUDED: continue
                            bucket_by_skill[sid] = bucket
                            skills[sid] = SkillRecord(skill_id=sid,
                                skill_name=sid.replace("_"," ").title(),
                                domain=dname, bucket=bucket,
                                source="canonical_VA_microskill_clustering_v3")
            _load_from_records(v3.get("micro_skill_records", []), "canonical_VA_microskill_clustering_v3")

    # 3. Legacy fallback: VA_microskill_clustering_second_pass.json
    if not skills:
        second, path = load_json_if_exists(ontology_dir, "VA_microskill_clustering_second_pass.json")
        if path:
            loaded_files["VA_microskill_clustering_second_pass.json"] = path
            for bucket, domains in (second.get("clusters_by_bucket_and_domain") or {}).items():
                if bucket not in CANONICAL_BUCKETS: continue
                if isinstance(domains, dict):
                    for dname, ids in domains.items():
                        for sid in ids:
                            if sid in EVALUATOR_EXCLUDED: continue
                            bucket_by_skill[sid] = bucket
                            skills[sid] = SkillRecord(skill_id=sid,
                                skill_name=sid.replace("_"," ").title(),
                                domain=DOMAIN_CODE_MAP.get(dname, dname), bucket=bucket,
                                source="canonical_VA_microskill_clustering_second_pass_legacy")
            _load_from_records(second.get("micro_skill_records", []),
                               "canonical_VA_microskill_clustering_second_pass_legacy")

    meta = {
        "loaded_files":       loaded_files,
        "skill_count":        len(skills),
        "ontology_skill_count": len(skills),
        "ontology_bucket_counts": dict(Counter(s.bucket for s in skills.values())),
        "ontology_files_loaded":  list(loaded_files.keys()),
        "canonical_policy": "v7.3b: VA_microskill_clustering_v3 is canonical; snake_case IDs throughout; evaluator_excluded skills suppressed.",
    }
    return skills, bucket_by_skill, meta

# ═══════════════════════════════════════════════════════════════════════════════
# TEXT / EVIDENCE EXTRACTION  (unchanged from v7.1)
# ═══════════════════════════════════════════════════════════════════════════════

def build_evidence_id(prefix: str, i: int) -> str:
    return f"{prefix}_{i:04d}"

def extract_text_maps(essay_text: str, prompt_text: str = "") -> Dict[str, Any]:
    paragraphs = split_paragraphs(essay_text)
    sentence_map: List[Dict]   = []
    paragraph_map: List[Dict]  = []
    evidence_index: Dict       = {}
    ev_i = 1
    global_sent_idx = 0

    for pi, para in enumerate(paragraphs):
        sents = split_sentences(para)
        p_eid = build_evidence_id("ev", ev_i); ev_i += 1
        paragraph_map.append({
            "paragraph_index": pi, "sentence_indices": list(range(global_sent_idx, global_sent_idx+len(sents))),
            "text": para, "word_count": len(words(para)), "evidence_id": p_eid,
        })
        evidence_index[p_eid] = {"type":"paragraph","paragraph_index":pi,"quote":para[:500]}

        for local_i, sent in enumerate(sents):
            eid = build_evidence_id("ev", ev_i); ev_i += 1
            item = {
                "sentence_index": global_sent_idx, "paragraph_index": pi, "local_sentence_index": local_i,
                "text": sent, "word_count": len(words(sent)), "evidence_id": eid,
                "has_reason_cue":     contains_any(sent, REASON_CUES),
                "has_example_cue":    contains_any(sent, EXAMPLE_CUES),
                "has_conclusion_cue": contains_any(sent, CONCLUSION_CUES),
                "has_contrast_cue":   contains_any(sent, CONTRAST_CUES),
                "has_position_cue":   contains_any(sent, POSITION_CUES),
            }
            sentence_map.append(item)
            evidence_index[eid] = {"type":"sentence","sentence_index":global_sent_idx,"paragraph_index":pi,"quote":sent}
            global_sent_idx += 1

    claim_candidates = []; reason_candidates = []; support_candidates = []
    example_candidates = []; conclusion_candidates = []; contrast_candidates = []

    for s in sentence_map:
        text = s["text"]; low = text.lower()
        is_claim = (
            contains_any(text, POSITION_CUES) or
            re.search(r"\b(advantage|disadvantage|problem|benefit|important|claim|say|argue|should|needs?)\b", low) or
            s["local_sentence_index"] == 0
        )
        if is_claim:
            claim_candidates.append({"evidence_id":s["evidence_id"],"sentence_index":s["sentence_index"],"text":text})
        if s["has_reason_cue"]:
            reason_candidates.append({"evidence_id":s["evidence_id"],"sentence_index":s["sentence_index"],"text":text})
        if s["has_reason_cue"] or re.search(r"\b(result|leads?|because|therefore|so|due to|needs?)\b", low):
            support_candidates.append({"evidence_id":s["evidence_id"],"sentence_index":s["sentence_index"],"text":text})
        if s["has_example_cue"]:
            example_candidates.append({"evidence_id":s["evidence_id"],"sentence_index":s["sentence_index"],"text":text})
        if s["has_conclusion_cue"] or s["paragraph_index"] == len(paragraphs)-1:
            conclusion_candidates.append({"evidence_id":s["evidence_id"],"sentence_index":s["sentence_index"],"text":text})
        if s["has_contrast_cue"]:
            contrast_candidates.append({"evidence_id":s["evidence_id"],"sentence_index":s["sentence_index"],"text":text})

    cwords = content_words(essay_text)
    repeated = [{"word":w,"count":c} for w,c in Counter(cwords).most_common(30) if c >= 2]
    transitions = []
    for cue in sorted(TRANSITION_CUES, key=len, reverse=True):
        count = len(re.findall(r"\b"+re.escape(cue)+r"\b", essay_text.lower()))
        if count: transitions.append({"marker":cue,"count":count})

    return {
        "paragraph_map": paragraph_map, "sentence_map": sentence_map,
        "argument_map": {
            "claim_candidates": claim_candidates, "reason_candidates": reason_candidates,
            "support_candidates": support_candidates, "example_candidates": example_candidates,
            "conclusion_candidates": conclusion_candidates, "contrast_candidates": contrast_candidates,
        },
        "cohesion_map": {"transition_markers": transitions, "repeated_content_words": repeated},
        "evidence_index": evidence_index,
    }

# ═══════════════════════════════════════════════════════════════════════════════
# LEXICAL EXTRACTION  (v7.1 logic, hardcoded topic patterns replaced with
#                     generic IELTS academic vocabulary detection)
# ═══════════════════════════════════════════════════════════════════════════════

_MALFORMED_PATTERNS = re.compile(
    r"\b(what leads|work the \w+|people benefits|amount of \w+ becomes|\w+ becomes bigger \w+|"
    r"little amount of|the \w+ stops? \w+ing as|workers what leads)\b"
)
_PREDICATE_VERBS = re.compile(
    r"\b("
    # copulas / auxiliaries
    r"is|are|was|were|am|be|been|being|"
    r"has|have|had|having|"
    r"do|does|did|doing|done|"
    r"can|ca|can't|cannot|could|would|should|will|shall|may|might|must|"
    # high-frequency content verbs (base + inflected forms)
    r"bring|brings|brought|bringing|"
    r"cause|causes|caused|causing|"
    r"give|gives|gave|given|giving|"
    r"need|needs|needed|needing|"
    r"take|takes|took|taken|taking|"
    r"work|works|worked|working|"
    r"go|goes|went|gone|going|"
    r"support|supports|supported|supporting|"
    r"believe|believes|believed|believing|"
    r"make|makes|made|making|"
    r"teach|teaches|taught|teaching|"
    r"guide|guides|guided|guiding|"
    r"spend|spent|spending|"
    r"invest|invests|invested|investing|"
    r"include|includes|included|including|"
    r"grow|grows|grew|grown|growing|"
    r"cost|costs|costing|"
    r"lead|leads|led|leading|"
    r"solve|solves|solved|solving|"
    r"create|creates|created|creating|"
    r"help|helps|helped|helping|"
    r"think|thinks|thought|thinking|"
    r"know|knows|knew|known|knowing|"
    r"get|gets|got|gotten|getting|"
    r"come|comes|came|coming|"
    r"see|sees|saw|seen|seeing|"
    r"become|becomes|became|becoming|"
    r"face|faces|faced|facing|"
    r"affect|affects|affected|affecting|"
    r"pay|pays|paid|paying|"
    r"open|opens|opened|opening|"
    r"remember|tell|tells|told|telling|"
    r"stop|stops|stopped|stopping|"
    r"enable|enables|enabled|enabling|"
    r"allow|allows|allowed|allowing|"
    r"require|requires|required|requiring|"
    r"benefit|benefits|benefited|benefiting|"
    r"show|shows|showed|shown|showing|"
    r"live|lives|lived|living|"
    r"use|uses|used|using|"
    r"depend|depends|depended|depending|"
    r"feel|feels|felt|feeling|"
    r"look|looks|looked|looking|"
    r"increase|increases|increased|increasing|"
    r"decrease|decreases|decreased|decreasing|"
    r"provide|provides|provided|providing|"
    r"receive|receives|received|receiving|"
    r"reduce|reduces|reduced|reducing|"
    r"spend|spent|spending|"
    r"keep|keeps|kept|keeping"
    r")\b"
)
# Tokens that must not appear at the START of an n-gram span
_NGRAM_REJECT_START = frozenset({
    # coordinating conjunctions
    'and', 'but', 'or', 'nor', 'yet',
    # subject pronouns
    'i', 'we', 'you', 'he', 'she', 'they',
    # copulas / auxiliaries at start → VP fragment not NP
    'is', 'are', 'was', 'were', 'am', 'has', 'have', 'had', 'do', 'does', 'did',
    # prepositions that open PPs, not NPs
    'of', 'on', 'at', 'about', 'than', 'to', 'through', 'between', 'among', 'upon',
    # subject pronoun 'it' + subordinating conjunctions / adverbs that open non-NP spans
    'it', 'if', 'today', 'in', 'when', 'while', 'so', 'very', 'quite', 'rather',
    # subordinating conjunctions / discourse starters (not NP heads)
    'even', 'though', 'although', 'since', 'unless', 'while',
    'still', 'also', 'indeed', 'however', 'therefore', 'moreover',
    'firstly', 'secondly', 'finally', 'overall', 'because',
    # v7.3b.3: articles / demonstratives → headless NP fragment ("the main", "the other")
    'the', 'a', 'an', 'this', 'that', 'these', 'those',
})

# Tokens that must not appear at the END of an n-gram span (incomplete fragment)
_NGRAM_REJECT_END = frozenset({
    # trailing conjunction → span cuts across phrase boundary
    'and', 'but', 'or', 'nor',
    # trailing determiner → headless NP
    'a', 'an', 'the',
    # trailing preposition → incomplete PP head
    'of', 'to', 'in', 'on', 'at', 'for', 'about',
    # trailing complementizer
    'that', 'which', 'when', 'where',
    # trailing adverbs (incomplete phrase)
    'more', 'very', 'also', 'even',
    # trailing subject pronouns / cross-clause tokens
    'i', 'we', 'you', 'he', 'she', 'they', 'it', 'me',
    # trailing preposition-like / clause-connector words that leave phrase open
    'with', 'than', 'into', 'onto', 'upon', 'while', 'since', 'although', 'though', 'once',
    # v7.3b.3: trailing possessives → cross-boundary ("example my", "hand our")
    'my', 'your', 'his', 'her', 'our', 'their', 'its',
    # v7.3b.3: trailing place/time deictics → open reference ("hand there", "problem here")
    'there', 'here', 'then', 'now',
    # v7.3b.3: trailing copulas → open predicate ("people is", "society was")
    'is', 'are', 'was', 'were', 'am',
})

# Comparative/superlative adjective forms that must not end an NP or VP span —
# they signal a fragment where the head noun of the phrase is missing
# (e.g. "hand older", "ability to older", "investing in younger").
# Note: does NOT apply to VP-only collocations like "getting older" (handled separately).
_COMPARATIVE_FRAG_END = frozenset({
    'older', 'younger', 'wider', 'greater', 'higher', 'lower',
    'better', 'worse', 'larger', 'smaller', 'cheaper', 'richer',
    'poorer', 'harder', 'easier', 'faster', 'slower', 'longer',
    'shorter', 'stronger', 'weaker', 'deeper', 'lighter', 'heavier',
    'busier', 'healthier', 'wealthier', 'safer', 'broader', 'closer',
})

# Copulas / strong auxiliaries that, in mid-3gram position, mark an SV boundary
# (e.g. "generation is busy", "parents should go" → subject + predicate fragment)
_SV_GATE_AUX = frozenset({
    'is', 'are', 'was', 'were', 'am',
    'will', 'would', 'should', 'shall', 'may', 'might', 'must', 'can', 'could',
})


def _chunk_quality(unit: str) -> Tuple[str, float, List[str]]:
    lw = unit.lower().strip()
    toks = lw.split()
    if not toks: return "discard", 0.0, ["empty"]
    flags: List[str] = []; value = 0.40

    if toks[0] in STOPWORDS or toks[-1] in STOPWORDS:
        flags.append("edge_function_word"); value -= 0.08
    if _MALFORMED_PATTERNS.search(lw):
        flags.append("possible_malformed_or_boundary_error"); value += 0.35
    if any(w in lw for w in BASIC_ACADEMIC_WORDS):
        flags.append("topic_relevant"); value += 0.20
    if _PREDICATE_VERBS.search(lw):
        flags.append("predicate_argument_candidate"); value += 0.15
    if len(toks) >= 6:
        flags.append("overlong_ngram"); value -= 0.20
    if len(toks) == 1 and lw not in BASIC_ACADEMIC_WORDS and lw not in VAGUE_NOUNS:
        value -= 0.15
    if not flags: flags.append("standard_candidate")

    if value >= 0.70:   label = "high_value_lret_candidate"
    elif value >= 0.45: label = "medium_value_lret_candidate"
    else:               label = "low_value_lret_candidate"
    return label, round(clamp(value) or 0.0, 3), flags

def extract_lexical_units(essay_text: str, maps: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    v7.3b: Extract lexical units for LRET consumption.

    LRET classifies each unit as KEEP / FIX / ENHANCE — evaluator does NOT label.
    Coverage must include all three categories:
      KEEP candidates  — good collocations student uses correctly (ageing population,
                         care homes, community groups, etc.)
      ENHANCE candidates — vague, generic or weak vocabulary (things, people, a lot,
                           some problems, many benefits)
      FIX candidates   — malformed patterns, informal register, incorrect collocations

    Changes from v7.3a extraction:
      1. Deduplicated by text only — no dual-type entries for same span.
      2. Unit gets exactly ONE type (verb_phrase wins over noun_phrase when span
         contains a main verb; otherwise noun_phrase).
      3. 2-gram and 3-gram only — 4/5-gram windows produce unintelligible cross-clause spans.
      4. Clause-break filter — 3-grams skip spans where internal token is a subject
         pronoun or subordinating/coordinating conjunction.
      5. ALL adjacent 2-gram content-word pairs are extracted (not only those containing
         academic words or predicate verbs) — ensures KEEP candidates are captured.
      6. axis_candidates and extraction_flags cover all three LRET categories.
    """
    units: Dict[str, Dict] = {}   # keyed by unit_text.lower() — no type duplication

    # Internal clause-break tokens: their presence inside (not at edge of) an n-gram
    # indicates the span crosses a clause boundary.
    _CLAUSE_INNER = frozenset({
        'because', 'although', 'though', 'since', 'while', 'when', 'if', 'unless',
        'which', 'who', 'whom', 'that',          # relative / subordinate markers
        'and', 'but', 'or', 'nor', 'yet',     # coordinating conjunctions
        'i', 'we', 'you', 'they', 'he', 'she', 'it',  # subject pronouns
        # v7.3b.3: comparative connector in inner position → clause boundary
        # ("experience than young" = two phrases bridged by than)
        'than', 'as',
        # v7.3b.3: copula in inner position of 3-gram → SV boundary
        # ("people is growing" = subject + predicate start)
        'is', 'are', 'was', 'were', 'am',
        # v7.3b.3: possessive det in inner position → phrase-boundary crossing
        # ("example my grandmother" = discourse phrase + next NP)
        'my', 'your', 'his', 'her', 'our', 'their', 'its',
    })

    # v7.3b.3: 3-gram with article in middle = phrase-boundary crossing
    # ("conclusion an ageing" = end of one NP + start of next)
    _MIDDLE_ARTICLE = frozenset({'a', 'an', 'the'})

    def has_clause_break(toks: List[str]) -> bool:
        if len(toks) <= 2:
            return False
        inner = [t.lower() for t in toks[1:-1]]
        if any(t in _CLAUSE_INNER for t in inner):
            return True
        if len(toks) == 3 and inner[0] in _MIDDLE_ARTICLE:
            return True
        return False

    def infer_type(toks: List[str]) -> str:
        if len(toks) == 1:
            return 'word'
        if _PREDICATE_VERBS.search(' '.join(t.lower() for t in toks)):
            return 'verb_phrase_or_predicate_chunk'
        return 'noun_phrase'

    def assess(toks: List[str]) -> Tuple[str, float, List[str], List[str]]:
        low  = [t.lower() for t in toks]
        joined = ' '.join(low)
        axes: List[str] = []
        flags: List[str] = []
        val = 0.45

        # KEEP candidates: clean 2-gram collocations (all content-word pairs)
        if len(toks) == 2 and sum(1 for t in low if t not in STOPWORDS) == 2:
            flags.append('collocation_candidate')
            axes.append('collocation_naturalness')
            val += 0.08

        # Academic / topic vocabulary (KEEP or ENHANCE)
        if any(t in BASIC_ACADEMIC_WORDS for t in low):
            axes.extend(['topic_vocabulary', 'semantic_specificity'])
            flags.append('topic_relevant')
            val += 0.18

        # Predicate-argument structure
        if _PREDICATE_VERBS.search(joined):
            axes.append('predicate_argument')
            flags.append('predicate_argument_candidate')
            val += 0.12

        # Malformed / incorrect collocation (FIX candidate)
        if _MALFORMED_PATTERNS.search(joined):
            axes.append('semantic_compatibility')
            flags.append('possible_malformed_or_boundary_error')
            val += 0.20

        # Vague vocabulary (ENHANCE candidate)
        if any(t in VAGUE_NOUNS for t in low):
            axes.append('semantic_specificity')
            flags.append('vague_vocabulary_candidate')
            val += 0.10

        # Informal register (FIX candidate)
        if any(t in INFORMAL_PHRASES for t in low + [joined]):
            axes.append('register_control')
            flags.append('informal_register')
            val += 0.10

        # Edge stopword (less useful)
        if low and (low[0] in STOPWORDS or low[-1] in STOPWORDS):
            flags.append('edge_function_word')
            val -= 0.05

        if not axes:
            axes.append('word_choice')

        val = round(min(1.0, max(0.0, val)), 3)
        sig = ('high_value_lret_candidate' if val >= 0.70
               else 'medium_value_lret_candidate' if val >= 0.50
               else 'standard_lret_candidate')
        return sig, val, sorted(set(axes)), flags

    def add(unit_text: str, utype: str, sent: Dict) -> None:
        u = re.sub(r'\s+', ' ', unit_text.strip(' ,.;:!?()[]{}\"\'')).strip()
        if len(u) < 3:
            return
        key = u.lower()
        toks = u.split()
        sig, val, axes, flags = assess(toks)
        if val < 0.30:
            return

        # ── LRET relevance gate (v7.3b.2) ────────────────────────────────────
        # Single words and noun phrases are valid lexical units regardless of
        # signal strength — vocabulary items (even generic ones) are assessable
        # by LRET. Only subject+verb fragments and truncated modal/copula
        # n-grams are discarded: these are syntactic artifacts, not lexical
        # units (e.g. "government can", "issue is", "should go").
        _LRET_ACT = frozenset({'informal_register', 'vague_vocabulary_candidate',
                               'possible_malformed_or_boundary_error'})
        flag_set = set(flags)

        # VP units that land on a function word boundary and carry no
        # collocation or LRET-actionable signal are SV/modal truncations:
        # "can cause", "issue is", "grandmother is", "should go", etc.
        if utype == 'verb_phrase_or_predicate_chunk':
            if ('edge_function_word' in flag_set
                    and 'collocation_candidate' not in flag_set
                    and val < 0.75
                    and not (flag_set & _LRET_ACT)):
                return
            # v7.3b.3: SV-aux gate — 3-gram [NOUN] [COPULA/MODAL] [*]
            # "generation is busy", "parents should go" → SV predicate fragments
            if (len(toks) == 3
                    and toks[1].lower() in _SV_GATE_AUX
                    and toks[0].lower() not in STOPWORDS
                    and 'collocation_candidate' not in flag_set
                    and not (flag_set & _LRET_ACT)):
                return

        # v7.3b.3: comparative-end gate (NP and VP) ──────────────────────────
        # Spans ending in a bare comparative/superlative adj with no head noun
        # are phrase-boundary fragments ("hand older", "investing in younger").
        # Exception: genuine V+ADJ collocations ("getting older") — these are
        # 2-grams where both tokens are content words (collocation_candidate set).
        if toks[-1].lower() in _COMPARATIVE_FRAG_END:
            if utype == 'noun_phrase':
                return   # NP fragment: head noun is the comparative, no modifier
            if utype == 'verb_phrase_or_predicate_chunk':
                # Allow 2-gram V+ADJ collocations ("getting older", "feeling better")
                if not (len(toks) == 2 and 'collocation_candidate' in flag_set):
                    return
        # ─────────────────────────────────────────────────────────────────────

        if key not in units:
            units[key] = {
                'unit_id':               f'lu_{len(units)+1:04d}',
                'unit':                  u,
                'unit_type':             utype,
                'source_sentence_index': sent.get('sentence_index'),
                'source_paragraph_index':sent.get('paragraph_index'),
                'context':               sent.get('text', ''),
                'axis_candidates':       axes,
                'frequency':             0,
                'evidence_source':       'rule_extraction_v7_3b',
                'evidence_ids':          [sent.get('evidence_id')],
                'candidate_value':       val,
                'extraction_signal':     sig,
                'extraction_flags':      flags,
                'classification_policy': 'extraction_only_lret_must_classify',
            }
        units[key]['frequency'] += 1
        ev = sent.get('evidence_id')
        if ev and ev not in units[key]['evidence_ids']:
            units[key]['evidence_ids'].append(ev)
        # Refresh with frequency bonus
        _, val2, _, _ = assess(units[key]['unit'].split())
        units[key]['candidate_value'] = round(min(1.0, val2 + min(0.08, 0.02*units[key]['frequency'])), 3)

    for sent in maps.get('sentence_map', []):
        stext = sent.get('text', '')
        toks = words(stext)
        low_toks = [t.lower() for t in toks]

        # 1. Single content words
        for tok, ltok in zip(toks, low_toks):
            if ltok in STOPWORDS or len(ltok) <= 2:
                continue
            add(tok, 'word', sent)

        # 2. Discourse / formulaic markers — FIRST so they claim their key
        #    before 2/3-gram loops can mis-type them as noun_phrase
        for cue in sorted(TRANSITION_CUES | INFORMAL_PHRASES):
            if cue.lower() in stext.lower():
                add(cue, 'discourse_or_formulaic_chunk', sent)

        # 3. 2-gram collocations — ALL adjacent pairs (captures KEEP candidates)
        for i in range(len(toks) - 1):
            chunk = toks[i:i+2]
            if all(t.lower() in STOPWORDS for t in chunk):
                continue
            if chunk[0].lower() in _NGRAM_REJECT_START:
                continue
            if chunk[-1].lower() in _NGRAM_REJECT_END:
                continue
            add(' '.join(chunk), infer_type(chunk), sent)

        # 4. 3-gram collocations — must have 2+ content words, no clause break
        for i in range(len(toks) - 2):
            chunk = toks[i:i+3]
            if sum(1 for t in chunk if t.lower() not in STOPWORDS) < 2:
                continue
            if has_clause_break(chunk):
                continue
            if chunk[0].lower() in _NGRAM_REJECT_START:
                continue
            if chunk[-1].lower() in _NGRAM_REJECT_END:
                continue
            add(' '.join(chunk), infer_type(chunk), sent)

    out = sorted(units.values(),
                 key=lambda u: (u['candidate_value'], u['frequency']),
                 reverse=True)
    capped = out[:MAX_LEXICAL_UNITS]
    for idx, u in enumerate(capped, 1):
        u['unit_id'] = f'lu_{idx:04d}'
    return capped

def normalize_detector_output(det: Optional[Dict]) -> Dict[str, Any]:
    if not det: return {"available":False,"diagnostic_rows":[],"summary":{}}
    root = det
    if isinstance(det.get("results"), list) and det["results"]: root = det["results"][0]
    rows: List[Dict] = []
    for key in ["diagnostic_rows","survived_candidates","validated_rows","rows","errors"]:
        if isinstance(root.get(key), list): rows.extend(root[key])
    cand = root.get("candidate_lists") if isinstance(root.get("candidate_lists"), dict) else {}
    for key in ["diagnostic_rows","survived_candidates","validated_rows","rows"]:
        if isinstance(cand.get(key), list): rows.extend(cand[key])
    compact = []
    for i, r in enumerate(rows[:500]):
        if not isinstance(r, dict): continue
        compact.append({
            "detector_evidence_id": r.get("row_id") or r.get("candidate_id") or f"det_{i+1:04d}",
            "rubric":    r.get("rubric") or r.get("category"),
            "family":    r.get("family") or r.get("issue_code"),
            "error_family": r.get("family") or r.get("issue_code"),
            "quote":     r.get("quote") or r.get("surface_quote") or r.get("local_quote"),
            "span_text": r.get("quote") or r.get("surface_quote") or r.get("local_quote"),
            "suggestion": r.get("repair_hypothesis") or r.get("suggestion"),
            "start":     r.get("span_start") or r.get("start"),
            "end":       r.get("span_end") or r.get("end"),
            "sentence_index":  r.get("sentence_index"),
            "paragraph_index": r.get("paragraph_index"),
            "paragraph_idx":   r.get("paragraph_index"),
            "confidence":      r.get("confidence"),
            "severity":        r.get("severity"),
        })
    return {"available":True,"diagnostic_rows":compact,"summary":{"row_count":len(compact)}}

def normalize_scorer_output(score: Optional[Dict]) -> Dict[str, Any]:
    if not score: return {"available":False}
    return {"available":True,"context_keys":list(score.keys())[:30],
            "note":"Scorer output provided as context only; evaluator v7 does not output IELTS scores."}

def baseline_features(essay_text: str, maps: Dict, lex_units: List[Dict]) -> Dict[str, Any]:
    w = lower_words(essay_text); cw = content_words(essay_text)
    sc = len(maps.get("sentence_map",[])); pc = len(maps.get("paragraph_map",[]))
    arg = maps.get("argument_map",{}); tr = maps.get("cohesion_map",{}).get("transition_markers",[])
    gf = extract_grammar_features(essay_text, maps.get("sentence_map", []))
    return {
        "word_count":                 len(w),
        "paragraph_count":            pc,
        "sentence_count":             sc,
        "avg_sentence_length":        round(len(w)/max(1,sc), 2),
        "content_word_diversity":     round(len(set(cw))/max(1,len(cw)), 3),
        "claim_candidate_count":      len(arg.get("claim_candidates",[])),
        "reason_candidate_count":     len(arg.get("reason_candidates",[])),
        "support_candidate_count":    len(arg.get("support_candidates",[])),
        "example_candidate_count":    len(arg.get("example_candidates",[])),
        "conclusion_candidate_count": len(arg.get("conclusion_candidates",[])),
        "transition_marker_count":    sum(t["count"] for t in tr),
        "transition_marker_variety":  len(tr),
        "lexical_unit_count":         len(lex_units),
        "repeated_content_word_count":len(maps.get("cohesion_map",{}).get("repeated_content_words",[])),
        "grammar_features":           gf,   # v7.3a: independent surface grammar analysis
    }


# ── v7.3a: Independent grammar surface analysis ───────────────────────────────

_DOUBLE_COMP_RE = re.compile(
    r"\bmore\s+(stronger|weaker|bigger|smaller|faster|slower|better|worse|"
    r"higher|lower|older|younger|easier|harder|longer|shorter|wider|narrower|"
    r"deeper|richer|poorer|heavier|lighter|greater|lesser|louder|quieter)\b",
    re.I,
)
_INFORMAL_SURFACE_RE = re.compile(
    r"\b(gonna|wanna|gotta|kinda|sorta|a lot of|lots of|big deal|no doubt|"
    r"for sure|you know|let's face it|it's obvious|everyone knows|"
    r"needless to say|as we all know|clearly everyone)\b",
    re.I,
)
_HEDGE_RE = re.compile(
    r"\b(arguably|perhaps|possibly|probably|may|might|could|seem|appears?|suggests?|"
    r"tends? to|generally|typically|often|usually|in many cases|to some extent|"
    r"it is possible that|it is likely that|it could be argued|"
    r"some (would|might) argue|this suggests|this implies|it appears)\b",
    re.I,
)
_PASSIVE_RE = re.compile(r"\b(is|are|was|were|been|be)\s+\w+ed\b", re.I)
_VERB_SIGNAL_RE = re.compile(
    r"\b(is|are|was|were|has|have|had|do|does|did|will|would|can|could|should|"
    r"may|might|must|shall|be|been|being|go|make|take|come|think|know|see|say|"
    r"tell|give|get|put|keep|let|begin|seem|show|hear|play|run|move|live|believe|"
    r"hold|bring|happen|write|stand|lose|fall|require|remain|suggest|represent|"
    r"produce|provide|develop|consider|appear|involve|follow|relate|need|feel|"
    r"become|include|allow|enable|lead|cause|result|mean|argue|claim|believe)\b",
    re.I,
)
_NOMIN_RE = re.compile(
    r"\b\w{6,}(tion|tions|ment|ments|ness|nesses|ity|ities|ance|ances|ence|ences|ism|isms)\b",
    re.I,
)
_RUN_ON_CONJ_RE = re.compile(
    r"\b(and|but|so|yet|or|nor|because|which|that|who|whom|where|when|since|although|though|while)\b",
    re.I,
)

def extract_grammar_features(essay_text: str, sent_map: List[Dict]) -> Dict[str, Any]:
    """
    v7.3a NEW — Rule-based surface grammar analysis directly from essay text.

    Does NOT require detector output. Returns observable grammar pattern features
    that let the evaluator dimension grammar skills independently.
    These are HEURISTIC PROXIES — not equivalent to detector accuracy assessment.
    They provide a meaningful signal for essay-only mode and supplement detector data.
    """
    sents = [s["text"] for s in sent_map] or split_sentences(essay_text)
    all_low = essay_text.lower()
    n = max(1, len(sents))

    # 1. Double-comparative forms (more stronger, more bigger, etc.)
    dbl_hits = _DOUBLE_COMP_RE.findall(all_low)
    double_comp_count = len(dbl_hits)

    # 2. Informal surface markers beyond INFORMAL_PHRASES constant
    informal_count = len(_INFORMAL_SURFACE_RE.findall(all_low))

    # 3. Sentence length coefficient of variation (variety proxy)
    sent_lengths = [len(words(s)) for s in sents if words(s)]
    if len(sent_lengths) >= 2:
        mean_sl = sum(sent_lengths) / len(sent_lengths)
        std_sl  = (sum((l - mean_sl)**2 for l in sent_lengths) / len(sent_lengths)) ** 0.5
        sent_length_cv = round(std_sl / max(1, mean_sl), 3)
    else:
        sent_length_cv = 0.0

    # 4. Sentence-opener variety (first 2 words, lower-cased)
    openers = []
    for s in sents:
        ws = words(s)
        if ws:
            openers.append(" ".join(ws[:min(2, len(ws))]).lower())
    opener_variety = round(len(set(openers)) / max(1, len(openers)), 3) if openers else 0.0

    # 5. Run-on sentence proxy (>35 words with ≥4 conjunctions)
    run_on_count = sum(
        1 for s in sents
        if len(words(s)) > 35 and len(_RUN_ON_CONJ_RE.findall(s)) >= 4
    )

    # 6. Fragment proxy (1–4 word sentences with no recognisable verb)
    fragment_count = sum(
        1 for s in sents
        if 1 <= len(words(s)) <= 4 and not _VERB_SIGNAL_RE.search(s)
    )

    # 7. Hedging / epistemic marker density
    hedge_count = sum(1 for s in sents if _HEDGE_RE.search(s))
    hedge_density = round(hedge_count / n, 3)

    # 8. Passive voice density
    passive_count = sum(1 for s in sents if _PASSIVE_RE.search(s))
    passive_density = round(passive_count / n, 3)

    # 9. Nominalisation density (academic register proxy)
    all_cwords = content_words(essay_text)
    nomin_types = {w for w in all_cwords if _NOMIN_RE.match(w)}
    nomin_density = round(len(nomin_types) / max(1, len(set(all_cwords))), 3)

    # 10. Paragraph topic-sentence quality proxy
    #     First sentence of each body paragraph should be ≥8 words and have a claim/content signal
    para_map = sent_map  # used just for reference; actual paragraphing checked below
    # (lightweight proxy — not per-paragraph iteration to avoid re-splitting)
    long_sent_ratio = round(sum(1 for l in sent_lengths if l >= 10) / max(1, len(sent_lengths)), 3)

    return {
        "double_comparative_count":   double_comp_count,
        "double_comparative_examples":dbl_hits[:3],
        "informal_marker_count":      informal_count,
        "sentence_length_cv":         sent_length_cv,
        "sentence_opener_variety":    opener_variety,
        "run_on_count":               run_on_count,
        "fragment_count":             fragment_count,
        "hedge_density":              hedge_density,
        "passive_density":            passive_density,
        "nominalisation_density":     nomin_density,
        "long_sentence_ratio":        long_sent_ratio,
        "analysis_mode":              "rule_based_surface_proxy_v7_3a",
    }


def apply_detector_grammar_penalties(
    vector: Dict[str, Optional[float]],
    detector: Dict,
) -> Dict[str, List[str]]:
    """
    v7.3a BUG FIX 2 — When detector is available, penalise grammar vector dimensions
    based on error family hit counts from detector rows.

    control_proxy starts at its rule-engine value (typically 0.75 minus surface penalties)
    and is further reduced per error family match.  Capped at 5 errors per family to
    prevent single family from zeroing the dimension.

    Returns a penalty_log dict {dimension: [\"FAMILY×count\", ...]} for diagnostic notes.
    Called from _compute_vector() grammar branch only.
    """
    if not detector.get("available"):
        return {}
    rows = detector.get("diagnostic_rows", [])
    if not rows:
        return {}

    family_counts: Dict[str, int] = {}
    for r in rows:
        fam = (r.get("family") or "").upper().strip()
        if fam:
            family_counts[fam] = family_counts.get(fam, 0) + 1

    penalty_log: Dict[str, List[str]] = {}
    for fam, count in family_counts.items():
        mapping = GRAMMAR_ERROR_FAMILY_MAP.get(fam)
        if not mapping:
            continue
        dim = mapping["dimension"]
        pen = mapping["penalty_per_error"]
        if dim not in vector:
            continue
        current = vector[dim] if isinstance(vector[dim], float) else 0.75
        capped_count = min(count, 5)
        vector[dim] = round(max(0.0, current - pen * capped_count), 3)
        penalty_log.setdefault(dim, []).append(f"{fam}×{count}")

    return penalty_log

# ═══════════════════════════════════════════════════════════════════════════════
# EVIDENCE DEPTH ENGINE  (NEW in v7.2)
# ═══════════════════════════════════════════════════════════════════════════════

# ── Pattern banks ─────────────────────────────────────────────────────────────

_GENERIC_CLAIM_RE = re.compile(
    r"^(there are|this topic has|this (issue|topic|matter|subject)|"
    r"some people say|some say|people say|as we can see|"
    r"(advantages?|disadvantages?|pros?|cons?) and (disadvantages?|advantages?|cons?|pros?)|"
    r"in this essay (i will|we will|this|both))",
    re.I,
)
_SPECIFIC_CLAIM_RE = re.compile(
    r"\b(i (believe|think|argue|maintain|would argue)|"
    r"in my (opinion|view)|"
    r"this essay (argues?|contends?|demonstrates?)|"
    r"the (main|primary|key|most important|fundamental|chief|central) (reason|cause|benefit|problem|advantage|disadvantage|issue|challenge) is|"
    r"(the government|society|employers?|schools?|individuals?) (should|must|need to|ought to|has to|have to)|"
    r"overall[,;]?\s+\w)",
    re.I,
)
_REBUTTAL_RE = re.compile(
    r"(some critics?|opponents?|those who oppose|those who disagree) (argue|claim|believe|say|think)|"
    r"others? (might|may|could) (argue|claim|say|suggest)|"
    r"the (argument|claim|view|idea|notion) that .{5,80} (is|seems|appears|looks) (wrong|incorrect|flawed|limited|misleading|oversimplified)|"
    r"while (it is|this is|that is|this may be|it may be) (true|correct|valid|accurate|understandable|reasonable),",
    re.I,
)
_COUNTERARG_ENGAGEMENT_RE = re.compile(
    r"(however|nevertheless|despite this|yet|even so)[,\s].{0,60}(fail|ignore|overlook|miss|neglect|not account)|"
    r"(to a (limited|certain|large) extent)|"
    r"(this (ignores?|overlooks?|fails? to account for|misses?))|"
    r"(while .{5,60}(is|are) (true|valid|correct|understandable)[,\s].{0,60}(however|nevertheless|but|yet|the reality))",
    re.I,
)
_WARRANT_RE = re.compile(
    r"(society|government|we|people|humans?) (has|have|must|should) (an? )?(obligation|responsibility|duty|right) to|"
    r"(it is|this is) (the|a) (responsibility|obligation|right|duty|role) of|"
    r"by (definition|nature|design|necessity)[,\s]|"
    r"in (any|most|all) (healthy|functioning|modern|democratic|civilised) (society|societies|system)[,\s]|"
    r"(the fundamental|the core|the basic|the underlying|the key) (reason|principle|logic|idea|value) (is|being|here)|"
    r"according to (the principle|established|research|studies|evidence)[,\s]|"
    r"(this is because|the reason (being|is) that) .{5,60}(general|universal|always|all people|everyone|every|inherently)",
    re.I,
)
_QUAL_RE = re.compile(
    r"\b(usually|often|in most cases|tends? to|generally|typically|in many cases|"
    r"to (some|a certain|a large) extent|can|may|might|sometimes|not always|"
    r"it depends|in certain|in some)\b",
    re.I,
)
_CAUSAL_CHAIN_RE = re.compile(
    r"\b(because|since|as a result|consequently|therefore|thus|hence|"
    r"due to|leads? to|causes?|results? in|enables?|allows?|means? that)\b",
    re.I,
)

def _full_text(sent_map: List[Dict]) -> str:
    return " ".join(s["text"].lower() for s in sent_map)

def is_advantages_disadvantages_pattern(maps: Dict[str, Any]) -> bool:
    """
    Returns True when the essay uses a pros/cons listing structure
    without genuine counterargument or rebuttal engagement.
    """
    all_text = _full_text(maps.get("sentence_map", []))
    has_both_sides = bool(
        # v7.3a BUG FIX 1: was \badvantage\b — word boundary after "advantage" failed for "advantages"
        # because "s" is a word character. Now matches singular AND plural forms.
        re.search(r"\badvantages?\b", all_text) and re.search(r"\bdisadvantages?\b", all_text)
        or re.search(r"\bpros?\b", all_text) and re.search(r"\bcons?\b", all_text)
        or re.search(r"\bbenefits?\b", all_text) and re.search(r"\bdrawbacks?\b", all_text)
    )
    if not has_both_sides: return False
    has_rebuttal   = bool(_REBUTTAL_RE.search(all_text))
    has_engagement = bool(_COUNTERARG_ENGAGEMENT_RE.search(all_text))
    return not (has_rebuttal or has_engagement)

def is_specific_position(text: str) -> bool:
    low = text.lower().strip()
    if _GENERIC_CLAIM_RE.search(low): return False
    return bool(_SPECIFIC_CLAIM_RE.search(low))

def _count_non_trivial_reasons(sent_map: List[Dict]) -> int:
    """Count sentences that form a genuine causal explanation (not circular)."""
    count = 0
    for s in sent_map:
        if not s.get("has_reason_cue"): continue
        low = s["text"].lower()
        chains = _CAUSAL_CHAIN_RE.findall(low)
        if len(chains) >= 1 and len(words(s["text"])) >= 8:
            count += 1
    return count

# ── Depth estimators by skill category ────────────────────────────────────────

def _depth_claim(skill_id: str, maps: Dict, base: Dict) -> int:
    claims = maps["argument_map"].get("claim_candidates", [])
    if not claims: return DEPTH_0
    specific = [c for c in claims if is_specific_position(c["text"])]
    if skill_id in ("arg_claim_generation",):
        # Just needs ANY claim generated
        return DEPTH_2 if specific else DEPTH_1
    if skill_id in ("arg_claim_relevance",):
        # Claim relevant to topic — almost always true if claim exists
        return DEPTH_2 if claims else DEPTH_0
    if skill_id in ("arg_claim_precision",):
        # Clear scope
        return DEPTH_2 if specific else DEPTH_1
    if skill_id in ("arg_claim_specificity",):
        # Specific mechanisms named
        return DEPTH_2 if len(specific) >= 1 else DEPTH_1 if claims else DEPTH_0
    # Default claim
    return DEPTH_2 if specific else DEPTH_1

def _depth_position_consistency(maps: Dict, base: Dict) -> int:
    claims = maps["argument_map"].get("claim_candidates", [])
    specific = [c for c in claims if is_specific_position(c["text"])]
    paras = maps.get("paragraph_map", [])
    if not specific: return DEPTH_1 if claims else DEPTH_0
    # Check position appears in multiple paragraphs
    if len(paras) >= 3 and len(specific) >= 2: return DEPTH_2
    return DEPTH_1

def _depth_reason(skill_id: str, maps: Dict, base: Dict) -> int:
    reasons = maps["argument_map"].get("reason_candidates", [])
    if not reasons: return DEPTH_0
    non_trivial = _count_non_trivial_reasons(maps.get("sentence_map", []))
    if skill_id in ("arg_reason_quality",):
        return DEPTH_2 if non_trivial >= 2 else DEPTH_1 if non_trivial >= 1 else DEPTH_0
    if skill_id in ("arg_reason_relevance",):
        return DEPTH_2 if non_trivial >= 2 else DEPTH_1 if reasons else DEPTH_0
    # reason_generation: were any reasons generated?
    return DEPTH_2 if non_trivial >= 2 else DEPTH_1 if non_trivial >= 1 or reasons else DEPTH_0

def _depth_support(skill_id: str, maps: Dict, base: Dict) -> int:
    support = maps["argument_map"].get("support_candidates", [])
    reasons = maps["argument_map"].get("reason_candidates", [])
    examples = maps["argument_map"].get("example_candidates", [])
    if not support: return DEPTH_0
    has_examples = len(examples) >= 1
    multiple = len(support) >= 2
    if skill_id in ("arg_support_alignment",):
        return DEPTH_2 if multiple and reasons else DEPTH_1 if support else DEPTH_0
    return DEPTH_2 if multiple and (reasons or has_examples) else DEPTH_1

def _depth_rebuttal(maps: Dict, base: Dict, is_ad: bool) -> int:
    if is_ad: return DEPTH_0   # A/D listing structure — slot only
    all_text = _full_text(maps.get("sentence_map", []))
    if _REBUTTAL_RE.search(all_text): return DEPTH_2
    if _COUNTERARG_ENGAGEMENT_RE.search(all_text): return DEPTH_1
    if any(s.get("has_contrast_cue") for s in maps.get("sentence_map", [])): return DEPTH_0
    return DEPTH_0

def _depth_counterarg(maps: Dict, base: Dict, is_ad: bool) -> int:
    if is_ad: return DEPTH_0
    all_text = _full_text(maps.get("sentence_map", []))
    if _REBUTTAL_RE.search(all_text) and _COUNTERARG_ENGAGEMENT_RE.search(all_text): return DEPTH_2
    if _COUNTERARG_ENGAGEMENT_RE.search(all_text): return DEPTH_1
    return DEPTH_0

def _depth_warrant(maps: Dict, base: Dict) -> int:
    all_text = _full_text(maps.get("sentence_map", []))
    if _WARRANT_RE.search(all_text): return DEPTH_2
    # Implicit warrant (strong causal chain with elaboration)
    nt = _count_non_trivial_reasons(maps.get("sentence_map", []))
    if nt >= 3: return DEPTH_1
    return DEPTH_0

def _depth_conclusion(maps: Dict, base: Dict) -> int:
    conclusions = maps["argument_map"].get("conclusion_candidates", [])
    if not conclusions: return DEPTH_0
    # Generic conclusion check
    last = conclusions[-1]["text"].lower()
    generic = bool(re.search(
        r"(as we can see|in conclusion|to (conclude|sum up|summarize))[,.]? "
        r"(there|this|it) (are|is|are some)",
        last
    ))
    if generic: return DEPTH_1
    specific = is_specific_position(conclusions[-1]["text"])
    return DEPTH_2 if specific else DEPTH_1

def _depth_overgeneralization(maps: Dict, base: Dict) -> int:
    all_text = _full_text(maps.get("sentence_map", []))
    qual_hits = len(_QUAL_RE.findall(all_text))
    if qual_hits >= 3: return DEPTH_2
    if qual_hits >= 1: return DEPTH_1
    return DEPTH_0

def _depth_causal(maps: Dict, base: Dict) -> int:
    nt = _count_non_trivial_reasons(maps.get("sentence_map", []))
    if nt >= 3: return DEPTH_2
    if nt >= 1: return DEPTH_1
    return DEPTH_0

def _depth_qualification(maps: Dict, base: Dict) -> int:
    return _depth_overgeneralization(maps, base)  # same evidence

def _depth_reasoning_depth(maps: Dict, base: Dict) -> int:
    nt = _count_non_trivial_reasons(maps.get("sentence_map", []))
    support = len(maps["argument_map"].get("support_candidates", []))
    if nt >= 3 and support >= 3: return DEPTH_2
    if nt >= 1: return DEPTH_1
    return DEPTH_0

def _depth_generic(skill_id: str, maps: Dict, base: Dict) -> int:
    """Depth estimator for non-argumentation skills."""
    sid = skill_id.lower()
    # Organization
    if any(k in sid for k in ["organization","paragraph","introduction","conclusion","structure","hierarchy","ws_"]):
        pc = base["paragraph_count"]; has_conc = base["conclusion_candidate_count"] > 0
        if pc >= 4 and has_conc: return DEPTH_2
        if pc >= 3: return DEPTH_1
        return DEPTH_0
    # Cohesion
    if any(k in sid for k in ["cohesion","transition","reference","flow","progression","sequencing"]):
        v = base["transition_marker_variety"]
        if v >= 4: return DEPTH_2
        if v >= 2: return DEPTH_1
        return DEPTH_0
    # Lexical
    if any(k in sid for k in ["lexical","vocabulary","collocation","word","phrase","nominalization","register","academic_tone"]):
        d = base["content_word_diversity"]
        if d >= 0.65: return DEPTH_2
        if d >= 0.45: return DEPTH_1
        return DEPTH_0
    # Task understanding
    if any(k in sid for k in ["task","genre","purpose","audience","prompt","component"]):
        cc = base["claim_candidate_count"]; pc = base["paragraph_count"]
        if cc >= 3 and pc >= 3: return DEPTH_2
        if cc >= 1: return DEPTH_1
        return DEPTH_0
    # Style
    if any(k in sid for k in ["style","tone","formality","clarity","conciseness","hedging"]):
        return DEPTH_1  # always some stylistic signal present
    # Content development
    if any(k in sid for k in ["content","idea","develop","depth","elaboration","topic"]):
        rc = base["reason_candidate_count"]; sc = base["support_candidate_count"]
        if rc >= 3 and sc >= 3: return DEPTH_2
        if rc >= 1: return DEPTH_1
        return DEPTH_0
    # Thinking / metacognitive
    if any(k in sid for k in ["think","metacognit","critical","aware","monitor","self"]):
        nt = _count_non_trivial_reasons(maps.get("sentence_map", []))
        return DEPTH_1 if nt >= 2 else DEPTH_0
    # Default: conservative
    return DEPTH_1 if base.get("word_count", 0) >= 200 else DEPTH_0

def estimate_skill_depth(skill_id: str, maps: Dict, base: Dict, is_ad: bool) -> int:
    """
    Dispatcher: returns DEPTH_0/1/2/3 for a given skill.
    This is the ceiling — the LLM may lower it, but cannot raise it.
    """
    sid = skill_id.lower()
    if sid in {"arg_rebuttal_generation","arg_rebuttal_quality","rebuttal_quality"}:
        return _depth_rebuttal(maps, base, is_ad)
    if sid in {"arg_counterargument_development","arg_counterargument_generation","counterargument_development"}:
        return _depth_counterarg(maps, base, is_ad)
    if sid in {"evaluation_of_alternatives"}:
        return _depth_counterarg(maps, base, is_ad)  # same structural requirement
    if sid in {"arg_warrant_generation","arg_warrant_quality"}:
        return _depth_warrant(maps, base)
    if "claim" in sid and sid.startswith("arg_"):
        return _depth_claim(sid, maps, base)
    if sid == "arg_position_consistency":
        return _depth_position_consistency(maps, base)
    if sid in {"arg_reason_generation","arg_reason_quality","arg_reason_relevance"}:
        return _depth_reason(sid, maps, base)
    if sid in {"arg_support_generation","arg_support_alignment"}:
        return _depth_support(sid, maps, base)
    if sid in {"support_quality","evidence_integration"}:
        return _depth_support(sid, maps, base)
    if sid == "arg_conclusion_alignment":
        return _depth_conclusion(maps, base)
    if sid in {"avoid_overgeneralization","qualification_and_nuance"}:
        return _depth_overgeneralization(maps, base)
    if sid in {"causal_reasoning"}:
        return _depth_causal(maps, base)
    if sid in {"reasoning_depth"}:
        return _depth_reasoning_depth(maps, base)
    if sid in {"claim_construction"}:
        return _depth_claim("arg_claim_generation", maps, base)
    if "reason" in sid:
        return _depth_reason(sid, maps, base)
    if "counterargument" in sid or "rebuttal" in sid:
        return _depth_rebuttal(maps, base, is_ad)
    # All other skills
    return _depth_generic(sid, maps, base)

def depth_to_status(depth: int, skill_id: str, bucket: str, is_ad: bool) -> str:
    """Convert depth + context to WKE status."""
    sid = skill_id.lower()
    if depth == DEPTH_0:
        # Check if slot was filled (student attempted the move but didn't execute)
        is_arg_or_reasoning = any(
            sid.startswith(p) for p in ("arg_","counterargument","rebuttal","evaluation_of","warrant","causal","reasoning")
        )
        if is_ad and sid in {s.lower() for s in AD_BLOCKED_SKILLS}:
            return "observed_slot_only"  # A/D pattern: slot is present but skill not demonstrated
        if is_arg_or_reasoning:
            return "not_observed"  # no attempt at all
        return "not_observed"
    if depth == DEPTH_1:
        return "observed_low_evidence"
    # DEPTH_2 or DEPTH_3
    return "observed"

# ═══════════════════════════════════════════════════════════════════════════════
# DIMENSION MEASUREMENT  (v7.2: depth-gated)
# ═══════════════════════════════════════════════════════════════════════════════

def dimension_template(skill: SkillRecord) -> List[str]:
    sid = skill.skill_id.lower(); name = skill.skill_name.lower()
    domain = skill.domain.lower(); metrics = " ".join(skill.metrics).lower()
    text = " ".join([sid, name, domain, metrics])
    if skill.bucket == "practice_exercise_required":
        return ["practice_evidence_presence","process_observability"]
    if "revision" in domain or sid.startswith("rev_") or "self" in domain:
        return ["revision_evidence_presence","process_observability"]
    # v7.3a: Grammar domain FIRST — avoids keyword substring conflicts.
    # "preposition_control" contains "position" (→ claim), "comparison_structure_control"
    # contains "structure" (→ organization). Domain check is unambiguous.
    if domain in GRAMMAR_DOMAINS:
        return ["structure_presence","variety","complexity","control_proxy","detector_support"]
    if any(k in text for k in ["claim","thesis","position"]):
        return ["presence","specificity","precision","relevance","qualification"]
    if any(k in text for k in ["support","evidence","example"]):
        return ["presence","relevance","specificity","integration","elaboration"]
    if any(k in text for k in ["reason","causal","consequence","warrant","rebuttal","counterargument","tradeoff","alternative","overgeneralization","nuance"]):
        return ["presence","logical_linking","depth","validity_proxy","nuance"]
    if any(k in text for k in ["organization","paragraph","introduction","conclusion","structure","hierarchy"]):
        return ["component_presence","sequence_control","focus","balance","closure"]
    if any(k in text for k in ["cohesion","transition","reference","flow","progression","sequencing"]):
        return ["connector_presence","transition_variety","reference_clarity_proxy","progression","economy"]
    if any(k in text for k in ["lexical","collocation","semantic","vocabulary","register","spelling","word","phrase","nominalization"]):
        return ["unit_presence","variety","specificity","naturalness_proxy","register_fit_proxy"]
    if any(k in text for k in ["grammar","sentence","article","agreement","tense","preposition","clause","punctuation","morphology"]):
        return ["structure_presence","variety","complexity","control_proxy","detector_support"]
    if any(k in text for k in ["task","genre","purpose","audience","component","focus"]):
        return ["task_alignment","component_coverage","focus","genre_fit","prompt_response"]
    if any(k in text for k in ["style","reader","tone","formality","clarity","conciseness","hedging"]):
        return ["clarity_proxy","reader_effort_proxy","formality_proxy","conciseness_proxy","tone_control"]
    return ["presence","coverage","control","complexity"]

def _is_grammar_skill(skill: SkillRecord) -> bool:
    text = " ".join([skill.skill_id.lower(), skill.skill_name.lower(), skill.domain.lower(), " ".join(skill.metrics).lower()])
    return skill.domain.lower() in GRAMMAR_DOMAINS or any(k in text for k in GRAMMAR_KEYWORDS)

def _compute_vector(skill: SkillRecord, dims: List[str], maps: Dict, lex_units: List[Dict],
                    base: Dict, detector: Dict, depth: int) -> Tuple[Dict[str,Optional[float]], List[str], str]:
    """
    Compute raw dimension vector for a skill. Dimension VALUES reflect what was measured.
    The STATUS is determined separately by depth_to_status().
    For DEPTH_0 / DEPTH_1, cap key dimensions to reflect limited evidence.
    """
    vector: Dict[str,Optional[float]] = {d: None for d in dims}
    evidence_ids: List[str] = []
    notes_list: List[str] = []

    arg = maps.get("argument_map",{}); cohesion = maps.get("cohesion_map",{})
    sent_map = maps.get("sentence_map",[])
    all_low = _full_text(sent_map)

    claim_evs     = [x["evidence_id"] for x in arg.get("claim_candidates",[])]
    reason_evs    = [x["evidence_id"] for x in arg.get("reason_candidates",[])]
    support_evs   = [x["evidence_id"] for x in arg.get("support_candidates",[])]
    example_evs   = [x["evidence_id"] for x in arg.get("example_candidates",[])]
    conclusion_evs= [x["evidence_id"] for x in arg.get("conclusion_candidates",[])]
    transition_evs= [s["evidence_id"] for s in sent_map
                     if any(t["marker"] in s["text"].lower() for t in cohesion.get("transition_markers",[]))]
    lex_evs       = list({e for u in lex_units[:50] for e in u.get("evidence_ids",[]) if e})

    sc = max(1, base["sentence_count"]); pc = max(1, base["paragraph_count"])
    cc = base["claim_candidate_count"];  rc = base["reason_candidate_count"]
    supc = base["support_candidate_count"]; exc = base["example_candidate_count"]
    tc = base["transition_marker_count"]; tv = base["transition_marker_variety"]
    rep = base["repeated_content_word_count"]; div = base["content_word_diversity"]
    asl = base["avg_sentence_length"]
    det_avail = detector.get("available", False)

    def s(name, val):
        if name in vector: vector[name] = clamp(val)

    sid = skill.skill_id.lower(); text = " ".join([sid, skill.skill_name.lower(), skill.domain.lower()])

    # ── grammar (DOMAIN-FIRST — MUST be the first branch) ────────────────────
    # Keyword-based matching causes conflicts:
    #   "comparison_structure_control" → "structure" fires organization branch
    #   "preposition_control"          → "position"  fires claim branch
    # Domain check is unambiguous: only Grammar Production skills hit this.
    if skill.domain.lower() in GRAMMAR_DOMAINS:
        gf          = base.get("grammar_features", {})
        sl_cv       = gf.get("sentence_length_cv", 0.0)
        opener_var  = gf.get("sentence_opener_variety", 0.0)
        dbl_comp    = gf.get("double_comparative_count", 0)
        fragments   = gf.get("fragment_count", 0)
        run_ons     = gf.get("run_on_count", 0)
        informal_ct = gf.get("informal_marker_count", 0)

        evidence_ids = [sx["evidence_id"] for sx in maps.get("sentence_map",[])[:5] if sx.get("evidence_id")]
        s("structure_presence", clamp(0.40 + 0.10*min(3, sc/5) + 0.15*opener_var))
        s("variety",            clamp(0.25 + 0.50*min(1.0, sl_cv*2.0) + (0.10 if sc >= 6 else 0.0)))
        s("complexity",         clamp(0.30 + 0.20*min(1.0, asl/20.0) - 0.08*fragments))
        control_base = 0.75 - 0.10*min(3, dbl_comp) - 0.06*min(3, run_ons) - 0.05*min(3, fragments) - 0.04*min(3, informal_ct)
        s("control_proxy",      clamp(control_base))
        s("detector_support",   1.0 if det_avail else 0.0)
        penalty_log = apply_detector_grammar_penalties(vector, detector)
        if penalty_log:
            notes_list.append(f"Detector grammar penalties applied: {penalty_log}.")
        notes_list.append(
            f"Grammar domain-first routing: double_comparatives={dbl_comp}, run_ons={run_ons}, "
            f"fragments={fragments}, sent_length_cv={sl_cv:.2f}, opener_variety={opener_var:.2f}."
        )

    # ── claim / thesis / position ─────────────────────────────────────────────
    elif any(k in text for k in ["claim","thesis","position"]):
        evidence_ids = claim_evs[:6] or conclusion_evs[:3]
        specific_claims = [c for c in arg.get("claim_candidates",[]) if is_specific_position(c["text"])]
        spec_ratio = len(specific_claims) / max(1, cc)
        s("presence",     min(1.0, cc / max(1, pc)))
        s("specificity",  clamp(0.20 + 0.40*spec_ratio + 0.15*min(1,div)))
        s("precision",    clamp(0.20 + 0.45*spec_ratio + 0.10*min(1,div) - 0.015*rep))
        s("relevance",    0.80 if claim_evs else 0.30)
        s("qualification",0.25 + (0.30 if _QUAL_RE.search(all_low) else 0.0))
        # Skill-specific overrides for distinctiveness
        if "specificity" in sid:
            s("specificity", clamp(0.20 + 0.50*spec_ratio))
        elif "precision" in sid:
            s("precision", clamp(0.20 + 0.50*spec_ratio - 0.015*rep))
        elif "relevance" in sid:
            s("relevance", 0.80 if claim_evs else 0.30)
        elif "consistency" in sid:
            s("presence", 0.70 if len(specific_claims) >= 2 else 0.40)
        notes_list.append("Claim dimensions use specific-position ratio, diversity, and repetition as proxies.")

    # ── support / evidence / example ─────────────────────────────────────────
    elif any(k in text for k in ["support","evidence","example"]):
        if "example" in text and exc == 0:
            return vector, [], "none"
        evidence_ids = (support_evs + example_evs + reason_evs)[:8]
        s("presence",     safe_ratio(supc+exc, max(1,cc), 0.0))
        s("relevance",    0.65 if supc else 0.25)
        s("specificity",  clamp(0.25 + 0.08*exc + 0.04*supc - 0.01*rep))
        s("integration",  clamp(0.30 + 0.08*len(reason_evs) + 0.10*exc))
        s("elaboration",  clamp(0.20 + 0.07*rc + 0.04*supc))
        notes_list.append("Support dimensions are product observations.")

    # ── reason / causal / warrant / rebuttal / counterarg ────────────────────
    elif any(k in text for k in ["reason","causal","consequence","warrant","rebuttal","counterargument","tradeoff","alternative","overgeneralization","nuance","depth"]):
        evidence_ids = (reason_evs + support_evs + claim_evs)[:8]
        nt = _count_non_trivial_reasons(sent_map)
        qual_hits = len(_QUAL_RE.findall(all_low))
        s("presence",       min(1.0, rc / max(1, cc)))
        s("logical_linking",clamp(0.20 + 0.10*nt + 0.04*tv))
        s("depth",          clamp(0.15 + 0.08*supc + 0.07*nt - 0.012*rep))
        s("validity_proxy", clamp(0.40 + 0.07*nt - 0.008*rep))
        s("nuance",         clamp(0.20 + 0.12*qual_hits + 0.05*tv))
        # For warrant/rebuttal/counterarg at depth 0: cap depth dimension
        if sid in {"arg_warrant_generation","arg_warrant_quality",
                   "arg_rebuttal_generation","arg_rebuttal_quality",
                   "arg_counterargument_development","arg_counterargument_generation",
                   "counterargument_development","rebuttal_quality","evaluation_of_alternatives"}:
            if depth == DEPTH_0:
                s("presence",       0.15)
                s("logical_linking",0.25)
                s("depth",          0.10)
                s("validity_proxy", 0.25)
                s("nuance",         0.20)
        notes_list.append("Reasoning dimensions use causal chain count and qualification frequency.")

    # ── organization / structure ──────────────────────────────────────────────
    elif any(k in text for k in ["organization","paragraph","introduction","conclusion","structure","hierarchy","ws_"]):
        evidence_ids = [p["evidence_id"] for p in maps.get("paragraph_map",[])][:8]
        s("component_presence",clamp((1 if pc >= 3 else 0.5)*(1 if base["conclusion_candidate_count"] else 0.75)))
        s("sequence_control",  clamp(0.40 + 0.10*min(3,tv) + (0.10 if pc >= 4 else 0)))
        s("focus",             clamp(0.50 + 0.05*cc - 0.01*rep))
        s("balance",           clamp(1.0 - min(0.5, abs(pc-4)*0.10)))
        s("closure",           0.80 if base["conclusion_candidate_count"] else 0.25)
        notes_list.append("Organization dimensions from paragraph map and structural component signals.")

    # ── cohesion ──────────────────────────────────────────────────────────────
    elif any(k in text for k in ["cohesion","transition","reference","flow","progression","sequencing"]):
        evidence_ids = transition_evs[:8] or [p["evidence_id"] for p in maps.get("paragraph_map",[])[:4]]
        s("connector_presence",      min(1.0, tc/max(1,pc)))
        s("transition_variety",      min(1.0, tv/6.0))
        s("reference_clarity_proxy", clamp(0.65 - min(0.20, rep*0.01)))
        s("progression",             clamp(0.40 + 0.06*cc + 0.04*rc))
        s("economy",                 clamp(0.75 - max(0.0, tc-sc*0.45)*0.05))
        notes_list.append("Cohesion dimensions are discourse marker and repetition proxies.")

    # ── lexical ───────────────────────────────────────────────────────────────
    elif any(k in text for k in ["lexical","collocation","semantic","vocabulary","register","spelling","word","phrase","nominalization"]):
        evidence_ids = list(dict.fromkeys(lex_evs))[:8]
        topic_units = [u for u in lex_units if "topic_vocabulary" in u.get("axis_candidates",[])]
        natural_units = [u for u in lex_units if any(a in u.get("axis_candidates",[]) for a in ["predicate_argument","semantic_compatibility"])]
        informal_hit = "let's" in all_low or "a lot" in all_low
        s("unit_presence",      min(1.0, len(lex_units)/max(1,sc*4)))
        s("variety",            clamp(div))
        s("specificity",        clamp(0.25 + 0.35*(len(topic_units)/max(1,len(lex_units))) + 0.15*div - 0.012*rep))
        s("naturalness_proxy",  clamp(0.45 + 0.20*(len(natural_units)/max(1,sc*2)) - 0.010*rep))
        s("register_fit_proxy", clamp(0.60 - (0.15 if informal_hit else 0) + 0.08*(len(topic_units)/max(1,80))))
        notes_list.append("Lexical dimensions are extraction-based proxies; LRET classifies units.")

    # ── task understanding ────────────────────────────────────────────────────
    elif any(k in text for k in ["task","genre","purpose","audience","component","focus","prompt"]):
        evidence_ids = claim_evs[:4] + conclusion_evs[:2]
        s("task_alignment",      0.70 if cc else 0.35)
        s("component_coverage",  clamp(0.25 + 0.15*min(4,pc) + (0.15 if base["conclusion_candidate_count"] else 0)))
        s("focus",               clamp(0.50 + 0.04*cc - 0.01*rep))
        s("genre_fit",           0.70 if pc >= 3 else 0.45)
        s("prompt_response",     0.70 if cc and supc else 0.45)
        notes_list.append("Task dimensions from prompt-response and component coverage proxies.")

    # ── style / reader impact (v7.3a: grammar_features provide hedging, nominalisation, passive) ──
    elif any(k in text for k in ["style","reader","tone","formality","clarity","conciseness","hedging"]):
        gf          = base.get("grammar_features", {})
        hedge_den   = gf.get("hedge_density", 0.0)
        nomin_den   = gf.get("nominalisation_density", 0.0)
        passive_den = gf.get("passive_density", 0.0)
        opener_var  = gf.get("sentence_opener_variety", 0.0)
        informal_ct = gf.get("informal_marker_count", 0)

        evidence_ids = [sx["evidence_id"] for sx in sent_map if sx["word_count"] > 22][:5] or [sx["evidence_id"] for sx in sent_map[:5]]
        informal_hit = "let's" in all_low
        # clarity: short ASL + opener variety + less repetition → clearer
        s("clarity_proxy",      clamp(0.75 - max(0, asl-22)*0.015 - min(0.15, rep*0.008) + min(0.10, opener_var*0.12)))
        s("reader_effort_proxy",clamp(0.35 + max(0, asl-12)*0.015 + min(0.25, rep*0.01)))
        # formality: academic hedging + nominalisation boost it; informal markers reduce it
        s("formality_proxy",    clamp(0.55 + min(0.15, hedge_den*0.80) + min(0.10, nomin_den*0.50)
                                      - 0.12*min(1, informal_ct) - (0.10 if informal_hit else 0.0)))
        s("conciseness_proxy",  clamp(0.72 - max(0, asl-20)*0.012))
        # tone_control: hedging density + transition variety + passive use signal academic register
        s("tone_control",       clamp(0.55 + min(0.15, hedge_den*0.80) + min(0.15, tv*0.025) + min(0.05, passive_den*0.20)))
        notes_list.append(
            f"Style dimensions (v7.3a): hedge_density={hedge_den:.2f}, "
            f"nominalisation_density={nomin_den:.2f}, passive_density={passive_den:.2f}, "
            f"opener_variety={opener_var:.2f}, informal_markers={informal_ct}."
        )

    else:
        evidence_ids = claim_evs[:3] + support_evs[:3]
        s("presence",    1.0 if evidence_ids else 0.0)
        s("coverage",    safe_ratio(len(evidence_ids), max(1,pc), 0.0))
        s("control",     0.55 if evidence_ids else None)
        s("complexity",  0.45 if evidence_ids else None)

    # ── Depth-based capping: DEPTH_1 → cap key performance dims at 0.55 ──────
    if depth == DEPTH_1:
        for key in ["depth","validity_proxy","specificity","precision","naturalness_proxy"]:
            if key in vector and isinstance(vector[key], float):
                vector[key] = min(vector[key], 0.55)
    if depth == DEPTH_0:
        for key in dims:
            if key in vector and isinstance(vector[key], float):
                vector[key] = min(vector[key], 0.30)

    note_str = " ".join(notes_list) or "Dimension template applied."
    return vector, list(dict.fromkeys(evidence_ids))[:10], note_str


def measure_dimensions(
    skill: SkillRecord,
    maps: Dict, lex_units: List[Dict], base: Dict,
    detector: Dict, scorer: Dict,
    depth: int, is_ad: bool,
) -> Tuple[str, Dict[str,Optional[float]], List[str], str, float, str]:
    """
    v7.3a entry point: applies all gates then calls _compute_vector.
    Returns (status, vector, evidence_ids, strength, confidence, note).
    Gate 3 (grammar) now uses grammar_features for essay-only mode — all grammar skills
    get a surface-analysis vector rather than being blocked.
    """
    dims = dimension_template(skill)
    null_vector: Dict[str,Optional[float]] = {d: None for d in dims}

    # Gate 1: practice / revision process skills
    if skill.bucket == "practice_exercise_required":
        status = "requires_revision_evidence" if ("revision" in skill.domain.lower() or skill.skill_id.startswith("rev_")) else "requires_practice_evidence"
        return status, null_vector, [], "none", 0.0, "Final essay cannot demonstrate this process skill."

    # Gate 2: Task-type (Information Processing = Task 1 only)
    if skill.domain.lower() == "information processing":
        return "not_applicable_to_task_type", null_vector, [], "none", 0.0, (
            "Information-processing skills require Task 1/chart input; not applicable to Task 2 essay."
        )

    # Gate 3: Grammar — v7.3a: essay-only mode now uses grammar_features for all grammar skills.
    # Previously: SENTENCE_STRUCTURE_OBSERVABLE → hardcoded partial; all others → blocked.
    # v7.3a: ALL grammar skills get a meaningful surface-analysis vector even without detector.
    # control_proxy is estimated from rule-based proxies (double-comparatives, run-ons, fragments).
    # This is still labelled observed_limited_without_detector (accuracy is inferred, not confirmed).
    if _is_grammar_skill(skill) and not detector.get("available"):
        gf         = base.get("grammar_features", {})
        asl_val    = base.get("avg_sentence_length", 0)
        sc_val     = base.get("sentence_count", 0)
        sl_cv      = gf.get("sentence_length_cv", 0.0)
        opener_var = gf.get("sentence_opener_variety", 0.0)
        dbl_comp   = gf.get("double_comparative_count", 0)
        fragments  = gf.get("fragment_count", 0)
        run_ons    = gf.get("run_on_count", 0)
        informal_ct= gf.get("informal_marker_count", 0)

        partial = dict(null_vector)
        if "structure_presence" in partial:
            partial["structure_presence"] = round(clamp(0.40 + 0.10*min(3, sc_val/5) + 0.15*opener_var) or 0.0, 3)
        if "variety" in partial:
            partial["variety"] = round(clamp(0.25 + 0.50*min(1.0, sl_cv*2.0) + (0.10 if sc_val >= 6 else 0.0)) or 0.0, 3)
        if "complexity" in partial:
            partial["complexity"] = round(clamp(0.30 + 0.20*min(1.0, asl_val/20.0) - 0.08*fragments) or 0.0, 3)
        if "control_proxy" in partial:
            # Surface accuracy proxy — double-comparatives, run-ons, fragments each penalise accuracy
            ctrl = 0.70 - 0.10*min(3, dbl_comp) - 0.06*min(3, run_ons) - 0.05*min(3, fragments) - 0.04*min(3, informal_ct)
            partial["control_proxy"] = round(clamp(ctrl) or 0.0, 3)
        if "detector_support" in partial:
            partial["detector_support"] = 0.0

        evs = [sx["evidence_id"] for sx in maps.get("sentence_map",[])[:5] if sx.get("evidence_id")]
        note = (
            f"Grammar independent surface analysis (no detector): "
            f"structure_presence from opener_variety={opener_var:.2f} and sentence_count={sc_val}; "
            f"variety from sent_length_cv={sl_cv:.2f}; complexity from asl={asl_val:.1f}; "
            f"control_proxy estimated: double_comparatives={dbl_comp}, run_ons={run_ons}, fragments={fragments}. "
            f"Grammar accuracy confirmation requires Detector."
        )
        return ("observed_limited_without_detector", partial, evs, "low", 0.38, note)

    # Depth → status (ceiling determined by rule engine)
    status = depth_to_status(depth, skill.skill_id, skill.bucket, is_ad)

    if status in {"not_observed","not_applicable_to_task_type","requires_practice_evidence","requires_revision_evidence"}:
        return status, null_vector, [], "none", 0.0, "Skill not demonstrated in this essay."

    # Compute vector
    vector, evidence_ids, note = _compute_vector(skill, dims, maps, lex_units, base, detector, depth)

    if status == "observed_slot_only":
        return status, vector, evidence_ids, "low", 0.30, (
            "Structural slot occupied (e.g., contrast marker present) but skill function not executed. "
            "Student attempted the move; execution is absent."
        )

    # Confidence
    base_conf = 0.42
    base_conf += min(0.22, len(set(evidence_ids)) * 0.04)
    if detector.get("available"): base_conf += 0.10
    if skill.bucket == "single_essay_observable": base_conf += 0.08
    elif skill.bucket == "hybrid_single_essay_plus_multi_essay_tracking": base_conf += 0.02
    if depth >= DEPTH_2: base_conf += 0.06
    confidence = round(clamp(base_conf) or 0.0, 3)

    # Downgrade status from DEPTH_1 observation if confidence very low
    if status == "observed" and confidence < 0.55:
        status = "observed_low_evidence"

    strength = evidence_strength_from_count(len(set(evidence_ids)), confidence)
    return status, vector, evidence_ids, strength, confidence, note

# ═══════════════════════════════════════════════════════════════════════════════
# BUILD SKILL OBSERVATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _dimension_note(dim: str) -> str:
    NOTES = {
        "presence":             "Whether the operation appears in the essay product.",
        "specificity":          "How concrete and narrowed the observed operation appears.",
        "precision":            "Operational precision proxy; not an IELTS score.",
        "qualification":        "Whether the writing limits or qualifies claims.",
        "elaboration":          "Whether ideas are developed beyond assertion.",
        "depth":                "Multi-step reasoning/development proxy.",
        "validity_proxy":       "Logical soundness proxy inferred from reasoning chains.",
        "naturalness_proxy":    "Extraction-based proxy; LRET must classify directly.",
        "detector_support":     "Whether Detector input was available for this dimension.",
        "reader_effort_proxy":  "Higher means more reader effort; lower is better.",
    }
    return NOTES.get(dim, "Operational measurement in [0,1]; not an IELTS score.")

def build_skill_observations(
    skills: Dict[str, SkillRecord], maps: Dict, lex_units: List[Dict],
    base: Dict, detector: Dict, scorer: Dict, is_ad: bool,
) -> Tuple[List[Dict], Dict[str,int]]:
    """Returns (observations list, depth_cache {skill_id: depth})."""
    observations: List[Dict] = []
    depth_cache: Dict[str,int] = {}

    for sid in sorted(skills.keys()):
        skill = skills[sid]
        # Compute depth for non-gated skills
        if (skill.bucket != "practice_exercise_required"
                and skill.domain.lower() != "information processing"
                and not (_is_grammar_skill(skill) and not detector.get("available"))):
            d = estimate_skill_depth(sid, maps, base, is_ad)
        else:
            d = DEPTH_0
        depth_cache[sid] = d

        status, vector, evidence_ids, strength, conf, notes = measure_dimensions(
            skill, maps, lex_units, base, detector, scorer, d, is_ad
        )
        sig = signal_from_vector(vector, status, skill.bucket)
        cap = capacity_state(vector, status, skill.bucket)

        observations.append({
            "skill_id":              skill.skill_id,
            "skill_name":            skill.skill_name,
            "domain":                skill.domain,
            "evaluation_bucket":     skill.bucket,
            "status":                status,
            "evidence_depth":        d,
            "skill_signal":          sig,
            "capacity_signal":       cap,
            "competence_vector":     vector,
            "dimension_notes":       {k: _dimension_note(k) for k in vector.keys()},
            "evidence_ids":          evidence_ids,
            "evidence_strength":     strength,
            "diagnostic_confidence": conf,
            "dependencies":          skill.dependencies,
            "metrics_from_registry": skill.metrics,
            "consumers":             skill.consumers or skill.recommended_owners,
            "required_inputs":       skill.required_inputs,
            "diagnostic_note":       notes,
            "source":                "ontology_driven_depth_wke_v7_3a",
        })

    return observations, depth_cache

# ═══════════════════════════════════════════════════════════════════════════════
# GAP SIGNAL COMPUTATION  (NEW in v7.2)
# ═══════════════════════════════════════════════════════════════════════════════

OBSERVABLE_BUCKETS = {"single_essay_observable", "hybrid_single_essay_plus_multi_essay_tracking"}

def compute_gap_signals(observations: List[Dict]) -> List[Dict]:
    """
    Returns gap signals from two conditions:
    A. Condition A: single-essay-observable skill is not_observed.
    B. Condition B: observed skill with weak dimensions (majority < 0.5).
    """
    gaps: List[Dict] = []
    for o in observations:
        bucket = o.get("evaluation_bucket","")
        status = o.get("status","")
        if bucket not in OBSERVABLE_BUCKETS: continue

        if status == "not_observed":
            gaps.append({
                "skill_id":    o["skill_id"], "skill_name": o["skill_name"],
                "domain":      o["domain"],   "gap_type":   "absence",
                "gap_note":    "Skill is observable from essay but was not demonstrated.",
                "priority_signal": o.get("priority_index", 0.0),
            })
        elif status in {"observed","observed_low_evidence"} and is_weak_observation(o):
            cv = o.get("competence_vector",{})
            weak_dims = [k for k,v in cv.items() if isinstance(v,float) and v < 0.5]
            gaps.append({
                "skill_id":    o["skill_id"], "skill_name": o["skill_name"],
                "domain":      o["domain"],   "gap_type":   "quality",
                "gap_note":    f"Skill attempted but weak execution: {', '.join(weak_dims[:3])}.",
                "priority_signal": o.get("priority_index", 0.0),
            })
        elif status == "observed_slot_only":
            gaps.append({
                "skill_id":    o["skill_id"], "skill_name": o["skill_name"],
                "domain":      o["domain"],   "gap_type":   "incomplete_execution",
                "gap_note":    "Student attempted this move but the structural execution is absent.",
                "priority_signal": o.get("priority_index", 0.0),
            })

    return sorted(gaps, key=lambda g: -g.get("priority_signal",0))

# ═══════════════════════════════════════════════════════════════════════════════
# LLM REFINEMENT  (redesigned for v7.2)
# ═══════════════════════════════════════════════════════════════════════════════

LLM_SYSTEM_V72 = """You are a writing skill depth analyst inside VA Premium Evaluator v7.2.

Your job: for each listed skill, independently assess how deeply the essay demonstrates it.

CRITICAL RULES:
1. Assess EACH SKILL INDEPENDENTLY. Never copy dimension_adjustments from one skill to another.
2. Skills in the same family (e.g. claim_generation / claim_precision / claim_specificity) measure
   DIFFERENT things and must receive DIFFERENT dimension values.
3. For each skill, first determine depth (DEPTH_0/1/2/3). Then recommend a status.
4. You may only LOWER status from the pre_rule_status — never raise it.
5. Do NOT output IELTS scores, bands, or LRET labels.
6. For skills flagged as observed_slot_only or not_observed, confirm or set to those values.

DEPTH SCALE:
  DEPTH_0: No demonstration. Cue word / structural slot present but skill not executed.
  DEPTH_1: Partial/generic demonstration. Skill attempted but incomplete or formulaic.
  DEPTH_2: Full demonstration. At least one clear, structurally complete execution.
  DEPTH_3: Multiple clear instances across essay.

STATUS from depth:
  DEPTH_0 (with slot) → observed_slot_only
  DEPTH_0 (no attempt)→ not_observed
  DEPTH_1             → observed_low_evidence
  DEPTH_2 / DEPTH_3   → observed

Return JSON only with this schema:
{
  "skill_refinements": [
    {
      "skill_id": "...",
      "depth_assessment": "DEPTH_0|DEPTH_1|DEPTH_2|DEPTH_3",
      "depth_note": "One sentence explaining why this depth was assigned.",
      "status_recommendation": "observed|observed_low_evidence|observed_slot_only|not_observed",
      "dimension_adjustments": {"dim_name": 0.0},
      "confidence_delta": 0.0
    }
  ]
}
Do not include skills you cannot assess. dimension_adjustments may be empty if depth is DEPTH_0."""

def _select_skills_for_llm(
    observations: List[Dict], depth_cache: Dict[str,int], max_skills: int
) -> List[Dict]:
    """Select skills that reached DEPTH_1+ for LLM assessment, prioritised by depth/domain."""
    eligible = [
        o for o in observations
        if o.get("status") in {"observed","observed_low_evidence","observed_slot_only"}
        and o.get("domain") in {
            "Argumentation","Reasoning & Critical Thinking","Content Development",
            "Cohesion","Organization","Style & Reader Impact","Advanced Lexical Competence",
            "Task Understanding","Thinking Competence",
        }
    ]
    # Always include AD-blocked skills so LLM can confirm depth_0
    ad_blocked = [
        o for o in observations
        if o["skill_id"] in AD_BLOCKED_SKILLS
        and o.get("status") not in {"requires_practice_evidence","not_applicable_to_task_type",
                                     "detector_required_for_reliable_grammar"}
    ]
    selected = list({o["skill_id"]: o for o in (ad_blocked + eligible)}.values())
    return selected[:max_skills]

def _build_llm_payload(
    selected: List[Dict], essay_text: str, evidence_index: Dict, depth_cache: Dict[str,int]
) -> Dict[str, Any]:
    skill_items = []
    for o in selected:
        sid = o["skill_id"]
        defn = SKILL_DEFINITIONS.get(sid, {})
        evs = {k: evidence_index[k] for k in o.get("evidence_ids",[])[:4] if k in evidence_index}
        skill_items.append({
            "skill_id":          sid,
            "skill_name":        o["skill_name"],
            "domain":            o["domain"],
            "pre_rule_depth":    depth_cache.get(sid, 0),
            "pre_rule_status":   o["status"],
            "definition":        defn.get("definition",""),
            "depth_2_requires":  defn.get("depth_2_requires",""),
            "depth_0_example":   defn.get("depth_0_example",""),
            "current_vector":    {k:v for k,v in o.get("competence_vector",{}).items() if v is not None},
            "candidate_evidence": list(evs.values()),
        })
    return {
        "instruction": (
            "Assess each skill independently using the essay text and candidate evidence. "
            "Return depth_assessment and status_recommendation for each. "
            "For skills with pre_rule_status=observed_slot_only, verify whether this is correct "
            "or whether the essay actually reaches DEPTH_1 or DEPTH_2."
        ),
        "essay_text": essay_text[:3500],
        "skills": skill_items,
    }

def call_llm_refinement(
    req: "EvaluatorRequest", observations: List[Dict],
    maps: Dict, depth_cache: Dict[str,int],
) -> Dict[str, Any]:
    if not req.use_llm: return {"enabled": False}
    if OpenAI is None: return {"enabled": False, "error": "openai package not installed"}
    if not os.getenv("OPENAI_API_KEY"): return {"enabled": False, "error": "OPENAI_API_KEY not set"}

    selected = _select_skills_for_llm(observations, depth_cache, req.max_llm_skills)
    if not selected: return {"enabled": True, "skill_count": 0, "raw_refinement": {}}

    payload = _build_llm_payload(selected, req.essay_text, maps.get("evidence_index",{}), depth_cache)

    client = OpenAI()
    t0 = time.time()
    resp = client.chat.completions.create(
        model=req.model or DEFAULT_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": LLM_SYSTEM_V72},
            {"role": "user",   "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    return {
        "enabled":          True,
        "model":            req.model or DEFAULT_MODEL,
        "latency_seconds":  round(time.time()-t0, 3),
        "skill_count":      len(selected),
        "usage":            resp.usage.model_dump() if getattr(resp,"usage",None) else None,
        "raw_refinement":   data,
    }

def apply_llm_refinement(observations: List[Dict], refinement: Dict) -> None:
    """
    Apply LLM depth_assessment results.
    LLM may only LOWER status — never raise above pre_rule_status.
    """
    if not refinement.get("enabled"): return
    by_id = {o["skill_id"]: o for o in observations}
    STATUS_ORDER = [
        "observed","observed_low_evidence","observed_slot_only","not_observed",
        "requires_practice_evidence","requires_revision_evidence","not_applicable_to_task_type",
        "detector_required_for_reliable_grammar",
    ]

    for r in (refinement.get("raw_refinement") or {}).get("skill_refinements", []):
        sid = r.get("skill_id")
        if sid not in by_id: continue
        o = by_id[sid]

        # Update evidence_depth from LLM depth_assessment
        depth_str = r.get("depth_assessment","")
        depth_map = {"DEPTH_0":0,"DEPTH_1":1,"DEPTH_2":2,"DEPTH_3":3}
        if depth_str in depth_map:
            llm_depth = depth_map[depth_str]
            if llm_depth < o.get("evidence_depth", 0):
                o["evidence_depth"] = llm_depth
                o["llm_depth_note"] = r.get("depth_note","")

        # Status override — LLM can only move status LOWER
        llm_status = r.get("status_recommendation","")
        current_status = o.get("status","not_observed")
        if llm_status and llm_status in STATUS_ORDER and current_status in STATUS_ORDER:
            current_rank = STATUS_ORDER.index(current_status)
            llm_rank = STATUS_ORDER.index(llm_status)
            if llm_rank > current_rank:   # LLM recommends lower status
                o["status"] = llm_status
                o["skill_signal"] = signal_from_vector(o["competence_vector"], llm_status, o["evaluation_bucket"])
                o["capacity_signal"] = capacity_state(o["competence_vector"], llm_status, o["evaluation_bucket"])
                # Re-cap vector if depth dropped
                if llm_status in {"observed_slot_only","not_observed"}:
                    for k in o["competence_vector"]:
                        if isinstance(o["competence_vector"][k], float):
                            o["competence_vector"][k] = min(o["competence_vector"][k], 0.30)

        # Dimension adjustments (only if LLM depth >= rule depth; skip for DEPTH_0 downgrades)
        adjustments = r.get("dimension_adjustments") or {}
        if adjustments and o.get("status") in {"observed","observed_low_evidence"}:
            for dim, val in adjustments.items():
                if dim in o["competence_vector"] and val is not None:
                    # Also enforce depth cap
                    cap = 0.55 if o.get("evidence_depth",0) == DEPTH_1 else 1.0
                    o["competence_vector"][dim] = clamp(min(float(val), cap))

        if r.get("depth_note"): o["llm_depth_note"] = r["depth_note"]
        delta = r.get("confidence_delta")
        if delta is not None:
            o["diagnostic_confidence"] = round(clamp(o.get("diagnostic_confidence",0) + float(delta)) or 0.0, 3)
        o["source"] = "hybrid_depth_rule_plus_llm_wke_v7_3a"

# ═══════════════════════════════════════════════════════════════════════════════
# PRIORITY INDEX  (v7.2 domain-weighted formula)
# ═══════════════════════════════════════════════════════════════════════════════

def priority_index_v72(obs: Dict) -> float:
    status = obs.get("status","")
    # Only observable and observed/low_evidence skills rank for priority
    # observed_slot_only also gets priority (growth area signal)
    if status not in {"observed","observed_low_evidence","observed_slot_only","not_observed"}:
        return 0.0
    if obs.get("evaluation_bucket","") not in OBSERVABLE_BUCKETS:
        return 0.0

    cv   = obs.get("competence_vector",{})
    vals = [v for v in cv.values() if isinstance(v,(int,float))]
    avg  = sum(vals)/len(vals) if vals else 0.5
    weakness   = max(0.0, 1.0 - avg)

    # Absence gap > quality gap > slot gap
    if status == "not_observed":          gap_weight = 1.0
    elif status == "observed_slot_only":  gap_weight = 0.85
    elif status == "observed_low_evidence":gap_weight = 0.60 + 0.3*weakness
    else:                                  gap_weight = 0.40 + 0.4*weakness   # observed but weak

    domain_w = DOMAIN_PRIORITY.get(obs.get("domain",""), 0.40)
    confidence = obs.get("diagnostic_confidence") or 0.5
    consumers  = min(1.0, len(obs.get("consumers",[])) / 5.0) if obs.get("consumers") else 0.5
    bucket_w   = 0.9 if obs.get("evaluation_bucket") == "single_essay_observable" else 0.75

    return round(bucket_w * gap_weight * domain_w * confidence * consumers, 4)

# ═══════════════════════════════════════════════════════════════════════════════
# CONSUMER PAYLOADS  (v7.2: gap signals included)
# ═══════════════════════════════════════════════════════════════════════════════

def build_consumer_payloads(
    observations: List[Dict], lexical_units: List[Dict],
    maps: Dict, base: Dict,
    detector: Optional[Dict] = None,
) -> Dict[str, Any]:
    for o in observations:
        o["priority_index"] = priority_index_v72(o)

    gap_signals = compute_gap_signals(observations)
    priority    = sorted([o for o in observations if o.get("priority_index",0) > 0],
                         key=lambda x: -x["priority_index"])
    strengths   = [o for o in observations if o.get("skill_signal") == "current_strength"]
    targets     = [o for o in priority if o.get("skill_signal") in {"development_target","monitor"}]
    practice_needed = [o for o in observations if o.get("status") in {"requires_practice_evidence","requires_revision_evidence"}]
    history_needed  = [o for o in observations if o.get("capacity_signal") == "requires_history_for_stability"]
    slot_only       = [o for o in observations if o.get("status") == "observed_slot_only"]

    return {
        "writing_coach_payload": {
            "current_strength_signals":  _compact_obs(strengths[:8]),
            "development_target_signals":_compact_obs(targets[:10]),
            "gap_signals":               gap_signals[:15],
            "slot_only_signals":         _compact_obs(slot_only[:8]),
            "capacity_limits":           _compact_obs([o for o in observations if o.get("capacity_signal") in {"fragile","emerging"}][:10]),
            "note": (
                "Writing Coach should convert signals into learner-facing feedback. "
                "gap_signals includes both absent and weak skills (gap_type: absence/quality/incomplete_execution). "
                "slot_only_signals indicate moves attempted but not executed — high teaching value."
            ),
        },
        "lret_payload": {
            "lexical_units_for_lret": lexical_units,
            "fix_candidates": [
                {k: v for k, v in row.items()
                 if k in {"span_text","error_family","suggestion","start","end","paragraph_idx"}}
                for row in (detector.get("diagnostic_rows",[]) if detector and detector.get("available") else [])
                if row.get("error_family") in LRET_FIX_FAMILIES
            ],
            "note": "No KEEP/FIX/ENHANCE labels; LRET owns lexical classification.",
        },
        "practice_engine_payload": {
            "practice_relevant_targets":  _compact_obs([o for o in targets if "Practice Engine" in o.get("consumers",[]) or "practice_engine" in [str(c).lower() for c in o.get("consumers",[])]][:15]),
            "practice_evidence_required": _compact_obs(practice_needed[:20]),
            "gap_targets_for_practice":   [g for g in gap_signals if g.get("gap_type") == "absence"][:10],
            "note": "Practice Engine chooses exercise type. Evaluator supplies targets and prerequisites only.",
        },
        "essay_revision_payload": {
            "revision_observable_targets":            _compact_obs([o for o in targets if o.get("evaluation_bucket") == "single_essay_observable"][:12]),
            "slot_only_targets_for_revision":         _compact_obs(slot_only[:8]),
            "revision_process_skills_requiring_cycle":_compact_obs([o for o in practice_needed if "revision" in o.get("domain","").lower()][:12]),
            "note": "Revision Engine compares pre/post WKM vectors.",
        },
        "learning_intelligence_payload": {
            "trackable_current_essay_observations": _compact_obs([o for o in observations if o.get("status") in {"observed","observed_low_evidence"}][:80]),
            "history_required_for_stability":       _compact_obs(history_needed[:40]),
            "non_inferable_from_final_essay":       _compact_obs(practice_needed[:40]),
            "note": "LIE converts repeated observations into stable mastery and growth estimates.",
        },
        "progress_tracker_payload": {
            "dimension_snapshots": [
                {"skill_id":o["skill_id"],"domain":o["domain"],"bucket":o["evaluation_bucket"],
                 "vector":o["competence_vector"],"confidence":o["diagnostic_confidence"],
                 "status":o["status"],"evidence_depth":o.get("evidence_depth",0)}
                for o in observations if o.get("status") in {"observed","observed_low_evidence"}
            ],
            "baseline_features": base,
        },
    }

def _compact_obs(items: List[Dict]) -> List[Dict]:
    return [{
        "skill_id":           o.get("skill_id"),
        "skill_name":         o.get("skill_name"),
        "domain":             o.get("domain"),
        "status":             o.get("status"),
        "evidence_depth":     o.get("evidence_depth"),
        "skill_signal":       o.get("skill_signal"),
        "capacity_signal":    o.get("capacity_signal"),
        "priority_index":     o.get("priority_index"),
        "diagnostic_confidence": o.get("diagnostic_confidence"),
        "evidence_strength":  o.get("evidence_strength"),
        "competence_vector":  o.get("competence_vector"),
    } for o in items]

# ═══════════════════════════════════════════════════════════════════════════════
# QA VALIDATION  (v7.1 checks + v7.2 additions)
# ═══════════════════════════════════════════════════════════════════════════════

def validate_output(obj: Dict[str, Any], is_ad: bool = False) -> Dict[str, Any]:
    errors: List[str] = []; warnings: List[str] = []

    # v7 boundary check
    def walk_keys(x: Any, path: str = "") -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                if k in FORBIDDEN_OUTPUT_FIELDS:
                    errors.append(f"Forbidden field at {path}/{k}")
                walk_keys(v, path+"/"+str(k))
        elif isinstance(x, list):
            for i, v in enumerate(x): walk_keys(v, path+f"[{i}]")
    walk_keys(obj)

    for u in (((obj.get("consumer_payloads") or {}).get("lret_payload") or {}).get("lexical_units_for_lret") or []):
        if "label" in u or str(u.get("classification","")).upper() in LRET_FORBIDDEN_LABELS:
            errors.append("LRET classification label found in lexical payload."); break

    sop = obj.get("skill_observation_profile", [])
    obs_without_ev = [o["skill_id"] for o in sop if o.get("status") == "observed" and not o.get("evidence_ids")]
    if obs_without_ev:
        warnings.append(f"Observed skills without evidence_ids: {len(obs_without_ev)}")

    # v7.2 QA_V72_001: AD-blocked skills must not be `observed` when A/D pattern detected
    if is_ad:
        ad_observed = [o["skill_id"] for o in sop
                       if o["skill_id"] in AD_BLOCKED_SKILLS and o.get("status") == "observed"]
        if ad_observed:
            errors.append(f"QA_V72_001: AD-blocked skills marked observed with A/D pattern: {ad_observed}")

    # v7.2 QA_V72_002: No argumentation skill observed at DEPTH_0
    depth0_observed = [o["skill_id"] for o in sop
                       if o.get("evidence_depth",1) == 0 and o.get("status") == "observed"]
    if depth0_observed:
        errors.append(f"QA_V72_002: Skills observed at DEPTH_0: {depth0_observed}")

    # v7.2 QA_V72_003: claim_precision and claim_specificity must have different vectors
    prec = next((o for o in sop if o["skill_id"] == "arg_claim_precision"), None)
    spec = next((o for o in sop if o["skill_id"] == "arg_claim_specificity"), None)
    if prec and spec and prec.get("status") == "observed" and spec.get("status") == "observed":
        if prec.get("competence_vector") == spec.get("competence_vector"):
            warnings.append("QA_V72_003: arg_claim_precision and arg_claim_specificity have identical vectors.")

    # v7.2 QA_V72_005: writing coach must have at least 1 gap signal when observable skills are not_observed
    not_obs_observable = [o for o in sop
                          if o.get("status") == "not_observed"
                          and o.get("evaluation_bucket","") in OBSERVABLE_BUCKETS]
    wcp = (obj.get("consumer_payloads") or {}).get("writing_coach_payload", {})
    if not_obs_observable and not wcp.get("gap_signals"):
        warnings.append("QA_V72_005: Observable skills are not_observed but writing_coach gap_signals is empty.")

    # v7.2 QA_V72_006: observed_slot_only should appear for AD-blocked skills when A/D pattern
    if is_ad:
        ad_not_slot = [o["skill_id"] for o in sop
                       if o["skill_id"] in AD_BLOCKED_SKILLS
                       and o.get("status") not in {"observed_slot_only","not_observed",
                                                    "requires_practice_evidence","not_applicable_to_task_type"}]
        if ad_not_slot:
            warnings.append(f"QA_V72_006: AD-blocked skills not marked slot_only or not_observed: {ad_not_slot}")

    # v7.2 QA_V72_007: soft ceiling on observed count
    obs_count = sum(1 for o in sop if o.get("status") == "observed")
    if obs_count > 55:
        warnings.append(f"QA_V72_007: observed count {obs_count} exceeds soft ceiling of 55.")

    # v7.3a QA_V73A_001: grammar skills with detector errors should not appear as current_strength
    # (This guards against future regressions to the Bug Fix 2 + Bug Fix 3 pair.)
    grammar_strengths = [
        o["skill_id"] for o in sop
        if o.get("skill_signal") == "current_strength"
        and o.get("domain","").lower() in GRAMMAR_DOMAINS
    ]
    det_row_count = (obj.get("input_summary") or {}).get("detector_row_count", 0)
    if grammar_strengths and det_row_count > 3:
        warnings.append(
            f"QA_V73A_001: Grammar skill(s) marked current_strength despite "
            f"{det_row_count} detector rows: {grammar_strengths}. "
            "Verify control_proxy was penalised by apply_detector_grammar_penalties()."
        )

    # v7.3a QA_V73A_002: observed_low_evidence skills must not be current_strength
    low_ev_strength = [
        o["skill_id"] for o in sop
        if o.get("status") == "observed_low_evidence" and o.get("skill_signal") == "current_strength"
    ]
    if low_ev_strength:
        errors.append(
            f"QA_V73A_002: observed_low_evidence skills marked current_strength "
            f"(signal_from_vector depth-gate failed): {low_ev_strength}"
        )

    return {"status": "failed" if errors else "passed", "errors": errors, "warnings": warnings}

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN EVALUATOR
# ═══════════════════════════════════════════════════════════════════════════════

def evaluate(req: EvaluatorRequest) -> Dict[str, Any]:
    t0 = time.time()
    detector_raw = req.detector_output if req.detector_output is not None else load_json_path(req.detector_output_path)
    scorer_raw   = req.scorer_output   if req.scorer_output   is not None else load_json_path(req.scorer_output_path)
    detector = normalize_detector_output(detector_raw)
    scorer   = normalize_scorer_output(scorer_raw)

    skills, bucket_map, ontology_meta = load_ontology(req.ontology_dir)
    maps         = extract_text_maps(req.essay_text, req.prompt_text or "")
    lexical_units= extract_lexical_units(req.essay_text, maps)
    base         = baseline_features(req.essay_text, maps, lexical_units)

    # v7.2: detect A/D pattern before building observations
    is_ad = is_advantages_disadvantages_pattern(maps)

    observations, depth_cache = build_skill_observations(
        skills, maps, lexical_units, base, detector, scorer, is_ad
    )

    llm_meta = call_llm_refinement(req, observations, maps, depth_cache) if req.use_llm else {"enabled": False}
    apply_llm_refinement(observations, llm_meta)

    consumer_payloads = build_consumer_payloads(observations, lexical_units, maps, base, detector=detector)

    evidence_hash = hashlib.sha256(
        json.dumps({"essay": req.essay_text, "prompt": req.prompt_text, "skill_count": len(skills)},
                   ensure_ascii=False).encode()
    ).hexdigest()[:16]

    # Status distribution summary
    status_dist = dict(Counter(o["status"] for o in observations))
    depth_dist  = dict(Counter(o.get("evidence_depth",0) for o in observations))

    result = {
        "schema_version": SCHEMA_VERSION,
        "metadata": {
            "engine_id":              ENGINE_ID,
            "engine_version":         ENGINE_VERSION,
            "created_at":             now_iso(),
            "run_id":                 str(uuid.uuid4()),
            "student_id":             req.student_id,
            "essay_id":               req.essay_id,
            "submission_id":          req.submission_id,
            "batch_id":               req.batch_id,
            "prompt_id":              req.prompt_id,
            "input_mode":             (
                "detector_scorer_assisted" if detector.get("available") and scorer.get("available")
                else "detector_assisted"   if detector.get("available")
                else "essay_only"
            ),
            "ontology_files_loaded":  ontology_meta.get("loaded_files", {}),
            "ontology_skill_count":   ontology_meta.get("skill_count", 0),
            "ontology_bucket_counts": ontology_meta.get("bucket_counts", {}),
            "advantages_disadvantages_pattern_detected": is_ad,
            "evidence_hash":          evidence_hash,
            "processing_time_seconds":round(time.time()-t0, 3),
        },
        "boundary_policy": {
            "does_not_score_ielts":             True,
            "does_not_detect_grammar_errors":   True,
            "does_not_classify_lret_units":     True,
            "grammar_mode_policy":    "v7.3a: Grammar skills now measured independently via extract_grammar_features() in essay-only mode. control_proxy is a rule-based surface proxy (double-comparatives, run-ons, fragments). Detector adds error-family penalty precision via apply_detector_grammar_penalties().",
            "lexical_mode_policy":    "Broken chunks are retained as extraction signals for LRET, not classified by Evaluator.",
            "depth_model":            "v7.2 Evidence Depth Model: DEPTH_0/1/2/3 determines status ceiling. LLM may lower, not raise.",
            "allowed_quantitative_outputs": ["counts","ratios","competence_vector_dimensions","confidence","priority_index","evidence_depth"],
            "forbidden_outputs":      sorted(FORBIDDEN_OUTPUT_FIELDS),
        },
        "input_summary": {
            "baseline_features":    base,
            "detector_available":   detector.get("available"),
            "detector_row_count":   len(detector.get("diagnostic_rows",[])),
            "scorer_available":     scorer.get("available"),
            "llm_refinement":       llm_meta,
            "advantages_disadvantages_pattern": is_ad,
        },
        "calibration_summary": {
            "status_distribution": status_dist,
            "depth_distribution":  depth_dist,
            "gap_signal_count":    len(consumer_payloads["writing_coach_payload"].get("gap_signals",[])),
            "observed_count":      status_dist.get("observed",0),
            "observed_low_count":  status_dist.get("observed_low_evidence",0),
            "slot_only_count":     status_dist.get("observed_slot_only",0),
            "not_observed_count":  status_dist.get("not_observed",0),
        },
        "evidence_graph": {
            "paragraph_map":           maps.get("paragraph_map"),
            "sentence_map":            maps.get("sentence_map"),
            "argument_map":            maps.get("argument_map"),
            "cohesion_map":            maps.get("cohesion_map"),
            "evidence_index":          maps.get("evidence_index"),
            "detector_evidence_sample":detector.get("diagnostic_rows",[])[:80],
        },
        "lexical_unit_profile": {
            "lexical_units_for_lret": lexical_units,
            "unit_count":             len(lexical_units),
            "policy":                 "extraction_only_no_keep_fix_enhance_labels",
        },
        "skill_observation_profile": observations,
        "consumer_payloads":         consumer_payloads,
    }
    result["qa"] = validate_output(result, is_ad=is_ad)
    return result

# ═══════════════════════════════════════════════════════════════════════════════
# API
# ═══════════════════════════════════════════════════════════════════════════════

if FastAPI is not None:
    app = FastAPI(title="VA Premium Evaluator v7.3a WKE STANDALONE", version=ENGINE_VERSION)

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return {"status":"ok","engine_id":ENGINE_ID,"engine_version":ENGINE_VERSION,
                "schema_version":SCHEMA_VERSION,"boundary":"non-scoring competence-vector evaluator v7.2"}

    @app.post("/evaluate")
    def evaluate_endpoint(req: EvaluatorRequest) -> Dict[str, Any]:
        try: return evaluate(req)
        except Exception as e: raise HTTPException(status_code=500, detail=str(e))
else:
    app = None

# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="VA Premium Evaluator v7.3b WKE standalone")
    parser.add_argument("--input",  required=True, help="Request JSON path")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args()
    with open(args.input,"r",encoding="utf-8") as f: payload = json.load(f)
    if args.no_llm: payload["use_llm"] = False
    req = EvaluatorRequest(**payload)
    result = evaluate(req)
    with open(args.output,"w",encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2 if args.pretty else None)
    # Print summary
    cal = result.get("calibration_summary",{})
    qa  = result.get("qa",{})
    ad  = result["metadata"].get("advantages_disadvantages_pattern_detected", False)
    print(f"Wrote {args.output}")
    print(f"  A/D pattern: {ad}")
    print(f"  observed={cal.get('observed_count',0)}  low={cal.get('observed_low_count',0)}  slot_only={cal.get('slot_only_count',0)}  not_observed={cal.get('not_observed_count',0)}")
    print(f"  gap_signals={cal.get('gap_signal_count',0)}")
    print(f"  QA: {qa.get('status','?')}  errors={len(qa.get('errors',[]))}  warnings={len(qa.get('warnings',[]))}")

if __name__ == "__main__":
    main()
