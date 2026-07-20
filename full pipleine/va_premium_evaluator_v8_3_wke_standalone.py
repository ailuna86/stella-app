"""
VA Premium Evaluator v8.2 — Writing Knowledge Extractor (WKE)
Standalone FastAPI + CLI service.

Core boundary (unchanged from v7):
  Detector detects errors.
  Scorer scores IELTS bands/criteria.
  LRET classifies lexical units.
  Evaluator measures writing knowledge, competence vectors, evidence, capacity signals,
  and downstream payloads. It does NOT output IELTS scores, performance_score,
  performance_band, or LRET KEEP/FIX/ENHANCE labels.

v8.2.0 changes vs v8.1.0 (lexical unit quality — all other modules unchanged):
  Fix A  — _NGRAM_REJECT_START expanded: relative pronouns (who, whom, which),
            missing discourse connectors (thus, hence, furthermore, additionally,
            consequently, nevertheless, nonetheless, thereby), prepositions (for,
            with, by, from), possessives (my, your, his, her, our, their, its),
            wh-words (what, how). Eliminates: "Thus people", "For vegetarian",
            "its normal", "which includes", "who become" opening fragments.
  Fix B  — _NGRAM_REJECT_END expanded: who, whom.
            Eliminates: "people who", "reason whom" closing fragments.
  Fix C  — _SV_2GRAM_VERB_FORMS frozenset (3sg + past forms of all content
            verbs in _PREDICATE_VERBS). New 2-gram SV gate in add():
            [N]+[content_verb_inflected] → SV fragment, rejected.
            Eliminates: "diet allows", "population brings", "advantages outweigh".
  Fix D  — New 3-gram trailing-content-verb SV gate in add():
            [N][N]+[content_verb_inflected] → SV 3-gram fragment, rejected.
            Exception: toks[1] in _SV_GATE_AUX (keeps "science has shown",
            "diet has become" — legitimate VP chains).
            Eliminates: "vegetarian diet allows", "worldwide population brings".

v8.1.0 changes vs v8.0 (unchanged in v8.2):
  v8.1.0    — Discourse quality sub-system (Modules A–H); CREE depth 0–4;
               Structural recalibration; idea_density _B_STOP + rstrip fixes.
               New QA gates: V81_001–V81_006.

v8.0.0    — Sentence control (green/yellow/red per sentence, 5-cond gate).
               Paragraph function quality (intro/body/conclusion requirements).
               Essay function + CREE chain per body paragraph.
               Example quality (personal anecdote flagging, IELTS suitability).
               LRET: route hints (FIX/ENHANCE/KEEP/CLARIFY/DROP) + quality metrics.
               Structural skill recalibration: current_strength cross-validated
               against paragraph_function_status — presence ≠ quality.
               essay_revision_control_payload added to consumer_payloads.
               New QA gates: V80_001–V80_006.
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

ENGINE_ID       = "VA_PREMIUM_EVALUATOR_WKE_V8_3"
ENGINE_VERSION  = "8.3.0-comma-boundary-aware-ngram-extraction"
SCHEMA_VERSION  = "WKM_OUTPUT_V8.3"
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
    # v8.2 Fix A: relative pronouns as span openers → clause fragments
    'who', 'whom', 'which',
    # v8.2 Fix A: missing discourse connectors
    'thus', 'hence', 'furthermore', 'additionally', 'consequently',
    'nevertheless', 'nonetheless', 'thereby',
    # v8.2 Fix A: prepositions missing from set
    'for', 'with', 'by', 'from',
    # v8.2 Fix A: possessives as span openers → open reference fragments
    'my', 'your', 'his', 'her', 'our', 'their', 'its',
    # v8.2 Fix A: wh-words
    'what', 'how',
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
    # v8.2 Fix B: relative pronouns as span enders → incomplete antecedent reference
    'who', 'whom',
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

# v8.2: extended aux set for the 3-gram exception in Fixes C/D and the type-agnostic gate.
# "has", "have", "had" mark perfect VP chains ("science has shown", "diet has increased")
# — these are legitimate VPs, not [N][aux][V] SV fragments.
# "do/does/did" mark emphatic/question VP patterns ("children do need", "experts did warn").
_SV_GATE_ALL_AUX = _SV_GATE_AUX | frozenset({'has', 'have', 'had', 'do', 'does', 'did'})

# v8.2 Fix C/D: 3sg-present and simple-past forms of all content verbs in
# _PREDICATE_VERBS. These morphological forms are unambiguously predicative
# (cannot function as nouns), so [NOUN] + [these] = SV fragment.
_SV_2GRAM_VERB_FORMS = frozenset({
    # 3sg present (-s / -es): unambiguously verb in subject+predicate position
    'brings', 'causes', 'gives', 'needs', 'takes', 'works', 'goes',
    'supports', 'believes', 'makes', 'teaches', 'guides', 'spends',
    'invests', 'includes', 'grows', 'costs', 'leads', 'solves', 'creates',
    'helps', 'thinks', 'knows', 'gets', 'comes', 'sees', 'becomes',
    'faces', 'affects', 'pays', 'opens', 'tells', 'stops', 'enables',
    'allows', 'requires', 'benefits', 'shows', 'lives', 'uses', 'depends',
    'feels', 'looks', 'increases', 'decreases', 'provides', 'receives',
    'reduces', 'keeps', 'outweighs',
    # simple past (irregular and regular): unambiguously verb
    'brought', 'caused', 'gave', 'needed', 'took', 'went', 'supported',
    'believed', 'made', 'taught', 'guided', 'spent', 'invested', 'included',
    'grew', 'led', 'solved', 'created', 'helped', 'thought', 'knew', 'got',
    'came', 'saw', 'became', 'faced', 'affected', 'paid', 'opened', 'told',
    'stopped', 'enabled', 'allowed', 'required', 'benefited', 'showed',
    'lived', 'used', 'depended', 'felt', 'looked', 'increased', 'decreased',
    'provided', 'received', 'reduced', 'kept', 'outweighed',
    # base + 3sg for verbs common in IELTS but absent from _PREDICATE_VERBS;
    # these units are typed noun_phrase by infer_type — requires type-agnostic gate
    'outweigh', 'outweighs',
})



# ═══════════════════════════════════════════════════════════════════════════════
# v8.0 SENTENCE CONTROL — GRAMMAR INSTABILITY PATTERNS
# These are UNIVERSAL quality signals, not Detector error-family classifiers.
# ═══════════════════════════════════════════════════════════════════════════════

# Double comparative: "more stronger", "more better"
_SC_DOUBLE_COMP = re.compile(
    r"\bmore\s+(stronger|weaker|bigger|smaller|faster|slower|better|worse|"
    r"higher|lower|older|younger|easier|harder|longer|shorter|wider|deeper|"
    r"richer|poorer|heavier|lighter|greater|lesser)\b", re.I)

# Modal/auxiliary + wrong verb form: "should went", "can did", "must ran"
_SC_MODAL_WRONG = re.compile(
    r"\b(can|could|will|would|should|must|may|might|shall)\s+\w+(ed|en)\b"
    r"(?!\s+by\b)(?!\s+to\b)", re.I)

# Aux-to + wrong form: "has to spent", "need to took"
_SC_AUX_TO_WRONG = re.compile(
    r"\b(has|have|had|needs?)\s+to\s+\w+(ed|en)\b", re.I)

# Uncountable/mass noun used as plural: "peoples", "informations", "advices"
_SC_PLURAL_ERROR = re.compile(
    r"\b(peoples|informations|advices|knowledges|furnitures|equipments|"
    r"homeworks|feedbacks|evidences|knowledges|wealths|weathers|"
    r"violences|behaviours\b|humours\b)\b", re.I)

# "so + comparative" ("so older", "so bigger") — non-standard intensifier
_SC_SO_COMP = re.compile(r"\bso\s+(much\s+)?\w+(er|est)\b", re.I)


# 3rd-person singular pronoun with wrong aux: "she have", "he have", "it have"
# Also catches "I has", "they has"
_SC_PRONOUN_AUX = re.compile(
    r'\b(she|he|it)\s+have\b'
    r'|\b(i|we|they|you)\s+has\b'
    r"|\b(she|he|it)\s+don't\b", re.I)

# Plural subject with singular copula: "people is", "they is"
# Implemented as a function (not bare regex) to filter "of X people is" false positives
_SC_PLURAL_COPULA_PAT = re.compile(
    r'\bpeople\s+(?:is|was)\b'
    r'|\bchildren\s+(?:is|was)\b'
    r'|\bthey\s+is\b'
    r'|\bwe\s+is\b'
    r'|\bcountries\s+(?:is|was)\b'
    r'|\bfamilies\s+(?:is|was)\b'
    r'|\bstudents\s+(?:is|was)\b'
    r'|\bworkers\s+(?:is|was)\b'
    r'|\bgovernments\s+(?:is|was)\b'
    r'|\bparents\s+(?:is|was)\b'
    r'|\bemployees\s+(?:is|was)\b'
    r'|\bteachers\s+(?:is|was)\b', re.I)
_SC_OF_PHRASE = re.compile(r'\bof\s+(?:\w+\s+){0,3}$', re.I)  # "of [adj?] " before noun

def _has_plural_copula(text: str) -> bool:
    """Check for plural subject + singular copula, filtering 'X of people is' complements."""
    low = text.lower()
    for m in _SC_PLURAL_COPULA_PAT.finditer(low):
        pre = low[max(0, m.start()-40):m.start()]
        # Skip if preceded by an "of"-phrase: "the number of older people is" is correct
        if _SC_OF_PHRASE.search(pre):
            continue
        return True
    return False
# Sentence has no verb-like token at all (likely fragment)
_SC_MIN_VERB = _PREDICATE_VERBS   # reuse existing pattern

# Subordinator overload: ≥3 clause markers in a short sentence
_SC_SUBORD = re.compile(
    r"\b(because|although|though|since|when|while|if|unless|as|that|which|who|whom)\b", re.I)

# Vague expressions that should become CLARIFY candidates
_SC_VAGUE_EXPR = re.compile(
    r"\b(some\s+kinds?\s+of\s+(things?|stuff)|many\s+things?|"
    r"this\s+way|some\s+problems?\s+like\s+this|and\s+so\s+on|"
    r"and\s+(stuff|things)\s+like\s+that|all\s+kinds?\s+of\s+things?|"
    r"some\s+(aspects?|factors?|issues?)\s+like\s+this)\b", re.I)

# Personal anecdote signals  
_SC_PERSONAL = re.compile(
    r"\b(my\s+(grandmother|grandfather|mother|father|sister|brother|friend|"
    r"uncle|aunt|cousin|teacher|parent|parents|family|boss|colleague|"
    r"classmate|neighbour|neighbor)|"
    r"my\s+own\s+(experience|life|family|home|school|country|city)|"
    r"when\s+I\s+(was|were|went|lived|studied|worked|grew|had))\b", re.I)

# Example introduction markers
_SC_EXAMPLE_INTRO = re.compile(
    r"\b(for\s+example|for\s+instance|such\s+as|e\.g\.)\b", re.I)

# Structural skill IDs that can be recalibrated
_STRUCT_SKILLS_ORG = frozenset({
    "global_structure_control","introduction_construction","conclusion_construction",
    "topic_sentence_control","paragraph_balance","paragraph_planning","logical_sequencing",
    "ws_conclusion_alignment_struct","ws_proposition_clarity","ws_proposition_completeness",
})
_STRUCT_SKILLS_COH = frozenset({
    "transition_control","information_flow","reference_management",
    "cohesion_without_overlinking","progression_management",
    "example_integration","cohesion",
})

# LRET: vague nouns that flag CLARIFY candidates
_LRET_VAGUE_WORDS = frozenset({
    "things","stuff","aspects","factors","issues","problems","benefits","advantages",
    "disadvantages","matters","areas","ways","parts","cases","examples","situations",
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
    v8.2: Extract lexical units for LRET consumption.

    LRET classifies each unit as KEEP / FIX / ENHANCE — evaluator does NOT label.
    Coverage must include all three categories:
      KEEP candidates  — good collocations student uses correctly (ageing population,
                         care homes, community groups, etc.)
      ENHANCE candidates — vague, generic or weak vocabulary (things, people, a lot,
                           some problems, many benefits)
      FIX candidates   — malformed patterns, informal register, incorrect collocations

    Architecture (unchanged from v7.3b):
      1. Deduplicated by text only — no dual-type entries for same span.
      2. Unit gets exactly ONE type (verb_phrase wins over noun_phrase when span
         contains a main verb; otherwise noun_phrase).
      3. 2-gram and 3-gram only — 4/5-gram windows produce unintelligible cross-clause spans.
      4. Clause-break filter — 3-grams skip spans where internal token is a subject
         pronoun or subordinating/coordinating conjunction.
      5. ALL adjacent 2-gram content-word pairs are extracted (not only those containing
         academic words or predicate verbs) — ensures KEEP candidates are captured.
      6. axis_candidates and extraction_flags cover all three LRET categories.

    v8.2 fragment gates (new — see _SV_2GRAM_VERB_FORMS, _NGRAM_REJECT_START/END):
      Fix A — _NGRAM_REJECT_START: relative pronouns, missing discourse connectors,
               prepositions, possessives, wh-words. Blocks "which includes", "who become",
               "Thus people", "For vegetarian", "its normal".
      Fix B — _NGRAM_REJECT_END: who, whom. Blocks "people who".
      Fix C — 2-gram SV gate: [N]+[3sg/past_verb] → reject.
               Blocks "diet allows", "population brings".
      Fix D — 3-gram SV gate: [N][N]+[3sg/past_verb] → reject.
               Blocks "vegetarian diet allows". Exception: aux in mid-position kept.
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

    # v8.3 Fix E -- comma-boundary awareness.
    #
    # v8.0-v8.2's clause-break detection (_CLAUSE_INNER, _NGRAM_REJECT_START/END,
    # the SV-fragment gates) all work by recognizing specific CLOSED-CLASS WORDS
    # in specific positions (subordinators, coordinators, subject pronouns,
    # copulas, possessives, an article in the middle slot, etc.). None of them
    # check the literal punctuation of the source sentence. Confirmed directly:
    # words() (line ~430) strips ALL punctuation via `[A-Za-z][A-Za-z\'\'\-]*`,
    # so by the time the 2-gram/3-gram loops run, information about where a
    # comma was is already gone. This lets windows silently bridge a comma
    # whenever neither word on either side happens to be on one of the
    # hand-maintained word lists -- e.g. "On the other hand, older people..."
    # produces the 3-gram "hand older people": "hand", "older", and "people"
    # are not in _CLAUSE_INNER and not _NGRAM_REJECT_START/END entries, so
    # nothing catches it, even though it plainly bridges two clauses. Same
    # root cause behind "money on hospitals" (from "...hospitals and care
    # homes...") and other comma-adjacent spans. This is a different,
    # structural class of gap from the word-list approach used in Fixes A-D
    # across v7.3b.1-v8.2 -- it does not require enumerating more words, it
    # uses punctuation that is already in the source text and was simply
    # being discarded before this point.
    #
    # Fix: compute which comma-delimited segment each token belongs to, and
    # reject any n-gram window whose first and last token are not in the same
    # segment. Additive to, not a replacement for, the existing word-list
    # gates above -- both still apply.
    def _comma_segment_ids(stext: str, expected_len: int) -> List[int]:
        seg_ids: List[int] = []
        for seg_idx, part in enumerate(stext.split(',')):
            n = len(words(part))
            seg_ids.extend([seg_idx] * n)
        if len(seg_ids) != expected_len:
            # Tokenization of comma-split parts didn't line up with the whole
            # sentence (rare edge case). Fail safe: treat as one segment so
            # this sentence's comma check becomes a no-op rather than
            # mis-aligning and rejecting/accepting the wrong spans.
            return [0] * expected_len
        return seg_ids

    def _crosses_comma(seg_ids: List[int], i: int, j: int) -> bool:
        if not seg_ids:
            return False
        return seg_ids[i] != seg_ids[j]

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
            # v8.2 Fix C: 2-gram [N] + [content_verb_inflected] → SV fragment
            # "diet allows", "population brings", "advantages outweigh"
            # Inflected (3sg/past) forms are morphologically unambiguous predicates.
            if (len(toks) == 2
                    and toks[1].lower() in _SV_2GRAM_VERB_FORMS
                    and toks[0].lower() not in STOPWORDS
                    and toks[0].lower() not in _SV_GATE_AUX):
                return
            # v8.2 Fix D: 3-gram [N][N/ADJ] + [content_verb_inflected] → SV fragment
            # "vegetarian diet allows", "worldwide population brings"
            # Exception: toks[1] in _SV_GATE_ALL_AUX keeps "science has shown",
            # "diet has increased", "children do need" — aux in mid-position =
            # legitimate VP chain (perfect/modal/emphatic), not NNV SV fragment.
            if (len(toks) == 3
                    and toks[2].lower() in _SV_2GRAM_VERB_FORMS
                    and toks[0].lower() not in STOPWORDS
                    and toks[1].lower() not in STOPWORDS
                    and toks[1].lower() not in _SV_GATE_ALL_AUX):
                return

        # v8.2 type-agnostic SV gate ─────────────────────────────────────────
        # Catches NP-typed SV fragments whose verb is not in _PREDICATE_VERBS
        # (so infer_type returns 'noun_phrase', bypassing the VP block above).
        # "advantages outweigh", "trend outweigh" → [NOUN] + [verb not in PV set]
        # Condition mirrors Fix C/D but is unit-type agnostic.
        # Uses _SV_GATE_ALL_AUX (incl. has/have/had/do) for the 3-gram exception
        # so "diet has increased", "science has shown" are NOT rejected.
        if len(toks) in (2, 3) and toks[-1].lower() in _SV_2GRAM_VERB_FORMS:
            if (toks[0].lower() not in STOPWORDS
                    and toks[0].lower() not in _SV_GATE_AUX
                    and (len(toks) == 2 or toks[1].lower() not in _SV_GATE_ALL_AUX)):
                return
        # ─────────────────────────────────────────────────────────────────────

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


        # ── v8.0 route hint ──────────────────────────────────────────────────
        if 'informal_register' in flag_set or 'possible_malformed_or_boundary_error' in flag_set:
            route_hint = 'FIX'
        elif utype == 'verb_phrase_or_predicate_chunk' and 'collocation_candidate' in flag_set and 'predicate_argument_candidate' in flag_set:
            route_hint = 'ENHANCE'
        elif any(w in key.lower().split() for w in _LRET_VAGUE_WORDS) and len(key.split()) <= 4:
            route_hint = 'CLARIFY'
        elif 'collocation_candidate' in flag_set and 'topic_relevant' in flag_set:
            route_hint = 'KEEP' if utype == 'word' else 'ENHANCE'
        elif utype == 'word' and key.lower() in BASIC_ACADEMIC_WORDS:
            route_hint = 'KEEP'
        elif utype == 'discourse_or_formulaic_chunk':
            route_hint = 'KEEP'
        elif val < 0.45:
            route_hint = 'DROP'
        else:
            route_hint = 'KEEP'

        span_complete = (
            'malformed' if 'possible_malformed_or_boundary_error' in flag_set else
            'partial'   if ('edge_function_word' in flag_set and utype == 'verb_phrase_or_predicate_chunk'
                            and 'collocation_candidate' not in flag_set) else
            'complete'
        )
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
                'candidate_route_hint':  route_hint,
                'span_completeness':     span_complete,
                'covered_subunits':      [],
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
        seg_ids = _comma_segment_ids(stext, len(toks))

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
            if _crosses_comma(seg_ids, i, i + 1):  # v8.3 Fix E
                continue
            add(' '.join(chunk), infer_type(chunk), sent)

        # 4. 3-gram collocations — must have 2+ content words, no clause break
        for i in range(len(toks) - 2):
            chunk = toks[i:i+3]
            if sum(1 for t in chunk if t.lower() not in STOPWORDS) < 2:
                continue
            if has_clause_break(chunk):
                continue
            if _crosses_comma(seg_ids, i, i + 2):  # v8.3 Fix E
                continue
            if chunk[0].lower() in _NGRAM_REJECT_START:
                continue
            if chunk[-1].lower() in _NGRAM_REJECT_END:
                continue
            add(' '.join(chunk), infer_type(chunk), sent)


    # v8.0 — Mark covered subunits (shorter spans contained in complete units)
    unit_texts = list(units.keys())
    for longer_key in unit_texts:
        if len(longer_key.split()) <= 1:
            continue
        for shorter_key in unit_texts:
            if shorter_key == longer_key:
                continue
            if shorter_key in longer_key and shorter_key != longer_key:
                if shorter_key not in units[longer_key]['covered_subunits']:
                    units[longer_key]['covered_subunits'].append(shorter_key)

    # v8.0 — Compute lexical unit quality metrics
    out = sorted(units.values(),
                 key=lambda u: (u['candidate_value'], u['frequency']),
                 reverse=True)
    capped = out[:MAX_LEXICAL_UNITS]
    for idx, u in enumerate(capped, 1):
        u['unit_id'] = f'lu_{idx:04d}'

    # v8.0 quality metrics
    complete_count  = sum(1 for u in capped if u.get('span_completeness') == 'complete')
    partial_count   = sum(1 for u in capped if u.get('span_completeness') == 'partial')
    malformed_count = sum(1 for u in capped if u.get('span_completeness') == 'malformed')
    clarify_count   = sum(1 for u in capped if u.get('candidate_route_hint') == 'CLARIFY')
    drop_count      = sum(1 for u in capped if u.get('candidate_route_hint') == 'DROP')
    covered_count   = sum(1 for u in capped if u.get('covered_subunits'))
    total           = len(capped)

    lret_quality = {
        "total_candidate_units":        total,
        "valid_meaningful_units":       complete_count,
        "single_word_units":            sum(1 for u in capped if u['unit_type'] == 'word'),
        "multiword_units":              sum(1 for u in capped if u['unit_type'] != 'word'),
        "complete_span_count":          complete_count,
        "partial_span_count":           partial_count,
        "malformed_span_count":         malformed_count,
        "clarify_candidate_count":      clarify_count,
        "drop_candidate_count":         drop_count,
        "covered_subunit_count":        covered_count,
        "meaningful_unit_rate":         round(complete_count / max(1, total), 3),
        "fragment_or_noise_rate":       round((partial_count + malformed_count) / max(1, total), 3),
        "essay_specific_rules_detected": False,
        "topic_specific_whitelist_detected": False,
    }

    return capped, lret_quality

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


# ═══════════════════════════════════════════════════════════════════════════════
# v8.0 SENTENCE CONTROL
# ═══════════════════════════════════════════════════════════════════════════════

def _sentence_safe_hint(grammar_obs: str, recov: str, lang_status: str) -> str:
    if lang_status == "red":
        return "This sentence needs to be fully rewritten. Focus on the main idea only."
    if grammar_obs in ("unstable", "severely_unstable"):
        return "Check the grammar structure in this sentence carefully before keeping it."
    if recov == "partial":
        return "The idea is understandable, but the sentence needs grammar or wording repair."
    if grammar_obs == "minor_instability":
        return "The sentence is mostly clear — check the small grammar or word form issue."
    return ""


def _detector_sent_index_set(detector: Optional[Dict]) -> set:
    """v8.1 FIX: detector 1-indexed -> evaluator 0-indexed. Subtract 1."""
    if not detector or not detector.get("available"):
        return set()
    rows = detector.get("diagnostic_rows", [])
    return {int(r["sentence_index"]) - 1
            for r in rows
            if r.get("sentence_index") is not None}


def _sentence_role_position_map(paragraph_map: List[Dict]) -> Dict[int, Tuple[str, bool, bool]]:
    """v8.4: map sentence_index -> (paragraph_role, is_first_in_paragraph,
    is_last_in_paragraph), using the identical role-detection rule
    assess_paragraph_function() already uses (first paragraph = introduction,
    last = conclusion, else body). Built once, ahead of per-sentence
    assessment, so sentence-level function-fit checks can reuse it without
    re-deriving paragraph structure.
    """
    n_paras = len(paragraph_map)
    out: Dict[int, Tuple[str, bool, bool]] = {}
    for pidx_pos, para in enumerate(paragraph_map):
        pidx = para.get("paragraph_index", pidx_pos)
        if pidx_pos == 0:
            role = "introduction"
        elif pidx_pos == n_paras - 1:
            role = "conclusion"
        else:
            role = "body"
        idxs = list(para.get("sentence_indices", []))
        for pos, sidx in enumerate(idxs):
            out[sidx] = (role, pos == 0, pos == len(idxs) - 1)
    return out


def _sentence_function_fit(sent: Dict, role: str, is_first: bool, is_last: bool) -> Tuple[str, Optional[str]]:
    """v8.4: real per-sentence function/role-fit signal, replacing the
    previous complete absence of one (sentence_control had no function field
    at all -- role fit was only ever assessed at the whole-paragraph level,
    confirmed by direct inspection of real evaluator output). Universal rule,
    not essay-specific: reuses the exact same structural cue flags
    (has_position_cue, has_reason_cue, has_example_cue, has_contrast_cue,
    has_conclusion_cue) that _check_intro/_check_body/_check_conclusion
    already use for paragraph-level checks, just applied to the individual
    sentence that is structurally expected to carry each role, based on
    established composition convention: topic/position sentences open a
    paragraph, wrap-up/position sentences close one, and middle sentences
    are supporting content with no single fixed requirement of their own.
    Only first and last sentences get a strict check; a middle sentence
    defaults to green (its content is elaboration/support, not a structural
    slot with a fixed job). A one-sentence paragraph is checked as both
    first and last.
    """
    substantive = len(words(sent.get("text", ""))) >= 5
    has_pos = bool(sent.get("has_position_cue"))
    has_reason = bool(sent.get("has_reason_cue"))
    has_example = bool(sent.get("has_example_cue"))
    has_contrast = bool(sent.get("has_contrast_cue"))
    has_conclusion = bool(sent.get("has_conclusion_cue"))

    if role == "introduction":
        # Topic framing (first sentence): any real content satisfies this --
        # matches _check_intro's own low bar (topic_framing_present is met
        # by the mere existence of a first sentence). Position/preview
        # (last sentence) is a specific structural marker, not just length,
        # so `substantive` deliberately does NOT satisfy it on its own --
        # confirmed against real data that a long, fluent, but purely
        # descriptive closing sentence ("For example in countries like
        # Japan...") still leaves position_stated and main_argument_preview
        # genuinely missing at the paragraph level; falling back to word
        # count here would silently mask exactly that gap.
        if is_first and not substantive:
            return "yellow", "This should help frame the essay's topic."
        if is_last and not (has_pos or has_reason or has_contrast):
            return "yellow", "This should state your position or preview your main points."
        return "green", None

    if role == "body":
        if is_first and not (has_pos or substantive):
            return "yellow", "This should state the main idea of this paragraph."
        if is_last and not (has_example or has_reason or has_contrast or has_conclusion):
            return "yellow", "This should support your point with a reason or example."
        return "green", None

    if role == "conclusion":
        if is_last and not (has_conclusion or has_pos):
            return "yellow", "This should clearly restate your final position."
        return "green", None

    return "green", None


def assess_sentence_control(
    sentence_map: List[Dict],
    detector: Optional[Dict] = None,
    paragraph_map: Optional[List[Dict]] = None,
) -> List[Dict]:
    """
    v8.0 — Rule-based sentence-level language control assessment.
    Returns one control object per sentence.

    Produces green / yellow / red WITHOUT grammar-family classification.
    Green gate: 5 conditions (see spec §4.1).

    v8.4: when paragraph_map is supplied, also computes a genuine per-
    sentence function/role-fit signal (sentence_function_status /
    sentence_function_note) -- see _sentence_function_fit(). Optional and
    backward compatible: omitting paragraph_map reproduces the exact prior
    output shape for any other caller.
    """
    det_sents = _detector_sent_index_set(detector)
    role_pos_map = _sentence_role_position_map(paragraph_map or [])
    results = []

    for sent in sentence_map:
        text = sent["text"]
        low  = text.lower()
        toks = words(text)
        sidx = sent["sentence_index"]

        grammar_hits: List[Tuple[str, str]] = []  # (observation_type, severity)

        if _SC_DOUBLE_COMP.search(low):
            grammar_hits.append(("verb_pattern_instability", "moderate"))
        if _SC_MODAL_WRONG.search(low):
            grammar_hits.append(("verb_pattern_instability", "high"))
        if _SC_AUX_TO_WRONG.search(low):
            grammar_hits.append(("verb_pattern_instability", "high"))
        if _SC_PLURAL_ERROR.search(low):
            grammar_hits.append(("agreement_control_problem", "moderate"))
        if _SC_PRONOUN_AUX.search(low):
            grammar_hits.append(("verb_pattern_instability", "high"))
        if _has_plural_copula(text):
            grammar_hits.append(("agreement_control_problem", "high"))
        if _SC_SO_COMP.search(low):
            grammar_hits.append(("local_grammar_instability", "minor"))

        # Subordinator overload: ≥3 clause markers in a sentence < 20 words
        n_subord = len(_SC_SUBORD.findall(low))
        if n_subord >= 3 and len(toks) < 20:
            grammar_hits.append(("clause_control_problem", "moderate"))

        # Very short sentence (likely a fragment)
        is_fragment_risk = len(toks) < 4 and not sent.get("has_conclusion_cue") and not sent.get("has_example_cue")
        if is_fragment_risk and not _SC_MIN_VERB.search(low):
            grammar_hits.append(("clause_control_problem", "minor"))

        has_detector_errors = sidx in det_sents

        # Grammar control observation (without family classification)
        # Weight by severity of hits, not just count
        n_high   = sum(1 for _, sev in grammar_hits if sev == "high")
        n_mod    = sum(1 for _, sev in grammar_hits if sev == "moderate")
        n_total  = len(grammar_hits)
        if n_total >= 3 or (n_high >= 2) or (n_high >= 1 and n_mod >= 1):
            grammar_obs = "severely_unstable"
        elif n_total >= 2 or n_high >= 1:
            grammar_obs = "unstable"
        elif n_total == 1:
            grammar_obs = "minor_instability"
        elif has_detector_errors:
            grammar_obs = "minor_instability"
        else:
            grammar_obs = "controlled"

        # Semantic recoverability — independent of grammar observation
        # High-severity individual errors are still recoverable if meaning is clear
        if grammar_obs == "severely_unstable":
            recov = "low"
        elif grammar_obs == "unstable":
            recov = "partial"
        elif grammar_obs == "minor_instability":
            recov = "full"
        else:
            recov = "full"

        # Lexical control observation (LRET-facing, no LRET classification)
        has_vague = bool(_SC_VAGUE_EXPR.search(low))
        lex_obs = "unstable" if has_vague and len(toks) < 8 else ("minor_instability" if has_vague else "controlled")

        # Green gate — all 5 must hold
        is_green = (
            grammar_obs == "controlled" and
            lex_obs in ("controlled", "minor_instability") and
            recov == "full" and
            not has_detector_errors and
            n_high == 0 and
            len(toks) >= 4
        )

        if is_green:
            lang_status = "green"
            rev_status  = "keep"
        elif grammar_obs == "severely_unstable" or recov in ("blocked", "low"):
            lang_status = "red"
            rev_status  = "rewrite"
        elif grammar_obs == "unstable" or recov == "partial":
            lang_status = "yellow"
            rev_status  = "improve"
        else:
            lang_status = "yellow"
            rev_status  = "improve"

        evidence = [
            {
                "evidence_type": obs_type,
                "observation":   f"Grammar control observation: {obs_type.replace('_',' ')} ({severity}).",
                "severity":      severity,
            }
            for obs_type, severity in grammar_hits
        ]
        if has_detector_errors:
            evidence.append({
                "evidence_type": "grammar_control",
                "observation":   "Detector evidence indicates language control issues in this sentence.",
                "severity":      "moderate",
            })

        root_cause = "none"
        if grammar_hits:
            root_cause = "local_language"
        elif has_vague:
            root_cause = "lexical_choice"

        func_status, func_note = "unknown", None
        role_pos = role_pos_map.get(sidx)
        if role_pos is not None:
            role, is_first, is_last = role_pos
            func_status, func_note = _sentence_function_fit(sent, role, is_first, is_last)

        results.append({
            "sentence_index":               sidx,
            "paragraph_index":              sent["paragraph_index"],
            "text":                         text,
            "language_control_status":      lang_status,
            "grammar_control_observation":  grammar_obs,
            "lexical_control_observation":  lex_obs,
            "semantic_recoverability":      recov,
            "revision_status":              rev_status,
            "root_cause_type":              root_cause,
            "sentence_function_status":     func_status,
            "sentence_function_note":       func_note,
            "student_safe_hint":            _sentence_safe_hint(grammar_obs, recov, lang_status),
            "confidence":                   round(0.60 + 0.20 * (1 if has_detector_errors else 0)
                                                   + 0.10 * (1 if grammar_hits else 0), 2),
            "evidence":                     evidence,
            "detector_errors_consulted":    has_detector_errors,
        })

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# v8.0 PARAGRAPH FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

_INTRO_POSITION_CUES = re.compile(
    r"\b(i believe|i think|i argue|i feel|in my opinion|in my view|"
    r"it is (clear|obvious|argued|believed)|this essay (will |argues?|discusses?)|"
    r"this (essay|paper|response))\b", re.I)
_BODY_TOPIC_CUES    = re.compile(r"\b(one|first|second|another|furthermore|moreover|additionally|"
                                   r"on the one hand|on the other hand|a (key|major|main|important|significant))\b", re.I)
_CONC_SUMMARY_CUES  = re.compile(r"\b(in conclusion|to conclude|to summarise|to summarize|"
                                   r"in summary|overall|in short|in brief|therefore|thus|hence)\b", re.I)
_PREVIEW_CUES       = re.compile(r"\b(will|this essay (will|shall)|first[ly]?|second[ly]?|"
                                   r"one (advantage|disadvantage|reason|benefit)|both|advantage|disadvantage)\b", re.I)
_NEW_IDEA_IN_CONC   = re.compile(r"\b(for example|for instance|such as|e\.g\.|research shows|"
                                   r"studies show|according to)\b", re.I)


def _para_sentences(para: Dict, sentence_map: List[Dict]) -> List[Dict]:
    idxs = set(para.get("sentence_indices", []))
    return [s for s in sentence_map if s["sentence_index"] in idxs]


def _language_evaluability(para_sents: List[Dict], sent_control: List[Dict]) -> str:
    """Check if language corruption blocks paragraph discourse evaluation."""
    sc_map    = {s["sentence_index"]: s for s in sent_control}
    red_count = sum(1 for s in para_sents if sc_map.get(s["sentence_index"], {}).get("language_control_status") == "red")
    yel_count = sum(1 for s in para_sents if sc_map.get(s["sentence_index"], {}).get("language_control_status") == "yellow")
    total     = len(para_sents)
    if total == 0:
        return "full"
    # blocked: majority are red
    if red_count >= max(2, total // 2):
        return "blocked"
    # partial: any red, OR majority are yellow (heavily degraded language)
    if red_count >= 1 or yel_count >= max(2, (total * 2) // 3):
        return "partial"
    return "full"


def _check_intro(para_sents: List[Dict]) -> Tuple[List[str], List[str], List[Dict]]:
    met, missing, alerts = [], [], []
    full_text = " ".join(s["text"] for s in para_sents)
    low = full_text.lower()

    # Topic framing: first sentence usually restates topic
    if len(para_sents) >= 1:
        met.append("topic_framing_present")
    else:
        missing.append("topic_framing_present")
        alerts.append({"alert_type": "missing_introduction", "severity": "red",
                       "evidence_quote": "",
                       "student_hint": "Write an introduction that introduces the topic."})

    # Position
    has_pos = any(s.get("has_position_cue") for s in para_sents) or bool(_INTRO_POSITION_CUES.search(full_text))
    if has_pos:
        met.append("position_stated")
    else:
        missing.append("position_stated")
        alerts.append({"alert_type": "position_too_general", "severity": "yellow",
                       "evidence_quote": para_sents[-1]["text"] if para_sents else "",
                       "student_hint": "Add a clear sentence that gives your position or opinion."})

    # Main argument preview
    has_preview = bool(_PREVIEW_CUES.search(low)) or any(
        s.get("has_reason_cue") or s.get("has_contrast_cue") for s in para_sents[1:])
    if has_preview:
        met.append("main_argument_preview")
    else:
        missing.append("main_argument_preview")
        alerts.append({"alert_type": "missing_main_argument_preview", "severity": "yellow",
                       "evidence_quote": para_sents[-1]["text"] if para_sents else "",
                       "student_hint": "Add a short preview of the main points you will discuss."})

    return met, missing, alerts


def _check_body(para_sents: List[Dict]) -> Tuple[List[str], List[str], List[Dict]]:
    met, missing, alerts = [], [], []
    full_text = " ".join(s["text"] for s in para_sents)

    # Topic sentence (first sentence should be a claim or direction)
    if para_sents and (para_sents[0].get("has_position_cue") or
                       bool(re.search(r"\b(one|a key|a main|another|first|second|"
                                       r"on the one hand|on the other hand)\b",
                                       para_sents[0]["text"], re.I))):
        met.append("topic_sentence_present")
    elif para_sents and len(words(para_sents[0]["text"])) >= 5:
        met.append("topic_sentence_present")  # Any substantive first sentence
    else:
        missing.append("topic_sentence_present")
        alerts.append({"alert_type": "weak_topic_sentence", "severity": "yellow",
                       "evidence_quote": para_sents[0]["text"] if para_sents else "",
                       "student_hint": "Start the paragraph with a clear main idea sentence."})

    # Reason
    has_reason = any(s.get("has_reason_cue") for s in para_sents)
    if has_reason:
        met.append("reason_present")
    else:
        missing.append("reason_present")
        alerts.append({"alert_type": "claim_without_reason", "severity": "yellow",
                       "evidence_quote": full_text[:120],
                       "student_hint": "Explain why this point is true or important."})

    # Example
    has_example = any(s.get("has_example_cue") for s in para_sents)
    if has_example:
        met.append("example_present")
    else:
        missing.append("example_present")
        alerts.append({"alert_type": "no_example", "severity": "yellow",
                       "evidence_quote": full_text[:120],
                       "student_hint": "Add a specific example to support your point."})

    # Explanation after example (any sentence after example_cue sentence)
    if has_example:
        ex_sent_indices = [i for i, s in enumerate(para_sents) if s.get("has_example_cue")]
        if ex_sent_indices and ex_sent_indices[-1] < len(para_sents) - 1:
            met.append("explanation_after_example")
        else:
            missing.append("explanation_after_example")
            alerts.append({"alert_type": "example_without_explanation", "severity": "yellow",
                           "evidence_quote": para_sents[ex_sent_indices[-1]]["text"] if ex_sent_indices else "",
                           "student_hint": "After the example, explain how it supports your main point."})

    return met, missing, alerts


def _check_conclusion(para_sents: List[Dict]) -> Tuple[List[str], List[str], List[Dict]]:
    met, missing, alerts = [], [], []
    full_text = " ".join(s["text"] for s in para_sents)
    low = full_text.lower()

    # Summary cue
    has_summary_cue = bool(_CONC_SUMMARY_CUES.search(low))
    if has_summary_cue:
        met.append("conclusion_marker_present")
    else:
        missing.append("conclusion_marker_present")

    # Final position
    has_final_pos = any(s.get("has_position_cue") or s.get("has_conclusion_cue") for s in para_sents)
    if has_final_pos:
        met.append("final_position_stated")
    else:
        missing.append("final_position_stated")
        alerts.append({"alert_type": "missing_final_position", "severity": "yellow",
                       "evidence_quote": para_sents[-1]["text"] if para_sents else "",
                       "student_hint": "Add a sentence that clearly restates your final answer or position."})

    # Reason summary (any reason cue = some summarising of points)
    has_reason_summary = any(s.get("has_reason_cue") or s.get("has_contrast_cue") for s in para_sents)
    if has_reason_summary or len(para_sents) >= 2:
        met.append("reason_summary_present")
    else:
        missing.append("reason_summary_present")
        alerts.append({"alert_type": "missing_reason_summary", "severity": "yellow",
                       "evidence_quote": full_text[:120],
                       "student_hint": "Briefly mention your main reasons before the final sentence."})

    # No new example
    if _NEW_IDEA_IN_CONC.search(low):
        missing.append("no_new_example_in_conclusion")
        alerts.append({"alert_type": "new_idea_in_conclusion", "severity": "yellow",
                       "evidence_quote": next(
                           (s["text"] for s in para_sents if _NEW_IDEA_IN_CONC.search(s["text"].lower())), ""),
                       "student_hint": "Do not add new examples in the conclusion — only summarise what you said."})
    else:
        met.append("no_new_example_in_conclusion")

    return met, missing, alerts


def assess_paragraph_function(
    paragraph_map: List[Dict],
    sentence_map:  List[Dict],
    sent_control:  List[Dict],
) -> List[Dict]:
    """
    v8.0 — Universal paragraph role and function quality assessment.
    Evaluates intro/body/conclusion against role requirements.
    """
    n_paras = len(paragraph_map)
    results  = []

    for para in paragraph_map:
        pidx  = para["paragraph_index"]
        psents = _para_sentences(para, sentence_map)
        lang_ev = _language_evaluability(psents, sent_control)

        # Role detection
        if pidx == 0:
            role = "introduction"
        elif pidx == n_paras - 1:
            role = "conclusion"
        else:
            role = "body"

        # Check requirements
        if lang_ev == "blocked":
            met, missing = [], ["all_requirements_blocked_by_language"]
            alerts = [{
                "alert_type": "paragraph_meaning_low_due_to_language_corruption",
                "severity": "red",
                "evidence_quote": para["text"][:200],
                "student_hint": "Rewrite the sentences in this paragraph before checking the overall structure.",
            }]
        elif role == "introduction":
            met, missing, alerts = _check_intro(psents)
        elif role == "conclusion":
            met, missing, alerts = _check_conclusion(psents)
        else:
            met, missing, alerts = _check_body(psents)

        # Function status from alerts + language evaluability
        has_red   = any(a["severity"] == "red"    for a in alerts)
        has_yel   = any(a["severity"] == "yellow"  for a in alerts)
        if lang_ev == "blocked" or has_red:
            func_status = "red"
            rev_status  = "rewrite"
        elif lang_ev == "partial" or has_yel or missing:
            # partial lang_ev (heavy yellow sentences) floors paragraph to yellow
            func_status = "yellow"
            rev_status  = "improve"
        else:
            func_status = "green"
            rev_status  = "keep"

        hint = ""
        if alerts:
            hint = alerts[0]["student_hint"]

        results.append({
            "paragraph_index":                pidx,
            "paragraph_role":                 role,
            "paragraph_function_status":      func_status,
            "revision_status":                rev_status,
            "role_requirements_met":          met,
            "role_requirements_missing":      missing,
            "function_alerts":                alerts,
            "language_evaluability":          lang_ev,
            "student_safe_hint":              hint,
            "confidence":                     0.55 if lang_ev == "blocked" else 0.75,
        })

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# v8.0 ESSAY FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def assess_essay_function(
    paragraph_map: List[Dict],
    sentence_map:  List[Dict],
    para_func:     List[Dict],
) -> Dict[str, Any]:
    '''
    v8.1 — Universal essay-level structural check.

    The A/D-specific coverage check is removed: task coverage is the
    detector's responsibility (task_profile.covered_required_components).
    This function measures structural execution: position consistency and
    essay progression. Discourse quality depth ratings come from Modules
    A-H which run afterwards in evaluate().
    '''
    n_paras    = len(paragraph_map)
    pf_map     = {p["paragraph_index"]: p for p in para_func}
    intro_func = pf_map.get(0, {})
    conc_func  = pf_map.get(n_paras - 1, {})

    intro_has_pos  = "position_stated"       in intro_func.get("role_requirements_met", [])
    conc_has_pos   = "final_position_stated"  in conc_func.get("role_requirements_met", [])
    intro_has_prev = "main_argument_preview"  in intro_func.get("role_requirements_met", [])

    task_response_status = (
        "red"    if intro_func.get("paragraph_function_status") == "red" else
        "yellow" if not intro_has_pos else
        "green"
    )
    position_consistency_status = (
        "green"  if intro_has_pos and conc_has_pos else
        "yellow" if intro_has_pos or conc_has_pos else
        "red"
    )
    pf_statuses = [p.get("paragraph_function_status","unknown") for p in para_func]
    if all(s == "green" for s in pf_statuses):
        progression_status = "green"
    elif any(s == "red" for s in pf_statuses):
        progression_status = "red"
    else:
        progression_status = "yellow"

    return {
        "task_response_status":        task_response_status,
        "position_consistency_status": position_consistency_status,
        "overall_progression_status":  progression_status,
        "intro_has_position":          intro_has_pos,
        "intro_has_preview":           intro_has_prev,
        "conclusion_has_position":     conc_has_pos,
        "intro_met":     intro_func.get("role_requirements_met", []),
        "intro_missing": intro_func.get("role_requirements_missing", []),
        "conclusion_met":     conc_func.get("role_requirements_met", []),
        "conclusion_missing": conc_func.get("role_requirements_missing", []),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# v8.0 EXAMPLE QUALITY
# ═══════════════════════════════════════════════════════════════════════════════

def detect_example_quality(
    sentence_map:  List[Dict],
    argument_map:  Dict,
) -> List[Dict]:
    """
    v8.0 — Classify each example sentence by type and IELTS suitability.
    Flags personal anecdotes and unexplained examples.
    """
    example_candidates = {e["sentence_index"] for e in argument_map.get("example_candidates", [])}
    results = []

    for sent in sentence_map:
        sidx = sent["sentence_index"]
        text = sent["text"]
        low  = text.lower()

        if not (sent.get("has_example_cue") or sidx in example_candidates):
            continue

        # Example type
        is_personal = bool(_SC_PERSONAL.search(text))
        is_country  = bool(re.search(
            r"\b(in [A-Z][a-z]+(,|\s)|in (Japan|Finland|Sweden|Germany|"
            r"Singapore|South Korea|the UK|the US|the USA|China|Australia|"
            r"Canada|France|India|Brazil|Norway|Denmark|New Zealand)|"
            r"government policy|public (programme|program|service|funding)|"
            r"according to research|studies (show|suggest))\b", text))
        is_hypothetical = bool(re.search(r"\b(if|imagine|suppose|consider|let's say|hypothetically)\b", low))
        is_vague = bool(_SC_VAGUE_EXPR.search(low)) or bool(
            re.search(r"\b(many (people|countries)|some (people|places)|most (people|countries))\b", low))

        if is_personal:
            ex_type = "personal_anecdote"
        elif is_country:
            ex_type = "country_policy_example"
        elif is_hypothetical:
            ex_type = "hypothetical"
        elif is_vague:
            ex_type = "unsupported_generalization"
        else:
            ex_type = "social_example"

        # IELTS suitability
        if is_personal:
            suitability = "red"
            alert = "example_too_personal_for_academic_task"
            hint = ("Keep the idea, but use a wider social example — for instance, "
                    "a type of programme, institution, or social trend.")
        elif is_vague:
            suitability = "yellow"
            alert = "example_too_vague"
            hint = "Make the example more specific — name a place, programme, or situation."
        elif is_country:
            suitability = "green"
            alert = "none"
            hint = ""
        else:
            suitability = "yellow"
            alert = "none"
            hint = "Make sure the example is connected to your main point."

        # Integration check: is there a follow-up explanation sentence?
        next_sent = next(
            (s for s in sentence_map if s["sentence_index"] == sidx + 1), None)
        if next_sent and not next_sent.get("has_example_cue") and not next_sent.get("has_conclusion_cue"):
            integration = "integrated"
        else:
            integration = "not_explained"
            if suitability == "green":
                suitability = "yellow"
            if alert == "none":
                alert = "example_not_explained"
                hint = "After the example, add a sentence that explains how it supports your point."

        results.append({
            "paragraph_index":             sent["paragraph_index"],
            "sentence_index":              sidx,
            "example_text":                text,
            "example_type":                ex_type,
            "integration_status":          integration,
            "ielts_suitability":           suitability,
            "alert_type":                  alert,
            "student_hint":                hint,
            "model_example_visible_before_revision": False,
        })

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# v8.0 ESSAY REVISION CONTROL PAYLOAD ASSEMBLER
# ═══════════════════════════════════════════════════════════════════════════════


# ================================================================
# v8.1 DISCOURSE QUALITY MODULES  (injected by patch_v81.py)
# ================================================================

# ═══════════════════════════════════════════════════════════════════════════════
# v8.1 DISCOURSE QUALITY — CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# Module A — Position clarity structural patterns
_POS_BARE_POLARITY = re.compile(
    r'\b(good|bad|right|wrong|important|necessary|beneficial|positive|negative|'
    r'better|worse|helpful|harmful|effective|useful|serious|significant)\s*[.!?]',
    re.I
)
_POS_COMPARISON = re.compile(
    r'\b(better than|worse than|more .{1,30} than|less .{1,30} than|'
    r'outweigh|prefer(?:.{0,5}) over|rather than|superior to|inferior to)\b', re.I
)
_POS_CONDITION = re.compile(
    r'\b(to some extent|depends on|in some cases|under certain '
    r'circumstances|in most situations|for the most part)\b', re.I
)
_POS_DEGREE_ADVERB = re.compile(
    r'\b(significantly|largely|primarily|mainly|mostly|greatly|'
    r'considerably|particularly|especially|strongly|firmly)\b', re.I
)
_POS_AGENT_NOUN = re.compile(
    r'\b(government|society|people|individuals|schools|employers|'
    r'authorities|communities|organisations?|institutions?|companies|'
    r'countries|nations|citizens|parents|teachers|students|workers)\b', re.I
)

# Module B — Idea density / subject extraction
_B_ANAPHOR   = re.compile(r'^(he|she|it|they|them|their|this|these|that|those|such)\b', re.I)
_B_DET_STRIP = re.compile(r'^(the|a|an|this|these|that|those|his|her|its|their|our|my|your)\s+', re.I)
_B_STOP      = frozenset({
    'the','a','an','of','to','in','on','for','with','at','from','by',
    'is','are','was','were','be','been','being','it','and','but','or',
    'if','not','no','so','then','also','even','both','all','only',
    'each','every','any','more','most','very','just','still','as',
    'when','where','which','who','what','how','there','here','its',
    # discourse connectors — sentence-initial connectors signal CONTINUATION
    'since','therefore','thus','hence','however','moreover','furthermore',
    'additionally','consequently','nevertheless','nonetheless','meanwhile',
    'indeed','besides','otherwise','accordingly','alternatively',
})

# Module C — CREE depth patterns
_C_EX_OPENER = re.compile(
    r'^(?:for example[,:]?\s*|for instance[,:]?\s*|such as\s+|'
    r'to illustrate[,:]?\s*|a (?:good |clear )?example (?:of this )?is\s*|'
    r'one example (?:of this )?is\s*|another example is\s*)',
    re.I
)
_C_REASON_PAT = re.compile(
    r'\b(because|since(?! \d)|as a result|therefore|this is because|'
    r'this means that|which means|leading to|due to|owing to|'
    r'hence|consequently|for this reason)\b', re.I
)
_C_EXPL_OPENER = re.compile(
    r'^(?:this (?:show|demonstrat|mean|suggest|prov|indicat|highlight)\w*|'
    r'therefore[,\s]|thus[,\s]|hence[,\s]|as a result[,\s]|'
    r'it (?:show|demonstrat|mean|suggest|prov|indicat)\w*|'
    r'these (?:show|demonstrat|mean|suggest|prov|indicat)\w*)',
    re.I
)
_C_CONTRAST_OP = re.compile(
    r'^(?:however|on the other hand|in contrast|nevertheless|'
    r'despite|although|even though|yet\b|but )\b', re.I
)
_C_SPECIFIC = re.compile(
    r'\b[A-Z][a-z]{2,}\b|\b(?:19|20)\d{2}\b'
    r'|\b\d+(?:\.\d+)?\s*(?:%|per cent|percent|million|billion|thousand)\b'
)
_C_PERSONAL = re.compile(
    r'\b(my |i (?:have|had|went|used|saw|felt|know|think|believe|went|'
    r'remember|grew|studied|work)|'
    r'my (?:friend|family|brother|sister|mother|father|parents|teacher|'
    r'school|experience|childhood|life|country|hometown|'
    r'grandfather|grandmother|uncle|aunt))\b', re.I
)

# Module E — Reference resolution
_E_SING = re.compile(r'\b(he|she|it)\b', re.I)
_E_PLUR = re.compile(r'\b(they|them|their)\b', re.I)
_E_DEMO = re.compile(r'\b(this|these|that|those|such)\b', re.I)
_E_CATA = re.compile(r'\b(?:this|these|that|those)\s+\w+', re.I)
_E_FP   = re.compile(r'\b(i|we|you|my|our|your)\b', re.I)

# Module F — Transition correctness
_F_CONTRAST   = re.compile(
    r'^(?:however|on the other hand|in contrast|nevertheless|yet|'
    r'conversely|by contrast|that said)[,.\s]', re.I
)
_F_ADDITION   = re.compile(
    r'^(?:furthermore|in addition|moreover|additionally|besides|'
    r'what is more|also[,])[,.\s]', re.I
)
_F_CAUSAL_FWD = re.compile(
    r'^(?:therefore|thus|hence|as a result|consequently|'
    r'for this reason|this means that|this is why)[,.\s]', re.I
)
_F_CONCESS = re.compile(
    r'^(?:although|even though|while\s|whereas|despite the fact)\b', re.I
)
_F_CAUSE_ANT = re.compile(
    r'\b(?:because|since\b|if\s|when\s|as long as|provided that|'
    r'due to|owing to|given that)\b', re.I
)
_F_NEGATION = re.compile(
    r"\b(?:not|never|no\s|neither|nor\s|hardly|barely|scarcely|"
    r"cannot|can't|won't|shouldn't|wouldn't|couldn't|doesn't|"
    r"don't|didn't|hasn't|haven't|wasn't|weren't|n't)\b", re.I
)
_F_ANY_MARKER = re.compile(
    r'^(?:however|furthermore|in addition|moreover|additionally|'
    r'therefore|thus|hence|as a result|consequently|nevertheless|'
    r'on the other hand|in contrast|although|even though|while\s|'
    r'besides|conversely|that said|by contrast|also[,]|what is more)'
    r'[,.\s]',
    re.I
)

# ── Depth constants ────────────────────────────────────────────────────────────
DQ_DEPTH_0 = 0   # absent / non-functional
DQ_DEPTH_1 = 1   # present but minimal / slot-only
DQ_DEPTH_2 = 2   # partial — present and partially effective
DQ_DEPTH_3 = 3   # full — sufficiently executed at evident band level


def _depth_label(d: int) -> str:
    return {0: "DEPTH_0", 1: "DEPTH_1", 2: "DEPTH_2", 3: "DEPTH_3"}.get(d, "DEPTH_0")


def _subj_head(text: str) -> Optional[str]:
    '''
    Extract the main referent head word from a sentence for topic-chain tracking.
    Returns None if the sentence opens with an anaphoric pronoun/demonstrative
    (signals CONTINUE of prior topic). Uses simple heuristics — no NLP required.
    '''
    stripped = text.strip()
    if not stripped:
        return None
    if _B_ANAPHOR.match(stripped):
        return None
    cleaned = _B_DET_STRIP.sub('', stripped, count=1)
    first = re.split(r'[\s,;:]+', cleaned, maxsplit=1)[0].lower()
    first = first[:-2] if first.endswith("'s") else first
    if first in _B_STOP or len(first) < 3:
        return None
    return first


# ═══════════════════════════════════════════════════════════════════════════════
# v8.1 MODULE A — Position Clarity
# ═══════════════════════════════════════════════════════════════════════════════

def assess_position_clarity(
    intro_para_sents: List[Dict],
    intro_met:        List[str],
) -> Dict[str, Any]:
    '''
    v8.1 Module A — Position clarity depth rating for the introduction.

    Measures whether the stated position is structurally specific or vague.
    Structural patterns only — no topic vocabulary.

    Depth mapping:
      DEPTH_3 — 2+ specificity signals, 0 vagueness
      DEPTH_2 — 1+ specificity OR ≤1 vagueness
      DEPTH_1 — 2+ vagueness, 0 specificity
      DEPTH_0 — no position found at all
    '''
    if 'position_stated' not in intro_met:
        return {
            'position_clarity': _depth_label(DQ_DEPTH_0),
            'vagueness_signals': ['VG_NO_POSITION'],
            'specificity_signals': [],
            'position_sentence_index': None,
            '_depth_int': DQ_DEPTH_0,
        }

    pos_sent = None
    pos_idx  = None
    for s in intro_para_sents:
        if s.get('has_position_cue'):
            pos_sent = s['text']
            pos_idx  = s['sentence_index']
    if pos_sent is None and intro_para_sents:
        pos_sent = intro_para_sents[-1]['text']
        pos_idx  = intro_para_sents[-1]['sentence_index']

    text = pos_sent or ''
    low  = text.lower()
    vagueness:   List[str] = []
    specificity: List[str] = []

    if _POS_BARE_POLARITY.search(text):
        vagueness.append('VG_BARE_POLARITY')
    if not _POS_AGENT_NOUN.search(low):
        vagueness.append('VG_NO_AGENT')
    if not _POS_DEGREE_ADVERB.search(low) and not _POS_CONDITION.search(low):
        vagueness.append('VG_NO_QUANTIFIER')

    if _POS_COMPARISON.search(low):
        specificity.append('SP_COMPARISON')
    if _POS_CONDITION.search(low):
        specificity.append('SP_CONDITION')
    if _POS_AGENT_NOUN.search(low):
        specificity.append('SP_AGENTS')
    if _POS_DEGREE_ADVERB.search(low):
        specificity.append('SP_DEGREE')

    n_vag = len(vagueness)
    n_sp  = len(specificity)

    if n_sp >= 2 and n_vag == 0:
        depth = DQ_DEPTH_3
    elif n_sp >= 1 or n_vag <= 1:
        depth = DQ_DEPTH_2
    else:
        depth = DQ_DEPTH_1

    return {
        'position_clarity': _depth_label(depth),
        'vagueness_signals': vagueness,
        'specificity_signals': specificity,
        'position_sentence_index': pos_idx,
        '_depth_int': depth,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# v8.1 MODULE B — Paragraph Idea Density
# ═══════════════════════════════════════════════════════════════════════════════

def assess_idea_density(
    para_sents:    List[Dict],
    paragraph_index: int = 0,
) -> Dict[str, Any]:
    '''
    v8.1 Module B — Topic chain continuity within a body paragraph.

    Detects whether the paragraph pursues one coherent idea or contains
    multiple competing referents. Subject-head heuristic — no NLP required.

    shift_rate:
      ≤ 0.20 → DEPTH_3 (focused)
      ≤ 0.40 → DEPTH_2 (moderate drift)
      ≤ 0.60 → DEPTH_1 (scattered)
      >  0.60 → DEPTH_0 (chaotic)
    '''
    if len(para_sents) < 3:
        return {
            'idea_density': _depth_label(DQ_DEPTH_3),
            'shift_rate': 0.0,
            'n_shifts': 0,
            'n_transitions': 0,
            'paragraph_index': paragraph_index,
            '_depth_int': DQ_DEPTH_3,
        }

    heads: List[Optional[str]] = [_subj_head(s['text']) for s in para_sents]
    seen_heads: set = set()
    n_shifts = 0
    n_transitions = 0

    for i in range(1, len(heads)):
        n_transitions += 1
        h_prev = heads[i - 1]
        h_curr = heads[i]

        if h_curr is None:          # anaphoric pronoun → CONTINUE
            if h_prev:
                seen_heads.add(h_prev)
            continue

        if h_curr in seen_heads or h_curr == h_prev:
            seen_heads.add(h_curr)  # CONTINUE or RETURN
        else:
            n_shifts += 1           # SHIFT
            seen_heads.add(h_curr)

        if h_prev:
            seen_heads.add(h_prev)

    shift_rate = n_shifts / n_transitions if n_transitions > 0 else 0.0

    if shift_rate <= 0.20:
        depth = DQ_DEPTH_3
    elif shift_rate <= 0.40:
        depth = DQ_DEPTH_2
    elif shift_rate <= 0.60:
        depth = DQ_DEPTH_1
    else:
        depth = DQ_DEPTH_0

    return {
        'idea_density': _depth_label(depth),
        'shift_rate': round(shift_rate, 3),
        'n_shifts': n_shifts,
        'n_transitions': n_transitions,
        'paragraph_index': paragraph_index,
        '_depth_int': depth,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# v8.1 MODULE C — CREE Chain Depth
# ═══════════════════════════════════════════════════════════════════════════════

def assess_cree_depth(
    para_sents:   List[Dict],
    para_idx:     int,
    ex_qual_map:  Dict[int, Dict],
) -> Dict[str, Any]:
    '''
    v8.1 Module C — CREE chain depth score per body paragraph.

    Layers (raw depth score 0–4 → DEPTH_0–3):
      C  claim    — paragraph opens without example/contrast opener
      R  reason   — causal connective clause ≥5 tokens after claim
      E  example  — example marker or specific-referent sentence after reason
      X  explain  — explanation sentence after example with backward reference

    Caps:
      personal anecdote example  → max DEPTH_2
      weak reason (< 5 tokens)   → max DEPTH_2
    '''
    if not para_sents:
        return {
            'cree_depth': _depth_label(DQ_DEPTH_0), 'raw_depth_score': 0,
            'layers_present': [], 'reason_weak': False,
            'example_weak': False, 'example_personal': False,
            'paragraph_index': para_idx, '_depth_int': DQ_DEPTH_0,
        }

    layers: List[str] = []
    reason_weak      = False
    example_weak     = False
    example_personal = False

    # Layer C — claim sentence
    first = para_sents[0]['text']
    if (not _C_EX_OPENER.match(first) and
            not _C_CONTRAST_OP.match(first) and
            len(words(first)) >= 4):
        layers.append('C')

    # Layer R — reason (must not be first sentence)
    reason_idx = None
    for i, s in enumerate(para_sents):
        if i == 0:
            continue
        if _C_REASON_PAT.search(s['text']):
            reason_weak = len(words(s['text'])) < 5
            layers.append('R')
            reason_idx = i
            break

    # Layer E — example (must follow reason if reason found)
    example_idx = None
    for i, s in enumerate(para_sents):
        if reason_idx is not None and i <= reason_idx:
            continue
        txt = s['text']
        has_ex   = bool(s.get('has_example_cue')) or bool(_C_EX_OPENER.match(txt))
        has_spec = bool(_C_SPECIFIC.search(txt))
        if has_ex or has_spec:
            eq = ex_qual_map.get(s['sentence_index'], {})
            if eq.get('example_type') == 'personal_anecdote' or _C_PERSONAL.search(txt):
                example_personal = True
                example_weak     = True
            elif eq.get('suitability') == 'red':
                example_weak = True
            layers.append('E')
            example_idx = i
            break

    # Layer X — explanation after example
    if example_idx is not None and example_idx < len(para_sents) - 1:
        for s in para_sents[example_idx + 1:]:
            txt = s['text']
            if _C_EX_OPENER.match(txt):
                continue
            if _C_EXPL_OPENER.match(txt) and len(words(txt)) >= 5:
                layers.append('X')
                break

    raw_score = len([l for l in layers if l in ('C', 'R', 'E', 'X')])
    depth = DQ_DEPTH_3 if raw_score == 4 else DQ_DEPTH_2 if raw_score >= 2 else DQ_DEPTH_1 if raw_score == 1 else DQ_DEPTH_0

    if example_personal and depth > DQ_DEPTH_2:
        depth = DQ_DEPTH_2
    if reason_weak and depth > DQ_DEPTH_2:
        depth = DQ_DEPTH_2

    return {
        'cree_depth': _depth_label(depth),
        'raw_depth_score': raw_score,
        'layers_present': layers,
        'reason_weak': reason_weak,
        'example_weak': example_weak,
        'example_personal': example_personal,
        'paragraph_index': para_idx,
        '_depth_int': depth,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# v8.1 MODULE D — Development Coverage (universal, task-type-agnostic)
# ═══════════════════════════════════════════════════════════════════════════════

def assess_development_coverage(cree_results: List[Dict]) -> Dict[str, Any]:
    '''
    v8.1 Module D — Universal argument development coverage.

    Aggregates CREE depth across all body paragraphs.
    Task-type-agnostic: the detector owns task-type classification and
    required-component existence checking. This module measures HOW DEEPLY
    each body paragraph develops its content — the same skill regardless of
    whether the essay is opinion, advantages/disadvantages, causes/solutions, etc.

    coverage_rate = fraction of body paragraphs at DEPTH_2 or higher.
    '''
    if not cree_results:
        return {
            'dev_coverage': _depth_label(DQ_DEPTH_0), 'coverage_rate': 0.0,
            'depth_distribution': {'DEPTH_0': 0, 'DEPTH_1': 0, 'DEPTH_2': 0, 'DEPTH_3': 0},
            'n_body_paragraphs': 0, '_depth_int': DQ_DEPTH_0,
        }

    depths = [r.get('_depth_int', 0) for r in cree_results]
    n = len(depths)
    dist = {
        'DEPTH_0': sum(1 for d in depths if d == 0),
        'DEPTH_1': sum(1 for d in depths if d == 1),
        'DEPTH_2': sum(1 for d in depths if d == 2),
        'DEPTH_3': sum(1 for d in depths if d == 3),
    }
    n_deep    = dist['DEPTH_3']
    n_partial = dist['DEPTH_2']
    coverage_rate = (n_deep + n_partial) / n

    if coverage_rate >= 0.75 and n_deep >= 1:
        depth = DQ_DEPTH_3
    elif coverage_rate >= 0.50:
        depth = DQ_DEPTH_2
    elif coverage_rate >= 0.25 or n_partial >= 1:
        depth = DQ_DEPTH_1
    else:
        depth = DQ_DEPTH_0

    return {
        'dev_coverage': _depth_label(depth),
        'coverage_rate': round(coverage_rate, 3),
        'depth_distribution': dist,
        'n_body_paragraphs': n,
        '_depth_int': depth,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# v8.1 MODULE E — Reference Resolution Quality
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_content_nouns(text: str) -> List[str]:
    '''Return lowercase content words (len >= 4, not in STOPWORDS) from text.'''
    return [
        w.lower().rstrip('s')
        for w in re.findall(r'\b[a-zA-Z]{4,}\b', text)
        if w.lower() not in STOPWORDS
    ]


def assess_ref_quality(
    para_sents:     List[Dict],
    paragraph_index: int = 0,
) -> Dict[str, Any]:
    '''
    v8.1 Module E — Reference resolution quality within a paragraph.

    Checks whether pronoun/demonstrative references in subject position have
    a resolvable antecedent in the preceding 1-2 sentences.
    Excludes first-person pronouns (always resolvable in IELTS context).
    Cataphoric demonstratives (this approach, these factors) are always resolved.

    broken_rate:
      0.0       → DEPTH_3
      ≤ 0.20    → DEPTH_2
      ≤ 0.40    → DEPTH_1
      > 0.40    → DEPTH_0
    '''
    broken_refs: List[Dict] = []
    n_checked = 0

    for i, sent in enumerate(para_sents):
        text     = sent['text']
        low      = text.lower()
        stripped = text.strip()

        if _E_FP.match(low.strip()):
            continue

        candidates = []
        if _E_SING.match(low.strip()):
            candidates.append(('pronoun_singular', _E_SING.match(low.strip()).group(0)))
        if _E_PLUR.match(low.strip()):
            candidates.append(('pronoun_plural', _E_PLUR.match(low.strip()).group(0)))
        # Demonstrative in subject position but NOT followed immediately by an NP (cataphoric)
        m_demo = _E_DEMO.match(low.strip())
        if m_demo and not _E_CATA.match(stripped):
            candidates.append(('demonstrative', m_demo.group(0)))

        for ref_type, token in candidates:
            n_checked += 1
            prior_nouns: set = set()
            for j in range(max(0, i - 2), i):
                prior_nouns.update(_extract_content_nouns(para_sents[j]['text']))

            if i == 0:
                broken_refs.append({
                    'sentence_index': sent['sentence_index'],
                    'token': token, 'ref_type': ref_type,
                    'reason': 'paragraph_initial_reference_no_antecedent',
                })
            elif not prior_nouns:
                broken_refs.append({
                    'sentence_index': sent['sentence_index'],
                    'token': token, 'ref_type': ref_type,
                    'reason': 'no_content_nouns_in_prior_sentences',
                })

    broken_rate = len(broken_refs) / n_checked if n_checked > 0 else 0.0

    if n_checked == 0:
        depth = DQ_DEPTH_3
    elif broken_rate == 0.0:
        depth = DQ_DEPTH_3
    elif broken_rate <= 0.20:
        depth = DQ_DEPTH_2
    elif broken_rate <= 0.40:
        depth = DQ_DEPTH_1
    else:
        depth = DQ_DEPTH_0

    return {
        'ref_quality': _depth_label(depth),
        'broken_rate': round(broken_rate, 3),
        'broken_refs': broken_refs,
        'n_checked': n_checked,
        'paragraph_index': paragraph_index,
        '_depth_int': depth,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# v8.1 MODULE F — Transition Correctness
# ═══════════════════════════════════════════════════════════════════════════════

def assess_transition_quality(sentence_map: List[Dict]) -> Dict[str, Any]:
    '''
    v8.1 Module F — Discourse marker usage correctness.

    For each sentence opening with a recognised discourse marker, checks
    whether the marker fits the semantic role of that sentence relative to
    its predecessor. Purely structural — no topic vocabulary.

    CONTRAST_NO_POLARITY_SHIFT  — contrast marker with no polarity change
    ADDITION_TOPIC_SHIFT        — addition marker but referent changes
    CAUSAL_NO_ANTECEDENT        — causal-forward with no cause in prior sentence
    CONCESSION_INCOMPLETE       — subordinating concession with no main clause

    misuse_rate:
      0         → DEPTH_3    (all correct OR no markers at all)
      ≤ 0.25    → DEPTH_2
      ≤ 0.50    → DEPTH_1
      > 0.50    → DEPTH_0
    n_markers == 0 → DEPTH_1  (absence is a separate signal)
    '''
    misused:   List[Dict] = []
    n_markers = 0

    for i, sent in enumerate(sentence_map):
        text = sent['text']
        low  = text.strip().lower()
        if not _F_ANY_MARKER.match(text.strip()):
            continue
        n_markers += 1

        prior_text = sentence_map[i - 1]['text'] if i > 0 else ''
        prior_low  = prior_text.lower()
        flag = None

        if _F_CONTRAST.match(text.strip()):
            prior_neg = bool(_F_NEGATION.search(prior_low))
            curr_neg  = bool(_F_NEGATION.search(low))
            has_cmp   = bool(re.search(r'\bthan\b|\bunlike\b|\bwhereas\b', low))
            if not has_cmp and (prior_neg == curr_neg):
                flag = 'CONTRAST_NO_POLARITY_SHIFT'

        elif _F_ADDITION.match(text.strip()):
            curr_head  = _subj_head(text)
            prior_head = _subj_head(prior_text)
            if curr_head and prior_head and curr_head != prior_head:
                flag = 'ADDITION_TOPIC_SHIFT'

        elif _F_CAUSAL_FWD.match(text.strip()):
            if prior_text and not _F_CAUSE_ANT.search(prior_low):
                flag = 'CAUSAL_NO_ANTECEDENT'

        elif _F_CONCESS.match(text.strip()):
            if len(re.split(r'[,;]', text)) < 2:
                flag = 'CONCESSION_INCOMPLETE'

        if flag:
            misused.append({
                'sentence_index': sent['sentence_index'],
                'marker': text.strip().split()[0].lower(),
                'flag': flag,
            })

    misuse_rate = len(misused) / n_markers if n_markers > 0 else 0.0

    if n_markers == 0:
        depth = DQ_DEPTH_1
    elif misuse_rate == 0.0:
        depth = DQ_DEPTH_3
    elif misuse_rate <= 0.25:
        depth = DQ_DEPTH_2
    elif misuse_rate <= 0.50:
        depth = DQ_DEPTH_1
    else:
        depth = DQ_DEPTH_0

    return {
        'transition_quality': _depth_label(depth),
        'misuse_rate': round(misuse_rate, 3),
        'misused_markers': misused,
        'n_markers': n_markers,
        '_depth_int': depth,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# v8.1 MODULE G — Conclusion Argument Alignment
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_body_claim_heads(
    paragraph_map: List[Dict],
    sentence_map:  List[Dict],
    cree_results:  List[Dict],
) -> set:
    '''
    Collect main content nouns from body paragraph claim (opening) sentences.
    Used by Module G to verify conclusion references what was argued in body.
    '''
    n_paras      = len(paragraph_map)
    claim_heads: set = set()
    cree_by_pidx = {r['paragraph_index']: r for r in cree_results}

    for para in paragraph_map:
        pidx = para['paragraph_index']
        if pidx == 0 or pidx == n_paras - 1:
            continue
        s_idxs    = set(para.get('sentence_indices', []))
        para_snts = [s for s in sentence_map if s['sentence_index'] in s_idxs]
        if not para_snts:
            continue
        cr = cree_by_pidx.get(pidx, {})
        if 'C' not in cr.get('layers_present', []):
            continue
        claim_heads.update(_extract_content_nouns(para_snts[0]['text']))

    return claim_heads


def assess_conclusion_alignment(
    conc_sents:       List[Dict],
    body_claim_heads: set,
) -> Dict[str, Any]:
    '''
    v8.1 Module G — Conclusion argument alignment.

    Detects whether the conclusion introduces claims absent from body paragraphs
    vs. restating/summarising what was actually argued.

    new_content_rate:
      0 + restated  → DEPTH_3
      ≤ 0.33        → DEPTH_2
      ≤ 0.50        → DEPTH_1
      > 0.50        → DEPTH_0
    '''
    if not conc_sents:
        return {
            'conclusion_alignment': _depth_label(DQ_DEPTH_0), 'new_content_rate': 1.0,
            'final_position_restated': False, 'new_content_sentences': [],
            '_depth_int': DQ_DEPTH_0,
        }

    if len(body_claim_heads) < 3:
        return {
            'conclusion_alignment': _depth_label(DQ_DEPTH_2), 'new_content_rate': 0.0,
            'final_position_restated': bool(
                conc_sents[-1].get('has_position_cue') or
                conc_sents[-1].get('has_conclusion_cue')
            ),
            'new_content_sentences': [],
            'note': 'inconclusive_insufficient_body_claim_heads',
            '_depth_int': DQ_DEPTH_2,
        }

    final_restated = bool(
        conc_sents[-1].get('has_position_cue') or
        conc_sents[-1].get('has_conclusion_cue') or
        re.search(
            r'\b(overall|in conclusion|in summary|to conclude|therefore|in short|ultimately)\b',
            conc_sents[-1]['text'], re.I
        )
    )

    new_content: List[int] = []
    n_content = 0

    for s in conc_sents:
        txt = s['text']
        if _F_ANY_MARKER.match(txt.strip()):
            continue
        if _B_ANAPHOR.match(txt.strip()):
            continue
        n_content += 1
        sent_heads = set(_extract_content_nouns(txt))
        if not sent_heads:
            continue
        overlap = len(sent_heads & body_claim_heads) / len(sent_heads)
        if overlap < 0.25:
            new_content.append(s['sentence_index'])

    ncr = len(new_content) / n_content if n_content > 0 else 0.0

    if ncr == 0.0 and final_restated:
        depth = DQ_DEPTH_3
    elif ncr <= 0.33:
        depth = DQ_DEPTH_2
    elif ncr <= 0.50:
        depth = DQ_DEPTH_1
    else:
        depth = DQ_DEPTH_0

    return {
        'conclusion_alignment': _depth_label(depth),
        'new_content_rate': round(ncr, 3),
        'final_position_restated': final_restated,
        'new_content_sentences': new_content,
        '_depth_int': depth,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# v8.1 MODULE H — Cohesion Quality
# ═══════════════════════════════════════════════════════════════════════════════

def assess_cohesion_quality(
    transition_depth: int,
    ref_depth:        int,
    n_markers:        int,
    n_sentences:      int,
) -> Dict[str, Any]:
    '''
    v8.1 Module H — Combined cohesion quality.

    Combines transition correctness (F) + device density + ref quality (E).
    cohesion_quality = min(transition_depth, device_density_depth, ref_depth).

    Device density appropriate range: 0.15–0.40 markers/sentence.
    '''
    density = n_markers / n_sentences if n_sentences > 0 else 0.0
    if density == 0:
        dd = DQ_DEPTH_1
    elif density <= 0.15:
        dd = DQ_DEPTH_2
    elif density <= 0.40:
        dd = DQ_DEPTH_3
    else:
        dd = DQ_DEPTH_2

    combined = min(transition_depth, dd, ref_depth)
    return {
        'cohesion_quality': _depth_label(combined),
        'device_density': _depth_label(dd),
        'density_rate': round(density, 3),
        'combined_depth': combined,
        '_depth_int': combined,
    }


# ── Discourse quality payload assembler ───────────────────────────────────────

def build_discourse_quality_payload(
    pos_clarity:   Dict,
    body_density:  List[Dict],
    cree_results:  List[Dict],
    dev_coverage:  Dict,
    essay_ref_depth: int,
    trans_q:       Dict,
    conc_align:    Dict,
    cohesion_q:    Dict,
) -> Dict[str, Any]:
    '''
    Assemble the discourse_quality sub-object for the ERCP.
    Computes summary_flags for writing coach note population.
    '''
    flags: List[str] = []
    if pos_clarity.get('_depth_int', DQ_DEPTH_3) <= DQ_DEPTH_1:
        flags.append('VAGUE_POSITION')
    for r in cree_results:
        if r.get('_depth_int', DQ_DEPTH_3) <= DQ_DEPTH_1:
            flags.append(f"CREE_THIN_P{r.get('paragraph_index','?')}")
    for bd in body_density:
        if bd.get('_depth_int', DQ_DEPTH_3) <= DQ_DEPTH_1:
            flags.append(f"SCATTERED_IDEAS_P{bd.get('paragraph_index','?')}")
    if conc_align.get('_depth_int', DQ_DEPTH_3) <= DQ_DEPTH_1:
        if conc_align.get('new_content_sentences'):
            flags.append('NEW_CLAIM_IN_CONCLUSION')
        if not conc_align.get('final_position_restated', True):
            flags.append('MISSING_FINAL_POSITION')
    if trans_q.get('_depth_int', DQ_DEPTH_3) <= DQ_DEPTH_1:
        flags.append('TRANSITION_MISUSE')
    if cohesion_q.get('_depth_int', DQ_DEPTH_3) <= DQ_DEPTH_1:
        flags.append('LOW_COHESION')

    return {
        'schema_version': 'DISCOURSE_QUALITY_V1_0',
        'position_clarity': pos_clarity.get('position_clarity', 'DEPTH_3'),
        'vagueness_signals': pos_clarity.get('vagueness_signals', []),
        'specificity_signals': pos_clarity.get('specificity_signals', []),
        'body_paragraphs': [
            {
                'paragraph_index': cr['paragraph_index'],
                'idea_density': next(
                    (bd.get('idea_density', 'DEPTH_3')
                     for bd in body_density
                     if bd.get('paragraph_index') == cr['paragraph_index']),
                    'DEPTH_3'
                ),
                'cree_depth':       cr.get('cree_depth', 'DEPTH_0'),
                'ref_quality':      _depth_label(essay_ref_depth),
                'layers_present':   cr.get('layers_present', []),
                'example_personal': cr.get('example_personal', False),
            }
            for cr in cree_results
        ],
        'transition_quality':   trans_q.get('transition_quality', 'DEPTH_3'),
        'misused_markers':      trans_q.get('misused_markers', []),
        'conclusion_alignment': conc_align.get('conclusion_alignment', 'DEPTH_3'),
        'new_content_sentences': conc_align.get('new_content_sentences', []),
        'final_position_restated': conc_align.get('final_position_restated', True),
        'dev_coverage':    dev_coverage.get('dev_coverage', 'DEPTH_3'),
        'coverage_rate':   dev_coverage.get('coverage_rate', 1.0),
        'cohesion_quality': cohesion_q.get('cohesion_quality', 'DEPTH_3'),
        'summary_flags': flags,
    }



def build_essay_revision_control_payload(
    maps:              Dict,
    sent_control:      List[Dict],
    para_func:         List[Dict],
    essay_func:        Dict,
    example_quality:   List[Dict],
    task_type:         str = "unknown",
    is_ad:             bool = False,
    discourse_quality: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    v8.0 — Assemble the essay_revision_control_payload for downstream ER consumption.
    """
    # QA self-check
    qa_flags = {
        "sentence_control_complete":     len(sent_control) == len(maps.get("sentence_map", [])),
        "paragraph_function_complete":   len(para_func)    == len(maps.get("paragraph_map", [])),
        "essay_function_complete":       bool(essay_func),
        "example_quality_complete":      True,
        "student_safe_messages_complete":True,
        "no_detector_family_classification": True,   # enforced: no SVA/article/verb_form labels
        "no_pre_revision_corrections":       True,   # no model sentences emitted here
        "no_essay_specific_patterns":        True,   # all logic is universal
        "no_topic_specific_whitelist":       True,
    }

    student_safe = {
        "sentence_missing_control": "Check this sentence carefully before keeping it.",
        "sentence_weak_language":   "The idea is understandable, but the sentence needs grammar or wording repair.",
        "paragraph_weak_introduction": "Add a clear position and preview your main reasons.",
        "paragraph_weak_body":      "Make sure each paragraph has a clear main idea, a reason, and a specific example.",
        "paragraph_weak_conclusion":"Summarise your main reasons and give your final answer.",
        "example_too_personal":     "Use a wider social example to make your argument stronger.",
        "example_not_explained":    "After the example, explain how it supports your main point.",
    }

    return {
        "schema_version":    "ESSAY_REVISION_CONTROL_PAYLOAD_V1_2",
        "discourse_quality": discourse_quality or {},
        "source_engine":     "VA_PREMIUM_EVALUATOR_WKE_V8_0",
        "task_schema": {
            "task_type":                   task_type,
            "task_type_confidence":        0.80 if is_ad else 0.55,
            "prompt_available":            False,
            "task_schema_warnings":        [] if is_ad else ["task_type_inferred_from_essay"],
            "required_rhetorical_functions": (
                ["introduce_topic","state_position","discuss_disadvantages",
                 "discuss_advantages","conclude_with_position"]
                if is_ad else []
            ),
        },
        "sentence_control":     sent_control,
        "paragraph_function":   para_func,
        "essay_function":       essay_func,
        "example_quality":      example_quality,
        "student_safe_messages":student_safe,
        "qa":                   qa_flags,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# v8.0 STRUCTURAL SKILL RECALIBRATION
# ═══════════════════════════════════════════════════════════════════════════════

def recalibrate_structural_signals(
    observations: List[Dict],
    dq:           Dict,
    para_func:    List[Dict],
    sent_control: List[Dict],
) -> None:
    '''
    v8.1 — Quality-depth recalibration using Module A-H depth ratings.

    Uses discourse_quality depth labels from the DQ payload to cap
    organisation/cohesion skill statuses. Operates only downward —
    never raises a status.

    Replaces v8.0 recalibration which only fired on RED paragraphs.
    '''
    STATUS_RANK = {
        "current_strength": 0, "observed": 1, "observed_low_evidence": 2,
        "observed_slot_only": 3, "not_observed": 4,
    }
    SIG_MAP = {
        "observed": "monitor", "observed_low_evidence": "monitor",
        "observed_slot_only": "development_target",
        "not_observed": "development_target",
    }

    def _cap(obs: Dict, max_status: str, note: str) -> None:
        if STATUS_RANK.get(obs.get("status",""), 99) < STATUS_RANK.get(max_status, 99):
            obs["status"] = max_status
            obs["skill_signal"] = SIG_MAP.get(max_status, obs.get("skill_signal","monitor"))
            obs.setdefault("recalibration_notes", []).append(f"v8.1_recalibration: {note}")

    def _dint(key: str) -> int:
        return {"DEPTH_0":0,"DEPTH_1":1,"DEPTH_2":2,"DEPTH_3":3}.get(dq.get(key,"DEPTH_3"), 3)

    pc_depth    = _dint("position_clarity")
    trans_depth = _dint("transition_quality")
    conc_depth  = _dint("conclusion_alignment")
    coh_depth   = _dint("cohesion_quality")
    dc_depth    = _dint("dev_coverage")

    bp = dq.get("body_paragraphs", [])
    _lmap = {"DEPTH_0":0,"DEPTH_1":1,"DEPTH_2":2,"DEPTH_3":3}
    worst_idea = min((_lmap.get(b.get("idea_density","DEPTH_3"),3) for b in bp), default=3)
    worst_cree = min((_lmap.get(b.get("cree_depth","DEPTH_3"),3)   for b in bp), default=3)

    _INTRO_SKILLS   = frozenset({"introduction_construction","ws_proposition_clarity","ws_proposition_completeness"})
    _CONC_SKILLS    = frozenset({"conclusion_construction","ws_conclusion_alignment_struct"})
    _BODY_SKILLS    = frozenset({"topic_sentence_control","paragraph_balance","paragraph_planning"})
    _ESSAY_SKILLS   = frozenset({"global_structure_control","logical_sequencing"})
    _CONTENT_SKILLS = frozenset({"argument_development","content_development","supporting_evidence"})
    _TR_SKILLS      = frozenset({"task_response_completeness","counter_argument_integration"})
    _COH_SUBS       = {"cohes","organ","connect","sequenc","transition","reference","paragraph","discourse"}

    def _is_coh(sid: str) -> bool:
        sl = sid.lower()
        return any(sub in sl for sub in _COH_SUBS) or sid in _STRUCT_SKILLS_ORG or sid in _STRUCT_SKILLS_COH

    for obs in observations:
        sid = obs.get("skill_id","")

        if sid in _INTRO_SKILLS:
            if pc_depth == DQ_DEPTH_0:
                _cap(obs, "not_observed", "position_clarity=DEPTH_0 (position absent)")
            elif pc_depth == DQ_DEPTH_1:
                _cap(obs, "observed_slot_only", "position_clarity=DEPTH_1 (vague/bare polarity)")

        if sid in _CONC_SKILLS:
            if conc_depth == DQ_DEPTH_0:
                _cap(obs, "not_observed", "conclusion_alignment=DEPTH_0")
            elif conc_depth == DQ_DEPTH_1:
                _cap(obs, "observed_slot_only", "conclusion_alignment=DEPTH_1 (new claims/missing restatement)")

        if sid in _BODY_SKILLS or sid in _CONTENT_SKILLS:
            if worst_cree == DQ_DEPTH_0:
                _cap(obs, "not_observed", "worst_cree_depth=DEPTH_0 (no body development)")
            elif worst_cree == DQ_DEPTH_1:
                _cap(obs, "observed_slot_only", "worst_cree_depth=DEPTH_1 (claim only, no reasoning)")

        if sid in _BODY_SKILLS and worst_idea <= DQ_DEPTH_1:
            _cap(obs, "observed_slot_only", f"idea_density worst=DEPTH_{worst_idea} (scattered ideas)")

        if sid in _ESSAY_SKILLS and worst_idea == DQ_DEPTH_0:
            _cap(obs, "observed_low_evidence", "idea_density DEPTH_0 (chaotic topic control)")

        if sid == "discourse_marker_control":
            if trans_depth == DQ_DEPTH_0:
                _cap(obs, "not_observed", "transition_quality=DEPTH_0 (all markers misused)")
            elif trans_depth == DQ_DEPTH_1:
                _cap(obs, "observed_slot_only", "transition_quality=DEPTH_1 (heavy misuse)")

        if sid in _ESSAY_SKILLS and trans_depth == DQ_DEPTH_0:
            _cap(obs, "observed_slot_only", "transition_quality=DEPTH_0 affects logical_sequencing")

        if _is_coh(sid):
            if coh_depth == DQ_DEPTH_0:
                _cap(obs, "observed_slot_only",
                     "cohesion_quality=DEPTH_0 (transition+ref+density all low)")
            elif coh_depth == DQ_DEPTH_1 and obs.get("skill_signal") == "current_strength":
                _cap(obs, "observed_low_evidence",
                     "cohesion_quality=DEPTH_1: current_strength not confirmed by quality evidence")

        if sid in _TR_SKILLS:
            if dc_depth == DQ_DEPTH_0:
                _cap(obs, "not_observed", "dev_coverage=DEPTH_0 (no developed body paragraphs)")
            elif dc_depth == DQ_DEPTH_1:
                _cap(obs, "observed_slot_only", "dev_coverage=DEPTH_1 (thin body development)")

    # ── Legacy v8.0 safety net: RED paragraph recalibration ──────────────────
    pf_by_role = {}
    for p in para_func:
        pf_by_role.setdefault(p.get("paragraph_role","body"), []).append(p)
    intro_red = any(p["paragraph_function_status"]=="red" for p in pf_by_role.get("introduction",[]))
    conc_red  = any(p["paragraph_function_status"]=="red" for p in pf_by_role.get("conclusion",[]))
    body_red  = any(p["paragraph_function_status"]=="red" for p in pf_by_role.get("body",[]))
    ref_pat   = re.compile(r'\b(this|these|they|it|them|their|those)\b', re.I)
    for obs in observations:
        sid = obs.get("skill_id","")
        if obs.get("skill_signal") != "current_strength":
            continue
        if sid in _INTRO_SKILLS and intro_red:
            _cap(obs, "monitor", "v8.0_legacy: introduction RED paragraph")
        elif sid in _CONC_SKILLS and conc_red:
            _cap(obs, "monitor", "v8.0_legacy: conclusion RED paragraph")
        elif sid in _BODY_SKILLS and body_red:
            _cap(obs, "monitor", "v8.0_legacy: body RED paragraph")
        elif _is_coh(sid) and (intro_red or conc_red or body_red):
            broken = [s for s in sent_control
                      if s["language_control_status"]=="red" and ref_pat.search(s["text"])]
            if broken:
                _cap(obs, "monitor", "v8.0_legacy: reference sentences RED language control")


def build_consumer_payloads(
    observations: List[Dict], lexical_units: List[Dict],
    lret_quality: Dict,
    maps: Dict, base: Dict,
    detector: Optional[Dict] = None,
    ercp: Optional[Dict] = None,
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
        "essay_revision_control_payload": ercp or {},
        "lret_payload": {
            "lexical_units_for_lret": lexical_units,
            "lexical_unit_quality":    lret_quality,
            "fix_candidates": [
                {k: v for k, v in row.items()
                 if k in {"span_text","error_family","suggestion","start","end","paragraph_idx"}}
                for row in (detector.get("diagnostic_rows",[]) if detector and detector.get("available") else [])
                if row.get("error_family") in LRET_FIX_FAMILIES
            ],
            "note": "candidate_route_hint is an evaluator suggestion; LRET owns final FIX/ENHANCE/KEEP/CLARIFY/DROP decision.",
        },
        "practice_engine_payload": {
            "practice_relevant_targets":  _compact_obs([o for o in targets if "Practice Engine" in o.get("consumers",[]) or "practice_engine" in [str(c).lower() for c in o.get("consumers",[])]][:15]),
            "practice_evidence_required": _compact_obs(practice_needed[:20]),
            "gap_targets_for_practice":   [g for g in gap_signals if g.get("gap_type") == "absence"][:10],
            "note": "Practice Engine chooses exercise type. Evaluator supplies targets and prerequisites only.",
        },
        "essay_revision_payload_legacy": {
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


    # ── v8.0 QA gates ─────────────────────────────────────────────────────────

    # QA_V80_001: essay_revision_control_payload must be present and have all sub-sections
    ercp_qa = (obj.get("consumer_payloads") or {}).get("essay_revision_control_payload", {})
    if not ercp_qa:
        errors.append("QA_V80_001: essay_revision_control_payload missing from consumer_payloads.")
    else:
        for section in ("sentence_control","paragraph_function","essay_function","example_quality"):
            if section not in ercp_qa:
                warnings.append(f"QA_V80_001: essay_revision_control_payload.{section} missing.")

    # QA_V80_002: No sentence should be green if detector has errors for it
    sc = ercp_qa.get("sentence_control", [])
    green_with_det = [
        s["sentence_index"] for s in sc
        if s.get("language_control_status") == "green" and s.get("detector_errors_consulted")
    ]
    if green_with_det:
        errors.append(
            f"QA_V80_002: Sentences marked green despite detector errors: {green_with_det}. "
            "Green gate requires no detector errors."
        )

    # QA_V80_003: Organization/Cohesion current_strength must not coexist with red para function
    pf = ercp_qa.get("paragraph_function", [])
    any_red_para = any(p.get("paragraph_function_status") == "red" for p in pf)
    if any_red_para:
        struct_strengths = [
            o["skill_id"] for o in sop
            if o.get("skill_signal") == "current_strength" and
               o.get("skill_id") in (_STRUCT_SKILLS_ORG | _STRUCT_SKILLS_COH)
        ]
        if struct_strengths:
            errors.append(
                f"QA_V80_003: Structural skills marked current_strength despite red paragraph function: "
                f"{struct_strengths}. Recalibration may have failed."
            )

    # QA_V80_004: LRET quality metrics — meaningful_unit_rate target
    lq = ((obj.get("consumer_payloads") or {}).get("lret_payload") or {}).get("lexical_unit_quality",{})
    mur = lq.get("meaningful_unit_rate", 1.0)
    if mur < 0.60:
        warnings.append(
            f"QA_V80_004: LRET meaningful_unit_rate is {mur:.2f} (target >= 0.70). "
            "Lexical extraction quality needs attention."
        )

    # QA_V80_005: LRET must not have ENHANCE-route malformed units
    lu = ((obj.get("consumer_payloads") or {}).get("lret_payload") or {}).get("lexical_units_for_lret", [])
    bad_enhance = [
        u.get("unit","?") for u in lu
        if u.get("candidate_route_hint") == "ENHANCE" and u.get("span_completeness") == "malformed"
    ]
    if bad_enhance:
        errors.append(
            f"QA_V80_005: ENHANCE-route units with malformed span detected: {bad_enhance}."
        )

    # QA_V80_006: No Detector family labels in evaluator output
    ercp_text = str(ercp_qa)
    forbidden_labels = [
        "SUBJECT_VERB_AGREEMENT","SVA","ARTICLE_DETERMINER","VERB_FORM","COLLOCATION_ERROR",
        "WORD_ORDER","CLAUSE_STRUCTURE","FRAGMENT","RUN_ON","PUNCTUATION_ERROR"
    ]
    found_labels = [lb for lb in forbidden_labels if lb in ercp_text]
    if found_labels:
        errors.append(
            f"QA_V80_006: Detector family labels found in essay_revision_control_payload: {found_labels}."
        )

    # V8.1 gates
    ercp_cp = obj.get("consumer_payloads", {}).get("essay_revision_control_payload", {})
    dq_qa   = ercp_cp.get("discourse_quality", {})
    if not dq_qa:
        errors.append("V81_001: discourse_quality absent from ERCP")
    else:
        for rk in ("position_clarity","transition_quality","conclusion_alignment","cohesion_quality"):
            if rk not in dq_qa:
                errors.append(f"V81_001: discourse_quality missing field: {rk}")
    coh_l = dq_qa.get("cohesion_quality","DEPTH_3")
    if coh_l in ("DEPTH_0","DEPTH_1"):
        coh_subs = {"cohes","organ","connect","sequenc","transition","reference","paragraph","discourse"}
        for obs in obj.get("skill_observation_profile", []):
            sid = obs.get("skill_id","")
            if any(s in sid.lower() for s in coh_subs) and obs.get("skill_signal") == "current_strength":
                errors.append(f"V81_002: {sid} current_strength but cohesion_quality={coh_l}")
    pc_l = dq_qa.get("position_clarity","DEPTH_3")
    if pc_l in ("DEPTH_0","DEPTH_1"):
        for obs in obj.get("skill_observation_profile", []):
            if obs.get("skill_id") == "ws_proposition_clarity" and obs.get("status") == "observed":
                errors.append(f"V81_003: ws_proposition_clarity=observed but position_clarity={pc_l}")
    _lm   = {"DEPTH_0":0,"DEPTH_1":1,"DEPTH_2":2,"DEPTH_3":3}
    bp_qa = dq_qa.get("body_paragraphs", [])
    wc_d  = min((_lm.get(b.get("cree_depth","DEPTH_3"),3) for b in bp_qa), default=3)
    if wc_d <= 1:
        for obs in obj.get("skill_observation_profile", []):
            if obs.get("skill_id") == "argument_development" and obs.get("status") == "observed":
                errors.append(f"V81_004: argument_development=observed but worst cree_depth=DEPTH_{wc_d}")
    conc_l = dq_qa.get("conclusion_alignment","DEPTH_3")
    if conc_l in ("DEPTH_0","DEPTH_1"):
        for obs in obj.get("skill_observation_profile", []):
            if obs.get("skill_id")=="conclusion_construction" and obs.get("skill_signal")=="current_strength":
                errors.append(f"V81_005: conclusion_construction current_strength but alignment={conc_l}")
    if dq_qa:
        chk = [dq_qa.get(k) for k in
               ("position_clarity","transition_quality","conclusion_alignment","cohesion_quality","dev_coverage")]
        if any(l in ("DEPTH_0","DEPTH_1") for l in chk if l) and not dq_qa.get("summary_flags"):
            warnings.append("V81_006: summary_flags empty but discourse issues detected")
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
    lexical_units, lret_quality = extract_lexical_units(req.essay_text, maps)
    base         = baseline_features(req.essay_text, maps, lexical_units)

    # v7.2: detect A/D pattern before building observations
    is_ad = is_advantages_disadvantages_pattern(maps)

    observations, depth_cache = build_skill_observations(
        skills, maps, lexical_units, base, detector, scorer, is_ad
    )

    llm_meta = call_llm_refinement(req, observations, maps, depth_cache) if req.use_llm else {"enabled": False}
    apply_llm_refinement(observations, llm_meta)

    # v8.0 — Sentence/paragraph/essay/example assessment
    # v8.1: inherit task_type from detector task_profile
    task_profile: Dict[str, Any] = {}
    if detector.get("available"):
        task_profile = (
            detector.get("evaluator_payload", {}).get("task_schema_profile", {}) or
            detector.get("scorer_payload",    {}).get("task_profile", {}) or {}
        )
    task_type = task_profile.get("task_type") or ("advantages_disadvantages" if is_ad else "unknown")

    sent_ctrl  = assess_sentence_control(maps.get("sentence_map",[]), detector, maps.get("paragraph_map",[]))
    para_func  = assess_paragraph_function(maps.get("paragraph_map",[]), maps.get("sentence_map",[]), sent_ctrl)
    essay_func = assess_essay_function(maps.get("paragraph_map",[]), maps.get("sentence_map",[]), para_func)
    ex_qual    = detect_example_quality(maps.get("sentence_map",[]), maps.get("argument_map",{}))

    # v8.1 discourse quality modules A-H
    paragraph_map = maps.get("paragraph_map", [])
    sentence_map  = maps.get("sentence_map", [])
    n_paras       = len(paragraph_map)
    ex_qual_map   = {eq["sentence_index"]: eq for eq in ex_qual if "sentence_index" in eq}

    # Module A
    intro_sents = [s for s in sentence_map if s.get("paragraph_index") == 0]
    pos_clarity = assess_position_clarity(intro_sents, essay_func.get("intro_met", []))

    # Modules B + C: per body paragraph
    body_density : List[Dict] = []
    cree_results : List[Dict] = []
    for para in paragraph_map:
        pidx = para["paragraph_index"]
        if pidx == 0 or pidx == n_paras - 1:
            continue
        psents = [s for s in sentence_map
                  if s["sentence_index"] in set(para.get("sentence_indices", []))]
        body_density.append(assess_idea_density(psents, paragraph_index=pidx))
        cree_results.append(assess_cree_depth(psents, pidx, ex_qual_map))

    # Module D: universal development coverage
    dev_coverage = assess_development_coverage(cree_results)

    # Module E: reference resolution quality (worst across paragraphs)
    ref_depths: List[int] = []
    for para in paragraph_map:
        psents = [s for s in sentence_map
                  if s["sentence_index"] in set(para.get("sentence_indices", []))]
        ref_depths.append(assess_ref_quality(psents).get("_depth_int", DQ_DEPTH_3))
    essay_ref_depth = min(ref_depths) if ref_depths else DQ_DEPTH_3

    # Module F: transition correctness (essay-level)
    trans_q = assess_transition_quality(sentence_map)

    # Module G: conclusion alignment
    body_claim_heads = _extract_body_claim_heads(paragraph_map, sentence_map, cree_results)
    conc_sents = [s for s in sentence_map if s.get("paragraph_index") == n_paras - 1]
    conc_align = assess_conclusion_alignment(conc_sents, body_claim_heads)

    # Module H: cohesion quality
    cohesion_q = assess_cohesion_quality(
        trans_q.get("_depth_int", DQ_DEPTH_3), essay_ref_depth,
        trans_q.get("n_markers", 0), len(sentence_map),
    )

    dq = build_discourse_quality_payload(
        pos_clarity, body_density, cree_results, dev_coverage,
        essay_ref_depth, trans_q, conc_align, cohesion_q,
    )

    ercp = build_essay_revision_control_payload(
        maps, sent_ctrl, para_func, essay_func, ex_qual, task_type, is_ad,
        discourse_quality=dq,
    )

    # v8.1 recalibration (depth-table driven)
    recalibrate_structural_signals(observations, dq, para_func, sent_ctrl)

    consumer_payloads = build_consumer_payloads(observations, lexical_units, lret_quality, maps, base, detector=detector, ercp=ercp)

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
            "task_type_from_detector": task_type,
            "discourse_quality_flags": dq.get("summary_flags", []),
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
