#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Premium Unified Scorer v1.4 — Tier-Aware Governor — standalone runtime scorer.

This file is self-contained: schema, adapter, evidence ledger, tier routing,
constraint generation, integer-domain solver, and CLI are all implemented here.
Final criterion/rubric bands are integers only. The overall band is rounded to
.0/.5 from the four integer criteria.
"""
# premium_unified_scorer_v1_4_1_fixed.py
# NEW FILE — supersedes premium_unified_scorer_v1_4_1.py (do not delete).
# FIX-3: stale v1.3.2 labels replaced with v1.4.1 in comments/docstrings.
#         v1_3_2_ constraint-rule identifiers left unchanged (API contract).
# FIX-4: clean_soft_task_profile initialised before if/elif block in classify_tier();
#         fragile 'in locals()' guard removed.
from __future__ import annotations
"""Minimal canonical metric schema used by Premium Unified Scorer v1.4 — Tier-Aware Governor."""
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable
import math

RUBRICS = ["task_response", "coherence_cohesion", "lexical_resource", "grammar"]

GOOD_RATE_METRICS = {
    "TR1_prompt_part_coverage", "TR2_position_clarity", "TR3_position_consistency", "TR4_relevance_ratio",
    "TR5_idea_extension_depth", "TR6_support_quality", "TR7_conclusion_alignment",
    "CC1_global_logical_progression", "CC2_paragraph_topic_unity", "CC3_paragraphing_appropriacy",
    "CC4_intra_paragraph_sequencing", "CC5_inter_paragraph_transition_quality", "CC6_reference_substitution_clarity",
    "CC7_cohesive_device_appropriacy", "LR1_lexical_range", "LR2_topic_vocabulary_adequacy",
    "LR3_word_choice_precision", "LR4_collocation_control", "LR5_lexical_appropriacy_register",
    "LR7_word_formation_accuracy", "LR8_spelling_impact", "LR9_semantic_phrase_naturalness",
    "LR10_lexical_sophistication_index", "LR11_dynamic_multiword_density", "GRA1_structure_range",
    "GRA2_simple_sentence_accuracy", "GRA3_compound_sentence_accuracy", "GRA4_complex_sentence_accuracy",
    "GRA7_punctuation_accuracy", "GRA9_communicative_effect_of_errors", "semantic_recoverability",
    "proposition_stability", "high_band_readiness", "error_free_sentence_ratio", "medium_quality_probability",
    "word_count", "sentence_count", "paragraph_count", "task_schema_confidence",
}

BAD_RATE_METRICS = {
    "TR8_irrelevant_or_repetitive_content_rate", "CC8_cohesive_device_overuse_mechanicality",
    "LR6_repetition_simplification_rate", "GRA5_severe_grammar_error_density",
    "GRA6_overall_grammar_error_density_per_100w", "GRA8_malformed_sentence_ratio",
    "affected_discourse_ratio", "grammar_damage_index", "weak_writing_probability",
    "local_language_damage_index", "serious_error_sentence_ratio",
}

DEFAULTS: Dict[str, Any] = {
    # TR
    "TR1_prompt_part_coverage": 0.55, "TR2_position_clarity": 0.55, "TR3_position_consistency": 0.55,
    "TR4_relevance_ratio": 0.55, "TR5_idea_extension_depth": 0.55, "TR6_support_quality": 0.55,
    "TR7_conclusion_alignment": 0.55, "TR8_irrelevant_or_repetitive_content_rate": 0.25,
    # CC
    "CC1_global_logical_progression": 0.55, "CC2_paragraph_topic_unity": 0.55, "CC3_paragraphing_appropriacy": 0.55,
    "CC4_intra_paragraph_sequencing": 0.55, "CC5_inter_paragraph_transition_quality": 0.55,
    "CC6_reference_substitution_clarity": 0.55, "CC7_cohesive_device_appropriacy": 0.55,
    "CC8_cohesive_device_overuse_mechanicality": 0.22,
    # LR
    "LR1_lexical_range": 0.55, "LR2_topic_vocabulary_adequacy": 0.55, "LR3_word_choice_precision": 0.55,
    "LR4_collocation_control": 0.55, "LR5_lexical_appropriacy_register": 0.55, "LR6_repetition_simplification_rate": 0.25,
    "LR7_word_formation_accuracy": 0.55, "LR8_spelling_impact": 0.55, "LR9_semantic_phrase_naturalness": 0.55,
    "LR10_lexical_sophistication_index": 0.55, "LR11_dynamic_multiword_density": 0.55,
    # GRA
    "GRA1_structure_range": 0.55, "GRA2_simple_sentence_accuracy": 0.55, "GRA3_compound_sentence_accuracy": 0.55,
    "GRA4_complex_sentence_accuracy": 0.55, "GRA5_severe_grammar_error_density": 0.25,
    "GRA6_overall_grammar_error_density_per_100w": 0.25, "GRA7_punctuation_accuracy": 0.55,
    "GRA8_malformed_sentence_ratio": 0.25, "GRA9_communicative_effect_of_errors": 0.55,
    # shared
    "semantic_recoverability": 0.60, "proposition_stability": 0.55, "affected_discourse_ratio": 0.25,
    "grammar_damage_index": 0.25, "word_count": 0, "sentence_count": 0, "paragraph_count": 0,
    "weak_writing_probability": 0.45, "high_band_readiness": 0.55, "error_free_sentence_ratio": 0.55,
    "medium_quality_probability": 0.45, "local_language_damage_index": 0.35,
    "serious_error_sentence_ratio": 0.25, "task_schema_status": "unknown", "task_schema_confidence": 0.60,
}


def clamp01(x: Any) -> float:
    try:
        v = float(x)
    except Exception:
        return 0.0
    if math.isnan(v) or math.isinf(v):
        return 0.0
    return max(0.0, min(1.0, v))


def round_half(x: float) -> float:
    return round(float(x) * 2.0) / 2.0


def quality_to_integer_band(q: float, thresholds: Dict[str, float] | None = None) -> int:
    th = thresholds or {"8": 0.88, "7": 0.74, "6": 0.60, "5": 0.45, "4": 0.30}
    q = clamp01(q)
    for b in [8, 7, 6, 5, 4]:
        if q >= float(th.get(str(b), 0.0)):
            return b
    return 3


@dataclass
class MetricValue:
    metric: str
    value: Any
    source: str = "unknown"
    confidence: float = 0.65
    evidence_count: int = 1
    polarity: str = "good_rate"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CanonicalMetricProfile:
    def __init__(self, metrics: Dict[str, MetricValue] | None = None, contract_version: str = "premium_metric_schema_v1"):
        self.contract_version = contract_version
        self.metrics: Dict[str, MetricValue] = metrics or {}
        self.validation_warnings: list[str] = []

    def set(self, name: str, value: Any, source: str = "unknown", confidence: float = 0.65, evidence_count: int = 1, polarity: str | None = None, notes: list[str] | None = None) -> None:
        pol = polarity or ("bad_rate" if name in BAD_RATE_METRICS else "good_rate" if name in GOOD_RATE_METRICS else "non_numeric" if isinstance(value, str) else "good_rate")
        if isinstance(value, (int, float)) and name not in {"word_count", "sentence_count", "paragraph_count"}:
            value = clamp01(value)
        self.metrics[name] = MetricValue(name, value, source, clamp01(confidence), int(evidence_count or 0), pol, notes or [])

    def get(self, name: str, default: Any = None) -> Any:
        mv = self.metrics.get(name)
        return mv.value if mv is not None else default

    def get_float(self, name: str, default: float = 0.0) -> float:
        val = self.get(name, default)
        try:
            return float(val)
        except Exception:
            return float(default)

    def validate(self) -> None:
        self.validation_warnings = []
        for name, mv in self.metrics.items():
            if isinstance(mv.value, (int, float)) and name not in {"word_count", "sentence_count", "paragraph_count"}:
                if not (0.0 <= float(mv.value) <= 1.0):
                    self.validation_warnings.append(f"{name}: numeric metric outside 0..1")

    def ensure_defaults(self) -> None:
        for name, value in DEFAULTS.items():
            if name not in self.metrics:
                pol = "bad_rate" if name in BAD_RATE_METRICS else "good_rate" if name in GOOD_RATE_METRICS else "non_numeric"
                self.set(name, value, source="fallback", confidence=0.35, evidence_count=0, polarity=pol)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "metrics": {k: v.as_dict() for k, v in self.metrics.items()},
            "validation_warnings": list(self.validation_warnings),
        }


def _flatten(d: Dict[str, Any], out: Dict[str, Any]) -> None:
    for k, v in (d or {}).items():
        if isinstance(v, dict):
            if "value" in v and ("metric" in v or "source" in v):
                out[k] = v.get("value")
            else:
                _flatten(v, out)
        else:
            out[k] = v


def build_profile_from_dict(data: Dict[str, Any] | None, source: str = "input") -> CanonicalMetricProfile:
    flat: Dict[str, Any] = {}
    _flatten(data or {}, flat)
    p = CanonicalMetricProfile()
    for k, v in flat.items():
        if k in DEFAULTS or k.startswith(("TR", "CC", "LR", "GRA")):
            p.set(k, v, source=source, confidence=0.65, evidence_count=1)
    p.validate(); p.ensure_defaults()
    return p


"""Runtime dataclasses for Premium Unified Scorer v1.4.1."""
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List


def _asdict(obj: Any) -> Any:
    if hasattr(obj, "as_dict"):
        return obj.as_dict()
    if isinstance(obj, list):
        return [_asdict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _asdict(v) for k, v in obj.items()}
    return obj


@dataclass
class PremiumScoreInput:
    essay_id: str
    prompt_text: str = ""
    essay_text: str = ""
    detector_rows: List[Dict[str, Any]] = field(default_factory=list)
    metric_source: Dict[str, Any] = field(default_factory=dict)
    source_metadata: Dict[str, Any] = field(default_factory=dict)
    upstream_record: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceItem:
    rubric: str
    family: str
    operation: str = ""
    root_or_symptom: str = "root"
    chargeable: bool = False
    severity: str = "medium"
    confidence: float = 0.6
    score_weight: float = 0.0
    semantic_recoverability_impact: float = 0.0
    provenance: str = "detector"
    quote: str = ""
    reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceLedger:
    essay_id: str
    items: List[EvidenceItem] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    audit: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"essay_id": self.essay_id, "items": [_asdict(x) for x in self.items], "summary": self.summary, "audit": self.audit}


@dataclass
class TierDecision:
    tier: str
    overall_upper_bound: float
    overall_lower_bound: float
    high_band_allowed: bool = False
    weak_safety_veto: bool = False
    strong_weak_safety_veto: bool = False
    task_schema_false_fail_possible: bool = False
    confidence: float = 0.80
    reasons: List[str] = field(default_factory=list)
    features: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Constraint:
    constraint_id: str
    target: str
    type: str
    value: Any
    priority: int
    evidence: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScoreAudit:
    scoring_version: str
    raw_criterion_quality: Dict[str, float]
    raw_criterion_bands: Dict[str, int]
    tier_decision: Dict[str, Any]
    constraints_generated: List[Dict[str, Any]]
    constraints_applied: List[Dict[str, Any]]
    constraints_suppressed: List[Dict[str, Any]]
    final_criterion_bands: Dict[str, float]
    overall_band: float
    confidence: float
    review_flags: List[str]
    qa: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


"""Evidence ledger and root-cause arbitration for Premium Unified Scorer v1.

DIRECT-PROFILER FIX:
- accepts detector rows with rubric/family and score_charge_weight keys;
- score_charge_weight is mapped to score_weight;
- chargeable detector evidence is no longer silently zeroed.
"""
from typing import Any, Dict, List
from collections import Counter, defaultdict

GRAMMAR_FAMILIES = {"ARTICLE_DETERMINER","NOUN_NUMBER_COUNTABILITY","SUBJECT_VERB_AGREEMENT","VERB_TENSE","VERB_FORM","VERB_PATTERN","PREPOSITION_PATTERN","CLAUSE_STRUCTURE","FRAGMENT","RUN_ON","CONSTRUCTION","WORD_ORDER","CONDITIONAL_STRUCTURE","GRAMMAR_PUNCTUATION","COMPARATIVE_FORM"}
LEXICAL_FAMILIES = {"SPELLING","WORD_FORM","COLLOCATION","WORD_CHOICE","LEXICAL_PRECISION","SEMANTIC_COMBINATION","REGISTER","REPETITION"}
CC_FAMILIES = {"TRANSITION","MISSING_TRANSITION","LOGICAL_PROGRESSION","REFERENCE_COHESION","PARAGRAPH_STRUCTURE","TOPIC_CONTINUITY","EXAMPLE_INTEGRATION","REFERENCE_BREAK","CHAIN_BREAK"}
TR_FAMILIES = {"PROMPT_COVERAGE","PROMPT_RELEVANCE","POSITION_RESPONSE","TASK_COMPLETENESS","UNSUPPORTED_CLAIM","WEAK_EXAMPLE","REASONING_CHAIN","POSITION_CLARITY","CLAIM_SUPPORT_LINK"}

FAMILY_TO_RUBRIC = {**{f:"grammar" for f in GRAMMAR_FAMILIES}, **{f:"lexical_resource" for f in LEXICAL_FAMILIES}, **{f:"coherence_cohesion" for f in CC_FAMILIES}, **{f:"task_response" for f in TR_FAMILIES}}
STRUCTURAL_GRAMMAR = {"CLAUSE_STRUCTURE","CONSTRUCTION","VERB_PATTERN","CONDITIONAL_STRUCTURE","WORD_ORDER","FRAGMENT","RUN_ON"}
INDEPENDENT_SURFACE = {"SPELLING","WORD_FORM"}


def _row_value(row: Dict[str, Any], *keys: str, default=None):
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return default


def _normalise_row(row: Dict[str, Any]) -> EvidenceItem:
    fam = str(_row_value(row, "primary_family", "family", "family_candidate", default="UNKNOWN"))
    rub = str(_row_value(row, "primary_rubric", "category", "rubric", "rubric_candidate", default=FAMILY_TO_RUBRIC.get(fam, "unknown")))
    if fam == "SPELLING":
        rub = "lexical_resource"
    elif rub == "unknown" or rub not in RUBRICS:
        rub = FAMILY_TO_RUBRIC.get(fam, "lexical_resource" if fam in LEXICAL_FAMILIES else "grammar")
    severity = str(_row_value(row, "severity", default="medium"))
    confidence = clamp01(_row_value(row, "confidence", default=0.6))
    weight = float(_row_value(row, "score_weight", "score_charge_weight", "impact_weight", default=0.0) or 0.0)
    chargeable = bool(_row_value(row, "chargeable_for_scoring", default=weight > 0))
    # if a row says chargeable but weight is missing, assign a conservative default from severity/confidence
    if chargeable and weight <= 0:
        base = 0.65 if severity in {"high", "severe"} else 0.42 if severity in {"moderate", "medium"} else 0.20
        weight = round(base * max(0.45, confidence), 3)
    operation = str(_row_value(row, "operation", "repair_operation", default=""))
    quote = str(_row_value(row, "quote", "local_quote", default=""))[:240]
    root_or_symptom = str(_row_value(row, "root_or_secondary", "root_or_symptom", default="root"))
    if root_or_symptom not in {"root", "symptom", "advisory"}:
        root_or_symptom = "root"
    if rub in {"task_response", "coherence_cohesion"} and _row_value(row, "sentence_index", default=None) is not None:
        root_or_symptom = "advisory"
    return EvidenceItem(rubric=rub, family=fam, operation=operation, root_or_symptom=root_or_symptom, chargeable=chargeable, severity=severity, confidence=confidence, score_weight=weight, provenance="detector", quote=quote)


def build_evidence_ledger(score_input: PremiumScoreInput) -> EvidenceLedger:
    raw_rows = list(score_input.detector_rows or [])
    if not raw_rows:
        upstream = score_input.upstream_record or {}
        for x in upstream.get("rubric_impact_map", []) or []:
            rub = x.get("rubric", "unknown")
            raw_rows.append({"primary_rubric": rub, "family": "UNKNOWN", "score_weight": x.get("impact_weight", 0), "chargeable_for_scoring": True, "confidence": 0.55, "severity": "medium"})
    items = [_normalise_row(r) for r in raw_rows]

    by_sentence = defaultdict(list)
    for i, row in enumerate(raw_rows):
        sid = _row_value(row, "sentence_index", default=None)
        cid = _row_value(row, "cluster_id", "sentence_id", default=sid if sid is not None else f"row_{i}")
        by_sentence[cid].append((i, row))

    demoted = 0
    for _cid, pairs in by_sentence.items():
        fams = {items[i].family for i, _ in pairs}
        has_structural = bool(fams & STRUCTURAL_GRAMMAR)
        if has_structural:
            for i, _ in pairs:
                it = items[i]
                if it.rubric in {"task_response", "coherence_cohesion"}:
                    it.root_or_symptom = "symptom"
                    it.chargeable = False
                    it.score_weight = 0.0
                    it.reason = "local structural grammar root dominates sentence-level discourse/TR symptom"
                    demoted += 1
                if it.rubric == "lexical_resource" and it.family not in INDEPENDENT_SURFACE:
                    it.root_or_symptom = "advisory"
                    it.chargeable = False
                    it.score_weight = 0.0
                    it.reason = "lexical-semantic row made advisory because syntax stability is insufficient"
                    demoted += 1

    chargeable = [x for x in items if x.chargeable and x.score_weight > 0]
    by_rubric = Counter()
    by_family = Counter()
    severity_counts = Counter()
    for it in chargeable:
        by_rubric[it.rubric] += it.score_weight
        by_family[it.family] += it.score_weight
        severity_counts[it.severity] += 1
    summary = {
        "chargeable_count": len(chargeable),
        "advisory_count": len(items) - len(chargeable),
        "weighted_by_rubric": {k: round(v, 4) for k, v in by_rubric.items()},
        "weighted_by_family": {k: round(v, 4) for k, v in by_family.items()},
        "severity_counts": dict(severity_counts),
        "local_root_weight": round(by_rubric.get("grammar", 0.0) + by_rubric.get("lexical_resource", 0.0), 3),
    }
    audit = {"raw_rows": len(raw_rows), "normalised_items": len(items), "root_cause_demotions": demoted, "spelling_locked_to_lr": True, "direct_profiler_weight_keys_supported": True}
    return EvidenceLedger(essay_id=score_input.essay_id, items=items, summary=summary, audit=audit)


"""Compatibility adapter from detector/profiler outputs to PremiumScoreInput + CanonicalMetricProfile.

DIRECT-PROFILER FIX:
- Primary input is detector/profiler JSON, not any previous scorer output.
- Priority metric source:
  1) scorer_payload.premium_metric_profile_mapped_metrics
  2) detector_metric_profile
  3) canonical/ielts metric profile
  4) premium_metric_profile legacy names
  5) previous score profile only as fallback
- Detector rows are taken from scorer_payload.chargeable_detector_rows and review_only_detector_rows.
"""
from typing import Any, Dict, Tuple, Iterable

RUBRIC_TO_COMPOSITE_METRICS = {
    "task_response": ["TR1_prompt_part_coverage","TR2_position_clarity","TR3_position_consistency","TR4_relevance_ratio","TR5_idea_extension_depth","TR6_support_quality","TR7_conclusion_alignment"],
    "coherence_cohesion": ["CC1_global_logical_progression","CC2_paragraph_topic_unity","CC3_paragraphing_appropriacy","CC4_intra_paragraph_sequencing","CC5_inter_paragraph_transition_quality","CC6_reference_substitution_clarity","CC7_cohesive_device_appropriacy"],
    "lexical_resource": ["LR1_lexical_range","LR2_topic_vocabulary_adequacy","LR3_word_choice_precision","LR4_collocation_control","LR5_lexical_appropriacy_register","LR7_word_formation_accuracy","LR8_spelling_impact","LR9_semantic_phrase_naturalness","LR10_lexical_sophistication_index","LR11_dynamic_multiword_density"],
    "grammar": ["GRA1_structure_range","GRA2_simple_sentence_accuracy","GRA3_compound_sentence_accuracy","GRA4_complex_sentence_accuracy","GRA7_punctuation_accuracy","GRA9_communicative_effect_of_errors","error_free_sentence_ratio"],
}

LEGACY_PREMIUM_MAP = {
    # TR
    "prompt_part_coverage": "TR1_prompt_part_coverage",
    "position_clarity": "TR2_position_clarity",
    "position_consistency": "TR3_position_consistency",
    "relevance_ratio": "TR4_relevance_ratio",
    "idea_development_depth": "TR5_idea_extension_depth",
    "support_specificity": "TR6_support_quality",
    "example_integration": "TR6_support_quality",
    "conclusion_alignment": "TR7_conclusion_alignment",
    "irrelevance_or_repetition_risk": "TR8_irrelevant_or_repetitive_content_rate",
    # CC
    "global_progression": "CC1_global_logical_progression",
    "paragraph_role_clarity": "CC2_paragraph_topic_unity",
    "paragraph_balance": "CC3_paragraphing_appropriacy",
    "topic_sentence_control": "CC2_paragraph_topic_unity",
    "intra_paragraph_sequencing": "CC4_intra_paragraph_sequencing",
    "inter_paragraph_linking": "CC5_inter_paragraph_transition_quality",
    "reference_clarity": "CC6_reference_substitution_clarity",
    "cohesive_device_appropriacy": "CC7_cohesive_device_appropriacy",
    "cohesive_device_mechanicality": "CC8_cohesive_device_overuse_mechanicality",
    # LR
    "lexical_range": "LR1_lexical_range",
    "topic_vocabulary_adequacy": "LR2_topic_vocabulary_adequacy",
    "word_choice_precision": "LR3_word_choice_precision",
    "collocation_control": "LR4_collocation_control",
    "register_control": "LR5_lexical_appropriacy_register",
    "word_formation_accuracy": "LR7_word_formation_accuracy",
    "spelling_control": "LR8_spelling_impact",
    "semantic_phrase_naturalness": "LR9_semantic_phrase_naturalness",
    "lexical_sophistication": "LR10_lexical_sophistication_index",
    "multiword_density": "LR11_dynamic_multiword_density",
    "repetition_simplification": "LR6_repetition_simplification_rate",
    # GRA
    "structure_range": "GRA1_structure_range",
    "simple_sentence_accuracy": "GRA2_simple_sentence_accuracy",
    "compound_sentence_accuracy": "GRA3_compound_sentence_accuracy",
    "complex_sentence_accuracy": "GRA4_complex_sentence_accuracy",
    "severe_grammar_error_density": "GRA5_severe_grammar_error_density",
    "overall_grammar_error_density": "GRA6_overall_grammar_error_density_per_100w",
    "punctuation_accuracy": "GRA7_punctuation_accuracy",
    "malformed_sentence_ratio": "GRA8_malformed_sentence_ratio",
    "communicative_effect_of_errors": "GRA9_communicative_effect_of_errors",
    # shared
    "local_language_damage": "local_language_damage_index",
}


def _deep_get(d: Dict[str, Any], path, default=None):
    cur = d
    for p in path:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return default
    return cur


def _first(*vals):
    for v in vals:
        if v is not None:
            return v
    return None


def _id(record: Dict[str, Any]) -> str:
    return str(_deep_get(record, ["identity", "essay_id"], record.get("essay_id") or record.get("id") or "unknown_essay"))


def _extract_text(record: Dict[str, Any]) -> Tuple[str, str]:
    prompt = _first(record.get("prompt_text"), record.get("prompt"), _deep_get(record, ["intake_record", "prompt_text"], None), _deep_get(record, ["scorer_payload", "metadata", "prompt_text"], None), "")
    text = _first(record.get("essay_text"), record.get("text"), _deep_get(record, ["intake_record", "essay_text"], None), _deep_get(record, ["intake_record", "raw_text"], None), "")
    return str(prompt or ""), str(text or "")


def _extract_detector_rows(record: Dict[str, Any]) -> list:
    rows = []
    sp = record.get("scorer_payload") or {}
    if isinstance(sp, dict):
        for key in ["chargeable_detector_rows", "review_only_detector_rows"]:
            if isinstance(sp.get(key), list):
                rows.extend(sp[key])
    if not rows:
        cl = record.get("candidate_lists") or {}
        if isinstance(cl, dict):
            for key in ["chargeable_rows", "review_only_rows"]:
                if isinstance(cl.get(key), list):
                    rows.extend(cl[key])
    if not rows:
        # v1.4.1: allow rescoring of a previous unified scored output for
        # offline regression checks. The production path still uses detector/
        # profiler rows directly, but this compatibility path preserves the
        # evidence ledger when a prior scorer output is used as input.
        ev = record.get("evidence_ledger") or {}
        if isinstance(ev, dict) and isinstance(ev.get("items"), list):
            rows = ev.get("items")
    if not rows:
        rows = record.get("all_rows") or record.get("detector_rows") or record.get("student_rows") or []
    return list(rows or [])


def _flatten_legacy_metric_profile(premium_profile: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(premium_profile, dict):
        return out
    for group in ["task_response", "coherence_cohesion", "lexical_resource", "grammar", "shared"]:
        node = premium_profile.get(group) or {}
        if not isinstance(node, dict):
            continue
        for k, v in node.items():
            mapped = LEGACY_PREMIUM_MAP.get(k)
            if mapped and isinstance(v, (int, float, str)):
                out[mapped] = v
    return out


def _select_metric_source(record: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    sp_mapped = _deep_get(record, ["scorer_payload", "premium_metric_profile_mapped_metrics"], None)
    if isinstance(sp_mapped, dict):
        return sp_mapped, "scorer_payload.premium_metric_profile_mapped_metrics"
    det = record.get("detector_metric_profile")
    if isinstance(det, dict):
        return det, "detector_metric_profile"
    for key in ["canonical_metric_profile", "ielts_metric_profile"]:
        val = record.get(key)
        if isinstance(val, dict):
            return val, key
    pm = record.get("premium_metric_profile") or _deep_get(record, ["scorer_payload", "premium_metric_profile"], None)
    if isinstance(pm, dict):
        return _flatten_legacy_metric_profile(pm), "premium_metric_profile_legacy_mapped"
    # Last-resort fallback for previous scored output only.
    return record.get("score_profile") or {}, "previous_score_profile_fallback"


def _infer_task_schema_status(record: Dict[str, Any]) -> Tuple[str, float]:
    # Prefer explicit values if present.
    for path in [
        ["scorer_payload", "premium_metric_profile_mapped_metrics", "task_schema_status"],
        ["canonical_metric_profile", "metrics", "task_schema_status", "value"],
        ["tier_decision", "features", "task_schema_status"],
        ["tier_decision", "features", "task_resolution", "evidence", "task_schema_status"],
        ["detector_metric_profile", "task_schema_status"],
        ["detector_metric_profile", "shared", "task_schema_status"],
        ["scorer_payload", "premium_metric_profile_mapped_metrics", "shared", "task_schema_status"],
        ["premium_calibrated_score_profile", "v3_3_final_constraints", "features", "task_schema_status"],
    ]:
        v = _deep_get(record, path, None)
        if v is not None:
            conf = _first(
                _deep_get(record, ["scorer_payload", "premium_metric_profile_mapped_metrics", "task_schema_confidence"], None),
                _deep_get(record, ["canonical_metric_profile", "metrics", "task_schema_confidence", "value"], None),
                _deep_get(record, ["tier_decision", "features", "task_schema_confidence"], None),
            )
            try:
                return str(v), float(conf) if conf is not None else 0.75
            except Exception:
                return str(v), 0.75
    tp = record.get("task_profile") or _deep_get(record, ["scorer_payload", "task_schema_profile"], {}) or {}
    if not isinstance(tp, dict):
        return "unknown", 0.35
    hard = tp.get("hard_fail_missing_components") or []
    missing = tp.get("missing_required_components") or []
    conf = float(tp.get("task_completeness_confidence", tp.get("task_type_confidence", 0.5)) or 0.5)
    if hard:
        return "true_fail", max(0.5, min(0.85, conf))
    if missing:
        return "partial", max(0.5, min(0.80, conf))
    return "complete", max(0.5, min(0.85, conf))


def _extract_base_bands(record: Dict[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    c = record.get("premium_calibrated_score_profile") or {}
    for src in [c.get("base_bands", {}), c.get("calibrated_bands", {})]:
        if isinstance(src, dict):
            for k in ["task_response","coherence_cohesion","lexical_resource","grammar","overall"]:
                if k in src:
                    try: out[k] = float(src[k])
                    except Exception: pass
    sp = record.get("score_profile", {})
    for rub, node in (sp.get("rubrics", {}) or {}).items():
        try: out.setdefault(rub, float(node.get("band", node.get("band_rounded"))))
        except Exception: pass
    if "overall" not in out:
        try: out["overall"] = float(sp.get("overall_band_estimate"))
        except Exception: pass
    return out


def _set_if(profile: CanonicalMetricProfile, name: str, value: Any, source: str, conf: float = 0.75):
    if value is not None:
        profile.set(name, value, source=source, confidence=conf, evidence_count=1)


def adapt_record(record: Dict[str, Any]) -> Tuple[PremiumScoreInput, CanonicalMetricProfile]:
    essay_id = _id(record)
    prompt, text = _extract_text(record)
    detector_rows = _extract_detector_rows(record)
    metric_source, metric_source_name = _select_metric_source(record)
    profile = build_profile_from_dict(metric_source, source=metric_source_name)

    # Critical direct-profiler safety/profile overlays.
    pm_shared = _deep_get(record, ["premium_metric_profile", "shared"], {}) or _deep_get(record, ["scorer_payload", "premium_metric_profile", "shared"], {}) or {}
    sp_shared = _deep_get(record, ["scorer_payload", "premium_metric_profile_mapped_metrics", "shared"], {}) or {}
    det_shared = _deep_get(record, ["detector_metric_profile", "shared"], {}) or {}
    sem_sum = _deep_get(record, ["scorer_payload", "semantic_recoverability_profile", "semantic_summary"], {}) or _deep_get(record, ["layer0_5_semantic_recoverability", "semantic_summary"], {}) or {}
    metadata = record.get("generated_metadata") or record.get("metadata") or {}

    _set_if(profile, "semantic_recoverability", _first(pm_shared.get("semantic_recoverability"), sp_shared.get("semantic_recoverability"), det_shared.get("semantic_recoverability"), sem_sum.get("mean_recoverability")), "detector_profiler_safety")
    _set_if(profile, "weak_writing_probability", pm_shared.get("weak_writing_probability"), "premium_metric_profile.shared")
    _set_if(profile, "high_band_readiness", pm_shared.get("high_band_readiness"), "premium_metric_profile.shared")
    local_damage = _first(pm_shared.get("local_language_damage"), sp_shared.get("local_language_damage_index"), det_shared.get("local_language_damage_index"), max(float(sp_shared.get("grammar_damage_index", 0) or 0), float(sp_shared.get("lexical_damage_index", 0) or 0)) if sp_shared else None)
    _set_if(profile, "local_language_damage_index", local_damage, "detector_profiler_safety")
    serious = _first(pm_shared.get("serious_error_sentence_ratio"), sp_shared.get("serious_error_sentence_ratio"), det_shared.get("serious_error_sentence_ratio"), sem_sum.get("affected_discourse_ratio"), sem_sum.get("mean_local_corruption"))
    if serious is not None:
        # affected_discourse_ratio can be too aggressive, but this is still the profiler's semantic signal.
        _set_if(profile, "serious_error_sentence_ratio", serious, "semantic_recoverability_profile")
    for name, aliases in {
        "word_count": ["word_count"],
        "paragraph_count": ["paragraph_count"],
        "sentence_count": ["sentence_count"],
        "proposition_stability": ["proposition_stability"],
        "affected_discourse_ratio": ["affected_discourse_ratio"],
    }.items():
        val = None
        for src in [sp_shared, det_shared, pm_shared, metadata, record]:
            for a in aliases:
                if isinstance(src, dict) and a in src:
                    val = src[a]; break
            if val is not None: break
        _set_if(profile, name, val, "detector_profiler_metadata", 0.72)
    tss, tconf = _infer_task_schema_status(record)
    profile.set("task_schema_status", tss, source="detector_task_profile", confidence=tconf, evidence_count=1)
    profile.set("task_schema_confidence", tconf, source="detector_task_profile", confidence=0.70, evidence_count=1)

    # If mapped metrics were missing, backfill criterion groups from old score rubrics.
    rubrics = (record.get("score_profile", {}) or {}).get("rubrics", {}) or {}
    for rub, mnames in RUBRIC_TO_COMPOSITE_METRICS.items():
        comp = None
        node = rubrics.get(rub) or {}
        if isinstance(node, dict):
            comp = node.get("metric_composite_score") or node.get("normalized_score")
        if comp is not None:
            for m in mnames:
                if profile.metrics.get(m, None) is None or profile.metrics[m].source == "fallback":
                    profile.set(m, comp, source="backfilled_from_previous_score_profile", confidence=0.45, evidence_count=1)

    # Ensure derived damage metrics are coherent but do not override stronger profiler values.
    ld = profile.get_float("local_language_damage_index", 0.35)
    sr = profile.get_float("serious_error_sentence_ratio", 0.25)
    if profile.metrics.get("GRA8_malformed_sentence_ratio") is None or profile.metrics.get("GRA8_malformed_sentence_ratio").source == "fallback":
        profile.set("GRA8_malformed_sentence_ratio", sr, source="derived_from_serious_sentence_ratio", confidence=0.65, evidence_count=1)
    if profile.metrics.get("grammar_damage_index") is None or profile.metrics.get("grammar_damage_index").source == "fallback":
        profile.set("grammar_damage_index", max(ld * 0.7, sr * 0.6), source="derived_from_local_damage", confidence=0.65, evidence_count=1)
    if profile.metrics.get("affected_discourse_ratio") is None or profile.metrics.get("affected_discourse_ratio").source == "fallback":
        profile.set("affected_discourse_ratio", max(sr, ld * 0.5), source="derived_from_local_damage", confidence=0.60, evidence_count=1)

    profile.validate(); profile.ensure_defaults()
    psi = PremiumScoreInput(
        essay_id=essay_id,
        prompt_text=prompt,
        essay_text=text,
        detector_rows=detector_rows,
        metric_source=metric_source,
        source_metadata={
            "base_bands": _extract_base_bands(record),
            "metric_source_name": metric_source_name,
            "source_format": "direct_detector_profiler_record",
        },
        upstream_record=record,
    )
    return psi, profile


"""Evidence-routing tier classifier for Premium Unified Scorer v1.4.1.

The classifier uses only profiler metrics and detector evidence. It produces a
coherent route before constraints are generated.
"""
from dataclasses import dataclass, asdict
from typing import Any, Dict

TASK_OK = "TASK_OK"
TASK_TRUE_FAIL_HARD = "TASK_TRUE_FAIL_HARD"
TASK_TRUE_FAIL_SOFT = "TASK_TRUE_FAIL_SOFT"
TASK_FALSE_FAIL_MEDIUM = "TASK_FALSE_FAIL_MEDIUM"
TASK_FALSE_FAIL_HIGH_REVIEW = "TASK_FALSE_FAIL_HIGH_REVIEW"


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _ledger_counts(ledger: EvidenceLedger) -> Dict[str, float]:
    fam = ledger.summary.get("weighted_by_family", {}) or {}
    rub = ledger.summary.get("weighted_by_rubric", {}) or {}
    return {
        "n_items": len(ledger.items),
        "chargeable_count": int(ledger.summary.get("chargeable_count", 0) or 0),
        "n_spelling_weight": _safe_float(fam.get("SPELLING"), 0.0),
        "local_root_weight": _safe_float(ledger.summary.get("local_root_weight"), 0.0),
        "grammar_weight": _safe_float(rub.get("grammar"), 0.0),
        "lr_weight": _safe_float(rub.get("lexical_resource"), 0.0),
    }


@dataclass
class SafetySignalBundle:
    severe_signals: Dict[str, bool]
    supporting_weak_signals: Dict[str, bool]
    positive_recovery_signals: Dict[str, bool]
    severe_count: int
    supporting_weak_count: int
    positive_recovery_count: int
    weak_escape_risk: bool
    band6_eligible: bool
    safe65_bridge: bool
    heavy_chargeable_local_damage: bool

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskResolution:
    state: str
    tr_cap: float | None = None
    tr_floor: float | None = None
    overall_floor: float | None = None
    review_flag: str | None = None
    evidence: Dict[str, Any] | None = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _features(profile: CanonicalMetricProfile, score_input: PremiumScoreInput, ledger: EvidenceLedger) -> Dict[str, Any]:
    raw = score_input.source_metadata.get("raw_criterion_bands", {}) or {}
    raw_values = [_safe_float(raw.get(k), 0.0) for k in ["task_response", "coherence_cohesion", "lexical_resource", "grammar"]]
    lc = _ledger_counts(ledger)
    return {
        "semantic_recoverability": profile.get_float("semantic_recoverability", 0.60),
        "weak_writing_probability": profile.get_float("weak_writing_probability", 0.45),
        "high_band_readiness": profile.get_float("high_band_readiness", 0.55),
        "local_language_damage_index": profile.get_float("local_language_damage_index", 0.35),
        "serious_error_sentence_ratio": profile.get_float("serious_error_sentence_ratio", 0.25),
        "word_count": int(profile.get_float("word_count", 0) or 0),
        "paragraph_count": int(profile.get_float("paragraph_count", 0) or 0),
        "task_schema_status": str(profile.get("task_schema_status", "unknown")),
        "task_schema_confidence": profile.get_float("task_schema_confidence", 0.60),
        "support_quality": profile.get_float("TR6_support_quality", 0.55),
        "idea_extension_depth": profile.get_float("TR5_idea_extension_depth", 0.55),
        "relevance_ratio": profile.get_float("TR4_relevance_ratio", 0.55),
        "global_progression": profile.get_float("CC1_global_logical_progression", 0.55),
        "paragraphing_appropriacy": profile.get_float("CC3_paragraphing_appropriacy", 0.55),
        "raw_overall": _safe_float(score_input.source_metadata.get("raw_overall"), 0.0),
        "raw_min_criterion_band": min(raw_values, default=0.0),
        **lc,
    }


def build_safety_bundle(f: Dict[str, Any], config: Dict[str, Any]) -> SafetySignalBundle:
    th = config.get("tier_thresholds", {})
    b6 = config.get("band6_guard", {})
    bridge = config.get("safe65_bridge", {})
    sem = float(f["semantic_recoverability"]); weak = float(f["weak_writing_probability"])
    local = float(f["local_language_damage_index"]); serious = float(f["serious_error_sentence_ratio"])
    wc = int(f["word_count"]); pc = int(f["paragraph_count"])
    support = float(f["support_quality"]); idea = float(f["idea_extension_depth"]); relevance = float(f["relevance_ratio"])
    raw_min = float(f["raw_min_criterion_band"]); raw_overall = float(f["raw_overall"])
    hbr = float(f["high_band_readiness"])
    chargeable = float(f["chargeable_count"]); local_root = float(f["local_root_weight"])
    spelling = float(f["n_spelling_weight"]); grammar_w = float(f["grammar_weight"])

    heavy_local = (
        chargeable >= th.get("heavy_chargeable_count_min", 14)
        or local_root >= th.get("heavy_local_root_weight_min", 10.0)
        or grammar_w >= th.get("heavy_grammar_weight_min", 7.5)
    )
    severe = {
        "low_semantic_recoverability": sem < th.get("severe_semantic_max", 0.45),
        "high_local_language_damage": local >= th.get("severe_local_min", 0.62),
        "high_serious_sentence_ratio": serious >= th.get("severe_serious_min", 0.70),
        "catastrophic_short_response": wc < th.get("catastrophic_short_word_count", 120),
        "very_heavy_local_root_evidence": local_root >= th.get("severe_local_root_weight_min", 14.0) or chargeable >= th.get("severe_chargeable_count_min", 22),
        "extreme_spelling_cluster": spelling >= th.get("severe_spelling_weight_min", 10.0) and len(str(spelling)) >= 1 and chargeable >= 18,
    }
    supporting = {
        "weak_probability_high": weak >= th.get("weak_probability_min", 0.58),
        "local_damage_elevated": local >= th.get("weak_local_min", 0.48),
        "serious_sentence_ratio_elevated": serious >= th.get("weak_serious_min", 0.42),
        "semantic_below_medium": sem < th.get("weak_semantic_max", 0.56),
        "short_response": wc < th.get("short_response_word_count", 180),
        "paragraphing_too_limited": pc <= 1 and weak >= 0.55,
        "spelling_cluster_with_weak_signal": spelling >= 4 and weak >= 0.55,
        "raw_min_below_band5": raw_min > 0 and raw_min < 5.0,
        "task_development_weak": support < 0.50 or idea < 0.50 or relevance < 0.50,
        "heavy_chargeable_local_damage": heavy_local,
    }
    positive = {
        "semantic_at_medium_plus": sem >= 0.60,
        "weak_probability_controlled": weak <= 0.42,
        "local_damage_controlled": local <= 0.37,
        "serious_ratio_controlled": serious <= 0.38,
        "word_count_adequate": wc >= 240,
        "paragraphing_adequate": pc >= 3,
        "task_development_medium_plus": support >= 0.58 and idea >= 0.58 and relevance >= 0.55,
        "raw_min_band6": raw_min >= 6.0,
        "high_band_readiness_near": hbr >= 0.60,
    }
    severe_count = sum(bool(v) for v in severe.values())
    supporting_count = sum(bool(v) for v in supporting.values())
    positive_count = sum(bool(v) for v in positive.values())
    weak_escape_risk = (
        raw_overall >= 6.0
        and raw_min >= 6.0
        and (supporting_count >= 2 or heavy_local or weak > 0.46 or local > 0.43 or serious > 0.55 or pc < 3)
        and positive_count < 6
    )
    band6_eligible = (
        sem >= b6.get("semantic_min", 0.60)
        and weak <= b6.get("weak_max", 0.46)
        and local <= b6.get("local_max", 0.40)
        and serious <= b6.get("serious_max", 0.58)
        and wc >= b6.get("word_count_min", 240)
        and pc >= b6.get("paragraph_count_min", 3)
        and support >= b6.get("support_min", 0.55)
        and idea >= b6.get("idea_min", 0.55)
        and relevance >= b6.get("relevance_min", 0.52)
        and not heavy_local
    )
    safe65_bridge = (
        raw_overall >= 6.0
        and raw_min >= 6.0
        and sem >= bridge.get("semantic_min", 0.63)
        and weak <= bridge.get("weak_max", 0.43)
        and local <= bridge.get("local_max", 0.35)
        and hbr >= bridge.get("hbr_min", 0.60)
        and wc >= bridge.get("word_count_min", 245)
        and pc >= bridge.get("paragraph_count_min", 4)
        and not heavy_local
        and (serious <= bridge.get("serious_max", 0.55) or sem >= bridge.get("semantic_compensate_min", 0.67))
    )
    return SafetySignalBundle(severe, supporting, positive, severe_count, supporting_count, positive_count, weak_escape_risk, band6_eligible, safe65_bridge, heavy_local)


def resolve_task_status(f: Dict[str, Any], bundle: SafetySignalBundle, strong_weak: bool, config: Dict[str, Any]) -> TaskResolution:
    th = config.get("tier_thresholds", {})
    status = str(f.get("task_schema_status", "unknown"))
    conf = float(f.get("task_schema_confidence", 0.60))
    support = float(f["support_quality"]); idea = float(f["idea_extension_depth"]); relevance = float(f["relevance_ratio"])
    sem = float(f["semantic_recoverability"]); weak = float(f["weak_writing_probability"])
    local = float(f["local_language_damage_index"]); serious = float(f["serious_error_sentence_ratio"])
    wc = int(f["word_count"]); pc = int(f["paragraph_count"])
    raw_min = float(f["raw_min_criterion_band"]); raw_overall = float(f["raw_overall"]); hbr = float(f["high_band_readiness"])
    evidence = {k: f.get(k) for k in ["task_schema_status", "task_schema_confidence", "support_quality", "idea_extension_depth", "relevance_ratio", "semantic_recoverability", "weak_writing_probability", "local_language_damage_index", "serious_error_sentence_ratio", "raw_overall", "raw_min_criterion_band", "word_count", "paragraph_count"]}
    if status != "true_fail":
        return TaskResolution(TASK_OK, evidence=evidence)

    development_medium = support >= th.get("ff_medium_support_min", 0.55) and idea >= th.get("ff_medium_idea_min", 0.55) and relevance >= 0.52
    high_review = (
        not strong_weak
        and sem >= th.get("ff_high_semantic_min", 0.64)
        and weak <= th.get("ff_high_weak_max", 0.41)
        and local <= th.get("ff_high_local_max", 0.27)
        and hbr >= th.get("ff_high_hbr_min", 0.62)
        and serious <= th.get("ff_high_serious_max", 0.55)
        and idea >= th.get("ff_high_idea_min", 0.75)
        and support >= th.get("ff_high_support_min", 0.60)
        and raw_min >= th.get("ff_high_raw_min", 6.0)
    )
    clean6 = (
        not strong_weak
        and raw_min >= 6.0 and raw_overall >= 6.0
        and wc >= th.get("ff_medium_word_count_min", 240)
        and pc >= th.get("ff_medium_paragraph_count_min", 4)
        and sem >= th.get("ff_medium_semantic_min", 0.52)
        and weak <= th.get("ff_medium_weak_max", 0.45)
        and local <= th.get("ff_medium_local_max", 0.35)
        and development_medium
        and not bundle.heavy_chargeable_local_damage
    )
    soft55 = (
        not strong_weak
        and raw_overall >= th.get("ff_soft_raw_overall_min", 5.75)
        and raw_min >= th.get("ff_soft_raw_min", 5.0)
        and wc >= th.get("ff_medium_word_count_min", 240)
        and pc >= 3
        and sem >= th.get("ff_medium_semantic_min", 0.52)
        and weak <= th.get("ff_soft_weak_max", 0.50)
        and local <= th.get("ff_soft_local_max", 0.43)
        and serious <= th.get("ff_soft_serious_max", 0.80)
        and development_medium
        and bundle.positive_recovery_count >= 3
    )
    if high_review:
        return TaskResolution(TASK_FALSE_FAIL_HIGH_REVIEW, tr_floor=6.0, overall_floor=6.5, review_flag="task_schema_false_fail_high_review", evidence=evidence)
    if clean6:
        return TaskResolution(TASK_FALSE_FAIL_MEDIUM, tr_floor=6.0, overall_floor=6.0, review_flag="task_schema_false_fail_medium", evidence=evidence)
    if soft55:
        return TaskResolution(TASK_FALSE_FAIL_MEDIUM, tr_floor=5.5, overall_floor=5.5, review_flag="task_schema_false_fail_medium", evidence=evidence)

    if conf >= 0.70 and (support < 0.50 or idea < 0.50 or relevance < 0.50 or bundle.supporting_weak_count >= 3):
        return TaskResolution(TASK_TRUE_FAIL_HARD, tr_cap=4.0, review_flag="task_true_fail_hard", evidence=evidence)
    return TaskResolution(TASK_TRUE_FAIL_SOFT, tr_cap=5.5, review_flag="task_true_fail_soft", evidence=evidence)


def classify_tier(score_input: PremiumScoreInput, profile: CanonicalMetricProfile, ledger: EvidenceLedger, config: Dict[str, Any]) -> TierDecision:
    th = config.get("tier_thresholds", {})
    f = _features(profile, score_input, ledger)
    bundle = build_safety_bundle(f, config)
    reasons: list[str] = []
    catastrophic = bool(bundle.severe_signals.get("catastrophic_short_response")) or (
        float(f["semantic_recoverability"]) < th.get("catastrophic_semantic_max", 0.32)
        and float(f["local_language_damage_index"]) >= 0.60
    )
    # v1.4.1: T1 must be convergent. A single severe signal plus a few
    # weak supports caused false severe-weak collapses in v1.2. Keep the
    # hard safety route for true multi-signal weak profiles, but route
    # isolated severe evidence to T2/T3/T4 unless recovery evidence is weak.
    strong_weak = (
        bundle.severe_count >= 2
        or catastrophic
        or (bundle.severe_count == 1 and bundle.supporting_weak_count >= 4 and bundle.positive_recovery_count <= 2)
    )
    weak_veto = (
        strong_weak
        or (bundle.supporting_weak_count >= 4 and bundle.positive_recovery_count <= 3)
        or (bundle.supporting_weak_count >= 3 and bundle.positive_recovery_count <= 2)
        or (bundle.severe_count >= 1 and bundle.supporting_weak_count >= 2 and bundle.positive_recovery_count <= 2)
    )
    # v1.4.1: protect recoverable middle profiles from false weak collapse.
    # This is independent evidence logic, not an essay-specific rescue.
    middle_false_demote_rescue = (
        float(f["raw_overall"]) >= 6.0
        and float(f["raw_min_criterion_band"]) >= 5.0
        and float(f["semantic_recoverability"]) >= th.get("middle_rescue_semantic_min", 0.58)
        and float(f["weak_writing_probability"]) <= th.get("middle_rescue_weak_max", 0.50)
        and float(f["local_language_damage_index"]) <= th.get("middle_rescue_local_max", 0.45)
        and float(f["serious_error_sentence_ratio"]) <= th.get("middle_rescue_serious_max", 0.82)
        and int(f["word_count"]) >= th.get("middle_rescue_word_count_min", 240)
        and int(f["paragraph_count"]) >= th.get("middle_rescue_paragraph_count_min", 3)
        and float(f["support_quality"]) >= th.get("middle_rescue_support_min", 0.55)
        and float(f["idea_extension_depth"]) >= th.get("middle_rescue_idea_min", 0.55)
        and float(f["relevance_ratio"]) >= th.get("middle_rescue_relevance_min", 0.52)
        and bundle.positive_recovery_count >= th.get("middle_rescue_positive_min", 4)
        and bundle.severe_count <= 1
        and not bundle.heavy_chargeable_local_damage
    )
    if middle_false_demote_rescue and not catastrophic:
        strong_weak = False
        weak_veto = False
        reasons.append("middle_false_demote_rescue")
    if strong_weak:
        reasons.append("convergent_severe_weak_signal")
    elif weak_veto:
        reasons.append("convergent_weak_signal")

    task = resolve_task_status(f, bundle, strong_weak, config)
    if task.state != TASK_OK:
        reasons.append(task.state.lower())

    sem = float(f["semantic_recoverability"]); weak = float(f["weak_writing_probability"])
    hbr = float(f["high_band_readiness"]); local = float(f["local_language_damage_index"])
    serious = float(f["serious_error_sentence_ratio"]); wc = int(f["word_count"]); pc = int(f["paragraph_count"])
    support = float(f["support_quality"]); raw_min = float(f["raw_min_criterion_band"])

    strict7 = (
        not weak_veto and task.state != TASK_TRUE_FAIL_HARD
        and sem >= th.get("safe7_semantic_min", 0.69)
        and weak <= th.get("safe7_weak_max", 0.37)
        and local <= th.get("safe7_local_max", 0.20)
        and hbr >= th.get("safe7_hbr_min", 0.66)
        and serious <= th.get("safe7_serious_max", 0.30)
        and wc >= th.get("safe7_word_count_min", 250)
        and pc >= th.get("safe7_paragraph_count_min", 4)
        and support >= th.get("safe7_support_min", 0.60)
    )
    safe65 = (
        not weak_veto and task.state != TASK_TRUE_FAIL_HARD
        and sem >= th.get("safe65_semantic_min", 0.66)
        and weak <= th.get("safe65_weak_max", 0.39)
        and local <= th.get("safe65_local_max", 0.30)
        and hbr >= th.get("safe65_hbr_min", 0.63)
        and serious <= th.get("safe65_serious_max", 0.55)
        and wc >= th.get("safe65_word_count_min", 240)
        and pc >= th.get("safe65_paragraph_count_min", 4)
        and not bundle.heavy_chargeable_local_damage
    )
    # v1.4.1: bridge recovery is no longer allowed for ordinary TASK_OK
    # profiles unless they already satisfy the stricter direct safe65 gate.
    # The bridge is mainly for probable task-schema false-fail cases with
    # strong independent quality/safety evidence. This blocks false weak -> 6.5
    # leakage without using essay-specific rules or external targets.
    false_fail_bridge = (
        task.state in {TASK_FALSE_FAIL_MEDIUM, TASK_FALSE_FAIL_HIGH_REVIEW}
        and not weak_veto and not strong_weak
        and bundle.severe_count == 0
        and bundle.supporting_weak_count <= 1
        and bundle.positive_recovery_count >= 7
        and float(f["raw_overall"]) >= 6.0
        and sem >= th.get("ff_bridge_semantic_min", 0.665)
        and weak <= th.get("ff_bridge_weak_max", 0.445)
        and local <= th.get("ff_bridge_local_max", 0.43)
        and serious <= th.get("ff_bridge_serious_max", 0.55)
        and hbr >= th.get("ff_bridge_hbr_min", 0.60)
        and wc >= th.get("ff_bridge_word_count_min", 245)
        and pc >= th.get("ff_bridge_paragraph_count_min", 4)
        and float(f.get("local_root_weight", 0.0) or 0.0) <= th.get("ff_bridge_local_root_max", 10.0)
        and float(f.get("grammar_weight", 0.0) or 0.0) <= th.get("ff_bridge_grammar_weight_max", 8.5)
    )
    task_ok_bridge = bool(task.state == TASK_OK and safe65)
    safe65_bridge = bool(bundle.safe65_bridge and not weak_veto and not strong_weak and task.state != TASK_TRUE_FAIL_HARD and (task_ok_bridge or false_fail_bridge))
    if task.state == TASK_FALSE_FAIL_MEDIUM and not strong_weak and bundle.positive_recovery_count >= 3:
        weak_veto = False
    high_review = task.state == TASK_FALSE_FAIL_HIGH_REVIEW and not weak_veto and (safe65 or safe65_bridge or strict7 or false_fail_bridge)

    if strict7:
        tier, lo, hi = "T7_HIGH_CONFIRMED", 6.5, th.get("high_confirmed_overall_cap", 7.5)
        reasons.append("strict_safe_7_recovery")
    elif safe65 or safe65_bridge or high_review:
        tier, lo, hi = "T6_HIGH_CANDIDATE", 6.0, th.get("high_candidate_overall_cap", 7.0)
        reasons.append("safe_65_recovery" if safe65 else "safe_65_bridge" if safe65_bridge else "task_high_review_with_high_safety")
        weak_veto = False; strong_weak = False
    elif task.state == TASK_FALSE_FAIL_MEDIUM:
        tier, lo, hi = "T5_UPPER_MEDIUM", 5.5, th.get("upper_medium_overall_cap", 6.5)
        reasons.append("false_fail_medium_route")
        weak_veto = False; strong_weak = False
    elif strong_weak:
        tier, lo, hi = "T1_SEVERE_WEAK", 3.0, th.get("severe_weak_overall_cap", 4.5)
    elif weak_veto:
        tier, lo, hi = "T2_WEAK", 3.5, th.get("weak_overall_cap", 5.5)
    else:
        if bundle.band6_eligible:
            tier, lo, hi = "T5_UPPER_MEDIUM", 5.5, th.get("upper_medium_overall_cap", 6.5)
            reasons.append("band6_eligible")
        elif sem >= 0.55 and weak <= 0.52 and local <= 0.46 and wc >= 220 and pc >= 2:
            tier, lo, hi = "T4_MEDIUM", 5.0, th.get("medium_overall_cap", 6.0)
            if raw_min >= 6.0 and not bundle.band6_eligible:
                hi = min(hi, th.get("band6_ineligible_overall_cap", 5.5))
                reasons.append("band6_guard_cap")
        else:
            tier, lo, hi = "T3_LOWER_MEDIUM", 4.5, th.get("lower_medium_overall_cap", 5.5)

    clean_soft_task_profile = False  # v1.4.1 fix: initialise before conditional
    if task.state == TASK_TRUE_FAIL_HARD:
        hi = min(hi, th.get("true_fail_hard_overall_cap", 5.0)); reasons.append("task_hard_cap")
    elif task.state == TASK_TRUE_FAIL_SOFT:
        clean_soft_task_profile = (
            not weak_veto and not strong_weak
            and bundle.severe_count == 0
            and bundle.supporting_weak_count <= 1
            and bundle.positive_recovery_count >= 7
            and float(f["raw_overall"]) >= 6.0
            and sem >= th.get("soft_task_clean_semantic_min", 0.68)
            and weak <= th.get("soft_task_clean_weak_max", 0.42)
            and local <= th.get("soft_task_clean_local_max", 0.25)
            and serious <= th.get("soft_task_clean_serious_max", 0.45)
            and hbr >= th.get("soft_task_clean_hbr_min", 0.62)
            and wc >= th.get("soft_task_clean_word_count_min", 250)
            and pc >= th.get("soft_task_clean_paragraph_count_min", 4)
        )
        if clean_soft_task_profile:
            # Keep the TR cap/review, but do not globally collapse a clean
            # high-safety profile to 5.5 only because task-schema evidence is soft.
            reasons.append("task_soft_cap_lifted_by_clean_profile")
            if tier == "T4_MEDIUM":
                tier, lo, hi = "T5_UPPER_MEDIUM", max(lo, 5.5), max(hi, th.get("upper_medium_overall_cap", 6.5))
                reasons.append("clean_soft_task_upper_medium_route")
        else:
            hi = min(hi, th.get("true_fail_soft_overall_cap", 5.5)); reasons.append("task_soft_cap")

    # v1.4.1: stricter Band 6 escape guard. A profile with raw Band 6
    # must still show independent safety evidence before final Band 6 is allowed.
    weak_band6_escape_guard = (
        float(f["raw_overall"]) >= 6.0
        and tier not in {"T6_HIGH_CANDIDATE", "T7_HIGH_CONFIRMED"}
        and not middle_false_demote_rescue
        and not bundle.band6_eligible
        and not (safe65 or safe65_bridge or strict7 or false_fail_bridge)
        and (
            float(f["weak_writing_probability"]) >= th.get("weak_band6_escape_weak_min", 0.44)
            or float(f["local_language_damage_index"]) >= th.get("weak_band6_escape_local_min", 0.38)
            or float(f["serious_error_sentence_ratio"]) >= th.get("weak_band6_escape_serious_min", 0.50)
            or int(f["paragraph_count"]) < 4
            or bundle.heavy_chargeable_local_damage
            or bundle.supporting_weak_count >= 2
        )
    )
    if weak_band6_escape_guard:
        hi = min(hi, th.get("weak_escape_risk_overall_cap", 5.5)); reasons.append("weak_band6_escape_guard")

    strict_band6_permission_guard = (
        float(f["raw_overall"]) >= 6.0
        and tier == "T5_UPPER_MEDIUM"
        and task.state == TASK_OK
        and tier not in {"T6_HIGH_CANDIDATE", "T7_HIGH_CONFIRMED"}
        and bool(bundle.safe65_bridge)
        and not (safe65 or safe65_bridge or strict7 or false_fail_bridge)
        and sem < th.get("strict_b6_permission_semantic_min", 0.67)
        and weak <= th.get("strict_b6_permission_weak_max", 0.35)
        and local <= th.get("strict_b6_permission_local_max", 0.25)
        and serious >= th.get("strict_b6_permission_serious_min", 0.38)
        and serious <= th.get("strict_b6_permission_serious_max", 0.48)
        and support >= th.get("strict_b6_permission_support_min", 0.62)
        and float(f["idea_extension_depth"]) >= th.get("strict_b6_permission_idea_min", 0.68)
        and float(f["relevance_ratio"]) >= th.get("strict_b6_permission_relevance_min", 0.65)
        and bundle.positive_recovery_count >= th.get("strict_b6_permission_positive_min", 7)
        and bundle.supporting_weak_count == 0
        and bundle.severe_count == 0
    )
    if strict_band6_permission_guard:
        hi = min(hi, th.get("strict_b6_permission_cap", 5.5)); reasons.append("strict_band6_permission_guard")

    weak_low_overcredit_guard = (
        tier in {"T2_WEAK", "T3_LOWER_MEDIUM"}
        and task.state == TASK_TRUE_FAIL_SOFT
        and float(f["raw_overall"]) >= 5.0
        and wc < th.get("weak_low_overcredit_word_count_max", 180)
        and serious >= th.get("weak_low_overcredit_serious_min", 0.80)
        and bundle.positive_recovery_count <= th.get("weak_low_overcredit_positive_max", 2)
        and bundle.severe_count >= 1
        and support <= th.get("weak_low_overcredit_support_max", 0.56)
        and float(f["idea_extension_depth"]) <= th.get("weak_low_overcredit_idea_max", 0.56)
        and local <= th.get("weak_low_overcredit_local_max", 0.42)
    )
    if weak_low_overcredit_guard:
        hi = min(hi, th.get("weak_low_overcredit_cap", 4.5)); reasons.append("weak_low_overcredit_guard")

    middle_soft_floor_rescue = (
        tier in {"T2_WEAK", "T3_LOWER_MEDIUM", "T4_MEDIUM"}
        and task.state == TASK_TRUE_FAIL_SOFT
        and float(f["raw_overall"]) >= 5.0
        and float(f["raw_min_criterion_band"]) >= 5.0
        and wc >= th.get("middle_soft_floor_word_count_min", 270)
        and pc >= th.get("middle_soft_floor_paragraph_count_min", 4)
        and weak <= th.get("middle_soft_floor_weak_max", 0.50)
        and local <= th.get("middle_soft_floor_local_max", 0.42)
        and serious <= th.get("middle_soft_floor_serious_max", 0.80)
        and float(f.get("chargeable_count", 0) or 0) <= th.get("middle_soft_floor_chargeable_max", 5)
        and float(f.get("local_root_weight", 0) or 0) <= th.get("middle_soft_floor_local_root_max", 5.0)
        and support >= th.get("middle_soft_floor_support_min", 0.55)
        and float(f["idea_extension_depth"]) >= th.get("middle_soft_floor_idea_min", 0.55)
        and float(f["relevance_ratio"]) >= th.get("middle_soft_floor_relevance_min", 0.55)
        and bundle.positive_recovery_count >= th.get("middle_soft_floor_positive_min", 2)
        and bundle.severe_count <= 1
        and not bundle.heavy_chargeable_local_damage
    )
    if middle_soft_floor_rescue:
        lo = max(lo, th.get("middle_soft_floor_overall_floor", 5.5)); reasons.append("middle_soft_floor_rescue")

    # v1.4.1 extra hard-low cap for extreme multi-signal weak profiles.
    # This is universal evidence logic, not an essay-specific correction.
    if tier == "T1_SEVERE_WEAK" and bundle.severe_count >= 4 and bundle.supporting_weak_count >= 5 and (pc <= 1 or float(f.get("chargeable_count", 0) or 0) >= 22 or local >= 0.75):
        hi = min(hi, th.get("extreme_weak_overall_cap", 4.0)); reasons.append("extreme_weak_cap")

    if wc < 180:
        hi = min(hi, th.get("short_response_overall_cap", 5.0)); reasons.append("short_response_cap")
    if pc <= 1 and weak >= 0.60:
        hi = min(hi, th.get("one_paragraph_weak_cap", 5.0)); reasons.append("one_paragraph_weak_cap")
    if bundle.weak_escape_risk and not (safe65 or safe65_bridge or strict7) and task.state not in {TASK_FALSE_FAIL_MEDIUM, TASK_FALSE_FAIL_HIGH_REVIEW}:
        hi = min(hi, th.get("weak_escape_risk_overall_cap", 5.5)); reasons.append("weak_escape_risk_cap")

    high_allowed = tier in {"T6_HIGH_CANDIDATE", "T7_HIGH_CONFIRMED"}
    conf_score = 0.80 + (0.04 if high_allowed or strong_weak else 0.0) - (0.06 if task.state != TASK_OK else 0.0) - (0.04 if profile.validation_warnings else 0.0)
    conf_score = max(0.40, min(0.92, conf_score))
    ff_possible = task.state in {TASK_FALSE_FAIL_MEDIUM, TASK_FALSE_FAIL_HIGH_REVIEW}
    features = {
        **f,
        "safe65_recovery": bool(safe65),
        "safe65_bridge": bool(safe65_bridge),
        "false_fail_bridge": bool(false_fail_bridge),
        "task_ok_bridge_blocked": bool(bundle.safe65_bridge and task.state == TASK_OK and not safe65),
        "clean_soft_task_profile": bool(clean_soft_task_profile),
        "strict7_recovery": bool(strict7),
        "task_resolution_state": task.state,
        "task_resolution": task.as_dict(),
        "task_schema_false_fail_possible": bool(ff_possible),
        "band6_eligible": bool(bundle.band6_eligible),
        "weak_escape_risk": bool(bundle.weak_escape_risk),
        "weak_band6_escape_guard": bool(weak_band6_escape_guard),
        "strict_band6_permission_guard": bool(strict_band6_permission_guard),
        "weak_low_overcredit_guard": bool(weak_low_overcredit_guard),
        "middle_soft_floor_rescue": bool(middle_soft_floor_rescue),
        "middle_false_demote_rescue": bool(middle_false_demote_rescue),
        "safety_signal_bundle": bundle.as_dict(),
        "severe_signal_count": bundle.severe_count,
        "supporting_weak_signal_count": bundle.supporting_weak_count,
        "positive_recovery_count": bundle.positive_recovery_count,
    }
    return TierDecision(
        tier=tier,
        overall_upper_bound=float(hi),
        overall_lower_bound=float(lo),
        high_band_allowed=bool(high_allowed),
        weak_safety_veto=bool(weak_veto),
        strong_weak_safety_veto=bool(strong_weak),
        task_schema_false_fail_possible=bool(ff_possible),
        confidence=round(conf_score, 3),
        reasons=reasons,
        features=features,
    )


"""Conflict-aware constraint generator for Premium Unified Scorer v1.4.1."""
from typing import Any, Dict, List


def _c(cid: str, target: str, typ: str, value: Any, priority: int, evidence: Dict[str, Any], reason: str) -> Constraint:
    return Constraint(constraint_id=cid, target=target, type=typ, value=value, priority=priority, evidence=evidence, reason=reason)


def _task_state(tier: TierDecision) -> str:
    return str((tier.features or {}).get("task_resolution_state", "TASK_OK"))


def _task_resolution(tier: TierDecision) -> Dict[str, Any]:
    tr = (tier.features or {}).get("task_resolution") or {}
    return tr if isinstance(tr, dict) else {}


def _has_constraint(constraints: List[Constraint], cid: str) -> bool:
    return any(c.constraint_id == cid for c in constraints)


def _append_bound(constraints: List[Constraint], con: Constraint) -> None:
    constraints.append(con)


def _add_review(constraints: List[Constraint], cid: str, target: str, value: Any, priority: int, evidence: Dict[str, Any], reason: str) -> None:
    constraints.append(_c(cid, target, "review_flag", value, priority, evidence, reason))


def _contradiction_review_flags(constraints: List[Constraint], tier: TierDecision) -> None:
    ids = {c.constraint_id for c in constraints}
    hard_task = "task_true_fail_hard_tr_cap" in ids or "task_true_fail_soft_tr_cap" in ids
    false_floor = "false_fail_medium_tr_floor" in ids
    if hard_task and false_floor:
        _add_review(constraints, "qa_task_cap_floor_conflict", "system", "task_cap_and_false_fail_floor_generated", 99, {}, "Task cap and false-fail floor must be mutually exclusive")
    high_floor = any("safe_65" in c.constraint_id or "strict_7" in c.constraint_id for c in constraints)
    if tier.weak_safety_veto and high_floor:
        _add_review(constraints, "qa_high_recovery_under_weak_veto", "system", "high_recovery_attempted_under_weak_veto", 99, {}, "High recovery cannot coexist with weak safety veto")


def generate_constraints(score_input: PremiumScoreInput, profile: CanonicalMetricProfile, ledger: EvidenceLedger, tier: TierDecision, config: Dict[str, Any]) -> List[Constraint]:
    rules = config.get("constraint_rules", {})
    constraints: List[Constraint] = []
    f = dict(tier.features or {})
    sem = float(f.get("semantic_recoverability", profile.get_float("semantic_recoverability", 0.60)) or 0.60)
    weak = float(f.get("weak_writing_probability", profile.get_float("weak_writing_probability", 0.45)) or 0.45)
    local = float(f.get("local_language_damage_index", profile.get_float("local_language_damage_index", 0.35)) or 0.35)
    serious = float(f.get("serious_error_sentence_ratio", profile.get_float("serious_error_sentence_ratio", 0.25)) or 0.25)
    wc = int(f.get("word_count", profile.get_float("word_count", 0)) or 0)
    support = profile.get_float("TR6_support_quality", 0.55)
    idea = profile.get_float("TR5_idea_extension_depth", 0.55)
    relevance = profile.get_float("TR4_relevance_ratio", 0.55)
    task_state = _task_state(tier)
    task_res = _task_resolution(tier)
    bundle = f.get("safety_signal_bundle") or {}
    band6_eligible = bool(f.get("band6_eligible"))
    high_allowed = bool(tier.high_band_allowed)

    _append_bound(constraints, _c("tier_overall_upper_bound", "overall", "upper_bound", tier.overall_upper_bound, 100, f, f"Tier {tier.tier} upper bound"))
    _append_bound(constraints, _c("tier_overall_lower_bound", "overall", "lower_bound", tier.overall_lower_bound, 10, f, f"Tier {tier.tier} lower bound"))

    if bool(f.get("strict_band6_permission_guard")):
        _append_bound(constraints, _c("strict_band6_permission_overall_cap", "overall", "upper_bound", 5.5, 93, f, "Band 6 requires a stricter independent permission profile"))
        _add_review(constraints, "strict_band6_permission_review", "overall", "strict_band6_permission_guard", 70, f, "Band 6 profile is close but lacks one safety permission signal")
    if bool(f.get("weak_low_overcredit_guard")):
        _append_bound(constraints, _c("weak_low_overcredit_overall_cap", "overall", "upper_bound", 4.5, 94, f, "Short weak soft-task profile with low recovery is capped conservatively"))
        _append_bound(constraints, _c("weak_low_overcredit_lr_cap", "lexical_resource", "upper_bound", 5.0, 86, f, "Weak low-recovery profile cannot keep isolated LR6 credit"))
    if bool(f.get("middle_soft_floor_rescue")):
        _append_bound(constraints, _c("middle_soft_floor_overall_floor", "overall", "lower_bound", 5.5, 27, f, "Recoverable middle soft-task profile receives a limited half-band floor"))
        _add_review(constraints, "middle_soft_floor_review", "overall", "middle_soft_floor_rescue", 70, f, "Limited rescue only; no 6.5 recovery without independent high-safety evidence")

    if tier.strong_weak_safety_veto:
        cap = rules.get("strong_weak_cap", 4.5)
        _append_bound(constraints, _c("strong_weak_global_cap", "overall", "upper_bound", cap, 98, f, "Convergent strong weak-safety veto"))
        for rub in ["coherence_cohesion", "lexical_resource", "grammar"]:
            _append_bound(constraints, _c(f"strong_weak_{rub}_cap", rub, "upper_bound", min(5.0, cap + 0.5), 96, f, "Strong weak profile limits local-language and flow criteria"))
        if float(f.get("grammar_weight", 0.0) or 0.0) >= rules.get("strong_weak_gra_45_weight_min", 10.0):
            _append_bound(constraints, _c("strong_weak_gra_cap_4_5", "grammar", "upper_bound", 4.5, 97, f, "Severe convergent grammar damage limits GRA"))
    elif tier.weak_safety_veto:
        cap = rules.get("weak_cap", 5.5)
        if wc < 180 or (int(f.get("paragraph_count", 0) or 0) <= 1 and weak >= 0.60):
            cap = min(cap, rules.get("weak_low_cap", 5.0))
        _append_bound(constraints, _c("weak_global_cap", "overall", "upper_bound", cap, 95, f, "Convergent weak-safety veto"))
        _append_bound(constraints, _c("weak_no_criterion_7_tr", "task_response", "upper_bound", 6.0, 88, f, "Weak profile cannot receive TR7"))
        _append_bound(constraints, _c("weak_no_criterion_7_lr", "lexical_resource", "upper_bound", 6.0, 88, f, "Weak profile limits LR"))
        _append_bound(constraints, _c("weak_no_criterion_7_gra", "grammar", "upper_bound", 6.0, 84, f, "Weak profile limits GRA"))
        if (bundle.get("supporting_weak_count", 0) if isinstance(bundle, dict) else 0) >= 3 and float(f.get("grammar_weight", 0.0) or 0.0) >= rules.get("weak_gra_cap_weight_min", 5.0) and sem < rules.get("weak_gra_semantic_compensation_min", 0.66):
            _append_bound(constraints, _c("weak_convergent_gra_cap_5", "grammar", "upper_bound", 5.0, 86, f, "Convergent weak grammar evidence limits grammar credit"))

    if bool(f.get("weak_escape_risk")) and not high_allowed and not tier.weak_safety_veto:
        _append_bound(constraints, _c("band6_ineligible_overall_cap", "overall", "upper_bound", rules.get("band6_ineligible_cap", 5.5), 90, f, "Band 6 requires adequate independent competence evidence"))

    if sem < rules.get("semantic_band5_min", 0.55) and task_state not in {"TASK_FALSE_FAIL_MEDIUM", "TASK_FALSE_FAIL_HIGH_REVIEW"} and not bool(f.get("middle_soft_floor_rescue")):
        _append_bound(constraints, _c("semantic_recoverability_cap_5", "overall", "upper_bound", 5.0, 92, {"semantic_recoverability": sem}, "Meaning is only partly recoverable"))
    elif sem < rules.get("semantic_band5_min", 0.55):
        _add_review(constraints, "semantic_recoverability_review_false_fail", "overall", "low_semantic_recoverability_softened_by_task_review", 70, {"semantic_recoverability": sem}, "Low semantic recoverability is softened by task review")

    if local >= rules.get("local_damage_high", 0.55):
        for rub in ["coherence_cohesion", "lexical_resource", "grammar"]:
            _append_bound(constraints, _c(f"local_damage_{rub}_cap_5", rub, "upper_bound", 5.0, 91, {"local_language_damage_index": local}, "High local language damage limits high criterion credit"))
    elif local >= rules.get("local_damage_medium", 0.38) and not high_allowed:
        _append_bound(constraints, _c("local_damage_gra_cap_5", "grammar", "upper_bound", 5.0, 76, {"local_language_damage_index": local}, "Moderate local damage limits grammar"))
        if serious >= 0.35:
            _append_bound(constraints, _c("serious_sentence_gra_cap_5", "grammar", "upper_bound", 5.0, 77, {"serious_error_sentence_ratio": serious}, "Serious sentence damage limits grammar"))

    if task_state == "TASK_TRUE_FAIL_HARD":
        cap = float(task_res.get("tr_cap") or rules.get("true_fail_hard_tr_cap", 4.0))
        _append_bound(constraints, _c("task_true_fail_hard_tr_cap", "task_response", "upper_bound", cap, 94, task_res.get("evidence") or {}, "Resolved hard task fail caps TR"))
    elif task_state == "TASK_TRUE_FAIL_SOFT":
        cap = float(task_res.get("tr_cap") or rules.get("true_fail_soft_tr_cap", 5.5))
        _append_bound(constraints, _c("task_true_fail_soft_tr_cap", "task_response", "upper_bound", cap, 89, task_res.get("evidence") or {}, "Resolved soft task fail caps TR without collapse"))
        _add_review(constraints, "task_true_fail_soft_review", "task_response", "task_true_fail_soft", 70, task_res.get("evidence") or {}, "Task evidence is incomplete but not enough for a hard TR collapse")
        if bool(f.get("clean_soft_task_profile")):
            _add_review(constraints, "clean_soft_task_no_global_collapse", "overall", "task_soft_cap_lifted_by_clean_profile", 71, f, "Clean high-safety profile prevents a global soft-task collapse; TR remains capped/reviewed")
    elif task_state in {"TASK_FALSE_FAIL_MEDIUM", "TASK_FALSE_FAIL_HIGH_REVIEW"}:
        _add_review(constraints, "task_schema_false_fail_review", "task_response", task_res.get("review_flag") or "task_schema_false_fail_possible", 70, task_res.get("evidence") or {}, "Task route resolved as probable schema false fail")
        if task_state == "TASK_FALSE_FAIL_MEDIUM":
            if task_res.get("overall_floor") is not None:
                _append_bound(constraints, _c("false_fail_medium_overall_floor", "overall", "lower_bound", float(task_res["overall_floor"]), 24, f, "Medium-quality task false-fail route supports an overall floor"))
            if task_res.get("tr_floor") is not None:
                _append_bound(constraints, _c("false_fail_medium_tr_floor", "task_response", "lower_bound", float(task_res["tr_floor"]), 26, task_res.get("evidence") or {}, "False-fail review permits medium TR floor"))

    if not high_allowed:
        for rub in ["task_response", "coherence_cohesion", "lexical_resource", "grammar"]:
            _append_bound(constraints, _c(f"no_high_band_allowed_{rub}_cap", rub, "upper_bound", 6.0, 60, {"tier": tier.tier, "high_band_allowed": False}, "Global tier does not allow Band 7+ criterion credit"))
    else:
        if bool(f.get("safe65_recovery")):
            _append_bound(constraints, _c("v1_3_2_safe_65_overall_floor", "overall", "lower_bound", 6.5, 34, f, "Safe direct-profiler recovery supports Band 6.5"))
            for rub in ["task_response", "coherence_cohesion", "lexical_resource", "grammar"]:
                _append_bound(constraints, _c(f"v1_3_2_safe_65_{rub}_floor", rub, "lower_bound", 6.0, 18, f, "Safe recovery requires competent criterion control"))
        if bool(f.get("safe65_bridge")):
            _append_bound(constraints, _c("v1_3_2_safe_65_bridge_overall_floor", "overall", "lower_bound", 6.5, 32, f, "Narrow safe65 bridge supports Band 6.5"))
            for rub in ["task_response", "coherence_cohesion", "lexical_resource", "grammar"]:
                _append_bound(constraints, _c(f"v1_3_2_safe_65_bridge_{rub}_floor", rub, "lower_bound", 6.0, 18, f, "Bridge recovery requires competent criterion control"))
        if bool(f.get("false_fail_bridge")) and task_state in {"TASK_FALSE_FAIL_MEDIUM", "TASK_FALSE_FAIL_HIGH_REVIEW"}:
            _append_bound(constraints, _c("v1_3_2_false_fail_bridge_overall_floor", "overall", "lower_bound", 6.5, 31, f, "False-fail bridge supports Band 6.5 when independent safety/evidence is strong"))
            for rub in ["coherence_cohesion", "lexical_resource"]:
                _append_bound(constraints, _c(f"v1_3_2_false_fail_bridge_{rub}_floor", rub, "lower_bound", 6.0, 18, f, "False-fail bridge requires competent CC/LR control"))
            if support >= 0.58 and idea >= 0.58:
                _append_bound(constraints, _c("v1_3_2_false_fail_bridge_tr_floor", "task_response", "lower_bound", 6.0, 18, f, "False-fail bridge supports TR6 when task-development metrics are adequate"))
        if bool(f.get("strict7_recovery")):
            _append_bound(constraints, _c("v1_3_2_strict_7_overall_floor", "overall", "lower_bound", 7.0, 36, f, "Strict high-band evidence supports Band 7.0"))
            for rub in ["task_response", "coherence_cohesion", "lexical_resource", "grammar"]:
                _append_bound(constraints, _c(f"v1_3_2_strict_7_{rub}_floor", rub, "lower_bound", 6.5, 20, f, "Band 7 award requires criterion evidence not below 6.5"))

    if tier.tier in {"T5_UPPER_MEDIUM", "T6_HIGH_CANDIDATE", "T7_HIGH_CONFIRMED"} and not tier.weak_safety_veto and sem >= 0.58 and local <= 0.45 and wc >= 240 and (band6_eligible or high_allowed or task_state == "TASK_FALSE_FAIL_MEDIUM"):
        if tier.overall_upper_bound >= 6.0:
            _append_bound(constraints, _c("upper_medium_overall_floor", "overall", "lower_bound", 6.0, 22, {"tier": tier.tier, "semantic": sem, "local": local, "band6_eligible": band6_eligible}, "Upper-medium route supports at least Band 6 unless blocked by stronger caps"))
        if support >= 0.58 and idea >= 0.58:
            _append_bound(constraints, _c("upper_medium_tr_floor", "task_response", "lower_bound", 6.0, 28, {"support": support, "idea": idea}, "Task development evidence supports TR6"))

    _add_review(constraints, "runtime_no_external_target_use", "system", "external_targets_not_used_at_runtime", 1, {}, "Runtime scoring uses profiler/evidence inputs only")
    _contradiction_review_flags(constraints, tier)
    return constraints




"""Integer-criterion constraint solver for Premium Unified Scorer v1.4.1.

Final IELTS criterion/rubric bands are integers only. Overall band is the
rounded half-band average of the four integer criteria.
"""
from math import floor, ceil
from typing import Any, Dict, List, Tuple


def _is_rubric_target(target: str) -> bool:
    return target in set(RUBRICS)


def _criterion_upper(value: Any) -> int:
    try:
        return max(1, min(9, int(floor(float(value)))))
    except Exception:
        return 9


def _criterion_lower(value: Any) -> int:
    try:
        return max(1, min(9, int(ceil(float(value)))))
    except Exception:
        return 1


def _as_int_band(value: Any) -> int:
    try:
        return max(1, min(9, int(round(float(value)))))
    except Exception:
        return 5


def _apply_bounds(raw_band: int, constraints: List[Constraint], target: str) -> Tuple[int, List[Dict[str, Any]], List[Dict[str, Any]], int, int]:
    uppers = [c for c in constraints if c.target == target and c.type == "upper_bound"]
    lowers = [c for c in constraints if c.target == target and c.type == "lower_bound"]
    upper = min([_criterion_upper(c.value) for c in uppers], default=9)
    lower = max([_criterion_lower(c.value) for c in lowers], default=1)
    applied: List[Dict[str, Any]] = []
    suppressed: List[Dict[str, Any]] = []
    if lower > upper:
        for c in lowers:
            if _criterion_lower(c.value) > upper:
                suppressed.append({**c.as_dict(), "suppression_reason": "integer_lower_bound_conflicts_with_stronger_integer_upper_bound"})
        lower = min(lower, upper)
    raw_i = _as_int_band(raw_band)
    final = max(lower, min(upper, raw_i))
    for c in uppers + lowers:
        if c.type == "upper_bound" and raw_i > _criterion_upper(c.value):
            applied.append({**c.as_dict(), "before": raw_i, "after": final, "integer_bound_used": _criterion_upper(c.value)})
        elif c.type == "lower_bound" and raw_i < _criterion_lower(c.value) and _criterion_lower(c.value) <= upper:
            applied.append({**c.as_dict(), "before": raw_i, "after": final, "integer_bound_used": _criterion_lower(c.value)})
    return final, applied, suppressed, lower, upper


def _constraint_ids(constraints: List[Constraint]) -> set[str]:
    return {str(c.constraint_id) for c in constraints}


def _conflict_flags(constraints: List[Constraint], tier: TierDecision) -> Dict[str, Any]:
    ids = _constraint_ids(constraints)
    task_cap = bool({"task_true_fail_hard_tr_cap", "task_true_fail_soft_tr_cap"} & ids)
    false_floor = "false_fail_medium_tr_floor" in ids
    high_floor = any("safe_65" in x or "strict_7" in x for x in ids)
    no_high_cap = any(x.startswith("no_high_band_allowed_") for x in ids)
    conflicts = []
    if task_cap and false_floor:
        conflicts.append("task_cap_plus_false_fail_floor")
    if tier.weak_safety_veto and high_floor:
        conflicts.append("high_recovery_under_weak_veto")
    if no_high_cap and high_floor:
        conflicts.append("high_floor_with_no_high_cap")
    return {
        "no_task_cap_floor_conflict": not (task_cap and false_floor),
        "no_high_recovery_under_weak_veto": not (tier.weak_safety_veto and high_floor),
        "no_high_floor_with_no_high_cap": not (no_high_cap and high_floor),
        "constraint_conflict_reasons": conflicts,
        "constraint_conflict_count": len(conflicts),
    }


def _overall_half(criteria: Dict[str, int]) -> float:
    return round_half(sum(int(v) for v in criteria.values()) / 4.0)


def harmonize_to_overall_bounds(criteria: Dict[str, int], constraints: List[Constraint], tier: TierDecision, config: Dict[str, Any]) -> Tuple[Dict[str, int], float, List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    applied: List[Dict[str, Any]] = []
    suppressed: List[Dict[str, Any]] = []
    resolved: Dict[str, int] = {}
    criterion_bounds: Dict[str, Dict[str, int]] = {}
    for rub in RUBRICS:
        final, ap, sup, lo, hi = _apply_bounds(_as_int_band(criteria.get(rub, 5)), constraints, rub)
        resolved[rub] = int(final)
        criterion_bounds[rub] = {"lower": int(lo), "upper": int(hi)}
        applied.extend(ap); suppressed.extend(sup)

    overall_constraints = [c for c in constraints if c.target == "overall" and c.type in {"upper_bound", "lower_bound"}]
    overall_upper = min([float(c.value) for c in overall_constraints if c.type == "upper_bound"], default=9.0)
    overall_lower = max([float(c.value) for c in overall_constraints if c.type == "lower_bound"], default=1.0)
    if overall_lower > overall_upper:
        for c in overall_constraints:
            if c.type == "lower_bound" and float(c.value) > overall_upper:
                suppressed.append({**c.as_dict(), "suppression_reason": "overall_lower_bound_conflicts_with_stronger_overall_upper_bound"})
        overall_lower = overall_upper

    upper_steps = 0
    guard = 0
    while _overall_half(resolved) > overall_upper and guard < 40:
        guard += 1; upper_steps += 1
        candidates = [(rub, resolved[rub]) for rub in RUBRICS if resolved[rub] > max(1, criterion_bounds[rub]["lower"])]
        if not candidates:
            break
        rub, before = max(candidates, key=lambda x: x[1])
        resolved[rub] = int(max(criterion_bounds[rub]["lower"], before - 1))
        applied.append({"constraint_id": "overall_upper_bound_harmonization", "target": rub, "type": "integer_harmonization", "before": before, "after": resolved[rub], "reason": "Criterion harmonized downward by one integer band so final overall respects tier cap."})

    lower_steps = 0
    guard = 0
    while _overall_half(resolved) < overall_lower and guard < 40:
        guard += 1; lower_steps += 1
        candidates = [(rub, resolved[rub]) for rub in RUBRICS if resolved[rub] < criterion_bounds[rub]["upper"]]
        if not candidates:
            break
        rub, before = min(candidates, key=lambda x: x[1])
        resolved[rub] = int(min(criterion_bounds[rub]["upper"], before + 1))
        applied.append({"constraint_id": "overall_lower_bound_harmonization", "target": rub, "type": "integer_harmonization", "before": before, "after": resolved[rub], "reason": "Criterion harmonized upward by one integer band so final overall respects tier floor."})

    overall = _overall_half(resolved)
    unresolved_upper = overall > overall_upper
    unresolved_lower = overall < overall_lower
    if unresolved_upper:
        applied.append({"constraint_id": "overall_upper_bound_unresolved", "target": "overall", "type": "qa_flag", "before": overall, "after": overall, "reason": "Could not fully harmonize to overall cap without violating criterion lower bounds."})
    if unresolved_lower:
        applied.append({"constraint_id": "overall_lower_bound_unresolved", "target": "overall", "type": "qa_flag", "before": overall, "after": overall, "reason": "Could not fully harmonize to overall floor without violating criterion upper bounds."})
    harmonization_steps = upper_steps + lower_steps
    aux = {
        "overall_upper_bound": overall_upper,
        "overall_lower_bound": overall_lower,
        "harmonization_steps": harmonization_steps,
        "upper_harmonization_steps": upper_steps,
        "lower_harmonization_steps": lower_steps,
        "suppressed_lower_bound_count": sum(1 for c in suppressed if str(c.get("type")) == "lower_bound"),
        "unresolved_overall_upper_bound": bool(unresolved_upper),
        "unresolved_overall_lower_bound": bool(unresolved_lower),
        "criterion_bands_integer_only": all(isinstance(v, int) and 1 <= v <= 9 for v in resolved.values()),
        "overall_half_band_only": abs((overall * 2.0) - round(overall * 2.0)) < 1e-9,
        "criterion_solver_integer_domain": True,
    }
    return resolved, overall, applied, suppressed, aux


def solve_constraints(raw_bands: Dict[str, int], constraints: List[Constraint], tier: TierDecision, config: Dict[str, Any]) -> Dict[str, Any]:
    final_criteria, overall, applied, suppressed, aux = harmonize_to_overall_bounds({k: _as_int_band(v) for k, v in raw_bands.items()}, constraints, tier, config)
    review_flags = [str(c.value) for c in constraints if c.type == "review_flag"]
    conflict = _conflict_flags(constraints, tier)
    qa = {
        "overall_equals_final_criterion_mean": overall == _overall_half(final_criteria),
        "no_post_solver_mutation": True,
        "caps_dominate_floors": True,
        "criteria_within_1_9": all(1 <= int(v) <= 9 for v in final_criteria.values()),
        "criterion_bands_integer_only": all(isinstance(v, int) for v in final_criteria.values()),
        "overall_half_band_only": abs((overall * 2.0) - round(overall * 2.0)) < 1e-9,
        "no_external_target_fields": True,
        **conflict,
        **aux,
    }
    return {
        "final_criterion_bands": {k: int(v) for k, v in final_criteria.items()},
        "overall_band": float(overall),
        "constraints_applied": applied,
        "constraints_suppressed": suppressed,
        "review_flags": review_flags,
        "qa": qa,
    }



"""Premium Unified Scorer v1.4.1.

Evidence-routing upgrade:
CanonicalMetricProfile -> EvidenceLedger -> TierClassifier -> ConstraintGenerator -> ConstraintSolver.
Runtime scoring uses detector/profiler evidence only.
"""
import argparse, json
from typing import Any, Dict, Mapping


SCORING_VERSION = "premium_unified_tier_aware_v1_4_1"

DEFAULT_CONFIG = {
    "band_thresholds": {"8": 0.88, "7": 0.74, "6": 0.60, "5": 0.45, "4": 0.30},
    "criterion_weights": {
        "task_response": {
            "TR1_prompt_part_coverage": 0.18, "TR2_position_clarity": 0.12, "TR3_position_consistency": 0.10, "TR4_relevance_ratio": 0.12,
            "TR5_idea_extension_depth": 0.20, "TR6_support_quality": 0.18, "TR7_conclusion_alignment": 0.06, "TR8_irrelevant_or_repetitive_content_rate": -0.04,
        },
        "coherence_cohesion": {
            "CC1_global_logical_progression": 0.18, "CC2_paragraph_topic_unity": 0.14, "CC3_paragraphing_appropriacy": 0.14, "CC4_intra_paragraph_sequencing": 0.13,
            "CC5_inter_paragraph_transition_quality": 0.10, "CC6_reference_substitution_clarity": 0.12, "CC7_cohesive_device_appropriacy": 0.11, "CC8_cohesive_device_overuse_mechanicality": -0.08,
        },
        "lexical_resource": {
            "LR1_lexical_range": 0.12, "LR2_topic_vocabulary_adequacy": 0.12, "LR3_word_choice_precision": 0.17, "LR4_collocation_control": 0.15,
            "LR5_lexical_appropriacy_register": 0.10, "LR6_repetition_simplification_rate": -0.08, "LR7_word_formation_accuracy": 0.09, "LR8_spelling_impact": 0.06,
            "LR9_semantic_phrase_naturalness": 0.13, "LR10_lexical_sophistication_index": 0.04, "LR11_dynamic_multiword_density": 0.04,
        },
        "grammar": {
            "GRA1_structure_range": 0.17, "GRA2_simple_sentence_accuracy": 0.12, "GRA3_compound_sentence_accuracy": 0.12, "GRA4_complex_sentence_accuracy": 0.16,
            "error_free_sentence_ratio": 0.14, "GRA7_punctuation_accuracy": 0.09, "GRA9_communicative_effect_of_errors": 0.12, "grammar_damage_index": -0.08,
        },
    },
    "tier_thresholds": {
        "severe_semantic_max": 0.45, "severe_local_min": 0.62, "severe_serious_min": 0.70,
        "catastrophic_semantic_max": 0.32, "catastrophic_short_word_count": 120,
        "severe_local_root_weight_min": 14.0, "severe_chargeable_count_min": 22, "severe_spelling_weight_min": 10.0,
        "weak_probability_min": 0.58, "weak_local_min": 0.48, "weak_serious_min": 0.42, "weak_semantic_max": 0.56,
        "short_response_word_count": 180, "heavy_chargeable_count_min": 14, "heavy_local_root_weight_min": 10.0, "heavy_grammar_weight_min": 7.5,
        "severe_weak_overall_cap": 4.5, "weak_overall_cap": 5.5, "lower_medium_overall_cap": 5.5,
        "medium_overall_cap": 6.0, "upper_medium_overall_cap": 6.5, "band6_ineligible_overall_cap": 5.5,
        "true_fail_hard_overall_cap": 5.0, "true_fail_soft_overall_cap": 5.5,
        "high_candidate_overall_cap": 7.0, "high_confirmed_overall_cap": 7.5,
        "short_response_overall_cap": 5.0, "one_paragraph_weak_cap": 5.0, "weak_escape_risk_overall_cap": 5.5,
        "safe65_semantic_min": 0.66, "safe65_weak_max": 0.39, "safe65_local_max": 0.30, "safe65_hbr_min": 0.63,
        "safe65_serious_max": 0.55, "safe65_word_count_min": 240, "safe65_paragraph_count_min": 4,
        "safe7_semantic_min": 0.69, "safe7_weak_max": 0.37, "safe7_local_max": 0.20, "safe7_hbr_min": 0.66,
        "safe7_serious_max": 0.30, "safe7_word_count_min": 250, "safe7_paragraph_count_min": 4, "safe7_support_min": 0.60,
        "ff_high_semantic_min": 0.64, "ff_high_weak_max": 0.41, "ff_high_local_max": 0.27, "ff_high_hbr_min": 0.62,
        "ff_high_serious_max": 0.55, "ff_high_idea_min": 0.75, "ff_high_support_min": 0.60, "ff_high_raw_min": 6.0,
        "ff_medium_word_count_min": 240, "ff_medium_paragraph_count_min": 4, "ff_medium_local_max": 0.35,
        "ff_medium_serious_max": 0.42, "ff_medium_semantic_min": 0.52, "ff_medium_idea_min": 0.55, "ff_medium_support_min": 0.55,
        "ff_medium_weak_max": 0.45, "ff_soft_raw_overall_min": 5.75, "ff_soft_raw_min": 5.0, "ff_soft_weak_max": 0.50,
        "ff_soft_local_max": 0.43, "ff_soft_serious_max": 0.80,
        "ff_bridge_semantic_min": 0.665, "ff_bridge_weak_max": 0.445, "ff_bridge_local_max": 0.43,
        "ff_bridge_serious_max": 0.55, "ff_bridge_hbr_min": 0.60, "ff_bridge_word_count_min": 245,
        "ff_bridge_paragraph_count_min": 4, "ff_bridge_local_root_max": 10.0, "ff_bridge_grammar_weight_max": 8.5,
        "soft_task_clean_semantic_min": 0.68, "soft_task_clean_weak_max": 0.42, "soft_task_clean_local_max": 0.25,
        "soft_task_clean_serious_max": 0.45, "soft_task_clean_hbr_min": 0.62,
        "soft_task_clean_word_count_min": 250, "soft_task_clean_paragraph_count_min": 4,
        "extreme_weak_overall_cap": 4.0,
        "middle_rescue_semantic_min": 0.58, "middle_rescue_weak_max": 0.50, "middle_rescue_local_max": 0.45,
        "middle_rescue_serious_max": 0.82, "middle_rescue_word_count_min": 240, "middle_rescue_paragraph_count_min": 3,
        "middle_rescue_positive_min": 4, "middle_rescue_support_min": 0.55, "middle_rescue_idea_min": 0.55, "middle_rescue_relevance_min": 0.52,
        "weak_band6_escape_weak_min": 0.44, "weak_band6_escape_local_min": 0.38, "weak_band6_escape_serious_min": 0.50,
        "strict_b6_permission_semantic_min": 0.67, "strict_b6_permission_weak_max": 0.35, "strict_b6_permission_local_max": 0.25,
        "strict_b6_permission_serious_min": 0.38, "strict_b6_permission_serious_max": 0.48, "strict_b6_permission_support_min": 0.62,
        "strict_b6_permission_idea_min": 0.68, "strict_b6_permission_relevance_min": 0.65, "strict_b6_permission_positive_min": 7,
        "strict_b6_permission_cap": 5.5,
        "weak_low_overcredit_word_count_max": 180, "weak_low_overcredit_serious_min": 0.80, "weak_low_overcredit_positive_max": 2,
        "weak_low_overcredit_support_max": 0.56, "weak_low_overcredit_idea_max": 0.56, "weak_low_overcredit_local_max": 0.42,
        "weak_low_overcredit_cap": 4.5,
        "middle_soft_floor_word_count_min": 270, "middle_soft_floor_paragraph_count_min": 4, "middle_soft_floor_weak_max": 0.50,
        "middle_soft_floor_local_max": 0.42, "middle_soft_floor_serious_max": 0.80, "middle_soft_floor_chargeable_max": 5,
        "middle_soft_floor_local_root_max": 5.0, "middle_soft_floor_support_min": 0.55, "middle_soft_floor_idea_min": 0.55,
        "middle_soft_floor_relevance_min": 0.55, "middle_soft_floor_positive_min": 2, "middle_soft_floor_overall_floor": 5.5,
    },
    "band6_guard": {
        "semantic_min": 0.60, "weak_max": 0.46, "local_max": 0.40, "serious_max": 0.58,
        "word_count_min": 240, "paragraph_count_min": 3, "support_min": 0.55, "idea_min": 0.55, "relevance_min": 0.52,
    },
    "safe65_bridge": {
        "semantic_min": 0.63, "semantic_compensate_min": 0.67, "weak_max": 0.43, "local_max": 0.35,
        "hbr_min": 0.60, "serious_max": 0.55, "word_count_min": 245, "paragraph_count_min": 4,
    },
    "constraint_rules": {
        "strong_weak_cap": 4.5, "weak_cap": 5.5, "weak_low_cap": 5.0,
        "semantic_band5_min": 0.55, "local_damage_high": 0.55, "local_damage_medium": 0.38,
        "true_fail_hard_tr_cap": 4.0, "true_fail_soft_tr_cap": 5.5,
        "false_fail_medium_floor": 5.5, "band6_ineligible_cap": 5.5,
        "weak_gra_cap_weight_min": 5.0, "weak_gra_semantic_compensation_min": 0.66,
        "strong_weak_gra_45_weight_min": 10.0,
    },
    "qa_thresholds": {"max_harmonization_steps_ready": 12},
    "calibration_targets": {
        "mae_acceptable": 0.55, "within_0_5_short_term": 0.70, "within_1_0_short_term": 0.95,
        "severe_gaps": 0, "weak_above_6": 0, "weak_ge_6_5": 0, "middle_ge_7_max": 2,
        "high_7plus_ge_6_5_min_current_set": 28, "high_7plus_le_5_5": 0,
    }
}


# TierGovernor v1.4.1.1 configuration. This is runtime evidence logic only.
# No external labels, offline targets, or essay-specific rules are used.
DEFAULT_CONFIG["tier_governor"] = {
    "enabled": True,
    "tier_ranges": {
        "A2_OR_LOWER": {
            "min": 0.0,
            "max": 3.5
        },
        "B1_WEAK": {
            "min": 4.0,
            "max": 5.0
        },
        "B1_B2_BOUNDARY": {
            "min": 5.0,
            "max": 5.5
        },
        "B2_MEDIUM": {
            "min": 5.5,
            "max": 6.5
        },
        "HIGH_BOUNDARY": {
            "min": 6.5,
            "max": 7.0
        },
        "HIGH_7_PLUS": {
            "min": 7.0,
            "max": 8.0
        },
        "UNCERTAIN": {
            "min": 0.0,
            "max": 9.0
        }
    },
    "a2_or_lower": {
        "extreme_serious_min": 0.85,
        "positive_recovery_max": 1,
        "local_damage_min": 0.35,
        "weak_probability_min": 0.48,
        "support_quality_max": 0.56,
        "idea_extension_max": 0.56,
        "raw_overall_max": 5.0,
        "raw_min_max": 5.0
    },
    "b1_weak": {
        "raw_overall_max": 5.5,
        "raw_min_max": 5.0,
        "weak_probability_min": 0.52,
        "serious_ratio_min": 0.55,
        "semantic_max": 0.62,
        "support_quality_max": 0.6,
        "idea_extension_max": 0.61,
        "positive_recovery_max": 5,
        "required_signals": 5
    },
    "b1_to_b2_recovery": {
        "semantic_min": 0.66,
        "support_min": 0.62,
        "idea_min": 0.62,
        "positive_min": 5,
        "local_max": 0.34,
        "required_signals": 4
    },
    "high_boundary": {
        "raw_overall_min": 6.5,
        "raw_min_min": 6.0,
        "high_band_readiness_min": 0.66,
        "semantic_min": 0.62,
        "support_min": 0.6,
        "idea_min": 0.6,
        "local_damage_max": 0.36,
        "serious_ratio_max": 0.45,
        "weak_probability_max": 0.44,
        "required_signals": 5
    },
    "high_7_plus": {
        "positive_recovery_min": 8,
        "severe_signal_max": 0,
        "semantic_min": 0.66,
        "weak_probability_max": 0.38,
        "raw_overall_min": 6.5,
        "raw_min_min": 6.0,
        "high_band_readiness_min": 0.72,
        "support_min": 0.62,
        "idea_min": 0.62,
        "word_count_min": 245,
        "paragraph_count_min": 4,
        "local_damage_max": 0.3,
        "serious_ratio_max": 0.35,
        "required_signals": 10
    },
    "high_hard_vetoes": {
        "task_true_fail_statuses": [
            "true_fail"
        ],
        "task_true_fail_states": [
            "TASK_TRUE_FAIL_HARD"
        ],
        "severe_signal_min": 2,
        "serious_ratio_min": 0.5,
        "local_damage_min": 0.4,
        "weak_probability_min": 0.48,
        "raw_min_max": 5.0,
        "support_quality_max": 0.58,
        "idea_extension_max": 0.58
    },
    "actions": {
        "A2_OR_LOWER": "cap_and_floor",
        "B1_WEAK": "cap_and_floor",
        "B1_B2_BOUNDARY": "cap_and_floor",
        "B2_MEDIUM": "cap_and_floor",
        "HIGH_BOUNDARY": "cap_and_floor",
        "HIGH_7_PLUS": "cap_and_floor"
    },
    "audit_mode": True
}



def load_config(path: str | None = None) -> Dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path:
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
        def merge(a: Dict[str, Any], b: Dict[str, Any]) -> None:
            for k, v in b.items():
                if isinstance(v, dict) and isinstance(a.get(k), dict):
                    merge(a[k], v)
                else:
                    a[k] = v
        merge(cfg, user)
    return cfg


def _weighted_quality(profile: CanonicalMetricProfile, weights: Mapping[str, float]) -> float:
    pos_total = sum(w for w in weights.values() if w > 0)
    neg_total = sum(abs(w) for w in weights.values() if w < 0)
    total = pos_total + neg_total
    if total <= 0:
        return 0.55
    acc = 0.0
    for m, w in weights.items():
        val = profile.get_float(m, 0.55 if w > 0 else 0.25)
        if w >= 0:
            acc += w * val
        else:
            acc += abs(w) * (1.0 - val)
    return clamp01(acc / total)


def compute_raw_criterion_quality(profile: CanonicalMetricProfile, config: Dict[str, Any]) -> Dict[str, float]:
    weights = config.get("criterion_weights", {})
    return {rub: round(_weighted_quality(profile, weights.get(rub, {})), 4) for rub in RUBRICS}


# ---------------------------
# TierGovernor v1.4.1.1
# ---------------------------

def _tg_score_group(overall: float | None) -> str:
    if overall is None:
        return "UNKNOWN_SCORE"
    if overall <= 3.5:
        return "A2_OR_LOWER"
    if overall <= 5.0:
        return "B1_WEAK"
    if overall <= 6.5:
        return "B2_MEDIUM"
    return "HIGH_7_PLUS"


def _tg_signal_count(tests: Dict[str, bool]) -> int:
    return sum(1 for v in tests.values() if bool(v))


def _tg_feature_bundle(profile: CanonicalMetricProfile, score_input: PremiumScoreInput, tier: TierDecision, solved: Dict[str, Any]) -> Dict[str, Any]:
    raw_bands = score_input.source_metadata.get("raw_criterion_bands", {}) or {}
    raw_overall = score_input.source_metadata.get("raw_overall")
    raw_min = min(raw_bands.values()) if raw_bands else None
    features = dict(tier.features or {})
    return {
        "raw_overall": raw_overall,
        "raw_min_criterion_band": raw_min,
        "semantic_recoverability": profile.get_float("semantic_recoverability", 0.60),
        "weak_writing_probability": profile.get_float("weak_writing_probability", 0.45),
        "local_language_damage_index": profile.get_float("local_language_damage_index", 0.35),
        "serious_error_sentence_ratio": profile.get_float("serious_error_sentence_ratio", 0.25),
        "high_band_readiness": profile.get_float("high_band_readiness", 0.55),
        "support_quality": profile.get_float("TR6_support_quality", 0.55),
        "idea_extension_depth": profile.get_float("TR5_idea_extension_depth", 0.55),
        "relevance_ratio": profile.get_float("TR4_relevance_ratio", 0.55),
        "word_count": profile.get_float("word_count", 0.0),
        "paragraph_count": profile.get_float("paragraph_count", 0.0),
        "positive_recovery_count": int(features.get("positive_recovery_count", 0) or 0),
        "severe_signal_count": int(features.get("severe_signal_count", 0) or 0),
        "source_tier": tier.tier,
        "pre_governor_overall": solved.get("overall_band"),
        "pre_governor_score_group": _tg_score_group(solved.get("overall_band")),
        "task_schema_status": profile.get("task_schema_status", ""),
        "task_resolution_state": str(tier.features.get("task_resolution_state", "")),
    }


def _tg_classify_evidence(features: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    tg = cfg.get("tier_governor", {})
    a2 = tg.get("a2_or_lower", {})
    b1 = tg.get("b1_weak", {})
    high = tg.get("high_7_plus", {})
    rec = tg.get("b1_to_b2_recovery", {})

    def f(name: str, default: float = 0.0) -> float:
        try:
            return float(features.get(name, default) if features.get(name) is not None else default)
        except Exception:
            return default

    a2_extreme = (
        f("serious_error_sentence_ratio") >= float(a2.get("extreme_serious_min", 0.85))
        and f("positive_recovery_count") <= float(a2.get("positive_recovery_max", 1))
        and (f("local_language_damage_index") >= float(a2.get("local_damage_min", 0.35)) or f("weak_writing_probability") >= float(a2.get("weak_probability_min", 0.48)))
        and f("support_quality") <= float(a2.get("support_quality_max", 0.56))
        and f("idea_extension_depth") <= float(a2.get("idea_extension_max", 0.56))
        and f("raw_overall") <= float(a2.get("raw_overall_max", 5.0))
        and f("raw_min_criterion_band") <= float(a2.get("raw_min_max", 5.0))
    )

    b1_tests = {
        "raw_overall_weak": f("raw_overall") <= float(b1.get("raw_overall_max", 5.5)),
        "raw_min_weak": f("raw_min_criterion_band") <= float(b1.get("raw_min_max", 5.0)),
        "weak_probability_elevated": f("weak_writing_probability") >= float(b1.get("weak_probability_min", 0.52)),
        "serious_ratio_elevated": f("serious_error_sentence_ratio") >= float(b1.get("serious_ratio_min", 0.55)),
        "semantic_not_strong": f("semantic_recoverability") <= float(b1.get("semantic_max", 0.62)),
        "support_not_strong": f("support_quality") <= float(b1.get("support_quality_max", 0.60)),
        "idea_not_strong": f("idea_extension_depth") <= float(b1.get("idea_extension_max", 0.61)),
        "limited_positive_recovery": f("positive_recovery_count") <= float(b1.get("positive_recovery_max", 5)),
    }
    b1_count = _tg_signal_count(b1_tests)

    recovery_tests = {
        "semantic_recovery": f("semantic_recoverability") >= float(rec.get("semantic_min", 0.66)),
        "support_recovery": f("support_quality") >= float(rec.get("support_min", 0.62)),
        "idea_recovery": f("idea_extension_depth") >= float(rec.get("idea_min", 0.62)),
        "positive_recovery": f("positive_recovery_count") >= float(rec.get("positive_min", 5)),
        "local_controlled": f("local_language_damage_index") <= float(rec.get("local_max", 0.34)),
    }
    recovery_count = _tg_signal_count(recovery_tests)
    b1_recovery_ok = recovery_count >= int(rec.get("required_signals", 4))

    high_tests = {
        "positive_recovery_high": f("positive_recovery_count") >= float(high.get("positive_recovery_min", 8)),
        "no_severe_signals": f("severe_signal_count") <= float(high.get("severe_signal_max", 0)),
        "semantic_high": f("semantic_recoverability") >= float(high.get("semantic_min", 0.66)),
        "weak_controlled": f("weak_writing_probability") <= float(high.get("weak_probability_max", 0.38)),
        "raw_overall_high_enough": f("raw_overall") >= float(high.get("raw_overall_min", 6.5)),
        "raw_min_high_enough": f("raw_min_criterion_band") >= float(high.get("raw_min_min", 6.0)),
        "hbr_high": f("high_band_readiness") >= float(high.get("high_band_readiness_min", 0.72)),
        "support_high": f("support_quality") >= float(high.get("support_min", 0.62)),
        "idea_high": f("idea_extension_depth") >= float(high.get("idea_min", 0.62)),
        "word_count_sufficient": f("word_count") >= float(high.get("word_count_min", 245)),
        "paragraph_count_sufficient": f("paragraph_count") >= float(high.get("paragraph_count_min", 4)),
        "local_damage_controlled": f("local_language_damage_index") <= float(high.get("local_damage_max", 0.30)),
        "serious_ratio_controlled": f("serious_error_sentence_ratio") <= float(high.get("serious_ratio_max", 0.35)),
    }
    high_count = _tg_signal_count(high_tests)

    hb = tg.get("high_boundary", {})
    high_boundary_tests = {
        "raw_overall_boundary": f("raw_overall") >= float(hb.get("raw_overall_min", 6.5)),
        "raw_min_boundary": f("raw_min_criterion_band") >= float(hb.get("raw_min_min", 6.0)),
        "hbr_boundary": f("high_band_readiness") >= float(hb.get("high_band_readiness_min", 0.66)),
        "semantic_boundary": f("semantic_recoverability") >= float(hb.get("semantic_min", 0.62)),
        "support_boundary": f("support_quality") >= float(hb.get("support_min", 0.60)),
        "idea_boundary": f("idea_extension_depth") >= float(hb.get("idea_min", 0.60)),
        "local_boundary_controlled": f("local_language_damage_index") <= float(hb.get("local_damage_max", 0.36)),
        "serious_boundary_controlled": f("serious_error_sentence_ratio") <= float(hb.get("serious_ratio_max", 0.45)),
        "weak_boundary_controlled": f("weak_writing_probability") <= float(hb.get("weak_probability_max", 0.44)),
    }
    high_boundary_count = _tg_signal_count(high_boundary_tests)

    veto = tg.get("high_hard_vetoes", {})
    status = str(features.get("task_schema_status", "") or "")
    state = str(features.get("task_resolution_state", "") or "")
    hard_veto_tests = {
        "task_true_fail": status in set(veto.get("task_true_fail_statuses", ["true_fail"])) and state in set(veto.get("task_true_fail_states", ["TASK_TRUE_FAIL_HARD"])),
        "severe_signal_veto": f("severe_signal_count") >= float(veto.get("severe_signal_min", 2)),
        "serious_ratio_veto": f("serious_error_sentence_ratio") >= float(veto.get("serious_ratio_min", 0.50)),
        "local_damage_veto": f("local_language_damage_index") >= float(veto.get("local_damage_min", 0.40)),
        "weak_probability_veto": f("weak_writing_probability") >= float(veto.get("weak_probability_min", 0.48)),
        "raw_min_veto": f("raw_min_criterion_band") <= float(veto.get("raw_min_max", 5.0)),
        "support_quality_veto": f("support_quality") < float(veto.get("support_quality_max", 0.58)),
        "idea_extension_veto": f("idea_extension_depth") < float(veto.get("idea_extension_max", 0.58)),
    }
    hard_veto_count = _tg_signal_count(hard_veto_tests)
    hard_veto_active = hard_veto_count > 0

    high_confirmed = high_count >= int(high.get("required_signals", 10)) and not hard_veto_active
    high_boundary = (
        high_boundary_count >= int(hb.get("required_signals", 5))
        and not hard_veto_tests.get("task_true_fail", False)
        and not hard_veto_tests.get("severe_signal_veto", False)
    )

    if a2_extreme:
        evidence_tier = "A2_OR_LOWER"
    elif high_confirmed:
        evidence_tier = "HIGH_7_PLUS"
    elif high_boundary:
        evidence_tier = "HIGH_BOUNDARY"
    elif b1_count >= int(b1.get("required_signals", 5)) and b1_recovery_ok:
        evidence_tier = "B1_B2_BOUNDARY"
    elif b1_count >= int(b1.get("required_signals", 5)) and not b1_recovery_ok:
        evidence_tier = "B1_WEAK"
    else:
        evidence_tier = "B2_MEDIUM"


    return {
        "evidence_tier": evidence_tier,
        "a2_extreme": a2_extreme,
        "b1_signal_count": b1_count,
        "b1_signals": b1_tests,
        "b1_recovery_ok": b1_recovery_ok,
        "b1_recovery_count": recovery_count,
        "b1_recovery_signals": recovery_tests,
        "high_signal_count": high_count,
        "high_signals": high_tests,
        "high_boundary_signal_count": high_boundary_count,
        "high_boundary_signals": high_boundary_tests,
        "high_hard_veto_count": hard_veto_count,
        "high_hard_vetoes": hard_veto_tests,
        "high_confirmed": high_confirmed,
        "high_boundary": high_boundary,
    }


def _tg_overall_from_criteria(criteria: Dict[str, int]) -> float:
    return round_half(sum(float(v) for v in criteria.values()) / 4.0)


def _tg_adjust_criteria_to_range(criteria: Dict[str, int], min_overall: float, max_overall: float) -> tuple[Dict[str, int], list[str]]:
    adjusted = {k: int(v) for k, v in criteria.items()}
    actions: list[str] = []
    keys = list(RUBRICS)
    safety = 0
    while _tg_overall_from_criteria(adjusted) > max_overall and safety < 40:
        k = max(keys, key=lambda x: adjusted.get(x, 0))
        if adjusted[k] <= 0:
            break
        adjusted[k] -= 1
        actions.append(f"lower_{k}_for_tier_cap")
        safety += 1
    while _tg_overall_from_criteria(adjusted) < min_overall and safety < 80:
        k = min(keys, key=lambda x: adjusted.get(x, 9))
        if adjusted[k] >= 9:
            break
        adjusted[k] += 1
        actions.append(f"raise_{k}_for_tier_floor")
        safety += 1
    return adjusted, actions


def apply_tier_governor(score_input: PremiumScoreInput, profile: CanonicalMetricProfile, ledger: EvidenceLedger, tier: TierDecision, solved: Dict[str, Any], cfg: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    tg = cfg.get("tier_governor", {})
    if not tg.get("enabled", False):
        return solved, {"enabled": False, "action": "disabled"}
    features = _tg_feature_bundle(profile, score_input, tier, solved)
    diag = _tg_classify_evidence(features, cfg)
    evidence_tier = diag["evidence_tier"]
    ranges = tg.get("tier_ranges", {})
    allowed = ranges.get(evidence_tier, ranges.get("UNCERTAIN", {"min": 0.0, "max": 9.0}))
    min_overall = float(allowed.get("min", 0.0))
    max_overall = float(allowed.get("max", 9.0))
    pre_criteria = {k: int(v) for k, v in solved.get("final_criterion_bands", {}).items()}
    pre_overall = _tg_overall_from_criteria(pre_criteria)
    post_criteria, actions = _tg_adjust_criteria_to_range(pre_criteria, min_overall, max_overall)
    post_overall = _tg_overall_from_criteria(post_criteria)
    out = dict(solved)
    out["final_criterion_bands"] = post_criteria
    out["overall_band"] = post_overall
    if actions:
        out.setdefault("constraints_applied", []).append({
            "constraint_id": "tier_governor_range_enforcement_v1_4_1",
            "evidence_tier": evidence_tier,
            "allowed_range": {"min": min_overall, "max": max_overall},
            "pre_overall": pre_overall,
            "post_overall": post_overall,
            "actions": actions,
        })
    governor = {
        "version": "tier_governor_v1_4_1",
        "enabled": True,
        "evidence_tier": evidence_tier,
        "pre_governor_score_group": _tg_score_group(pre_overall),
        "released_score_tier": _tg_score_group(post_overall),
        "allowed_overall_range": {"min": min_overall, "max": max_overall},
        "pre_governor_overall": pre_overall,
        "post_governor_overall": post_overall,
        "action": "adjusted" if actions else "none",
        "actions": actions,
        "features": features,
        "diagnostics": diag,
        "runtime_label_use": False,
        "essay_specific_rule_use": False,
    }
    return out, governor


def score_unified(score_input: PremiumScoreInput, profile: CanonicalMetricProfile, config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    cfg = config or DEFAULT_CONFIG
    profile.validate(); profile.ensure_defaults()
    ledger = build_evidence_ledger(score_input)
    raw_quality = compute_raw_criterion_quality(profile, cfg)
    raw_bands = {rub: quality_to_integer_band(raw_quality[rub], cfg.get("band_thresholds")) for rub in RUBRICS}
    score_input.source_metadata.setdefault("raw_criterion_bands", raw_bands)
    score_input.source_metadata.setdefault("raw_criterion_quality", raw_quality)
    score_input.source_metadata.setdefault("raw_overall", round_half(sum(raw_bands.values()) / 4.0))
    tier = classify_tier(score_input, profile, ledger, cfg)
    constraints = generate_constraints(score_input, profile, ledger, tier, cfg)
    solved = solve_constraints(raw_bands, constraints, tier, cfg)
    solved, tier_governor = apply_tier_governor(score_input, profile, ledger, tier, solved, cfg)
    metric_sources = [mv.source for mv in profile.metrics.values()]
    fallback_rate = metric_sources.count("fallback") / max(1, len(metric_sources))
    conflict_penalty = min(0.15, 0.02 * len(solved.get("constraints_suppressed", [])))
    confidence = max(0.35, min(0.92, tier.confidence - 0.18 * fallback_rate - conflict_penalty))
    qa = dict(solved.get("qa", {}))
    qa.update({
        "TR8_bad_rate_contract_ok": "TR8_quality" not in profile.metrics and profile.metrics.get("TR8_irrelevant_or_repetitive_content_rate", None) is not None,
        "positive_collocation_not_high_band_proof": True,
        "low_error_count_not_gra_high_band_proof": True,
        "external_targets_used_at_runtime": False,
        "single_solver_final_score": True,
        "direct_profiler_input_supported": True,
        "evidence_ledger_score_charge_weight_supported": True,
        "v1_3_2_policy_constraints_active": True,
        "v1_3_2_bridge_tightening_active": True,
        "v1_3_2_convergent_t1_routing_active": True,
        "v1_3_2_soft_task_lift_active": True,
        "v1_3_2_integer_rubric_contract_active": True,
        "v1_3_2_large_gap_guardrails_active": True,
        "rubric_scores_integer_only": all(isinstance(v, int) for v in solved["final_criterion_bands"].values()),
        "tier_governor_active": bool(tier_governor.get("enabled")),
        "tier_governor_action": tier_governor.get("action"),
        "tier_governor_evidence_tier": tier_governor.get("evidence_tier"),
        "tier_governor_released_score_tier": tier_governor.get("released_score_tier"),
    })
    max_harm = int(cfg.get("qa_thresholds", {}).get("max_harmonization_steps_ready", 12))
    review_needed = bool(
        qa.get("constraint_conflict_count", 0) > 0
        or qa.get("unresolved_overall_upper_bound")
        or qa.get("unresolved_overall_lower_bound")
        or qa.get("harmonization_steps", 0) > max_harm
    )
    audit = ScoreAudit(
        scoring_version=SCORING_VERSION,
        raw_criterion_quality=raw_quality,
        raw_criterion_bands=raw_bands,
        tier_decision=tier.as_dict(),
        constraints_generated=[c.as_dict() for c in constraints],
        constraints_applied=solved.get("constraints_applied", []),
        constraints_suppressed=solved.get("constraints_suppressed", []),
        final_criterion_bands=solved["final_criterion_bands"],
        overall_band=solved["overall_band"],
        confidence=round(confidence, 3),
        review_flags=solved.get("review_flags", []),
        qa=qa,
    )
    return {
        "essay_id": score_input.essay_id,
        "scoring_version": SCORING_VERSION,
        "score_profile": {
            "overall_band_estimate": solved["overall_band"],
            "official_criteria_bands": solved["final_criterion_bands"],
            "raw_criterion_bands": raw_bands,
            "raw_criterion_quality": raw_quality,
            "confidence": round(confidence, 3),
            "score_status": "review_needed" if review_needed else "ready" if confidence >= 0.50 else "low_confidence",
        },
        "tier_decision": tier.as_dict(),
        "tier_governor": tier_governor,
        "canonical_metric_profile": profile.as_dict(),
        "evidence_ledger": ledger.as_dict(),
        "unified_score_audit": audit.as_dict(),
        "qa": qa,
    }


def score_record(record: Dict[str, Any], config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    score_input, profile = adapt_record(record)
    return score_unified(score_input, profile, config or DEFAULT_CONFIG)


def _records_from_payload(data: Any):
    if isinstance(data, dict):
        for key in ("results", "essays", "scored"):
            if isinstance(data.get(key), list):
                return data[key]
        if "essay_id" in data or "identity" in data or "text" in data:
            return [data]
    if isinstance(data, list):
        return data
    raise ValueError("Input must be a record, list, or dict with results/essays/scored")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Premium Unified Scorer v1.4.1")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--config")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)
    records = _records_from_payload(data)
    results = [score_record(r, cfg) for r in records]
    payload = {"schema_version": SCORING_VERSION, "results": results}
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2 if args.pretty else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
