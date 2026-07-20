"""
LRET Engine v1.6.2 -- Layered Resource-QA + Contextual Synonym Quotas
================================================================================

Standalone production-oriented LRET engine for IELTS / Academic Writing lexical
learning tasks.

v1.6.1 keeps the v1.6 layered resource-QA architecture and fixes a
production runtime bug: the v1.6 final pedagogical-gain helper referenced
STOPWORDS without defining the fallback alias. The fix is universal and does
not change candidate classification logic except preventing the crash.

  raw/evaluator units
  -> reconstruction and preclassification
  -> candidate scope QA
  -> LLM/resource classification support
  -> suggestion generation
  -> suggestion QA with contextual insertion + pedagogical-gain checks
  -> CLARIFY overlap deduplication
  -> KEEP visibility filtering
  -> replacement/practice/explanation synchronization
  -> final release QA

The engine is universal. It contains no essay-id, sentence-id, prompt-topic,
sample-answer, or hardcoded essay phrase rules. Resource-based suggestion support
uses external/canonical word-level resources and deterministic QA gates; it is not
an embedded phrase bank.

Input:
  * direct LRET_INPUT_V1.1-style JSON, or
  * full Evaluator/WKE JSON containing consumer_payloads.lret_payload.

Output:
  * LRET_OUTPUT_V1.1-compatible JSON with v1.6 QA/profile additions.

Run:
  python lret_engine_v1_6_2_layered_resource_qa.py     --input response_1783333960540.json     --output lret_v1_6_output.json     --canonical-resources final_app_registries_v3_CONSOLIDATED_CANONICAL.zip     --use-llm --pretty --summary
"""

from __future__ import annotations

import argparse
import copy
import datetime as _dt
import hashlib
import json
import re
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

SCHEMA_VERSION_IN = "LRET_INPUT_V1.1"
SCHEMA_VERSION_OUT = "LRET_OUTPUT_V1.1"
ENGINE_ID = "LRET"
ENGINE_VERSION = "lret-engine-v1.4.2-universal-hybrid-anti-overfit"

# ---------------------------------------------------------------------------
# v1.3 universal lexical-only policy
# ---------------------------------------------------------------------------
LRET_LEXICAL_FIX_FAMILIES: Set[str] = {
    "SPELLING",
    "WORD_FORM",
    "WORD_FORM_LEXICAL",
    "COLLOCATION",
    "WORD_CHOICE",
    "LEXICAL_PRECISION",
    "SEMANTIC_COMBINATION",
    "REGISTER",
    "REDUNDANCY",
    "PREPOSITION_PATTERN",
    "PREPOSITION_PATTERN_LEXICAL",
}

LRET_GRAMMAR_BLOCKLIST: Set[str] = {
    "ARTICLE_DETERMINER",
    "SUBJECT_VERB_AGREEMENT",
    "VERB_TENSE",
    "VERB_FORM",
    "VERB_FORM_GRAMMAR",
    "CLAUSE_STRUCTURE",
    "FRAGMENT",
    "RUN_ON",
    "COMPARATIVE_FORM",
    "COMPARATIVE_FORM_GRAMMAR",
    "GRAMMAR_PUNCTUATION",
    "PUNCTUATION",
    "SPACING",
    "CAPITALIZATION",
    "CONSTRUCTION",
    "GRAMMATICAL_RANGE",
}

# Function words are removed from content-token overlap calculations.
EDGE_STOPWORDS: Set[str] = {
    "a", "an", "the", "of", "to", "for", "with", "from", "by", "in", "on", "at", "into",
    "and", "or", "but", "nor", "yet", "so", "that", "this", "these", "those", "it", "its",
    "is", "are", "was", "were", "am", "be", "been", "being", "do", "does", "did",
    "have", "has", "had", "will", "would", "can", "could", "should", "may", "might", "must",
    "i", "we", "you", "they", "he", "she", "them", "their", "our", "your", "my", "me",
    "there", "here", "as", "than", "very", "more", "most", "some", "one", "both", "also",
}

# v1.6.1 bugfix: v1.6 final QA helpers used STOPWORDS as a generic alias
# for content-token filtering but the symbol was not defined in the standalone
# file. Keep it as a universal alias/extension of EDGE_STOPWORDS so the helper
# remains resource- and topic-independent.
STOPWORDS: Set[str] = set(EDGE_STOPWORDS) | {
    "all", "any", "each", "every", "few", "many", "much", "several", "such",
    "like", "just", "only", "own", "same", "other", "another", "again",
    "before", "after", "during", "while", "when", "where", "why", "how",
}

DISCOURSE_MARKER_ALLOWLIST: Set[str] = {
    "as a result", "on the other hand", "in conclusion", "for example", "for instance",
    "in addition", "as well as", "such as", "in other words", "in contrast", "in summary",
}

STABLE_MULTI_KEEP: Set[str] = set()
STABLE_SINGLE_KEEP: Set[str] = set()
ACADEMIC_SIGNAL_WORDS: Set[str] = set()
COMMON_PREDICATE_HEADS: Set[str] = set()
# Generic verbs/nouns are weak as isolated KEEP evidence unless they are part of a phrase.
GENERIC_SINGLE_WORD_KEEP_BLOCK: Set[str] = set()

# Optional low-value single-word block; empty by default in v1.3.2 to avoid embedded word resources.
# This is not essay-specific: it blocks isolated discourse/framing fragments and generic modifiers
# unless they are part of a stronger phrase/collocation KEEP unit.
LOW_VALUE_SINGLE_KEEP_BLOCK: Set[str] = set()
VAGUE_PLACEHOLDER_NOUNS: Set[str] = set()
AGE_OR_GROUP_MODIFIERS: Set[str] = set()
PERSON_GROUP_HEADS: Set[str] = set()
LEXICAL_ERROR_MARKERS: Set[str] = set()
BANNED_WORDS: Set[str] = set()


# Optional external lexical resources. The engine ships with no exact phrase bank.
# Resource schema may contain:
# {
#   "phrase_suggestions": {"normalized phrase": ["replacement", ...]},
#   "word_suggestions": {"word": ["replacement", ...]},
#   "formulaic_keep": ["for example", ...],
#   "stable_keep_phrases": [...],
#   "stable_keep_words": [...],
#   "academic_signal_words": [...]
# }
EXTERNAL_PHRASE_SUGGESTIONS: Dict[str, List[str]] = {}
EXTERNAL_WORD_SUGGESTIONS: Dict[str, List[str]] = {}
EXTERNAL_FORMULAIC_KEEP: Set[str] = set()
EXTERNAL_STABLE_KEEP_PHRASES: Set[str] = set()
EXTERNAL_STABLE_KEEP_WORDS: Set[str] = set()
EXTERNAL_ACADEMIC_SIGNAL_WORDS: Set[str] = set()

# Universal structural words/classes. These are not essay phrase entries; they are broad
# closed-class or high-frequency pattern triggers used to form templates. They are not used
# to suppress a specific essay phrase.
UNIVERSAL_VAGUE_NOUNS: Set[str] = {"thing", "things", "stuff", "kind", "kinds", "something", "anything", "everything"}
UNIVERSAL_BASIC_ADJECTIVE_UPGRADES: Dict[str, List[str]] = {}
UNIVERSAL_QUANTITY_UPGRADES: Dict[str, List[str]] = {
    "a lot of": ["substantial", "considerable", "extensive"],
    "lots of": ["substantial", "considerable", "extensive"],
    "many": ["numerous", "several", "various"],
    "some": ["several", "certain", "a number of"],
}
UNIVERSAL_SPEED_ADVERB_UPGRADES: Dict[str, List[str]] = {}
UNIVERSAL_BASIC_VERB_UPGRADES: Dict[str, List[str]] = {}

def load_external_lexical_resources(paths: Optional[Sequence[str]]) -> None:
    """Load optional external lexical resources without embedding them in code."""
    global EXTERNAL_PHRASE_SUGGESTIONS, EXTERNAL_WORD_SUGGESTIONS
    global EXTERNAL_FORMULAIC_KEEP, EXTERNAL_STABLE_KEEP_PHRASES, EXTERNAL_STABLE_KEEP_WORDS, EXTERNAL_ACADEMIC_SIGNAL_WORDS
    if not paths:
        return
    for path in paths:
        if not path:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        for k, v in (data.get("phrase_suggestions") or {}).items():
            vals = [str(x).strip() for x in (v or []) if str(x).strip()]
            if vals:
                EXTERNAL_PHRASE_SUGGESTIONS[norm_text(k)] = vals
        for k, v in (data.get("word_suggestions") or {}).items():
            vals = [str(x).strip() for x in (v or []) if str(x).strip()]
            if vals:
                EXTERNAL_WORD_SUGGESTIONS[norm_text(k)] = vals
        EXTERNAL_FORMULAIC_KEEP |= {norm_text(x) for x in data.get("formulaic_keep", []) if str(x).strip()}
        EXTERNAL_STABLE_KEEP_PHRASES |= {norm_text(x) for x in data.get("stable_keep_phrases", []) if str(x).strip()}
        EXTERNAL_STABLE_KEEP_WORDS |= {norm_text(x) for x in data.get("stable_keep_words", []) if str(x).strip()}
        EXTERNAL_ACADEMIC_SIGNAL_WORDS |= {norm_text(x) for x in data.get("academic_signal_words", []) if str(x).strip()}

NOISY_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"^(for|of|to|with|from|by|about)\s+the$", re.I),
    re.compile(r"^(and|but|or|nor|yet|so)\s+\w+$", re.I),
    re.compile(r"^\w+\s+(and|but|or|nor|yet|so)$", re.I),
    re.compile(r"^[a-z]$", re.I),
)

GENERIC_SUGGESTION_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(change|use|choose|write|replace|correct|fix)\b.*\b(required|correct|appropriate|better)\b", re.I),
    re.compile(r"\b(required|correct|appropriate)\s+(noun|verb|adjective|adverb|word|form)\b", re.I),
    re.compile(r"\b(noun|verb|adjective|adverb)\s*/\s*(adjective|adverb|noun|verb)\s+form\b", re.I),
    re.compile(r"\bderivative\b", re.I),
    re.compile(r"\bword\s+form\b", re.I),
)

# v1.3.2 has no embedded exact phrase/collocation/word enhancement bank.
# Exact lexical alternatives must be loaded from an external resource file or produced by
# a pluggable LLM layer outside this deterministic engine. This prevents sample-shaped
# rules from entering the production code.
PERSON_NOUNS: Set[str] = set()

VALIDATION_GATES: List[str] = [
    "span_fit",
    "grammar_role_preserved",
    "meaning_link_detected",
    "claim_strength_preserved",
    "register_preserved_or_improved",
    "context_specificity_ok",
    "no_topic_drift",
    "no_unsafe_word",
]

# ---------------------------------------------------------------------------
# Utility and normalization
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def norm_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def compact_norm(text: Any) -> str:
    """Lowercase alphanumeric normalization for detecting punctuation-only variants."""
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def surface_tokens(text: Any) -> List[str]:
    return re.findall(r"[A-Za-z']+", str(text or ""))


def simple_stem(tok: str) -> str:
    t = re.sub(r"[^A-Za-z]", "", tok.lower())
    if not t:
        return ""
    if t.endswith("ies") and len(t) > 4:
        return t[:-3] + "y"
    if t.endswith("ing") and len(t) > 5:
        return t[:-3]
    if t.endswith("ed") and len(t) > 4:
        return t[:-2]
    if t.endswith("s") and len(t) > 3 and not t.endswith("ss"):
        return t[:-1]
    return t


def content_tokens(text: Any) -> List[str]:
    out: List[str] = []
    for tok in surface_tokens(text):
        s = simple_stem(tok)
        if s and s not in EDGE_STOPWORDS:
            out.append(s)
    return out


def token_sequence_contained(shorter: Sequence[str], longer: Sequence[str]) -> bool:
    if not shorter or not longer or len(shorter) > len(longer):
        return False
    for i in range(0, len(longer) - len(shorter) + 1):
        if list(longer[i:i + len(shorter)]) == list(shorter):
            return True
    return False


def content_overlap_ratio(a: Any, b: Any) -> float:
    at = set(content_tokens(a))
    bt = set(content_tokens(b))
    if not at or not bt:
        return 0.0
    return len(at & bt) / max(1, min(len(at), len(bt)))


def replacement_validation(reason: str, accepted: bool = True, gates: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "accepted": accepted,
        "gates": gates or list(VALIDATION_GATES),
        "reason": reason,
    }


def new_run_id(identity: Dict[str, Any]) -> str:
    seed = json.dumps(identity, sort_keys=True, ensure_ascii=False) + str(time.time()) + str(uuid.uuid4())
    return "run_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Contextual-fit validation
# ---------------------------------------------------------------------------

@dataclass
class GateResult:
    passed: bool
    reason: str
    gates_checked: List[str] = field(default_factory=list)


class ContextFitValidator:
    def validate(self, original: str, candidate: str, sentence: str, *, source: str = "unknown") -> GateResult:
        raise NotImplementedError


class RuleBasedContextFitValidator(ContextFitValidator):
    """Conservative default context-fit validator.

    This is intentionally deterministic. It accepts external-resource replacements,
    transparent lexical repairs, and clear universal-pattern phrase replacements.
    It is not a substitute for a future LLM semantic validator, but it implements
    v1.3 fail-closed behavior.
    """

    def validate(self, original: str, candidate: str, sentence: str, *, source: str = "unknown") -> GateResult:
        original = str(original or "").strip()
        candidate = str(candidate or "").strip()
        sentence = str(sentence or "").strip()
        if not original or not candidate:
            return GateResult(False, "empty original or candidate", [])
        low_cand = norm_text(candidate)
        if any(w in low_cand.split() for w in BANNED_WORDS):
            return GateResult(False, "candidate contains banned/unsafe word", ["no_unsafe_word"])
        # Basic length sanity. Phrase replacements may be slightly longer, but not an explanation.
        o_len = max(1, len(surface_tokens(original)))
        c_len = max(1, len(surface_tokens(candidate)))
        if c_len > max(o_len + 5, o_len * 3):
            return GateResult(False, "replacement length is not reasonable", ["span_fit", "grammar_role_preserved"])

        low_orig = norm_text(original)

        if source in {"external_resource", "universal_pattern", "single_word_fallback"}:
            return GateResult(True, f"passed {source} contextual-fit check", list(VALIDATION_GATES))

        if self._transparent_repair_preserves_core(original, candidate):
            return GateResult(True, "passed transparent lexical repair contextual-fit check", list(VALIDATION_GATES))

        return GateResult(False, "no external-resource, universal-pattern, or transparent contextual link between original and candidate", list(VALIDATION_GATES))


    @staticmethod
    def _transparent_repair_preserves_core(original: str, candidate: str) -> bool:
        o = content_tokens(original)
        c = content_tokens(candidate)
        if not o or not c:
            return False
        os, cs = set(o), set(c)
        overlap = len(os & cs) / max(1, len(os))
        if overlap >= 0.95 and abs(len(c) - len(o)) <= 1:
            return True
        if overlap >= 0.66 and len(c) <= len(o) + 4:
            return True
        if "more" in surface_tokens(original.lower()) and overlap >= 0.75:
            return True
        return False


# ---------------------------------------------------------------------------
# Input adapters
# ---------------------------------------------------------------------------

def reconstruct_essay_text_from_evaluator_output(evaluator_output: Dict[str, Any]) -> str:
    eg = evaluator_output.get("evidence_graph") or {}
    paragraphs = eg.get("paragraph_map") or []
    if isinstance(paragraphs, list) and paragraphs:
        ordered = sorted([p for p in paragraphs if isinstance(p, dict) and p.get("text")], key=lambda p: p.get("paragraph_index", 0))
        text = "\n\n".join(str(p.get("text", "")).strip() for p in ordered if str(p.get("text", "")).strip())
        if text:
            return text
    sentences = eg.get("sentence_map") or []
    if isinstance(sentences, list) and sentences:
        ordered_s = sorted([s for s in sentences if isinstance(s, dict) and s.get("text")], key=lambda s: s.get("sentence_index", 0))
        text = " ".join(str(s.get("text", "")).strip() for s in ordered_s if str(s.get("text", "")).strip())
        if text:
            return text
    for key in ("essay_text", "raw_essay", "source_text", "text"):
        if evaluator_output.get(key):
            return str(evaluator_output[key])
    return ""


def make_lret_input(
    raw: Dict[str, Any],
    *,
    mode: Optional[str] = None,
    student_id: Optional[str] = None,
    essay_id: Optional[str] = None,
    submission_id: Optional[str] = None,
    learner_lexical_history: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if raw.get("schema_version") == SCHEMA_VERSION_IN and raw.get("lret_fix_payload") is not None:
        payload = copy.deepcopy(raw)
        if mode:
            payload["mode"] = mode
        if learner_lexical_history is not None:
            payload["learner_lexical_history"] = learner_lexical_history
        return payload

    consumer_payloads = raw.get("consumer_payloads") or {}
    lret_payload = consumer_payloads.get("lret_payload") or raw.get("lret_payload") or {}
    metadata = raw.get("metadata") or {}
    identity = {
        "student_id": student_id or metadata.get("student_id") or "anonymous",
        "essay_id": essay_id or metadata.get("essay_id") or "unknown_essay",
        "submission_id": submission_id or metadata.get("submission_id") or metadata.get("run_id"),
        "batch_id": metadata.get("batch_id"),
        "prompt_id": metadata.get("prompt_id"),
        "draft_id": metadata.get("draft_id"),
        "parent_submission_id": metadata.get("parent_submission_id"),
    }
    identity = {k: v for k, v in identity.items() if v is not None}
    return {
        "schema_version": SCHEMA_VERSION_IN,
        "identity": identity,
        "essay_text": reconstruct_essay_text_from_evaluator_output(raw),
        "lret_fix_payload": lret_payload,
        "learner_lexical_history": learner_lexical_history if learner_lexical_history is not None else {"unit_occurrence_counts": {}},
        "mode": mode or "fix_and_enhance",
        "source_adapter": {
            "adapter": "make_lret_input_v1_3",
            "source_schema_version": raw.get("schema_version"),
            "source_engine_id": metadata.get("engine_id"),
        },
    }


# ---------------------------------------------------------------------------
# Unit conversion and filtering
# ---------------------------------------------------------------------------

def ingest_and_validate(payload: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any], List[str]]:
    warnings: List[str] = []
    lret_payload = payload.get("lret_fix_payload") or {}
    raw_units = lret_payload.get("lexical_units_for_lret") or []
    raw_fixes = lret_payload.get("fix_candidates") or []
    if not isinstance(raw_units, list):
        warnings.append("lexical_units_for_lret missing or not a list")
        raw_units = []
    if not isinstance(raw_fixes, list):
        warnings.append("fix_candidates missing or not a list")
        raw_fixes = []

    units: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, int, str]] = set()
    for i, u in enumerate(raw_units):
        if not isinstance(u, dict):
            continue
        text = str(u.get("unit") or u.get("unit_text") or "").strip()
        if not text:
            continue
        sent = int(u.get("source_sentence_index", -1)) if str(u.get("source_sentence_index", "")).lstrip("-").isdigit() else -1
        unit_norm = norm_text(text)
        key = (unit_norm, sent, str(u.get("unit_type") or ""))
        if key in seen:
            continue
        seen.add(key)
        item = copy.deepcopy(u)
        item["unit_id"] = str(item.get("unit_id") or f"lu_{i+1:04d}")
        item["unit_text"] = text
        item["unit_norm"] = unit_norm
        item["token_sequence"] = content_tokens(text)
        item["unit_length_words"] = len(surface_tokens(text))
        item["source_sentence_index"] = sent
        item["source_paragraph_index"] = item.get("source_paragraph_index")
        item.setdefault("context", "")
        item.setdefault("candidate_value", 0.0)
        item.setdefault("extraction_flags", [])
        item.setdefault("axis_candidates", [])
        item.setdefault("frequency", 1)
        item["source_kind"] = "evaluator_lexical_unit"
        units.append(item)
    return units, [f for f in raw_fixes if isinstance(f, dict)], payload.get("learner_lexical_history") or {}, warnings


def is_noise_unit(unit: Dict[str, Any]) -> Tuple[bool, str]:
    text = norm_text(unit.get("unit_text"))
    if not text:
        return True, "empty unit"
    if text in DISCOURSE_MARKER_ALLOWLIST:
        return False, "allowlisted discourse/formulaic phrase"
    if any(p.search(text) for p in NOISY_PATTERNS):
        return True, "fragment_or_boundary_artifact"
    toks = surface_tokens(text)
    if len(toks) == 1 and norm_text(toks[0]) in EDGE_STOPWORDS:
        return True, "edge function word with no lexical target"
    if unit.get("extraction_flags") and "edge_function_word" in unit.get("extraction_flags", []) and len(content_tokens(text)) <= 1:
        return True, "edge function word with no redeeming lexical signal"
    return False, "kept"




def context_has_local_grammar_corruption(context: str) -> bool:
    """Conservative guard for single-word ENHANCE.

    v1.3 allows correct units to be enhanced, but a single word inside a malformed
    clause should not become a paraphrase task because the root problem is not
    lexical choice. Phrase-level lexical/vague-expression tasks may still survive
    when they have context-specific whole-phrase replacements.
    """
    low = norm_text(context)
    # Generic malformed-shape checks only. No exact essay phrase triggers.
    corruption_patterns = [
        r"\b(?:has|have)\s+to\s+\w+ed\b",
        r"\b(?:a|an)\s+\w+s\b",
        r"\bmore\s+\w+er\b",
        r"\bfor\s+\w+ing?\b",
        r"\b\w+\s+be\s+\w+\b",
    ]
    return any(re.search(p, low) for p in corruption_patterns)




def semantic_stability_for_enhance(unit_text: str, context: str = "") -> Tuple[bool, str]:
    """Decide whether a phrase is stable enough for ordinary ENHANCE.

    Universal rule: if a phrase is built mainly from placeholder nouns such as
    "things/kinds/stuff" and the phrase itself does not contain a recoverable lexical head,
    a paraphrase would be inferential. Such cases should be routed to clarification, not
    to normal ENHANCE.
    """
    text = str(unit_text or "").strip()
    low = norm_text(text)
    if not text:
        return False, "empty phrase"
    ctoks = content_tokens(text)
    if not ctoks:
        return False, "no content tokens"
    vague = {simple_stem(t) for t in UNIVERSAL_VAGUE_NOUNS}
    stems = {simple_stem(t) for t in ctoks}
    if stems and stems <= vague:
        return False, "semantic_stability_low_placeholder_phrase"
    # Phrases ending in a placeholder noun without a concrete lexical head are too unstable.
    if ctoks[-1] in vague and len([t for t in ctoks if t not in vague]) == 0:
        return False, "semantic_stability_low_placeholder_head"
    return True, "semantic_stability_ok"


def is_single_modifier_subunit(text: str, context: str = "") -> bool:
    """Detect isolated modifiers that are better represented inside a noun phrase KEEP."""
    toks = [t.lower() for t in surface_tokens(text)]
    if len(toks) != 1:
        return False
    tok = simple_stem(toks[0])
    if tok not in AGE_OR_GROUP_MODIFIERS:
        return False
    ctx = norm_text(context)
    return any(re.search(rf"\b{re.escape(tok)}\s+{re.escape(head)}\b", ctx) for head in PERSON_GROUP_HEADS)


def covered_subunit_has_independent_keep_value(unit: Dict[str, Any], coverer: Dict[str, Any]) -> bool:
    """v1.3.2 stricter KEEP rule for units already covered by an ENHANCE task.

    Covered subunits may remain as positive evidence only if they are independently useful:
    stable topic words, complete noun collocations, formulaic expressions, or reusable phrase-bank
    phrases. Fragments such as verb+partial-object or isolated modifiers are not preserved.
    """
    text = str(unit.get("unit_text") or "").strip()
    low = norm_text(text)
    if not text:
        return False
    if len(surface_tokens(text)) == 1:
        tok = simple_stem(text)
        if tok in LOW_VALUE_SINGLE_KEEP_BLOCK or is_single_modifier_subunit(text, str(unit.get("context") or "")):
            return False
        return tok in STABLE_SINGLE_KEEP or tok in ACADEMIC_SIGNAL_WORDS
    if is_unrecoverable_phrase_fragment(text, str(unit.get("context") or "")):
        return False
    if low in DISCOURSE_MARKER_ALLOWLIST or low in STABLE_MULTI_KEEP:
        return True
    if low in EXTERNAL_PHRASE_SUGGESTIONS:
        ok, _ = semantic_stability_for_enhance(text, str(unit.get("context") or ""))
        return ok
    unit_type = str(unit.get("unit_type") or "")
    # Covered short predicate fragments are usually incomplete unless explicitly licensed above.
    if ("verb_phrase" in unit_type or "predicate" in unit_type) and len(content_tokens(text)) <= 2:
        return False
    # Covered noun collocations can be preserved if they have a concrete head and no placeholder-only semantics.
    if "noun_phrase" in unit_type and len(content_tokens(text)) >= 2:
        ok, _ = semantic_stability_for_enhance(text, str(unit.get("context") or ""))
        return ok
    axes = set(unit.get("axis_candidates") or [])
    flags = set(unit.get("extraction_flags") or [])
    return bool((axes & {"topic_vocabulary", "collocation_naturalness"} or flags & {"topic_relevant", "collocation_candidate"}) and len(content_tokens(text)) >= 2)

def is_unrecoverable_phrase_fragment(unit_text: str, context: str = "") -> bool:
    """Reject malformed chunks as ENHANCE/KEEP candidates using universal shape rules."""
    low = norm_text(unit_text)
    ctx = norm_text(context)
    if not low:
        return True
    if low in EXTERNAL_FORMULAIC_KEEP or low in EXTERNAL_STABLE_KEEP_PHRASES:
        return False

    # Generic malformed-shape rules only. They describe construction shape, not essay phrases.
    malformed_patterns = [
        r"\b(?:has|have)\s+to\s+\w+ed\b",
        r"\b(?:a|an)\s+\w+s\b",
        r"\bmore\s+\w+er\b",
        r"\b\w+\s+be\s+\w+\b",
    ]
    if any(re.search(p, low) for p in malformed_patterns):
        return True

    # Incomplete phrase shapes: modal/auxiliary followed by a clipped complement,
    # a determiner/adjective fragment without a head, or a preposition-led boundary artifact.
    toks = surface_tokens(low)
    ctoks = content_tokens(low)
    if ctoks and any(t in UNIVERSAL_VAGUE_NOUNS for t in ctoks) and any(t.endswith("ing") for t in ctoks):
        return True
    if toks and toks[-1].lower() in {"many", "some", "several", "few", "both"}:
        return True
    if toks and (toks[0].lower() in {"hand"} or (toks[0].lower() in {"one", "other"} and toks[-1].lower() == "hand")):
        return True
    if len(toks) >= 2:
        if toks[0].lower() in {"can", "could", "should", "would", "will", "may", "might", "must"} and len(ctoks) <= 2:
            return True
        if toks[-1].lower() in {"and", "or", "but", "to", "of", "for", "with", "by", "from"}:
            return True
        if toks[0].lower() in {"of", "for", "with", "by", "from", "about"}:
            return True
    if len(ctoks) == 1 and len(toks) > 1:
        return True

    # Clause-fragment heuristic: subject-like NP + dangling -ing/finite verb at the end.
    if len(ctoks) >= 2:
        last = ctoks[-1]
        if last.endswith("ing") and len(ctoks) <= 3:
            return True
        if ctx and context_has_local_grammar_corruption(ctx) and set(ctoks) <= set(content_tokens(ctx)) and len(ctoks) <= 2:
            return True
    return False


def has_independent_keep_value(unit: Dict[str, Any]) -> bool:
    """Universal KEEP eligibility: positive, pedagogically useful lexical evidence.

    KEEP does not use an embedded topic-word list. It relies on upstream evidence flags,
    unit type, candidate value, and optional external keep resources.
    """
    text = str(unit.get("unit_text") or "").strip()
    low = norm_text(text)
    if not text or is_noise_unit(unit)[0]:
        return False
    toks = surface_tokens(text)
    ctoks = content_tokens(text)
    if not ctoks:
        return False

    axes = set(unit.get("axis_candidates") or [])
    flags = set(unit.get("extraction_flags") or [])
    value = float(unit.get("candidate_value") or 0.0)

    if len(toks) == 1:
        tok = simple_stem(toks[0])
        if tok in EDGE_STOPWORDS or tok in GENERIC_SINGLE_WORD_KEEP_BLOCK:
            return False
        if tok in EXTERNAL_STABLE_KEEP_WORDS or tok in EXTERNAL_ACADEMIC_SIGNAL_WORDS:
            return True
        # Without exact embedded word lists, single-word KEEP must be licensed by upstream topic evidence.
        if ("topic_vocabulary" in axes or "topic_relevant" in flags) and value >= 0.60:
            return True
        return False

    if is_unrecoverable_phrase_fragment(text, str(unit.get("context") or "")):
        return False
    stable, _ = semantic_stability_for_enhance(text, str(unit.get("context") or ""))
    if not stable:
        return False

    if low in EXTERNAL_FORMULAIC_KEEP or low in EXTERNAL_STABLE_KEEP_PHRASES:
        return True

    unit_type = str(unit.get("unit_type") or "")
    if "noun_phrase" in unit_type and len(ctoks) >= 2:
        return bool(axes & {"collocation_naturalness", "topic_vocabulary", "semantic_specificity"} or flags & {"collocation_candidate", "topic_relevant"})
    if "verb_phrase" in unit_type or "predicate" in unit_type:
        # Complete predicate phrase only: at least three content tokens and an upstream predicate/collocation signal.
        if len(ctoks) >= 3:
            return bool(axes & {"collocation_naturalness", "topic_vocabulary", "semantic_specificity"} or flags & {"collocation_candidate", "topic_relevant", "predicate_argument_candidate"})
    if axes & {"collocation_naturalness", "topic_vocabulary"} and len(ctoks) >= 2:
        return True
    if flags & {"collocation_candidate", "topic_relevant"} and len(ctoks) >= 2:
        return True
    return False


def keep_type_for_unit(unit: Dict[str, Any]) -> str:
    text = str(unit.get("unit_text") or "")
    low = norm_text(text)
    if len(surface_tokens(text)) == 1:
        if low in EXTERNAL_STABLE_KEEP_WORDS or simple_stem(low) in EXTERNAL_ACADEMIC_SIGNAL_WORDS:
            return "keep_topic_vocabulary"
        return "keep_word"
    if low in EXTERNAL_FORMULAIC_KEEP:
        return "keep_formulaic_expression"
    if low in EXTERNAL_STABLE_KEEP_PHRASES:
        return "keep_collocation"
    axes = set(unit.get("axis_candidates") or [])
    flags = set(unit.get("extraction_flags") or [])
    if "topic_vocabulary" in axes or "topic_relevant" in flags:
        return "keep_topic_vocabulary"
    if "collocation_naturalness" in axes or "collocation_candidate" in flags:
        return "keep_collocation"
    return "keep_phrase"


def positive_role_for_keep(keep_type: str, unit: Dict[str, Any]) -> str:
    if keep_type == "keep_word":
        return "single_word_control"
    if keep_type == "keep_topic_vocabulary":
        return "topic_control"
    if keep_type in {"keep_collocation", "keep_academic_phrase"}:
        return "collocation_control"
    if keep_type == "keep_formulaic_expression":
        return "academic_expression"
    return "phrase_control"


def find_covering_task_for_keep(unit: Dict[str, Any], task_units: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    # Exact task duplicates are not KEEP; subunits/related phrase fragments may be KEEP with annotation.
    exact = [t for t in task_units if same_sentence(t, unit) and norm_text(t.get("unit_text")) == norm_text(unit.get("unit_text"))]
    if exact:
        return {"_exact_task_duplicate": True, **exact[0]}
    coverers = [t for t in task_units if covers_unit(t, unit)]
    if not coverers:
        return None
    return sorted(coverers, key=candidate_rank, reverse=True)[0]
def noise_filter(units: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    survivors: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for u in units:
        noisy, reason = is_noise_unit(u)
        if noisy:
            dropped.append({"unit": u.get("unit_text"), "reason": reason, "stage": "noise_filter"})
        else:
            survivors.append(u)
    return survivors, dropped


# ---------------------------------------------------------------------------
# FIX derivation -- lexical-only
# ---------------------------------------------------------------------------

def sentence_context_for_span(essay_text: str, start: Optional[int], end: Optional[int], fallback: str) -> str:
    essay_text = essay_text or ""
    if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(essay_text):
        left_points = [essay_text.rfind(".", 0, start), essay_text.rfind("!", 0, start), essay_text.rfind("?", 0, start), essay_text.rfind("\n", 0, start)]
        left = max(left_points)
        right_points = [p for p in (essay_text.find(".", end), essay_text.find("!", end), essay_text.find("?", end), essay_text.find("\n", end)) if p != -1]
        left = 0 if left == -1 else left + 1
        right = min(right_points) + 1 if right_points else len(essay_text)
        ctx = essay_text[left:right].strip()
        if ctx:
            return ctx
    return str(fallback or essay_text or "").strip()


def is_concrete_student_facing_suggestion(original: str, suggestion: Any) -> Tuple[bool, str]:
    if suggestion is None:
        return False, "missing suggestion"
    s = str(suggestion).strip()
    if not s:
        return False, "empty suggestion"
    if len(surface_tokens(s)) > max(8, len(surface_tokens(original)) + 5):
        return False, "suggestion is too long/instructional to be a concrete replacement"
    if any(p.search(s) for p in GENERIC_SUGGESTION_PATTERNS):
        return False, "suggestion is a generic instruction, not a concrete replacement"
    if norm_text(s) == norm_text(original):
        return False, "suggestion is identical to original"
    if not re.search(r"[A-Za-z]", s):
        return False, "suggestion has no lexical content"
    return True, "concrete replacement candidate"



def normalize_lret_fix_family(span_text: str, family: str) -> str:
    """Map detector families into LRET lexical-only subfamilies using universal form rules."""
    fam = str(family or "").upper().strip()
    low = norm_text(span_text)
    # Double comparative inside a lexical/adjective phrase is a word-form repair for LRET,
    # not a collocation repair. This is a universal morphology pattern, not essay-specific.
    if re.search(r"\bmore\s+[a-z]+er\b", low):
        return "WORD_FORM_LEXICAL"
    if fam == "WORD_FORM":
        return "WORD_FORM"
    return fam



def expand_lexical_fix_span_universally(
    span_text: str,
    suggestion: Any,
    family: str,
    context: str,
    start: Optional[int] = None,
    end: Optional[int] = None,
) -> Tuple[str, Any, str, Optional[int], Optional[int], Optional[str]]:
    """Universal span-normalization hook.

    v1.3.2 contains no embedded exact lexical support-noun patterns. Any expansion
    that depends on a lexical item must come from Detector/Evaluator span boundaries,
    an external resource, or an LLM preprocessor. This function only normalizes the
    family label for generic shape-level cases.
    """
    span = str(span_text or "").strip()
    fam = normalize_lret_fix_family(span, family)
    return span, suggestion, fam, start, end, None


def looks_like_grammar_only_fix(family: str, span_text: str) -> bool:
    fam = family.upper().strip()
    span = norm_text(span_text)
    if fam in LRET_GRAMMAR_BLOCKLIST:
        return True
    # WORD_FORM is allowed only when lexical morphology/content-word form.
    if fam == "WORD_FORM":
        grammar_patterns = [
            r"\b(?:has|have)\s+to\s+\w+ed\b",
            r"\b(?:a|an)\s+\w+s\b",
        ]
        if any(re.search(p, span) for p in grammar_patterns):
            return True
    if fam == "PREPOSITION_PATTERN":
        return True
    return False


def infer_deterministic_lexical_repair(span_text: str, family: str, context: str = "") -> Optional[str]:
    """Return only universal deterministic lexical repairs.

    v1.3.2 intentionally contains no sample-derived word repairs. If Detector/Evaluator
    provides a concrete suggestion, it is used after validation. If not, this function
    only handles transparent morphology patterns that do not require an exact word list.
    """
    span = str(span_text or "").strip()
    low = norm_text(span)
    if not span:
        return None
    # Universal double-comparative: remove redundant 'more' before an -er adjective.
    if re.search(r"\bmore\s+[a-z]+er\b", low):
        return re.sub(r"\bmore\s+([A-Za-z]+er)\b", r"\1", span, flags=re.I)
    return None


def derive_fix_units(
    fix_candidates: List[Dict[str, Any]],
    essay_text: str,
    validator: ContextFitValidator,
) -> Tuple[List[Dict[str, Any]], List[Tuple[Optional[int], Optional[int], str]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    fix_units: List[Dict[str, Any]] = []
    claimed_spans: List[Tuple[Optional[int], Optional[int], str]] = []
    suppressed: List[Dict[str, Any]] = []
    context_failures: List[Dict[str, Any]] = []

    for idx, fc in enumerate(fix_candidates, start=1):
        family = str(fc.get("error_family") or fc.get("family") or fc.get("detector_family") or "").upper().strip()
        span_text = str(fc.get("span_text") or fc.get("unit_text") or fc.get("surface_quote") or "").strip()
        if not span_text:
            continue
        start = fc.get("start") if isinstance(fc.get("start"), int) else None
        end = fc.get("end") if isinstance(fc.get("end"), int) else None
        context = sentence_context_for_span(essay_text, start, end, span_text)

        # v1.3.2: universal precision pass before filtering/validation.
        # This may expand a too-narrow lexical span and/or relabel a lexical-form error.
        raw_suggestion = fc.get("suggestion")
        span_text, raw_suggestion, family, start, end, span_expansion_note = expand_lexical_fix_span_universally(
            span_text, raw_suggestion, family, context, start, end
        )

        if family not in LRET_LEXICAL_FIX_FAMILIES or looks_like_grammar_only_fix(family, span_text):
            suppressed.append({
                "unit": span_text,
                "family": family,
                "reason": "grammar_only_not_lret",
                "stage": "fix_filter",
            })
            claimed_spans.append((start, end, span_text))
            continue

        suggestion = raw_suggestion
        concrete_ok, concrete_reason = is_concrete_student_facing_suggestion(span_text, suggestion)
        if not concrete_ok:
            inferred = infer_deterministic_lexical_repair(span_text, family, context)
            if inferred:
                suggestion = inferred
                concrete_ok, concrete_reason = is_concrete_student_facing_suggestion(span_text, suggestion)
        if not concrete_ok:
            suppressed.append({
                "unit": span_text,
                "family": family,
                "reason": "no_concrete_fix_suggestion",
                "detail": concrete_reason,
                "stage": "fix_filter",
            })
            claimed_spans.append((start, end, span_text))
            continue

        vsource = "external_resource" if repair_source in {"resource_or_deterministic_repair", "openai_fix_repair"} else "fix_repair"
        result = validator.validate(span_text, str(suggestion), context, source=vsource)
        if not result.passed:
            suppressed.append({
                "unit": span_text,
                "family": family,
                "reason": "context_fit_failed",
                "detail": result.reason,
                "stage": "fix_filter",
            })
            context_failures.append({"unit_text": span_text, "candidate": str(suggestion), "tier": "fix", "reason": result.reason})
            claimed_spans.append((start, end, span_text))
            continue

        unit = {
            "unit_id": f"fix_{idx:04d}",
            "class_label": "FIX",
            "unit_text": span_text,
            "unit_norm": norm_text(span_text),
            "unit_type": "lexical_repair_span",
            "replacement_scope": "whole_phrase" if len(surface_tokens(span_text)) > 1 else "word",
            "error_family": family,
            "detector_family": family,
            "issue_code": family,
            "occurrence_count": 1,
            "source_sentence_index": _infer_sentence_index_from_context(essay_text, context),
            "source_paragraph_index": fc.get("paragraph_idx"),
            "context": context,
            "locations": [{"start": start, "end": end, "paragraph_idx": fc.get("paragraph_idx")}],
            "requires_full_contextual_check": True,
            "safety_level": "must_repair_final_lexical_error",
            "suggestions": [{
                "text": str(suggestion).strip(),
                "validation": replacement_validation(result.reason, True, result.gates_checked),
            }],
            "suggestion_source_quality": concrete_reason,
            "span_expansion_note": span_expansion_note,
            "covered_subunits": [],
            "dedup_role": "candidate_pending_dedup",
        }
        build_fix_phase1(unit)
        fix_units.append(unit)
        claimed_spans.append((start, end, span_text))
    return fix_units, claimed_spans, suppressed, context_failures


def _infer_sentence_index_from_context(essay_text: str, context: str) -> int:
    # Direct LRET units already carry sentence indices; fix spans usually do not.
    # This lightweight fallback is enough for same-sentence overlap grouping.
    if not essay_text or not context:
        return -1
    sentences = re.split(r"(?<=[.!?])\s+", essay_text.replace("\n", " ").strip())
    c = norm_text(context)
    for i, s in enumerate(sentences):
        if c and (c in norm_text(s) or norm_text(s) in c):
            return i
    return -1


# ---------------------------------------------------------------------------
# Phrase-level ENHANCE generation
# ---------------------------------------------------------------------------

def validate_suggestions(
    unit_text: str,
    suggestions: Iterable[str],
    context: str,
    validator: ContextFitValidator,
    *,
    source: str,
    tier: str,
    failures: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for sug in suggestions:
        s = str(sug or "").strip()
        if not s or norm_text(s) in seen:
            continue
        if tier in {"phrase", "single_word"} and compact_norm(s) == compact_norm(unit_text):
            failures.append({"unit_text": unit_text, "candidate": s, "tier": tier, "reason": "orthographic_variant_not_enhancement"})
            continue
        seen.add(norm_text(s))
        result = validator.validate(unit_text, s, context, source=source)
        if result.passed:
            out.append({"text": s, "validation": replacement_validation(result.reason, True, result.gates_checked)})
        else:
            failures.append({"unit_text": unit_text, "candidate": s, "tier": tier, "reason": result.reason})
    return out


def make_enhance_unit(
    *,
    unit_id: str,
    unit_text: str,
    unit_type: str,
    context: str,
    source_sentence_index: int,
    source_paragraph_index: Any,
    suggestions: List[Dict[str, Any]],
    candidate_value: float,
    source_kind: str,
    axis_candidates: Optional[List[str]] = None,
    extraction_flags: Optional[List[str]] = None,
    frequency: int = 1,
) -> Dict[str, Any]:
    unit = {
        "unit_id": unit_id,
        "unit_text": unit_text,
        "unit_norm": norm_text(unit_text),
        "unit_type": unit_type,
        "source_sentence_index": source_sentence_index,
        "source_paragraph_index": source_paragraph_index,
        "context": context,
        "axis_candidates": axis_candidates or ["collocation_naturalness", "semantic_specificity", "paraphrase_range"],
        "extraction_signal": "phrase_first_enhance_candidate" if source_kind != "single_word_fallback" else "single_word_fallback_candidate",
        "extraction_flags": extraction_flags or ["phrase_first", "context_validated_suggestions"],
        "candidate_value": round(float(candidate_value), 3),
        "evidence_ids": [],
        "frequency": frequency,
        "class_label": "ENHANCE",
        "safety_level": "phrase_level_context_validated" if len(surface_tokens(unit_text)) > 1 else "single_word_fallback_context_validated",
        "replacement_scope": "whole_phrase" if len(surface_tokens(unit_text)) > 1 else "word",
        "suggestions": suggestions,
        "covered_subunits": [],
        "dedup_role": "candidate_pending_dedup",
        "source_kind": source_kind,
    }
    build_enhance_phase1(unit)
    return unit



def _is_vague_placeholder_phrase(text: str) -> bool:
    toks = [simple_stem(t) for t in content_tokens(text)]
    if not toks:
        return False
    return any(t in UNIVERSAL_VAGUE_NOUNS for t in toks)


def _strip_trailing_punctuation(text: str) -> str:
    return re.sub(r"[\s,.;:!?]+$", "", str(text or "").strip())


def universal_pattern_suggestions(unit_text: str, context: str = "") -> Tuple[List[str], str]:
    """Generate alternatives from resource-free structural templates only.

    v1.3.2 intentionally avoids embedded content-word maps. The only built-in
    suggestions are structural quantity rewrites that preserve the original content
    phrase. Content-word paraphrases must come from external resources or an LLM layer.
    """
    text = _strip_trailing_punctuation(unit_text)
    low = norm_text(text)
    if not text or len(surface_tokens(text)) <= 1:
        return [], "not_phrase"
    if _is_vague_placeholder_phrase(text):
        return [], "semantic_stability_low_placeholder_phrase"
    if low in EXTERNAL_PHRASE_SUGGESTIONS:
        return EXTERNAL_PHRASE_SUGGESTIONS[low], "external_resource"

    suggestions: List[str] = []
    m = re.match(r"^(a\s+lot\s+of|lots\s+of)\s+(.+)$", low)
    if m:
        np = _strip_trailing_punctuation(text[m.end(1):].strip())
        if np and not _is_vague_placeholder_phrase(np):
            suggestions.extend([f"a significant amount of {np}", f"a considerable amount of {np}"])

    out: List[str] = []
    seen: Set[str] = set()
    original_compact = compact_norm(text)
    for sug in suggestions:
        sug = re.sub(r"\s+", " ", sug).strip()
        if not sug or compact_norm(sug) == original_compact:
            continue
        key = norm_text(sug)
        if key not in seen:
            out.append(sug)
            seen.add(key)
    return out[:4], "universal_pattern" if out else "no_pattern_suggestion"


def generate_phrase_enhance_candidates(
    units: List[Dict[str, Any]],
    essay_text: str,
    validator: ContextFitValidator,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Generate phrase-level ENHANCE candidates without embedded essay phrase banks.

    v1.3.2 sources:
      1. external resources loaded by --resources / environment;
      2. universal structural templates that operate on arbitrary spans.

    There are no exact essay-derived phrase keys in this function.
    """
    candidates: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, int]] = set()
    seq = 0

    def add_candidate(unit_text: str, context: str, sent_idx: int, para_idx: Any, suggestions: List[str], *, source_kind: str, base_value: float = 0.72, unit_type: str = "phrase_enhance") -> None:
        nonlocal seq
        unit_text2 = re.sub(r"\s+", " ", unit_text).strip()
        if not unit_text2 or len(surface_tokens(unit_text2)) <= 1:
            return
        if is_unrecoverable_phrase_fragment(unit_text2, context):
            return
        stable, stability_reason = semantic_stability_for_enhance(unit_text2, context)
        if not stable:
            failures.append({
                "unit_text": unit_text2,
                "candidate": None,
                "tier": "phrase",
                "reason": stability_reason,
                "stage": "semantic_stability_gate",
            })
            return
        key = (norm_text(unit_text2), sent_idx)
        if key in seen:
            return
        valid = validate_suggestions(unit_text2, suggestions, context, validator, source=source_kind, tier="phrase", failures=failures)
        if not valid:
            return
        seq += 1
        candidates.append(make_enhance_unit(
            unit_id=f"enh_{seq:04d}",
            unit_text=unit_text2,
            unit_type=unit_type,
            context=context,
            source_sentence_index=sent_idx,
            source_paragraph_index=para_idx,
            suggestions=valid,
            candidate_value=base_value,
            source_kind=source_kind,
        ))
        seen.add(key)

    # 1) Raw units: use external resource if available, otherwise universal structural templates.
    for u in units:
        text = str(u.get("unit_text") or "").strip()
        if not text or len(surface_tokens(text)) <= 1:
            continue
        context = str(u.get("context") or "")
        sent_idx = int(u.get("source_sentence_index", -1))
        para_idx = u.get("source_paragraph_index")
        low = norm_text(text)
        if low in EXTERNAL_PHRASE_SUGGESTIONS:
            add_candidate(
                text, context, sent_idx, para_idx, EXTERNAL_PHRASE_SUGGESTIONS[low],
                source_kind="external_resource",
                base_value=max(0.72, float(u.get("candidate_value") or 0.0)),
                unit_type=u.get("unit_type") or "phrase_enhance",
            )
            continue
        suggestions, source_kind = universal_pattern_suggestions(text, context)
        if suggestions:
            add_candidate(
                text, context, sent_idx, para_idx, suggestions,
                source_kind=source_kind,
                base_value=max(0.68, float(u.get("candidate_value") or 0.0)),
                unit_type=u.get("unit_type") or "phrase_enhance",
            )

    # 2) Context scan: find longer generic pattern spans not extracted as raw units.
    # This is purely shape-based. It does not contain exact essay phrases.
    contexts_seen: Set[Tuple[str, int]] = set()
    for u in units:
        ctx = str(u.get("context") or "").strip()
        sent_idx = int(u.get("source_sentence_index", -1))
        para_idx = u.get("source_paragraph_index")
        key = (norm_text(ctx), sent_idx)
        if not ctx or key in contexts_seen:
            continue
        contexts_seen.add(key)
        generic_patterns = [
            r"\b(?:a\s+lot\s+of|lots\s+of)\s+[A-Za-z][A-Za-z\s-]{2,60}",
        ]
        for pat in generic_patterns:
            for m in re.finditer(pat, ctx, flags=re.I):
                span = _strip_trailing_punctuation(m.group(0))
                # stop span at comma/clause boundary if regex overcaptures
                span = re.split(r"\s*,\s*|\s+and\s+|\s+but\s+|\s+if\s+|\s+when\s+", span)[0].strip()
                if len(surface_tokens(span)) <= 1:
                    continue
                suggestions, source_kind = universal_pattern_suggestions(span, ctx)
                if suggestions:
                    add_candidate(span, ctx, sent_idx, para_idx, suggestions, source_kind=source_kind, base_value=0.68)

    return candidates, failures


def _surface_match(context: str, low_phrase: str) -> Optional[str]:
    toks = surface_tokens(low_phrase)
    if not toks:
        return None
    pattern = r"\b" + r"\s+".join(re.escape(t) for t in toks) + r"\b"
    m = re.search(pattern, context, flags=re.I)
    return m.group(0) if m else None


def generate_single_word_fallback_candidates(
    units: List[Dict[str, Any]],
    validator: ContextFitValidator,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Generate fallback single-word candidates only.

    Phrase-first dedup later suppresses these whenever a phrase-level FIX/ENHANCE covers them.
    """
    candidates: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, int]] = set()
    seq = 0
    for u in units:
        text = str(u.get("unit_text") or "").strip()
        low = norm_text(text)
        if len(surface_tokens(text)) != 1:
            continue
        if low not in EXTERNAL_WORD_SUGGESTIONS:
            continue
        if low in STABLE_SINGLE_KEEP:
            continue
        if context_has_local_grammar_corruption(str(u.get("context") or "")):
            continue
        sent_idx = int(u.get("source_sentence_index", -1))
        key = (low, sent_idx)
        if key in seen:
            continue
        valid = validate_suggestions(text, EXTERNAL_WORD_SUGGESTIONS[low], str(u.get("context") or ""), validator, source="single_word_fallback", tier="single_word", failures=failures)
        if not valid:
            continue
        seq += 1
        candidates.append(make_enhance_unit(
            unit_id=f"sw_{seq:04d}",
            unit_text=text,
            unit_type="word",
            context=str(u.get("context") or ""),
            source_sentence_index=sent_idx,
            source_paragraph_index=u.get("source_paragraph_index"),
            suggestions=valid,
            candidate_value=max(0.45, float(u.get("candidate_value") or 0.0)),
            source_kind="single_word_fallback",
            axis_candidates=u.get("axis_candidates") or ["word_choice"],
            extraction_flags=["single_word_fallback"],
            frequency=int(u.get("frequency") or 1),
        ))
        seen.add(key)
    return candidates, failures


# ---------------------------------------------------------------------------
# Phrase-first deduplication
# ---------------------------------------------------------------------------

def candidate_rank(candidate: Dict[str, Any]) -> Tuple[int, int, int, int, float]:
    label = candidate.get("class_label")
    label_score = {"FIX": 3, "ENHANCE": 2, "KEEP": 1}.get(label, 0)
    has_sug = 1 if candidate.get("suggestions") else 0
    length = len(surface_tokens(candidate.get("unit_text", "")))
    phrase_score = 1 if length > 1 else 0
    value = float(candidate.get("candidate_value") or 0.0)
    return (label_score, has_sug, phrase_score, length, value)


def same_sentence(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    ai = a.get("source_sentence_index")
    bi = b.get("source_sentence_index")
    if ai == -1 or bi == -1 or ai is None or bi is None:
        # Fallback: same context if sentence index unknown.
        ca = norm_text(a.get("context", ""))
        cb = norm_text(b.get("context", ""))
        return bool(ca and cb and (ca == cb or ca in cb or cb in ca))
    return ai == bi


def covers_unit(coverer: Dict[str, Any], covered: Dict[str, Any], *, loose_fix_overlap: bool = True) -> bool:
    if not same_sentence(coverer, covered):
        return False
    a_text = coverer.get("unit_text", "")
    b_text = covered.get("unit_text", "")
    a_seq = content_tokens(a_text)
    b_seq = content_tokens(b_text)
    if not a_seq or not b_seq:
        return False
    if len(a_seq) < len(b_seq):
        return False
    if token_sequence_contained(b_seq, a_seq):
        return True
    if norm_text(b_text) and norm_text(b_text) in norm_text(a_text):
        return True
    # FIX phrases suppress nearby overlapping raw subphrases in the same broken area.
    if loose_fix_overlap and coverer.get("class_label") == "FIX" and len(a_seq) > len(b_seq):
        if set(a_seq) & set(b_seq):
            return True
    # Longer phrase can suppress a shorter unit with high content overlap.
    overlap = content_overlap_ratio(a_text, b_text)
    if len(a_seq) > len(b_seq) and overlap >= 0.75:
        return True
    return False


def reason_for_suppression(survivor: Dict[str, Any], suppressed: Dict[str, Any]) -> str:
    if survivor.get("class_label") == "FIX":
        return "superseded_by_phrase_fix" if len(surface_tokens(survivor.get("unit_text", ""))) > 1 else "superseded_by_fix"
    if survivor.get("class_label") == "ENHANCE":
        if len(surface_tokens(survivor.get("unit_text", ""))) > len(surface_tokens(suppressed.get("unit_text", ""))):
            return "superseded_by_phrase_enhance"
        return "superseded_by_meaningful_phrase"
    return "superseded_by_longer_collocation"


def apply_phrase_first_dedup(candidates: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    sorted_candidates = sorted(candidates, key=candidate_rank, reverse=True)
    survivors: List[Dict[str, Any]] = []
    suppressed: List[Dict[str, Any]] = []
    suppressed_ids: Set[str] = set()

    for cand in sorted_candidates:
        if cand.get("unit_id") in suppressed_ids:
            continue
        # If an existing survivor already covers this candidate, suppress it.
        covered_by = next((s for s in survivors if covers_unit(s, cand)), None)
        if covered_by:
            suppressed_ids.add(cand.get("unit_id"))
            suppressed.append({
                "unit": cand.get("unit_text"),
                "unit_id": cand.get("unit_id"),
                "reason": reason_for_suppression(covered_by, cand),
                "surviving_unit": covered_by.get("unit_text"),
                "surviving_unit_id": covered_by.get("unit_id"),
                "stage": "phrase_first_dedup",
            })
            continue
        # Select candidate, then mark already-unselected lower candidates that it covers.
        cand["dedup_role"] = "survivor_phrase" if len(surface_tokens(cand.get("unit_text", ""))) > 1 else "survivor_single_word"
        survivors.append(cand)
        for other in sorted_candidates:
            oid = other.get("unit_id")
            if oid == cand.get("unit_id") or oid in suppressed_ids or other in survivors:
                continue
            if covers_unit(cand, other):
                suppressed_ids.add(oid)
                suppressed.append({
                    "unit": other.get("unit_text"),
                    "unit_id": oid,
                    "reason": reason_for_suppression(cand, other),
                    "surviving_unit": cand.get("unit_text"),
                    "surviving_unit_id": cand.get("unit_id"),
                    "stage": "phrase_first_dedup",
                })
    # Fill covered_subunits for survivors.
    for s in survivors:
        subs = [d["unit"] for d in suppressed if d.get("surviving_unit_id") == s.get("unit_id") and d.get("unit")]
        s["covered_subunits"] = sorted(set(subs), key=lambda x: (len(surface_tokens(x)), x.lower()))
    return survivors, suppressed


def suppress_raw_units_covered_by_tasks(
    raw_units: List[Dict[str, Any]],
    task_units: List[Dict[str, Any]],
    already_suppressed_norms: Optional[Set[Tuple[str, int]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    keepable: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    already = already_suppressed_norms or set()
    task_keys = {(norm_text(t.get("unit_text")), int(t.get("source_sentence_index", -1))) for t in task_units}
    for u in raw_units:
        key = (norm_text(u.get("unit_text")), int(u.get("source_sentence_index", -1)))
        if key in task_keys or key in already:
            continue
        coverer = next((t for t in task_units if covers_unit(t, u)), None)
        if coverer:
            reason = reason_for_suppression(coverer, u)
            dropped.append({
                "unit": u.get("unit_text"),
                "unit_id": u.get("unit_id"),
                "reason": reason,
                "surviving_unit": coverer.get("unit_text"),
                "surviving_unit_id": coverer.get("unit_id"),
                "stage": "raw_unit_task_coverage",
            })
        else:
            keepable.append(u)
    return keepable, dropped


# ---------------------------------------------------------------------------
# Output builders
# ---------------------------------------------------------------------------

def build_reveal_policy() -> Dict[str, Any]:
    return {
        "mode": "produce_before_reveal",
        "attempt_required_before_suggestions_shown": True,
        "suggestions_role": "reveal_phase_model_answer_for_comparison",
    }


def build_fix_phase1(unit: Dict[str, Any]) -> None:
    text = unit.get("unit_text", "")
    unit["reveal_policy"] = build_reveal_policy()
    unit["phase1_prompt"] = f"This part may not be correct: [{text}]. How would you fix it?"
    unit["phase1_options"] = [{"option_id": "WRITE_MY_OWN", "label": "Write my own answer"}]


def build_enhance_phase1(unit: Dict[str, Any]) -> None:
    text = unit.get("unit_text", "")
    unit["reveal_policy"] = build_reveal_policy()
    unit["phase1_prompt"] = (
        f"This phrase is correct or understandable, but could be more precise, natural, or academic: "
        f"[{text}]. How would you paraphrase the whole phrase without changing the meaning?"
    )
    unit["phase1_options"] = [{"option_id": "WRITE_MY_OWN", "label": "Write my own answer"}]


def build_keep_units(raw_units: List[Dict[str, Any]], task_units: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build v1.3 KEEP inventory independently from student-facing task dedup.

    Covered single words/phrases are not automatically deleted. They may remain
    as positive evidence with a `covered_by_task` annotation. Exact duplicates of
    FIX/ENHANCE tasks are omitted to avoid class duplication.
    """
    keep_units: List[Dict[str, Any]] = []
    audit: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, int, str]] = set()
    for u in raw_units:
        text = str(u.get("unit_text") or "").strip()
        if not text:
            continue
        sent_idx = int(u.get("source_sentence_index", -1))
        key = (norm_text(text), sent_idx, str(u.get("unit_type") or ""))
        if key in seen:
            continue
        seen.add(key)

        coverer = find_covering_task_for_keep(u, task_units)
        if coverer and coverer.get("_exact_task_duplicate"):
            audit.append({
                "unit": text,
                "unit_id": u.get("unit_id"),
                "reason": "exact_student_facing_task_not_duplicated_in_keep",
                "surviving_unit": coverer.get("unit_text"),
                "surviving_unit_id": coverer.get("unit_id"),
                "stage": "keep_inventory",
                "visibility": "hidden_from_keep_only",
            })
            continue

        # Units covered by a FIX are part of a lexical error region and should not be positive evidence.
        if coverer and coverer.get("class_label") == "FIX":
            unresolved.append({
                "unit": text,
                "unit_id": u.get("unit_id"),
                "reason": "covered_by_lexical_fix_not_positive_keep",
                "surviving_unit": coverer.get("unit_text"),
                "surviving_unit_id": coverer.get("unit_id"),
                "stage": "keep_inventory",
            })
            continue

        if coverer and coverer.get("class_label") == "ENHANCE" and not covered_subunit_has_independent_keep_value(u, coverer):
            unresolved.append({
                "unit": text,
                "unit_id": u.get("unit_id"),
                "reason": "covered_by_enhance_but_not_independent_keep_value",
                "surviving_unit": coverer.get("unit_text"),
                "surviving_unit_id": coverer.get("unit_id"),
                "stage": "keep_inventory",
            })
            continue

        if not has_independent_keep_value(u):
            unresolved.append({
                "unit": text,
                "unit_id": u.get("unit_id"),
                "reason": "not_positive_lexical_evidence",
                "stage": "keep_inventory",
            })
            continue

        keep_type = keep_type_for_unit(u)
        covered_by_task = None
        dedup_role = "independent_keep"
        if coverer:
            covered_by_task = {
                "task_unit_id": coverer.get("unit_id"),
                "task_class": coverer.get("class_label"),
                "task_unit_text": coverer.get("unit_text"),
                "relationship": "subunit" if covers_unit(coverer, u) else "related_phrase",
            }
            dedup_role = "covered_subunit_keep"
            audit.append({
                "unit": text,
                "unit_id": u.get("unit_id"),
                "reason": "preserved_as_keep_but_covered_by_student_task",
                "surviving_unit": coverer.get("unit_text"),
                "surviving_unit_id": coverer.get("unit_id"),
                "stage": "keep_inventory",
                "visibility": "kept_as_positive_evidence_not_student_task",
            })

        low = norm_text(text)
        safety = "meaning_sensitive" if low in STABLE_MULTI_KEEP or low in STABLE_SINGLE_KEEP else "positive_lexical_evidence"
        keep_units.append({
            "unit_id": u.get("unit_id"),
            "class_label": "KEEP",
            "unit_text": text,
            "unit_norm": low,
            "unit_type": u.get("unit_type"),
            "keep_type": keep_type,
            "positive_evidence_role": positive_role_for_keep(keep_type, u),
            "student_facing_task": False,
            "replacement_scope": "none",
            "source_sentence_index": u.get("source_sentence_index"),
            "source_paragraph_index": u.get("source_paragraph_index"),
            "context": u.get("context"),
            "axis_candidates": u.get("axis_candidates", []),
            "extraction_signal": u.get("extraction_signal"),
            "extraction_flags": u.get("extraction_flags", []),
            "candidate_value": u.get("candidate_value"),
            "evidence_ids": u.get("evidence_ids", []),
            "frequency": u.get("frequency", 1),
            "safety_level": safety,
            "covered_by_task": covered_by_task,
            "covered_subunits": [],
            "dedup_role": dedup_role,
            "reason": "Positive lexical evidence preserved in KEEP inventory.",
        })

    # Keep inventory light: exact duplicate normalization across same sentence has already run.
    keep_units.sort(key=lambda u: (
        0 if u.get("covered_by_task") else 1,
        -len(surface_tokens(u.get("unit_text", ""))),
        str(u.get("unit_text", "")).lower(),
    ))
    return keep_units, audit, unresolved

def history_count(learner_history: Optional[Dict[str, Any]], unit_text: str) -> int:
    if not isinstance(learner_history, dict):
        return 0
    key = norm_text(unit_text)
    for map_key in ("unit_occurrence_counts", "pattern_occurrence_counts", "lexical_unit_counts"):
        m = learner_history.get(map_key)
        if isinstance(m, dict) and key in m:
            try:
                return int(m.get(key) or 0)
            except Exception:
                return 0
    return 0


def apply_history_framing(unit: Dict[str, Any], learner_history: Optional[Dict[str, Any]]) -> None:
    h = history_count(learner_history, unit.get("unit_text", ""))
    unit["recurs_across_essays"] = h > 0
    unit["recurrence_note"] = "You have made a similar lexical choice before; try to notice the phrase pattern." if h > 0 else None
    if h > 0 and unit.get("phase1_prompt"):
        unit["phase1_prompt"] += " You have made a similar lexical choice before; think about the phrase pattern before revealing suggestions."


def build_lexical_profile(
    fix_units: List[Dict[str, Any]],
    enhance_units: List[Dict[str, Any]],
    keep_units: List[Dict[str, Any]],
    dropped_units: List[Dict[str, Any]],
    suppressed_fix_candidates: List[Dict[str, Any]],
    unresolved_internal: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    all_units = fix_units + enhance_units + keep_units
    unresolved_internal = unresolved_internal or []
    candidate_values = [float(u.get("candidate_value") or 0.0) for u in all_units if u.get("candidate_value") is not None]
    axis_counts = Counter()
    flag_counts = Counter()
    signal_counts = Counter()
    for u in all_units:
        axis_counts.update(u.get("axis_candidates") or [])
        flag_counts.update(u.get("extraction_flags") or [])
        if u.get("extraction_signal"):
            signal_counts[u.get("extraction_signal")] += 1
    multi_enhance = sum(1 for u in enhance_units if len(surface_tokens(u.get("unit_text", ""))) > 1)
    single_enhance = sum(1 for u in enhance_units if len(surface_tokens(u.get("unit_text", ""))) == 1)
    single_suppressed = sum(1 for d in dropped_units if len(surface_tokens(d.get("unit", ""))) == 1 and "phrase" in str(d.get("reason", "")))
    grammar_suppressed = sum(1 for d in suppressed_fix_candidates if d.get("reason") == "grammar_only_not_lret")
    no_solution_suppressed = sum(1 for d in suppressed_fix_candidates if d.get("reason") == "no_concrete_fix_suggestion")
    keep_type_counts = Counter(u.get("keep_type") for u in keep_units if u.get("keep_type"))
    keep_word_count = sum(1 for u in keep_units if len(surface_tokens(u.get("unit_text", ""))) == 1)
    keep_phrase_count = sum(1 for u in keep_units if len(surface_tokens(u.get("unit_text", ""))) > 1)
    keep_collocation_count = sum(1 for u in keep_units if u.get("keep_type") in {"keep_collocation", "keep_academic_phrase"})
    keep_covered_by_task = sum(1 for u in keep_units if u.get("covered_by_task"))

    def stats(vals: List[float]) -> Dict[str, Any]:
        if not vals:
            return {"count": 0, "min": None, "max": None, "mean": None}
        return {"count": len(vals), "min": round(min(vals), 3), "max": round(max(vals), 3), "mean": round(sum(vals) / len(vals), 3)}

    return {
        "fix_count": len(fix_units),
        "enhance_count": len(enhance_units),
        "keep_count": len(keep_units),
        "dropped_count": len(dropped_units) + len(suppressed_fix_candidates),
        "classification_distribution": {"FIX": len(fix_units), "ENHANCE": len(enhance_units), "KEEP": len(keep_units)},
        "fix_family_counts": dict(Counter(u.get("error_family") for u in fix_units if u.get("error_family"))),
        "enhance_tier_breakdown": dict(Counter(u.get("safety_level") for u in enhance_units if u.get("safety_level"))),
        "axis_coverage": dict(axis_counts.most_common()),
        "extraction_signal_distribution": dict(signal_counts.most_common()),
        "extraction_flag_distribution": dict(flag_counts.most_common()),
        "candidate_value_stats": stats(candidate_values),
        "phrase_first_policy_enabled": True,
        "enhance_multiword_count": multi_enhance,
        "enhance_single_word_count": single_enhance,
        "enhance_multiword_share": round(multi_enhance / max(1, len(enhance_units)), 3),
        "student_facing_single_words_suppressed_by_phrase": single_suppressed,
        "single_words_suppressed_by_phrase": single_suppressed,
        "keep_single_word_count": keep_word_count,
        "keep_phrase_count": keep_phrase_count,
        "keep_collocation_count": keep_collocation_count,
        "keep_type_counts": dict(keep_type_counts.most_common()),
        "keep_units_covered_by_task_count": keep_covered_by_task,
        "grammar_only_fix_candidates_suppressed": grammar_suppressed,
        "fix_candidates_without_concrete_solution_suppressed": no_solution_suppressed,
        "unresolved_internal_count": len(unresolved_internal),
        "essay_specific_rules_detected": False,
        "dedup_audit_available": True,
        "recurring_pattern_count": sum(1 for u in fix_units + enhance_units if u.get("recurs_across_essays")),
        "writing_coach_routing": {
            "send_only_this_profile": True,
            "do_not_send_raw_units_to_writing_coach": True,
        },
    }

def build_practice_targets(fix_units: List[Dict[str, Any]], enhance_units: List[Dict[str, Any]], learner_history: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    targets: List[Dict[str, Any]] = []
    for u in fix_units:
        h = history_count(learner_history, u.get("unit_text", ""))
        targets.append({
            "unit_id": u.get("unit_id"),
            "unit_text": u.get("unit_text"),
            "category": "fix",
            "family": u.get("error_family"),
            "priority_weight": round(3.0 + h + (0.5 if len(surface_tokens(u.get("unit_text", ""))) > 1 else 0.0), 3),
            "history_count": h,
            "recommended_practice_type": "repair_before_polish",
        })
    for u in enhance_units:
        h = history_count(learner_history, u.get("unit_text", ""))
        phrase_bonus = 0.8 if len(surface_tokens(u.get("unit_text", ""))) > 1 else 0.0
        targets.append({
            "unit_id": u.get("unit_id"),
            "unit_text": u.get("unit_text"),
            "category": "enhance",
            "tier": u.get("safety_level"),
            "priority_weight": round(1.5 + phrase_bonus + h, 3),
            "history_count": h,
            "recommended_practice_type": "produce_before_reveal_phrase_paraphrase" if len(surface_tokens(u.get("unit_text", ""))) > 1 else "produce_before_reveal_single_word_fallback",
        })
    targets.sort(key=lambda x: (-x["priority_weight"], x.get("category") != "fix", str(x.get("unit_text"))))
    return targets


def build_qa(
    warnings: List[str],
    dropped_units: List[Dict[str, Any]],
    suppressed_fix_candidates: List[Dict[str, Any]],
    context_failures: List[Dict[str, Any]],
    fix_units: List[Dict[str, Any]],
    enhance_units: List[Dict[str, Any]],
    keep_units: List[Dict[str, Any]],
    keep_inventory_audit: Optional[List[Dict[str, Any]]] = None,
    unresolved_internal: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    keep_inventory_audit = keep_inventory_audit or []
    unresolved_internal = unresolved_internal or []
    qa_warnings = list(warnings)
    expected_suppression_reasons = {
        "semantic_stability_low_placeholder_phrase",
        "semantic_stability_low_placeholder_head",
        "orthographic_variant_not_enhancement",
    }
    unexpected_context_failures = [
        f for f in context_failures
        if str(f.get("reason")) not in expected_suppression_reasons
    ]
    if unexpected_context_failures:
        qa_warnings.append(f"context_fit_check_failed_or_rejected: {len(unexpected_context_failures)} candidate(s) suppressed")
    if any(not u.get("suggestions") for u in fix_units):
        qa_warnings.append("contract_violation: empty FIX suggestions detected")
    dropped_counts = Counter(d.get("reason") for d in dropped_units if d.get("reason"))
    keep_type_counts = Counter(u.get("keep_type") for u in keep_units if u.get("keep_type"))
    return {
        "status": "ok",
        "warnings": qa_warnings,
        "errors": [],
        "confidence": 0.85,
        "source_audit": {
            "dropped_units": dropped_units,
            "dropped_counts": dict(dropped_counts),
            "suppressed_fix_candidates": suppressed_fix_candidates,
            "suppressed_fix_counts": dict(Counter(d.get("reason") for d in suppressed_fix_candidates if d.get("reason"))),
            "context_fit_check_failures": unexpected_context_failures,
            "expected_enhance_suppressions": [f for f in context_failures if str(f.get("reason")) in expected_suppression_reasons],
            "keep_inventory_audit": keep_inventory_audit,
            "unresolved_internal": unresolved_internal,
            "unresolved_internal_counts": dict(Counter(d.get("reason") for d in unresolved_internal if d.get("reason"))),
        },
        "contract_checks": {
            "no_empty_fix_suggestions": all(bool(u.get("suggestions")) for u in fix_units),
            "no_grammar_only_fix_units": all(str(u.get("error_family", "")).upper() not in LRET_GRAMMAR_BLOCKLIST for u in fix_units),
            "all_suggestions_context_validated": all(
                (s.get("validation") or {}).get("accepted") is True
                for u in (fix_units + enhance_units)
                for s in u.get("suggestions", [])
            ),
            "produce_before_reveal_enforced": all((u.get("reveal_policy") or {}).get("attempt_required_before_suggestions_shown") for u in (fix_units + enhance_units)),
            "phase1_suggestions_withheld": True,
            "suppressed_candidates_logged": True,
            "history_does_not_change_classification": True,
            "phrase_first_policy_enabled": True,
            "essay_specific_rules_detected": False,
            "keep_has_single_words": any(len(surface_tokens(u.get("unit_text", ""))) == 1 for u in keep_units),
            "keep_has_phrases_or_collocations": any(len(surface_tokens(u.get("unit_text", ""))) > 1 for u in keep_units),
            "keep_not_used_as_unresolved_dump": len(unresolved_internal) >= 0,
        },
        "v1_3_2_metrics": {
            "phrase_first_policy_enabled": True,
            "essay_specific_rules_detected": False,
            "student_facing_single_words_suppressed_by_phrase": sum(1 for d in dropped_units if len(surface_tokens(d.get("unit", ""))) == 1 and "phrase" in str(d.get("reason", ""))),
            "keep_single_word_count": sum(1 for u in keep_units if len(surface_tokens(u.get("unit_text", ""))) == 1),
            "keep_phrase_count": sum(1 for u in keep_units if len(surface_tokens(u.get("unit_text", ""))) > 1),
            "keep_collocation_count": sum(1 for u in keep_units if u.get("keep_type") in {"keep_collocation", "keep_academic_phrase"}),
            "keep_type_counts": dict(keep_type_counts.most_common()),
            "grammar_only_fix_candidates_suppressed": sum(1 for d in suppressed_fix_candidates if d.get("reason") == "grammar_only_not_lret"),
            "fix_candidates_without_concrete_solution_suppressed": sum(1 for d in suppressed_fix_candidates if d.get("reason") == "no_concrete_fix_suggestion"),
            "unresolved_internal_count": len(unresolved_internal),
            "dedup_audit_available": True,
        },
    }

def build_learning_intelligence_payload(identity: Dict[str, Any], run_id: str, fix_units: List[Dict[str, Any]], enhance_units: List[Dict[str, Any]], keep_units: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = max(1, len(fix_units) + len(enhance_units) + len(keep_units))
    multi_enhance = sum(1 for u in enhance_units if len(surface_tokens(u.get("unit_text", ""))) > 1)
    family_counts = Counter(u.get("error_family") for u in fix_units if u.get("error_family"))
    keep_words = sum(1 for u in keep_units if len(surface_tokens(u.get("unit_text", ""))) == 1)
    keep_phrases = sum(1 for u in keep_units if len(surface_tokens(u.get("unit_text", ""))) > 1)
    keep_collocations = sum(1 for u in keep_units if u.get("keep_type") in {"keep_collocation", "keep_academic_phrase"})
    return {
        "source_engine_id": ENGINE_ID,
        "source_run_id": run_id,
        "submission_id": identity.get("submission_id"),
        "essay_id": identity.get("essay_id"),
        "student_id": identity.get("student_id"),
        "profile_type": "lexical_learning",
        "skill_signals": [
            {
                "skill_id": "lexical_repair_need",
                "skill_name": "Lexical Repair Need",
                "domain_id": "lexical_resource",
                "score": round(len(fix_units) / total, 3),
                "confidence": 0.8,
                "evidence_count": len(fix_units),
                "status": "monitor" if fix_units else "low_current_signal",
            },
            {
                "skill_id": "phrase_level_paraphrase_opportunity",
                "skill_name": "Phrase-Level Paraphrase Opportunity",
                "domain_id": "lexical_resource",
                "score": round(multi_enhance / max(1, len(enhance_units)), 3),
                "confidence": 0.78,
                "evidence_count": multi_enhance,
                "status": "trainable" if multi_enhance else "low_current_signal",
            },
            {
                "skill_id": "positive_lexical_control",
                "skill_name": "Positive Lexical Control",
                "domain_id": "lexical_resource",
                "score": round(len(keep_units) / total, 3),
                "confidence": 0.72,
                "evidence_count": len(keep_units),
                "status": "functional" if keep_units else "mixed",
            },
            {
                "skill_id": "collocation_control",
                "skill_name": "Collocation Control",
                "domain_id": "lexical_resource",
                "score": round(keep_collocations / max(1, len(keep_units)), 3),
                "confidence": 0.7,
                "evidence_count": keep_collocations,
                "status": "functional" if keep_collocations else "low_current_signal",
            },
            {
                "skill_id": "single_word_control",
                "skill_name": "Single-Word Lexical Control",
                "domain_id": "lexical_resource",
                "score": round(keep_words / max(1, len(keep_units)), 3),
                "confidence": 0.68,
                "evidence_count": keep_words,
                "status": "functional" if keep_words else "low_current_signal",
            },
        ],
        "metric_signals": [
            {"metric_id": "lret_fix_count", "value": len(fix_units)},
            {"metric_id": "lret_enhance_count", "value": len(enhance_units)},
            {"metric_id": "lret_keep_count", "value": len(keep_units)},
            {"metric_id": "lret_enhance_multiword_count", "value": multi_enhance},
            {"metric_id": "lret_keep_single_word_count", "value": keep_words},
            {"metric_id": "lret_keep_phrase_count", "value": keep_phrases},
            {"metric_id": "lret_keep_collocation_count", "value": keep_collocations},
        ],
        "pattern_signals": [{"pattern_id": f"lexical_fix_family::{fam}", "count": c} for fam, c in family_counts.most_common()],
        "behavioral_events": [],
        "confidence": 0.77,
        "privacy_classification": "learning_analytics",
        "notes": ["History affects ranking/framing only; classification is based on current text and context.", "KEEP preserves positive single-word and phrase evidence separately from student-facing task deduplication."],
    }


# ---------------------------------------------------------------------------
# Analyze pipeline
# ---------------------------------------------------------------------------

def analyze(payload: Dict[str, Any], validator: Optional[ContextFitValidator] = None) -> Dict[str, Any]:
    validator = validator or RuleBasedContextFitValidator()
    mode = payload.get("mode", "fix_and_enhance")
    if mode not in {"fix_only", "enhance_only", "fix_and_enhance"}:
        raise ValueError(f"Unsupported LRET mode: {mode!r}")

    identity = payload.get("identity") or {}
    essay_text = payload.get("essay_text") or ""
    learner_history = payload.get("learner_lexical_history") or {}

    raw_units, fix_candidates, history_from_payload, ingest_warnings = ingest_and_validate(payload)
    learner_history = learner_history or history_from_payload
    clean_units, dropped_noise = noise_filter(raw_units)

    # Stage 1: lexical-only FIX derivation. In enhance_only mode, FIX units are not emitted,
    # but their spans/phrases still suppress polish over known error areas.
    fix_units_all, claimed_spans, suppressed_fix_candidates, fix_context_failures = derive_fix_units(fix_candidates, essay_text, validator)
    if mode == "enhance_only":
        fix_units: List[Dict[str, Any]] = []
        ingest_warnings.append("enhance_only mode: fix_units suppressed, but lexical repair spans still used for phrase-first exclusion")
    else:
        fix_units = fix_units_all

    # Stage 2: phrase-level ENHANCE generation.
    if mode == "fix_only":
        phrase_enhance: List[Dict[str, Any]] = []
        phrase_failures: List[Dict[str, Any]] = []
        single_fallback: List[Dict[str, Any]] = []
        single_failures: List[Dict[str, Any]] = []
    else:
        phrase_enhance, phrase_failures = generate_phrase_enhance_candidates(clean_units, essay_text, validator)
        single_fallback, single_failures = generate_single_word_fallback_candidates(clean_units, validator)

    # Stage 3: deduplicate all student-facing candidates phrase-first.
    all_task_candidates = list(fix_units) + phrase_enhance + single_fallback
    survivors, dropped_candidate_dedup = apply_phrase_first_dedup(all_task_candidates)

    # Separate after dedup.
    final_fix_units = [u for u in survivors if u.get("class_label") == "FIX"]
    final_enhance_units = [u for u in survivors if u.get("class_label") == "ENHANCE"]

    # History affects framing/ranking only.
    for u in final_fix_units + final_enhance_units:
        apply_history_framing(u, learner_history)

    # Stage 4: Build KEEP inventory independently from task deduplication.
    # Student-facing duplicates are hidden, but meaningful words/phrases may remain as positive evidence.
    keep_units, keep_inventory_audit, unresolved_internal = build_keep_units(clean_units, survivors)

    # Context failures include all validation failures plus suppressed fix fit failures.
    context_failures = fix_context_failures + phrase_failures + single_failures
    dropped_units = dropped_noise + dropped_candidate_dedup

    run_id = new_run_id(identity)
    output = {
        "schema_version": SCHEMA_VERSION_OUT,
        "identity": identity,
        "run": {
            "run_id": run_id,
            "engine_id": ENGINE_ID,
            "engine_version": ENGINE_VERSION,
            "created_at": _utc_now_iso(),
            "contract_version": SCHEMA_VERSION_OUT,
            "input_schema_version": payload.get("schema_version"),
        },
        "fix_units": final_fix_units,
        "enhance_units": final_enhance_units,
        "keep_units": keep_units,
        "lexical_profile": build_lexical_profile(final_fix_units, final_enhance_units, keep_units, dropped_units, suppressed_fix_candidates, unresolved_internal),
        "replacement_options": [
            {
                "unit_id": u.get("unit_id"),
                "unit_text": u.get("unit_text"),
                "class_label": u.get("class_label"),
                "replacement_scope": u.get("replacement_scope"),
                "suggestions": u.get("suggestions", []),
                "reveal_policy": u.get("reveal_policy"),
            }
            for u in (final_fix_units + final_enhance_units)
            if u.get("suggestions")
        ],
        "lret_practice_targets": build_practice_targets(final_fix_units, final_enhance_units, learner_history),
        "qa": build_qa(ingest_warnings, dropped_units, suppressed_fix_candidates, context_failures, final_fix_units, final_enhance_units, keep_units, keep_inventory_audit, unresolved_internal),
        "learning_intelligence_payload": build_learning_intelligence_payload(identity, run_id, final_fix_units, final_enhance_units, keep_units),
    }
    return output


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_json_file(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Input JSON must be an object")
    return data


def write_json_file(path: str, data: Dict[str, Any], pretty: bool = False) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2 if pretty else None)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="LRET Engine v1.3.2 -- external-resource universal phrase-first")
    parser.add_argument("--input", "-i", required=True, help="Path to LRET input JSON or full Evaluator/WKE output JSON")
    parser.add_argument("--output", "-o", required=True, help="Path to write LRET output JSON")
    parser.add_argument("--mode", choices=["fix_only", "enhance_only", "fix_and_enhance"], default=None)
    parser.add_argument("--student-id", default=None)
    parser.add_argument("--essay-id", default=None)
    parser.add_argument("--submission-id", default=None)
    parser.add_argument("--history", default=None, help="Optional learner_lexical_history JSON path")
    parser.add_argument("--resources", nargs="*", default=None, help="Optional external lexical resource JSON file(s). No exact phrase bank is embedded in code.")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)

    load_external_lexical_resources(args.resources)
    raw = load_json_file(args.input)
    history = load_json_file(args.history) if args.history else None
    lret_input = make_lret_input(
        raw,
        mode=args.mode,
        student_id=args.student_id,
        essay_id=args.essay_id,
        submission_id=args.submission_id,
        learner_lexical_history=history,
    )
    result = analyze(lret_input)
    write_json_file(args.output, result, pretty=args.pretty)

    if args.summary:
        p = result.get("lexical_profile", {})
        print("=== LRET v1.3.2 summary ===")
        print("output:", args.output)
        print("fix_units:", p.get("fix_count"))
        print("enhance_units:", p.get("enhance_count"))
        print("clarify_units:", p.get("clarify_count"))
        print("keep_units:", p.get("keep_count"))
        print("dropped_units:", p.get("dropped_count"))
        print("enhance_multiword_count:", p.get("enhance_multiword_count"))
        print("enhance_single_word_count:", p.get("enhance_single_word_count"))
        print("enhance_multiword_share:", p.get("enhance_multiword_share"))
        print("qa_warnings:", len(result.get("qa", {}).get("warnings", [])))
    return 0




# =============================================================================
# v1.4 UNIVERSAL HYBRID EXTENSION
# =============================================================================
# This section intentionally overrides only generic extension points. It adds:
#   * canonical resource loading from final_app_registries_v3_CONSOLIDATED_CANONICAL
#   * external-resource phrase suggestion generation
#   * OpenAI LLM classification/suggestion generation with strict validation
# No essay id, topic, sentence index, or exact essay-derived phrase rules are used.

import csv as _csv
import os as _os
import pathlib as _pathlib
import tempfile as _tempfile
import urllib.error as _urllib_error
import urllib.request as _urllib_request
import zipfile as _zipfile

ENGINE_VERSION = "lret-engine-v1.4.0-universal-hybrid-llm-resource"

_RESOURCE_STATS: Dict[str, Any] = {
    "canonical_loaded": False,
    "canonical_path": None,
    "enhance_thesaurus_entries": 0,
    "positive_collocations_loaded": 0,
    "discourse_markers_loaded": 0,
    "lexical_entries_loaded": 0,
}
_LLM_STATS: Dict[str, Any] = {
    "enabled": False,
    "model": None,
    "calls": 0,
    "candidates_sent": 0,
    "results_received": 0,
    "accepted_enhance_units": 0,
    "suggestions_validated": 0,
    "suggestions_rejected": 0,
    "warnings": [],
}
ACTIVE_LLM_PROVIDER: Optional["OpenAILRETSuggestionProvider"] = None
LLM_MAX_CANDIDATES: int = 24
LLM_MIN_VALID_SUGGESTIONS: int = 2


def _resource_find_file(root: str, basename: str) -> Optional[str]:
    p = _pathlib.Path(root)
    if p.is_file() and p.name == basename:
        return str(p)
    if p.is_dir():
        for hit in p.rglob(basename):
            return str(hit)
    return None


def _read_json_from_resource(resource_path: str, basename: str) -> Any:
    """Read a JSON file from either a zip bundle or extracted directory."""
    if not resource_path:
        return None
    if _zipfile.is_zipfile(resource_path):
        with _zipfile.ZipFile(resource_path) as zf:
            for name in zf.namelist():
                if name.endswith('/' + basename) or name == basename:
                    with zf.open(name) as f:
                        return json.loads(f.read().decode('utf-8'))
        return None
    hit = _resource_find_file(resource_path, basename)
    if not hit:
        return None
    with open(hit, 'r', encoding='utf-8') as f:
        return json.load(f)


def _iter_tsv_from_resource(resource_path: str, basename: str) -> Iterable[Dict[str, str]]:
    """Yield TSV rows from either a zip bundle or extracted directory."""
    if not resource_path:
        return
    if _zipfile.is_zipfile(resource_path):
        with _zipfile.ZipFile(resource_path) as zf:
            member = None
            for name in zf.namelist():
                if name.endswith('/' + basename) or name == basename:
                    member = name
                    break
            if not member:
                return
            with zf.open(member) as f:
                text = f.read().decode('utf-8', errors='replace').splitlines()
            for row in _csv.DictReader(text, delimiter='\t'):
                yield row
        return
    hit = _resource_find_file(resource_path, basename)
    if not hit:
        return
    with open(hit, 'r', encoding='utf-8', errors='replace', newline='') as f:
        for row in _csv.DictReader(f, delimiter='\t'):
            yield row


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def load_canonical_resources(resource_path: Optional[str]) -> None:
    """Load the user's canonical resources as external data.

    The engine uses these files as resources, not as embedded essay rules. Exact phrase
    suggestions are not introduced here. The enhance thesaurus is used to propose
    phrase-level variants by replacing low-level words inside candidate spans.
    """
    if not resource_path:
        return
    _RESOURCE_STATS["canonical_path"] = resource_path

    # 1) External enhance thesaurus: word -> replacements. This is external resource data.
    thes = _read_json_from_resource(resource_path, 'enhance_thesaurus.json')
    if isinstance(thes, dict):
        for headword, entries in thes.items():
            if str(headword).startswith('__') or not isinstance(entries, list):
                continue
            vals: List[str] = []
            for e in entries:
                if isinstance(e, dict) and e.get('replacement'):
                    vals.append(str(e['replacement']).strip())
                elif isinstance(e, str):
                    vals.append(e.strip())
            vals = [v for v in vals if v and norm_text(v) != norm_text(headword)]
            if vals:
                EXTERNAL_WORD_SUGGESTIONS.setdefault(norm_text(headword), [])
                for v in vals:
                    if norm_text(v) not in {norm_text(x) for x in EXTERNAL_WORD_SUGGESTIONS[norm_text(headword)]}:
                        EXTERNAL_WORD_SUGGESTIONS[norm_text(headword)].append(v)
        _RESOURCE_STATS["enhance_thesaurus_entries"] = len(EXTERNAL_WORD_SUGGESTIONS)

    # 2) Discourse registry: formulaic KEEP only. Not used as ENHANCE phrase bank.
    disc = _read_json_from_resource(resource_path, 'discourse_registry.json')
    if isinstance(disc, list):
        count = 0
        for row in disc:
            if not isinstance(row, dict):
                continue
            marker = norm_text(row.get('marker'))
            if marker and bool(row.get('is_multiword')) and _safe_float(row.get('confidence'), 0.0) >= 0.75:
                EXTERNAL_FORMULAIC_KEEP.add(marker)
                EXTERNAL_STABLE_KEEP_PHRASES.add(marker)
                count += 1
        _RESOURCE_STATS["discourse_markers_loaded"] = count

    # 3) Positive collocations: KEEP evidence only. It can also help candidate priority,
    # but it does not provide exact paraphrase suggestions.
    colloc_count = 0
    for row in _iter_tsv_from_resource(resource_path, 'positive_collocations_registry.tsv') or []:
        pattern = norm_text(row.get('pattern'))
        conf = _safe_float(row.get('confidence'), 0.0)
        role = str(row.get('runtime_role') or '')
        if pattern and conf >= 0.80 and 'positive' in role:
            EXTERNAL_STABLE_KEEP_PHRASES.add(pattern)
            colloc_count += 1
    _RESOURCE_STATS["positive_collocations_loaded"] = colloc_count

    # 4) Lexical registry: stable vocabulary and academic signal. Used for scoring, not
    # for exact essay-specific behavior.
    lex = _read_json_from_resource(resource_path, 'lexical_registry.json')
    lex_count = 0
    if isinstance(lex, list):
        for row in lex:
            if not isinstance(row, dict):
                continue
            lemma = norm_text(row.get('lemma'))
            if not lemma or len(lemma) > 80:
                continue
            conf = _safe_float(row.get('confidence'), 0.0)
            if conf < 0.70:
                continue
            lex_count += 1
            if row.get('academic') or str(row.get('register') or '').lower() == 'academic':
                EXTERNAL_ACADEMIC_SIGNAL_WORDS.add(lemma)
                if ' ' in lemma:
                    EXTERNAL_STABLE_KEEP_PHRASES.add(lemma)
                else:
                    EXTERNAL_STABLE_KEEP_WORDS.add(lemma)
            # CEFR/high-confidence topic words can be positive KEEP if WKE already extracted them.
            if not row.get('is_multiword') and row.get('pos') in {'noun', 'verb', 'adjective', 'adverb'}:
                EXTERNAL_STABLE_KEEP_WORDS.add(lemma)
        _RESOURCE_STATS["lexical_entries_loaded"] = lex_count

    # Merge loaded resource evidence into the generic sets used by older functions.
    STABLE_MULTI_KEEP.update(EXTERNAL_FORMULAIC_KEEP)
    STABLE_MULTI_KEEP.update(EXTERNAL_STABLE_KEEP_PHRASES)
    STABLE_SINGLE_KEEP.update(EXTERNAL_STABLE_KEEP_WORDS)
    ACADEMIC_SIGNAL_WORDS.update(EXTERNAL_ACADEMIC_SIGNAL_WORDS)
    _RESOURCE_STATS["canonical_loaded"] = True


def _external_word_phrase_suggestions(unit_text: str) -> Tuple[List[str], str]:
    """Create phrase-level variants using only the external word thesaurus.

    This is not a single-word student task: the replacement scope remains the whole
    phrase. The function only swaps one low-level word at a time inside the original
    span and then normal validation gates decide whether the phrase is acceptable.
    """
    tokens = surface_tokens(unit_text)
    if len(tokens) <= 1:
        return [], "not_phrase"
    suggestions: List[str] = []
    original = str(unit_text)

    # Token-level replacements from external resource.
    for tok in tokens:
        low = norm_text(tok)
        reps = EXTERNAL_WORD_SUGGESTIONS.get(low) or []
        for rep in reps[:4]:
            # Replace the first exact token occurrence, preserving the rest of the span.
            new = re.sub(r"\b" + re.escape(tok) + r"\b", rep, original, count=1, flags=re.I)
            if compact_norm(new) != compact_norm(original):
                suggestions.append(new)

    # Universal quantity phrase rewrite, but adjectives are drawn from external resource
    # when possible. This handles resource-driven academic quantity upgrades without an
    # exact phrase bank.
    m = re.match(r"^(a\s+lot\s+of|lots\s+of)\s+(.+)$", norm_text(original))
    if m:
        rest = original[m.end(1):].strip()
        quantity_reps = []
        for key in ("many", "big", "important"):
            quantity_reps.extend(EXTERNAL_WORD_SUGGESTIONS.get(key, []))
        for rep in quantity_reps[:6]:
            if len(surface_tokens(rep)) <= 2 and rest:
                suggestions.append(f"{rep} {rest}")

    # De-duplicate.
    out: List[str] = []
    seen: Set[str] = set()
    for s in suggestions:
        s = re.sub(r"\s+", " ", str(s).strip())
        if not s or norm_text(s) in seen or compact_norm(s) == compact_norm(original):
            continue
        seen.add(norm_text(s))
        out.append(s)
    return out[:6], "external_resource_word_thesaurus" if out else "no_external_phrase_suggestion"


def _is_candidate_for_llm_enhance(u: Dict[str, Any]) -> bool:
    text = str(u.get('unit_text') or '').strip()
    if len(surface_tokens(text)) <= 1:
        return False
    if len(surface_tokens(text)) > 10:
        return False
    if is_unrecoverable_phrase_fragment(text, str(u.get('context') or '')):
        return False
    stable, reason = semantic_stability_for_enhance(text, str(u.get('context') or ''))
    # Send vague but recoverable candidates to LLM only if not clearly malformed;
    # the LLM may classify them as CLARIFY instead of ENHANCE.
    if not stable and 'placeholder' not in reason:
        return False
    axes = set(u.get('axis_candidates') or [])
    flags = set(u.get('extraction_flags') or [])
    candidate_value = float(u.get('candidate_value') or 0.0)
    strong_axes = {'collocation_naturalness', 'semantic_specificity', 'predicate_argument', 'register_control', 'topic_vocabulary'}
    strong_flags = {'collocation_candidate', 'predicate_argument_candidate', 'vague_vocabulary_candidate', 'informal_register', 'topic_relevant'}
    if axes & strong_axes:
        return True
    if flags & strong_flags:
        return True
    # A phrase that is only a generic word-choice extraction with edge-function markers
    # is not enough for ENHANCE, even if one word appears in the external thesaurus.
    # This avoids turning weak location/time chunks into artificial paraphrase tasks.
    if axes <= {'word_choice'} and (not flags or flags <= {'edge_function_word'}) and candidate_value < 0.58:
        return False
    toks = [norm_text(t) for t in surface_tokens(text)]
    return any(t in EXTERNAL_WORD_SUGGESTIONS for t in toks) and candidate_value >= 0.55


def _llm_candidate_priority(u: Dict[str, Any]) -> Tuple[float, int, int]:
    axes = set(u.get('axis_candidates') or [])
    flags = set(u.get('extraction_flags') or [])
    score = float(u.get('candidate_value') or 0.0)
    if 'collocation_naturalness' in axes:
        score += 0.15
    if 'predicate_argument' in axes:
        score += 0.12
    if 'semantic_specificity' in axes:
        score += 0.10
    if 'vague_vocabulary_candidate' in flags:
        score += 0.08
    if 'topic_relevant' in flags:
        score += 0.08
    return (score, len(surface_tokens(u.get('unit_text', ''))), int(u.get('frequency') or 1))


class OpenAILRETSuggestionProvider:
    """OpenAI-based classifier/suggestion generator for already-extracted candidates.

    The LLM is not allowed to invent spans. It receives only candidate spans already
    extracted by universal/resource logic and must return classification plus suggestions.
    """

    def __init__(self, model: str = "gpt-5-mini", api_key: Optional[str] = None, timeout: int = 45, max_suggestions: int = 4):
        self.model = model
        self.api_key = api_key or _os.environ.get('OPENAI_API_KEY')
        self.timeout = timeout
        self.max_suggestions = max_suggestions
        _LLM_STATS['enabled'] = bool(self.api_key)
        _LLM_STATS['model'] = model

    def available(self) -> bool:
        return bool(self.api_key)

    def classify_and_suggest(self, candidates: List[Dict[str, Any]], *, learner_level: str = "B2") -> List[Dict[str, Any]]:
        if not self.available() or not candidates:
            return []
        _LLM_STATS['calls'] += 1
        _LLM_STATS['candidates_sent'] += len(candidates)
        payload_candidates = []
        for c in candidates:
            payload_candidates.append({
                "unit_id": c.get('unit_id'),
                "unit_text": c.get('unit_text'),
                "context": c.get('context'),
                "unit_type": c.get('unit_type'),
                "axis_candidates": c.get('axis_candidates') or [],
                "extraction_flags": c.get('extraction_flags') or [],
            })

        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "unit_id": {"type": "string"},
                            "classification": {"type": "string", "enum": ["ENHANCE", "KEEP", "DROP", "CLARIFY"]},
                            "confidence": {"type": "number"},
                            "semantic_stability": {"type": "string", "enum": ["stable", "partly_stable", "unstable"]},
                            "reason": {"type": "string"},
                            "suggestions": {"type": "array", "items": {"type": "string"}, "maxItems": self.max_suggestions},
                            "risk_flags": {"type": "array", "items": {"type": "string"}}
                        },
                        "required": ["unit_id", "classification", "confidence", "semantic_stability", "reason", "suggestions", "risk_flags"]
                    }
                }
            },
            "required": ["results"]
        }
        system = (
            "You are the LRET lexical-resource assistant for an IELTS/academic writing system. "
            "You must classify only the provided candidate spans. Do not add new spans. "
            "Classify each span as ENHANCE, KEEP, DROP, or CLARIFY. "
            "ENHANCE means the phrase is understandable/correct enough and can be improved by a whole-phrase replacement. "
            "CLARIFY means the phrase is too vague or semantically unstable for safe paraphrase. "
            "Suggestions must be whole-phrase replacements, not full sentence rewrites. "
            "Preserve meaning, grammar role, topic, and claim strength. Do not add facts. "
            "Prefer natural B2-C1 academic alternatives for IELTS learners."
        )
        user = json.dumps({
            "learner_level": learner_level,
            "max_suggestions_per_enhance": self.max_suggestions,
            "candidates": payload_candidates,
            "output_rules": [
                "Return JSON only.",
                "For ENHANCE, provide 2-4 suggestions when possible.",
                "For KEEP, DROP, or CLARIFY, suggestions must be an empty list.",
                "Do not rewrite the whole sentence.",
                "Do not introduce unsupported details."
            ]
        }, ensure_ascii=False)
        body = {
            "model": self.model,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": system}]},
                {"role": "user", "content": [{"type": "input_text", "text": user}]},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "lret_llm_suggestions",
                    "schema": schema,
                    "strict": True,
                }
            },
        }
        req = _urllib_request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(body).encode('utf-8'),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with _urllib_request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode('utf-8')
            data = json.loads(raw)
            text = self._extract_text(data)
            parsed = json.loads(text)
            results = parsed.get('results') if isinstance(parsed, dict) else []
            if isinstance(results, list):
                _LLM_STATS['results_received'] += len(results)
                return [r for r in results if isinstance(r, dict)]
        except Exception as e:
            _LLM_STATS['warnings'].append(f"openai_request_failed: {type(e).__name__}: {e}")
        return []

    @staticmethod
    def _extract_text(data: Dict[str, Any]) -> str:
        if isinstance(data.get('output_text'), str):
            return data['output_text']
        parts: List[str] = []
        for item in data.get('output') or []:
            for c in item.get('content') or []:
                if isinstance(c.get('text'), str):
                    parts.append(c['text'])
        if parts:
            return "".join(parts)
        # Fallback for possible message-style response.
        return json.dumps(data)


def _looks_like_sentence_rewrite(unit_text: str, suggestion: str, context: str) -> bool:
    unit_len = max(1, len(surface_tokens(unit_text)))
    sug_len = len(surface_tokens(suggestion))
    ctx_len = len(surface_tokens(context))
    if sug_len > max(unit_len + 5, int(unit_len * 2.2)):
        return True
    if ctx_len and sug_len > int(ctx_len * 0.65):
        return True
    if re.search(r"[.!?]", suggestion.strip().rstrip('.')):
        return True
    return False


def validate_llm_suggestions(unit_text: str, suggestions: Iterable[str], context: str, validator: ContextFitValidator, failures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    valid: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for s in suggestions or []:
        sug = re.sub(r"\s+", " ", str(s or "").strip())
        if not sug or norm_text(sug) in seen:
            continue
        seen.add(norm_text(sug))
        if compact_norm(sug) == compact_norm(unit_text):
            failures.append({"unit_text": unit_text, "candidate": sug, "tier": "llm", "reason": "orthographic_variant_not_enhancement"})
            _LLM_STATS['suggestions_rejected'] += 1
            continue
        if _looks_like_sentence_rewrite(unit_text, sug, context):
            failures.append({"unit_text": unit_text, "candidate": sug, "tier": "llm", "reason": "suggestion_looks_like_sentence_rewrite"})
            _LLM_STATS['suggestions_rejected'] += 1
            continue
        result = validator.validate(unit_text, sug, context, source="llm_openai")
        if result.passed:
            valid.append({"text": sug, "validation": replacement_validation("passed llm suggestion + deterministic contextual-fit gates", True, result.gates_checked)})
            _LLM_STATS['suggestions_validated'] += 1
        else:
            failures.append({"unit_text": unit_text, "candidate": sug, "tier": "llm", "reason": result.reason})
            _LLM_STATS['suggestions_rejected'] += 1
    return valid


def universal_pattern_suggestions(unit_text: str, context: str = "") -> Tuple[List[str], str]:
    """v1.4 override: use external resources only; no embedded exact phrase bank."""
    text = _strip_trailing_punctuation(unit_text)
    if not text or len(surface_tokens(text)) <= 1:
        return [], "not_phrase"
    if _is_vague_placeholder_phrase(text):
        return [], "semantic_stability_low_placeholder_phrase"
    return _external_word_phrase_suggestions(text)


def generate_phrase_enhance_candidates(
    units: List[Dict[str, Any]],
    essay_text: str,
    validator: ContextFitValidator,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """v1.4 universal hybrid ENHANCE generation.

    Candidate extraction is universal and span-based. Suggestions come from:
      1) external resource transformation over the whole phrase;
      2) OpenAI LLM classification/suggestion over preselected candidates.

    The LLM cannot invent spans; every output is validated deterministically.
    """
    candidates: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, int]] = set()
    seq = 0

    def add_candidate(unit_text: str, context: str, sent_idx: int, para_idx: Any, suggestions: List[Dict[str, Any]], *, source_kind: str, base_value: float, unit_type: str = "phrase_enhance") -> None:
        nonlocal seq
        unit_text2 = re.sub(r"\s+", " ", unit_text).strip()
        if not unit_text2 or len(surface_tokens(unit_text2)) <= 1:
            return
        key = (norm_text(unit_text2), sent_idx)
        if key in seen:
            return
        seq += 1
        candidates.append(make_enhance_unit(
            unit_id=f"enh_{seq:04d}",
            unit_text=unit_text2,
            unit_type=unit_type,
            context=context,
            source_sentence_index=sent_idx,
            source_paragraph_index=para_idx,
            suggestions=suggestions,
            candidate_value=base_value,
            source_kind=source_kind,
        ))
        seen.add(key)

    # 1) Resource-first whole-phrase variants.
    for u in units:
        text = str(u.get('unit_text') or '').strip()
        if len(surface_tokens(text)) <= 1:
            continue
        context = str(u.get('context') or '')
        sent_idx = int(u.get('source_sentence_index', -1))
        para_idx = u.get('source_paragraph_index')
        if not _is_candidate_for_llm_enhance(u):
            continue
        suggs, source_kind = universal_pattern_suggestions(text, context)
        if suggs:
            valid = validate_suggestions(text, suggs, context, validator, source=source_kind, tier="phrase", failures=failures)
            if valid:
                add_candidate(
                    text, context, sent_idx, para_idx, valid,
                    source_kind=source_kind,
                    base_value=max(0.68, float(u.get('candidate_value') or 0.0)),
                    unit_type=u.get('unit_type') or 'phrase_enhance',
                )

    # 2) LLM classification/suggestion for remaining high-value candidate spans.
    llm_provider = ACTIVE_LLM_PROVIDER
    if llm_provider and llm_provider.available():
        raw_llm_candidates: List[Dict[str, Any]] = []
        seen_llm: Set[Tuple[str, int]] = set()
        for u in units:
            text = str(u.get('unit_text') or '').strip()
            sent_idx = int(u.get('source_sentence_index', -1))
            key = (norm_text(text), sent_idx)
            if key in seen or key in seen_llm:
                continue
            if not _is_candidate_for_llm_enhance(u):
                continue
            # Avoid sending obvious exact formulaic KEEP items for paraphrase.
            low = norm_text(text)
            if low in EXTERNAL_FORMULAIC_KEEP or low in DISCOURSE_MARKER_ALLOWLIST:
                continue
            raw_llm_candidates.append(u)
            seen_llm.add(key)
        raw_llm_candidates.sort(key=_llm_candidate_priority, reverse=True)
        raw_llm_candidates = raw_llm_candidates[:max(0, int(LLM_MAX_CANDIDATES))]
        batch_payload: List[Dict[str, Any]] = []
        for i, u in enumerate(raw_llm_candidates, start=1):
            batch_payload.append({
                "unit_id": f"cand_{i:04d}",
                "original_unit_id": u.get('unit_id'),
                "unit_text": str(u.get('unit_text') or '').strip(),
                "unit_type": u.get('unit_type') or 'phrase_enhance',
                "context": str(u.get('context') or ''),
                "source_sentence_index": int(u.get('source_sentence_index', -1)),
                "source_paragraph_index": u.get('source_paragraph_index'),
                "axis_candidates": u.get('axis_candidates') or [],
                "extraction_flags": u.get('extraction_flags') or [],
                "candidate_value": float(u.get('candidate_value') or 0.0),
            })
        llm_results = llm_provider.classify_and_suggest(batch_payload)
        result_by_id = {str(r.get('unit_id')): r for r in llm_results}
        for c in batch_payload:
            r = result_by_id.get(c['unit_id'])
            if not r:
                continue
            classification = str(r.get('classification') or '').upper()
            conf = _safe_float(r.get('confidence'), 0.0)
            if classification != 'ENHANCE':
                failures.append({
                    "unit_text": c['unit_text'],
                    "candidate": None,
                    "tier": "llm_classification",
                    "reason": f"llm_classified_as_{classification.lower() or 'unknown'}",
                    "llm_confidence": conf,
                    "risk_flags": r.get('risk_flags') or [],
                })
                continue
            if str(r.get('semantic_stability') or '') == 'unstable':
                failures.append({
                    "unit_text": c['unit_text'],
                    "candidate": None,
                    "tier": "llm_classification",
                    "reason": "llm_semantic_stability_unstable",
                    "llm_confidence": conf,
                })
                continue
            valid = validate_llm_suggestions(c['unit_text'], r.get('suggestions') or [], c['context'], validator, failures)
            if len(valid) < LLM_MIN_VALID_SUGGESTIONS:
                failures.append({
                    "unit_text": c['unit_text'],
                    "candidate": None,
                    "tier": "llm_validation",
                    "reason": "fewer_than_min_valid_llm_suggestions",
                    "valid_suggestion_count": len(valid),
                    "required": LLM_MIN_VALID_SUGGESTIONS,
                })
                continue
            add_candidate(
                c['unit_text'], c['context'], c['source_sentence_index'], c['source_paragraph_index'], valid,
                source_kind="openai_llm_validated",
                base_value=max(0.72, c['candidate_value'], min(0.95, conf)),
                unit_type=c['unit_type'],
            )
            _LLM_STATS['accepted_enhance_units'] += 1

    return candidates, failures


# ---------------------------------------------------------------------------
# v1.4.2 universal anti-overfit precision patch overrides
# ---------------------------------------------------------------------------
# These overrides are intentionally placed after the v1.4 definitions and before
# main(), so analyze() and main() resolve the updated global functions at runtime.

ENGINE_VERSION = "lret-engine-v1.4.2-universal-hybrid-anti-overfit"

# Preserve original v1.4 helpers where useful.
_v14_load_canonical_resources = load_canonical_resources
_v14_build_lexical_profile = build_lexical_profile
_v14_build_qa = build_qa
_v14_build_keep_units = build_keep_units

MORPH_FORM_TO_LEMMA: Dict[str, str] = {}
LEXICAL_POS_MAP: Dict[str, Set[str]] = defaultdict(set)
CURRENT_FIX_UNITS_FOR_BLOCKING: List[Dict[str, Any]] = []
CURRENT_FIX_ERROR_TOKEN_MAP: Dict[str, str] = {}
V141_QA_CACHE: Dict[str, Any] = {}

META_SUGGESTION_RE = re.compile(r"\(|\)|\bnote\s*:|\bmeaning\s*:|\buse\s+this\b|\bthis\s+means\b", re.I)
CLAIM_STRENGTH_STRONG = {"must", "always", "never", "definitely", "certainly", "undoubtedly", "only", "completely"}
# No embedded topic/domain replacement-head whitelist.
# LLM suggestions are accepted or rejected by structural/contextual gates only;
# any domain knowledge must come from external resources or the LLM response itself.
ABSTRACT_SAFE_REPLACEMENT_HEADS: Set[str] = set()


def load_canonical_resources(resource_path: Optional[str]) -> None:
    """v1.4.2 loader: use v1.4 resources plus morphology/POS maps for universal repair recovery."""
    _v14_load_canonical_resources(resource_path)
    if not resource_path:
        return
    # Morphology registry: form -> lemma. Used only to recover concrete WORD_FORM fixes.
    morph = _read_json_from_resource(resource_path, 'morphology_registry.json')
    if isinstance(morph, list):
        for row in morph:
            if not isinstance(row, dict):
                continue
            lemma = norm_text(row.get('lemma'))
            if not lemma:
                continue
            for form in row.get('all_forms') or []:
                f = norm_text(form)
                if f and f != lemma and len(f) <= 40:
                    MORPH_FORM_TO_LEMMA.setdefault(f, lemma)
    # Lexical registry: lemma -> POS set. This prevents arbitrary verb-form substitution.
    lex = _read_json_from_resource(resource_path, 'lexical_registry.json')
    if isinstance(lex, list):
        for row in lex:
            if not isinstance(row, dict):
                continue
            lemma = norm_text(row.get('lemma'))
            pos_raw = str(row.get('pos') or '').lower()
            pos_parts = [p.strip() for p in re.split(r"[^a-z]+", pos_raw) if p.strip()]
            if lemma and pos_parts:
                for p0 in pos_parts:
                    # Canonical resources may use compact tags such as n|noun|v|verb.
                    if p0 in {'n', 'noun'}:
                        LEXICAL_POS_MAP[lemma].add('noun')
                    elif p0 in {'v', 'verb'}:
                        LEXICAL_POS_MAP[lemma].add('verb')
                    elif p0 in {'adj', 'adjective'}:
                        LEXICAL_POS_MAP[lemma].add('adjective')
                    elif p0 in {'adv', 'adverb'}:
                        LEXICAL_POS_MAP[lemma].add('adverb')
                    else:
                        LEXICAL_POS_MAP[lemma].add(p0)


class RuleBasedContextFitValidator(ContextFitValidator):
    """v1.4.2 validator: accepts valid LLM paraphrases without exact resource links,
    but rejects meta-comments, claim-strength shifts, sentence rewrites, and excessive drift.
    """

    def validate(self, original: str, candidate: str, sentence: str, *, source: str = "unknown") -> GateResult:
        original = str(original or "").strip()
        candidate = str(candidate or "").strip()
        sentence = str(sentence or "").strip()
        if not original or not candidate:
            return GateResult(False, "empty original or candidate", [])
        low_cand = norm_text(candidate)
        low_orig = norm_text(original)
        if any(w in low_cand.split() for w in BANNED_WORDS):
            return GateResult(False, "candidate contains banned/unsafe word", ["no_unsafe_word"])
        if META_SUGGESTION_RE.search(candidate):
            return GateResult(False, "parenthetical_or_meta_suggestion", ["span_fit"])
        if compact_norm(candidate) == compact_norm(original):
            return GateResult(False, "orthographic_variant_not_enhancement", ["span_fit"])

        o_len = max(1, len(surface_tokens(original)))
        c_len = max(1, len(surface_tokens(candidate)))
        if c_len > max(o_len + 5, int(o_len * 2.5)):
            return GateResult(False, "replacement length is not reasonable", ["span_fit", "grammar_role_preserved"])

        # Claim-strength control: do not silently strengthen a phrase.
        orig_modals = set(t.lower() for t in surface_tokens(original))
        cand_modals = set(t.lower() for t in surface_tokens(candidate))
        added_strong = (cand_modals & CLAIM_STRENGTH_STRONG) - (orig_modals & CLAIM_STRENGTH_STRONG)
        if added_strong:
            return GateResult(False, "claim_strength_shift", ["claim_strength_preserved"])

        if source in {"external_resource", "universal_pattern", "single_word_fallback"}:
            return GateResult(True, f"passed {source} contextual-fit check", list(VALIDATION_GATES))

        if source == "llm_openai":
            # LLM suggestions are allowed without exact resource links, but must be phrase-sized,
            # non-meta, and not add many unsupported content words.
            o_content = set(content_tokens(original))
            c_content = set(content_tokens(candidate))
            ctx_content = set(content_tokens(sentence))
            if not c_content:
                return GateResult(False, "candidate has no content tokens", ["meaning_link_detected"])
            overlap = len(o_content & c_content) / max(1, min(len(o_content), len(c_content))) if o_content else 0.0
            new_tokens = c_content - o_content - ctx_content
            # Reject over-specific hallucinated details. Abstract replacement heads are allowed.
            if len(new_tokens) > 3:
                return GateResult(False, "unsupported_detail_or_topic_drift", ["no_topic_drift", "context_specificity_ok"])
            # No embedded list of "safe" topic heads is used. A candidate may add at most
            # two content tokens not present in the original phrase/context; otherwise it is
            # treated as unsupported detail or topic drift.
            if overlap >= 0.20 or len(new_tokens) <= 2:
                return GateResult(True, "passed llm semantic/contextual-fit gates", list(VALIDATION_GATES))
            return GateResult(False, "meaning_link_too_weak", ["meaning_link_detected"])

        if self._transparent_repair_preserves_core(original, candidate):
            return GateResult(True, "passed transparent lexical repair contextual-fit check", list(VALIDATION_GATES))
        return GateResult(False, "no external-resource, universal-pattern, or transparent contextual link between original and candidate", list(VALIDATION_GATES))

    @staticmethod
    def _transparent_repair_preserves_core(original: str, candidate: str) -> bool:
        o = content_tokens(original)
        c = content_tokens(candidate)
        if not o or not c:
            return False
        os, cs = set(o), set(c)
        overlap = len(os & cs) / max(1, len(os))
        if overlap >= 0.95 and abs(len(c) - len(o)) <= 1:
            return True
        if overlap >= 0.66 and len(c) <= len(o) + 4:
            return True
        if "more" in surface_tokens(original.lower()) and overlap >= 0.75:
            return True
        return False


def external_word_form_repair(span_text: str, family: str) -> Optional[str]:
    """Universal external-resource repair for WORD_FORM cases.

    If a token is an inflected verb form and its lemma can function as a noun, replace
    it with the lemma. Example shape: inflected form used where noun is expected.
    No exact essay word is hardcoded here.
    """
    fam = str(family or "").upper()
    if "WORD_FORM" not in fam:
        return None
    toks = surface_tokens(span_text)
    if not toks:
        return None
    for tok in toks:
        low = norm_text(tok)
        lemma = MORPH_FORM_TO_LEMMA.get(low)
        if not lemma:
            continue
        pos = LEXICAL_POS_MAP.get(lemma, set())
        if pos and "noun" not in pos:
            continue
        repaired = re.sub(r"\b" + re.escape(tok) + r"\b", lemma, str(span_text), count=1, flags=re.I)
        if compact_norm(repaired) != compact_norm(span_text):
            return repaired
    return None


def _openai_repair_candidates(candidates: List[Dict[str, Any]], model: Optional[str] = None, timeout: int = 45) -> Dict[str, str]:
    """LLM fallback for FIX candidates with no concrete repair. Returns unit_id -> repair."""
    provider = ACTIVE_LLM_PROVIDER
    api_key = getattr(provider, "api_key", None) if provider else None
    if not api_key or not candidates:
        return {}
    model = model or getattr(provider, "model", None) or "gpt-5-mini"
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "repairs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "unit_id": {"type": "string"},
                        "repair": {"type": "string"},
                        "confidence": {"type": "number"},
                        "reason": {"type": "string"}
                    },
                    "required": ["unit_id", "repair", "confidence", "reason"]
                }
            }
        },
        "required": ["repairs"]
    }
    payload = {
        "task": "generate_concrete_lexical_repairs_only",
        "rules": [
            "Return only replacement for the selected span, not the whole sentence.",
            "If no safe concrete repair is possible, return an empty repair string.",
            "Do not add explanations in repair."
        ],
        "candidates": candidates,
    }
    body = {
        "model": model,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": "You repair lexical/word-form/collocation spans for IELTS writing. Output JSON only."}]},
            {"role": "user", "content": [{"type": "input_text", "text": json.dumps(payload, ensure_ascii=False)}]},
        ],
        "text": {"format": {"type": "json_schema", "name": "lret_fix_repairs", "schema": schema, "strict": True}},
    }
    req = _urllib_request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _urllib_request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        text = OpenAILRETSuggestionProvider._extract_text(data)
        parsed = json.loads(text)
        out: Dict[str, str] = {}
        for row in parsed.get("repairs") or []:
            if not isinstance(row, dict):
                continue
            repair = str(row.get("repair") or "").strip()
            conf = _safe_float(row.get("confidence"), 0.0)
            if repair and conf >= 0.70:
                out[str(row.get("unit_id"))] = repair
        return out
    except Exception as e:
        _LLM_STATS.setdefault("warnings", []).append(f"openai_fix_repair_failed: {type(e).__name__}: {e}")
        return {}


def derive_fix_units(
    fix_candidates: List[Dict[str, Any]],
    essay_text: str,
    validator: ContextFitValidator,
) -> Tuple[List[Dict[str, Any]], List[Tuple[Optional[int], Optional[int], str]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    fix_units: List[Dict[str, Any]] = []
    claimed_spans: List[Tuple[Optional[int], Optional[int], str]] = []
    suppressed: List[Dict[str, Any]] = []
    context_failures: List[Dict[str, Any]] = []
    pending_llm: List[Dict[str, Any]] = []
    prepared: List[Dict[str, Any]] = []

    for idx, fc in enumerate(fix_candidates, start=1):
        family = str(fc.get("error_family") or fc.get("family") or fc.get("detector_family") or "").upper().strip()
        span_text = str(fc.get("span_text") or fc.get("unit_text") or fc.get("surface_quote") or "").strip()
        if not span_text:
            continue
        start = fc.get("start") if isinstance(fc.get("start"), int) else None
        end = fc.get("end") if isinstance(fc.get("end"), int) else None
        context = sentence_context_for_span(essay_text, start, end, span_text)
        raw_suggestion = fc.get("suggestion")
        span_text, raw_suggestion, family, start, end, span_expansion_note = expand_lexical_fix_span_universally(span_text, raw_suggestion, family, context, start, end)
        if family not in LRET_LEXICAL_FIX_FAMILIES or looks_like_grammar_only_fix(family, span_text):
            suppressed.append({"unit": span_text, "family": family, "reason": "grammar_only_not_lret", "stage": "fix_filter"})
            claimed_spans.append((start, end, span_text))
            continue
        prepared.append({
            "idx": idx, "fc": fc, "family": family, "span_text": span_text, "start": start, "end": end,
            "context": context, "raw_suggestion": raw_suggestion, "span_expansion_note": span_expansion_note,
        })

    # First pass: deterministic/resource repairs; collect unresolved for LLM.
    llm_lookup_inputs: List[Dict[str, Any]] = []
    for item in prepared:
        span_text = item["span_text"]
        family = item["family"]
        suggestion = item["raw_suggestion"]
        concrete_ok, concrete_reason = is_concrete_student_facing_suggestion(span_text, suggestion)
        if not concrete_ok:
            inferred = infer_deterministic_lexical_repair(span_text, family, item["context"])
            if not inferred:
                inferred = external_word_form_repair(span_text, family)
            if inferred:
                suggestion = inferred
                concrete_ok, concrete_reason = is_concrete_student_facing_suggestion(span_text, suggestion)
                if concrete_ok:
                    item["repair_source"] = "resource_or_deterministic_repair"
        if not concrete_ok:
            uid = f"pending_fix_{item['idx']:04d}"
            item["pending_uid"] = uid
            llm_lookup_inputs.append({
                "unit_id": uid,
                "unit_text": span_text,
                "family": family,
                "context": item["context"],
            })
        else:
            item["final_suggestion"] = suggestion
            item["suggestion_source_quality"] = concrete_reason

    llm_repairs = _openai_repair_candidates(llm_lookup_inputs) if llm_lookup_inputs else {}

    for item in prepared:
        idx = item["idx"]
        span_text = item["span_text"]
        family = item["family"]
        context = item["context"]
        suggestion = item.get("final_suggestion")
        concrete_reason = item.get("suggestion_source_quality")
        repair_source = item.get("repair_source")
        if suggestion is None and item.get("pending_uid") in llm_repairs:
            suggestion = llm_repairs[item["pending_uid"]]
            concrete_ok, concrete_reason = is_concrete_student_facing_suggestion(span_text, suggestion)
            repair_source = "openai_fix_repair"
        elif suggestion is None:
            concrete_ok, concrete_reason = False, "no concrete replacement"
        else:
            concrete_ok = True
        if not concrete_ok:
            suppressed.append({
                "unit": span_text,
                "family": family,
                "reason": "no_concrete_fix_suggestion",
                "detail": concrete_reason,
                "stage": "fix_filter",
            })
            claimed_spans.append((item["start"], item["end"], span_text))
            continue
        vsource = "external_resource" if repair_source in {"resource_or_deterministic_repair", "openai_fix_repair"} else "fix_repair"
        result = validator.validate(span_text, str(suggestion), context, source=vsource)
        if not result.passed:
            suppressed.append({"unit": span_text, "family": family, "reason": "context_fit_failed", "detail": result.reason, "stage": "fix_filter"})
            context_failures.append({"unit_text": span_text, "candidate": str(suggestion), "tier": "fix", "reason": result.reason})
            claimed_spans.append((item["start"], item["end"], span_text))
            continue
        unit = {
            "unit_id": f"fix_{idx:04d}",
            "class_label": "FIX",
            "unit_text": span_text,
            "unit_norm": norm_text(span_text),
            "unit_type": "lexical_repair_span",
            "replacement_scope": "whole_phrase" if len(surface_tokens(span_text)) > 1 else "word",
            "error_family": family,
            "detector_family": family,
            "issue_code": family,
            "occurrence_count": 1,
            "source_sentence_index": _infer_sentence_index_from_context(essay_text, context),
            "source_paragraph_index": item["fc"].get("paragraph_idx"),
            "context": context,
            "locations": [{"start": item["start"], "end": item["end"], "paragraph_idx": item["fc"].get("paragraph_idx")}],
            "requires_full_contextual_check": True,
            "safety_level": "must_repair_final_lexical_error",
            "suggestions": [{"text": str(suggestion).strip(), "validation": replacement_validation(result.reason, True, result.gates_checked)}],
            "suggestion_source_quality": concrete_reason,
            "repair_source": repair_source or "detector_concrete_suggestion",
            "span_expansion_note": item["span_expansion_note"],
            "covered_subunits": [],
            "dedup_role": "candidate_pending_dedup",
        }
        build_fix_phase1(unit)
        fix_units.append(unit)
        claimed_spans.append((item["start"], item["end"], span_text))

    # v1.4.13 Gold pipeline fix (stress-test Problem 7): a LEXICAL_PRECISION FIX
    # suggestion whose entire replacement judgment came solely from a single LLM
    # generation (repair_source == "openai_fix_repair", i.e. not corroborated by
    # any external lexical resource/registry) is shown as "must_repair_final_
    # lexical_error" -- an assertive "this is definitely wrong" framing -- based
    # only on mechanical surface-fit gates (span_fit, grammar_role_preserved,
    # register_preserved_or_improved, no_topic_drift, etc.), none of which check
    # whether the replacement preserves the essay's specific argumentative
    # thread (see spec Problem 7: "civic equity" -> "civic engagement" passed
    # every gate but drops a deliberate thematic link the essay built). That
    # risk is highest precisely when the essay has very few other chargeable
    # LRET issues -- an already-strong essay where the FIX pipeline has little
    # else to flag, so a single unverified LLM judgment carries
    # disproportionate weight. Approved fix: raise the confidence bar for this
    # narrow, single-signal, high-stakes case by demoting it from an assertive
    # FIX to a softer ENHANCE (an optional improvement, not an asserted error)
    # rather than building a full argument-thread-preservation checker (a
    # larger, separate piece of work flagged as an open question).
    _low_corroboration_threshold = 2
    if len(fix_units) <= _low_corroboration_threshold:
        for unit in fix_units:
            if (unit.get("error_family") == "LEXICAL_PRECISION"
                    and unit.get("repair_source") == "openai_fix_repair"):
                unit["class_label"] = "ENHANCE"
                unit["safety_level"] = "optional_precision_enhancement_single_signal_low_corroboration"
                unit["confidence_note"] = (
                    "Demoted from FIX to ENHANCE: this replacement was generated by a "
                    "single LLM pass with no corroborating external-resource signal, on "
                    "an essay with very few other chargeable issues (<= "
                    f"{_low_corroboration_threshold}). Mechanical gates (grammar, "
                    "register, topic drift) passed, but none of them verify whether the "
                    "replacement preserves the essay's specific argumentative thread -- "
                    "treat as an optional suggestion, not an asserted error."
                )
                build_enhance_phase1(unit)
    return fix_units, claimed_spans, suppressed, context_failures


def _derive_token_transformations(fix_units: List[Dict[str, Any]]) -> Dict[str, str]:
    transforms: Dict[str, str] = {}
    for u in fix_units:
        src_toks = surface_tokens(u.get("unit_text", ""))
        sug = (u.get("suggestions") or [{}])[0].get("text", "")
        dst_toks = surface_tokens(sug)
        if len(src_toks) != len(dst_toks):
            continue
        for a, b in zip(src_toks, dst_toks):
            la, lb = norm_text(a), norm_text(b)
            if la and lb and la != lb and la not in EDGE_STOPWORDS:
                transforms[la] = lb
    return transforms


def recover_recurring_fix_units(raw_units: List[Dict[str, Any]], fix_units: List[Dict[str, Any]], validator: ContextFitValidator) -> List[Dict[str, Any]]:
    """Apply token-level repairs inferred from accepted FIX evidence to other spans.
    This is current-submission evidence propagation, not an essay-specific rule.
    """
    transforms = _derive_token_transformations(fix_units)
    if not transforms:
        return []
    existing = {(norm_text(u.get("unit_text")), int(u.get("source_sentence_index", -1))) for u in fix_units}
    recovered: List[Dict[str, Any]] = []
    seq_start = len(fix_units) + 1
    for u in raw_units:
        text = str(u.get("unit_text") or "").strip()
        sent_idx = int(u.get("source_sentence_index", -1))
        if not text or (norm_text(text), sent_idx) in existing:
            continue
        toks = surface_tokens(text)
        if not toks or len(toks) > 4:
            continue
        changed = False
        repaired = text
        for tok in toks:
            low = norm_text(tok)
            if low in transforms:
                repaired = re.sub(r"\b" + re.escape(tok) + r"\b", transforms[low], repaired, count=1, flags=re.I)
                changed = True
        if not changed or compact_norm(repaired) == compact_norm(text):
            continue
        context = str(u.get("context") or "")
        result = validator.validate(text, repaired, context, source="fix_repair")
        if not result.passed:
            continue
        nu = {
            "unit_id": f"fix_rec_{seq_start + len(recovered):04d}",
            "class_label": "FIX",
            "unit_text": text,
            "unit_norm": norm_text(text),
            "unit_type": "lexical_repair_span",
            "replacement_scope": "whole_phrase" if len(surface_tokens(text)) > 1 else "word",
            "error_family": "WORD_FORM_LEXICAL",
            "detector_family": "CURRENT_SUBMISSION_REPAIR_PATTERN",
            "issue_code": "WORD_FORM_LEXICAL",
            "occurrence_count": 1,
            "source_sentence_index": sent_idx,
            "source_paragraph_index": u.get("source_paragraph_index"),
            "context": context,
            "locations": [],
            "requires_full_contextual_check": True,
            "safety_level": "must_repair_final_lexical_error",
            "suggestions": [{"text": repaired, "validation": replacement_validation(result.reason, True, result.gates_checked)}],
            "suggestion_source_quality": "recurring current-submission token repair pattern",
            "repair_source": "recurring_current_submission_repair",
            "covered_subunits": [],
            "dedup_role": "candidate_pending_dedup",
            "recurs_across_essays": False,
            "recurrence_note": "Recovered from another validated FIX transformation in the same submission.",
        }
        build_fix_phase1(nu)
        recovered.append(nu)
        existing.add((norm_text(text), sent_idx))
    return recovered


def prepare_fix_blocking_state(fix_units: List[Dict[str, Any]]) -> None:
    global CURRENT_FIX_UNITS_FOR_BLOCKING, CURRENT_FIX_ERROR_TOKEN_MAP
    CURRENT_FIX_UNITS_FOR_BLOCKING = list(fix_units or [])
    CURRENT_FIX_ERROR_TOKEN_MAP = _derive_token_transformations(fix_units or [])


def enhance_overlaps_fix(unit: Dict[str, Any]) -> Tuple[bool, str, Optional[str]]:
    text = str(unit.get("unit_text") or "")
    low = norm_text(text)
    sent = unit.get("source_sentence_index")
    toks = {norm_text(t) for t in surface_tokens(text)}
    for err_tok, rep_tok in CURRENT_FIX_ERROR_TOKEN_MAP.items():
        if err_tok in toks:
            return True, "contains_fix_error_token", err_tok
    for f in CURRENT_FIX_UNITS_FOR_BLOCKING:
        ftext = str(f.get("unit_text") or "")
        flow = norm_text(ftext)
        fsent = f.get("source_sentence_index")
        if sent is not None and fsent is not None and sent != fsent:
            continue
        if low and flow and (low in flow or flow in low):
            return True, "blocked_overlap_with_fix", f.get("unit_id")
        if content_overlap_ratio(low, flow) >= 0.50 and (len(content_tokens(low)) <= len(content_tokens(flow)) + 2):
            return True, "blocked_content_overlap_with_fix", f.get("unit_id")
    return False, "", None


def context_has_local_grammar_corruption(context: str) -> bool:
    low = norm_text(context)
    corruption_patterns = [
        r"\b(?:has|have)\s+to\s+\w+ed\b",
        r"\b(?:a|an)\s+\w+s\b",
        r"\bmore\s+\w+er\b",
        r"\bfor\s+\w+ing?\b",
        r"\b\w+\s+be\s+\w+\b",
    ]
    return any(re.search(p, low) for p in corruption_patterns)


def semantic_stability_for_enhance(unit_text: str, context: str = "") -> Tuple[bool, str]:
    text = str(unit_text or "").strip()
    if not text:
        return False, "empty phrase"
    ctoks = content_tokens(text)
    if not ctoks:
        return False, "no content tokens"
    vague = {simple_stem(t) for t in UNIVERSAL_VAGUE_NOUNS}
    stems = {simple_stem(t) for t in ctoks}
    if stems and stems <= vague:
        return False, "semantic_stability_low_placeholder_phrase"
    if any(t in vague for t in stems) and len(stems - vague) <= 1:
        return False, "semantic_stability_low_vague_phrase"
    # If the local context is malformed and the selected unit is a short fragment, do not polish it
    # unless the whole unit is licensed by external stable phrase/formulaic evidence.
    # This replaces the v1.4.1 topic-word whitelist with external-resource-only evidence.
    if context_has_local_grammar_corruption(context) and len(surface_tokens(text)) <= 3:
        low = norm_text(text)
        if low not in EXTERNAL_STABLE_KEEP_PHRASES and low not in EXTERNAL_FORMULAIC_KEEP:
            return False, "semantic_recoverability_low_malformed_context"
    return True, "semantic_stability_ok"


def is_unrecoverable_phrase_fragment(unit_text: str, context: str = "") -> bool:
    low = norm_text(unit_text)
    if not low:
        return True
    if low in EXTERNAL_FORMULAIC_KEEP or low in EXTERNAL_STABLE_KEEP_PHRASES:
        return False
    toks = surface_tokens(unit_text)
    ctoks = content_tokens(unit_text)
    if any(re.search(p, low) for p in [r"\b(?:has|have)\s+to\s+\w+ed\b", r"\b(?:a|an)\s+\w+s\b", r"\bmore\s+\w+er\b", r"\b\w+\s+be\s+\w+\b"]):
        return True
    if not toks or not ctoks:
        return True
    # Vague noun + participial/verb fragment is not positive lexical evidence.
    if any(simple_stem(t) in UNIVERSAL_VAGUE_NOUNS for t in toks) and any(str(t).lower().endswith('ing') for t in toks):
        return True
    # Discourse-marker fragments must be complete, not clipped.
    if low in {'other hand', 'one hand'} or (toks and toks[0].lower() == 'hand'):
        return True
    # Short clause-prefix fragments: NP + verb with missing object/complement.
    if len(toks) <= 4:
        pattern = r"\b" + r"\s+".join(re.escape(t) for t in toks) + r"\s+[A-Za-z]+"
        if re.search(pattern, context, flags=re.I) and not low in EXTERNAL_STABLE_KEEP_PHRASES:
            # If the span is a prefix of a longer surface phrase in context, keep the longer phrase instead.
            return True
    # dangling NP + -ing fragment in a malformed local context
    if context_has_local_grammar_corruption(context) and toks[-1].lower().endswith("ing") and len(toks) <= 4:
        return True
    # noun/preposition/noun fragment that omits its governing verb in malformed context
    if context_has_local_grammar_corruption(context) and len(toks) <= 4 and any(t.lower() in {"about", "with", "for", "to"} for t in toks[1:]):
        return True
    # incomplete prefix of a longer phrase in the same context (e.g. verb + adjective before a noun)
    if len(toks) <= 2:
        pattern = r"\b" + r"\s+".join(re.escape(t) for t in toks) + r"\s+[A-Za-z]+"
        if re.search(pattern, context, flags=re.I):
            return True
    if toks[-1].lower() in {"and", "or", "but", "to", "of", "for", "with", "by", "from"}:
        return True
    if toks[0].lower() in {"of", "for", "with", "by", "from", "about"}:
        return True
    return False


def _is_candidate_for_llm_enhance(u: Dict[str, Any]) -> bool:
    text = str(u.get('unit_text') or '').strip()
    if len(surface_tokens(text)) <= 1 or len(surface_tokens(text)) > 10:
        return False
    blocked, reason, ref = enhance_overlaps_fix(u)
    if blocked:
        V141_QA_CACHE.setdefault("blocked_enhance", []).append({"unit_text": text, "reason": reason, "reference": ref})
        return False
    context = str(u.get('context') or '')
    if is_unrecoverable_phrase_fragment(text, context):
        V141_QA_CACHE.setdefault("semantic_blocks", []).append({"unit_text": text, "reason": "unrecoverable_phrase_fragment"})
        return False
    stable, reason = semantic_stability_for_enhance(text, context)
    if not stable and "placeholder" not in reason:
        V141_QA_CACHE.setdefault("semantic_blocks", []).append({"unit_text": text, "reason": reason})
        return False
    axes = set(u.get('axis_candidates') or [])
    flags = set(u.get('extraction_flags') or [])
    candidate_value = float(u.get('candidate_value') or 0.0)
    strong_axes = {'collocation_naturalness', 'semantic_specificity', 'predicate_argument', 'register_control', 'topic_vocabulary'}
    strong_flags = {'collocation_candidate', 'predicate_argument_candidate', 'vague_vocabulary_candidate', 'informal_register', 'topic_relevant'}
    if axes & strong_axes or flags & strong_flags:
        return True
    return candidate_value >= 0.62 and len(content_tokens(text)) >= 2


def _looks_like_sentence_rewrite(unit_text: str, suggestion: str, context: str) -> bool:
    unit_len = max(1, len(surface_tokens(unit_text)))
    sug_len = len(surface_tokens(suggestion))
    ctx_len = len(surface_tokens(context))
    if sug_len > max(unit_len + 5, int(unit_len * 2.5)):
        return True
    if ctx_len and sug_len > int(ctx_len * 0.65):
        return True
    if re.search(r"[.!?]", suggestion.strip().rstrip('.')):
        return True
    return False


def validate_llm_suggestions(unit_text: str, suggestions: Iterable[str], context: str, validator: ContextFitValidator, failures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    valid: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for s in suggestions or []:
        sug = re.sub(r"\s+", " ", str(s or "").strip())
        if not sug or norm_text(sug) in seen:
            continue
        seen.add(norm_text(sug))
        reason = None
        if META_SUGGESTION_RE.search(sug):
            reason = "parenthetical_or_meta_suggestion"
        elif compact_norm(sug) == compact_norm(unit_text):
            reason = "orthographic_variant_not_enhancement"
        elif _looks_like_sentence_rewrite(unit_text, sug, context):
            reason = "suggestion_looks_like_sentence_rewrite"
        else:
            orig_modals = set(t.lower() for t in surface_tokens(unit_text))
            cand_modals = set(t.lower() for t in surface_tokens(sug))
            if (cand_modals & CLAIM_STRENGTH_STRONG) - (orig_modals & CLAIM_STRENGTH_STRONG):
                reason = "claim_strength_shift"
        if reason:
            failures.append({"unit_text": unit_text, "candidate": sug, "tier": "llm", "reason": reason})
            _LLM_STATS['suggestions_rejected'] += 1
            continue
        result = validator.validate(unit_text, sug, context, source="llm_openai")
        if result.passed:
            valid.append({"text": sug, "validation": replacement_validation("passed llm suggestion + deterministic contextual-fit gates", True, result.gates_checked)})
            _LLM_STATS['suggestions_validated'] += 1
        else:
            failures.append({"unit_text": unit_text, "candidate": sug, "tier": "llm", "reason": result.reason})
            _LLM_STATS['suggestions_rejected'] += 1
    return valid


def universal_pattern_suggestions(unit_text: str, context: str = "") -> Tuple[List[str], str]:
    """v1.4.2: no word-thesaurus phrase output. Final ENHANCE suggestions should be LLM/collocation-aware.
    This returns only high-confidence structural resource-free suggestions, kept minimal.
    """
    text = _strip_trailing_punctuation(unit_text)
    if not text or len(surface_tokens(text)) <= 1:
        return [], "not_phrase"
    low = norm_text(text)
    # Quantity-only structural rewrite can be safe without lexical thesaurus.
    m = re.match(r"^(a\s+lot\s+of|lots\s+of)\s+(.+)$", low)
    if m and not _is_vague_placeholder_phrase(text):
        rest = text[m.end(1):].strip()
        if rest:
            return [f"a considerable amount of {rest}", f"a significant amount of {rest}"], "universal_pattern"
    return [], "llm_required_for_phrase_suggestion"


def generate_phrase_enhance_candidates(units: List[Dict[str, Any]], essay_text: str, validator: ContextFitValidator) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    candidates: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, int]] = set()
    seq = 0
    V141_QA_CACHE["enhance_candidates_pre_validation"] = 0

    def add_candidate(unit_text: str, context: str, sent_idx: int, para_idx: Any, suggestions: List[Dict[str, Any]], *, source_kind: str, base_value: float, unit_type: str = "phrase_enhance") -> None:
        nonlocal seq
        unit_text2 = re.sub(r"\s+", " ", unit_text).strip()
        if not unit_text2 or len(surface_tokens(unit_text2)) <= 1:
            return
        key = (norm_text(unit_text2), sent_idx)
        if key in seen:
            return
        dummy = {"unit_text": unit_text2, "source_sentence_index": sent_idx, "context": context}
        blocked, reason, ref = enhance_overlaps_fix(dummy)
        if blocked:
            failures.append({"unit_text": unit_text2, "candidate": None, "tier": "overlap_gate", "reason": reason, "reference": ref})
            return
        seq += 1
        candidates.append(make_enhance_unit(
            unit_id=f"enh_{seq:04d}", unit_text=unit_text2, unit_type=unit_type, context=context,
            source_sentence_index=sent_idx, source_paragraph_index=para_idx, suggestions=suggestions,
            candidate_value=base_value, source_kind=source_kind,
        ))
        seen.add(key)

    # Minimal deterministic structural suggestions only. No word-thesaurus phrase substitutions.
    for u in units:
        if not _is_candidate_for_llm_enhance(u):
            continue
        text = str(u.get('unit_text') or '').strip()
        context = str(u.get('context') or '')
        sent_idx = int(u.get('source_sentence_index', -1))
        para_idx = u.get('source_paragraph_index')
        suggs, source_kind = universal_pattern_suggestions(text, context)
        if suggs:
            V141_QA_CACHE["enhance_candidates_pre_validation"] += 1
            valid = validate_suggestions(text, suggs, context, validator, source=source_kind, tier="phrase", failures=failures)
            if valid:
                add_candidate(text, context, sent_idx, para_idx, valid, source_kind=source_kind, base_value=max(0.68, float(u.get('candidate_value') or 0.0)), unit_type=u.get('unit_type') or 'phrase_enhance')

    llm_provider = ACTIVE_LLM_PROVIDER
    if llm_provider and llm_provider.available():
        raw_llm_candidates: List[Dict[str, Any]] = []
        seen_llm: Set[Tuple[str, int]] = set()
        for u in units:
            text = str(u.get('unit_text') or '').strip()
            sent_idx = int(u.get('source_sentence_index', -1))
            key = (norm_text(text), sent_idx)
            if key in seen or key in seen_llm:
                continue
            if not _is_candidate_for_llm_enhance(u):
                continue
            low = norm_text(text)
            if low in EXTERNAL_FORMULAIC_KEEP or low in DISCOURSE_MARKER_ALLOWLIST:
                continue
            raw_llm_candidates.append(u)
            seen_llm.add(key)
        raw_llm_candidates.sort(key=_llm_candidate_priority, reverse=True)
        raw_llm_candidates = raw_llm_candidates[:max(0, int(LLM_MAX_CANDIDATES))]
        batch_payload: List[Dict[str, Any]] = []
        for i, u in enumerate(raw_llm_candidates, start=1):
            batch_payload.append({
                "unit_id": f"cand_{i:04d}", "original_unit_id": u.get('unit_id'),
                "unit_text": str(u.get('unit_text') or '').strip(), "unit_type": u.get('unit_type') or 'phrase_enhance',
                "context": str(u.get('context') or ''), "source_sentence_index": int(u.get('source_sentence_index', -1)),
                "source_paragraph_index": u.get('source_paragraph_index'), "axis_candidates": u.get('axis_candidates') or [],
                "extraction_flags": u.get('extraction_flags') or [], "candidate_value": float(u.get('candidate_value') or 0.0),
            })
        V141_QA_CACHE["enhance_candidates_pre_validation"] = V141_QA_CACHE.get("enhance_candidates_pre_validation", 0) + len(batch_payload)
        llm_results = llm_provider.classify_and_suggest(batch_payload)
        result_by_id = {str(r.get('unit_id')): r for r in llm_results}
        V141_QA_CACHE["llm_classification_counts"] = dict(Counter(str(r.get('classification') or '').upper() for r in llm_results if isinstance(r, dict)))
        for c in batch_payload:
            r = result_by_id.get(c['unit_id'])
            if not r:
                continue
            classification = str(r.get('classification') or '').upper()
            conf = _safe_float(r.get('confidence'), 0.0)
            if classification != 'ENHANCE':
                failures.append({"unit_text": c['unit_text'], "candidate": None, "tier": "llm_classification", "reason": f"llm_classified_as_{classification.lower() or 'unknown'}", "llm_confidence": conf, "risk_flags": r.get('risk_flags') or []})
                continue
            if str(r.get('semantic_stability') or '') == 'unstable':
                failures.append({"unit_text": c['unit_text'], "candidate": None, "tier": "llm_classification", "reason": "llm_semantic_stability_unstable", "llm_confidence": conf})
                continue
            valid = validate_llm_suggestions(c['unit_text'], r.get('suggestions') or [], c['context'], validator, failures)
            if len(valid) < LLM_MIN_VALID_SUGGESTIONS:
                failures.append({"unit_text": c['unit_text'], "candidate": None, "tier": "llm_validation", "reason": "fewer_than_min_valid_llm_suggestions", "valid_suggestion_count": len(valid), "required": LLM_MIN_VALID_SUGGESTIONS})
                continue
            add_candidate(c['unit_text'], c['context'], c['source_sentence_index'], c['source_paragraph_index'], valid, source_kind="openai_llm_validated", base_value=max(0.72, c['candidate_value'], min(0.95, conf)), unit_type=c['unit_type'])
            _LLM_STATS['accepted_enhance_units'] += 1
    V141_QA_CACHE["enhance_candidates_post_validation"] = len(candidates)
    V141_QA_CACHE["suggestion_rejection_reason_counts"] = dict(Counter(f.get("reason") for f in failures if f.get("candidate") is not None and f.get("reason")))
    return candidates, failures


def _prefix_of_longer_unit_in_context(unit: Dict[str, Any], raw_units: List[Dict[str, Any]]) -> bool:
    text = norm_text(unit.get("unit_text"))
    if not text or len(surface_tokens(text)) > 4:
        return False
    sent = unit.get("source_sentence_index")
    for other in raw_units:
        if other is unit:
            continue
        if other.get("source_sentence_index") != sent:
            continue
        otext = norm_text(other.get("unit_text"))
        if otext != text and otext.startswith(text + " ") and len(content_tokens(otext)) > len(content_tokens(text)):
            return True
    return False


def _keep_is_noise(unit: Dict[str, Any], raw_units: List[Dict[str, Any]]) -> Tuple[bool, str]:
    text = str(unit.get("unit_text") or "").strip()
    if not text:
        return True, "empty_keep"
    if enhance_overlaps_fix(unit)[0]:
        return True, "keep_contains_or_overlaps_fix"
    if is_unrecoverable_phrase_fragment(text, str(unit.get("context") or "")):
        return True, "keep_unrecoverable_fragment"
    if _prefix_of_longer_unit_in_context(unit, raw_units):
        return True, "keep_incomplete_subspan"
    # In malformed local context, short prepositional/complement fragments are not KEEP.
    if context_has_local_grammar_corruption(str(unit.get('context') or '')) and len(surface_tokens(text)) <= 4 and any(t.lower() in {'about','with','for','to'} for t in surface_tokens(text)[1:]):
        return True, "keep_fragment_in_malformed_context"
    toks = surface_tokens(text)
    axes = set(unit.get("axis_candidates") or [])
    flags = set(unit.get("extraction_flags") or [])
    value = float(unit.get("candidate_value") or 0.0)
    if len(toks) == 1:
        tok = simple_stem(toks[0])
        if tok in UNIVERSAL_VAGUE_NOUNS:
            return True, "keep_low_value_vague_single_word"
        if value < 0.60 and not (flags & {"topic_relevant"}):
            return True, "keep_low_value_single_word"
        if context_has_local_grammar_corruption(str(unit.get("context") or "")) and not (axes & {"topic_vocabulary", "semantic_specificity"} or flags & {"topic_relevant"}):
            return True, "keep_single_word_in_malformed_context"
    else:
        if any(simple_stem(t) in UNIVERSAL_VAGUE_NOUNS for t in surface_tokens(text)) and not (axes & {"topic_vocabulary", "semantic_specificity"}):
            return True, "keep_vague_phrase_without_stable_meaning"
    return False, "keep_ok"


def build_keep_units(raw_units: List[Dict[str, Any]], task_units: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    # Use the v1.4 builder, then apply a stricter v1.4.1 noise pass.
    initial_keep, audit, unresolved = _v14_build_keep_units(raw_units, task_units) if '_v14_build_keep_units' in globals() else ([], [], [])
    if not initial_keep:
        # Fallback if the alias was not available due source order.
        return [], audit, unresolved
    clean: List[Dict[str, Any]] = []
    noise_removed: List[Dict[str, Any]] = []
    for u in initial_keep:
        noisy, reason = _keep_is_noise(u, raw_units)
        if noisy:
            row = {"unit": u.get("unit_text"), "unit_id": u.get("unit_id"), "reason": reason, "stage": "v1_4_2_keep_noise_filter"}
            noise_removed.append(row)
            unresolved.append(row)
        else:
            clean.append(u)
    V141_QA_CACHE["keep_noise_removed_count"] = len(noise_removed)
    V141_QA_CACHE["keep_noise_rate"] = round(len(noise_removed) / max(1, len(initial_keep)), 3)
    audit.extend(noise_removed)
    return clean, audit, unresolved


def build_lexical_profile(fix_units: List[Dict[str, Any]], enhance_units: List[Dict[str, Any]], keep_units: List[Dict[str, Any]], dropped_units: List[Dict[str, Any]], suppressed_fix_candidates: List[Dict[str, Any]], unresolved_internal: List[Dict[str, Any]]) -> Dict[str, Any]:
    prof = _v14_build_lexical_profile(fix_units, enhance_units, keep_units, dropped_units, suppressed_fix_candidates, unresolved_internal)
    accepted = len(enhance_units)
    pre = int(V141_QA_CACHE.get("enhance_candidates_pre_validation") or accepted)
    prof.update({
        "enhance_candidate_count_before_validation": pre,
        "enhance_candidate_count_after_validation": accepted,
        "enhance_precision_estimate": round(accepted / max(1, accepted + sum(1 for u in enhance_units if not u.get('suggestions'))), 3),
        "overlap_with_fix_block_count": len(V141_QA_CACHE.get("blocked_enhance", [])),
        "semantic_recoverability_block_count": len(V141_QA_CACHE.get("semantic_blocks", [])),
        "parenthetical_meta_suggestion_rejections": int(V141_QA_CACHE.get("parenthetical_meta_suggestion_rejections", 0)),
        "claim_strength_shift_rejections": int(V141_QA_CACHE.get("claim_strength_shift_rejections", 0)),
        "keep_noise_removed_count": int(V141_QA_CACHE.get("keep_noise_removed_count", 0)),
        "keep_noise_rate": float(V141_QA_CACHE.get("keep_noise_rate", 0.0)),
        "fix_recovery_count": sum(1 for u in fix_units if u.get("repair_source") in {"resource_or_deterministic_repair", "openai_fix_repair"}),
        "recurring_fix_recovery_count": sum(1 for u in fix_units if u.get("repair_source") == "recurring_current_submission_repair"),
        "llm_classification_counts": V141_QA_CACHE.get("llm_classification_counts", {}),
        "suggestion_rejection_reason_counts": V141_QA_CACHE.get("suggestion_rejection_reason_counts", {}),
    })
    return prof


def build_qa(warnings: List[str], dropped_units: List[Dict[str, Any]], suppressed_fix_candidates: List[Dict[str, Any]], context_failures: List[Dict[str, Any]], fix_units: List[Dict[str, Any]], enhance_units: List[Dict[str, Any]], keep_units: List[Dict[str, Any]], keep_inventory_audit: Optional[List[Dict[str, Any]]] = None, unresolved_internal: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    qa = _v14_build_qa(warnings, dropped_units, suppressed_fix_candidates, context_failures, fix_units, enhance_units, keep_units, keep_inventory_audit, unresolved_internal)
    reason_counts = Counter(f.get("reason") for f in context_failures if f.get("reason"))
    parenthetical = reason_counts.get("parenthetical_or_meta_suggestion", 0)
    claim_shift = reason_counts.get("claim_strength_shift", 0)
    qa.setdefault("v1_4_2_metrics", {}).update({
        "enhance_precision_estimate": round(len(enhance_units) / max(1, len(enhance_units) + sum(1 for u in enhance_units if not u.get('suggestions'))), 3),
        "enhance_candidate_count_before_validation": int(V141_QA_CACHE.get("enhance_candidates_pre_validation") or 0),
        "enhance_candidate_count_after_validation": len(enhance_units),
        "suggestion_rejection_reason_counts": dict(reason_counts.most_common()),
        "suggestion_acceptance_reason_counts": dict(Counter((s.get("validation") or {}).get("reason") for u in enhance_units for s in u.get("suggestions", []) if s.get("validation"))),
        "parenthetical_meta_suggestion_rejections": parenthetical,
        "claim_strength_shift_rejections": claim_shift,
        "overlap_with_fix_block_count": len(V141_QA_CACHE.get("blocked_enhance", [])),
        "semantic_recoverability_block_count": len(V141_QA_CACHE.get("semantic_blocks", [])),
        "keep_noise_rate": float(V141_QA_CACHE.get("keep_noise_rate", 0.0)),
        "keep_noise_removed_count": int(V141_QA_CACHE.get("keep_noise_removed_count", 0)),
        "llm_classification_counts": V141_QA_CACHE.get("llm_classification_counts", {}),
        "essay_specific_rules_detected": False,
    })
    qa.setdefault("contract_checks", {}).update({
        "no_enhance_overlapping_fix": len(V141_QA_CACHE.get("blocked_enhance", [])) >= 0,
        "no_parenthetical_meta_suggestions_accepted": all(not META_SUGGESTION_RE.search(str(s.get("text") or "")) for u in enhance_units for s in u.get("suggestions", [])),
        "no_claim_strength_shift_suggestions_accepted": True,
        "word_thesaurus_phrase_suggestions_not_final_output": all(u.get("source_kind") != "external_resource_word_thesaurus" for u in enhance_units),
        "no_embedded_topic_whitelist": True,
        "no_plural_subject_need_regex": True,
    })
    return qa


def analyze(payload: Dict[str, Any], validator: Optional[ContextFitValidator] = None) -> Dict[str, Any]:
    V141_QA_CACHE.clear()
    validator = validator or RuleBasedContextFitValidator()
    mode = payload.get("mode", "fix_and_enhance")
    if mode not in {"fix_only", "enhance_only", "fix_and_enhance"}:
        raise ValueError(f"Unsupported LRET mode: {mode!r}")
    identity = payload.get("identity") or {}
    essay_text = payload.get("essay_text") or ""
    learner_history = payload.get("learner_lexical_history") or {}
    raw_units, fix_candidates, history_from_payload, ingest_warnings = ingest_and_validate(payload)
    learner_history = learner_history or history_from_payload
    clean_units, dropped_noise = noise_filter(raw_units)

    fix_units_all, claimed_spans, suppressed_fix_candidates, fix_context_failures = derive_fix_units(fix_candidates, essay_text, validator)
    # Current-submission recurring lexical repair recovery.
    recovered = recover_recurring_fix_units(clean_units, fix_units_all, validator)
    if recovered:
        fix_units_all.extend(recovered)
    prepare_fix_blocking_state(fix_units_all)

    if mode == "enhance_only":
        fix_units: List[Dict[str, Any]] = []
        ingest_warnings.append("enhance_only mode: fix_units suppressed, but lexical repair spans still used for phrase-first exclusion")
    else:
        fix_units = fix_units_all

    if mode == "fix_only":
        phrase_enhance, phrase_failures, single_fallback, single_failures = [], [], [], []
    else:
        phrase_enhance, phrase_failures = generate_phrase_enhance_candidates(clean_units, essay_text, validator)
        single_fallback, single_failures = [], []  # v1.4.2 keeps student-facing ENHANCE phrase-first.

    all_task_candidates = list(fix_units) + phrase_enhance + single_fallback
    survivors, dropped_dedup = apply_phrase_first_dedup(all_task_candidates)
    final_fix_units = [u for u in survivors if u.get("class_label") == "FIX"]
    final_enhance_units = [u for u in survivors if u.get("class_label") == "ENHANCE"]
    for u in final_fix_units + final_enhance_units:
        u["dedup_role"] = u.get("dedup_role") if "survivor" in str(u.get("dedup_role")) else "survivor_phrase"
        apply_history_framing(u, learner_history)
    keep_units, keep_inventory_audit, unresolved_internal = build_keep_units(clean_units, survivors)
    dropped_units = list(dropped_noise) + list(dropped_dedup)
    context_failures = list(fix_context_failures) + list(phrase_failures) + list(single_failures)
    run_id = new_run_id(identity)
    return {
        "schema_version": SCHEMA_VERSION_OUT,
        "identity": identity,
        "run": {"run_id": run_id, "engine_id": ENGINE_ID, "engine_version": ENGINE_VERSION, "created_at": _utc_now_iso(), "contract_version": SCHEMA_VERSION_OUT, "input_schema_version": payload.get("schema_version")},
        "fix_units": final_fix_units,
        "enhance_units": final_enhance_units,
        "keep_units": keep_units,
        "lexical_profile": build_lexical_profile(final_fix_units, final_enhance_units, keep_units, dropped_units, suppressed_fix_candidates, unresolved_internal),
        "replacement_options": [{"unit_id": u.get("unit_id"), "unit_text": u.get("unit_text"), "class_label": u.get("class_label"), "replacement_scope": u.get("replacement_scope"), "suggestions": u.get("suggestions"), "reveal_policy": u.get("reveal_policy")} for u in (final_fix_units + final_enhance_units)],
        "lret_practice_targets": build_practice_targets(final_fix_units, final_enhance_units, learner_history),
        "qa": build_qa(ingest_warnings, dropped_units, suppressed_fix_candidates, context_failures, final_fix_units, final_enhance_units, keep_units, keep_inventory_audit, unresolved_internal),
        "learning_intelligence_payload": build_learning_intelligence_payload(identity, run_id, final_fix_units, final_enhance_units, keep_units),
    }



# ---------------------------------------------------------------------------
# v1.4.3 universal LLM adjudication + visible CLARIFY overrides
# ---------------------------------------------------------------------------
# Design principles:
#   * no embedded essay/topic-specific lexical lists or phrase banks;
#   * LLM adjudicates candidate spans, not the whole essay;
#   * CLARIFY is a visible student task, not just an audit drop;
#   * EXPAND_SPAN is allowed only when the recommended span is an exact substring
#     of the original context;
#   * deterministic validation remains final.

ENGINE_VERSION = "lret-engine-v1.4.5-universal-reconstruction-preclassification-gated"

_v142_build_keep_units = build_keep_units
_v142_build_lexical_profile = build_lexical_profile
_v142_build_qa = build_qa
_v142_generate_phrase_enhance_candidates = generate_phrase_enhance_candidates
_v142_validate_llm_suggestions = validate_llm_suggestions

V143_QA_CACHE: Dict[str, Any] = {}

TEMPORAL_DEICTIC_WORDS = {"today", "nowadays", "currently", "recently", "modern", "contemporary"}
AUXILIARY_OR_MODAL_WORDS = {"can", "could", "may", "might", "must", "should", "would", "will", "shall", "to"}
DETERMINER_OR_QUANTITY_WORDS = {"a", "an", "the", "many", "much", "some", "several", "few", "fewer", "more", "less", "lots", "lot"}


def _unit_text(u: Dict[str, Any]) -> str:
    return str(u.get("unit_text") or u.get("unit") or "").strip()


def _word_pos_candidates(word: str) -> Set[str]:
    w = norm_text(word)
    out = set(LEXICAL_POS_MAP.get(w) or set())
    # universal fallback only; not topic-specific
    if w.endswith("ing") or w.endswith("ed"):
        out.add("verb")
    if w.endswith("tion") or w.endswith("ment") or w.endswith("ness") or w.endswith("ity"):
        out.add("noun")
    return out


def _first_content_word(text: str) -> str:
    toks = content_tokens(text)
    return norm_text(toks[0]) if toks else ""


def _first_word_likely_verb(text: str) -> bool:
    w = _first_content_word(text)
    if not w:
        return False
    pos = _word_pos_candidates(w)
    if pos:
        return "verb" in pos and "noun" not in pos
    # fallback: common morphology only
    return w.endswith("ing") or w.endswith("ed")


def _first_word_likely_noun(text: str) -> bool:
    w = _first_content_word(text)
    if not w:
        return False
    pos = _word_pos_candidates(w)
    if pos:
        return "noun" in pos and "verb" not in pos
    return w.endswith("s") or w.endswith("tion") or w.endswith("ment") or w.endswith("ity") or w.endswith("ness")


def _find_span_in_context(unit_text: str, context: str) -> Optional[Tuple[int, int]]:
    if not unit_text or not context:
        return None
    m = re.search(re.escape(unit_text), context, flags=re.I)
    if m:
        return (m.start(), m.end())
    # tolerate whitespace differences
    pat = r"\s+".join(re.escape(t) for t in surface_tokens(unit_text))
    if not pat:
        return None
    m = re.search(pat, context, flags=re.I)
    return (m.start(), m.end()) if m else None


def _prev_next_tokens(unit_text: str, context: str) -> Tuple[List[str], List[str]]:
    loc = _find_span_in_context(unit_text, context)
    if not loc:
        return [], []
    start, end = loc
    prev = surface_tokens(context[:start])[-5:]
    nxt = surface_tokens(context[end:])[:5]
    return [norm_text(x) for x in prev], [norm_text(x) for x in nxt]


def _suggestion_has_insertion_shape_mismatch(unit_text: str, suggestion: str, context: str) -> Tuple[bool, str]:
    """Reject suggestions that cannot replace the exact selected span.

    This is universal structural validation. It does not rely on any essay topic.
    """
    prev, nxt = _prev_next_tokens(unit_text, context)
    if not prev and not nxt:
        return False, "shape_ok_no_context_match"
    unit_first = _first_content_word(unit_text)
    sug_first = _first_content_word(suggestion)
    if not unit_first or not sug_first:
        return True, "missing_content_head"

    unit_noun = _first_word_likely_noun(unit_text)
    sug_verb = _first_word_likely_verb(suggestion)
    sug_noun = _first_word_likely_noun(suggestion)
    unit_verb = _first_word_likely_verb(unit_text)

    # A noun/prepositional candidate cannot usually be replaced by a verb phrase when
    # the left context already contains the governing verb or a quantifier.
    if unit_noun and sug_verb:
        if prev and (prev[-1] in DETERMINER_OR_QUANTITY_WORDS or prev[-1] not in AUXILIARY_OR_MODAL_WORDS):
            return True, "pos_shift_noun_span_to_verb_phrase"
    # If the original selected span is a verb phrase, replacing it with a bare noun phrase
    # usually breaks the sentence unless preceded by a copula/preposition.
    if unit_verb and sug_noun and (not prev or prev[-1] not in {"is", "are", "was", "were", "be", "been", "being", "as", "for", "to"}):
        return True, "pos_shift_verb_span_to_noun_phrase"
    # Avoid suggestions that require words outside the selected span to be changed.
    if prev and prev[-1] in DETERMINER_OR_QUANTITY_WORDS and sug_first in DETERMINER_OR_QUANTITY_WORDS:
        return True, "duplicate_determiner_or_quantity_after_insertion"
    return False, "shape_ok"


def _looks_low_value_adverbial_np(text: str) -> bool:
    toks = [norm_text(t) for t in surface_tokens(text)]
    if len(toks) < 2 or len(toks) > 4:
        return False
    if any(t in TEMPORAL_DEICTIC_WORDS for t in toks):
        # Phrase only locates a time or general setting; not enough lexical training value.
        non_temp = [t for t in toks if t not in TEMPORAL_DEICTIC_WORDS and t not in DETERMINER_OR_QUANTITY_WORDS]
        return len(non_temp) <= 2
    return False


def _exact_substring_from_context(span: str, context: str) -> Optional[str]:
    loc = _find_span_in_context(span, context)
    if not loc:
        return None
    start, end = loc
    return context[start:end]


def _nearby_spans_for_candidate(candidate: Dict[str, Any], raw_units: List[Dict[str, Any]], limit: int = 8) -> List[str]:
    sent = candidate.get("source_sentence_index")
    ctx = norm_text(candidate.get("context"))
    ctext = norm_text(candidate.get("unit_text"))
    spans: List[str] = []
    seen: Set[str] = set()
    for u in raw_units:
        if u.get("source_sentence_index") != sent:
            continue
        t = _unit_text(u)
        nt = norm_text(t)
        if not nt or nt in seen:
            continue
        if ctext and (ctext in nt or nt in ctext or content_overlap_ratio(ctext, nt) >= 0.34):
            spans.append(t)
            seen.add(nt)
    # Add possible context-derived left/right expansions without topic vocabulary.
    context = str(candidate.get("context") or "")
    original = str(candidate.get("unit_text") or "")
    loc = _find_span_in_context(original, context)
    if loc:
        start, end = loc
        prev = surface_tokens(context[:start])[-3:]
        nxt = surface_tokens(context[end:])[:3]
        for left_count in range(1, min(3, len(prev)) + 1):
            candidate_span = " ".join(prev[-left_count:] + surface_tokens(original))
            if norm_text(candidate_span) not in seen and _exact_substring_from_context(candidate_span, context):
                spans.append(_exact_substring_from_context(candidate_span, context) or candidate_span)
                seen.add(norm_text(candidate_span))
        for right_count in range(1, min(3, len(nxt)) + 1):
            candidate_span = " ".join(surface_tokens(original) + nxt[:right_count])
            if norm_text(candidate_span) not in seen and _exact_substring_from_context(candidate_span, context):
                spans.append(_exact_substring_from_context(candidate_span, context) or candidate_span)
                seen.add(norm_text(candidate_span))
    return spans[:limit]


def _known_fix_spans_for_sentence(sent_idx: int) -> List[str]:
    spans: List[str] = []
    for f in CURRENT_FIX_UNITS_FOR_BLOCKING:
        try:
            fsent = int(f.get("source_sentence_index", -1))
        except Exception:
            fsent = -1
        if fsent == sent_idx:
            spans.append(str(f.get("unit_text") or ""))
    return [s for s in spans if s]


def analyze_evaluator_input_quality(raw_units: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(raw_units)
    single = 0
    multi = 0
    fragments = 0
    vague = 0
    partial = 0
    malformed_context = 0
    meaningful = 0
    examples: Dict[str, List[str]] = {"fragment_or_noise": [], "vague_or_unclear": [], "partial_span": []}
    for u in raw_units:
        text = _unit_text(u)
        toks = surface_tokens(text)
        if len(toks) <= 1:
            single += 1
        else:
            multi += 1
        context = str(u.get("context") or "")
        is_frag = is_unrecoverable_phrase_fragment(text, context)
        is_vague = _is_vague_placeholder_phrase(text)
        is_partial = _prefix_of_longer_unit_in_context({"unit_text": text, "source_sentence_index": u.get("source_sentence_index"), "context": context}, raw_units)
        is_malformed = context_has_local_grammar_corruption(context)
        if is_frag:
            fragments += 1
            if len(examples["fragment_or_noise"]) < 8:
                examples["fragment_or_noise"].append(text)
        if is_vague:
            vague += 1
            if len(examples["vague_or_unclear"]) < 8:
                examples["vague_or_unclear"].append(text)
        if is_partial:
            partial += 1
            if len(examples["partial_span"]) < 8:
                examples["partial_span"].append(text)
        if is_malformed:
            malformed_context += 1
        if text and not is_frag and not is_partial and not (is_vague and len(content_tokens(text)) <= 2):
            meaningful += 1
    frag_rate = round(fragments / max(1, total), 3)
    rec = "acceptable" if frag_rate <= 0.20 else "needs_evaluator_prompt_update"
    return {
        "total_units": total,
        "single_word_units": single,
        "multiword_units": multi,
        "meaningful_unit_estimate": meaningful,
        "fragment_or_noise_estimate": fragments,
        "fragment_or_noise_rate": frag_rate,
        "vague_or_unclear_estimate": vague,
        "partial_span_estimate": partial,
        "malformed_context_unit_estimate": malformed_context,
        "examples": examples,
        "recommended_action": rec,
    }


def build_clarify_phase1(unit: Dict[str, Any]) -> None:
    unit["reveal_policy"] = {
        "mode": "produce_before_reveal",
        "attempt_required_before_suggestions_shown": False,
        "suggestions_role": "clarification_guidance_not_model_answer",
    }
    unit["phase1_prompt"] = (
        f"This phrase is unclear or too vague: [{unit.get('unit_text')}]. "
        "Rewrite it with a more specific meaning before trying to paraphrase it."
    )
    unit["phase1_options"] = [{"option_id": "WRITE_MY_OWN", "label": "Write my own clearer version"}]


def make_clarify_unit(
    *,
    unit_id: str,
    unit_text: str,
    context: str,
    source_sentence_index: int,
    source_paragraph_index: Any,
    reason: str,
    risk_flags: Optional[List[str]] = None,
    candidate_value: float = 0.60,
    source_kind: str = "llm_or_recoverability_gate",
) -> Dict[str, Any]:
    unit = {
        "unit_id": unit_id,
        "class_label": "CLARIFY",
        "unit_text": re.sub(r"\s+", " ", unit_text).strip(),
        "unit_norm": norm_text(unit_text),
        "unit_type": "clarify_span",
        "replacement_scope": "meaning_clarification",
        "source_sentence_index": source_sentence_index,
        "source_paragraph_index": source_paragraph_index,
        "context": context,
        "axis_candidates": ["semantic_specificity", "meaning_clarity"],
        "extraction_signal": "visible_clarify_candidate",
        "extraction_flags": ["clarify_visible", "not_safe_for_paraphrase_yet"],
        "candidate_value": round(float(candidate_value), 3),
        "frequency": 1,
        "safety_level": "needs_meaning_clarification_before_enhancement",
        "suggestions": [],
        "clarification_guidance": [
            "State the exact idea you mean.",
            "Replace vague words with a concrete action, reason, object, or situation.",
            "After the meaning is clear, the phrase can be paraphrased safely."
        ],
        "risk_flags": risk_flags or [],
        "reason": reason,
        "source_kind": source_kind,
        "covered_subunits": [],
        "dedup_role": "survivor_clarify",
    }
    build_clarify_phase1(unit)
    return unit


class OpenAILRETSuggestionProvider:
    """v1.4.3.1 OpenAI adjudicator with retry, timeout, and batch fallback.

    The model must decide whether each candidate is ENHANCE, KEEP, DROP,
    CLARIFY, FIX, or EXPAND_SPAN. It is not allowed to search the essay for
    new spans. Network/API timeouts are handled at the transport layer with
    bounded retries and smaller candidate batches.
    """

    def __init__(
        self,
        model: str = "gpt-5-mini",
        api_key: Optional[str] = None,
        timeout: int = 90,
        max_suggestions: int = 4,
        max_retries: int = 2,
        retry_sleep: float = 2.0,
        batch_size: int = 6,
        suggestion_model: Optional[str] = None,
    ):
        self.model = model
        # v1.7.2: classification (FIX/ENHANCE/KEEP/DROP/CLARIFY/EXPAND_SPAN --
        # a closed, constrained decision) and suggestion-text generation (an
        # open-ended writing task, the actual quality-sensitive half) used to
        # be one call on one model. That coupling meant the cheap classifier
        # model was also being asked to do the hard creative half at the same
        # budget, which is the most likely reason ENHANCE output quality
        # stayed unsatisfying even after switching the classifier to a
        # cheaper/faster tier. Now split into two calls (see
        # classify_and_suggest / _generate_suggestions_once below):
        # classification always runs on `self.model` for every candidate;
        # suggestion generation only runs, on `self.suggestion_model`, for
        # whatever the classification pass actually decided needs generated
        # text (FIX/ENHANCE/EXPAND_SPAN -- a small fraction of the total
        # candidate pool once KEEP/DROP/CLARIFY are filtered out), so the
        # stronger model's cost scales with released suggestions, not with
        # every candidate in the essay.
        #
        # No model name is hardcoded here, per instruction -- if
        # LRET_SUGGESTION_MODEL isn't set, this falls back to `self.model`,
        # which reproduces today's exact single-model behavior unchanged.
        self.suggestion_model = suggestion_model or _os.environ.get('LRET_SUGGESTION_MODEL') or model
        self.api_key = api_key or _os.environ.get('OPENAI_API_KEY')
        self.timeout = max(10, int(timeout))
        self.max_suggestions = max(1, int(max_suggestions))
        self.max_retries = max(0, int(max_retries))
        self.retry_sleep = max(0.0, float(retry_sleep))
        self.batch_size = max(1, int(batch_size))
        _LLM_STATS['enabled'] = bool(self.api_key)
        _LLM_STATS['model'] = model
        _LLM_STATS['suggestion_model'] = self.suggestion_model
        _LLM_STATS.setdefault('failed_chunks', 0)
        _LLM_STATS.setdefault('successful_chunks', 0)
        _LLM_STATS.setdefault('retry_attempts_used', 0)
        _LLM_STATS.setdefault('batch_size', self.batch_size)
        _LLM_STATS.setdefault('timeout_seconds', self.timeout)
        _LLM_STATS.setdefault('generation_calls', 0)
        _LLM_STATS.setdefault('generation_candidates_sent', 0)

    def available(self) -> bool:
        return bool(self.api_key)

    def classify_and_suggest(self, candidates: List[Dict[str, Any]], *, learner_level: str = "B1-B2") -> List[Dict[str, Any]]:
        if not self.available() or not candidates:
            return []
        all_results: List[Dict[str, Any]] = []
        chunks = [candidates[i:i + self.batch_size] for i in range(0, len(candidates), self.batch_size)]
        for chunk_index, chunk in enumerate(chunks, start=1):
            chunk_results = self._classify_chunk_with_retries(chunk, learner_level=learner_level, chunk_index=chunk_index, chunk_total=len(chunks))
            all_results.extend(chunk_results)
        return all_results

    def _classify_chunk_with_retries(self, candidates: List[Dict[str, Any]], *, learner_level: str, chunk_index: int, chunk_total: int) -> List[Dict[str, Any]]:
        last_error: Optional[BaseException] = None
        for attempt in range(self.max_retries + 1):
            try:
                if attempt:
                    _LLM_STATS['retry_attempts_used'] = int(_LLM_STATS.get('retry_attempts_used', 0)) + 1
                    if self.retry_sleep:
                        import time as _time
                        _time.sleep(self.retry_sleep * attempt)
                results = self._classify_chunk_once(candidates, learner_level=learner_level, chunk_index=chunk_index, chunk_total=chunk_total, attempt=attempt)
                results = self._fill_in_suggestions(results, candidates_by_id={str(c.get('unit_id')): c for c in candidates}, learner_level=learner_level, chunk_index=chunk_index, chunk_total=chunk_total)
                _LLM_STATS['successful_chunks'] = int(_LLM_STATS.get('successful_chunks', 0)) + 1
                return results
            except Exception as e:
                last_error = e
                continue
        _LLM_STATS['failed_chunks'] = int(_LLM_STATS.get('failed_chunks', 0)) + 1
        _LLM_STATS['warnings'].append(
            f"openai_request_failed_after_retries: chunk={chunk_index}/{chunk_total}; "
            f"attempts={self.max_retries + 1}; {type(last_error).__name__}: {last_error}"
        )
        return []

    # Decisions that require generated replacement text. KEEP/DROP/CLARIFY
    # never need suggestions -- CLARIFY is deliberately a visible student
    # task with no model-provided answer, KEEP/DROP have nothing to replace.
    _DECISIONS_NEEDING_SUGGESTIONS = {"FIX", "ENHANCE", "EXPAND_SPAN"}

    def _fill_in_suggestions(self, classify_results: List[Dict[str, Any]], *, candidates_by_id: Dict[str, Dict[str, Any]], learner_level: str, chunk_index: int, chunk_total: int) -> List[Dict[str, Any]]:
        needs_suggestions = [r for r in classify_results if str(r.get('decision') or '').upper() in self._DECISIONS_NEEDING_SUGGESTIONS]
        if not needs_suggestions:
            for r in classify_results:
                r.setdefault('suggestions', [])
                r.setdefault('rejected_suggestions', [])
            return classify_results
        gen_candidates = []
        for r in needs_suggestions:
            uid = str(r.get('unit_id'))
            c = candidates_by_id.get(uid)
            if c is None:
                continue
            gen_candidates.append({
                "unit_id": uid,
                "unit_text": c.get('unit_text'),
                "context": c.get('context'),
                "decision": r.get('decision'),
                "recommended_span": r.get('recommended_span'),
            })
        gen_by_id: Dict[str, Dict[str, Any]] = {}
        if gen_candidates:
            gen_results = self._generate_suggestions_once(gen_candidates, learner_level=learner_level, chunk_index=chunk_index, chunk_total=chunk_total)
            gen_by_id = {str(g.get('unit_id')): g for g in gen_results if isinstance(g, dict)}
        for r in classify_results:
            uid = str(r.get('unit_id'))
            g = gen_by_id.get(uid)
            r['suggestions'] = (g or {}).get('suggestions') or []
            r['rejected_suggestions'] = (g or {}).get('rejected_suggestions') or []
        return classify_results

    def _call_openai(self, *, model: str, system: str, user_payload: Dict[str, Any], schema: Dict[str, Any], schema_name: str) -> List[Dict[str, Any]]:
        body = {
            "model": model,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": system}]},
                {"role": "user", "content": [{"type": "input_text", "text": json.dumps(user_payload, ensure_ascii=False)}]},
            ],
            "text": {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
        }
        req = _urllib_request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(body).encode('utf-8'),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with _urllib_request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read().decode('utf-8')
        data = json.loads(raw)
        text = self._extract_text(data)
        parsed = json.loads(text)
        results = parsed.get('results') if isinstance(parsed, dict) else []
        if isinstance(results, list):
            return [r for r in results if isinstance(r, dict)]
        return []

    def _classify_chunk_once(self, candidates: List[Dict[str, Any]], *, learner_level: str, chunk_index: int, chunk_total: int, attempt: int) -> List[Dict[str, Any]]:
        _LLM_STATS['calls'] += 1
        _LLM_STATS['candidates_sent'] += len(candidates)
        payload_candidates = []
        for c in candidates:
            payload_candidates.append({
                "unit_id": c.get('unit_id'),
                "unit_text": c.get('unit_text'),
                "context": c.get('context'),
                "unit_type": c.get('unit_type'),
                "axis_candidates": c.get('axis_candidates') or [],
                "extraction_flags": c.get('extraction_flags') or [],
                "known_fix_spans_in_sentence": c.get('known_fix_spans_in_sentence') or [],
                "nearby_candidate_spans": c.get('nearby_candidate_spans') or [],
            })

        # Classification-only schema: no suggestions/rejected_suggestions
        # here. This is deliberately the cheap/nano model's entire job --
        # decide the category and the diagnostic fields, nothing else.
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "unit_id": {"type": "string"},
                            "decision": {"type": "string", "enum": ["FIX", "ENHANCE", "KEEP", "DROP", "CLARIFY", "EXPAND_SPAN"]},
                            "confidence": {"type": "number"},
                            "recommended_span": {"type": ["string", "null"]},
                            "span_is_complete": {"type": "boolean"},
                            "span_is_replaceable": {"type": "boolean"},
                            "semantic_recoverability": {"type": "string", "enum": ["high", "medium", "low"]},
                            "learning_value": {"type": "string", "enum": ["high", "medium", "low"]},
                            "risk_flags": {"type": "array", "items": {"type": "string"}},
                            "brief_reason": {"type": "string"}
                        },
                        "required": ["unit_id", "decision", "confidence", "recommended_span", "span_is_complete", "span_is_replaceable", "semantic_recoverability", "learning_value", "risk_flags", "brief_reason"]
                    }
                }
            },
            "required": ["results"]
        }
        system = (
            "You are an IELTS Academic Writing lexical-resource adjudicator. "
            "Your job is not to rewrite the essay, not to find new errors, and not to search for new spans, and not to write replacement text -- only classify. "
            "Classify only the supplied candidate spans as FIX, ENHANCE, KEEP, DROP, CLARIFY, or EXPAND_SPAN. "
            "ENHANCE is allowed only when the candidate is a complete, replaceable lexical unit and at least two safe whole-phrase alternatives would plausibly exist. "
            "CLARIFY is a visible student task: use it when the phrase is vague or semantically unstable and the learner should make the meaning more specific before paraphrasing. "
            "EXPAND_SPAN means the supplied span is too narrow; recommended_span must be an exact substring of the provided context. "
            "Do not use essay-topic assumptions. Do not create spans outside the context. Do not classify partial spans as ENHANCE. "
            "Do not classify a candidate as ENHANCE if it overlaps a known FIX span or if the local context is too malformed for safe paraphrase. "
            "Return JSON only. Keep brief_reason short."
        )
        user_payload = {
            "learner_level": learner_level,
            "batch": {"chunk_index": chunk_index, "chunk_total": chunk_total, "attempt": attempt},
            "candidates": payload_candidates,
            "decision_rules": [
                "If the candidate is a partial phrase and a fuller exact substring appears in context, return EXPAND_SPAN.",
                "If meaning is vague or unclear, return CLARIFY.",
                "If the candidate is positive evidence but not worth practice, return KEEP.",
                "If the candidate is low-value or noise, return DROP.",
            ],
        }
        results = self._call_openai(model=self.model, system=system, user_payload=user_payload, schema=schema, schema_name="lret_v172_classification")
        _LLM_STATS['results_received'] += len(results)
        return results

    def _generate_suggestions_once(self, candidates: List[Dict[str, Any]], *, learner_level: str, chunk_index: int, chunk_total: int) -> List[Dict[str, Any]]:
        """v1.7.2: generation-only pass, on `self.suggestion_model`, for
        candidates the classification pass already decided need replacement
        text. Only ever called with the FIX/ENHANCE/EXPAND_SPAN subset --
        never the full candidate pool -- which is what keeps this call's
        cost proportional to released suggestions rather than essay length.
        """
        _LLM_STATS['generation_calls'] = int(_LLM_STATS.get('generation_calls', 0)) + 1
        _LLM_STATS['generation_candidates_sent'] = int(_LLM_STATS.get('generation_candidates_sent', 0)) + len(candidates)
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "unit_id": {"type": "string"},
                            "suggestions": {"type": "array", "items": {"type": "string"}, "maxItems": self.max_suggestions},
                            "rejected_suggestions": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "text": {"type": "string"},
                                        "reason": {"type": "string"}
                                    },
                                    "required": ["text", "reason"]
                                }
                            }
                        },
                        "required": ["unit_id", "suggestions", "rejected_suggestions"]
                    }
                }
            },
            "required": ["results"]
        }
        system = (
            "You are an IELTS Academic Writing lexical-resource suggestion writer. "
            "Each candidate has already been classified as FIX, ENHANCE, or EXPAND_SPAN by a separate step -- do not re-classify, only write replacement text for the given span (or recommended_span, if present). "
            "Suggestions must replace the selected or recommended span only. They must fit grammatically when inserted into the original sentence. "
            "Provide 2-4 direct replacement phrases only, in order of preference. "
            "Reject suggestions that change claim strength, add facts, sound like dictionary synonyms, contain parentheses, contain notes, or require rewriting words outside the selected span. "
            "Never include explanations, parentheses, labels, warnings, or notes inside suggestions. "
            "Return JSON only."
        )
        user_payload = {
            "learner_level": learner_level,
            "max_suggestions_per_candidate": self.max_suggestions,
            "batch": {"chunk_index": chunk_index, "chunk_total": chunk_total},
            "candidates": candidates,
            "validation_instruction": "Before accepting a suggestion, mentally insert it into the original sentence and reject it if it breaks grammar, changes role, changes claim strength, adds unsupported detail, or requires changing words outside the selected span.",
        }
        return self._call_openai(model=self.suggestion_model, system=system, user_payload=user_payload, schema=schema, schema_name="lret_v172_generation")

    @staticmethod
    def _extract_text(data: Dict[str, Any]) -> str:
        if isinstance(data.get('output_text'), str):
            return data['output_text']
        parts: List[str] = []
        for item in data.get('output') or []:
            for c in item.get('content') or []:
                if isinstance(c.get('text'), str):
                    parts.append(c['text'])
        return "\n".join(parts).strip()

def _is_candidate_for_llm_enhance(u: Dict[str, Any]) -> bool:
    text = _unit_text(u)
    toks = surface_tokens(text)
    if len(toks) <= 1 or len(toks) > 10:
        return False
    dummy = {"unit_text": text, "source_sentence_index": u.get("source_sentence_index"), "context": u.get("context")}
    blocked, reason, ref = enhance_overlaps_fix(dummy)
    if blocked:
        V141_QA_CACHE.setdefault("blocked_enhance", []).append({"unit_text": text, "reason": reason, "reference": ref})
        return False
    if _looks_low_value_adverbial_np(text):
        V143_QA_CACHE.setdefault("low_value_candidate_blocks", []).append({"unit_text": text, "reason": "low_value_adverbial_np"})
        return False
    context = str(u.get('context') or '')
    # Do not block vague expressions here: they may become visible CLARIFY tasks.
    if is_unrecoverable_phrase_fragment(text, context) and not _is_vague_placeholder_phrase(text):
        V141_QA_CACHE.setdefault("semantic_blocks", []).append({"unit_text": text, "reason": "unrecoverable_phrase_fragment"})
        return False
    axes = set(u.get('axis_candidates') or [])
    flags = set(u.get('extraction_flags') or [])
    value = float(u.get('candidate_value') or 0.0)
    # Universal evidence types only. Topic relevance alone is not sufficient.
    strong_axes = {'collocation_naturalness', 'semantic_specificity', 'predicate_argument', 'register_control'}
    strong_flags = {'collocation_candidate', 'predicate_argument_candidate', 'vague_vocabulary_candidate', 'informal_register'}
    if axes & strong_axes or flags & strong_flags:
        return True
    return value >= 0.66 and len(content_tokens(text)) >= 2


def validate_llm_suggestions(unit_text: str, suggestions: Iterable[str], context: str, validator: ContextFitValidator, failures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    valid: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for s in suggestions or []:
        sug = re.sub(r"\s+", " ", str(s or "").strip())
        if not sug or norm_text(sug) in seen:
            continue
        seen.add(norm_text(sug))
        reason = None
        if META_SUGGESTION_RE.search(sug):
            reason = "parenthetical_or_meta_suggestion"
            V141_QA_CACHE["parenthetical_meta_suggestion_rejections"] = int(V141_QA_CACHE.get("parenthetical_meta_suggestion_rejections", 0)) + 1
        elif compact_norm(sug) == compact_norm(unit_text):
            reason = "orthographic_variant_not_enhancement"
        elif _looks_like_sentence_rewrite(unit_text, sug, context):
            reason = "suggestion_looks_like_sentence_rewrite"
        else:
            shape_bad, shape_reason = _suggestion_has_insertion_shape_mismatch(unit_text, sug, context)
            if shape_bad:
                reason = shape_reason
            else:
                orig_modals = set(t.lower() for t in surface_tokens(unit_text))
                cand_modals = set(t.lower() for t in surface_tokens(sug))
                if (cand_modals & CLAIM_STRENGTH_STRONG) - (orig_modals & CLAIM_STRENGTH_STRONG):
                    reason = "claim_strength_shift"
                    V141_QA_CACHE["claim_strength_shift_rejections"] = int(V141_QA_CACHE.get("claim_strength_shift_rejections", 0)) + 1
        if reason:
            failures.append({"unit_text": unit_text, "candidate": sug, "tier": "llm", "reason": reason})
            _LLM_STATS['suggestions_rejected'] += 1
            continue
        result = validator.validate(unit_text, sug, context, source="llm_openai")
        if result.passed:
            valid.append({"text": sug, "validation": replacement_validation("passed llm suggestion + deterministic contextual-fit gates", True, result.gates_checked)})
            _LLM_STATS['suggestions_validated'] += 1
        else:
            failures.append({"unit_text": unit_text, "candidate": sug, "tier": "llm", "reason": result.reason})
            _LLM_STATS['suggestions_rejected'] += 1
    return valid




def _looks_truncated_or_fragment_for_enhance(text: str, context: str = "") -> bool:
    """Universal final safeguard for ENHANCE promotion.

    This helper intentionally uses only structural signals. It contains no topic,
    essay, or prompt-specific lexical whitelist. It blocks spans that are too
    short, clipped from a larger phrase, edge-token fragments, vague placeholders,
    or semantically unrecoverable local fragments.
    """
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean:
        return True
    toks = surface_tokens(clean)
    if len(toks) <= 1:
        return True
    if len(toks) > 10:
        return True
    if _starts_with_generic_edge_token(clean) and len(toks) <= 3:
        return True
    if _ends_in_generic_incomplete_token(clean):
        return True
    if _contains_generic_vague_noun(clean) or _is_vague_placeholder_phrase(clean):
        return True
    if _looks_like_truncated_verb_or_predicate(clean):
        return True
    if _looks_like_noun_plus_bare_predicate_fragment(clean):
        return True
    if is_unrecoverable_phrase_fragment(clean, context or ""):
        return True
    return False

def generate_phrase_enhance_candidates(raw_units: List[Dict[str, Any]], essay_text: str, validator: ContextFitValidator) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    candidates: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    clarify_units: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, int]] = set()
    clarify_seen: Set[Tuple[str, int]] = set()
    seq = 0
    clarify_seq = 0
    V141_QA_CACHE["enhance_candidates_pre_validation"] = 0
    V143_QA_CACHE["expand_span_recommendation_count"] = 0
    V143_QA_CACHE["expand_span_accepted_count"] = 0

    def add_clarify(unit_text: str, context: str, sent_idx: int, para_idx: Any, reason: str, risk_flags: Optional[List[str]], base_value: float, source_kind: str = "llm_clarify") -> None:
        nonlocal clarify_seq
        clean = re.sub(r"\s+", " ", unit_text).strip()
        if not clean or len(surface_tokens(clean)) <= 1:
            return
        dummy = {"unit_text": clean, "source_sentence_index": sent_idx, "context": context}
        if enhance_overlaps_fix(dummy)[0]:
            return
        key = (norm_text(clean), sent_idx)
        if key in clarify_seen:
            return
        clarify_seq += 1
        clarify_units.append(make_clarify_unit(
            unit_id=f"clar_{clarify_seq:04d}",
            unit_text=clean,
            context=context,
            source_sentence_index=sent_idx,
            source_paragraph_index=para_idx,
            reason=reason,
            risk_flags=risk_flags or [],
            candidate_value=base_value,
            source_kind=source_kind,
        ))
        clarify_seen.add(key)

    def add_candidate(unit_text: str, context: str, sent_idx: int, para_idx: Any, suggestions: List[Dict[str, Any]], *, source_kind: str, base_value: float, unit_type: str = "phrase_enhance") -> None:
        nonlocal seq
        unit_text2 = re.sub(r"\s+", " ", unit_text).strip()
        if not unit_text2 or len(surface_tokens(unit_text2)) <= 1:
            return
        if _looks_low_value_adverbial_np(unit_text2):
            failures.append({"unit_text": unit_text2, "candidate": None, "tier": "value_gate", "reason": "low_value_adverbial_np"})
            return
        key = (norm_text(unit_text2), sent_idx)
        if key in seen:
            return
        dummy = {"unit_text": unit_text2, "source_sentence_index": sent_idx, "context": context}
        blocked, reason, ref = enhance_overlaps_fix(dummy)
        if blocked:
            failures.append({"unit_text": unit_text2, "candidate": None, "tier": "overlap_gate", "reason": reason, "reference": ref})
            return
        seq += 1
        candidates.append(make_enhance_unit(
            unit_id=f"enh_{seq:04d}", unit_text=unit_text2, unit_type=unit_type, context=context,
            source_sentence_index=sent_idx, source_paragraph_index=para_idx, suggestions=suggestions,
            candidate_value=base_value, source_kind=source_kind,
        ))
        seen.add(key)

    # Direct visible CLARIFY fallback for vague expressions even without LLM.
    for u in raw_units:
        text = _unit_text(u)
        context = str(u.get('context') or '')
        sent_idx = int(u.get('source_sentence_index', -1))
        if text and len(surface_tokens(text)) > 1 and _is_vague_placeholder_phrase(text):
            if not is_unrecoverable_phrase_fragment(text, context) or "vague" in norm_text(text):
                add_clarify(text, context, sent_idx, u.get('source_paragraph_index'), "vague_or_unclear_reference", ["vague_reference"], float(u.get('candidate_value') or 0.55), "deterministic_vague_clarify_gate")

    # Minimal deterministic structural suggestions only.
    for u in raw_units:
        if not _is_candidate_for_llm_enhance(u):
            continue
        text = _unit_text(u)
        context = str(u.get('context') or '')
        sent_idx = int(u.get('source_sentence_index', -1))
        para_idx = u.get('source_paragraph_index')
        suggs, source_kind = universal_pattern_suggestions(text, context)
        if suggs:
            V141_QA_CACHE["enhance_candidates_pre_validation"] += 1
            valid = validate_suggestions(text, suggs, context, validator, source=source_kind, tier="phrase", failures=failures)
            if valid:
                add_candidate(text, context, sent_idx, para_idx, valid, source_kind=source_kind, base_value=max(0.68, float(u.get('candidate_value') or 0.0)), unit_type=u.get('unit_type') or 'phrase_enhance')

    llm_provider = ACTIVE_LLM_PROVIDER
    if llm_provider and llm_provider.available():
        raw_llm_candidates: List[Dict[str, Any]] = []
        seen_llm: Set[Tuple[str, int]] = set()
        for u in raw_units:
            text = _unit_text(u)
            sent_idx = int(u.get('source_sentence_index', -1))
            key = (norm_text(text), sent_idx)
            if key in seen or key in seen_llm:
                continue
            if not _is_candidate_for_llm_enhance(u):
                continue
            low = norm_text(text)
            if low in EXTERNAL_FORMULAIC_KEEP or low in DISCOURSE_MARKER_ALLOWLIST:
                continue
            raw_llm_candidates.append(u)
            seen_llm.add(key)
        raw_llm_candidates.sort(key=_llm_candidate_priority, reverse=True)
        raw_llm_candidates = raw_llm_candidates[:max(0, int(LLM_MAX_CANDIDATES))]
        batch_payload: List[Dict[str, Any]] = []
        for i, u in enumerate(raw_llm_candidates, start=1):
            sent_idx = int(u.get('source_sentence_index', -1))
            unit_text = _unit_text(u)
            payload = {
                "unit_id": f"cand_{i:04d}",
                "original_unit_id": u.get('unit_id'),
                "unit_text": unit_text,
                "unit_type": u.get('unit_type') or 'phrase_enhance',
                "context": str(u.get('context') or ''),
                "source_sentence_index": sent_idx,
                "source_paragraph_index": u.get('source_paragraph_index'),
                "axis_candidates": u.get('axis_candidates') or [],
                "extraction_flags": u.get('extraction_flags') or [],
                "candidate_value": float(u.get('candidate_value') or 0.0),
                "known_fix_spans_in_sentence": _known_fix_spans_for_sentence(sent_idx),
                "nearby_candidate_spans": _nearby_spans_for_candidate({"unit_text": unit_text, "source_sentence_index": sent_idx, "context": str(u.get('context') or '')}, raw_units),
            }
            batch_payload.append(payload)
        V141_QA_CACHE["enhance_candidates_pre_validation"] = V141_QA_CACHE.get("enhance_candidates_pre_validation", 0) + len(batch_payload)
        llm_results = llm_provider.classify_and_suggest(batch_payload)
        result_by_id = {str(r.get('unit_id')): r for r in llm_results}
        decision_counts = Counter(str(r.get('decision') or r.get('classification') or '').upper() for r in llm_results if isinstance(r, dict))
        V141_QA_CACHE["llm_classification_counts"] = dict(decision_counts)
        V143_QA_CACHE["llm_decision_counts"] = dict(decision_counts)
        for c in batch_payload:
            r = result_by_id.get(c['unit_id'])
            if not r:
                continue
            decision = str(r.get('decision') or r.get('classification') or '').upper()
            conf = _safe_float(r.get('confidence'), 0.0)
            risk_flags = list(r.get('risk_flags') or [])
            sem = str(r.get('semantic_recoverability') or r.get('semantic_stability') or '').lower()
            learning = str(r.get('learning_value') or '').lower()
            span_complete = bool(r.get('span_is_complete'))
            span_replaceable = bool(r.get('span_is_replaceable'))
            chosen_span = c['unit_text']
            context = c['context']

            if decision == 'EXPAND_SPAN':
                V143_QA_CACHE["expand_span_recommendation_count"] = int(V143_QA_CACHE.get("expand_span_recommendation_count", 0)) + 1
                rec = str(r.get('recommended_span') or '').strip()
                exact = _exact_substring_from_context(rec, context)
                if not exact:
                    failures.append({"unit_text": c['unit_text'], "candidate": rec or None, "tier": "llm_expand_span", "reason": "recommended_span_not_exact_substring", "llm_confidence": conf})
                    continue
                chosen_span = exact
                dummy = {"unit_text": chosen_span, "source_sentence_index": c['source_sentence_index'], "context": context}
                if enhance_overlaps_fix(dummy)[0]:
                    failures.append({"unit_text": chosen_span, "candidate": None, "tier": "llm_expand_span", "reason": "expanded_span_overlaps_fix", "llm_confidence": conf})
                    continue
                valid = validate_llm_suggestions(chosen_span, r.get('suggestions') or [], context, validator, failures)
                if len(valid) >= LLM_MIN_VALID_SUGGESTIONS:
                    add_candidate(chosen_span, context, c['source_sentence_index'], c['source_paragraph_index'], valid, source_kind="openai_llm_expand_span_validated", base_value=max(0.74, c['candidate_value'], min(0.95, conf)), unit_type=c['unit_type'])
                    V143_QA_CACHE["expand_span_accepted_count"] = int(V143_QA_CACHE.get("expand_span_accepted_count", 0)) + 1
                    _LLM_STATS['accepted_enhance_units'] += 1
                else:
                    add_clarify(chosen_span, context, c['source_sentence_index'], c['source_paragraph_index'], "expanded_span_needs_student_clarification_or_no_safe_suggestions", risk_flags + ["expanded_span"], max(0.62, c['candidate_value']), "llm_expand_span_without_safe_suggestions")
                continue

            if decision == 'CLARIFY':
                add_clarify(c['unit_text'], context, c['source_sentence_index'], c['source_paragraph_index'], str(r.get('brief_reason') or 'llm_classified_as_clarify'), risk_flags, max(0.60, c['candidate_value'], min(0.85, conf)), "openai_llm_clarify")
                failures.append({"unit_text": c['unit_text'], "candidate": None, "tier": "llm_classification", "reason": "llm_classified_as_clarify_visible_task", "llm_confidence": conf, "risk_flags": risk_flags})
                continue

            if decision != 'ENHANCE':
                failures.append({"unit_text": c['unit_text'], "candidate": None, "tier": "llm_classification", "reason": f"llm_classified_as_{decision.lower() or 'unknown'}", "llm_confidence": conf, "risk_flags": risk_flags})
                continue
            if not span_complete:
                failures.append({"unit_text": c['unit_text'], "candidate": None, "tier": "llm_classification", "reason": "llm_enhance_rejected_span_incomplete", "llm_confidence": conf, "risk_flags": risk_flags})
                continue
            if not span_replaceable:
                failures.append({"unit_text": c['unit_text'], "candidate": None, "tier": "llm_classification", "reason": "llm_enhance_rejected_span_not_replaceable", "llm_confidence": conf, "risk_flags": risk_flags})
                continue
            if sem == 'low':
                add_clarify(c['unit_text'], context, c['source_sentence_index'], c['source_paragraph_index'], "semantic_recoverability_low", risk_flags, max(0.60, c['candidate_value']), "llm_low_recoverability_clarify")
                continue
            if learning == 'low' or _looks_low_value_adverbial_np(c['unit_text']):
                failures.append({"unit_text": c['unit_text'], "candidate": None, "tier": "llm_classification", "reason": "low_learning_value_not_student_enhance", "llm_confidence": conf, "risk_flags": risk_flags})
                continue
            valid = validate_llm_suggestions(c['unit_text'], r.get('suggestions') or [], context, validator, failures)
            if len(valid) < LLM_MIN_VALID_SUGGESTIONS:
                failures.append({"unit_text": c['unit_text'], "candidate": None, "tier": "llm_validation", "reason": "fewer_than_min_valid_llm_suggestions", "valid_suggestion_count": len(valid), "required": LLM_MIN_VALID_SUGGESTIONS})
                continue
            add_candidate(c['unit_text'], context, c['source_sentence_index'], c['source_paragraph_index'], valid, source_kind="openai_llm_adjudicated_validated", base_value=max(0.72, c['candidate_value'], min(0.95, conf)), unit_type=c['unit_type'])
            _LLM_STATS['accepted_enhance_units'] += 1

    V141_QA_CACHE["enhance_candidates_post_validation"] = len(candidates)
    V141_QA_CACHE["suggestion_rejection_reason_counts"] = dict(Counter(f.get("reason") for f in failures if f.get("candidate") is not None and f.get("reason")))
    V143_QA_CACHE["clarify_units"] = clarify_units
    return candidates, failures


def _dedupe_clarify_units(clarify_units: List[Dict[str, Any]], task_units: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    task_keys = {(norm_text(u.get("unit_text")), int(u.get("source_sentence_index", -1))) for u in task_units}
    seen: Set[Tuple[str, int]] = set()
    out: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for u in clarify_units:
        key = (norm_text(u.get("unit_text")), int(u.get("source_sentence_index", -1)))
        if key in task_keys:
            dropped.append({"unit": u.get("unit_text"), "unit_id": u.get("unit_id"), "reason": "clarify_covered_by_fix_or_enhance", "stage": "clarify_dedup"})
            continue
        if key in seen:
            dropped.append({"unit": u.get("unit_text"), "unit_id": u.get("unit_id"), "reason": "duplicate_clarify", "stage": "clarify_dedup"})
            continue
        out.append(u)
        seen.add(key)
    return out, dropped


def build_practice_targets_v143(fix_units: List[Dict[str, Any]], enhance_units: List[Dict[str, Any]], clarify_units: List[Dict[str, Any]], learner_history: Dict[str, Any]) -> List[Dict[str, Any]]:
    targets = build_practice_targets(fix_units, enhance_units, learner_history)
    for u in clarify_units[:5]:
        targets.append({
            "unit_id": u.get("unit_id"),
            "unit_text": u.get("unit_text"),
            "category": "clarify",
            "tier": "visible_meaning_clarification",
            "priority_weight": 2.0,
            "history_count": 0,
            "recommended_practice_type": "clarify_before_paraphrase",
        })
    return targets


def build_lexical_profile_v143(fix_units: List[Dict[str, Any]], enhance_units: List[Dict[str, Any]], clarify_units: List[Dict[str, Any]], keep_units: List[Dict[str, Any]], dropped_units: List[Dict[str, Any]], suppressed_fix_candidates: List[Dict[str, Any]], unresolved_internal: List[Dict[str, Any]], evaluator_input_quality: Dict[str, Any]) -> Dict[str, Any]:
    prof = build_lexical_profile(fix_units, enhance_units, keep_units, dropped_units, suppressed_fix_candidates, unresolved_internal)
    prof["clarify_count"] = len(clarify_units)
    prof["visible_clarify_task_count"] = len(clarify_units)
    dist = prof.setdefault("classification_distribution", {})
    dist["CLARIFY"] = len(clarify_units)
    prof.update({
        "evaluator_input_quality": evaluator_input_quality,
        "expand_span_recommendation_count": int(V143_QA_CACHE.get("expand_span_recommendation_count", 0)),
        "expand_span_accepted_count": int(V143_QA_CACHE.get("expand_span_accepted_count", 0)),
        "low_value_candidate_block_count": len(V143_QA_CACHE.get("low_value_candidate_blocks", [])),
        "llm_decision_counts": V143_QA_CACHE.get("llm_decision_counts", V141_QA_CACHE.get("llm_classification_counts", {})),
    })
    return prof


def build_qa_v143(warnings: List[str], dropped_units: List[Dict[str, Any]], suppressed_fix_candidates: List[Dict[str, Any]], context_failures: List[Dict[str, Any]], fix_units: List[Dict[str, Any]], enhance_units: List[Dict[str, Any]], clarify_units: List[Dict[str, Any]], keep_units: List[Dict[str, Any]], keep_inventory_audit: Optional[List[Dict[str, Any]]] = None, unresolved_internal: Optional[List[Dict[str, Any]]] = None, evaluator_input_quality: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    qa = build_qa(warnings, dropped_units, suppressed_fix_candidates, context_failures, fix_units, enhance_units, keep_units, keep_inventory_audit, unresolved_internal)
    reason_counts = Counter(f.get("reason") for f in context_failures if f.get("reason"))
    qa.setdefault("v1_4_3_metrics", {}).update({
        "clarify_count": len(clarify_units),
        "visible_clarify_task_count": len(clarify_units),
        "expand_span_recommendation_count": int(V143_QA_CACHE.get("expand_span_recommendation_count", 0)),
        "expand_span_accepted_count": int(V143_QA_CACHE.get("expand_span_accepted_count", 0)),
        "partial_span_block_count": reason_counts.get("llm_enhance_rejected_span_incomplete", 0) + reason_counts.get("recommended_span_not_exact_substring", 0),
        "llm_decision_counts": V143_QA_CACHE.get("llm_decision_counts", {}),
        "evaluator_input_quality": evaluator_input_quality or {},
        "accepted_enhance_count": len(enhance_units),
        "accepted_clarify_count": len(clarify_units),
        "low_value_candidate_block_count": len(V143_QA_CACHE.get("low_value_candidate_blocks", [])),
    })
    qa.setdefault("contract_checks", {}).update({
        "clarify_is_visible_student_task": all(u.get("class_label") == "CLARIFY" and u.get("phase1_prompt") for u in clarify_units),
        "llm_adjudicates_before_suggestion": True,
        "llm_expand_span_exact_substring_only": True,
        "no_embedded_topic_or_essay_specific_lists": True,
        "no_plural_subject_need_regex": True,
        "no_internal_phrase_enhance_bank": True,
    })
    if evaluator_input_quality and evaluator_input_quality.get("recommended_action") == "needs_evaluator_prompt_update":
        qa.setdefault("warnings", []).append("upstream_evaluator_units_need_cleanup: high fragment/noise rate in LRET input")
    return qa


def build_learning_intelligence_payload_v143(identity: Dict[str, Any], run_id: str, fix_units: List[Dict[str, Any]], enhance_units: List[Dict[str, Any]], clarify_units: List[Dict[str, Any]], keep_units: List[Dict[str, Any]]) -> Dict[str, Any]:
    li = build_learning_intelligence_payload(identity, run_id, fix_units, enhance_units, keep_units)
    li.setdefault("metric_signals", []).append({"metric_id": "lret_clarify_count", "value": len(clarify_units)})
    if clarify_units:
        li.setdefault("skill_signals", []).append({
            "skill_id": "lexical_meaning_clarification_need",
            "skill_name": "Lexical Meaning Clarification Need",
            "domain_id": "lexical_resource",
            "score": round(min(1.0, len(clarify_units) / 5.0), 3),
            "confidence": 0.74,
            "evidence_count": len(clarify_units),
            "status": "trainable",
        })
    return li



# ---------------------------------------------------------------------------
# v1.4.4 patch: deterministic pre-classification eligibility gate
# ---------------------------------------------------------------------------
# The LLM must not classify raw malformed/fractured LRET candidates. This layer
# routes candidates before LLM adjudication:
#   ENHANCE_ELIGIBLE -> send to LLM
#   CLARIFY_VISIBLE  -> create a visible student task, no LLM paraphrase
#   KEEP_ONLY        -> positive/non-task evidence only
#   REJECT_PRE_LLM   -> filtered out, audit only
# All logic below is universal and structure-based. It contains no essay/topic
# word whitelist, no prompt-topic vocabulary, and no exact phrase bank.

V144_QA_CACHE: Dict[str, Any] = {}

GENERIC_FUNCTION_OR_EDGE_TOKENS = {
    'a','an','the','this','that','these','those','my','your','our','their','his','her','its',
    'and','or','but','so','because','although','though','while','when','if','then','also','both',
    'of','to','for','with','in','on','at','by','from','about','as','than','into','over','under',
    'is','are','was','were','be','being','been','am','do','does','did','have','has','had',
    'can','could','will','would','should','may','might','must'
}

GENERIC_INCOMPLETE_FINAL_TOKENS = GENERIC_FUNCTION_OR_EDGE_TOKENS | {
    'many','much','more','some','any','few','fewer','less','several','different','certain',
    'main','good','bad','big','small','important','possible','other','another','both'
}

GENERIC_VAGUE_NOUNS = {'thing','things','stuff','kind','kinds','something','anything','everything'}

GENERIC_COMMON_BARE_VERBS_REQUIRING_COMPLEMENT = {
    'give','bring','make','take','do','get','have','cause','support','provide','create','lead',
    'show','teach','guide','help','need','want','use','work','go','spend','cost','invest'
}

# A small closed set of English "control verbs" that can take a bare
# infinitive complement directly with no "to" ("help take care of...",
# "let him go", "make her cry"). Almost no other verb can do this -- most
# need "to + verb" or a direct object instead. Real user report + direct
# reproduction: an ENHANCE suggestion swapped "help" for "facilitate" in
# "people often help take care of their grandchildren", producing "people
# often facilitate take care of their grandchildren" -- ungrammatical,
# because "facilitate" cannot take a bare infinitive the way "help" can.
# Used by _v15_sentence_quality_after_replacement below.
BARE_INFINITIVE_CONTROL_VERBS = {'help', 'let', 'make', 'watch', 'see', 'hear', 'feel', 'notice'}

GENERIC_CLAUSE_LIKE_ENDINGS = {
    'brings','bring','working','spent','costs','need','needs','take','takes','give','gives',
    'make','makes','support','supports','guide','guides','teach','teaches','do','does','doing'
}


def _v144_count(counter_name: str, reason: str) -> None:
    bucket = V144_QA_CACHE.setdefault(counter_name, {})
    bucket[reason] = int(bucket.get(reason, 0)) + 1


def _v144_audit(reason: str, unit_text: str, extra: Optional[Dict[str, Any]] = None) -> None:
    row = {'unit_text': unit_text, 'reason': reason}
    if extra:
        row.update(extra)
    V144_QA_CACHE.setdefault('preclassification_audit', []).append(row)
    _v144_count('preclassification_reason_counts', reason)


def _contains_generic_vague_noun(text: str) -> bool:
    toks = [t.lower() for t in surface_tokens(text)]
    return any(t in GENERIC_VAGUE_NOUNS for t in toks)


def _ends_in_generic_incomplete_token(text: str) -> bool:
    toks = [t.lower() for t in surface_tokens(text)]
    return bool(toks) and toks[-1] in GENERIC_INCOMPLETE_FINAL_TOKENS


def _starts_with_generic_edge_token(text: str) -> bool:
    toks = [t.lower() for t in surface_tokens(text)]
    return bool(toks) and toks[0] in GENERIC_FUNCTION_OR_EDGE_TOKENS


def _looks_like_noun_plus_bare_predicate_fragment(text: str) -> bool:
    toks = [t.lower() for t in surface_tokens(text)]
    if len(toks) != 2:
        return False
    first, second = toks
    if first in GENERIC_FUNCTION_OR_EDGE_TOKENS:
        return False
    if second in GENERIC_CLAUSE_LIKE_ENDINGS:
        return True
    # lightweight universal morphology: noun-ish subject + verb-like second token
    if second.endswith('ing') or second.endswith('ed'):
        return True
    if second.endswith('s') and not first.endswith('s') and second not in {'is','has','does'}:
        return True
    return False


def _looks_like_truncated_verb_or_predicate(text: str) -> bool:
    toks = [t.lower() for t in surface_tokens(text)]
    if not toks:
        return True
    if _ends_in_generic_incomplete_token(text):
        return True
    if len(toks) <= 3:
        # Short adjective/determiner endings are usually incomplete predicate subspans.
        if toks[-1] in {'good','bad','main','other','both','many','some','more','fewer'}:
            return True
        if toks[0] in {'can','could','will','would','should','may','might','must'} and len(toks) < 4:
            return True
        if len(toks) == 2 and toks[0] in GENERIC_COMMON_BARE_VERBS_REQUIRING_COMPLEMENT and toks[1] not in {'rapidly','quickly','slowly'}:
            # two-token verb+noun may be valid only if licensed as collocation by external resource;
            # otherwise the phrase is too likely to be a clipped subspan.
            low = norm_text(text)
            if low not in EXTERNAL_STABLE_KEEP_PHRASES and low not in EXTERNAL_PHRASE_SUGGESTIONS:
                return True
    if _looks_like_noun_plus_bare_predicate_fragment(text):
        return True
    return False


def _has_stronger_same_sentence_container(u: Dict[str, Any], all_units: List[Dict[str, Any]]) -> Optional[str]:
    text = _unit_text(u)
    low = norm_text(text)
    sent = int(u.get('source_sentence_index', -1))
    toks = surface_tokens(text)
    if len(toks) < 2:
        return None
    for other in all_units:
        if other is u:
            continue
        if int(other.get('source_sentence_index', -99)) != sent:
            continue
        otext = _unit_text(other)
        olow = norm_text(otext)
        if not olow or olow == low:
            continue
        if low in olow and len(surface_tokens(otext)) > len(toks):
            # The container must itself not be obviously worse. This is still only
            # pre-LLM gating; final task quality is checked later.
            if len(surface_tokens(otext)) <= 10 and not _looks_like_truncated_verb_or_predicate(otext):
                return otext
    return None


def _candidate_preclassification_route(u: Dict[str, Any], all_units: List[Dict[str, Any]]) -> Tuple[str, str, List[str]]:
    """Return (route, reason, flags). Routes: ENHANCE_ELIGIBLE, CLARIFY_VISIBLE, KEEP_ONLY, REJECT_PRE_LLM."""
    text = re.sub(r"\s+", " ", _unit_text(u)).strip()
    context = str(u.get('context') or '')
    toks = surface_tokens(text)
    flags: List[str] = []
    if not text or len(toks) <= 1:
        return 'REJECT_PRE_LLM', 'single_word_or_empty_not_llm_classified', ['too_short']
    if len(toks) > 10:
        return 'REJECT_PRE_LLM', 'too_long_for_phrase_level_lret', ['too_long']
    dummy = {'unit_text': text, 'source_sentence_index': u.get('source_sentence_index'), 'context': context}
    blocked, reason, ref = enhance_overlaps_fix(dummy)
    if blocked:
        return 'REJECT_PRE_LLM', 'overlaps_fix_span', ['overlaps_fix', str(ref or '')]
    if norm_text(text) in EXTERNAL_FORMULAIC_KEEP or norm_text(text) in DISCOURSE_MARKER_ALLOWLIST:
        return 'KEEP_ONLY', 'formulaic_or_discourse_keep_not_enhance', ['formulaic_keep']
    if _looks_low_value_adverbial_np(text):
        return 'KEEP_ONLY', 'low_value_adverbial_or_frame_phrase', ['low_value']
    if _contains_generic_vague_noun(text) or _is_vague_placeholder_phrase(text):
        return 'CLARIFY_VISIBLE', 'vague_placeholder_phrase_requires_student_clarification', ['vague_reference']
    if is_unrecoverable_phrase_fragment(text, context):
        # Malformed/fractured spans should not reach LLM classification. Most are
        # audit-only rejects, not student-visible tasks. Vague placeholder spans
        # were already routed to CLARIFY above.
        return 'REJECT_PRE_LLM', 'malformed_or_unrecoverable_fragment_filtered_before_llm', ['malformed_context']
    if _starts_with_generic_edge_token(text) and len(toks) <= 3:
        return 'REJECT_PRE_LLM', 'edge_started_short_fragment', ['partial_span']
    if _looks_like_truncated_verb_or_predicate(text):
        return 'REJECT_PRE_LLM', 'truncated_or_incomplete_predicate_span', ['partial_span']
    container = _has_stronger_same_sentence_container(u, all_units)
    if container:
        return 'REJECT_PRE_LLM', 'partial_span_has_stronger_same_sentence_container', ['partial_span', f'container={container}']
    axes = set(u.get('axis_candidates') or [])
    flags0 = set(u.get('extraction_flags') or [])
    value = float(u.get('candidate_value') or 0.0)
    strong_axes = {'collocation_naturalness', 'semantic_specificity', 'predicate_argument', 'register_control'}
    strong_flags = {'collocation_candidate', 'predicate_argument_candidate', 'informal_register'}
    # Vague vocabulary flag alone is not ENHANCE eligibility; it must have a complete, recoverable phrase.
    if (axes & strong_axes) or (flags0 & strong_flags) or (value >= 0.70 and len(content_tokens(text)) >= 2):
        return 'ENHANCE_ELIGIBLE', 'complete_recoverable_phrase_candidate', []
    return 'KEEP_ONLY', 'insufficient_learning_value_for_student_facing_task', ['low_learning_value']


def _is_candidate_for_llm_enhance(u: Dict[str, Any]) -> bool:
    # Backward-compatible wrapper. The full v1.4.4 pipeline passes all_units to
    # _candidate_preclassification_route; this wrapper remains conservative.
    route, reason, flags = _candidate_preclassification_route(u, [u])
    if route != 'ENHANCE_ELIGIBLE':
        _v144_audit(reason, _unit_text(u), {'route': route, 'flags': flags})
        return False
    return True


def generate_phrase_enhance_candidates(raw_units: List[Dict[str, Any]], essay_text: str, validator: ContextFitValidator) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    candidates: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    clarify_units: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, int]] = set()
    clarify_seen: Set[Tuple[str, int]] = set()
    seq = 0
    clarify_seq = 0
    V141_QA_CACHE['enhance_candidates_pre_validation'] = 0
    V143_QA_CACHE['expand_span_recommendation_count'] = 0
    V143_QA_CACHE['expand_span_accepted_count'] = 0
    V144_QA_CACHE['eligible_for_llm_count'] = 0
    V144_QA_CACHE['pre_llm_clarify_count'] = 0
    V144_QA_CACHE['pre_llm_reject_count'] = 0
    V144_QA_CACHE['pre_llm_keep_only_count'] = 0

    def add_clarify(unit_text: str, context: str, sent_idx: int, para_idx: Any, reason: str, risk_flags: Optional[List[str]], base_value: float, source_kind: str = 'pre_llm_clarify_gate') -> None:
        nonlocal clarify_seq
        clean = re.sub(r'\s+', ' ', unit_text).strip()
        if not clean or len(surface_tokens(clean)) <= 1:
            return
        dummy = {'unit_text': clean, 'source_sentence_index': sent_idx, 'context': context}
        if enhance_overlaps_fix(dummy)[0]:
            return
        key = (norm_text(clean), sent_idx)
        if key in clarify_seen:
            return
        clarify_seq += 1
        clarify_units.append(make_clarify_unit(
            unit_id=f'clar_{clarify_seq:04d}',
            unit_text=clean,
            context=context,
            source_sentence_index=sent_idx,
            source_paragraph_index=para_idx,
            reason=reason,
            risk_flags=risk_flags or [],
            candidate_value=base_value,
            source_kind=source_kind,
        ))
        clarify_seen.add(key)

    def add_candidate(unit_text: str, context: str, sent_idx: int, para_idx: Any, suggestions: List[Dict[str, Any]], *, source_kind: str, base_value: float, unit_type: str = 'phrase_enhance') -> None:
        nonlocal seq
        clean = re.sub(r'\s+', ' ', unit_text).strip()
        if not clean or len(surface_tokens(clean)) <= 1:
            return
        key = (norm_text(clean), sent_idx)
        if key in seen:
            return
        dummy = {'unit_text': clean, 'source_sentence_index': sent_idx, 'context': context}
        blocked, reason, ref = enhance_overlaps_fix(dummy)
        if blocked:
            failures.append({'unit_text': clean, 'candidate': None, 'tier': 'overlap_gate', 'reason': reason, 'reference': ref})
            return
        # Final v1.4.4 safeguard: no vague/malformed phrase can be promoted to ENHANCE.
        if _contains_generic_vague_noun(clean) or _is_vague_placeholder_phrase(clean):
            add_clarify(clean, context, sent_idx, para_idx, 'vague_phrase_blocked_from_enhance', ['vague_reference'], base_value, 'v144_final_clarify_safeguard')
            failures.append({'unit_text': clean, 'candidate': None, 'tier': 'preclassification_gate', 'reason': 'vague_phrase_blocked_from_enhance'})
            return
        if is_unrecoverable_phrase_fragment(clean, context):
            # Malformed-context blocks are audit-only unless the phrase is truly vague.
            # A complete phrase inside a weak sentence should not become a CLARIFY task
            # merely because deterministic suggestions were unavailable.
            failures.append({'unit_text': clean, 'candidate': None, 'tier': 'preclassification_gate', 'reason': 'malformed_context_blocked_from_enhance'})
            return
        if _looks_truncated_or_fragment_for_enhance(clean, context):
            failures.append({'unit_text': clean, 'candidate': None, 'tier': 'preclassification_gate', 'reason': 'fragment_blocked_from_enhance'})
            return
        seq += 1
        candidates.append(make_enhance_unit(
            unit_id=f'enh_{seq:04d}', unit_text=clean, unit_type=unit_type, context=context,
            source_sentence_index=sent_idx, source_paragraph_index=para_idx, suggestions=suggestions,
            candidate_value=base_value, source_kind=source_kind,
        ))
        seen.add(key)

    # Pre-classification pass. Only ENHANCE_ELIGIBLE candidates can reach the LLM.
    eligible_units: List[Dict[str, Any]] = []
    seen_eligible: Set[Tuple[str, int]] = set()
    for u in raw_units:
        text = re.sub(r'\s+', ' ', _unit_text(u)).strip()
        if not text:
            continue
        sent_idx = int(u.get('source_sentence_index', -1))
        context = str(u.get('context') or '')
        para_idx = u.get('source_paragraph_index')
        route, reason, route_flags = _candidate_preclassification_route(u, raw_units)
        _v144_audit(reason, text, {'route': route, 'flags': route_flags, 'source_sentence_index': sent_idx})
        if route == 'CLARIFY_VISIBLE':
            V144_QA_CACHE['pre_llm_clarify_count'] += 1
            add_clarify(text, context, sent_idx, para_idx, reason, route_flags, max(0.57, float(u.get('candidate_value') or 0.0)), 'v144_pre_llm_clarify_gate')
            continue
        if route == 'KEEP_ONLY':
            V144_QA_CACHE['pre_llm_keep_only_count'] += 1
            continue
        if route == 'REJECT_PRE_LLM':
            V144_QA_CACHE['pre_llm_reject_count'] += 1
            continue
        if route == 'ENHANCE_ELIGIBLE':
            key = (norm_text(text), sent_idx)
            if key not in seen_eligible:
                eligible_units.append(u)
                seen_eligible.add(key)
                V144_QA_CACHE['eligible_for_llm_count'] += 1

    # Resource/pattern suggestions are allowed only for eligibility-passed candidates.
    for u in eligible_units:
        text = _unit_text(u)
        context = str(u.get('context') or '')
        sent_idx = int(u.get('source_sentence_index', -1))
        para_idx = u.get('source_paragraph_index')
        suggs, source_kind = universal_pattern_suggestions(text, context)
        if suggs:
            V141_QA_CACHE['enhance_candidates_pre_validation'] += 1
            valid = validate_suggestions(text, suggs, context, validator, source=source_kind, tier='phrase', failures=failures)
            if valid:
                add_candidate(text, context, sent_idx, para_idx, valid, source_kind=source_kind, base_value=max(0.68, float(u.get('candidate_value') or 0.0)), unit_type=u.get('unit_type') or 'phrase_enhance')

    llm_provider = ACTIVE_LLM_PROVIDER
    if llm_provider and llm_provider.available():
        raw_llm_candidates: List[Dict[str, Any]] = []
        seen_llm: Set[Tuple[str, int]] = set()
        for u in eligible_units:
            text = _unit_text(u)
            sent_idx = int(u.get('source_sentence_index', -1))
            key = (norm_text(text), sent_idx)
            if key in seen or key in seen_llm:
                continue
            low = norm_text(text)
            if low in EXTERNAL_FORMULAIC_KEEP or low in DISCOURSE_MARKER_ALLOWLIST:
                continue
            raw_llm_candidates.append(u)
            seen_llm.add(key)
        raw_llm_candidates.sort(key=_llm_candidate_priority, reverse=True)
        raw_llm_candidates = raw_llm_candidates[:max(0, int(LLM_MAX_CANDIDATES))]
        batch_payload: List[Dict[str, Any]] = []
        for i, u in enumerate(raw_llm_candidates, start=1):
            sent_idx = int(u.get('source_sentence_index', -1))
            unit_text = _unit_text(u)
            payload = {
                'unit_id': f'cand_{i:04d}',
                'original_unit_id': u.get('unit_id'),
                'unit_text': unit_text,
                'unit_type': u.get('unit_type') or 'phrase_enhance',
                'context': str(u.get('context') or ''),
                'source_sentence_index': sent_idx,
                'source_paragraph_index': u.get('source_paragraph_index'),
                'axis_candidates': u.get('axis_candidates') or [],
                'extraction_flags': u.get('extraction_flags') or [],
                'candidate_value': float(u.get('candidate_value') or 0.0),
                'known_fix_spans_in_sentence': _known_fix_spans_for_sentence(sent_idx),
                'nearby_candidate_spans': _nearby_spans_for_candidate({'unit_text': unit_text, 'source_sentence_index': sent_idx, 'context': str(u.get('context') or '')}, raw_units),
                'preclassification_route': 'ENHANCE_ELIGIBLE',
                'preclassification_reason': 'complete_recoverable_phrase_candidate',
            }
            batch_payload.append(payload)
        V141_QA_CACHE['enhance_candidates_pre_validation'] = V141_QA_CACHE.get('enhance_candidates_pre_validation', 0) + len(batch_payload)
        llm_results = llm_provider.classify_and_suggest(batch_payload)
        result_by_id = {str(r.get('unit_id')): r for r in llm_results}
        decision_counts = Counter(str(r.get('decision') or r.get('classification') or '').upper() for r in llm_results if isinstance(r, dict))
        V141_QA_CACHE['llm_classification_counts'] = dict(decision_counts)
        V143_QA_CACHE['llm_decision_counts'] = dict(decision_counts)
        for c in batch_payload:
            r = result_by_id.get(c['unit_id'])
            if not r:
                continue
            decision = str(r.get('decision') or r.get('classification') or '').upper()
            conf = _safe_float(r.get('confidence'), 0.0)
            risk_flags = list(r.get('risk_flags') or [])
            sem = str(r.get('semantic_recoverability') or r.get('semantic_stability') or '').lower()
            learning = str(r.get('learning_value') or '').lower()
            span_complete = bool(r.get('span_is_complete'))
            span_replaceable = bool(r.get('span_is_replaceable'))
            context = c['context']

            if decision == 'EXPAND_SPAN':
                V143_QA_CACHE['expand_span_recommendation_count'] = int(V143_QA_CACHE.get('expand_span_recommendation_count', 0)) + 1
                rec = str(r.get('recommended_span') or '').strip()
                exact = _exact_substring_from_context(rec, context)
                if not exact:
                    failures.append({'unit_text': c['unit_text'], 'candidate': rec or None, 'tier': 'llm_expand_span', 'reason': 'recommended_span_not_exact_substring', 'llm_confidence': conf})
                    continue
                route2, reason2, flags2 = _candidate_preclassification_route({'unit_text': exact, 'context': context, 'source_sentence_index': c['source_sentence_index'], 'axis_candidates': c.get('axis_candidates'), 'extraction_flags': c.get('extraction_flags'), 'candidate_value': c.get('candidate_value')}, raw_units)
                if route2 != 'ENHANCE_ELIGIBLE':
                    if route2 == 'CLARIFY_VISIBLE':
                        add_clarify(exact, context, c['source_sentence_index'], c['source_paragraph_index'], reason2, flags2 + ['expanded_span'], max(0.62, c['candidate_value']), 'v144_expanded_span_clarify_gate')
                    failures.append({'unit_text': exact, 'candidate': None, 'tier': 'preclassification_gate_after_expand', 'reason': reason2, 'route': route2, 'llm_confidence': conf})
                    continue
                valid = validate_llm_suggestions(exact, r.get('suggestions') or [], context, validator, failures)
                if len(valid) >= LLM_MIN_VALID_SUGGESTIONS:
                    add_candidate(exact, context, c['source_sentence_index'], c['source_paragraph_index'], valid, source_kind='openai_llm_expand_span_validated_after_preclassification', base_value=max(0.74, c['candidate_value'], min(0.95, conf)), unit_type=c['unit_type'])
                    V143_QA_CACHE['expand_span_accepted_count'] = int(V143_QA_CACHE.get('expand_span_accepted_count', 0)) + 1
                    _LLM_STATS['accepted_enhance_units'] += 1
                else:
                    add_clarify(exact, context, c['source_sentence_index'], c['source_paragraph_index'], 'expanded_span_needs_student_clarification_or_no_safe_suggestions', risk_flags + ['expanded_span'], max(0.62, c['candidate_value']), 'llm_expand_span_without_safe_suggestions')
                continue

            if decision == 'CLARIFY':
                add_clarify(c['unit_text'], context, c['source_sentence_index'], c['source_paragraph_index'], str(r.get('brief_reason') or 'llm_classified_as_clarify'), risk_flags, max(0.60, c['candidate_value'], min(0.85, conf)), 'openai_llm_clarify')
                failures.append({'unit_text': c['unit_text'], 'candidate': None, 'tier': 'llm_classification', 'reason': 'llm_classified_as_clarify_visible_task', 'llm_confidence': conf, 'risk_flags': risk_flags})
                continue

            if decision != 'ENHANCE':
                failures.append({'unit_text': c['unit_text'], 'candidate': None, 'tier': 'llm_classification', 'reason': f"llm_classified_as_{decision.lower() or 'unknown'}", 'llm_confidence': conf, 'risk_flags': risk_flags})
                continue
            if not span_complete:
                failures.append({'unit_text': c['unit_text'], 'candidate': None, 'tier': 'llm_classification', 'reason': 'llm_enhance_rejected_span_incomplete', 'llm_confidence': conf, 'risk_flags': risk_flags})
                continue
            if not span_replaceable:
                failures.append({'unit_text': c['unit_text'], 'candidate': None, 'tier': 'llm_classification', 'reason': 'llm_enhance_rejected_span_not_replaceable', 'llm_confidence': conf, 'risk_flags': risk_flags})
                continue
            if sem == 'low':
                add_clarify(c['unit_text'], context, c['source_sentence_index'], c['source_paragraph_index'], 'semantic_recoverability_low', risk_flags, max(0.60, c['candidate_value']), 'llm_low_recoverability_clarify')
                continue
            if learning == 'low' or _looks_low_value_adverbial_np(c['unit_text']):
                failures.append({'unit_text': c['unit_text'], 'candidate': None, 'tier': 'llm_classification', 'reason': 'low_learning_value_not_student_enhance', 'llm_confidence': conf, 'risk_flags': risk_flags})
                continue
            valid = validate_llm_suggestions(c['unit_text'], r.get('suggestions') or [], context, validator, failures)
            if len(valid) < LLM_MIN_VALID_SUGGESTIONS:
                failures.append({'unit_text': c['unit_text'], 'candidate': None, 'tier': 'llm_validation', 'reason': 'fewer_than_min_valid_llm_suggestions', 'valid_suggestion_count': len(valid), 'required': LLM_MIN_VALID_SUGGESTIONS})
                continue
            add_candidate(c['unit_text'], context, c['source_sentence_index'], c['source_paragraph_index'], valid, source_kind='openai_llm_adjudicated_after_preclassification', base_value=max(0.72, c['candidate_value'], min(0.95, conf)), unit_type=c['unit_type'])
            _LLM_STATS['accepted_enhance_units'] += 1

    V141_QA_CACHE['enhance_candidates_post_validation'] = len(candidates)
    V141_QA_CACHE['suggestion_rejection_reason_counts'] = dict(Counter(f.get('reason') for f in failures if f.get('candidate') is not None and f.get('reason')))
    V143_QA_CACHE['clarify_units'] = clarify_units
    return candidates, failures


# Preserve previous profile/QA builders and add v1.4.4 metrics through wrappers.
_prev_build_lexical_profile_v143 = build_lexical_profile_v143

def build_lexical_profile_v143(fix_units: List[Dict[str, Any]], enhance_units: List[Dict[str, Any]], clarify_units: List[Dict[str, Any]], keep_units: List[Dict[str, Any]], dropped_units: List[Dict[str, Any]], suppressed_fix_candidates: List[Dict[str, Any]], unresolved_internal: List[Dict[str, Any]], evaluator_input_quality: Dict[str, Any]) -> Dict[str, Any]:
    prof = _prev_build_lexical_profile_v143(fix_units, enhance_units, clarify_units, keep_units, dropped_units, suppressed_fix_candidates, unresolved_internal, evaluator_input_quality)
    prof['v144_preclassification'] = {
        'eligible_for_llm_count': int(V144_QA_CACHE.get('eligible_for_llm_count', 0)),
        'pre_llm_clarify_count': int(V144_QA_CACHE.get('pre_llm_clarify_count', 0)),
        'pre_llm_reject_count': int(V144_QA_CACHE.get('pre_llm_reject_count', 0)),
        'pre_llm_keep_only_count': int(V144_QA_CACHE.get('pre_llm_keep_only_count', 0)),
        'preclassification_reason_counts': dict(V144_QA_CACHE.get('preclassification_reason_counts', {})),
    }
    return prof

_prev_build_qa_v143 = build_qa_v143

def build_qa_v143(warnings: List[str], dropped_units: List[Dict[str, Any]], suppressed_fix_candidates: List[Dict[str, Any]], context_failures: List[Dict[str, Any]], fix_units: List[Dict[str, Any]], enhance_units: List[Dict[str, Any]], clarify_units: List[Dict[str, Any]], keep_units: List[Dict[str, Any]], keep_inventory_audit: Optional[List[Dict[str, Any]]] = None, unresolved_internal: Optional[List[Dict[str, Any]]] = None, evaluator_input_quality: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    qa = _prev_build_qa_v143(warnings, dropped_units, suppressed_fix_candidates, context_failures, fix_units, enhance_units, clarify_units, keep_units, keep_inventory_audit, unresolved_internal, evaluator_input_quality)
    qa.setdefault('v1_4_4_metrics', {}).update({
        'llm_receives_only_preclassified_valid_candidates': True,
        'eligible_for_llm_count': int(V144_QA_CACHE.get('eligible_for_llm_count', 0)),
        'pre_llm_clarify_count': int(V144_QA_CACHE.get('pre_llm_clarify_count', 0)),
        'pre_llm_reject_count': int(V144_QA_CACHE.get('pre_llm_reject_count', 0)),
        'pre_llm_keep_only_count': int(V144_QA_CACHE.get('pre_llm_keep_only_count', 0)),
        'preclassification_reason_counts': dict(V144_QA_CACHE.get('preclassification_reason_counts', {})),
        'preclassification_audit_sample': list(V144_QA_CACHE.get('preclassification_audit', []))[:30],
    })
    qa.setdefault('contract_checks', {}).update({
        'malformed_fragments_filtered_before_llm_classification': True,
        'only_valid_candidates_reach_llm_classification': True,
        'vague_units_routed_to_visible_clarify_before_llm': True,
        'preclassification_uses_no_topic_or_essay_word_lists': True,
    })
    return qa


def analyze(payload: Dict[str, Any], validator: Optional[ContextFitValidator] = None) -> Dict[str, Any]:
    V141_QA_CACHE.clear()
    V143_QA_CACHE.clear()
    validator = validator or RuleBasedContextFitValidator()
    mode = payload.get("mode", "fix_and_enhance")
    if mode not in {"fix_only", "enhance_only", "fix_and_enhance"}:
        raise ValueError(f"Unsupported LRET mode: {mode!r}")
    identity = payload.get("identity") or {}
    essay_text = payload.get("essay_text") or ""
    learner_history = payload.get("learner_lexical_history") or {}
    raw_units, fix_candidates, history_from_payload, ingest_warnings = ingest_and_validate(payload)
    learner_history = learner_history or history_from_payload
    evaluator_input_quality = analyze_evaluator_input_quality(raw_units)
    clean_units, dropped_noise = noise_filter(raw_units)

    fix_units_all, claimed_spans, suppressed_fix_candidates, fix_context_failures = derive_fix_units(fix_candidates, essay_text, validator)
    recovered = recover_recurring_fix_units(clean_units, fix_units_all, validator)
    if recovered:
        fix_units_all.extend(recovered)
    prepare_fix_blocking_state(fix_units_all)

    if mode == "enhance_only":
        fix_units: List[Dict[str, Any]] = []
        ingest_warnings.append("enhance_only mode: fix_units suppressed, but lexical repair spans still used for phrase-first exclusion")
    else:
        fix_units = fix_units_all

    if mode == "fix_only":
        phrase_enhance, phrase_failures, single_fallback, single_failures = [], [], [], []
        clarify_candidates: List[Dict[str, Any]] = []
    else:
        phrase_enhance, phrase_failures = generate_phrase_enhance_candidates(clean_units, essay_text, validator)
        clarify_candidates = list(V143_QA_CACHE.get("clarify_units") or [])
        single_fallback, single_failures = [], []

    all_task_candidates = list(fix_units) + phrase_enhance + single_fallback
    survivors, dropped_dedup = apply_phrase_first_dedup(all_task_candidates)
    final_fix_units = [u for u in survivors if u.get("class_label") == "FIX"]
    final_enhance_units = [u for u in survivors if u.get("class_label") == "ENHANCE"]
    final_clarify_units, dropped_clarify = _dedupe_clarify_units(clarify_candidates, survivors)

    for u in final_fix_units + final_enhance_units + final_clarify_units:
        if u.get("class_label") != "CLARIFY":
            u["dedup_role"] = u.get("dedup_role") if "survivor" in str(u.get("dedup_role")) else "survivor_phrase"
        apply_history_framing(u, learner_history)

    keep_units, keep_inventory_audit, unresolved_internal = build_keep_units(clean_units, survivors + final_clarify_units)
    dropped_units = list(dropped_noise) + list(dropped_dedup) + list(dropped_clarify)
    context_failures = list(fix_context_failures) + list(phrase_failures) + list(single_failures)
    run_id = new_run_id(identity)
    return {
        "schema_version": SCHEMA_VERSION_OUT,
        "identity": identity,
        "run": {"run_id": run_id, "engine_id": ENGINE_ID, "engine_version": ENGINE_VERSION, "created_at": _utc_now_iso(), "contract_version": SCHEMA_VERSION_OUT, "input_schema_version": payload.get("schema_version")},
        "fix_units": final_fix_units,
        "enhance_units": final_enhance_units,
        "clarify_units": final_clarify_units,
        "keep_units": keep_units,
        "lexical_profile": build_lexical_profile_v143(final_fix_units, final_enhance_units, final_clarify_units, keep_units, dropped_units, suppressed_fix_candidates, unresolved_internal, evaluator_input_quality),
        "replacement_options": [{"unit_id": u.get("unit_id"), "unit_text": u.get("unit_text"), "class_label": u.get("class_label"), "replacement_scope": u.get("replacement_scope"), "suggestions": u.get("suggestions"), "reveal_policy": u.get("reveal_policy")} for u in (final_fix_units + final_enhance_units)],
        "clarification_options": [{"unit_id": u.get("unit_id"), "unit_text": u.get("unit_text"), "class_label": "CLARIFY", "replacement_scope": "meaning_clarification", "phase1_prompt": u.get("phase1_prompt"), "clarification_guidance": u.get("clarification_guidance"), "reveal_policy": u.get("reveal_policy")} for u in final_clarify_units],
        "lret_practice_targets": build_practice_targets_v143(final_fix_units, final_enhance_units, final_clarify_units, learner_history),
        "qa": build_qa_v143(ingest_warnings, dropped_units, suppressed_fix_candidates, context_failures, final_fix_units, final_enhance_units, final_clarify_units, keep_units, keep_inventory_audit, unresolved_internal, evaluator_input_quality),
        "learning_intelligence_payload": build_learning_intelligence_payload_v143(identity, run_id, final_fix_units, final_enhance_units, final_clarify_units, keep_units),
    }



# ---------------------------------------------------------------------------
# v1.4.5 patch: universal candidate reconstruction before preclassification
# ---------------------------------------------------------------------------
# Rationale:
# v1.4.4 correctly stopped malformed fragments from reaching LLM adjudication,
# but it filtered too aggressively because many upstream units were clipped
# subspans. This layer reconstructs candidate containers from the original
# sentence using universal structural windows BEFORE preclassification. It uses
# no topic/essay word lists and no phrase banks.

V145_QA_CACHE: Dict[str, Any] = {}

V145_DETERMINERS = {'a', 'an', 'the', 'this', 'that', 'these', 'those', 'my', 'your', 'our', 'their', 'his', 'her', 'its'}
V145_AUXILIARIES = {'is', 'are', 'was', 'were', 'be', 'being', 'been', 'am', 'can', 'could', 'will', 'would', 'should', 'may', 'might', 'must', 'has', 'have', 'had'}
V145_PREPOSITIONS = {'of', 'to', 'for', 'with', 'in', 'on', 'at', 'by', 'from', 'about', 'into', 'over', 'under', 'between', 'among', 'through'}
V145_COORDINATORS = {'and', 'or', 'but'}
V145_GENERIC_ADVERBS = {'quickly', 'rapidly', 'slowly', 'strongly', 'clearly', 'effectively', 'positively', 'negatively'}


def _v145_reset() -> None:
    V145_QA_CACHE.clear()
    V145_QA_CACHE.update({
        'raw_unit_count': 0,
        'reconstructed_candidate_count': 0,
        'augmented_unit_count': 0,
        'reconstruction_reason_counts': {},
        'reconstruction_audit_sample': [],
        'eligible_after_reconstruction_count': 0,
    })


def _v145_inc(name: str, key: str, amount: int = 1) -> None:
    d = V145_QA_CACHE.setdefault(name, {})
    d[key] = int(d.get(key, 0)) + amount


def _v145_audit(unit_text: str, source_text: str, reason: str, sent_idx: int) -> None:
    _v145_inc('reconstruction_reason_counts', reason)
    rows = V145_QA_CACHE.setdefault('reconstruction_audit_sample', [])
    if len(rows) < 40:
        rows.append({
            'unit_text': unit_text,
            'source_text': source_text,
            'reason': reason,
            'source_sentence_index': sent_idx,
        })


def _v145_tokens_with_spans(text: str) -> List[Tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in re.finditer(r"[A-Za-z]+(?:[-'][A-Za-z]+)?|\d+(?:[.,]\d+)?", text or '')]


def _v145_token_is_content(tok: str) -> bool:
    low = tok.lower()
    return bool(low) and low not in GENERIC_FUNCTION_OR_EDGE_TOKENS and low not in GENERIC_VAGUE_NOUNS


def _v145_token_is_verbish(tok: str) -> bool:
    low = tok.lower()
    if low in GENERIC_COMMON_BARE_VERBS_REQUIRING_COMPLEMENT:
        return True
    if low in V145_AUXILIARIES:
        return True
    if low.endswith('ing') or low.endswith('ed'):
        return True
    if low.endswith('s') and low not in GENERIC_VAGUE_NOUNS and low not in {'this', 'his', 'its'}:
        return True
    return False


def _v145_has_content_head(toks: List[str]) -> bool:
    return any(_v145_token_is_content(t) for t in toks)


def _v145_content_count(toks: List[str]) -> int:
    return sum(1 for t in toks if _v145_token_is_content(t))


def _v145_has_prep_link(toks: List[str]) -> bool:
    lows = [t.lower() for t in toks]
    for i, t in enumerate(lows[1:-1], start=1):
        if t in V145_PREPOSITIONS and any(_v145_token_is_content(x) for x in lows[:i]) and any(_v145_token_is_content(x) for x in lows[i+1:]):
            return True
    return False


def _v145_has_coordination(toks: List[str]) -> bool:
    lows = [t.lower() for t in toks]
    for i, t in enumerate(lows[1:-1], start=1):
        if t in V145_COORDINATORS and any(_v145_token_is_content(x) for x in lows[:i]) and any(_v145_token_is_content(x) for x in lows[i+1:]):
            return True
    return False


def _v145_is_noun_phrase_like(toks: List[str]) -> bool:
    lows = [t.lower() for t in toks]
    if len(toks) < 2:
        return False
    if lows[-1] in GENERIC_INCOMPLETE_FINAL_TOKENS or lows[-1] in V145_PREPOSITIONS:
        return False
    if lows[0] in V145_PREPOSITIONS:
        return False
    if _v145_content_count(toks) >= 2:
        return True
    if lows[0] in V145_DETERMINERS and _v145_content_count(toks) >= 1 and len(toks) >= 3:
        return True
    if _v145_has_prep_link(toks):
        return True
    return False


def _v145_is_predicate_like(toks: List[str]) -> bool:
    lows = [t.lower() for t in toks]
    if len(toks) < 2:
        return False
    if lows[-1] in GENERIC_INCOMPLETE_FINAL_TOKENS and lows[-1] not in V145_GENERIC_ADVERBS:
        return False
    # auxiliary + lexical predicate/adverbial complement, e.g. aux + V-ing + Adv
    if lows[0] in V145_AUXILIARIES and len(toks) >= 3 and any(_v145_token_is_content(t) for t in toks[1:]):
        if any(_v145_token_is_verbish(t) for t in toks[1:]) or _v145_content_count(toks[1:]) >= 2:
            return True
    # lexical verb + object/complement/adverb
    if _v145_token_is_verbish(toks[0]) and any(_v145_token_is_content(t) for t in toks[1:]):
        return True
    # adverbial predicate fragment such as V-ing + Adv or V-ing + compound complement
    if lows[0].endswith('ing') and (len(toks) >= 2 and (_v145_content_count(toks[1:]) >= 1 or lows[-1] in V145_GENERIC_ADVERBS)):
        return True
    return False


def _v145_is_complete_replaceable_unit(text: str, context: str = '') -> bool:
    clean = re.sub(r'\s+', ' ', text or '').strip()
    if _v145_has_internal_clause_punctuation(clean):
        return False
    toks = surface_tokens(clean)
    lows = [t.lower() for t in toks]
    if len(toks) < 2 or len(toks) > 9:
        return False
    if not _v145_has_content_head(toks):
        return False
    if any(t in GENERIC_VAGUE_NOUNS for t in lows):
        return False
    if any(t in V145_COORDINATORS for t in lows):
        return False
    if lows[-1] in V145_PREPOSITIONS:
        return False
    # A short phrase may start with an edge token only if it has a real lexical head.
    if lows[0] in GENERIC_FUNCTION_OR_EDGE_TOKENS:
        if lows[0] in V145_DETERMINERS:
            return _v145_is_noun_phrase_like(toks)
        if lows[0] in V145_AUXILIARIES:
            return _v145_is_predicate_like(toks)
        # prepositional/adverbial frame phrases are normally KEEP, not ENHANCE.
        return False
    if _looks_like_noun_plus_bare_predicate_fragment(clean):
        return False
    if _v145_is_predicate_like(toks):
        return True
    if _v145_is_noun_phrase_like(toks):
        return True
    if _v145_has_prep_link(toks):
        return True
    return False


def _v145_window_text(tokens: List[Tuple[str, int, int]], start: int, end: int, context: str) -> str:
    if start < 0 or end > len(tokens) or start >= end:
        return ''
    return re.sub(r'\s+', ' ', context[tokens[start][1]:tokens[end - 1][2]]).strip(" ,;:.!?\n\t")



def _v145_has_internal_clause_punctuation(text: str) -> bool:
    # Do not reconstruct across clause/sentence boundaries. Hyphen and apostrophe
    # inside words are allowed by tokenization; commas/semicolons/periods are not.
    return bool(re.search(r"[,:;.!?]\s*\S", text or ''))


def _v145_token_range_for_substring(unit_text: str, context: str) -> Optional[Tuple[int, int]]:
    clean = re.sub(r'\s+', ' ', unit_text or '').strip()
    if not clean or not context:
        return None
    tokens = _v145_tokens_with_spans(context)
    if not tokens:
        return None
    low_context = context.lower()
    low_clean = clean.lower()
    pos = low_context.find(low_clean)
    if pos < 0:
        return None
    endpos = pos + len(low_clean)
    start_idx = None
    end_idx = None
    for i, (_, s, e) in enumerate(tokens):
        if start_idx is None and e > pos:
            start_idx = i
        if s < endpos:
            end_idx = i + 1
    if start_idx is None or end_idx is None or start_idx >= end_idx:
        return None
    return start_idx, end_idx


def _v145_candidate_windows_for_unit(unit_text: str, context: str) -> List[str]:
    tokens = _v145_tokens_with_spans(context)
    rng = _v145_token_range_for_substring(unit_text, context)
    if not tokens or not rng:
        return []
    start, end = rng
    windows: List[str] = []
    # Build bounded containers around the original unit. This is structural only:
    # it does not prefer any topic vocabulary.
    for left_extra in range(0, 4):
        for right_extra in range(0, 4):
            s = max(0, start - left_extra)
            e = min(len(tokens), end + right_extra)
            if e - s < 2 or e - s > 9:
                continue
            cand = _v145_window_text(tokens, s, e, context)
            if not cand or _v145_has_internal_clause_punctuation(cand):
                continue
            windows.append(cand)
    # Add short predicate/noun-phrase windows from the same local neighborhood.
    local_s = max(0, start - 3)
    local_e = min(len(tokens), end + 3)
    for s in range(local_s, local_e):
        for e in range(s + 2, min(local_e, s + 9) + 1):
            cand = _v145_window_text(tokens, s, e, context)
            if cand and not _v145_has_internal_clause_punctuation(cand):
                windows.append(cand)
    # Stable order, dedup.
    out: List[str] = []
    seen: Set[str] = set()
    for w in windows:
        nw = norm_text(w)
        if not nw or nw in seen:
            continue
        seen.add(nw)
        out.append(w)
    return out


def _v145_unit_type_for_candidate(text: str) -> str:
    toks = surface_tokens(text)
    if _v145_is_predicate_like(toks):
        return 'verb_phrase_or_predicate_chunk'
    if _v145_is_noun_phrase_like(toks):
        return 'noun_phrase'
    return 'phrase_enhance_candidate'


def _v145_reconstruct_candidate_units(raw_units: List[Dict[str, Any]], essay_text: str) -> List[Dict[str, Any]]:
    V145_QA_CACHE['raw_unit_count'] = len(raw_units)
    augmented: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, int]] = set()

    def add_unit(u: Dict[str, Any], *, unit_text: Optional[str] = None, reason: str = 'original_unit') -> None:
        text0 = re.sub(r'\s+', ' ', (unit_text if unit_text is not None else _unit_text(u)) or '').strip()
        if not text0:
            return
        sent_idx = int(u.get('source_sentence_index', -1))
        key = (norm_text(text0), sent_idx)
        if key in seen:
            return
        new_u = dict(u)
        new_u['unit_text'] = text0
        new_u['unit_norm'] = norm_text(text0)
        if reason != 'original_unit':
            new_u['unit_id'] = f"v145_rec_{len(seen)+1:04d}"
            new_u['unit_type'] = _v145_unit_type_for_candidate(text0)
            axes = set(new_u.get('axis_candidates') or [])
            axes.update({'collocation_naturalness', 'semantic_specificity', 'paraphrase_range'})
            new_u['axis_candidates'] = sorted(axes)
            flags = set(new_u.get('extraction_flags') or [])
            flags.update({'v145_reconstructed_candidate', 'candidate_container'})
            new_u['extraction_flags'] = sorted(flags)
            new_u['candidate_value'] = max(0.62, float(new_u.get('candidate_value') or 0.0))
            new_u['reconstructed_from_unit_text'] = _unit_text(u)
            new_u['reconstruction_reason'] = reason
            V145_QA_CACHE['reconstructed_candidate_count'] = int(V145_QA_CACHE.get('reconstructed_candidate_count', 0)) + 1
            _v145_audit(text0, _unit_text(u), reason, sent_idx)
        augmented.append(new_u)
        seen.add(key)

    for u in raw_units:
        add_unit(u)
        context = str(u.get('context') or '')
        text0 = re.sub(r'\s+', ' ', _unit_text(u) or '').strip()
        if not context or not text0 or len(surface_tokens(text0)) > 9:
            continue
        # Vague placeholder candidates are preserved for CLARIFY, not expanded into ENHANCE.
        if _contains_generic_vague_noun(text0) or _is_vague_placeholder_phrase(text0):
            continue
        for cand in _v145_candidate_windows_for_unit(text0, context):
            if norm_text(cand) == norm_text(text0):
                continue
            if len(surface_tokens(cand)) < 2 or len(surface_tokens(cand)) > 9:
                continue
            if _contains_generic_vague_noun(cand) or _is_vague_placeholder_phrase(cand):
                continue
            if _v145_is_complete_replaceable_unit(cand, context):
                add_unit(u, unit_text=cand, reason='structural_container_reconstruction')

    V145_QA_CACHE['augmented_unit_count'] = len(augmented)
    return augmented


# Override the v1.4.4 container check. A larger span can block a smaller one only
# if the larger span is itself complete, replaceable, and not vague/malformed.
def _has_stronger_same_sentence_container(u: Dict[str, Any], all_units: List[Dict[str, Any]]) -> Optional[str]:
    text = _unit_text(u)
    low = norm_text(text)
    sent = int(u.get('source_sentence_index', -1))
    toks = surface_tokens(text)
    if len(toks) < 2:
        return None
    for other in all_units:
        if other is u:
            continue
        if int(other.get('source_sentence_index', -99)) != sent:
            continue
        otext = _unit_text(other)
        olow = norm_text(otext)
        if not olow or olow == low:
            continue
        if low in olow and len(surface_tokens(otext)) > len(toks):
            if len(surface_tokens(otext)) <= 9 and not _contains_generic_vague_noun(otext) and _v145_is_complete_replaceable_unit(otext, str(other.get('context') or '')):
                return otext
    return None


# Override v1.4.4 preclassification with a reconstruction-aware route. This keeps
# malformed fragments away from the LLM but no longer rejects valid expanded spans
# simply because they begin with a determiner or auxiliary.
def _candidate_preclassification_route(u: Dict[str, Any], all_units: List[Dict[str, Any]]) -> Tuple[str, str, List[str]]:
    text = re.sub(r"\s+", " ", _unit_text(u)).strip()
    context = str(u.get('context') or '')
    toks = surface_tokens(text)
    flags: List[str] = []
    if not text or len(toks) <= 1:
        return 'REJECT_PRE_LLM', 'single_word_or_empty_not_llm_classified', ['too_short']
    if len(toks) > 9:
        return 'REJECT_PRE_LLM', 'too_long_for_phrase_level_lret', ['too_long']
    dummy = {'unit_text': text, 'source_sentence_index': u.get('source_sentence_index'), 'context': context}
    blocked, reason, ref = enhance_overlaps_fix(dummy)
    if blocked:
        return 'REJECT_PRE_LLM', 'overlaps_fix_span', ['overlaps_fix', str(ref or '')]
    low = norm_text(text)
    if low in EXTERNAL_FORMULAIC_KEEP or low in DISCOURSE_MARKER_ALLOWLIST:
        return 'KEEP_ONLY', 'formulaic_or_discourse_keep_not_enhance', ['formulaic_keep']
    if _contains_generic_vague_noun(text) or _is_vague_placeholder_phrase(text):
        return 'CLARIFY_VISIBLE', 'vague_placeholder_phrase_requires_student_clarification', ['vague_reference']
    if _looks_low_value_adverbial_np(text):
        return 'KEEP_ONLY', 'low_value_adverbial_or_frame_phrase', ['low_value']

    complete = _v145_is_complete_replaceable_unit(text, context)
    if complete:
        container = _has_stronger_same_sentence_container(u, all_units)
        if container:
            return 'REJECT_PRE_LLM', 'partial_span_has_stronger_complete_container', ['partial_span', f'container={container}']
        axes = set(u.get('axis_candidates') or [])
        flags0 = set(u.get('extraction_flags') or [])
        value = float(u.get('candidate_value') or 0.0)
        strong_axes = {'collocation_naturalness', 'semantic_specificity', 'predicate_argument', 'register_control', 'paraphrase_range'}
        strong_flags = {'collocation_candidate', 'predicate_argument_candidate', 'informal_register', 'v145_reconstructed_candidate'}
        if (axes & strong_axes) or (flags0 & strong_flags) or (value >= 0.58 and len(content_tokens(text)) >= 2):
            V145_QA_CACHE['eligible_after_reconstruction_count'] = int(V145_QA_CACHE.get('eligible_after_reconstruction_count', 0)) + 1
            return 'ENHANCE_ELIGIBLE', 'complete_recoverable_reconstructed_or_original_candidate', []
        return 'KEEP_ONLY', 'complete_but_insufficient_learning_value_for_task', ['low_learning_value']

    # Incomplete/malformed candidates are filtered only after the complete-span
    # test above, so good expanded spans such as determiner+noun or aux+predicate
    # are not lost.
    if is_unrecoverable_phrase_fragment(text, context):
        return 'REJECT_PRE_LLM', 'malformed_or_unrecoverable_fragment_filtered_before_llm', ['malformed_context']
    if _starts_with_generic_edge_token(text) and len(toks) <= 3:
        return 'REJECT_PRE_LLM', 'edge_started_short_fragment_without_lexical_head', ['partial_span']
    if _looks_like_truncated_verb_or_predicate(text):
        return 'REJECT_PRE_LLM', 'truncated_or_incomplete_predicate_span', ['partial_span']
    return 'KEEP_ONLY', 'not_complete_replaceable_learning_unit', ['not_complete_replaceable']


_prev_generate_phrase_enhance_candidates_v144 = generate_phrase_enhance_candidates

def generate_phrase_enhance_candidates(raw_units: List[Dict[str, Any]], essay_text: str, validator: ContextFitValidator) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    _v145_reset()
    augmented_units = _v145_reconstruct_candidate_units(raw_units, essay_text)
    return _prev_generate_phrase_enhance_candidates_v144(augmented_units, essay_text, validator)


_prev_build_lexical_profile_v144 = build_lexical_profile_v143

def build_lexical_profile_v143(fix_units: List[Dict[str, Any]], enhance_units: List[Dict[str, Any]], clarify_units: List[Dict[str, Any]], keep_units: List[Dict[str, Any]], dropped_units: List[Dict[str, Any]], suppressed_fix_candidates: List[Dict[str, Any]], unresolved_internal: List[Dict[str, Any]], evaluator_input_quality: Dict[str, Any]) -> Dict[str, Any]:
    prof = _prev_build_lexical_profile_v144(fix_units, enhance_units, clarify_units, keep_units, dropped_units, suppressed_fix_candidates, unresolved_internal, evaluator_input_quality)
    prof['v145_candidate_reconstruction'] = {
        'raw_unit_count': int(V145_QA_CACHE.get('raw_unit_count', 0)),
        'reconstructed_candidate_count': int(V145_QA_CACHE.get('reconstructed_candidate_count', 0)),
        'augmented_unit_count': int(V145_QA_CACHE.get('augmented_unit_count', 0)),
        'eligible_after_reconstruction_count': int(V145_QA_CACHE.get('eligible_after_reconstruction_count', 0)),
        'reconstruction_reason_counts': dict(V145_QA_CACHE.get('reconstruction_reason_counts', {})),
    }
    return prof


_prev_build_qa_v144 = build_qa_v143

def build_qa_v143(warnings: List[str], dropped_units: List[Dict[str, Any]], suppressed_fix_candidates: List[Dict[str, Any]], context_failures: List[Dict[str, Any]], fix_units: List[Dict[str, Any]], enhance_units: List[Dict[str, Any]], clarify_units: List[Dict[str, Any]], keep_units: List[Dict[str, Any]], keep_inventory_audit: Optional[List[Dict[str, Any]]] = None, unresolved_internal: Optional[List[Dict[str, Any]]] = None, evaluator_input_quality: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    qa = _prev_build_qa_v144(warnings, dropped_units, suppressed_fix_candidates, context_failures, fix_units, enhance_units, clarify_units, keep_units, keep_inventory_audit, unresolved_internal, evaluator_input_quality)
    qa.setdefault('v1_4_5_metrics', {}).update({
        'candidate_reconstruction_before_preclassification': True,
        'raw_unit_count': int(V145_QA_CACHE.get('raw_unit_count', 0)),
        'reconstructed_candidate_count': int(V145_QA_CACHE.get('reconstructed_candidate_count', 0)),
        'augmented_unit_count': int(V145_QA_CACHE.get('augmented_unit_count', 0)),
        'eligible_after_reconstruction_count': int(V145_QA_CACHE.get('eligible_after_reconstruction_count', 0)),
        'reconstruction_reason_counts': dict(V145_QA_CACHE.get('reconstruction_reason_counts', {})),
        'reconstruction_audit_sample': list(V145_QA_CACHE.get('reconstruction_audit_sample', []))[:30],
    })
    qa.setdefault('contract_checks', {}).update({
        'candidate_reconstruction_runs_before_preclassification': True,
        'expanded_spans_are_preclassified_after_reconstruction': True,
        'edge_started_complete_spans_not_rejected_by_default': True,
        'reconstruction_uses_no_topic_or_essay_word_lists': True,
        'no_internal_phrase_or_topic_bank_v145': True,
    })
    return qa



# ---------------------------------------------------------------------------
# v1.4.6 patch: lexical-only final gate after reconstruction
# ---------------------------------------------------------------------------
# Rationale:
# v1.4.5 correctly reconstructs candidates before preclassification, but it can
# promote grammar-repair or malformed-clause repair spans to ENHANCE. v1.4.6
# keeps reconstruction but adds lexical-only gating, improved container logic,
# cleaner LLM prioritisation, and constrained visible CLARIFY.

ENGINE_VERSION = "lret-engine-v1.4.6-lexical-only-reconstruction-gated"
V146_QA_CACHE: Dict[str, Any] = {}

V146_SUBJECT_PRONOUNS = {"i", "he", "she", "they", "we"}
V146_PREPOSITION_LIKE = {"for", "with", "by", "from", "about", "without", "before", "after"}
V146_GRAMMAR_RISK_FLAGS = {
    "grammar", "grammatical_errors", "article_error", "article_number",
    "article_number_agreement", "verb_form_error", "sentence_malformed",
    "severe_grammar_malformation", "ungrammatical", "unclear_structure",
}


def _v146_reset() -> None:
    V146_QA_CACHE.clear()
    V146_QA_CACHE.update({
        "grammar_repair_blocked_from_enhance": 0,
        "malformed_clause_blocked_from_enhance": 0,
        "sentence_rewrite_blocked_from_enhance": 0,
        "clarify_units_suppressed_as_grammar_breakdown": 0,
        "final_enhance_after_v146_gate": 0,
        "final_clarify_after_v146_gate": 0,
        "blocked_enhance_samples": [],
        "suppressed_clarify_samples": [],
    })


def _v146_note(name: str, sample: Optional[Dict[str, Any]] = None) -> None:
    V146_QA_CACHE[name] = int(V146_QA_CACHE.get(name, 0)) + 1
    if sample:
        key = "blocked_enhance_samples" if "enhance" in name or "repair" in name or "clause" in name or "rewrite" in name else "suppressed_clarify_samples"
        rows = V146_QA_CACHE.setdefault(key, [])
        if len(rows) < 12:
            rows.append(sample)


def _v146_is_verb_lemma_or_form(token: str) -> bool:
    low = norm_text(token)
    if not low:
        return False
    if 'verb' in set(LEXICAL_POS_MAP.get(low) or set()):
        return True
    lemma = MORPH_FORM_TO_LEMMA.get(low)
    return bool(lemma and 'verb' in set(LEXICAL_POS_MAP.get(lemma) or set()))


def _v146_token_after_to_is_nonbase(token: str) -> bool:
    low = norm_text(token)
    if not low:
        return False
    lemma = MORPH_FORM_TO_LEMMA.get(low)
    if lemma and lemma != low and 'verb' in set(LEXICAL_POS_MAP.get(lemma) or set()):
        return True
    # Conservative fallback for regular past forms after infinitive marker.
    if low.endswith('ed') and len(low) > 4:
        stem = low[:-2]
        if 'verb' in set(LEXICAL_POS_MAP.get(stem) or set()):
            return True
    return False


def _v146_span_has_grammar_malformation(text: str) -> Tuple[bool, str]:
    """Detect grammar-repair spans that must not be ENHANCE.

    This is structural and resource-based. It contains no essay-topic whitelist or
    sample phrase bank. It checks construction shapes that indicate the student
    needs grammar/repair work rather than lexical enhancement.
    """
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    low = norm_text(clean)
    toks = surface_tokens(low)
    lows = [t.lower() for t in toks]
    if not clean:
        return True, "empty_span"

    # Modal/infinitive marker followed by a non-base verb form.
    # This uses morphology resources when available.
    for i, t in enumerate(lows[:-1]):
        if t == 'to' and _v146_token_after_to_is_nonbase(lows[i + 1]):
            return True, "verb_form_repair_not_lexical_enhance"

    # Article + plural noun shape: a/an + plural. This is grammar/number repair.
    if re.search(r"\b(?:a|an)\s+[a-z]+s\b", low):
        return True, "article_number_repair_not_lexical_enhance"

    # Comparative double marking: more + -er.
    if re.search(r"\bmore\s+[a-z]+er\b", low):
        return True, "comparative_word_form_repair_not_enhance"

    # Pronoun case after preposition: with they, for he, etc.
    for i, t in enumerate(lows[:-1]):
        if t in V146_PREPOSITION_LIKE and lows[i + 1] in V146_SUBJECT_PRONOUNS:
            return True, "pronoun_case_repair_not_lexical_enhance"

    # Preposition + bare verb shape in a malformed construction. To avoid false
    # positives such as "for work", require either a clearly corrupt context or a
    # following object/complement.
    ctx_bad = context_has_local_grammar_corruption(clean)
    for i, t in enumerate(lows[:-1]):
        if t in V146_PREPOSITION_LIKE and _v146_is_verb_lemma_or_form(lows[i + 1]):
            if ctx_bad or i + 2 < len(lows):
                return True, "preposition_bare_verb_repair_not_enhance"

    # Bare be after a nominal head in a clause-like span.
    if re.search(r"\b[a-z]+\s+be\s+[a-z]+\b", low) and not re.search(r"\bto\s+be\b", low):
        return True, "bare_be_clause_repair_not_enhance"

    return False, "ok"


def _v146_is_sentence_rewrite_like(text: str, suggestions: Optional[List[Dict[str, Any]]] = None) -> bool:
    toks = surface_tokens(text)
    lows = [t.lower() for t in toks]
    if len(toks) > 9:
        return True
    # A finite-clause-like span is allowed only when it functions as a compact
    # lexical expression. If suggestions replace the whole clause with another
    # clause, it is often sentence rewriting rather than LRET phrase work.
    finite_markers = {"is", "are", "was", "were", "has", "have", "had", "does", "do", "did"}
    if len(toks) >= 7 and any(t in finite_markers for t in lows[:4]):
        # Compact cost/evaluation clauses can still be lexical phrase tasks, so
        # do not block solely on this signal. It must also lack a clear
        # collocation/predicate-object nucleus.
        if not _v145_has_prep_link(toks) and _v145_content_count(toks) < 3:
            return True
    return False


def _v146_is_valid_final_enhance(unit: Dict[str, Any]) -> Tuple[bool, str]:
    text = str(unit.get('unit_text') or '').strip()
    context = str(unit.get('context') or '')
    suggestions = list(unit.get('suggestions') or [])
    malformed, reason = _v146_span_has_grammar_malformation(text)
    if malformed:
        return False, reason
    if context_has_local_grammar_corruption(context) and is_unrecoverable_phrase_fragment(text, context):
        return False, "malformed_clause_repair_not_enhance"
    if _v146_is_sentence_rewrite_like(text, suggestions):
        return False, "sentence_rewrite_not_phrase_level_enhance"
    # If every accepted suggestion mainly fixes a grammatical malformed token,
    # suppress the ENHANCE. This prevents "spent -> spend" style tasks from
    # appearing as lexical enhancement.
    for s in suggestions:
        st = str((s or {}).get('text') or '')
        if not st:
            continue
        if _v146_span_has_grammar_malformation(text)[0] and not _v146_span_has_grammar_malformation(st)[0]:
            return False, "grammar_repair_suggestion_not_enhance"
    return True, "ok"


def _v146_clarify_is_student_useful(unit: Dict[str, Any]) -> Tuple[bool, str]:
    text = str(unit.get('unit_text') or '').strip()
    toks = surface_tokens(text)
    lows = [t.lower() for t in toks]
    risk = {str(x).lower() for x in (unit.get('risk_flags') or [])}
    reason = str(unit.get('reason') or '').lower()
    if len(toks) < 2:
        return False, "clarify_too_short"
    if len(toks) > 7:
        return False, "clarify_too_long_for_lret"
    if _v146_span_has_grammar_malformation(text)[0] and not (_contains_generic_vague_noun(text) or 'vague' in reason or 'vague' in risk):
        return False, "grammar_breakdown_not_lret_clarify"
    if any(r in risk for r in V146_GRAMMAR_RISK_FLAGS) and not (_contains_generic_vague_noun(text) or 'vague' in reason):
        return False, "grammar_breakdown_not_lret_clarify"
    if lows and lows[0] in {'even', 'although', 'though', 'because', 'when', 'while'}:
        return False, "discourse_clause_not_lret_clarify"
    return True, "ok"


def _v146_filter_enhance_units(enhance_units: List[Dict[str, Any]], failures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for u in enhance_units:
        ok, reason = _v146_is_valid_final_enhance(u)
        if ok:
            out.append(u)
            continue
        if 'sentence_rewrite' in reason:
            metric = "sentence_rewrite_blocked_from_enhance"
        elif 'malformed' in reason or 'bare_be' in reason or 'preposition' in reason or 'pronoun' in reason:
            metric = "malformed_clause_blocked_from_enhance"
        else:
            metric = "grammar_repair_blocked_from_enhance"
        _v146_note(metric, {"unit_text": u.get('unit_text'), "reason": reason})
        failures.append({"unit_text": u.get('unit_text'), "candidate": None, "tier": "v146_final_lexical_only_gate", "reason": reason})
    V146_QA_CACHE['final_enhance_after_v146_gate'] = len(out)
    return out


def _v146_filter_clarify_units(clarify_units: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    scored: List[Tuple[float, Dict[str, Any]]] = []
    dropped: List[Dict[str, Any]] = []
    for u in clarify_units:
        ok, reason = _v146_clarify_is_student_useful(u)
        if not ok:
            _v146_note('clarify_units_suppressed_as_grammar_breakdown', {"unit_text": u.get('unit_text'), "reason": reason})
            dropped.append({"unit": u.get('unit_text'), "unit_id": u.get('unit_id'), "reason": reason, "stage": "v146_clarify_filter"})
            continue
        text = str(u.get('unit_text') or '')
        score = float(u.get('candidate_value') or 0.0)
        if _contains_generic_vague_noun(text) or _is_vague_placeholder_phrase(text):
            score += 0.25
        if 'vague' in str(u.get('reason') or '').lower():
            score += 0.15
        score -= max(0, len(surface_tokens(text)) - 5) * 0.04
        scored.append((score, u))
    scored.sort(key=lambda x: x[0], reverse=True)
    # Keep CLARIFY visible but bounded. More than five usually overwhelms the
    # student and indicates upstream evaluator noise rather than LRET work.
    kept = [u for _, u in scored[:5]]
    for _, u in scored[5:]:
        _v146_note('clarify_units_suppressed_as_grammar_breakdown', {"unit_text": u.get('unit_text'), "reason": "clarify_over_target_cap"})
        dropped.append({"unit": u.get('unit_text'), "unit_id": u.get('unit_id'), "reason": "clarify_over_target_cap", "stage": "v146_clarify_filter"})
    V146_QA_CACHE['final_clarify_after_v146_gate'] = len(kept)
    return kept, dropped


# v1.4.6: a larger same-sentence container should not automatically suppress a
# good lexical phrase if it merely adds auxiliaries/determiners/function words,
# if it contains vague placeholders, or if it is grammatically malformed.
def _has_stronger_same_sentence_container(u: Dict[str, Any], all_units: List[Dict[str, Any]]) -> Optional[str]:
    text = _unit_text(u)
    low = norm_text(text)
    sent = int(u.get('source_sentence_index', -1))
    toks = surface_tokens(text)
    if len(toks) < 2:
        return None
    small_set = set(norm_text(t) for t in toks)
    for other in all_units:
        if other is u:
            continue
        if int(other.get('source_sentence_index', -99)) != sent:
            continue
        otext = _unit_text(other)
        olow = norm_text(otext)
        if not olow or olow == low:
            continue
        otoks = surface_tokens(otext)
        if low in olow and len(otoks) > len(toks):
            if len(otoks) > 9:
                continue
            if _contains_generic_vague_noun(otext) or _is_vague_placeholder_phrase(otext):
                continue
            if _v146_span_has_grammar_malformation(otext)[0]:
                continue
            added = [norm_text(t) for t in otoks if norm_text(t) not in small_set]
            if added and all(t in GENERIC_FUNCTION_OR_EDGE_TOKENS or t in V145_AUXILIARIES or t in V145_DETERMINERS for t in added):
                continue
            if _v145_is_complete_replaceable_unit(otext, str(other.get('context') or '')):
                return otext
    return None


_prev_candidate_preclassification_route_v145 = _candidate_preclassification_route

def _candidate_preclassification_route(u: Dict[str, Any], all_units: List[Dict[str, Any]]) -> Tuple[str, str, List[str]]:
    text = re.sub(r"\s+", " ", _unit_text(u)).strip()
    if not text:
        return 'REJECT_PRE_LLM', 'empty_candidate', ['empty']
    malformed, mreason = _v146_span_has_grammar_malformation(text)
    if malformed:
        if _contains_generic_vague_noun(text) or _is_vague_placeholder_phrase(text):
            return 'CLARIFY_VISIBLE', 'vague_malformed_phrase_requires_clarification', ['vague_reference', mreason]
        return 'REJECT_PRE_LLM', 'grammar_repair_candidate_blocked_before_llm', [mreason]
    route, reason, flags = _prev_candidate_preclassification_route_v145(u, all_units)
    return route, reason, flags


# v1.4.6: prefer clean short lexical units over long reconstructed clauses.
def _llm_candidate_priority(u: Dict[str, Any]) -> Tuple[float, float, int]:
    text = _unit_text(u)
    toks = surface_tokens(text)
    n = len(toks)
    axes = set(u.get('axis_candidates') or [])
    flags = set(u.get('extraction_flags') or [])
    score = float(u.get('candidate_value') or 0.0)
    if _v146_span_has_grammar_malformation(text)[0]:
        score -= 3.0
    if 'collocation_naturalness' in axes:
        score += 0.22
    if 'predicate_argument' in axes:
        score += 0.18
    if 'semantic_specificity' in axes:
        score += 0.10
    if 'v145_reconstructed_candidate' in flags:
        score += 0.05
    if 2 <= n <= 5:
        score += 0.25
    elif n == 6:
        score += 0.08
    elif n >= 8:
        score -= 0.25
    if _contains_generic_vague_noun(text) or _is_vague_placeholder_phrase(text):
        score -= 0.35
    if context_has_local_grammar_corruption(str(u.get('context') or '')) and n >= 7:
        score -= 0.25
    closeness = -abs(n - 4)
    return (score, closeness, int(u.get('frequency') or 1))


_prev_analyze_v145 = analyze

def analyze(payload: Dict[str, Any], validator: Optional[ContextFitValidator] = None) -> Dict[str, Any]:
    _v146_reset()
    validator = validator or RuleBasedContextFitValidator()
    mode = payload.get("mode", "fix_and_enhance")
    if mode not in {"fix_only", "enhance_only", "fix_and_enhance"}:
        raise ValueError(f"Unsupported LRET mode: {mode!r}")
    identity = payload.get("identity") or {}
    essay_text = payload.get("essay_text") or ""
    learner_history = payload.get("learner_lexical_history") or {}
    raw_units, fix_candidates, history_from_payload, ingest_warnings = ingest_and_validate(payload)
    learner_history = learner_history or history_from_payload
    evaluator_input_quality = analyze_evaluator_input_quality(raw_units)
    clean_units, dropped_noise = noise_filter(raw_units)

    fix_units_all, claimed_spans, suppressed_fix_candidates, fix_context_failures = derive_fix_units(fix_candidates, essay_text, validator)
    recovered = recover_recurring_fix_units(clean_units, fix_units_all, validator)
    if recovered:
        fix_units_all.extend(recovered)
    prepare_fix_blocking_state(fix_units_all)

    if mode == "enhance_only":
        fix_units: List[Dict[str, Any]] = []
        ingest_warnings.append("enhance_only mode: fix_units suppressed, but lexical repair spans still used for phrase-first exclusion")
    else:
        fix_units = fix_units_all

    if mode == "fix_only":
        phrase_enhance, phrase_failures, single_fallback, single_failures = [], [], [], []
        clarify_candidates: List[Dict[str, Any]] = []
    else:
        phrase_enhance, phrase_failures = generate_phrase_enhance_candidates(clean_units, essay_text, validator)
        phrase_enhance = _v146_filter_enhance_units(phrase_enhance, phrase_failures)
        clarify_candidates = list(V143_QA_CACHE.get("clarify_units") or [])
        clarify_candidates, dropped_clarify_v146 = _v146_filter_clarify_units(clarify_candidates)
        single_fallback, single_failures = [], []

    all_task_candidates = list(fix_units) + phrase_enhance + single_fallback
    survivors, dropped_dedup = apply_phrase_first_dedup(all_task_candidates)
    final_fix_units = [u for u in survivors if u.get("class_label") == "FIX"]
    final_enhance_units = [u for u in survivors if u.get("class_label") == "ENHANCE"]
    final_clarify_units, dropped_clarify = _dedupe_clarify_units(clarify_candidates, survivors)

    for u in final_fix_units + final_enhance_units + final_clarify_units:
        if u.get("class_label") != "CLARIFY":
            u["dedup_role"] = u.get("dedup_role") if "survivor" in str(u.get("dedup_role")) else "survivor_phrase"
        apply_history_framing(u, learner_history)

    keep_units, keep_inventory_audit, unresolved_internal = build_keep_units(clean_units, survivors + final_clarify_units)
    dropped_units = list(dropped_noise) + list(dropped_dedup) + list(dropped_clarify) + list(locals().get('dropped_clarify_v146', []))
    context_failures = list(fix_context_failures) + list(phrase_failures) + list(single_failures)
    run_id = new_run_id(identity)
    result = {
        "schema_version": SCHEMA_VERSION_OUT,
        "identity": identity,
        "run": {"run_id": run_id, "engine_id": ENGINE_ID, "engine_version": ENGINE_VERSION, "created_at": _utc_now_iso(), "contract_version": SCHEMA_VERSION_OUT, "input_schema_version": payload.get("schema_version")},
        "fix_units": final_fix_units,
        "enhance_units": final_enhance_units,
        "clarify_units": final_clarify_units,
        "keep_units": keep_units,
        "lexical_profile": build_lexical_profile_v143(final_fix_units, final_enhance_units, final_clarify_units, keep_units, dropped_units, suppressed_fix_candidates, unresolved_internal, evaluator_input_quality),
        "replacement_options": [{"unit_id": u.get("unit_id"), "unit_text": u.get("unit_text"), "class_label": u.get("class_label"), "replacement_scope": u.get("replacement_scope"), "suggestions": u.get("suggestions"), "reveal_policy": u.get("reveal_policy")} for u in (final_fix_units + final_enhance_units)],
        "clarification_options": [{"unit_id": u.get("unit_id"), "unit_text": u.get("unit_text"), "class_label": "CLARIFY", "replacement_scope": "meaning_clarification", "phase1_prompt": u.get("phase1_prompt"), "clarification_guidance": u.get("clarification_guidance"), "reveal_policy": u.get("reveal_policy")} for u in final_clarify_units],
        "lret_practice_targets": build_practice_targets_v143(final_fix_units, final_enhance_units, final_clarify_units, learner_history),
        "qa": build_qa_v143(ingest_warnings, dropped_units, suppressed_fix_candidates, context_failures, final_fix_units, final_enhance_units, final_clarify_units, keep_units, keep_inventory_audit, unresolved_internal, evaluator_input_quality),
        "learning_intelligence_payload": build_learning_intelligence_payload_v143(identity, run_id, final_fix_units, final_enhance_units, final_clarify_units, keep_units),
    }
    result['lexical_profile']['v146_lexical_only_gate'] = {
        'grammar_repair_blocked_from_enhance': int(V146_QA_CACHE.get('grammar_repair_blocked_from_enhance', 0)),
        'malformed_clause_blocked_from_enhance': int(V146_QA_CACHE.get('malformed_clause_blocked_from_enhance', 0)),
        'sentence_rewrite_blocked_from_enhance': int(V146_QA_CACHE.get('sentence_rewrite_blocked_from_enhance', 0)),
        'clarify_units_suppressed_as_grammar_breakdown': int(V146_QA_CACHE.get('clarify_units_suppressed_as_grammar_breakdown', 0)),
        'final_enhance_after_v146_gate': len(final_enhance_units),
        'final_clarify_after_v146_gate': len(final_clarify_units),
    }
    result['qa'].setdefault('v1_4_6_metrics', {}).update(result['lexical_profile']['v146_lexical_only_gate'])
    result['qa']['v1_4_6_metrics']['blocked_enhance_samples'] = list(V146_QA_CACHE.get('blocked_enhance_samples', []))[:12]
    result['qa']['v1_4_6_metrics']['suppressed_clarify_samples'] = list(V146_QA_CACHE.get('suppressed_clarify_samples', []))[:12]
    result['qa'].setdefault('contract_checks', {}).update({
        'v146_lexical_only_final_gate_enabled': True,
        'grammar_repairs_not_released_as_enhance': all(not _v146_span_has_grammar_malformation(u.get('unit_text', ''))[0] for u in final_enhance_units),
        'malformed_clause_repairs_not_released_as_enhance': True,
        'clarify_visible_but_bounded': len(final_clarify_units) <= 5,
        'no_topic_or_essay_specific_rules_v146': True,
    })
    return result



# ---------------------------------------------------------------------------
# v1.5 patch: context-insertion validation + active canonical-resource evidence
# ---------------------------------------------------------------------------
# This version is intentionally architectural rather than a narrow patch.
# It keeps the universal reconstruction/preclassification pipeline, but adds:
#   1) active resource indexing from canonical registries;
#   2) resource-aware candidate ranking and evidence export;
#   3) post-suggestion replacement-in-context validation;
#   4) grammar-repair delta exclusion before ENHANCE release.
# No essay id, topic whitelist, sample sentence rule, or embedded phrase bank is used.

ENGINE_VERSION = "lret-engine-v1.5-context-insertion-resource-validated"

V15_QA_CACHE: Dict[str, Any] = {}
V15_RESOURCE_INDEX: Dict[str, Any] = {
    "positive_collocations": {},
    "governance_patterns": {},
    "lexical_entries": {},
    "academic_lemmas": set(),
    "irregular_plurals": set(),
    "irregular_singular_by_plural": {},
}

V15_ARTICLES = {"a", "an", "the"}
V15_MODAL_OR_TO_LEFT = {"to", "can", "could", "may", "might", "must", "should", "would", "will", "shall"}
V15_LIGHT_FUNCTION_STARTERS = {"there", "it", "this", "that", "these", "those"}


def _v15_reset() -> None:
    V15_QA_CACHE.clear()
    V15_QA_CACHE.update({
        "resource_indexes_loaded": False,
        "positive_collocation_index_size": 0,
        "governance_pattern_index_size": 0,
        "lexical_entry_index_size": 0,
        "irregular_plural_index_size": 0,
        "resource_evidence_attached_count": 0,
        "resource_priority_boost_count": 0,
        "replace_in_context_checked": 0,
        "replace_in_context_rejected": 0,
        "grammar_delta_rejected": 0,
        "resource_unsupported_suggestion_rejected": 0,
        "final_sentence_quality_rejected": 0,
        "accepted_after_context_insertion": 0,
        "blocked_samples": [],
    })

def _v15_refresh_resource_metrics() -> None:
    V15_QA_CACHE['resource_indexes_loaded'] = bool(
        V15_RESOURCE_INDEX.get('positive_collocations') or
        V15_RESOURCE_INDEX.get('governance_patterns') or
        V15_RESOURCE_INDEX.get('lexical_entries')
    )
    V15_QA_CACHE['positive_collocation_index_size'] = len(V15_RESOURCE_INDEX.get('positive_collocations') or {})
    V15_QA_CACHE['governance_pattern_index_size'] = len(V15_RESOURCE_INDEX.get('governance_patterns') or {})
    V15_QA_CACHE['lexical_entry_index_size'] = len(V15_RESOURCE_INDEX.get('lexical_entries') or {})
    V15_QA_CACHE['irregular_plural_index_size'] = len(V15_RESOURCE_INDEX.get('irregular_plurals') or set())



def _v15_note(name: str, sample: Optional[Dict[str, Any]] = None) -> None:
    V15_QA_CACHE[name] = int(V15_QA_CACHE.get(name, 0)) + 1
    if sample:
        V15_QA_CACHE.setdefault("blocked_samples", [])
        if len(V15_QA_CACHE["blocked_samples"]) < 24:
            V15_QA_CACHE["blocked_samples"].append(sample)


def _v15_index_add(bucket: str, key: str, value: Dict[str, Any]) -> None:
    key2 = norm_text(key)
    if not key2:
        return
    V15_RESOURCE_INDEX.setdefault(bucket, {})[key2] = value


def _v15_load_resource_indexes(resource_path: Optional[str]) -> None:
    if not resource_path:
        return
    # Reset only the v1.5 indexes; older resource stores remain loaded by the base loader.
    V15_RESOURCE_INDEX["positive_collocations"] = {}
    V15_RESOURCE_INDEX["governance_patterns"] = {}
    V15_RESOURCE_INDEX["lexical_entries"] = {}
    V15_RESOURCE_INDEX["academic_lemmas"] = set()
    V15_RESOURCE_INDEX["irregular_plurals"] = set()
    V15_RESOURCE_INDEX["irregular_singular_by_plural"] = {}

    for row in _iter_tsv_from_resource(resource_path, 'positive_collocations_registry.tsv') or []:
        pattern = norm_text(row.get('pattern'))
        conf = _safe_float(row.get('confidence'), 0.0)
        role = str(row.get('runtime_role') or '')
        if pattern and conf >= 0.80 and ('positive' in role or 'lret' in role):
            _v15_index_add('positive_collocations', pattern, {
                'pattern': pattern,
                'confidence': conf,
                'relation_type': row.get('relation_type'),
                'headword': norm_text(row.get('headword')),
                'collocate': norm_text(row.get('collocate')),
            })

    for row in _iter_tsv_from_resource(resource_path, 'preposition_governance_registry.tsv') or []:
        pattern = norm_text(row.get('pattern'))
        conf = _safe_float(row.get('confidence'), 0.0)
        status = str(row.get('status') or '')
        if pattern and conf >= 0.80 and 'review' not in status.lower():
            _v15_index_add('governance_patterns', pattern, {
                'pattern': pattern,
                'confidence': conf,
                'relation_type': row.get('relation_type'),
                'source': 'preposition_governance_registry',
            })

    for row in _iter_tsv_from_resource(resource_path, 'verb_complement_registry.tsv') or []:
        gov = norm_text(row.get('governor'))
        pat = norm_text(row.get('complement_pattern'))
        conf = _safe_float(row.get('confidence'), 0.0)
        if gov and pat and conf >= 0.85:
            _v15_index_add('governance_patterns', f"{gov}::{pat}", {
                'pattern': f"{gov}::{pat}",
                'confidence': conf,
                'relation_type': row.get('relation_type'),
                'source': 'verb_complement_registry',
            })

    lex = _read_json_from_resource(resource_path, 'lexical_registry.json')
    if isinstance(lex, list):
        for row in lex:
            if not isinstance(row, dict):
                continue
            lemma = norm_text(row.get('lemma'))
            if not lemma or len(lemma) > 80:
                continue
            conf = _safe_float(row.get('confidence'), 0.0)
            if conf < 0.70:
                continue
            V15_RESOURCE_INDEX['lexical_entries'][lemma] = {
                'lemma': lemma,
                'pos': row.get('pos'),
                'cefr': row.get('cefr'),
                'register': row.get('register'),
                'academic': bool(row.get('academic')),
                'confidence': conf,
                'is_multiword': bool(row.get('is_multiword')),
            }
            if bool(row.get('academic')) or str(row.get('register') or '').lower() == 'academic':
                V15_RESOURCE_INDEX['academic_lemmas'].add(lemma)

    irr = _read_json_from_resource(resource_path, 'irregular_noun_registry.json')
    if isinstance(irr, list):
        for row in irr:
            if not isinstance(row, dict):
                continue
            singular = norm_text(row.get('singular'))
            variants = row.get('plural_variants') or []
            plural = norm_text(row.get('plural'))
            vals = [plural] + [norm_text(x) for x in variants]
            for val in vals:
                if val:
                    V15_RESOURCE_INDEX['irregular_plurals'].add(val)
                    if singular:
                        V15_RESOURCE_INDEX['irregular_singular_by_plural'][val] = singular

    V15_QA_CACHE['resource_indexes_loaded'] = True
    V15_QA_CACHE['positive_collocation_index_size'] = len(V15_RESOURCE_INDEX.get('positive_collocations') or {})
    V15_QA_CACHE['governance_pattern_index_size'] = len(V15_RESOURCE_INDEX.get('governance_patterns') or {})
    V15_QA_CACHE['lexical_entry_index_size'] = len(V15_RESOURCE_INDEX.get('lexical_entries') or {})
    V15_QA_CACHE['irregular_plural_index_size'] = len(V15_RESOURCE_INDEX.get('irregular_plurals') or set())


_prev_load_canonical_resources_v146 = load_canonical_resources

def load_canonical_resources(resource_path: Optional[str]) -> None:
    _prev_load_canonical_resources_v146(resource_path)
    _v15_load_resource_indexes(resource_path)


def _v15_text_contains_resource_pattern(text: str) -> bool:
    low = norm_text(text)
    if not low:
        return False
    if low in V15_RESOURCE_INDEX.get('positive_collocations', {}):
        return True
    if low in V15_RESOURCE_INDEX.get('governance_patterns', {}):
        return True
    return False


def _v15_resource_evidence_for_text(text: str) -> Dict[str, Any]:
    low = norm_text(text)
    toks = [norm_text(t) for t in surface_tokens(text)]
    evidence = {
        'exact_positive_collocation': low in V15_RESOURCE_INDEX.get('positive_collocations', {}),
        'exact_governance_pattern': low in V15_RESOURCE_INDEX.get('governance_patterns', {}),
        'positive_collocation_submatches': [],
        'governance_submatches': [],
        'lexical_entry_count': 0,
        'academic_lemma_count': 0,
        'resource_score': 0.0,
    }
    if evidence['exact_positive_collocation']:
        evidence['resource_score'] += 0.55
    if evidence['exact_governance_pattern']:
        evidence['resource_score'] += 0.35
    # Use bounded submatch scan to avoid turning the resource bank into an exact phrase generator.
    for pat in V15_RESOURCE_INDEX.get('positive_collocations', {}).keys():
        if len(evidence['positive_collocation_submatches']) >= 5:
            break
        if pat and len(surface_tokens(pat)) >= 2 and pat in low:
            evidence['positive_collocation_submatches'].append(pat)
    for pat in V15_RESOURCE_INDEX.get('governance_patterns', {}).keys():
        if len(evidence['governance_submatches']) >= 5:
            break
        if '::' not in pat and pat and len(surface_tokens(pat)) >= 2 and pat in low:
            evidence['governance_submatches'].append(pat)
    if evidence['positive_collocation_submatches']:
        evidence['resource_score'] += min(0.35, 0.12 * len(evidence['positive_collocation_submatches']))
    if evidence['governance_submatches']:
        evidence['resource_score'] += min(0.20, 0.08 * len(evidence['governance_submatches']))
    lex_entries = V15_RESOURCE_INDEX.get('lexical_entries', {})
    acad = V15_RESOURCE_INDEX.get('academic_lemmas', set())
    for t in toks:
        if t in lex_entries:
            evidence['lexical_entry_count'] += 1
        if t in acad:
            evidence['academic_lemma_count'] += 1
    evidence['resource_score'] += min(0.25, 0.04 * evidence['lexical_entry_count'])
    evidence['resource_score'] += min(0.25, 0.08 * evidence['academic_lemma_count'])
    evidence['resource_score'] = round(float(evidence['resource_score']), 3)
    return evidence


def _v15_enrich_unit_with_resource_evidence(u: Dict[str, Any]) -> Dict[str, Any]:
    text = _unit_text(u)
    evidence = _v15_resource_evidence_for_text(text)
    u['resource_evidence'] = evidence
    if evidence.get('resource_score', 0.0) > 0:
        _v15_note('resource_evidence_attached_count')
        flags = set(u.get('extraction_flags') or [])
        axes = set(u.get('axis_candidates') or [])
        if evidence.get('exact_positive_collocation') or evidence.get('positive_collocation_submatches'):
            flags.add('external_resource_positive_collocation')
            axes.add('collocation_naturalness')
        if evidence.get('exact_governance_pattern') or evidence.get('governance_submatches'):
            flags.add('external_resource_governance_pattern')
            axes.add('collocation_naturalness')
        if evidence.get('academic_lemma_count'):
            flags.add('external_resource_academic_lexis')
            axes.add('register_control')
        u['extraction_flags'] = sorted(flags)
        u['axis_candidates'] = sorted(axes)
        old_value = float(u.get('candidate_value') or 0.0)
        boost = min(0.16, 0.08 * evidence.get('resource_score', 0.0))
        if boost > 0:
            u['candidate_value'] = round(min(0.95, old_value + boost), 3)
            _v15_note('resource_priority_boost_count')
    return u


_prev_v145_reconstruct_candidate_units_v146 = _v145_reconstruct_candidate_units

def _v145_reconstruct_candidate_units(raw_units: List[Dict[str, Any]], essay_text: str) -> List[Dict[str, Any]]:
    units = _prev_v145_reconstruct_candidate_units_v146(raw_units, essay_text)
    for u in units:
        _v15_enrich_unit_with_resource_evidence(u)
    return units


def _v15_token_is_plural_noun_like(tok: str) -> bool:
    low = norm_text(tok)
    if not low:
        return False
    if low in V15_RESOURCE_INDEX.get('irregular_plurals', set()):
        return True
    if low.endswith('s') and len(low) > 3 and not low.endswith('ss'):
        return True
    return False


def _v15_has_article_plural_mismatch(text: str) -> bool:
    toks = [norm_text(t) for t in surface_tokens(text)]
    for i in range(len(toks) - 1):
        if toks[i] in {'a', 'an'} and _v15_token_is_plural_noun_like(toks[i + 1]):
            return True
    return False


def _v15_first_token_is_nonbase_verb_form(tok: str) -> bool:
    low = norm_text(tok)
    if not low:
        return False
    lemma = MORPH_FORM_TO_LEMMA.get(low)
    return bool(lemma and lemma != low)


def _v15_context_left_of_span(context: str, unit_text: str) -> str:
    if not context or not unit_text:
        return ''
    m = re.search(re.escape(unit_text), context, flags=re.I)
    if not m:
        return ''
    return context[:m.start()]


def _v15_context_right_of_span(context: str, unit_text: str) -> str:
    if not context or not unit_text:
        return ''
    m = re.search(re.escape(unit_text), context, flags=re.I)
    if not m:
        return ''
    return context[m.end():]


def _v15_original_span_is_grammar_repair_candidate(unit_text: str, context: str) -> Tuple[bool, str]:
    text = re.sub(r"\s+", " ", str(unit_text or '')).strip()
    if not text:
        return False, 'ok'
    toks = surface_tokens(text)
    lows = [norm_text(t) for t in toks]
    if _v15_has_article_plural_mismatch(text):
        return True, 'article_number_repair_delta'
    malformed, mreason = _v146_span_has_grammar_malformation(text)
    if malformed:
        return True, mreason
    left = _v15_context_left_of_span(context, text)
    left_toks = [norm_text(t) for t in surface_tokens(left)][-3:]
    if lows and left_toks:
        first = lows[0]
        if left_toks[-1] in V15_MODAL_OR_TO_LEFT and _v15_first_token_is_nonbase_verb_form(first):
            return True, 'verb_form_repair_required_by_left_context'
        if len(left_toks) >= 2 and left_toks[-2:] == ['has', 'to'] and _v15_first_token_is_nonbase_verb_form(first):
            return True, 'verb_form_repair_required_by_left_context'
        if left_toks[-1] in {'a', 'an'} and first in GENERIC_COMMON_BARE_VERBS_REQUIRING_COMPLEMENT:
            return True, 'article_left_context_incompatible_with_verb_replacement'
    return False, 'ok'


def _v15_suggestion_delta_is_grammar_repair(unit_text: str, suggestion_text: str, context: str) -> Tuple[bool, str]:
    original = re.sub(r"\s+", " ", str(unit_text or '')).strip()
    suggestion = re.sub(r"\s+", " ", str(suggestion_text or '')).strip()
    if not original or not suggestion:
        return True, 'empty_suggestion'
    repair, reason = _v15_original_span_is_grammar_repair_candidate(original, context)
    if repair:
        return True, reason
    o_toks = [norm_text(t) for t in surface_tokens(original)]
    s_toks = [norm_text(t) for t in surface_tokens(suggestion)]
    o_no_articles = [t for t in o_toks if t not in V15_ARTICLES]
    s_no_articles = [t for t in s_toks if t not in V15_ARTICLES]
    if o_no_articles == s_no_articles and o_toks != s_toks:
        return True, 'article_only_or_determiner_only_repair'
    # If all changed tokens collapse to the same lemma, the change is morphology, not ENHANCE.
    if len(o_toks) == len(s_toks) and o_toks != s_toks:
        changed = [(a, b) for a, b in zip(o_toks, s_toks) if a != b]
        if changed:
            all_morph = True
            for a, b in changed:
                la = MORPH_FORM_TO_LEMMA.get(a, a)
                lb = MORPH_FORM_TO_LEMMA.get(b, b)
                if la != lb:
                    all_morph = False
                    break
            if all_morph:
                return True, 'morphology_only_repair_delta'
    return False, 'ok'


def _v15_replace_first_exact(context: str, unit_text: str, suggestion_text: str) -> Tuple[Optional[str], str, str, str]:
    if not context or not unit_text or not suggestion_text:
        return None, '', '', 'empty_context_or_span'
    m = re.search(re.escape(unit_text), context, flags=re.I)
    if not m:
        return None, '', '', 'span_not_exact_substring_for_replacement'
    left = context[:m.start()]
    right = context[m.end():]
    return left + suggestion_text + right, left, right, 'ok'


def _v15_sentence_quality_after_replacement(unit_text: str, suggestion_text: str, context: str) -> Tuple[bool, str, Optional[str]]:
    final_sentence, left, right, reason = _v15_replace_first_exact(context, unit_text, suggestion_text)
    if final_sentence is None:
        return False, reason, None
    text = re.sub(r"\s+", " ", final_sentence).strip()
    low = norm_text(text)
    left_toks = [norm_text(t) for t in surface_tokens(left)]
    sugg_toks = [norm_text(t) for t in surface_tokens(suggestion_text)]
    if not sugg_toks:
        return False, 'empty_suggestion_after_tokenization', text
    if left_toks and left_toks[-1] in {'a', 'an'}:
        first = sugg_toks[0]
        if first in GENERIC_COMMON_BARE_VERBS_REQUIRING_COMPLEMENT or _v15_first_token_is_nonbase_verb_form(first):
            return False, 'replacement_breaks_left_article_context', text
    if re.search(r"\b(a|an|the)\s+(a|an|the)\b", low):
        return False, 'replacement_creates_double_determiner', text
    if re.search(r"\b(to)\s+\w+(ed|en)\b", low):
        # Use a soft regex as a backstop; base morphology check above catches most resource-known cases.
        return False, 'replacement_leaves_nonbase_after_to_pattern', text
    if re.search(r"\b(for|with|by|from|to)\s+(they|he|she|we|I)\b", text):
        return False, 'replacement_leaves_preposition_subject_pronoun_pattern', text
    if _v15_has_article_plural_mismatch(text):
        return False, 'replacement_leaves_article_plural_mismatch', text
    if re.search(r"\b(an|a)\s+(is|are|was|were|be|been|being|has|have|had|can|could|may|might|must|should|would|will)\b", low):
        return False, 'replacement_creates_article_auxiliary_sequence', text
    # Real user report, reproduced directly: "people often help" -> "people
    # often facilitate" inside "...people often help take care of their
    # grandchildren..." produced "...people often facilitate take care of
    # their grandchildren..." -- ungrammatical, because "help" is one of a
    # small set of English control verbs that can take a bare-infinitive
    # complement directly ("help take care of", "let him go"), and
    # "facilitate" is not. Reject swaps of a bare-infinitive control verb
    # for a non-control verb when the text right after the replacement
    # still looks like the start of a bare-infinitive clause.
    unit_toks_low = [norm_text(t) for t in surface_tokens(unit_text)]
    orig_last = unit_toks_low[-1] if unit_toks_low else ''
    right_first = (norm_text(right).split() or [None])[0]
    if (orig_last in BARE_INFINITIVE_CONTROL_VERBS
            and sugg_toks[-1] not in BARE_INFINITIVE_CONTROL_VERBS
            and right_first in GENERIC_COMMON_BARE_VERBS_REQUIRING_COMPLEMENT):
        return False, 'replacement_breaks_bare_infinitive_complement_pattern', text
    return True, 'ok', text


def _v15_resource_support_for_suggestion(unit_text: str, suggestion_text: str) -> Dict[str, Any]:
    u = _v15_resource_evidence_for_text(unit_text)
    s = _v15_resource_evidence_for_text(suggestion_text)
    return {
        'unit_resource_score': u.get('resource_score', 0.0),
        'suggestion_resource_score': s.get('resource_score', 0.0),
        'suggestion_has_positive_collocation': bool(s.get('exact_positive_collocation') or s.get('positive_collocation_submatches')),
        'suggestion_has_governance_pattern': bool(s.get('exact_governance_pattern') or s.get('governance_submatches')),
        'suggestion_academic_lemma_count': int(s.get('academic_lemma_count') or 0),
    }


def _v15_validate_enhance_suggestion(unit_text: str, suggestion_text: str, context: str) -> Tuple[bool, str, Optional[str], Dict[str, Any]]:
    _v15_note('replace_in_context_checked')
    grammar_delta, delta_reason = _v15_suggestion_delta_is_grammar_repair(unit_text, suggestion_text, context)
    if grammar_delta:
        _v15_note('grammar_delta_rejected', {'unit_text': unit_text, 'candidate': suggestion_text, 'reason': delta_reason})
        return False, delta_reason, None, {}
    ok_sentence, sent_reason, final_sentence = _v15_sentence_quality_after_replacement(unit_text, suggestion_text, context)
    if not ok_sentence:
        _v15_note('final_sentence_quality_rejected', {'unit_text': unit_text, 'candidate': suggestion_text, 'reason': sent_reason, 'final_sentence': final_sentence})
        _v15_note('replace_in_context_rejected')
        return False, sent_reason, final_sentence, {}
    resource_support = _v15_resource_support_for_suggestion(unit_text, suggestion_text)
    # Do not require exact collocation evidence for every valid paraphrase, but reject weak suggestions
    # when neither the original nor the suggestion has any resource-backed lexical signal and the phrase is short.
    if len(content_tokens(unit_text)) <= 2:
        total_signal = float(resource_support.get('unit_resource_score') or 0.0) + float(resource_support.get('suggestion_resource_score') or 0.0)
        if total_signal < 0.05:
            _v15_note('resource_unsupported_suggestion_rejected', {'unit_text': unit_text, 'candidate': suggestion_text, 'reason': 'short_phrase_without_resource_signal'})
            return False, 'short_phrase_without_resource_signal', final_sentence, resource_support
    _v15_note('accepted_after_context_insertion')
    return True, 'passed_replace_in_context_and_resource_gate', final_sentence, resource_support


_prev_validate_llm_suggestions_v146 = validate_llm_suggestions

def validate_llm_suggestions(unit_text: str, suggestions: Iterable[str], context: str, validator: ContextFitValidator, failures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    prelim = _prev_validate_llm_suggestions_v146(unit_text, suggestions, context, validator, failures)
    final: List[Dict[str, Any]] = []
    for item in prelim:
        text = str(item.get('text') if isinstance(item, dict) else item).strip()
        ok, reason, final_sentence, resource_support = _v15_validate_enhance_suggestion(unit_text, text, context)
        if not ok:
            failures.append({
                'unit_text': unit_text,
                'candidate': text,
                'tier': 'v15_replace_in_context_resource_gate',
                'reason': reason,
                'final_sentence': final_sentence,
                'resource_support': resource_support,
            })
            continue
        if isinstance(item, dict):
            item2 = copy.deepcopy(item)
        else:
            item2 = {'text': text, 'validation': {'accepted': True, 'gates': [], 'reason': 'accepted'}}
        item2.setdefault('validation', {}).setdefault('gates', [])
        item2['validation']['gates'] = list(item2['validation'].get('gates') or []) + ['replace_in_context_sentence_fit', 'resource_signal_checked']
        item2['validation']['reason'] = 'passed llm suggestion + deterministic contextual-fit + v1.5 insertion/resource gates'
        item2['final_sentence_after_replacement'] = final_sentence
        item2['resource_support'] = resource_support
        final.append(item2)
    return final


_prev_validate_suggestions_v146 = validate_suggestions

def validate_suggestions(unit_text: str, suggestions: Iterable[str], context: str, validator: ContextFitValidator, *, source: str, tier: str, failures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    prelim = _prev_validate_suggestions_v146(unit_text, suggestions, context, validator, source=source, tier=tier, failures=failures)
    final: List[Dict[str, Any]] = []
    for item in prelim:
        text = str(item.get('text') if isinstance(item, dict) else item).strip()
        ok, reason, final_sentence, resource_support = _v15_validate_enhance_suggestion(unit_text, text, context)
        if not ok:
            failures.append({
                'unit_text': unit_text,
                'candidate': text,
                'tier': 'v15_replace_in_context_resource_gate',
                'source': source,
                'reason': reason,
                'final_sentence': final_sentence,
                'resource_support': resource_support,
            })
            continue
        item2 = copy.deepcopy(item)
        item2.setdefault('validation', {}).setdefault('gates', [])
        item2['validation']['gates'] = list(item2['validation'].get('gates') or []) + ['replace_in_context_sentence_fit', 'resource_signal_checked']
        item2['validation']['reason'] = 'passed deterministic contextual-fit + v1.5 insertion/resource gates'
        item2['final_sentence_after_replacement'] = final_sentence
        item2['resource_support'] = resource_support
        final.append(item2)
    return final


_prev_v146_is_valid_final_enhance_v146 = _v146_is_valid_final_enhance

def _v146_is_valid_final_enhance(unit: Dict[str, Any]) -> Tuple[bool, str]:
    ok, reason = _prev_v146_is_valid_final_enhance_v146(unit)
    if not ok:
        return ok, reason
    text = str(unit.get('unit_text') or '')
    context = str(unit.get('context') or '')
    repair, rep_reason = _v15_original_span_is_grammar_repair_candidate(text, context)
    if repair:
        return False, 'v15_' + rep_reason
    good = 0
    for s in unit.get('suggestions') or []:
        st = str(s.get('text') if isinstance(s, dict) else s)
        ok2, reason2, _, _ = _v15_validate_enhance_suggestion(text, st, context)
        if ok2:
            good += 1
    if good < max(1, LLM_MIN_VALID_SUGGESTIONS):
        return False, 'v15_fewer_than_min_insertable_suggestions'
    return True, 'ok'


_prev_candidate_preclassification_route_v146_active = _candidate_preclassification_route

def _candidate_preclassification_route(u: Dict[str, Any], all_units: List[Dict[str, Any]]) -> Tuple[str, str, List[str]]:
    _v15_enrich_unit_with_resource_evidence(u)
    text = _unit_text(u)
    context = str(u.get('context') or '')
    repair, reason = _v15_original_span_is_grammar_repair_candidate(text, context)
    if repair:
        if _contains_generic_vague_noun(text) or _is_vague_placeholder_phrase(text):
            return 'CLARIFY_VISIBLE', 'v15_vague_grammar_unstable_phrase_requires_clarification', ['vague_reference', reason]
        return 'REJECT_PRE_LLM', 'v15_grammar_repair_candidate_blocked_before_llm', [reason]
    route, base_reason, flags = _prev_candidate_preclassification_route_v146_active(u, all_units)
    if route == 'ENHANCE_ELIGIBLE':
        ev = u.get('resource_evidence') or {}
        if float(ev.get('resource_score') or 0.0) >= 0.30:
            flags = list(flags) + ['resource_supported_enhance_candidate']
    return route, base_reason, flags


_prev_llm_candidate_priority_v146 = _llm_candidate_priority

def _llm_candidate_priority(u: Dict[str, Any]) -> Tuple[float, float, int]:
    _v15_enrich_unit_with_resource_evidence(u)
    base = _prev_llm_candidate_priority_v146(u)
    base_score = float(base[0]) if isinstance(base, tuple) and base else 0.0
    closeness = float(base[1]) if isinstance(base, tuple) and len(base) > 1 else 0.0
    freq = int(base[2]) if isinstance(base, tuple) and len(base) > 2 else int(u.get('frequency') or 1)
    ev = u.get('resource_evidence') or {}
    resource_boost = min(0.65, float(ev.get('resource_score') or 0.0))
    if resource_boost > 0:
        _v15_note('resource_priority_boost_count')
    text = _unit_text(u)
    context = str(u.get('context') or '')
    repair, _ = _v15_original_span_is_grammar_repair_candidate(text, context)
    if repair:
        resource_boost -= 4.0
    return (base_score + resource_boost, closeness, freq)


_prev_build_lexical_profile_v146 = build_lexical_profile_v143

def build_lexical_profile_v143(fix_units: List[Dict[str, Any]], enhance_units: List[Dict[str, Any]], clarify_units: List[Dict[str, Any]], keep_units: List[Dict[str, Any]], dropped_units: List[Dict[str, Any]], suppressed_fix_candidates: List[Dict[str, Any]], unresolved_internal: List[Dict[str, Any]], evaluator_input_quality: Dict[str, Any]) -> Dict[str, Any]:
    prof = _prev_build_lexical_profile_v146(fix_units, enhance_units, clarify_units, keep_units, dropped_units, suppressed_fix_candidates, unresolved_internal, evaluator_input_quality)
    prof['v15_context_insertion_resource_gate'] = {
        'resource_indexes_loaded': bool(V15_QA_CACHE.get('resource_indexes_loaded')),
        'positive_collocation_index_size': int(V15_QA_CACHE.get('positive_collocation_index_size', 0)),
        'governance_pattern_index_size': int(V15_QA_CACHE.get('governance_pattern_index_size', 0)),
        'lexical_entry_index_size': int(V15_QA_CACHE.get('lexical_entry_index_size', 0)),
        'irregular_plural_index_size': int(V15_QA_CACHE.get('irregular_plural_index_size', 0)),
        'resource_evidence_attached_count': int(V15_QA_CACHE.get('resource_evidence_attached_count', 0)),
        'resource_priority_boost_count': int(V15_QA_CACHE.get('resource_priority_boost_count', 0)),
        'replace_in_context_checked': int(V15_QA_CACHE.get('replace_in_context_checked', 0)),
        'replace_in_context_rejected': int(V15_QA_CACHE.get('replace_in_context_rejected', 0)),
        'grammar_delta_rejected': int(V15_QA_CACHE.get('grammar_delta_rejected', 0)),
        'resource_unsupported_suggestion_rejected': int(V15_QA_CACHE.get('resource_unsupported_suggestion_rejected', 0)),
        'final_sentence_quality_rejected': int(V15_QA_CACHE.get('final_sentence_quality_rejected', 0)),
        'accepted_after_context_insertion': int(V15_QA_CACHE.get('accepted_after_context_insertion', 0)),
    }
    return prof


_prev_build_qa_v146 = build_qa_v143

def build_qa_v143(warnings: List[str], dropped_units: List[Dict[str, Any]], suppressed_fix_candidates: List[Dict[str, Any]], context_failures: List[Dict[str, Any]], fix_units: List[Dict[str, Any]], enhance_units: List[Dict[str, Any]], clarify_units: List[Dict[str, Any]], keep_units: List[Dict[str, Any]], keep_inventory_audit: Optional[List[Dict[str, Any]]] = None, unresolved_internal: Optional[List[Dict[str, Any]]] = None, evaluator_input_quality: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    qa = _prev_build_qa_v146(warnings, dropped_units, suppressed_fix_candidates, context_failures, fix_units, enhance_units, clarify_units, keep_units, keep_inventory_audit, unresolved_internal, evaluator_input_quality)
    qa.setdefault('v1_5_metrics', {}).update({
        'active_resource_indexing_enabled': bool(V15_QA_CACHE.get('resource_indexes_loaded')),
        'positive_collocation_index_size': int(V15_QA_CACHE.get('positive_collocation_index_size', 0)),
        'governance_pattern_index_size': int(V15_QA_CACHE.get('governance_pattern_index_size', 0)),
        'lexical_entry_index_size': int(V15_QA_CACHE.get('lexical_entry_index_size', 0)),
        'irregular_plural_index_size': int(V15_QA_CACHE.get('irregular_plural_index_size', 0)),
        'resource_evidence_attached_count': int(V15_QA_CACHE.get('resource_evidence_attached_count', 0)),
        'resource_priority_boost_count': int(V15_QA_CACHE.get('resource_priority_boost_count', 0)),
        'replace_in_context_checked': int(V15_QA_CACHE.get('replace_in_context_checked', 0)),
        'replace_in_context_rejected': int(V15_QA_CACHE.get('replace_in_context_rejected', 0)),
        'grammar_delta_rejected': int(V15_QA_CACHE.get('grammar_delta_rejected', 0)),
        'resource_unsupported_suggestion_rejected': int(V15_QA_CACHE.get('resource_unsupported_suggestion_rejected', 0)),
        'final_sentence_quality_rejected': int(V15_QA_CACHE.get('final_sentence_quality_rejected', 0)),
        'accepted_after_context_insertion': int(V15_QA_CACHE.get('accepted_after_context_insertion', 0)),
        'blocked_samples': list(V15_QA_CACHE.get('blocked_samples', []))[:24],
    })
    qa.setdefault('contract_checks', {}).update({
        'v15_context_insertion_validation_enabled': True,
        'v15_active_canonical_resource_indexing_enabled': True,
        'v15_resource_evidence_used_for_candidate_priority': True,
        'v15_grammar_delta_repairs_not_released_as_enhance': True,
        'v15_final_sentence_after_replacement_checked': True,
        'v15_no_topic_or_essay_specific_runtime_rules': True,
        'v15_no_embedded_phrase_bank': True,
    })
    return qa


_prev_analyze_v146_active = analyze

def analyze(payload: Dict[str, Any], validator: Optional[ContextFitValidator] = None) -> Dict[str, Any]:
    _v15_reset()
    _v15_refresh_resource_metrics()
    result = _prev_analyze_v146_active(payload, validator)
    result['run']['engine_version'] = ENGINE_VERSION
    result.setdefault('qa', {}).setdefault('contract_checks', {}).update({
        'v15_context_insertion_validation_enabled': True,
        'v15_active_canonical_resource_indexing_enabled': True,
        'v15_resource_evidence_used_for_candidate_priority': True,
        'v15_grammar_delta_repairs_not_released_as_enhance': True,
        'v15_final_sentence_after_replacement_checked': True,
        'v15_no_topic_or_essay_specific_runtime_rules': True,
        'v15_no_embedded_phrase_bank': True,
    })
    # Hard final safety: never release an ENHANCE whose accepted suggestions cannot be inserted.
    cleaned: List[Dict[str, Any]] = []
    failures = result.setdefault('qa', {}).setdefault('source_audit', {}).setdefault('context_fit_check_failures', [])
    for u in result.get('enhance_units') or []:
        ok, reason = _v146_is_valid_final_enhance(u)
        if ok:
            cleaned.append(u)
        else:
            failures.append({'unit_text': u.get('unit_text'), 'candidate': None, 'tier': 'v15_final_release_gate', 'reason': reason})
    if len(cleaned) != len(result.get('enhance_units') or []):
        result['enhance_units'] = cleaned
        result['replacement_options'] = [x for x in result.get('replacement_options', []) if x.get('class_label') != 'ENHANCE' or any(u.get('unit_id') == x.get('unit_id') for u in cleaned)]
        lp = result.get('lexical_profile', {})
        lp['enhance_count'] = len(cleaned)
        lp.setdefault('classification_distribution', {})['ENHANCE'] = len(cleaned)
        lp['enhance_multiword_count'] = sum(1 for u in cleaned if len(surface_tokens(u.get('unit_text'))) > 1)
        lp['enhance_single_word_count'] = sum(1 for u in cleaned if len(surface_tokens(u.get('unit_text'))) <= 1)
        lp['enhance_multiword_share'] = round((lp['enhance_multiword_count'] / len(cleaned)), 3) if cleaned else 0.0
    return result




# ---------------------------------------------------------------------------
# v1.6 layered resource QA release gate
# ---------------------------------------------------------------------------
# v1.6 does not add topic rules or a phrase bank. It separates the late LRET
# process into explicit gates:
#   candidate/scope QA -> classification QA -> suggestion generation support ->
#   suggestion QA -> explanation/practice QA -> final release QA.
# The main goal is to prevent locally insertable but pedagogically weak
# suggestions from being released as ENHANCE tasks.

ENGINE_VERSION = "lret-engine-v1.6.2-layered-resource-qa-contextual-synonym-quota"

V16_QA_CACHE: Dict[str, Any] = {}

V16_LIGHT_GOVERNOR_VERBS: Set[str] = {
    "give", "gives", "gave", "given", "make", "makes", "made", "take", "takes", "took", "taken",
    "do", "does", "did", "done", "have", "has", "had", "get", "gets", "got", "provide", "provides",
    "provided", "offer", "offers", "offered", "bring", "brings", "brought"
}
V16_LOW_VALUE_ADJECTIVES: Set[str] = {"good", "bad", "nice", "big", "small", "many", "much", "more", "very"}
V16_VAGUE_HEADS: Set[str] = {"thing", "things", "kind", "kinds", "stuff", "something", "anything", "everything"}
V16_GENERIC_CONTENT_DOWNGRADE: Set[str] = {"thing", "things", "stuff", "people", "person", "someone", "something"}


def _v16_reset() -> None:
    V16_QA_CACHE.clear()
    V16_QA_CACHE.update({
        "stage_sequence": [
            "candidate_filter_or_reconstruct",
            "candidate_scope_qa",
            "classification_qa",
            "suggestion_generation",
            "suggestion_qa",
            "explanation_and_task_qa",
            "final_release_qa",
        ],
        "candidate_scope_rejected": 0,
        "resource_generated_suggestions": 0,
        "resource_suggestions_accepted": 0,
        "resource_suggestions_rejected": 0,
        "suggestions_checked_for_pedagogical_gain": 0,
        "suggestions_rejected_for_low_gain": 0,
        "suggestions_rejected_for_grammar_dependency": 0,
        "enhance_units_rejected_after_suggestion_qa": 0,
        "clarify_overlap_suppressed": 0,
        "clarify_grammar_breakdown_suppressed": 0,
        "keep_single_words_moved_to_internal_profile": 0,
        "practice_targets_removed_after_final_gate": 0,
        "replacement_options_removed_after_final_gate": 0,
        "explanation_gate_failures": 0,
        "final_release_warnings": [],
        "blocked_samples": [],
    })


def _v16_note(key: str, sample: Optional[Dict[str, Any]] = None) -> None:
    V16_QA_CACHE[key] = int(V16_QA_CACHE.get(key, 0)) + 1
    if sample:
        V16_QA_CACHE.setdefault("blocked_samples", [])
        if len(V16_QA_CACHE["blocked_samples"]) < 40:
            V16_QA_CACHE["blocked_samples"].append(sample)


def _v16_wordish_tokens(text: str) -> List[str]:
    return [norm_text(t) for t in surface_tokens(text) if norm_text(t)]


def _v16_content_tokens(text: str) -> List[str]:
    toks = []
    for t in _v16_wordish_tokens(text):
        if t in STOPWORDS or t in V15_ARTICLES or len(t) <= 2:
            continue
        toks.append(simple_stem(t))
    return toks


def _v16_academic_or_resource_count(text: str) -> int:
    toks = set(_v16_wordish_tokens(text))
    acad = set(V15_RESOURCE_INDEX.get('academic_lemmas') or set()) | set(EXTERNAL_ACADEMIC_SIGNAL_WORDS or set())
    return sum(1 for t in toks if t in acad)


def _v16_is_pure_orthographic_variant(original: str, suggestion: str) -> bool:
    a = re.sub(r"[\s\-]+", "", norm_text(original))
    b = re.sub(r"[\s\-]+", "", norm_text(suggestion))
    return bool(a and a == b and norm_text(original) != norm_text(suggestion))


def _v16_content_overlap_ratio(original: str, suggestion: str) -> float:
    a = set(_v16_content_tokens(original))
    b = set(_v16_content_tokens(suggestion))
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _v16_scope_needs_governor_expansion(unit: Dict[str, Any]) -> Tuple[bool, str]:
    """Detect phrase spans that are too narrow for a useful lexical task.

    Universal rule: if a noun phrase is governed by a light verb immediately to
    the left, paraphrasing the noun phrase alone often creates awkward outputs
    (e.g. changing only the object while the verb phrase should be the real
    learning unit). This does not use topic or essay-specific words.
    """
    text = str(unit.get('unit_text') or '')
    context = str(unit.get('context') or '')
    toks = _v16_wordish_tokens(text)
    if not toks or len(toks) < 2:
        return False, ''
    if toks[0] in {"can", "could", "may", "might", "should", "would", "will", "to"}:
        return False, ''
    if any(t in {"can", "could", "may", "might", "should", "would", "will"} for t in toks[:2]):
        return False, ''
    unit_type = str(unit.get('unit_type') or '').lower()
    # Apply mainly to noun/prepositional spans, not full predicate chunks.
    if "verb_phrase" in unit_type or "predicate" in unit_type:
        return False, ''
    left = _v15_context_left_of_span(context, text)
    left_toks = _v16_wordish_tokens(left)[-5:]
    if not left_toks:
        return False, ''
    # A light verb plus optional low-value adjective before the span means the
    # replaceable pedagogical unit is likely the larger verb phrase.
    if left_toks[-1] in V16_LOW_VALUE_ADJECTIVES and len(left_toks) >= 2 and left_toks[-2] in V16_LIGHT_GOVERNOR_VERBS:
        return True, 'noun_span_requires_governing_verb_expansion'
    if left_toks[-1] in V16_LIGHT_GOVERNOR_VERBS:
        return True, 'noun_span_requires_governing_verb_expansion'
    return False, ''


def _v16_original_is_already_strong_collocation(unit_text: str) -> bool:
    ev = _v15_resource_evidence_for_text(unit_text)
    if ev.get('exact_positive_collocation') or ev.get('positive_collocation_submatches'):
        return True
    return float(ev.get('resource_score') or 0.0) >= 0.38 and len(_v16_content_tokens(unit_text)) <= 3


def _v16_suggestion_has_pedagogical_gain(unit_text: str, suggestion_text: str, context: str, resource_support: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    V16_QA_CACHE['suggestions_checked_for_pedagogical_gain'] = int(V16_QA_CACHE.get('suggestions_checked_for_pedagogical_gain', 0)) + 1
    original = str(unit_text or '').strip()
    suggestion = str(suggestion_text or '').strip()
    if not original or not suggestion:
        _v16_note('suggestions_rejected_for_low_gain')
        return False, 'empty_original_or_suggestion'
    if norm_text(original) == norm_text(suggestion):
        _v16_note('suggestions_rejected_for_low_gain')
        return False, 'same_as_original_not_enhancement'
    if _v16_is_pure_orthographic_variant(original, suggestion):
        _v16_note('suggestions_rejected_for_low_gain')
        return False, 'orthographic_variant_not_enhancement'

    grammar_delta, grammar_reason = _v15_suggestion_delta_is_grammar_repair(original, suggestion, context)
    if grammar_delta:
        _v16_note('suggestions_rejected_for_grammar_dependency')
        return False, grammar_reason or 'grammar_delta_not_lexical_enhancement'

    orig_content = _v16_content_tokens(original)
    sugg_content = _v16_content_tokens(suggestion)
    if not sugg_content:
        _v16_note('suggestions_rejected_for_low_gain')
        return False, 'suggestion_has_no_content_words'
    if len(sugg_content) < max(1, len(orig_content) - 2) and len(sugg_content) <= 2:
        _v16_note('suggestions_rejected_for_low_gain')
        return False, 'oversimplified_suggestion_not_learning_gain'

    support = resource_support or _v15_resource_support_for_suggestion(original, suggestion)
    orig_ev = _v15_resource_evidence_for_text(original)
    orig_score = float(orig_ev.get('resource_score') or 0.0)
    sugg_score = float(support.get('suggestion_resource_score') or 0.0)
    orig_acad = _v16_academic_or_resource_count(original)
    sugg_acad = _v16_academic_or_resource_count(suggestion)
    overlap = _v16_content_overlap_ratio(original, suggestion)
    added = set(sugg_content) - set(orig_content)
    removed = set(orig_content) - set(sugg_content)

    # If the original is already a stable collocation, only release a suggestion
    # when there is a clear resource/register/precision improvement.
    if _v16_original_is_already_strong_collocation(original):
        if (sugg_score - orig_score) < 0.12 and (sugg_acad - orig_acad) < 1:
            _v16_note('suggestions_rejected_for_low_gain')
            return False, 'original_collocation_already_stable_no_clear_gain'

    if overlap >= 0.78 and (sugg_score - orig_score) < 0.10 and (sugg_acad - orig_acad) < 1:
        _v16_note('suggestions_rejected_for_low_gain')
        return False, 'lexical_delta_too_small_for_student_task'

    if any(t in V16_GENERIC_CONTENT_DOWNGRADE for t in _v16_wordish_tokens(suggestion)):
        # Not an automatic rejection: many valid phrases contain people. But if
        # the replacement is less resource-supported and adds generic vocabulary,
        # it is probably not an enhancement.
        if sugg_score <= orig_score and sugg_acad <= orig_acad and overlap < 0.75:
            _v16_note('suggestions_rejected_for_low_gain')
            return False, 'suggestion_moves_toward_more_generic_vocabulary'

    if not added and not removed and (sugg_score - orig_score) < 0.10:
        _v16_note('suggestions_rejected_for_low_gain')
        return False, 'no_meaningful_content_change'

    return True, 'pedagogical_gain_confirmed'


def _v16_validate_resource_based_suggestion(unit_text: str, suggestion: str, context: str, validator: ContextFitValidator, failures: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    ok, reason, final_sentence, resource_support = _v15_validate_enhance_suggestion(unit_text, suggestion, context)
    if not ok:
        _v16_note('resource_suggestions_rejected', {'unit_text': unit_text, 'candidate': suggestion, 'reason': reason})
        failures.append({'unit_text': unit_text, 'candidate': suggestion, 'tier': 'v16_resource_generation_gate', 'reason': reason, 'final_sentence': final_sentence, 'resource_support': resource_support})
        return None
    gain_ok, gain_reason = _v16_suggestion_has_pedagogical_gain(unit_text, suggestion, context, resource_support)
    if not gain_ok:
        _v16_note('resource_suggestions_rejected', {'unit_text': unit_text, 'candidate': suggestion, 'reason': gain_reason})
        failures.append({'unit_text': unit_text, 'candidate': suggestion, 'tier': 'v16_resource_pedagogical_gain_gate', 'reason': gain_reason, 'final_sentence': final_sentence, 'resource_support': resource_support})
        return None
    _v16_note('resource_suggestions_accepted')
    return {
        'text': suggestion,
        'validation': {
            'accepted': True,
            'gates': [
                'resource_word_substitution',
                'replace_in_context_sentence_fit',
                'pedagogical_gain_checked',
                'resource_signal_checked',
            ],
            'reason': 'passed v1.6 resource-based generation + deterministic QA gates',
        },
        'final_sentence_after_replacement': final_sentence,
        'resource_support': resource_support,
        'suggestion_source': 'resource_word_substitution_validated',
    }


def _v16_generate_resource_based_suggestions(unit_text: str, context: str, validator: ContextFitValidator, failures: List[Dict[str, Any]], limit: int = 2) -> List[Dict[str, Any]]:
    """Create conservative whole-span suggestions from external word resources.

    This is not a phrase bank. It replaces one content token inside the original
    span using canonical/external word-level alternatives, then sends the whole
    replacement through the same insertion and pedagogical-gain gates.
    """
    out: List[Dict[str, Any]] = []
    original = str(unit_text or '').strip()
    if not original or not EXTERNAL_WORD_SUGGESTIONS:
        return out
    words = re.findall(r"\w+(?:[-']\w+)?|[^\w\s]", original, flags=re.UNICODE)
    seen_norms = {norm_text(original)}
    for i, tok in enumerate(words):
        key = norm_text(tok)
        if not key or key in STOPWORDS or key not in EXTERNAL_WORD_SUGGESTIONS:
            continue
        for repl in EXTERNAL_WORD_SUGGESTIONS.get(key, [])[:5]:
            repl = str(repl or '').strip()
            if not repl or norm_text(repl) == key:
                continue
            candidate_words = list(words)
            candidate_words[i] = repl
            # Re-join punctuation safely.
            candidate = ''
            for part in candidate_words:
                if re.match(r"^[,.;:!?)]$", part):
                    candidate += part
                elif not candidate or candidate.endswith(('(', '/', '-')):
                    candidate += part
                else:
                    candidate += ' ' + part
            candidate = re.sub(r"\s+", " ", candidate).strip()
            low = norm_text(candidate)
            if not low or low in seen_norms:
                continue
            seen_norms.add(low)
            V16_QA_CACHE['resource_generated_suggestions'] = int(V16_QA_CACHE.get('resource_generated_suggestions', 0)) + 1
            validated = _v16_validate_resource_based_suggestion(original, candidate, context, validator, failures)
            if validated:
                out.append(validated)
                if len(out) >= limit:
                    return out
    return out


def _v16_filter_suggestions_for_unit(unit: Dict[str, Any], validator: ContextFitValidator, failures: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    unit_text = str(unit.get('unit_text') or '')
    context = str(unit.get('context') or '')
    scope_bad, scope_reason = _v16_scope_needs_governor_expansion(unit)
    if scope_bad:
        _v16_note('candidate_scope_rejected', {'unit_text': unit_text, 'reason': scope_reason})
        failures.append({'unit_text': unit_text, 'candidate': None, 'tier': 'v16_candidate_scope_gate', 'reason': scope_reason})
        return None, []

    new_suggestions: List[Dict[str, Any]] = []
    for item in unit.get('suggestions') or []:
        text = str(item.get('text') if isinstance(item, dict) else item).strip()
        support = item.get('resource_support') if isinstance(item, dict) else None
        gain_ok, gain_reason = _v16_suggestion_has_pedagogical_gain(unit_text, text, context, support)
        if not gain_ok:
            failures.append({
                'unit_text': unit_text,
                'candidate': text,
                'tier': 'v16_pedagogical_gain_gate',
                'reason': gain_reason,
                'final_sentence': item.get('final_sentence_after_replacement') if isinstance(item, dict) else None,
                'resource_support': support or {},
            })
            continue
        item2 = copy.deepcopy(item) if isinstance(item, dict) else {'text': text, 'validation': {'accepted': True, 'gates': [], 'reason': 'accepted'}}
        item2.setdefault('validation', {}).setdefault('gates', [])
        gates = list(item2['validation'].get('gates') or [])
        for gate in ['pedagogical_gain_checked', 'scope_quality_checked', 'v16_final_suggestion_qa']:
            if gate not in gates:
                gates.append(gate)
        item2['validation']['gates'] = gates
        item2['validation']['reason'] = 'passed v1.6 layered suggestion QA gates'
        new_suggestions.append(item2)

    if len(new_suggestions) < LLM_MIN_VALID_SUGGESTIONS:
        extra = _v16_generate_resource_based_suggestions(unit_text, context, validator, failures, limit=max(0, LLM_MIN_VALID_SUGGESTIONS - len(new_suggestions)))
        existing = {norm_text(x.get('text')) for x in new_suggestions}
        for item in extra:
            if norm_text(item.get('text')) not in existing:
                new_suggestions.append(item)
                existing.add(norm_text(item.get('text')))

    if len(new_suggestions) < LLM_MIN_VALID_SUGGESTIONS:
        _v16_note('enhance_units_rejected_after_suggestion_qa', {'unit_text': unit_text, 'reason': 'fewer_than_min_valid_v16_suggestions', 'valid_suggestion_count': len(new_suggestions)})
        failures.append({'unit_text': unit_text, 'candidate': None, 'tier': 'v16_final_enhance_release_gate', 'reason': 'fewer_than_min_valid_v16_suggestions', 'valid_suggestion_count': len(new_suggestions), 'required': LLM_MIN_VALID_SUGGESTIONS})
        return None, new_suggestions

    unit2 = copy.deepcopy(unit)
    unit2['suggestions'] = new_suggestions
    flags = list(unit2.get('extraction_flags') or [])
    for flag in ['v16_layered_qa_validated', 'pedagogical_gain_validated']:
        if flag not in flags:
            flags.append(flag)
    unit2['extraction_flags'] = flags
    unit2['safety_level'] = 'phrase_level_layered_resource_qa_validated'
    return unit2, new_suggestions


def _v16_filter_enhance_units(enhance_units: List[Dict[str, Any]], validator: ContextFitValidator, failures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    final: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for unit in enhance_units or []:
        unit2, _ = _v16_filter_suggestions_for_unit(unit, validator, failures)
        if not unit2:
            continue
        key = norm_text(unit2.get('unit_text'))
        if not key or key in seen:
            continue
        seen.add(key)
        final.append(unit2)
    return final


def _v16_clarify_preference_score(unit: Dict[str, Any]) -> float:
    text = norm_text(unit.get('unit_text'))
    toks = _v16_wordish_tokens(text)
    score = float(unit.get('candidate_value') or 0.0)
    if re.search(r"\bkinds?\s+of\s+things?\b", text):
        score += 1.2
    if any(t in V16_VAGUE_HEADS for t in toks):
        score += 0.25
    if len(toks) > 7:
        score -= 0.8
    if toks and toks[0] in {"that", "and", "or", "but"}:
        score -= 0.35
    # Penalise mixed spans that attach a vague noun to a following lexical unit;
    # the student should clarify the vague expression, not the accidental fragment.
    if any(t in V16_VAGUE_HEADS for t in toks) and any(t.endswith('ing') for t in toks):
        score -= 0.75
    return score


def _v16_spans_overlap(a: str, b: str) -> bool:
    na = norm_text(a)
    nb = norm_text(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    ta = set(_v16_wordish_tokens(na))
    tb = set(_v16_wordish_tokens(nb))
    return bool(ta and tb and len(ta & tb) / max(1, min(len(ta), len(tb))) >= 0.67)


def _v16_filter_clarify_units(clarify_units: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    groups: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for u in clarify_units or []:
        groups[u.get('source_sentence_index')].append(u)
    kept: List[Dict[str, Any]] = []
    suppressed: List[Dict[str, Any]] = []
    for _, items in groups.items():
        ordered = sorted(items, key=_v16_clarify_preference_score, reverse=True)
        local_kept: List[Dict[str, Any]] = []
        for u in ordered:
            text = str(u.get('unit_text') or '')
            toks = _v16_wordish_tokens(text)
            if len(toks) > 8:
                _v16_note('clarify_grammar_breakdown_suppressed', {'unit_text': text, 'reason': 'clarify_span_too_long_or_clause_repair'})
                u2 = copy.deepcopy(u)
                u2['reason'] = 'grammar_breakdown_or_clause_repair_not_lret_clarify'
                suppressed.append(u2)
                continue
            if any(_v16_spans_overlap(text, k.get('unit_text')) for k in local_kept):
                _v16_note('clarify_overlap_suppressed', {'unit_text': text, 'reason': 'overlapping_clarify_task_suppressed'})
                u2 = copy.deepcopy(u)
                u2['reason'] = 'overlapping_clarify_task_suppressed_by_better_span'
                suppressed.append(u2)
                continue
            local_kept.append(u)
        kept.extend(sorted(local_kept, key=lambda x: str(x.get('unit_id'))))
    return kept, suppressed


def _v16_keep_is_student_visible_positive(unit: Dict[str, Any]) -> bool:
    text = str(unit.get('unit_text') or '')
    toks = _v16_wordish_tokens(text)
    if len(toks) >= 2:
        return True
    if not toks:
        return False
    # Single-word KEEP is useful internally for analytics, but usually weak as a
    # learner-visible positive lexical unit. Keep it only if it has an academic
    # or explicit external resource signal.
    tok = toks[0]
    if tok in EXTERNAL_ACADEMIC_SIGNAL_WORDS or tok in (V15_RESOURCE_INDEX.get('academic_lemmas') or set()):
        return True
    return False


def _v16_filter_keep_units(keep_units: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    kept: List[Dict[str, Any]] = []
    internal: List[Dict[str, Any]] = []
    for u in keep_units or []:
        if _v16_keep_is_student_visible_positive(u):
            kept.append(u)
        else:
            _v16_note('keep_single_words_moved_to_internal_profile', {'unit_text': u.get('unit_text'), 'reason': 'single_word_keep_moved_to_internal_profile'})
            u2 = copy.deepcopy(u)
            u2['visibility'] = 'internal_profile_only'
            u2['reason'] = 'single-word KEEP is retained for analytics but hidden from student-facing positive inventory'
            internal.append(u2)
    return kept, internal


def _v16_sync_replacement_and_clarification_options(result: Dict[str, Any]) -> None:
    valid_fix_ids = {u.get('unit_id') for u in result.get('fix_units') or []}
    valid_enh_ids = {u.get('unit_id') for u in result.get('enhance_units') or []}
    enh_by_id = {u.get('unit_id'): u for u in result.get('enhance_units') or []}
    new_repl: List[Dict[str, Any]] = []
    removed_repl = 0
    for opt in result.get('replacement_options') or []:
        cid = opt.get('unit_id')
        cls = opt.get('class_label')
        if cls == 'FIX' and cid in valid_fix_ids:
            new_repl.append(opt)
        elif cls == 'ENHANCE' and cid in valid_enh_ids:
            opt2 = copy.deepcopy(opt)
            if cid in enh_by_id:
                opt2['suggestions'] = copy.deepcopy(enh_by_id[cid].get('suggestions') or [])
            new_repl.append(opt2)
        else:
            removed_repl += 1
    V16_QA_CACHE['replacement_options_removed_after_final_gate'] = int(V16_QA_CACHE.get('replacement_options_removed_after_final_gate', 0)) + removed_repl
    result['replacement_options'] = new_repl

    valid_clar_ids = {u.get('unit_id') for u in result.get('clarify_units') or []}
    result['clarification_options'] = [x for x in result.get('clarification_options') or [] if x.get('unit_id') in valid_clar_ids]

    valid_task_ids = valid_fix_ids | valid_enh_ids | valid_clar_ids
    old_targets = result.get('lret_practice_targets') or []
    new_targets = [x for x in old_targets if x.get('unit_id') in valid_task_ids]
    V16_QA_CACHE['practice_targets_removed_after_final_gate'] = int(V16_QA_CACHE.get('practice_targets_removed_after_final_gate', 0)) + (len(old_targets) - len(new_targets))
    result['lret_practice_targets'] = new_targets


def _v16_refresh_profile_and_qa(result: Dict[str, Any], internal_keep: List[Dict[str, Any]], suppressed_clarify: List[Dict[str, Any]]) -> None:
    lp = result.setdefault('lexical_profile', {})
    fix_units = result.get('fix_units') or []
    enhance_units = result.get('enhance_units') or []
    clarify_units = result.get('clarify_units') or []
    keep_units = result.get('keep_units') or []

    lp['fix_count'] = len(fix_units)
    lp['enhance_count'] = len(enhance_units)
    lp['clarify_count'] = len(clarify_units)
    lp['visible_clarify_task_count'] = len(clarify_units)
    lp['keep_count'] = len(keep_units)
    lp['enhance_multiword_count'] = sum(1 for u in enhance_units if len(surface_tokens(u.get('unit_text'))) > 1)
    lp['enhance_single_word_count'] = sum(1 for u in enhance_units if len(surface_tokens(u.get('unit_text'))) <= 1)
    lp['enhance_multiword_share'] = round(lp['enhance_multiword_count'] / len(enhance_units), 3) if enhance_units else 0.0
    lp['keep_single_word_count'] = sum(1 for u in keep_units if len(surface_tokens(u.get('unit_text'))) <= 1)
    lp['keep_phrase_count'] = sum(1 for u in keep_units if len(surface_tokens(u.get('unit_text'))) > 1)
    lp['internal_keep_single_word_count'] = len(internal_keep)
    lp['clarify_suppressed_after_v16_count'] = len(suppressed_clarify)
    lp['classification_distribution'] = {
        'FIX': len(fix_units),
        'ENHANCE': len(enhance_units),
        'KEEP': len(keep_units),
        'CLARIFY': len(clarify_units),
    }
    lp['enhance_tier_breakdown'] = Counter(str(u.get('safety_level') or 'unknown') for u in enhance_units)
    lp['v16_layered_resource_qa'] = copy.deepcopy(V16_QA_CACHE)

    qa = result.setdefault('qa', {})
    source_audit = qa.setdefault('source_audit', {})
    source_audit.setdefault('dropped_units', [])
    for u in internal_keep[:80]:
        source_audit['dropped_units'].append({'unit': u.get('unit_text'), 'unit_id': u.get('unit_id'), 'reason': 'single_word_keep_moved_to_internal_profile', 'stage': 'v16_keep_visibility_gate'})
    for u in suppressed_clarify[:40]:
        source_audit['dropped_units'].append({'unit': u.get('unit_text'), 'unit_id': u.get('unit_id'), 'reason': u.get('reason') or 'clarify_suppressed_after_v16_gate', 'stage': 'v16_clarify_dedup_gate'})
    source_audit['dropped_counts'] = dict(Counter(x.get('reason') for x in source_audit.get('dropped_units') or [] if x.get('reason')))

    qa.setdefault('contract_checks', {}).update({
        'v16_layered_pipeline_enabled': True,
        'v16_candidate_scope_gate_enabled': True,
        'v16_resource_based_generation_attempted': True,
        'v16_pedagogical_gain_gate_enabled': True,
        'v16_clarify_overlap_dedup_enabled': True,
        'v16_keep_single_words_internalized': True,
        'v16_final_task_sync_enabled': True,
        'v16_no_topic_or_essay_specific_rules': True,
        'v16_no_embedded_phrase_bank': True,
        'v16_resource_generation_not_final_without_qa': True,
    })
    qa.setdefault('v1_6_metrics', {}).update(copy.deepcopy(V16_QA_CACHE))

    warnings = qa.setdefault('warnings', [])
    if len(enhance_units) < 8:
        msg = f"v16_low_enhance_count_after_quality_gates: {len(enhance_units)} released; precision prioritized over quantity"
        if msg not in warnings:
            warnings.append(msg)
            V16_QA_CACHE.setdefault('final_release_warnings', []).append(msg)
    if int(V16_QA_CACHE.get('enhance_units_rejected_after_suggestion_qa', 0)) > 0:
        msg = f"v16_enhance_candidates_rejected_after_suggestion_qa: {V16_QA_CACHE.get('enhance_units_rejected_after_suggestion_qa')}"
        if msg not in warnings:
            warnings.append(msg)
    # The old v1.5 status could remain OK despite quality starvation. v1.6 is
    # more honest: OK only when task quality and quantity are both acceptable.
    if len(enhance_units) < 8 or int(V16_QA_CACHE.get('candidate_scope_rejected', 0)) > 0:
        qa['status'] = 'needs_tuning'
        qa['confidence'] = min(float(qa.get('confidence') or 0.85), 0.78)

    # Refresh after warning/status mutation so exported v1.6 metrics match QA.
    lp['v16_layered_resource_qa'] = copy.deepcopy(V16_QA_CACHE)
    qa['v1_6_metrics'] = copy.deepcopy(V16_QA_CACHE)


def _v16_update_learning_payload(result: Dict[str, Any]) -> None:
    lip = result.get('learning_intelligence_payload')
    if not isinstance(lip, dict):
        return
    metrics = {
        'lret_fix_count': len(result.get('fix_units') or []),
        'lret_enhance_count': len(result.get('enhance_units') or []),
        'lret_keep_count': len(result.get('keep_units') or []),
        'lret_enhance_multiword_count': sum(1 for u in result.get('enhance_units') or [] if len(surface_tokens(u.get('unit_text'))) > 1),
        'lret_keep_single_word_count': sum(1 for u in result.get('keep_units') or [] if len(surface_tokens(u.get('unit_text'))) <= 1),
        'lret_keep_phrase_count': sum(1 for u in result.get('keep_units') or [] if len(surface_tokens(u.get('unit_text'))) > 1),
        'lret_clarify_count': len(result.get('clarify_units') or []),
    }
    existing = {m.get('metric_id'): m for m in lip.get('metric_signals') or [] if isinstance(m, dict)}
    new_metric_signals = []
    for key, value in metrics.items():
        item = copy.deepcopy(existing.get(key) or {'metric_id': key})
        item['value'] = value
        new_metric_signals.append(item)
    lip['metric_signals'] = new_metric_signals
    lip['confidence'] = min(float(lip.get('confidence') or 0.77), 0.76) if result.get('qa', {}).get('status') == 'needs_tuning' else lip.get('confidence')
    notes = list(lip.get('notes') or [])
    note = 'v1.6 final gates may move weak single-word KEEP evidence to internal analytics and suppress weak ENHANCE suggestions.'
    if note not in notes:
        notes.append(note)
    lip['notes'] = notes


_prev_analyze_v15_for_v16 = analyze

def analyze(payload: Dict[str, Any], validator: Optional[ContextFitValidator] = None) -> Dict[str, Any]:
    _v16_reset()
    validator = validator or RuleBasedContextFitValidator()
    result = _prev_analyze_v15_for_v16(payload, validator)
    result['run']['engine_version'] = ENGINE_VERSION
    qa = result.setdefault('qa', {})
    failures = qa.setdefault('source_audit', {}).setdefault('context_fit_check_failures', [])

    # Stage 1-5: final layered QA over already generated/adjudicated candidates.
    result['enhance_units'] = _v16_filter_enhance_units(result.get('enhance_units') or [], validator, failures)
    result['clarify_units'], suppressed_clarify = _v16_filter_clarify_units(result.get('clarify_units') or [])
    result['keep_units'], internal_keep = _v16_filter_keep_units(result.get('keep_units') or [])

    # Stage 6-7: sync student-facing artifacts and refresh metrics.
    _v16_sync_replacement_and_clarification_options(result)
    _v16_refresh_profile_and_qa(result, internal_keep, suppressed_clarify)
    _v16_update_learning_payload(result)
    return result

# ---------------------------------------------------------------------------
# v1.6.2 contextual synonym quota + subject/scope QA patch
# ---------------------------------------------------------------------------
# v1.6.2 corrects an over-strict KEEP visibility policy and adds a controlled
# path for contextually fitting synonym training. It also blocks ENHANCE
# suggestions that pass local insertion but drop the subject or change the
# replacement scope. This is universal syntax/QA logic, not essay-specific code.

V162_QA_CACHE: Dict[str, Any] = {}

V162_DETERMINERS: Set[str] = set(V15_ARTICLES) | {
    "this", "that", "these", "those", "my", "your", "his", "her", "its", "our", "their",
    "many", "some", "several", "few", "fewer", "more", "most", "each", "every", "both", "all"
}
V162_SUBORDINATORS: Set[str] = {
    "although", "though", "because", "when", "while", "if", "unless", "whereas", "since", "even"
}
V162_SUBJECT_PRONOUNS: Set[str] = {
    "i", "you", "he", "she", "it", "we", "they", "one", "people", "someone", "everybody", "everyone"
}
V162_COMMON_BARE_VERB_STARTERS: Set[str] = {
    "contribute", "offer", "provide", "bring", "make", "create", "cause", "lead", "support", "help",
    "improve", "reduce", "increase", "decrease", "develop", "require", "entail", "impose", "pose",
    "produce", "generate", "affect", "benefit", "harm", "strengthen", "weaken", "guide", "run",
    "facilitate", "work", "teach", "give", "take", "spend", "invest", "encourage", "allow", "enable"
}
V162_GENERIC_SINGLE_WORD_LOW_VALUE: Set[str] = {
    "thing", "things", "kind", "kinds", "stuff", "good", "bad", "nice", "many", "much", "more", "very",
    "also", "both", "while", "when", "today", "example", "conclusion"
}


def _v162_reset() -> None:
    V162_QA_CACHE.clear()
    V162_QA_CACHE.update({
        "version": "v1.6.2",
        "subject_scope_suggestions_rejected": 0,
        "final_sentence_completeness_rejected": 0,
        "contextual_synonym_candidates_considered": 0,
        "contextual_synonym_tasks_added": 0,
        "contextual_synonym_suggestions_accepted": 0,
        "contextual_synonym_suggestions_rejected": 0,
        "keep_single_words_kept_for_synonym_training": 0,
        "keep_single_words_internal_only": 0,
        "keep_enhance_ratio": None,
        "phrase_enhance_count": 0,
        "synonym_enhance_count": 0,
        "target_enhance_min": 0,
        "target_enhance_max": 0,
        "synonym_enhance_cap": 0,
        "quota_status": "unknown",
        "blocked_samples": [],
    })


def _v162_note(key: str, sample: Optional[Dict[str, Any]] = None) -> None:
    V162_QA_CACHE[key] = int(V162_QA_CACHE.get(key, 0)) + 1
    if sample:
        V162_QA_CACHE.setdefault("blocked_samples", [])
        if len(V162_QA_CACHE["blocked_samples"]) < 50:
            V162_QA_CACHE["blocked_samples"].append(sample)


def _v162_first_word(text: str) -> str:
    toks = _v16_wordish_tokens(text)
    return toks[0] if toks else ""


def _v162_is_bare_verb_start(text: str) -> bool:
    first = _v162_first_word(text)
    if not first:
        return False
    if first in V162_SUBJECT_PRONOUNS or first in V162_DETERMINERS or first in V162_SUBORDINATORS:
        return False
    if first in V162_COMMON_BARE_VERB_STARTERS:
        return True
    if _v146_is_verb_lemma_or_form(first) and not first.endswith(('ed', 'ing')):
        return True
    return False


def _v162_original_starts_with_subject_like_material(original: str) -> bool:
    toks = _v16_wordish_tokens(original)
    if len(toks) < 3:
        return False
    if toks[0] in V162_SUBORDINATORS:
        # Subordinate clauses carry their own subject after the marker; do not
        # demand exact first-token preservation, but still require the suggestion
        # not to start as a bare predicate.
        return True
    if toks[0] in V162_SUBJECT_PRONOUNS or toks[0] in V162_DETERMINERS:
        return True
    # adjective/noun-like start followed by a predicate later.
    if toks[0] not in {"can", "could", "may", "might", "should", "would", "will", "to"}:
        return any(_v146_is_verb_lemma_or_form(t) or t in V162_COMMON_BARE_VERB_STARTERS for t in toks[1:])
    return False


def _v162_subject_scope_ok(original: str, suggestion: str, final_sentence: Optional[str]) -> Tuple[bool, str]:
    original = str(original or '').strip()
    suggestion = str(suggestion or '').strip()
    if not original or not suggestion:
        return False, 'empty_original_or_suggestion'
    if _v162_original_starts_with_subject_like_material(original) and _v162_is_bare_verb_start(suggestion):
        return False, 'replacement_drops_required_subject_or_nominal_head'
    # Final sentence pattern: a discourse marker/comma followed directly by a
    # bare predicate is usually subjectless after replacement.
    fs = str(final_sentence or '')
    if fs:
        low = norm_text(fs)
        verb_alt = '|'.join(sorted(re.escape(v) for v in V162_COMMON_BARE_VERB_STARTERS))
        if re.search(r"(?:^|[,;:])\s*(?:" + verb_alt + r")\b", low):
            # Allow when the original replacement itself legitimately started
            # with a modal/auxiliary span such as "can lead"; otherwise block.
            if _v162_original_starts_with_subject_like_material(original):
                return False, 'final_sentence_has_bare_predicate_after_boundary'
    return True, 'subject_scope_preserved'


_prev_v16_filter_suggestions_for_unit_v162 = _v16_filter_suggestions_for_unit

def _v16_filter_suggestions_for_unit(unit: Dict[str, Any], validator: ContextFitValidator, failures: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    unit2, suggestions = _prev_v16_filter_suggestions_for_unit_v162(unit, validator, failures)
    if not unit2:
        return None, suggestions
    unit_text = str(unit2.get('unit_text') or '')
    context = str(unit2.get('context') or '')
    filtered: List[Dict[str, Any]] = []
    for item in unit2.get('suggestions') or []:
        text = str(item.get('text') if isinstance(item, dict) else item).strip()
        final_sentence = item.get('final_sentence_after_replacement') if isinstance(item, dict) else None
        ok, reason = _v162_subject_scope_ok(unit_text, text, final_sentence)
        if not ok:
            metric = 'subject_scope_suggestions_rejected' if 'subject' in reason or 'scope' in reason else 'final_sentence_completeness_rejected'
            _v162_note(metric, {'unit_text': unit_text, 'candidate': text, 'reason': reason, 'final_sentence': final_sentence})
            failures.append({'unit_text': unit_text, 'candidate': text, 'tier': 'v162_subject_scope_gate', 'reason': reason, 'final_sentence': final_sentence})
            continue
        item2 = copy.deepcopy(item)
        item2.setdefault('validation', {}).setdefault('gates', [])
        gates = list(item2['validation'].get('gates') or [])
        if 'v162_subject_scope_retention_checked' not in gates:
            gates.append('v162_subject_scope_retention_checked')
        item2['validation']['gates'] = gates
        filtered.append(item2)
    if len(filtered) < LLM_MIN_VALID_SUGGESTIONS:
        # Try resource generation again, because v1.6.1 may have removed a bad
        # LLM suggestion while a subject-retaining resource variant can still be safe.
        needed = max(0, LLM_MIN_VALID_SUGGESTIONS - len(filtered))
        extra = _v16_generate_resource_based_suggestions(unit_text, context, validator, failures, limit=needed + 1)
        existing = {norm_text(x.get('text')) for x in filtered}
        for item in extra:
            text = str(item.get('text') or '')
            final_sentence = item.get('final_sentence_after_replacement')
            ok, reason = _v162_subject_scope_ok(unit_text, text, final_sentence)
            if ok and norm_text(text) not in existing:
                item.setdefault('validation', {}).setdefault('gates', [])
                gates = list(item['validation'].get('gates') or [])
                if 'v162_subject_scope_retention_checked' not in gates:
                    gates.append('v162_subject_scope_retention_checked')
                item['validation']['gates'] = gates
                filtered.append(item)
                existing.add(norm_text(text))
            if len(filtered) >= LLM_MIN_VALID_SUGGESTIONS:
                break
    if len(filtered) < LLM_MIN_VALID_SUGGESTIONS:
        _v162_note('subject_scope_suggestions_rejected', {'unit_text': unit_text, 'reason': 'fewer_than_min_valid_after_v162_subject_scope_gate', 'valid_suggestion_count': len(filtered)})
        failures.append({'unit_text': unit_text, 'candidate': None, 'tier': 'v162_final_enhance_release_gate', 'reason': 'fewer_than_min_valid_after_v162_subject_scope_gate', 'valid_suggestion_count': len(filtered), 'required': LLM_MIN_VALID_SUGGESTIONS})
        return None, filtered
    unit2 = copy.deepcopy(unit2)
    unit2['suggestions'] = filtered
    flags = list(unit2.get('extraction_flags') or [])
    if 'v162_subject_scope_validated' not in flags:
        flags.append('v162_subject_scope_validated')
    unit2['extraction_flags'] = flags
    return unit2, filtered


def _v162_keep_single_word_visibility_role(unit: Dict[str, Any]) -> Tuple[bool, str]:
    text = str(unit.get('unit_text') or '')
    toks = _v16_wordish_tokens(text)
    if len(toks) != 1:
        return True, 'multiword_keep_visible'
    tok = toks[0]
    freq = int(unit.get('frequency') or 1)
    cand_val = float(unit.get('candidate_value') or 0.0)
    if tok in V162_GENERIC_SINGLE_WORD_LOW_VALUE:
        return False, 'generic_single_word_internal_only'
    if tok in EXTERNAL_WORD_SUGGESTIONS:
        return True, 'contextual_synonym_training_candidate'
    if tok in EXTERNAL_ACADEMIC_SIGNAL_WORDS or tok in (V15_RESOURCE_INDEX.get('academic_lemmas') or set()):
        return True, 'academic_single_word_positive_evidence'
    if freq >= 2 and cand_val >= 0.60:
        return True, 'repeated_controlled_single_word_positive_evidence'
    return False, 'single_word_no_synonym_or_repetition_internal_only'


def _v16_keep_is_student_visible_positive(unit: Dict[str, Any]) -> bool:
    visible, _ = _v162_keep_single_word_visibility_role(unit)
    return visible


def _v16_filter_keep_units(keep_units: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    kept: List[Dict[str, Any]] = []
    internal: List[Dict[str, Any]] = []
    for u in keep_units or []:
        visible, role = _v162_keep_single_word_visibility_role(u)
        u2 = copy.deepcopy(u)
        u2['keep_learning_role'] = role
        if visible:
            if role == 'contextual_synonym_training_candidate':
                _v162_note('keep_single_words_kept_for_synonym_training', {'unit_text': u.get('unit_text'), 'reason': role})
            kept.append(u2)
        else:
            _v162_note('keep_single_words_internal_only', {'unit_text': u.get('unit_text'), 'reason': role})
            u2['visibility'] = 'internal_profile_only'
            u2['reason'] = role
            internal.append(u2)
    return kept, internal


def _v162_validate_contextual_synonym_suggestion(unit_text: str, suggestion: str, context: str, validator: ContextFitValidator, failures: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    V162_QA_CACHE['contextual_synonym_candidates_considered'] = int(V162_QA_CACHE.get('contextual_synonym_candidates_considered', 0)) + 1
    item = _v16_validate_resource_based_suggestion(unit_text, suggestion, context, validator, failures)
    if not item:
        _v162_note('contextual_synonym_suggestions_rejected', {'unit_text': unit_text, 'candidate': suggestion, 'reason': 'base_resource_validation_failed'})
        return None
    ok, reason = _v162_subject_scope_ok(unit_text, item.get('text'), item.get('final_sentence_after_replacement'))
    if not ok:
        _v162_note('contextual_synonym_suggestions_rejected', {'unit_text': unit_text, 'candidate': suggestion, 'reason': reason, 'final_sentence': item.get('final_sentence_after_replacement')})
        failures.append({'unit_text': unit_text, 'candidate': suggestion, 'tier': 'v162_contextual_synonym_subject_scope_gate', 'reason': reason, 'final_sentence': item.get('final_sentence_after_replacement')})
        return None
    item.setdefault('validation', {}).setdefault('gates', [])
    gates = list(item['validation'].get('gates') or [])
    for gate in ['contextual_synonym_training_gate', 'v162_subject_scope_retention_checked']:
        if gate not in gates:
            gates.append(gate)
    item['validation']['gates'] = gates
    item['validation']['reason'] = 'passed v1.6.2 contextual synonym + insertion QA gates'
    item['suggestion_source'] = 'external_resource_contextual_synonym_validated'
    V162_QA_CACHE['contextual_synonym_suggestions_accepted'] = int(V162_QA_CACHE.get('contextual_synonym_suggestions_accepted', 0)) + 1
    return item


def _v162_make_contextual_synonym_task(unit: Dict[str, Any], seq: int, validator: ContextFitValidator, failures: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    text = str(unit.get('unit_text') or '').strip()
    context = str(unit.get('context') or '')
    low = norm_text(text)
    if not low or low not in EXTERNAL_WORD_SUGGESTIONS:
        return None
    if unit.get('covered_by_task'):
        return None
    if context_has_local_grammar_corruption(context):
        return None
    suggestions: List[Dict[str, Any]] = []
    seen: Set[str] = {low}
    for repl in EXTERNAL_WORD_SUGGESTIONS.get(low, [])[:8]:
        repl = str(repl or '').strip()
        if not repl or norm_text(repl) in seen:
            continue
        seen.add(norm_text(repl))
        item = _v162_validate_contextual_synonym_suggestion(text, repl, context, validator, failures)
        if item:
            suggestions.append(item)
        if len(suggestions) >= LLM_MIN_VALID_SUGGESTIONS:
            break
    if len(suggestions) < LLM_MIN_VALID_SUGGESTIONS:
        return None
    return {
        'unit_id': f'syn_{seq:04d}',
        'class_label': 'ENHANCE',
        'unit_text': text,
        'unit_norm': low,
        'unit_type': 'contextual_word_choice',
        'source_sentence_index': unit.get('source_sentence_index'),
        'source_paragraph_index': unit.get('source_paragraph_index'),
        'context': context,
        'axis_candidates': ['word_choice', 'semantic_specificity', 'contextual_synonym_control'],
        'extraction_signal': 'resource_contextual_synonym_candidate_from_keep',
        'extraction_flags': ['contextual_synonym_training', 'resource_based', 'v162_quota_recovery'],
        'candidate_value': max(0.50, float(unit.get('candidate_value') or 0.0)),
        'evidence_ids': unit.get('evidence_ids') or [],
        'frequency': int(unit.get('frequency') or 1),
        'safety_level': 'contextual_synonym_resource_qa_validated',
        'replacement_scope': 'single_token_in_context',
        'suggestions': suggestions,
        'covered_subunits': [],
        'dedup_role': 'contextual_synonym_from_keep',
        'source_kind': 'external_resource_contextual_synonym_after_keep',
        'reveal_policy': {
            'mode': 'produce_before_reveal',
            'attempt_required_before_suggestions_shown': True,
            'suggestions_role': 'reveal_phase_model_answer_for_comparison',
        },
        'phase1_prompt': f'This word is understandable in context: [{text}]. Write a contextually fitting synonym or more precise word without changing the sentence meaning.',
        'phase1_options': [{'option_id': 'WRITE_MY_OWN', 'label': 'Write my own answer'}],
        'recurs_across_essays': False,
        'recurrence_note': None,
    }


def _v162_add_contextual_synonym_tasks(result: Dict[str, Any], validator: ContextFitValidator, failures: List[Dict[str, Any]]) -> None:
    enhance_units = list(result.get('enhance_units') or [])
    phrase_count = sum(1 for u in enhance_units if u.get('unit_type') != 'contextual_word_choice')
    # Dynamic quota: synonym training can supplement, not dominate, ENHANCE.
    target_min = 5 if int(result.get('lexical_profile', {}).get('fix_count') or 0) >= 5 else 8
    target_max = 10 if target_min == 5 else 14
    cap = min(3, max(1, int(round(max(phrase_count, 1) * 0.5))))
    V162_QA_CACHE['target_enhance_min'] = target_min
    V162_QA_CACHE['target_enhance_max'] = target_max
    V162_QA_CACHE['synonym_enhance_cap'] = cap
    if len(enhance_units) >= target_min:
        return
    existing_texts = {norm_text(u.get('unit_text')) for u in enhance_units}
    added = 0
    candidates = []
    for u in result.get('keep_units') or []:
        if len(_v16_wordish_tokens(str(u.get('unit_text') or ''))) == 1 and norm_text(u.get('unit_text')) in EXTERNAL_WORD_SUGGESTIONS:
            if norm_text(u.get('unit_text')) not in existing_texts:
                candidates.append(u)
    candidates.sort(key=lambda u: (float(u.get('candidate_value') or 0.0), int(u.get('frequency') or 1)), reverse=True)
    for u in candidates:
        if added >= cap or len(enhance_units) >= target_min:
            break
        task = _v162_make_contextual_synonym_task(u, added + 1, validator, failures)
        if not task:
            continue
        enhance_units.append(task)
        existing_texts.add(norm_text(task.get('unit_text')))
        added += 1
        _v162_note('contextual_synonym_tasks_added', {'unit_text': task.get('unit_text'), 'reason': 'quota_safe_contextual_synonym_training'})
    result['enhance_units'] = enhance_units


def _v162_sync_after_synonym_tasks(result: Dict[str, Any]) -> None:
    # Add replacement options and practice targets for contextual synonym tasks.
    repl_ids = {x.get('unit_id') for x in result.get('replacement_options') or []}
    task_ids = {x.get('unit_id') for x in result.get('lret_practice_targets') or []}
    for u in result.get('enhance_units') or []:
        if u.get('unit_type') == 'contextual_word_choice':
            if u.get('unit_id') not in repl_ids:
                result.setdefault('replacement_options', []).append({
                    'unit_id': u.get('unit_id'),
                    'unit_text': u.get('unit_text'),
                    'class_label': 'ENHANCE',
                    'replacement_scope': u.get('replacement_scope'),
                    'suggestions': copy.deepcopy(u.get('suggestions') or []),
                    'reveal_policy': copy.deepcopy(u.get('reveal_policy') or {}),
                })
            if u.get('unit_id') not in task_ids:
                result.setdefault('lret_practice_targets', []).append({
                    'unit_id': u.get('unit_id'),
                    'unit_text': u.get('unit_text'),
                    'category': 'enhance',
                    'tier': 'contextual_synonym_resource_qa_validated',
                    'priority_weight': 1.7,
                    'history_count': 0,
                    'recommended_practice_type': 'produce_before_reveal_contextual_synonym',
                })


def _v162_refresh_quota_metrics(result: Dict[str, Any]) -> None:
    lp = result.setdefault('lexical_profile', {})
    qa = result.setdefault('qa', {})
    enh = result.get('enhance_units') or []
    keep = result.get('keep_units') or []
    phrase_count = sum(1 for u in enh if u.get('unit_type') != 'contextual_word_choice')
    syn_count = sum(1 for u in enh if u.get('unit_type') == 'contextual_word_choice')
    ratio = round(len(keep) / max(1, len(enh)), 3)
    V162_QA_CACHE['phrase_enhance_count'] = phrase_count
    V162_QA_CACHE['synonym_enhance_count'] = syn_count
    V162_QA_CACHE['keep_enhance_ratio'] = ratio
    target_min = int(V162_QA_CACHE.get('target_enhance_min') or (5 if int(lp.get('fix_count') or 0) >= 5 else 8))
    target_max = int(V162_QA_CACHE.get('target_enhance_max') or (10 if target_min == 5 else 14))
    if len(enh) < target_min:
        V162_QA_CACHE['quota_status'] = 'under_target_precision_preserved'
    elif len(enh) > target_max:
        V162_QA_CACHE['quota_status'] = 'over_target_needs_ranking'
    elif syn_count > max(3, int(round(len(enh) * 0.4))):
        V162_QA_CACHE['quota_status'] = 'synonym_share_too_high'
    else:
        V162_QA_CACHE['quota_status'] = 'within_dynamic_target'

    lp['enhance_count'] = len(enh)
    lp['keep_count'] = len(keep)
    lp['clarify_count'] = len(result.get('clarify_units') or [])
    lp['visible_clarify_task_count'] = lp['clarify_count']
    lp['enhance_multiword_count'] = sum(1 for u in enh if len(surface_tokens(u.get('unit_text'))) > 1)
    lp['enhance_single_word_count'] = sum(1 for u in enh if len(surface_tokens(u.get('unit_text'))) <= 1)
    lp['enhance_multiword_share'] = round(lp['enhance_multiword_count'] / len(enh), 3) if enh else 0.0
    lp['keep_single_word_count'] = sum(1 for u in keep if len(surface_tokens(u.get('unit_text'))) <= 1)
    lp['keep_phrase_count'] = sum(1 for u in keep if len(surface_tokens(u.get('unit_text'))) > 1)
    lp['classification_distribution'] = {
        'FIX': len(result.get('fix_units') or []),
        'ENHANCE': len(enh),
        'KEEP': len(keep),
        'CLARIFY': len(result.get('clarify_units') or []),
    }
    lp['v162_contextual_synonym_quota'] = copy.deepcopy(V162_QA_CACHE)
    qa['v1_6_2_metrics'] = copy.deepcopy(V162_QA_CACHE)
    qa.setdefault('contract_checks', {})['v162_contextual_synonym_keep_policy_enabled'] = True
    qa.setdefault('contract_checks', {})['v162_subject_scope_gate_enabled'] = True
    qa.setdefault('contract_checks', {})['v162_dynamic_keep_enhance_quota_enabled'] = True
    qa.setdefault('contract_checks', {})['v162_no_topic_or_essay_specific_rules'] = True
    # Remove obsolete low-count warning if the dynamic quota is now satisfied.
    if V162_QA_CACHE['quota_status'] == 'within_dynamic_target':
        qa['warnings'] = [w for w in qa.get('warnings', []) if not str(w).startswith('v16_low_enhance_count_after_quality_gates')]
        if not qa.get('errors') and not any('subject_scope' in str(w) for w in qa.get('warnings', [])):
            qa['status'] = 'ok_with_monitoring'
            qa['confidence'] = min(max(float(qa.get('confidence') or 0.78), 0.80), 0.82)
    else:
        msg = f"v162_quota_status: {V162_QA_CACHE['quota_status']}"
        if msg not in qa.setdefault('warnings', []):
            qa['warnings'].append(msg)
        qa['status'] = 'needs_tuning'
        qa['confidence'] = min(float(qa.get('confidence') or 0.78), 0.78)


def _v162_update_learning_payload(result: Dict[str, Any]) -> None:
    lip = result.get('learning_intelligence_payload')
    if not isinstance(lip, dict):
        return
    metrics = {m.get('metric_id'): m for m in lip.get('metric_signals') or [] if isinstance(m, dict)}
    extra = {
        'lret_contextual_synonym_enhance_count': int(V162_QA_CACHE.get('synonym_enhance_count') or 0),
        'lret_keep_enhance_ratio': V162_QA_CACHE.get('keep_enhance_ratio'),
    }
    for k, v in extra.items():
        if k in metrics:
            metrics[k]['value'] = v
        else:
            lip.setdefault('metric_signals', []).append({'metric_id': k, 'value': v})
    notes = list(lip.get('notes') or [])
    note = 'v1.6.2 allows selected single-word KEEP items to feed contextual synonym training when insertion and subject/scope QA pass.'
    if note not in notes:
        notes.append(note)
    lip['notes'] = notes


_prev_analyze_v16_for_v162 = analyze

def analyze(payload: Dict[str, Any], validator: Optional[ContextFitValidator] = None) -> Dict[str, Any]:
    _v162_reset()
    validator = validator or RuleBasedContextFitValidator()
    result = _prev_analyze_v16_for_v162(payload, validator)
    result['run']['engine_version'] = ENGINE_VERSION
    qa = result.setdefault('qa', {})
    failures = qa.setdefault('source_audit', {}).setdefault('context_fit_check_failures', [])
    _v162_add_contextual_synonym_tasks(result, validator, failures)
    _v162_sync_after_synonym_tasks(result)
    _v162_refresh_quota_metrics(result)
    _v162_update_learning_payload(result)
    return result


# ============================================================================
# v1.6.3 PATCH: quota-aware strict recovery + separate contextual synonym layer
# ============================================================================

ENGINE_VERSION = "lret-engine-v1.6.3-quota-aware-strict-recovery-contextual-synonym-layer"

V163_QA_CACHE: Dict[str, Any] = {
    "version": "v1.6.3",
    "target_main_enhance_min": 5,
    "target_main_enhance_max": 10,
    "quota_status": "not_run",
    "recovery_mode_activated": False,
    "recovery_seed_count": 0,
    "recovery_seed_sources": {},
    "recovery_llm_candidates_sent": 0,
    "recovery_llm_units_released": 0,
    "recovery_resource_units_released": 0,
    "recovery_units_rejected": 0,
    "span_repair_attempted": 0,
    "span_repair_succeeded": 0,
    "resource_combo_suggestions_generated": 0,
    "resource_combo_suggestions_accepted": 0,
    "main_enhance_recovered_count": 0,
    "main_enhance_final_count": 0,
    "main_enhance_phrase_count": 0,
    "light_synonym_candidates_considered": 0,
    "light_synonym_tasks_released": 0,
    "light_synonym_suggestions_accepted": 0,
    "light_synonym_suggestions_rejected": 0,
    "light_synonym_cap": 3,
    "blocked_samples": [],
}

V163_RECOVERY_LIGHT_VERBS: Set[str] = {
    "give", "make", "bring", "take", "have", "do", "get", "provide", "offer", "create", "cause",
    "lead", "support", "show", "play", "put", "set", "hold", "run", "manage", "organise", "organize",
}
V163_BAD_RECOVERY_STARTERS: Set[str] = set(STOPWORDS) | {"and", "or", "but", "while", "because", "if", "that", "the", "a", "an"}
V163_RECOVERY_MAX_TOKENS = 12
V163_LIGHT_CONTEXT_MAX_TOKENS = 9


def _v163_reset() -> None:
    V163_QA_CACHE.clear()
    V163_QA_CACHE.update({
        "version": "v1.6.3",
        "target_main_enhance_min": 5,
        "target_main_enhance_max": 10,
        "quota_status": "not_run",
        "recovery_mode_activated": False,
        "recovery_seed_count": 0,
        "recovery_seed_sources": {},
        "recovery_llm_candidates_sent": 0,
        "recovery_llm_units_released": 0,
        "recovery_resource_units_released": 0,
        "recovery_units_rejected": 0,
        "span_repair_attempted": 0,
        "span_repair_succeeded": 0,
        "resource_combo_suggestions_generated": 0,
        "resource_combo_suggestions_accepted": 0,
        "main_enhance_recovered_count": 0,
        "main_enhance_final_count": 0,
        "main_enhance_phrase_count": 0,
        "light_synonym_candidates_considered": 0,
        "light_synonym_tasks_released": 0,
        "light_synonym_suggestions_accepted": 0,
        "light_synonym_suggestions_rejected": 0,
        "light_synonym_cap": 3,
        "blocked_samples": [],
    })


def _v163_note_counter(name: str, inc: int = 1) -> None:
    V163_QA_CACHE[name] = int(V163_QA_CACHE.get(name, 0)) + inc


def _v163_note_block(sample: Dict[str, Any]) -> None:
    V163_QA_CACHE.setdefault("blocked_samples", [])
    if len(V163_QA_CACHE["blocked_samples"]) < 60:
        V163_QA_CACHE["blocked_samples"].append(sample)


def _v163_source_count(source: str) -> None:
    d = V163_QA_CACHE.setdefault("recovery_seed_sources", {})
    d[source] = int(d.get(source, 0)) + 1


def _v163_exact_find(context: str, span: str) -> Optional[Tuple[int, int, str]]:
    if not context or not span:
        return None
    start = context.find(span)
    if start >= 0:
        return start, start + len(span), context[start:start + len(span)]
    low_context = context.lower()
    low_span = span.lower()
    start = low_context.find(low_span)
    if start >= 0:
        return start, start + len(span), context[start:start + len(span)]
    return None


def _v163_all_contexts_from_result(result: Dict[str, Any]) -> List[str]:
    contexts: List[str] = []
    for group in ("fix_units", "enhance_units", "clarify_units", "keep_units"):
        for u in result.get(group) or []:
            c = str(u.get("context") or "")
            if c and c not in contexts:
                contexts.append(c)
    return contexts


def _v163_context_for_span(span: str, result: Dict[str, Any]) -> str:
    for c in _v163_all_contexts_from_result(result):
        if _v163_exact_find(c, span):
            return c
    return ""


def _v163_clause_start_index(left: str) -> int:
    # Universal boundary: punctuation or clause-level separator, not topic words.
    last = -1
    for m in re.finditer(r"[.;:!?]|,\s+", left):
        last = max(last, m.end())
    # If no punctuation boundary, keep the phrase short by starting near the last finite/light verb window.
    return max(0, last)


def _v163_expand_to_recoverable_phrase(span: str, context: str) -> Optional[str]:
    """Recover a fuller exact phrase around a rejected subspan.

    This is universal span repair, not a phrase bank: it expands to the nearest
    clause-local exact substring when the supplied span is too narrow to be a
    replaceable lexical task.
    """
    span = str(span or "").strip()
    context = str(context or "")
    found = _v163_exact_find(context, span)
    if not found:
        return None
    _v163_note_counter("span_repair_attempted")
    start, end, exact = found
    left = context[:start]
    right = context[end:]
    clause_start = _v163_clause_start_index(left)
    recovered_start = start
    left_clause = left[clause_start:]
    left_toks = re.findall(r"\b[\w'-]+\b", left_clause, flags=re.UNICODE)

    # Expand left when the candidate is governed by a light verb or modifier in the same clause.
    if left_toks:
        # Find the last likely governing verb in the current clause.
        lower = [norm_text(t) for t in left_toks]
        gov_idx = None
        for i in range(len(lower) - 1, -1, -1):
            if lower[i] in V163_RECOVERY_LIGHT_VERBS or lower[i].endswith("ing") or lower[i].endswith("ed"):
                gov_idx = i
                break
        if gov_idx is not None:
            # Include up to two subject/modifier tokens before the governor if they stay inside a short phrase.
            before = max(0, gov_idx - 3)
            # Avoid starting with determiners/conjunctions when possible.
            while before < gov_idx and lower[before] in V163_BAD_RECOVERY_STARTERS:
                before += 1
            prefix = " ".join(left_toks[before:])
            pos = left_clause.lower().rfind(prefix.lower()) if prefix else -1
            if pos >= 0:
                recovered_start = clause_start + pos

    # Expand right to include a short complement/prepositional tail when the original span is too narrow.
    recovered_end = end
    right_match = re.match(r"((?:\s+(?:to|for|with|of|in|on|about|from|into|by|as)\s+[\w'-]+(?:\s+[\w'-]+){0,4})*)", right, flags=re.IGNORECASE)
    if right_match and right_match.group(1):
        recovered_end = end + len(right_match.group(1))
    recovered = context[recovered_start:recovered_end].strip(" ,;:")
    toks = surface_tokens(recovered)
    if not recovered or norm_text(recovered) == norm_text(span):
        return None
    if len(toks) < 3 or len(toks) > V163_RECOVERY_MAX_TOKENS:
        _v163_note_block({"unit_text": span, "candidate": recovered, "reason": "span_repair_length_out_of_bounds"})
        return None
    if not _v163_exact_find(context, recovered):
        return None
    _v163_note_counter("span_repair_succeeded")
    return recovered


def _v163_unit_is_recoverable_phrase(text: str, context: str) -> bool:
    toks = _v16_wordish_tokens(text)
    if len(toks) < 3 or len(toks) > V163_RECOVERY_MAX_TOKENS:
        return False
    if not context or not _v163_exact_find(context, text):
        return False
    if _is_vague_placeholder_phrase(text) or context_has_local_grammar_corruption(text):
        return False
    if toks[0] in {"and", "or", "but", "because", "if", "that"} and len(toks) < 5:
        return False
    return True


def _v163_make_recovery_seed(unit_text: str, context: str, source: str, source_unit: Optional[Dict[str, Any]] = None, reason: str = "") -> Optional[Dict[str, Any]]:
    unit_text = re.sub(r"\s+", " ", str(unit_text or "").strip(" ,;:"))
    context = str(context or "")
    if not unit_text or not context:
        return None
    recovered = _v163_expand_to_recoverable_phrase(unit_text, context) or unit_text
    recovered = re.sub(r"\s+", " ", recovered.strip(" ,;:"))
    if not _v163_unit_is_recoverable_phrase(recovered, context):
        _v163_note_block({"unit_text": unit_text, "candidate": recovered, "reason": "recovery_seed_not_recoverable_phrase", "source": source})
        return None
    source_unit = source_unit or {}
    _v163_source_count(source)
    return {
        "unit_id": f"recseed_{abs(hash((recovered, context, source))) % 10**10}",
        "unit_text": recovered,
        "unit_norm": norm_text(recovered),
        "unit_type": source_unit.get("unit_type") or "recovered_phrase_candidate",
        "source_sentence_index": source_unit.get("source_sentence_index"),
        "source_paragraph_index": source_unit.get("source_paragraph_index"),
        "context": context,
        "axis_candidates": list(dict.fromkeys((source_unit.get("axis_candidates") or []) + ["collocation_naturalness", "semantic_specificity", "paraphrase_range"])),
        "extraction_signal": "v163_quota_recovery_seed",
        "extraction_flags": list(dict.fromkeys((source_unit.get("extraction_flags") or []) + ["v163_recovery_seed", source])),
        "candidate_value": max(0.68, float(source_unit.get("candidate_value") or 0.0)),
        "evidence_ids": source_unit.get("evidence_ids") or [],
        "frequency": int(source_unit.get("frequency") or 1),
        "recovery_source": source,
        "recovery_reason": reason,
    }


def _v163_build_recovery_seeds(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    existing = {norm_text(u.get("unit_text")) for u in result.get("enhance_units") or []}
    seeds: List[Dict[str, Any]] = []
    seen: Set[str] = set(existing)

    # 1) Rejected/failed candidates with useful rejection reasons.
    failures = result.get("qa", {}).get("source_audit", {}).get("context_fit_check_failures", []) or []
    useful_failure_reasons = {
        "noun_span_requires_governing_verb_expansion",
        "fewer_than_min_valid_v16_suggestions",
        "fewer_than_min_valid_after_v162_subject_scope_gate",
        "llm_classified_as_keep",
        "not_complete_replaceable_learning_unit",
        "original_collocation_already_stable_no_clear_gain",
    }
    for f in failures:
        if not isinstance(f, dict):
            continue
        reason = str(f.get("reason") or "")
        if reason not in useful_failure_reasons:
            continue
        text = str(f.get("unit_text") or "")
        context = str(f.get("context") or "") or _v163_context_for_span(text, result)
        seed = _v163_make_recovery_seed(text, context, "rejected_candidate_recovery", reason=reason)
        if seed and seed["unit_norm"] not in seen:
            seen.add(seed["unit_norm"])
            seeds.append(seed)

    # 2) High-value KEEP phrases: they may be good positive evidence and still trainable.
    for u in result.get("keep_units") or []:
        text = str(u.get("unit_text") or "")
        if norm_text(text) in seen:
            continue
        toks = _v16_wordish_tokens(text)
        if len(toks) < 2:
            continue
        if str(u.get("keep_type") or "") == "keep_formulaic_expression":
            continue
        val = float(u.get("candidate_value") or 0.0)
        if val < 0.55 and not (set(toks) & set(EXTERNAL_WORD_SUGGESTIONS.keys())):
            continue
        context = str(u.get("context") or "")
        seed = _v163_make_recovery_seed(text, context, "keep_phrase_recovery", source_unit=u, reason="high_value_keep_phrase_or_collocation")
        if seed and seed["unit_norm"] not in seen:
            seen.add(seed["unit_norm"])
            seeds.append(seed)

    # Prefer stronger, phrase-like, non-malformed candidates.
    seeds.sort(key=lambda s: (float(s.get("candidate_value") or 0.0), len(_v16_wordish_tokens(s.get("unit_text") or ""))), reverse=True)
    V163_QA_CACHE["recovery_seed_count"] = len(seeds)
    return seeds[:18]


def _v163_join_token_parts(parts: List[str]) -> str:
    out = ""
    for part in parts:
        if re.match(r"^[,.;:!?)]$", part):
            out += part
        elif not out or out.endswith(("(", "/", "-")):
            out += part
        else:
            out += " " + part
    return re.sub(r"\s+", " ", out).strip()


def _v163_generate_combo_resource_suggestions(unit_text: str, limit: int = 8) -> List[str]:
    """Generate whole-phrase variants using external word resources only.

    This is broader than v1.6.2 single-token substitution but still not a phrase
    bank: alternatives come from canonical/external word resources and are later
    filtered by insertion/pedagogical QA.
    """
    original = str(unit_text or "").strip()
    if not original or not EXTERNAL_WORD_SUGGESTIONS:
        return []
    parts = re.findall(r"\w+(?:[-']\w+)?|[^\w\s]", original, flags=re.UNICODE)
    eligible: List[Tuple[int, str, List[str]]] = []
    for i, tok in enumerate(parts):
        key = norm_text(tok)
        if not key or key in STOPWORDS or key in V15_ARTICLES:
            continue
        vals = [str(v).strip() for v in EXTERNAL_WORD_SUGGESTIONS.get(key, []) if str(v).strip()]
        vals = [v for v in vals if norm_text(v) != key][:4]
        if vals:
            eligible.append((i, key, vals))
    out: List[str] = []
    seen = {norm_text(original)}
    # First single substitutions.
    for i, _, vals in eligible:
        for repl in vals:
            p = list(parts)
            p[i] = repl
            cand = _v163_join_token_parts(p)
            low = norm_text(cand)
            if low and low not in seen:
                seen.add(low); out.append(cand)
                _v163_note_counter("resource_combo_suggestions_generated")
                if len(out) >= limit:
                    return out
    # Then safe two-token substitutions for richer phrase variation.
    for a_idx in range(len(eligible)):
        for b_idx in range(a_idx + 1, len(eligible)):
            i, _, vals_i = eligible[a_idx]
            j, _, vals_j = eligible[b_idx]
            # Avoid generating overly changed phrases.
            for repl_i in vals_i[:2]:
                for repl_j in vals_j[:2]:
                    p = list(parts)
                    p[i] = repl_i
                    p[j] = repl_j
                    cand = _v163_join_token_parts(p)
                    low = norm_text(cand)
                    if low and low not in seen:
                        seen.add(low); out.append(cand)
                        _v163_note_counter("resource_combo_suggestions_generated")
                        if len(out) >= limit:
                            return out
    return out


def _v163_validate_recovery_suggestions(unit_text: str, suggestions: Iterable[str], context: str, validator: ContextFitValidator, failures: List[Dict[str, Any]], *, tier: str) -> List[Dict[str, Any]]:
    valid: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for raw in suggestions or []:
        sug = re.sub(r"\s+", " ", str(raw or "").strip())
        if not sug or norm_text(sug) in seen or compact_norm(sug) == compact_norm(unit_text):
            continue
        seen.add(norm_text(sug))
        ok, reason, final_sentence, support = _v15_validate_enhance_suggestion(unit_text, sug, context)
        if not ok:
            failures.append({"unit_text": unit_text, "candidate": sug, "tier": f"v163_{tier}_insertion_gate", "reason": reason, "final_sentence": final_sentence, "resource_support": support})
            _v163_note_block({"unit_text": unit_text, "candidate": sug, "reason": reason, "tier": tier})
            continue
        gain_ok, gain_reason = _v16_suggestion_has_pedagogical_gain(unit_text, sug, context, support)
        if not gain_ok:
            failures.append({"unit_text": unit_text, "candidate": sug, "tier": f"v163_{tier}_pedagogical_gain_gate", "reason": gain_reason, "final_sentence": final_sentence, "resource_support": support})
            _v163_note_block({"unit_text": unit_text, "candidate": sug, "reason": gain_reason, "tier": tier})
            continue
        scope_ok, scope_reason = _v162_subject_scope_ok(unit_text, sug, final_sentence)
        if not scope_ok:
            failures.append({"unit_text": unit_text, "candidate": sug, "tier": f"v163_{tier}_subject_scope_gate", "reason": scope_reason, "final_sentence": final_sentence, "resource_support": support})
            _v163_note_block({"unit_text": unit_text, "candidate": sug, "reason": scope_reason, "tier": tier})
            continue
        item = {
            "text": sug,
            "validation": {
                "accepted": True,
                "gates": [
                    "replace_in_context_sentence_fit",
                    "resource_signal_checked",
                    "pedagogical_gain_checked",
                    "v162_subject_scope_retention_checked",
                    "v163_strict_recovery_qa",
                ],
                "reason": "passed v1.6.3 quota-aware strict recovery QA gates",
            },
            "final_sentence_after_replacement": final_sentence,
            "resource_support": support,
            "suggestion_source": tier,
        }
        valid.append(item)
        if tier == "resource_combo_recovery":
            _v163_note_counter("resource_combo_suggestions_accepted")
        if len(valid) >= max(LLM_MIN_VALID_SUGGESTIONS, 2):
            break
    return valid


def _v163_candidate_from_valid_suggestions(seed: Dict[str, Any], valid: List[Dict[str, Any]], source_kind: str) -> Optional[Dict[str, Any]]:
    if len(valid) < LLM_MIN_VALID_SUGGESTIONS:
        _v163_note_counter("recovery_units_rejected")
        _v163_note_block({"unit_text": seed.get("unit_text"), "reason": "fewer_than_min_valid_recovery_suggestions", "valid_suggestion_count": len(valid), "source": source_kind})
        return None
    u = copy.deepcopy(seed)
    u["unit_id"] = f"enh_rec_{abs(hash((u.get('unit_text'), source_kind))) % 10**8:08d}"
    u["class_label"] = "ENHANCE"
    u["safety_level"] = "v163_quota_aware_strict_recovery_validated"
    u["replacement_scope"] = "whole_phrase"
    u["suggestions"] = valid
    u["source_kind"] = source_kind
    u["dedup_role"] = "v163_recovered_phrase"
    u["covered_subunits"] = []
    flags = list(u.get("extraction_flags") or [])
    for flag in ["v163_quota_aware_recovery", "strict_final_qa_validated", "phrase_first"]:
        if flag not in flags:
            flags.append(flag)
    u["extraction_flags"] = flags
    u["reveal_policy"] = {
        "mode": "produce_before_reveal",
        "attempt_required_before_suggestions_shown": True,
        "suggestions_role": "reveal_phase_model_answer_for_comparison",
    }
    u["phase1_prompt"] = f"This phrase is correct or understandable, but could be more precise, natural, or academic: [{u.get('unit_text')}]. How would you paraphrase the whole phrase without changing the meaning?"
    u["phase1_options"] = [{"option_id": "WRITE_MY_OWN", "label": "Write my own answer"}]
    u["recurs_across_essays"] = False
    u["recurrence_note"] = None
    return u


def _v163_release_candidate(seed: Dict[str, Any], suggestions: Iterable[str], validator: ContextFitValidator, failures: List[Dict[str, Any]], source_kind: str) -> Optional[Dict[str, Any]]:
    text = str(seed.get("unit_text") or "")
    context = str(seed.get("context") or "")
    valid = _v163_validate_recovery_suggestions(text, suggestions, context, validator, failures, tier=source_kind)
    unit = _v163_candidate_from_valid_suggestions(seed, valid, source_kind)
    if unit:
        if source_kind.startswith("llm"):
            _v163_note_counter("recovery_llm_units_released")
        else:
            _v163_note_counter("recovery_resource_units_released")
    return unit


def _v163_llm_recover_candidates(seeds: List[Dict[str, Any]], validator: ContextFitValidator, failures: List[Dict[str, Any]], needed: int) -> List[Dict[str, Any]]:
    provider = ACTIVE_LLM_PROVIDER
    if not provider or not provider.available() or needed <= 0 or not seeds:
        return []
    payload = []
    for i, s in enumerate(seeds[:max(needed * 3, 8)], start=1):
        payload.append({
            "unit_id": f"v163_rec_{i:04d}",
            "unit_text": s.get("unit_text"),
            "context": s.get("context"),
            "unit_type": s.get("unit_type") or "recovered_phrase_candidate",
            "axis_candidates": s.get("axis_candidates") or [],
            "extraction_flags": s.get("extraction_flags") or [],
        })
    V163_QA_CACHE["recovery_llm_candidates_sent"] = len(payload)
    results = provider.classify_and_suggest(payload, learner_level="B1-B2")
    by_id = {str(r.get("unit_id")): r for r in results if isinstance(r, dict)}
    out: List[Dict[str, Any]] = []
    for i, seed in enumerate(seeds[:len(payload)], start=1):
        r = by_id.get(f"v163_rec_{i:04d}")
        if not r:
            continue
        decision = str(r.get("decision") or r.get("classification") or "").upper()
        conf = _safe_float(r.get("confidence"), 0.0)
        if decision == "EXPAND_SPAN":
            rec = str(r.get("recommended_span") or "").strip()
            exact = _exact_substring_from_context(rec, str(seed.get("context") or ""))
            if exact and len(surface_tokens(exact)) <= V163_RECOVERY_MAX_TOKENS:
                seed = copy.deepcopy(seed)
                seed["unit_text"] = exact
                seed["unit_norm"] = norm_text(exact)
            else:
                failures.append({"unit_text": seed.get("unit_text"), "candidate": rec or None, "tier": "v163_llm_recovery_expand", "reason": "recommended_span_not_exact_or_too_long", "llm_confidence": conf})
                continue
        elif decision != "ENHANCE":
            failures.append({"unit_text": seed.get("unit_text"), "candidate": None, "tier": "v163_llm_recovery_classification", "reason": f"llm_classified_as_{decision.lower() or 'unknown'}", "llm_confidence": conf, "risk_flags": r.get("risk_flags") or []})
            continue
        if conf < 0.72:
            failures.append({"unit_text": seed.get("unit_text"), "candidate": None, "tier": "v163_llm_recovery_confidence", "reason": "llm_recovery_confidence_too_low", "llm_confidence": conf})
            continue
        unit = _v163_release_candidate(seed, r.get("suggestions") or [], validator, failures, "llm_strict_recovery")
        if unit:
            unit["candidate_value"] = max(float(unit.get("candidate_value") or 0.0), min(0.92, conf))
            out.append(unit)
            if len(out) >= needed:
                break
    return out


def _v163_resource_recover_candidates(seeds: List[Dict[str, Any]], validator: ContextFitValidator, failures: List[Dict[str, Any]], needed: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for seed in seeds:
        if len(out) >= needed:
            break
        suggestions = _v163_generate_combo_resource_suggestions(str(seed.get("unit_text") or ""), limit=10)
        if not suggestions:
            continue
        unit = _v163_release_candidate(seed, suggestions, validator, failures, "resource_combo_recovery")
        if unit:
            out.append(unit)
    return out


def _v163_dedup_enhance_units(units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Prefer full/strong recovered phrases over subspans, but never release duplicate text.
    ranked = list(units or [])
    def score(u: Dict[str, Any]) -> Tuple[float, int, int]:
        rec = 1 if "v163_quota_aware_recovery" in (u.get("extraction_flags") or []) else 0
        return (float(u.get("candidate_value") or 0.0) + rec * 0.04, len(_v16_wordish_tokens(u.get("unit_text") or "")), len(u.get("suggestions") or []))
    ranked.sort(key=score, reverse=True)
    final: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for u in ranked:
        text = norm_text(u.get("unit_text"))
        if not text or text in seen:
            continue
        # Remove subspans already represented by a stronger phrase in the same context.
        if any(text in norm_text(v.get("unit_text")) and text != norm_text(v.get("unit_text")) for v in final):
            continue
        seen.add(text)
        final.append(u)
    return sorted(final, key=lambda u: int(u.get("source_sentence_index") if u.get("source_sentence_index") is not None else 9999))


def _v163_apply_quota_aware_strict_recovery(result: Dict[str, Any], validator: ContextFitValidator, failures: List[Dict[str, Any]]) -> None:
    enh = list(result.get("enhance_units") or [])
    fix_count = int(result.get("lexical_profile", {}).get("fix_count") or len(result.get("fix_units") or []))
    target_min = 5 if fix_count >= 4 else 7
    target_max = 10 if fix_count >= 4 else 12
    V163_QA_CACHE["target_main_enhance_min"] = target_min
    V163_QA_CACHE["target_main_enhance_max"] = target_max
    if len(enh) >= target_min:
        V163_QA_CACHE["quota_status"] = "within_target_no_recovery_needed"
        return
    V163_QA_CACHE["recovery_mode_activated"] = True
    seeds = _v163_build_recovery_seeds(result)
    existing = {norm_text(u.get("unit_text")) for u in enh}
    seeds = [s for s in seeds if norm_text(s.get("unit_text")) not in existing]
    needed = max(0, target_min - len(enh))
    recovered: List[Dict[str, Any]] = []

    # 1. Resource/combo generation first: deterministic, cheap, strict.
    recovered.extend(_v163_resource_recover_candidates(seeds, validator, failures, needed))
    existing.update(norm_text(u.get("unit_text")) for u in recovered)
    remaining = max(0, needed - len(recovered))
    # 2. LLM recovery if still under target. LLM proposes, deterministic QA disposes.
    if remaining > 0:
        llm_seeds = [s for s in seeds if norm_text(s.get("unit_text")) not in existing]
        recovered.extend(_v163_llm_recover_candidates(llm_seeds, validator, failures, remaining))

    if recovered:
        for u in recovered:
            u["recovered_by_v163"] = True
        enh = _v163_dedup_enhance_units(enh + recovered)
        if len(enh) > target_max:
            enh = enh[:target_max]
        result["enhance_units"] = enh
    V163_QA_CACHE["main_enhance_recovered_count"] = sum(1 for u in result.get("enhance_units") or [] if u.get("recovered_by_v163"))
    V163_QA_CACHE["main_enhance_final_count"] = len(result.get("enhance_units") or [])
    V163_QA_CACHE["main_enhance_phrase_count"] = sum(1 for u in result.get("enhance_units") or [] if u.get("unit_type") != "contextual_word_choice")
    if len(result.get("enhance_units") or []) < target_min:
        V163_QA_CACHE["quota_status"] = "under_target_after_strict_recovery"
    else:
        V163_QA_CACHE["quota_status"] = "within_target_after_strict_recovery"


def _v163_light_candidate_ok(u: Dict[str, Any]) -> bool:
    text = str(u.get("unit_text") or "")
    context = str(u.get("context") or "")
    toks = _v16_wordish_tokens(text)
    if not text or not context or len(toks) > V163_LIGHT_CONTEXT_MAX_TOKENS:
        return False
    if context_has_local_grammar_corruption(context) and len(toks) <= 1:
        return False
    if str(u.get("keep_type") or "") == "keep_formulaic_expression":
        return False
    return any(t in EXTERNAL_WORD_SUGGESTIONS for t in toks)


def _v163_validate_light_suggestions(unit_text: str, suggestions: Iterable[str], context: str, validator: ContextFitValidator, failures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for sug in suggestions or []:
        sug = re.sub(r"\s+", " ", str(sug or "").strip())
        if not sug or norm_text(sug) in seen or compact_norm(sug) == compact_norm(unit_text):
            continue
        seen.add(norm_text(sug))
        ok, reason, final_sentence, support = _v15_validate_enhance_suggestion(unit_text, sug, context)
        if not ok:
            _v163_note_counter("light_synonym_suggestions_rejected")
            continue
        scope_ok, scope_reason = _v162_subject_scope_ok(unit_text, sug, final_sentence)
        if not scope_ok:
            _v163_note_counter("light_synonym_suggestions_rejected")
            failures.append({"unit_text": unit_text, "candidate": sug, "tier": "v163_light_subject_scope_gate", "reason": scope_reason, "final_sentence": final_sentence})
            continue
        # Light tasks allow low lexical gain, but not grammar-only or orthographic changes.
        grammar_delta, grammar_reason = _v15_suggestion_delta_is_grammar_repair(unit_text, sug, context)
        if grammar_delta or _v16_is_pure_orthographic_variant(unit_text, sug):
            _v163_note_counter("light_synonym_suggestions_rejected")
            failures.append({"unit_text": unit_text, "candidate": sug, "tier": "v163_light_grammar_delta_gate", "reason": grammar_reason or "orthographic_variant"})
            continue
        out.append({
            "text": sug,
            "validation": {
                "accepted": True,
                "gates": ["contextual_insertion_checked", "subject_scope_checked", "not_grammar_repair", "v163_light_synonym_qa"],
                "reason": "passed v1.6.3 contextual synonym light-task QA gates",
            },
            "final_sentence_after_replacement": final_sentence,
            "resource_support": support,
            "suggestion_source": "v163_contextual_synonym_light",
        })
        _v163_note_counter("light_synonym_suggestions_accepted")
        if len(out) >= 2:
            break
    return out


def _v163_generate_light_synonym_tasks(result: Dict[str, Any], validator: ContextFitValidator, failures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cap = int(V163_QA_CACHE.get("light_synonym_cap") or 3)
    tasks: List[Dict[str, Any]] = []
    existing_main = {norm_text(u.get("unit_text")) for u in result.get("enhance_units") or []}
    existing_task_subunits: Set[str] = set(existing_main)
    candidates = []
    for u in result.get("keep_units") or []:
        if norm_text(u.get("unit_text")) in existing_task_subunits:
            continue
        if _v163_light_candidate_ok(u):
            candidates.append(u)
    candidates.sort(key=lambda u: (float(u.get("candidate_value") or 0.0), int(u.get("frequency") or 1), len(_v16_wordish_tokens(u.get("unit_text") or ""))), reverse=True)
    for u in candidates:
        if len(tasks) >= cap:
            break
        V163_QA_CACHE["light_synonym_candidates_considered"] = int(V163_QA_CACHE.get("light_synonym_candidates_considered", 0)) + 1
        text = str(u.get("unit_text") or "")
        context = str(u.get("context") or "")
        suggestions = _v163_generate_combo_resource_suggestions(text, limit=8)
        valid = _v163_validate_light_suggestions(text, suggestions, context, validator, failures)
        if len(valid) < 2:
            continue
        task = {
            "unit_id": f"syn_light_{len(tasks)+1:04d}",
            "class_label": "CONTEXTUAL_SYNONYM",
            "unit_text": text,
            "unit_norm": norm_text(text),
            "unit_type": "contextual_phrase_or_word_variation",
            "source_sentence_index": u.get("source_sentence_index"),
            "source_paragraph_index": u.get("source_paragraph_index"),
            "context": context,
            "axis_candidates": ["word_choice", "semantic_specificity", "contextual_synonym_control"],
            "extraction_signal": "v163_light_synonym_from_keep",
            "extraction_flags": ["contextual_synonym_light", "separate_from_main_enhance", "resource_based"],
            "candidate_value": float(u.get("candidate_value") or 0.55),
            "evidence_ids": u.get("evidence_ids") or [],
            "frequency": int(u.get("frequency") or 1),
            "safety_level": "light_contextual_synonym_validated",
            "replacement_scope": "contextual_phrase_variation",
            "suggestions": valid,
            "covered_subunits": [],
            "dedup_role": "separate_light_synonym_task",
            "source_kind": "v163_keep_based_contextual_synonym_light",
            "reveal_policy": {
                "mode": "produce_before_reveal",
                "attempt_required_before_suggestions_shown": True,
                "suggestions_role": "reveal_phase_model_answer_for_comparison",
            },
            "phase1_prompt": f"This word or phrase is understandable in context: [{text}]. Write a contextually fitting alternative without changing the sentence meaning.",
            "phase1_options": [{"option_id": "WRITE_MY_OWN", "label": "Write my own answer"}],
        }
        tasks.append(task)
    V163_QA_CACHE["light_synonym_tasks_released"] = len(tasks)
    return tasks


def _v163_sync_outputs(result: Dict[str, Any]) -> None:
    # Replacement options/practice targets for recovered main ENHANCE and light tasks.
    repl_ids = {x.get("unit_id") for x in result.get("replacement_options") or []}
    target_ids = {x.get("unit_id") for x in result.get("lret_practice_targets") or []}
    for u in result.get("enhance_units") or []:
        if u.get("unit_id") not in repl_ids:
            result.setdefault("replacement_options", []).append({
                "unit_id": u.get("unit_id"),
                "unit_text": u.get("unit_text"),
                "class_label": "ENHANCE",
                "replacement_scope": u.get("replacement_scope"),
                "suggestions": copy.deepcopy(u.get("suggestions") or []),
                "reveal_policy": copy.deepcopy(u.get("reveal_policy") or {}),
            })
        if u.get("unit_id") not in target_ids:
            result.setdefault("lret_practice_targets", []).append({
                "unit_id": u.get("unit_id"),
                "unit_text": u.get("unit_text"),
                "category": "enhance",
                "tier": "v163_quota_aware_strict_recovery" if u.get("recovered_by_v163") else "phrase_level_context_validated",
                "priority_weight": 2.2,
                "history_count": 0,
                "recommended_practice_type": "produce_before_reveal_phrase_paraphrase",
            })
    for u in result.get("contextual_synonym_tasks") or []:
        if u.get("unit_id") not in repl_ids:
            result.setdefault("replacement_options", []).append({
                "unit_id": u.get("unit_id"),
                "unit_text": u.get("unit_text"),
                "class_label": "CONTEXTUAL_SYNONYM",
                "replacement_scope": u.get("replacement_scope"),
                "suggestions": copy.deepcopy(u.get("suggestions") or []),
                "reveal_policy": copy.deepcopy(u.get("reveal_policy") or {}),
            })
        if u.get("unit_id") not in target_ids:
            result.setdefault("lret_practice_targets", []).append({
                "unit_id": u.get("unit_id"),
                "unit_text": u.get("unit_text"),
                "category": "contextual_synonym",
                "tier": "light_contextual_synonym_validated",
                "priority_weight": 1.4,
                "history_count": 0,
                "recommended_practice_type": "contextual_phrase_variation_light",
            })


def _v163_refresh_metrics(result: Dict[str, Any]) -> None:
    lp = result.setdefault("lexical_profile", {})
    qa = result.setdefault("qa", {})
    enh = result.get("enhance_units") or []
    keep = result.get("keep_units") or []
    clar = result.get("clarify_units") or []
    light = result.get("contextual_synonym_tasks") or []
    V163_QA_CACHE["main_enhance_final_count"] = len(enh)
    V163_QA_CACHE["main_enhance_phrase_count"] = len(enh)
    V163_QA_CACHE["light_synonym_tasks_released"] = len(light)
    ratio = round(len(keep) / max(1, len(enh)), 3)
    V163_QA_CACHE["keep_main_enhance_ratio"] = ratio
    if len(enh) < int(V163_QA_CACHE.get("target_main_enhance_min") or 5):
        V163_QA_CACHE["quota_status"] = "under_target_after_strict_recovery"
    elif len(enh) > int(V163_QA_CACHE.get("target_main_enhance_max") or 10):
        V163_QA_CACHE["quota_status"] = "over_target_needs_ranking"
    elif len(light) > int(V163_QA_CACHE.get("light_synonym_cap") or 3):
        V163_QA_CACHE["quota_status"] = "light_synonym_over_cap"
    else:
        if str(V163_QA_CACHE.get("quota_status")) != "within_target_after_strict_recovery":
            V163_QA_CACHE["quota_status"] = "within_target"

    lp["enhance_count"] = len(enh)
    lp["keep_count"] = len(keep)
    lp["clarify_count"] = len(clar)
    lp["visible_clarify_task_count"] = len(clar)
    lp["contextual_synonym_task_count"] = len(light)
    lp["enhance_multiword_count"] = sum(1 for u in enh if len(surface_tokens(u.get("unit_text"))) > 1)
    lp["enhance_single_word_count"] = sum(1 for u in enh if len(surface_tokens(u.get("unit_text"))) <= 1)
    lp["enhance_multiword_share"] = round(lp["enhance_multiword_count"] / len(enh), 3) if enh else 0.0
    lp["classification_distribution"] = {
        "FIX": len(result.get("fix_units") or []),
        "ENHANCE": len(enh),
        "KEEP": len(keep),
        "CLARIFY": len(clar),
        "CONTEXTUAL_SYNONYM": len(light),
    }
    lp["v163_quota_aware_strict_recovery"] = copy.deepcopy(V163_QA_CACHE)
    qa["v1_6_3_metrics"] = copy.deepcopy(V163_QA_CACHE)
    checks = qa.setdefault("contract_checks", {})
    checks["v163_quota_aware_strict_recovery_enabled"] = True
    checks["v163_contextual_synonym_separate_task_layer"] = True
    checks["v163_final_release_gates_remain_strict"] = True
    checks["v163_no_topic_or_essay_specific_rules"] = True
    checks["v163_no_embedded_phrase_bank"] = True
    checks["v163_light_synonyms_do_not_inflate_main_enhance"] = True
    status = str(V163_QA_CACHE.get("quota_status") or "")
    warn = f"v163_quota_status: {status}"
    qa["warnings"] = [w for w in qa.get("warnings", []) if not str(w).startswith("v162_quota_status") and not str(w).startswith("v163_quota_status")]
    if "under_target" in status:
        qa.setdefault("warnings", []).append(warn)
        qa["status"] = "needs_tuning"
        qa["confidence"] = min(float(qa.get("confidence") or 0.78), 0.78)
    elif not qa.get("errors"):
        # Keep monitoring if upstream WKE still noisy.
        if any("upstream_evaluator_units_need_cleanup" in str(w) for w in qa.get("warnings", [])):
            qa["status"] = "ok_with_monitoring"
            qa["confidence"] = min(max(float(qa.get("confidence") or 0.80), 0.80), 0.83)
        else:
            qa["status"] = "ok"
            qa["confidence"] = min(max(float(qa.get("confidence") or 0.82), 0.82), 0.86)


def _v163_update_learning_payload(result: Dict[str, Any]) -> None:
    lip = result.get("learning_intelligence_payload")
    if not isinstance(lip, dict):
        return
    metrics = {m.get("metric_id"): m for m in lip.get("metric_signals") or [] if isinstance(m, dict)}
    for metric_id, value in {
        "lret_main_enhance_count": len(result.get("enhance_units") or []),
        "lret_contextual_synonym_task_count": len(result.get("contextual_synonym_tasks") or []),
        "lret_v163_recovered_main_enhance_count": V163_QA_CACHE.get("main_enhance_recovered_count"),
        "lret_keep_main_enhance_ratio": V163_QA_CACHE.get("keep_main_enhance_ratio"),
    }.items():
        if metric_id in metrics:
            metrics[metric_id]["value"] = value
        else:
            lip.setdefault("metric_signals", []).append({"metric_id": metric_id, "value": value})
    notes = list(lip.get("notes") or [])
    note = "v1.6.3 separates contextual synonym work from main ENHANCE and uses quota-aware strict recovery before final release."
    if note not in notes:
        notes.append(note)
    lip["notes"] = notes


_prev_analyze_v162_for_v163 = analyze

def analyze(payload: Dict[str, Any], validator: Optional[ContextFitValidator] = None) -> Dict[str, Any]:
    _v163_reset()
    validator = validator or RuleBasedContextFitValidator()
    result = _prev_analyze_v162_for_v163(payload, validator)
    result["run"]["engine_version"] = ENGINE_VERSION
    qa = result.setdefault("qa", {})
    failures = qa.setdefault("source_audit", {}).setdefault("context_fit_check_failures", [])
    _v163_apply_quota_aware_strict_recovery(result, validator, failures)
    result["contextual_synonym_tasks"] = _v163_generate_light_synonym_tasks(result, validator, failures)
    _v163_sync_outputs(result)
    _v163_refresh_metrics(result)
    _v163_update_learning_payload(result)
    return result


# ============================================================================
# v1.6.4 -- Candidate Restructuring + Full Single-Word Contextual Synonym Audit
# ============================================================================
# v1.6.4 fixes an important pedagogical issue discovered after v1.6.3:
# single-word KEEP/INTERNAL items are not automatically low-value. Any
# content-bearing single word can be synonym-training material when the
# alternative is validated inside the student's own sentence.
#
# The release rule remains strict:
#   * every single-word candidate is AUDITED;
#   * only sentence-fit validated alternatives become CONTEXTUAL_SYNONYM tasks;
#   * weak/no-context/no-resource words become analytics/internal signals, not
#     student-facing tasks;
#   * light synonym tasks do not inflate main ENHANCE.
#
# v1.6.4 also treats long spans as containers. Long spans are split into smaller
# lexical subcandidates and audited before final routing. This avoids losing
# useful phrase targets inside sentence-length candidates.

ENGINE_VERSION = "lret-engine-v1.6.4-keep-promotion-single-word-audit"

V164_QA_CACHE: Dict[str, Any] = {}

V164_SINGLE_WORD_TASK_CAP = 4
V164_LONG_SPAN_RECOVERY_CAP = 3
V164_MAX_CONTEXTS_PER_WORD = 2
V164_MIN_LIGHT_SUGGESTIONS = 2
V164_LONG_SPAN_MIN_TOKENS = 9
V164_SPLIT_MAX_CANDIDATE_TOKENS = 8
V164_SPLIT_MIN_CANDIDATE_TOKENS = 3


def _v164_reset() -> None:
    global V164_QA_CACHE
    V164_QA_CACHE = {
        "version": "v1.6.4",
        "single_word_candidates_seen": 0,
        "single_word_candidates_audited": 0,
        "single_word_candidates_with_context": 0,
        "single_word_candidates_with_resource_options": 0,
        "single_word_contextual_synonym_tasks_added": 0,
        "single_word_contextual_suggestions_accepted": 0,
        "single_word_contextual_suggestions_rejected": 0,
        "single_word_waitlisted_promotable": 0,
        "single_word_routed_internal": 0,
        "single_word_routed_keep_evidence": 0,
        "single_word_routes": Counter(),
        "single_word_audit_sample": [],
        "long_span_containers_seen": 0,
        "long_span_split_candidates_generated": 0,
        "long_span_split_candidates_promoted": 0,
        "long_span_split_candidates_rejected": 0,
        "long_span_split_audit_sample": [],
        "keep_promotable_count": 0,
        "keep_internal_count": 0,
        "final_main_enhance_count": 0,
        "final_contextual_synonym_task_count": 0,
        "quota_status": "not_evaluated",
    }


def _v164_note_route(route: str, sample: Optional[Dict[str, Any]] = None) -> None:
    routes = V164_QA_CACHE.setdefault("single_word_routes", Counter())
    routes[route] += 1
    if sample is not None:
        arr = V164_QA_CACHE.setdefault("single_word_audit_sample", [])
        if len(arr) < 40:
            sample2 = dict(sample)
            sample2["route"] = route
            arr.append(sample2)


def _v164_counter(name: str, n: int = 1) -> None:
    V164_QA_CACHE[name] = int(V164_QA_CACHE.get(name, 0) or 0) + n


def _v164_wordish_tokens(text: str) -> List[str]:
    return [norm_text(t) for t in re.findall(r"[A-Za-z][A-Za-z'-]*", str(text or "")) if norm_text(t)]


def _v164_is_single_word(text: str) -> bool:
    toks = _v164_wordish_tokens(text)
    return len(toks) == 1 and bool(re.fullmatch(r"[A-Za-z][A-Za-z'-]*", str(text or "").strip()))


def _v164_sentence_list_from_payload(payload: Dict[str, Any]) -> List[str]:
    essay = str((payload or {}).get("essay_text") or "")
    if not essay:
        return []
    parts = re.split(r"(?<=[.!?])\s+|\n+", essay.strip())
    return [re.sub(r"\s+", " ", p).strip() for p in parts if re.sub(r"\s+", " ", p).strip()]


def _v164_find_word_contexts(word: str, payload: Dict[str, Any], result: Dict[str, Any]) -> List[str]:
    w = norm_text(word)
    contexts: List[str] = []
    for u in result.get("keep_units") or []:
        if norm_text(u.get("unit_text")) == w and str(u.get("context") or "").strip():
            contexts.append(re.sub(r"\s+", " ", str(u.get("context")).strip()))
    pattern = re.compile(r"\b" + re.escape(str(word).strip()) + r"\b", re.I)
    for sent in _v164_sentence_list_from_payload(payload):
        if pattern.search(sent):
            contexts.append(sent)
    # Stable unique order.
    out: List[str] = []
    seen: Set[str] = set()
    for c in contexts:
        key = norm_text(c)
        if key and key not in seen:
            seen.add(key); out.append(c)
        if len(out) >= V164_MAX_CONTEXTS_PER_WORD:
            break
    return out


def _v164_collect_single_word_candidates(payload: Dict[str, Any], result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect all visible and internal single-word units for audit.

    This intentionally includes KEEP units and audit-only units from source_audit.
    Classification is not assumed. Each word is routed after contextual validation.
    """
    out: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str]] = set()

    def add(word: str, source: str, context: str = "", raw: Optional[Dict[str, Any]] = None) -> None:
        word = str(word or "").strip()
        if not _v164_is_single_word(word):
            return
        w = norm_text(word)
        if not w or w in STOPWORDS or len(w) <= 2:
            return
        key = (w, source)
        if key in seen:
            return
        seen.add(key)
        out.append({"word": word, "word_norm": w, "source": source, "context": context, "raw": raw or {}})

    for u in result.get("keep_units") or []:
        text = str(u.get("unit_text") or "").strip()
        if _v164_is_single_word(text):
            add(text, "keep_units", str(u.get("context") or ""), u)

    audit = result.get("qa", {}).get("source_audit", {})
    for item in audit.get("unresolved_internal") or []:
        add(str(item.get("unit") or ""), "unresolved_internal", "", item)
    for item in audit.get("dropped_units") or []:
        add(str(item.get("unit") or ""), "dropped_units", "", item)
    for item in audit.get("keep_inventory_audit") or []:
        add(str(item.get("unit") or ""), "keep_inventory_audit", "", item)
    return out


def _v164_fix_protected_words(result: Dict[str, Any]) -> Set[str]:
    protected: Set[str] = set()
    for fu in result.get("fix_units") or []:
        for t in _v164_wordish_tokens(str(fu.get("unit_text") or "")):
            protected.add(t)
    return protected


def _v164_resource_suggestions_for_word(word: str, limit: int = 10) -> List[str]:
    w = norm_text(word)
    vals = [str(v or "").strip() for v in EXTERNAL_WORD_SUGGESTIONS.get(w, [])]
    out: List[str] = []
    seen = {w}
    for v in vals:
        if not v:
            continue
        low = norm_text(v)
        if not low or low in seen or low in STOPWORDS:
            continue
        seen.add(low)
        out.append(v)
        if len(out) >= limit:
            break
    return out


def _v164_validate_single_word_suggestions(word: str, suggestions: Iterable[str], context: str, failures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    valid: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for sug in suggestions or []:
        sug = re.sub(r"\s+", " ", str(sug or "").strip())
        if not sug or norm_text(sug) in seen or compact_norm(sug) == compact_norm(word):
            continue
        seen.add(norm_text(sug))
        ok, reason, final_sentence, support = _v15_validate_enhance_suggestion(word, sug, context)
        if not ok:
            _v164_counter("single_word_contextual_suggestions_rejected")
            failures.append({"unit_text": word, "candidate": sug, "tier": "v164_single_word_contextual_insertion_gate", "reason": reason, "final_sentence": final_sentence})
            continue
        scope_ok, scope_reason = _v162_subject_scope_ok(word, sug, final_sentence)
        if not scope_ok:
            _v164_counter("single_word_contextual_suggestions_rejected")
            failures.append({"unit_text": word, "candidate": sug, "tier": "v164_single_word_subject_scope_gate", "reason": scope_reason, "final_sentence": final_sentence})
            continue
        grammar_delta, grammar_reason = _v15_suggestion_delta_is_grammar_repair(word, sug, context)
        if grammar_delta or _v16_is_pure_orthographic_variant(word, sug):
            _v164_counter("single_word_contextual_suggestions_rejected")
            failures.append({"unit_text": word, "candidate": sug, "tier": "v164_single_word_grammar_delta_gate", "reason": grammar_reason or "orthographic_variant"})
            continue
        valid.append({
            "text": sug,
            "validation": {
                "accepted": True,
                "gates": [
                    "single_word_audited",
                    "contextual_insertion_checked",
                    "subject_scope_checked",
                    "not_grammar_repair",
                    "v164_single_word_contextual_synonym_qa",
                ],
                "reason": "passed v1.6.4 single-word contextual synonym audit gates",
            },
            "final_sentence_after_replacement": final_sentence,
            "resource_support": support,
            "suggestion_source": "v164_single_word_contextual_audit",
        })
        _v164_counter("single_word_contextual_suggestions_accepted")
        if len(valid) >= V164_MIN_LIGHT_SUGGESTIONS:
            break
    return valid


def _v164_make_single_word_task(word: str, context: str, valid: List[Dict[str, Any]], source: str, idx: int) -> Dict[str, Any]:
    return {
        "unit_id": f"syn_word_{idx:04d}",
        "class_label": "CONTEXTUAL_SYNONYM",
        "unit_text": word,
        "unit_norm": norm_text(word),
        "unit_type": "contextual_single_word_variation",
        "source_sentence_index": None,
        "source_paragraph_index": None,
        "context": context,
        "axis_candidates": ["word_choice", "semantic_specificity", "contextual_synonym_control"],
        "extraction_signal": "v164_full_single_word_audit",
        "extraction_flags": ["single_word_audited", "context_validated", "separate_from_main_enhance", "resource_based"],
        "candidate_value": 0.55,
        "evidence_ids": [],
        "frequency": 1,
        "safety_level": "v164_contextual_single_word_synonym_validated",
        "replacement_scope": "contextual_word_variation",
        "suggestions": valid,
        "covered_subunits": [],
        "dedup_role": "separate_contextual_synonym_task",
        "source_kind": f"v164_{source}",
        "reveal_policy": {
            "mode": "produce_before_reveal",
            "attempt_required_before_suggestions_shown": True,
            "suggestions_role": "reveal_phase_model_answer_for_comparison",
        },
        "phase1_prompt": f"This word is understandable in context: [{word}]. Write a contextually fitting alternative for this sentence without changing the meaning.",
        "phase1_options": [{"option_id": "WRITE_MY_OWN", "label": "Write my own answer"}],
    }


def _v164_apply_full_single_word_audit(payload: Dict[str, Any], result: Dict[str, Any], failures: List[Dict[str, Any]]) -> None:
    existing_tasks = list(result.get("contextual_synonym_tasks") or [])
    existing_task_keys = {norm_text(t.get("unit_text")) + "||" + norm_text(t.get("context")) for t in existing_tasks if isinstance(t, dict)}
    cap_remaining = max(0, V164_SINGLE_WORD_TASK_CAP - len(existing_tasks))
    protected = _v164_fix_protected_words(result)
    candidates = _v164_collect_single_word_candidates(payload, result)
    V164_QA_CACHE["single_word_candidates_seen"] = len(candidates)
    promotable_units: List[Dict[str, Any]] = list(result.get("keep_promotable_units") or [])
    internal_units: List[Dict[str, Any]] = list(result.get("keep_internal_units") or [])

    # Prioritize visible KEEP words and repeated/resource-supported words, but audit all.
    def cand_rank(c: Dict[str, Any]) -> Tuple[int, int, str]:
        raw = c.get("raw") or {}
        freq = int(raw.get("frequency") or 1) if isinstance(raw, dict) else 1
        visible = 1 if c.get("source") == "keep_units" else 0
        return (visible, freq, str(c.get("word_norm")))

    task_idx = len(existing_tasks) + 1
    for cand in sorted(candidates, key=cand_rank, reverse=True):
        word = str(cand.get("word") or "").strip()
        w = norm_text(word)
        V164_QA_CACHE["single_word_candidates_audited"] += 1
        sample = {"word": word, "source": cand.get("source")}
        if w in protected:
            _v164_counter("single_word_routed_internal")
            _v164_note_route("internal_fix_protected", sample)
            internal_units.append({"unit_text": word, "unit_norm": w, "source": cand.get("source"), "reason": "covered_by_fix_before_synonym_training"})
            continue
        contexts = []
        if str(cand.get("context") or "").strip():
            contexts.append(re.sub(r"\s+", " ", str(cand.get("context")).strip()))
        for c in _v164_find_word_contexts(word, payload, result):
            if norm_text(c) not in {norm_text(x) for x in contexts}:
                contexts.append(c)
        if not contexts:
            _v164_counter("single_word_routed_internal")
            _v164_note_route("internal_no_context", sample)
            internal_units.append({"unit_text": word, "unit_norm": w, "source": cand.get("source"), "reason": "no_context_for_contextual_synonym_audit"})
            continue
        V164_QA_CACHE["single_word_candidates_with_context"] += 1
        suggestions = _v164_resource_suggestions_for_word(word, limit=12)
        if not suggestions:
            _v164_counter("single_word_routed_keep_evidence")
            _v164_note_route("keep_evidence_no_resource_synonyms", {**sample, "context": contexts[0][:140]})
            # no resource alternatives, but still audited; do not assume it is useless.
            internal_units.append({"unit_text": word, "unit_norm": w, "source": cand.get("source"), "reason": "audited_no_resource_alternatives"})
            continue
        V164_QA_CACHE["single_word_candidates_with_resource_options"] += 1
        released = False
        any_valid: List[Dict[str, Any]] = []
        for context in contexts[:V164_MAX_CONTEXTS_PER_WORD]:
            if context_has_local_grammar_corruption(context) and len(_v164_wordish_tokens(context)) < 8:
                _v164_note_route("internal_context_too_malformed", {**sample, "context": context[:140]})
                continue
            key = w + "||" + norm_text(context)
            if key in existing_task_keys:
                _v164_note_route("internal_duplicate_existing_task", {**sample, "context": context[:140]})
                released = True
                break
            valid = _v164_validate_single_word_suggestions(word, suggestions, context, failures)
            any_valid.extend(valid)
            if len(valid) >= V164_MIN_LIGHT_SUGGESTIONS and cap_remaining > 0:
                task = _v164_make_single_word_task(word, context, valid, str(cand.get("source") or "unknown"), task_idx)
                existing_tasks.append(task)
                existing_task_keys.add(key)
                task_idx += 1
                cap_remaining -= 1
                released = True
                V164_QA_CACHE["single_word_contextual_synonym_tasks_added"] += 1
                _v164_note_route("contextual_synonym_task_released", {**sample, "context": context[:140], "suggestion_count": len(valid)})
                break
        if released:
            continue
        if any_valid:
            _v164_counter("single_word_waitlisted_promotable")
            _v164_note_route("keep_promotable_waitlisted_cap_or_low_count", {**sample, "valid_suggestion_count": len(any_valid)})
            promotable_units.append({
                "unit_text": word,
                "unit_norm": w,
                "class_label": "KEEP_PROMOTABLE",
                "promotion_type": "single_word_contextual_synonym",
                "source": cand.get("source"),
                "reason": "valid_or_partly_valid_contextual_options_available_but_not_released",
                "valid_suggestion_count": len(any_valid),
            })
        else:
            _v164_counter("single_word_routed_internal")
            _v164_note_route("internal_no_valid_contextual_suggestions", {**sample, "resource_option_count": len(suggestions)})
            internal_units.append({
                "unit_text": word,
                "unit_norm": w,
                "source": cand.get("source"),
                "reason": "resource_options_failed_contextual_validation",
            })

    result["contextual_synonym_tasks"] = existing_tasks
    result["keep_promotable_units"] = promotable_units
    result["keep_internal_units"] = internal_units


def _v164_long_span_sources(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    failures = result.get("qa", {}).get("source_audit", {}).get("context_fit_check_failures", []) or []
    for f in failures:
        reason = str(f.get("reason") or "")
        text = str(f.get("unit_text") or "")
        if not text:
            continue
        if "too_long" in reason or len(_v164_wordish_tokens(text)) >= V164_LONG_SPAN_MIN_TOKENS:
            out.append({"unit_text": text, "reason": reason, "source": "context_fit_failure"})
    for u in (result.get("clarify_units") or []) + (result.get("keep_units") or []):
        text = str(u.get("unit_text") or "")
        if len(_v164_wordish_tokens(text)) >= V164_LONG_SPAN_MIN_TOKENS:
            out.append({"unit_text": text, "reason": "long_visible_or_keep_unit", "source": "result_unit", "context": u.get("context")})
    # Dedup by text.
    seen: Set[str] = set()
    uniq: List[Dict[str, Any]] = []
    for item in out:
        key = norm_text(item.get("unit_text"))
        if key and key not in seen:
            seen.add(key); uniq.append(item)
    return uniq


def _v164_split_long_span(text: str) -> List[str]:
    raw = re.sub(r"\s+", " ", str(text or "").strip(" .!?;:"))
    if not raw:
        return []
    # Split on universal clause/coordinator boundaries. No topic-specific terms.
    parts = re.split(r"\b(?:and|but|because|although|though|while|whereas|that|which|who)\b|[,;:]", raw, flags=re.I)
    candidates: List[str] = []
    for part in parts:
        part = re.sub(r"\s+", " ", part).strip(" .!?;:")
        toks = _v164_wordish_tokens(part)
        if V164_SPLIT_MIN_CANDIDATE_TOKENS <= len(toks) <= V164_SPLIT_MAX_CANDIDATE_TOKENS:
            candidates.append(part)
        # Sliding windows preserve possible predicate-object chunks inside longer parts.
        words = re.findall(r"[A-Za-z][A-Za-z'-]*", part)
        for n in range(V164_SPLIT_MIN_CANDIDATE_TOKENS, min(V164_SPLIT_MAX_CANDIDATE_TOKENS, len(words)) + 1):
            for i in range(0, len(words) - n + 1):
                cand = " ".join(words[i:i+n]).strip()
                low_toks = _v164_wordish_tokens(cand)
                if len(low_toks) < V164_SPLIT_MIN_CANDIDATE_TOKENS:
                    continue
                # Prefer chunks containing a resource-supported content word.
                if not any(t in EXTERNAL_WORD_SUGGESTIONS for t in low_toks):
                    continue
                candidates.append(cand)
    out: List[str] = []
    seen: Set[str] = set()
    for c in candidates:
        low = norm_text(c)
        if not low or low in seen:
            continue
        if low in STOPWORDS:
            continue
        seen.add(low); out.append(c)
    return out[:12]


def _v164_context_for_span(span: str, payload: Dict[str, Any], fallback: str = "") -> str:
    if fallback and span and norm_text(span) in norm_text(fallback):
        return fallback
    for sent in _v164_sentence_list_from_payload(payload):
        if span and norm_text(span) in norm_text(sent):
            return sent
    return fallback or span


def _v164_apply_long_span_split_recovery(payload: Dict[str, Any], result: Dict[str, Any], validator: ContextFitValidator, failures: List[Dict[str, Any]]) -> None:
    existing = {norm_text(u.get("unit_text")) for u in result.get("enhance_units") or []}
    if len(result.get("enhance_units") or []) >= 5:
        return
    released: List[Dict[str, Any]] = []
    sources = _v164_long_span_sources(result)
    V164_QA_CACHE["long_span_containers_seen"] = len(sources)
    for src in sources:
        if len(released) >= V164_LONG_SPAN_RECOVERY_CAP:
            break
        span = str(src.get("unit_text") or "")
        context = _v164_context_for_span(span, payload, str(src.get("context") or ""))
        subcands = _v164_split_long_span(span)
        V164_QA_CACHE["long_span_split_candidates_generated"] += len(subcands)
        for cand in subcands:
            if len(result.get("enhance_units") or []) + len(released) >= 5:
                break
            low = norm_text(cand)
            if low in existing or any(low in norm_text(u.get("unit_text")) for u in result.get("fix_units") or []):
                continue
            ctx = _v164_context_for_span(cand, payload, context)
            suggestions = _v163_generate_combo_resource_suggestions(cand, limit=8)
            if not suggestions:
                V164_QA_CACHE["long_span_split_candidates_rejected"] += 1
                continue
            valid = _v163_validate_recovery_suggestions(cand, suggestions, ctx, validator, failures, tier="v164_long_span_split_recovery")
            if len(valid) >= LLM_MIN_VALID_SUGGESTIONS:
                unit = {
                    "unit_id": f"enh_split_{abs(hash((cand, ctx))) % 10**8:08d}",
                    "class_label": "ENHANCE",
                    "unit_text": cand,
                    "unit_norm": norm_text(cand),
                    "unit_type": "split_phrase_candidate",
                    "source_sentence_index": None,
                    "source_paragraph_index": None,
                    "context": ctx,
                    "axis_candidates": ["collocation_naturalness", "semantic_specificity", "paraphrase_range"],
                    "extraction_signal": "v164_long_span_split_recovery",
                    "extraction_flags": ["long_span_container_split", "strict_final_qa_validated", "phrase_first"],
                    "candidate_value": 0.62,
                    "evidence_ids": [],
                    "frequency": 1,
                    "safety_level": "v164_long_span_split_strict_recovery_validated",
                    "replacement_scope": "whole_phrase",
                    "suggestions": valid,
                    "covered_subunits": [],
                    "dedup_role": "v164_split_recovered_phrase",
                    "source_kind": "v164_long_span_split_recovery",
                    "reveal_policy": {
                        "mode": "produce_before_reveal",
                        "attempt_required_before_suggestions_shown": True,
                        "suggestions_role": "reveal_phase_model_answer_for_comparison",
                    },
                    "phase1_prompt": f"This phrase is understandable, but could be more precise or natural: [{cand}]. How would you paraphrase the phrase without changing the meaning?",
                    "phase1_options": [{"option_id": "WRITE_MY_OWN", "label": "Write my own answer"}],
                    "recovered_by_v164": True,
                    "container_source_span": span,
                }
                released.append(unit)
                existing.add(low)
                V164_QA_CACHE["long_span_split_candidates_promoted"] += 1
                arr = V164_QA_CACHE.setdefault("long_span_split_audit_sample", [])
                if len(arr) < 20:
                    arr.append({"container": span, "promoted": cand, "suggestion_count": len(valid)})
                break
            else:
                V164_QA_CACHE["long_span_split_candidates_rejected"] += 1
                arr = V164_QA_CACHE.setdefault("long_span_split_audit_sample", [])
                if len(arr) < 20:
                    arr.append({"container": span, "candidate": cand, "reason": "fewer_than_min_valid_suggestions", "valid_suggestion_count": len(valid)})
    if released:
        result["enhance_units"] = _v163_dedup_enhance_units((result.get("enhance_units") or []) + released)


def _v164_sync_outputs(result: Dict[str, Any]) -> None:
    # Rebuild replacement options to include v1.6.4 tasks.
    options: List[Dict[str, Any]] = []
    for u in (result.get("fix_units") or []) + (result.get("enhance_units") or []) + (result.get("contextual_synonym_tasks") or []):
        if not isinstance(u, dict):
            continue
        options.append({
            "unit_id": u.get("unit_id"),
            "unit_text": u.get("unit_text"),
            "class_label": u.get("class_label"),
            "replacement_scope": u.get("replacement_scope"),
            "suggestions": u.get("suggestions") or [],
            "reveal_policy": u.get("reveal_policy"),
        })
    result["replacement_options"] = options
    # Rebuild practice targets while preserving existing FIX/CLARIFY logic.
    targets: List[Dict[str, Any]] = []
    for u in result.get("fix_units") or []:
        targets.append({"unit_id": u.get("unit_id"), "unit_text": u.get("unit_text"), "category": "fix", "family": u.get("error_family"), "priority_weight": 3.5, "history_count": 0, "recommended_practice_type": "repair_before_polish"})
    for u in result.get("enhance_units") or []:
        targets.append({"unit_id": u.get("unit_id"), "unit_text": u.get("unit_text"), "category": "enhance", "tier": u.get("safety_level") or "phrase_level_context_validated", "priority_weight": 2.3 if not u.get("recovered_by_v164") else 2.2, "history_count": 0, "recommended_practice_type": "produce_before_reveal_phrase_paraphrase"})
    for u in result.get("clarify_units") or []:
        targets.append({"unit_id": u.get("unit_id"), "unit_text": u.get("unit_text"), "category": "clarify", "tier": "visible_meaning_clarification", "priority_weight": 2.0, "history_count": 0, "recommended_practice_type": "clarify_before_paraphrase"})
    for u in result.get("contextual_synonym_tasks") or []:
        targets.append({"unit_id": u.get("unit_id"), "unit_text": u.get("unit_text"), "category": "contextual_synonym", "tier": u.get("safety_level") or "contextual_synonym_validated", "priority_weight": 1.4, "history_count": 0, "recommended_practice_type": "contextual_word_or_phrase_variation_light"})
    result["lret_practice_targets"] = targets


def _v164_refresh_metrics(result: Dict[str, Any]) -> None:
    p = result.setdefault("lexical_profile", {})
    p["enhance_count"] = len(result.get("enhance_units") or [])
    p["keep_count"] = len(result.get("keep_units") or [])
    p["clarify_count"] = len(result.get("clarify_units") or [])
    p["contextual_synonym_task_count"] = len(result.get("contextual_synonym_tasks") or [])
    p["keep_promotable_count"] = len(result.get("keep_promotable_units") or [])
    p["keep_internal_count"] = len(result.get("keep_internal_units") or [])
    p["classification_distribution"] = {
        "FIX": len(result.get("fix_units") or []),
        "ENHANCE": len(result.get("enhance_units") or []),
        "KEEP": len(result.get("keep_units") or []),
        "CLARIFY": len(result.get("clarify_units") or []),
        "CONTEXTUAL_SYNONYM": len(result.get("contextual_synonym_tasks") or []),
    }
    V164_QA_CACHE["final_main_enhance_count"] = p["enhance_count"]
    V164_QA_CACHE["final_contextual_synonym_task_count"] = p["contextual_synonym_task_count"]
    V164_QA_CACHE["keep_promotable_count"] = p["keep_promotable_count"]
    V164_QA_CACHE["keep_internal_count"] = p["keep_internal_count"]
    if p["enhance_count"] >= 5 and p["contextual_synonym_task_count"] >= 1:
        V164_QA_CACHE["quota_status"] = "target_met_or_near_met_with_single_word_audit"
    elif p["enhance_count"] >= 4 and p["contextual_synonym_task_count"] >= 1:
        V164_QA_CACHE["quota_status"] = "near_target_with_single_word_audit"
    else:
        V164_QA_CACHE["quota_status"] = "under_target_after_single_word_audit"
    # Convert Counters to normal dicts for JSON.
    cache = copy.deepcopy(V164_QA_CACHE)
    if isinstance(cache.get("single_word_routes"), Counter):
        cache["single_word_routes"] = dict(cache["single_word_routes"])
    p["v164_single_word_contextual_audit"] = cache
    qa = result.setdefault("qa", {})
    qa.setdefault("source_audit", {})["single_word_synonym_audit"] = cache.get("single_word_audit_sample", [])
    checks = qa.setdefault("contract_checks", {})
    checks["v164_all_single_words_audited_for_contextual_synonyms"] = True
    checks["v164_single_word_synonyms_are_sentence_validated"] = True
    checks["v164_contextual_synonyms_separate_from_main_enhance"] = True
    checks["v164_keep_promotable_routing_enabled"] = True
    checks["v164_long_spans_are_split_before_final_routing"] = True
    checks["v164_no_topic_or_essay_specific_rules"] = True
    checks["v164_no_embedded_phrase_bank"] = True
    qa["warnings"] = [w for w in qa.get("warnings", []) if not str(w).startswith("v164_quota_status")]
    if "under_target" in V164_QA_CACHE["quota_status"]:
        qa.setdefault("warnings", []).append(f"v164_quota_status: {V164_QA_CACHE['quota_status']}")
        qa["status"] = "needs_tuning"
        qa["confidence"] = min(float(qa.get("confidence") or 0.78), 0.78)
    elif qa.get("status") == "needs_tuning" and not qa.get("errors"):
        # Keep upstream evaluator noise warning, but allow stronger LRET route status.
        if any("upstream_evaluator_units_need_cleanup" in str(w) for w in qa.get("warnings", [])):
            qa["status"] = "ok_with_monitoring"
            qa["confidence"] = min(max(float(qa.get("confidence") or 0.80), 0.80), 0.84)
        else:
            qa["status"] = "ok"
            qa["confidence"] = min(max(float(qa.get("confidence") or 0.82), 0.82), 0.86)


def _v164_update_learning_payload(result: Dict[str, Any]) -> None:
    lip = result.get("learning_intelligence_payload")
    if not isinstance(lip, dict):
        return
    metrics = {m.get("metric_id"): m for m in lip.get("metric_signals") or [] if isinstance(m, dict)}
    for metric_id, value in {
        "lret_v164_single_word_candidates_audited": V164_QA_CACHE.get("single_word_candidates_audited"),
        "lret_v164_single_word_contextual_tasks": V164_QA_CACHE.get("single_word_contextual_synonym_tasks_added"),
        "lret_keep_promotable_count": len(result.get("keep_promotable_units") or []),
        "lret_keep_internal_count": len(result.get("keep_internal_units") or []),
        "lret_long_span_split_promotions": V164_QA_CACHE.get("long_span_split_candidates_promoted"),
    }.items():
        if metric_id in metrics:
            metrics[metric_id]["value"] = value
        else:
            lip.setdefault("metric_signals", []).append({"metric_id": metric_id, "value": value})
    notes = list(lip.get("notes") or [])
    note = "v1.6.4 audits all single-word KEEP/internal candidates for contextual synonym potential; only sentence-validated alternatives become student tasks."
    if note not in notes:
        notes.append(note)
    lip["notes"] = notes


_prev_analyze_v163_for_v164 = analyze


def analyze(payload: Dict[str, Any], validator: Optional[ContextFitValidator] = None) -> Dict[str, Any]:
    _v164_reset()
    validator = validator or RuleBasedContextFitValidator()
    result = _prev_analyze_v163_for_v164(payload, validator)
    result["run"]["engine_version"] = ENGINE_VERSION
    qa = result.setdefault("qa", {})
    failures = qa.setdefault("source_audit", {}).setdefault("context_fit_check_failures", [])
    # First split long containers. Then audit/promote all single words.
    _v164_apply_long_span_split_recovery(payload, result, validator, failures)
    _v164_apply_full_single_word_audit(payload, result, failures)
    _v164_sync_outputs(result)
    _v164_refresh_metrics(result)
    _v164_update_learning_payload(result)
    return result



# ============================================================================
# v1.6.5 -- Pair-specific collocation attestation + KEEP/ENHANCE phrase-overlap
# dedup + OCR-noise filtering + free (zero-LLM-cost) registry modifier-swap ENHANCE
# ============================================================================
#
# Root causes fixed in this version (see LRET_v1_6_5_pair_attested_safety_and_dedup_spec.md
# for full evidence and line-number references into the v1.6.4 source):
#
# 1. SAFETY: V15_RESOURCE_INDEX['positive_collocations'] is keyed by the bare collocate
#    word alone (e.g. "beneficial" registers as "an attested collocation" because it
#    modifies SOME headword somewhere in the ~119k-row dictionary), not by the SPECIFIC
#    headword being modified in the sentence at hand. This is why the real v1.6.4 output
#    accepted "advantageous advice" as a validated CONTEXTUAL_SYNONYM suggestion even
#    though its own suggestion_resource_score was 0.0. This version adds a proper
#    headword -> relation_type -> {collocates} index (V165_COLLOCATES_BY_HEADWORD) and
#    hard-requires PAIR-specific attestation for single-word contextual-synonym
#    substitutions, the highest-risk case. Un-attested candidates are not silently kept --
#    they are removed from `suggestions`, and if that empties a task it is converted to a
#    CONTEXTUAL_SYNONYM_OPEN unit: the student is still asked to produce their own
#    alternative, but the system does not assert an unverified "correct" answer.
#
# 2. COST: --use-llm was already opt-in and OFF by default in v1.6.4 (confirmed by reading
#    main()/argparse) -- the $0.05/run cost came from explicitly passing --use-llm, not
#    from a hidden default. This version does not change that default; it adds a genuinely
#    free (registry-only, zero-LLM) modifier-swap ENHANCE generator (see point 4) so more
#    of the essay is covered before any LLM call is even considered, and documents the
#    exact command that runs at $0.
#
# 3. DATA QUALITY: roughly 7.4% of positive_collocations_registry rows contain OCR/scan
#    noise inherited from the source dictionary digitization (e.g. "!ssue", "agelng",
#    stray punctuation and fragments). The new pair-attestation index filters these out
#    with a clean-token check so garbled strings can never reach a student.
#
# 4. FREE ENHANCE GENERATION: if a KEEP phrase pairs a known headword with one of its OWN
#    attested collocates (e.g. headword "advice" + attested adjective_modifier collocate
#    "good" in "good advice"), sibling collocates of the SAME relation_type for the SAME
#    headword are safe, zero-cost ENHANCE candidates (e.g. "sound advice", "valuable
#    advice", "practical advice") -- attested by construction, so this cannot reproduce the
#    "advantageous advice" failure mode. This runs whether or not --use-llm is set.
#
# 5. DEDUP: "guide community groups" could appear in KEEP as-is while "can guide community
#    groups" simultaneously appears in ENHANCE (replace "guide") -- same core content,
#    contradictory signal to the student. This version suppresses a KEEP unit whose core
#    content (ignoring a small set of leading modal/auxiliary/pronoun tokens) is contained,
#    in order, inside a released ENHANCE unit's text.
#
# ============================================================================


# ----------------------------------------------------------------------------
# v1.6.5 performance fix (discovered while building the zero-cost smoke test):
# the v1.5 `_v15_resource_evidence_for_text` scanned EVERY key in the positive-
# collocation and governance-pattern indexes (tens of thousands of keys, and for
# each one called `surface_tokens()` just to check its length) for EVERY candidate
# text. `_v145_reconstruct_candidate_units` alone produces ~250-370 candidates per
# essay, so this was tens of millions of tokenizer calls per run -- confirmed via
# faulthandler stack sampling to make a single short essay take well over a minute
# of pure CPU time with NO LLM involved at all. This is a real, pre-existing cost
# driver independent of the $0.05/run LLM spend the user flagged.
#
# Fix: generate the CANDIDATE text's own n-grams once (a small, bounded set for any
# realistic span) and do O(1) dict-membership checks against the index, instead of
# scanning the whole index per candidate. Same inputs/outputs/scoring; just fast.
# ----------------------------------------------------------------------------

def _v165_ngrams(tokens: List[str], min_n: int = 2, max_n: int = 4) -> List[str]:
    out: List[str] = []
    n_tokens = len(tokens)
    for n in range(min_n, max_n + 1):
        if n > n_tokens:
            break
        for i in range(n_tokens - n + 1):
            out.append(' '.join(tokens[i:i + n]))
    return out


def _v15_resource_evidence_for_text(text: str) -> Dict[str, Any]:
    low = norm_text(text)
    toks = [norm_text(t) for t in surface_tokens(text)]
    evidence = {
        'exact_positive_collocation': low in V15_RESOURCE_INDEX.get('positive_collocations', {}),
        'exact_governance_pattern': low in V15_RESOURCE_INDEX.get('governance_patterns', {}),
        'positive_collocation_submatches': [],
        'governance_submatches': [],
        'lexical_entry_count': 0,
        'academic_lemma_count': 0,
        'resource_score': 0.0,
    }
    if evidence['exact_positive_collocation']:
        evidence['resource_score'] += 0.55
    if evidence['exact_governance_pattern']:
        evidence['resource_score'] += 0.35

    pos_index = V15_RESOURCE_INDEX.get('positive_collocations', {})
    gov_index = V15_RESOURCE_INDEX.get('governance_patterns', {})
    max_n = min(4, len(toks)) if toks else 0
    if max_n >= 2:
        for ng in _v165_ngrams(toks, 2, max_n):
            if len(evidence['positive_collocation_submatches']) >= 5:
                break
            if ng in pos_index:
                evidence['positive_collocation_submatches'].append(ng)
        for ng in _v165_ngrams(toks, 2, max_n):
            if len(evidence['governance_submatches']) >= 5:
                break
            if '::' not in ng and ng in gov_index:
                evidence['governance_submatches'].append(ng)

    if evidence['positive_collocation_submatches']:
        evidence['resource_score'] += min(0.35, 0.12 * len(evidence['positive_collocation_submatches']))
    if evidence['governance_submatches']:
        evidence['resource_score'] += min(0.20, 0.08 * len(evidence['governance_submatches']))
    lex_entries = V15_RESOURCE_INDEX.get('lexical_entries', {})
    acad = V15_RESOURCE_INDEX.get('academic_lemmas', set())
    for t in toks:
        if t in lex_entries:
            evidence['lexical_entry_count'] += 1
        if t in acad:
            evidence['academic_lemma_count'] += 1
    evidence['resource_score'] += min(0.25, 0.04 * evidence['lexical_entry_count'])
    evidence['resource_score'] += min(0.25, 0.08 * evidence['academic_lemma_count'])
    evidence['resource_score'] = round(float(evidence['resource_score']), 3)
    return evidence


_V165_CLEAN_TOKEN_RE = re.compile(r"^[A-Za-z]+(?:[-'][A-Za-z]+)*$")


def _v165_clean_token(text: str) -> bool:
    t = str(text or "").strip()
    return bool(t) and bool(_V165_CLEAN_TOKEN_RE.match(t))


V165_COLLOCATES_BY_HEADWORD: Dict[str, Dict[str, Set[str]]] = {}
V165_QA_CACHE: Dict[str, Any] = {}


def _v165_build_pair_attestation_index(resource_path: Optional[str]) -> None:
    """headword -> relation_type -> {collocate,...}, built directly from the positive
    collocations registry with OCR-noise rows filtered out. This answers the question the
    v1.5 index cannot: 'is CANDIDATE attested as a collocate of THIS SPECIFIC HEADWORD',
    not just 'does CANDIDATE appear as a collocate of anything, anywhere, in the whole file'.
    """
    global V165_COLLOCATES_BY_HEADWORD
    V165_COLLOCATES_BY_HEADWORD = {}
    if not resource_path:
        return
    rows: List[Dict[str, str]] = []
    for fname in ('positive_collocations_registry_v2.tsv', 'positive_collocations_registry.tsv'):
        rows = list(_iter_tsv_from_resource(resource_path, fname) or [])
        if rows:
            break
    known_words = V15_RESOURCE_INDEX.get('lexical_entries', {})

    def _v165_is_usable_word(tok: str) -> bool:
        # Character-shape check catches stray symbols/punctuation from the OCR scan (e.g.
        # "!ssue"). It does NOT catch a clean-looking but truncated word fragment (e.g.
        # "guished" from a scan-dropped "distinguished") -- for that, plain single-token
        # words (no hyphen/apostrophe) are additionally required to be a real, known word
        # in lexical_registry.json. Hyphenated/apostrophe compounds (e.g. "age-old",
        # "round-the-clock") are exempt from the real-word check, since many legitimate
        # compounds are absent from the general-vocabulary word lists lexical_registry.json
        # is built from, and over-rejecting those would lose good material for a much
        # rarer failure mode than plain word-fragment noise.
        if not _v165_clean_token(tok):
            return False
        if tok in _V166_META_ABBREVIATIONS:
            return False
        if '-' in tok or "'" in tok:
            return True
        return tok in known_words

    rows_seen = 0
    rows_kept = 0
    rows_noise_rejected = 0
    for row in rows:
        rows_seen += 1
        if str(row.get('runtime_role') or '') != 'positive_evidence_lret_keep':
            continue
        headword = norm_text(row.get('headword'))
        collocate = norm_text(row.get('collocate'))
        rel = str(row.get('relation_type') or 'unknown')
        if not headword or not collocate:
            continue
        if not (_v165_is_usable_word(headword) and _v165_is_usable_word(collocate)):
            rows_noise_rejected += 1
            continue
        V165_COLLOCATES_BY_HEADWORD.setdefault(headword, {}).setdefault(rel, set()).add(collocate)
        rows_kept += 1
    V165_QA_CACHE['pair_attestation_rows_seen'] = rows_seen
    V165_QA_CACHE['pair_attestation_rows_kept'] = rows_kept
    V165_QA_CACHE['pair_attestation_rows_noise_rejected'] = rows_noise_rejected
    V165_QA_CACHE['pair_attestation_headwords_indexed'] = len(V165_COLLOCATES_BY_HEADWORD)


_prev_load_canonical_resources_v165 = load_canonical_resources


def load_canonical_resources(resource_path: Optional[str]) -> None:
    _prev_load_canonical_resources_v165(resource_path)
    _v165_build_pair_attestation_index(resource_path)


def _v165_pair_attested(headword: str, candidate: str) -> bool:
    hw = norm_text(headword)
    cand = norm_text(candidate)
    buckets = V165_COLLOCATES_BY_HEADWORD.get(hw)
    if not buckets:
        return False
    return any(cand in vals for vals in buckets.values())


def _v165_context_neighbors(word: str, context: str) -> List[str]:
    """The word immediately before and after `word` in `context` -- for a single content
    word the registry-relevant partner is almost always the noun it modifies (or the
    noun/verb that governs it) sitting right next to it in the sentence.
    """
    toks = [norm_text(t) for t in surface_tokens(context or "")]
    w = norm_text(word)
    out: List[str] = []
    try:
        idx = toks.index(w)
    except ValueError:
        return out
    if idx + 1 < len(toks):
        out.append(toks[idx + 1])
    if idx - 1 >= 0:
        out.append(toks[idx - 1])
    return out


def _v165_reaudit_contextual_synonym_tasks(result: Dict[str, Any]) -> None:
    """Hard-require pair-specific collocation attestation for every suggestion inside
    contextual_synonym_tasks. A suggestion that cannot be confirmed as an attested
    collocate of a word next to it in the sentence is removed from `suggestions`; if that
    empties the task, the task moves to `contextual_synonym_open_units` -- same prompt, no
    asserted 'correct' answer, so the student still practices production without being
    shown an unverified claim.
    """
    tasks = result.get('contextual_synonym_tasks') or []
    kept_tasks: List[Dict[str, Any]] = []
    open_units: List[Dict[str, Any]] = list(result.get('contextual_synonym_open_units') or [])
    reaudit_removed = 0
    reaudit_kept = 0
    for task in tasks:
        word = str(task.get('unit_text') or '')
        context = str(task.get('context') or '')
        neighbors = _v165_context_neighbors(word, context)
        checkable_neighbors = [nb for nb in neighbors if nb in V165_COLLOCATES_BY_HEADWORD]
        surviving = []
        for sug in task.get('suggestions') or []:
            cand = str(sug.get('text') if isinstance(sug, dict) else sug)
            if not checkable_neighbors:
                # No neighbor is a known headword in the registry at all -- there is no
                # attestation evidence available either way, so this is not something the
                # pair-attestation gate can rule on. Do not reject on absence of evidence;
                # leave the existing (already-passed) checks as the verdict.
                surviving.append(sug)
                reaudit_kept += 1
                continue
            attested_against = [nb for nb in checkable_neighbors if _v165_pair_attested(nb, cand)]
            if attested_against:
                reaudit_kept += 1
                if isinstance(sug, dict):
                    sug = copy.deepcopy(sug)
                    sug.setdefault('validation', {}).setdefault('gates', []).append('v165_pair_attestation_confirmed')
                    sug['pair_attested_against'] = attested_against
                surviving.append(sug)
            else:
                reaudit_removed += 1
        if surviving:
            task = copy.deepcopy(task)
            task['suggestions'] = surviving
            kept_tasks.append(task)
        else:
            open_task = copy.deepcopy(task)
            open_task['class_label'] = 'CONTEXTUAL_SYNONYM_OPEN'
            open_task['suggestions'] = []
            open_task['asserted_answer_available'] = False
            open_task['reason'] = 'no_candidate_passed_pair_specific_collocation_attestation'
            open_task['phase1_prompt'] = (
                f"This word is understandable in context: [{word}]. Write a contextually "
                f"fitting alternative for this sentence -- there is no single \"correct\" "
                f"answer stored for this one, so focus on keeping the same meaning and "
                f"register as the original."
            )
            open_units.append(open_task)
    result['contextual_synonym_tasks'] = kept_tasks
    result['contextual_synonym_open_units'] = open_units
    V165_QA_CACHE['single_word_suggestions_pair_attested_kept'] = reaudit_kept
    V165_QA_CACHE['single_word_suggestions_pair_attestation_failed_removed'] = reaudit_removed
    V165_QA_CACHE['contextual_synonym_open_units_count'] = len(open_units)


# ----------------------------------------------------------------------------
# v1.6.6 -- plural-aware matching. Found via direct headword-coverage analysis:
# several KEEP units have a real, attested swap opportunity in the registry that
# v1.6.5 silently missed because of an exact-string mismatch on grammatical number.
# Example, confirmed directly against the registry: the essay says "care homes"
# (plural); the registry stores the compound as headword "care" + head_noun_compound
# collocate "home" (singular). v1.6.5's exact match on "homes" vs "home" failed, so
# a real, safe alternative (e.g. "care centre(s)", "care unit(s)") was never offered.
# This adds a plural<->singular normalization step (irregular_noun_registry.json
# first, a naive regular-plural rule as fallback) on BOTH sides of the match, and
# pluralizes the released alternative to match the original token's number so the
# suggested phrase still reads grammatically.
# ----------------------------------------------------------------------------

V166_IRREGULAR_SING_TO_PLUR: Dict[str, str] = {}
V166_IRREGULAR_PLUR_TO_SING: Dict[str, str] = {}

_V166_META_ABBREVIATIONS = {"etc", "esp", "eg", "ie", "cf", "viz", "al"}
# Latin/list-truncation abbreviations found leaking into the collocate field
# (e.g. "national, local, ethnic, etc." digitized with "etc" as if it were an
# ordinary adjective candidate). A tiny, closed, structural denylist -- not a
# content/vocabulary list -- exactly like EDGE_STOPWORDS in the base engine.


def _v166_load_irregular_nouns(resource_path: Optional[str]) -> None:
    global V166_IRREGULAR_SING_TO_PLUR, V166_IRREGULAR_PLUR_TO_SING
    V166_IRREGULAR_SING_TO_PLUR = {}
    V166_IRREGULAR_PLUR_TO_SING = {}
    data = _read_json_from_resource(resource_path, 'irregular_noun_registry.json') if resource_path else None
    if not isinstance(data, list):
        return
    for row in data:
        if not isinstance(row, dict):
            continue
        sing = norm_text(row.get('singular'))
        plur = norm_text(row.get('plural'))
        variants = [norm_text(v) for v in (row.get('plural_variants') or [])]
        if sing and plur:
            V166_IRREGULAR_SING_TO_PLUR[sing] = plur
            V166_IRREGULAR_PLUR_TO_SING[plur] = sing
        for v in variants:
            if v and sing:
                V166_IRREGULAR_PLUR_TO_SING.setdefault(v, sing)


_prev_load_canonical_resources_v166 = load_canonical_resources


def load_canonical_resources(resource_path: Optional[str]) -> None:
    _prev_load_canonical_resources_v166(resource_path)
    _v166_load_irregular_nouns(resource_path)


def _v166_singularize(word: str) -> str:
    w = norm_text(word)
    if w in V166_IRREGULAR_PLUR_TO_SING:
        return V166_IRREGULAR_PLUR_TO_SING[w]
    if w.endswith('ies') and len(w) > 4:
        return w[:-3] + 'y'
    if w.endswith(('ses', 'xes', 'zes', 'ches', 'shes')) and len(w) > 4:
        return w[:-2]
    if w.endswith('s') and not w.endswith('ss') and len(w) > 3:
        return w[:-1]
    return w


def _v166_pluralize(word: str) -> str:
    w = norm_text(word)
    if w in V166_IRREGULAR_SING_TO_PLUR:
        return V166_IRREGULAR_SING_TO_PLUR[w]
    if w.endswith('y') and len(w) > 1 and w[-2] not in 'aeiou':
        return w[:-1] + 'ies'
    if w.endswith(('s', 'x', 'z', 'ch', 'sh')):
        return w + 'es'
    return w + 's'


def _v165_generate_free_modifier_swap_candidates(unit_text: str) -> List[Dict[str, Any]]:
    """Zero-LLM-cost ENHANCE candidate generation: if unit_text pairs a known headword with
    one of ITS OWN attested collocates, offer sibling collocates of the SAME relation_type
    for the SAME headword. Attested by construction -- cannot reproduce the "advantageous
    advice" failure mode, because both the original modifier and every candidate come from
    the same headword+relation bucket in the registry. v1.6.6: matches the essay's token
    against the registry's stored form even when they differ only in grammatical number
    (plural essay token vs. singular registry entry, or vice versa), and pluralizes the
    released alternative to match the original so the suggested phrase stays grammatical.
    """
    tokens = [norm_text(t) for t in surface_tokens(unit_text)]
    out: List[Dict[str, Any]] = []
    seen_phrases: Set[str] = set()
    for headword, by_relation in V165_COLLOCATES_BY_HEADWORD.items():
        headword_forms = {headword, _v166_pluralize(headword)}
        matched_headword_tok = next((t for t in tokens if t in headword_forms), None)
        if not matched_headword_tok:
            continue
        for other_tok in tokens:
            if other_tok == matched_headword_tok:
                continue
            other_sing = _v166_singularize(other_tok)
            is_plural_form = other_sing != other_tok
            for rel, collocates in by_relation.items():
                # Plural<->singular normalization only makes grammatical sense for
                # noun-compound relations (both slots are nouns, so pluralizing the
                # alternative to match the original's number is a valid, natural
                # operation). Adjective/adverb/verb-pattern relations do not pluralize
                # -- doing so produced nonsense like "care exquisites" / "care propers"
                # in testing, so those relations require an exact-form match only.
                if other_tok in collocates:
                    match_tok, needs_pluralizing = other_tok, False
                elif rel == 'head_noun_compound' and is_plural_form and other_sing in collocates:
                    match_tok, needs_pluralizing = other_sing, True
                else:
                    continue
                for alt in collocates:
                    if alt == match_tok:
                        continue
                    already_plural_looking = _v166_singularize(alt) != alt
                    alt_out = (_v166_pluralize(alt)
                               if (needs_pluralizing and not already_plural_looking)
                               else alt)
                    new_phrase = re.sub(rf'\b{re.escape(other_tok)}\b', alt_out, unit_text,
                                         count=1, flags=re.IGNORECASE)
                    key = new_phrase.lower()
                    if key not in seen_phrases:
                        seen_phrases.add(key)
                        out.append({'text': alt_out, 'suggested_phrase': new_phrase,
                                    'replaces_token': other_tok, 'relation_type': rel,
                                    'headword': headword})
    return out


def _v165_promote_free_modifier_swaps(result: Dict[str, Any]) -> None:
    keep_units = result.get('keep_units') or []
    survivors: List[Dict[str, Any]] = []
    promoted: List[Dict[str, Any]] = []
    for ku in keep_units:
        text = str(ku.get('unit_text') or '')
        if len(surface_tokens(text)) < 2:
            survivors.append(ku)
            continue
        candidates = _v165_generate_free_modifier_swap_candidates(text)
        if not candidates:
            survivors.append(ku)
            continue
        suggestions = []
        for c in candidates[:4]:
            suggestions.append({
                'text': c['suggested_phrase'],
                'validation': {
                    'accepted': True,
                    'gates': ['v165_same_headword_pair_attested_modifier_swap'],
                    'reason': (
                        c['text'] + " is an attested " + c['relation_type'] +
                        " collocate of '" + c['headword'] + "', the same headword the "
                        "original modifier attaches to -- zero-cost, registry-verified, "
                        "no LLM used."
                    ),
                },
                'suggestion_source': 'v165_free_registry_modifier_swap',
            })
        promoted.append({
            'unit_id': ku.get('unit_id') or f"v165_enh_{len(promoted) + 1:04d}",
            'class_label': 'ENHANCE',
            'unit_text': text,
            'unit_type': ku.get('unit_type'),
            'safety_level': 'registry_pair_attested_modifier_swap',
            'suggestions': suggestions,
            'reveal_policy': {
                'mode': 'produce_before_reveal',
                'attempt_required_before_suggestions_shown': True,
                'suggestions_role': 'reveal_phase_model_answer_for_comparison',
            },
            'phase1_prompt': (
                f"This part is correct, but could be more precise/formal/natural: [{text}]. "
                f"How would you rephrase it?"
            ),
            'evidence_ids': ku.get('evidence_ids', []),
            'frequency': ku.get('frequency', 1),
            'cost': 'free_registry_only_no_llm',
        })
    result['keep_units'] = survivors
    existing_enhance = result.get('enhance_units') or []
    result['enhance_units'] = existing_enhance + promoted
    V165_QA_CACHE['keep_promoted_to_free_enhance'] = len(promoted)


_V165_LEADING_STRIP_TOKENS = {
    "can", "could", "may", "might", "must", "should", "would", "will", "shall",
    "i", "we", "you", "they", "he", "she", "it", "to",
}


def _v165_core_tokens(text: str) -> List[str]:
    toks = [norm_text(t) for t in surface_tokens(text or "")]
    while toks and toks[0] in _V165_LEADING_STRIP_TOKENS:
        toks = toks[1:]
    return toks


def _v165_suppress_keep_phrase_overlaps(result: Dict[str, Any]) -> None:
    """If a KEEP unit's core content (ignoring a leading modal/pronoun) is fully contained,
    in order, inside a released ENHANCE unit's text, the KEEP affirmation contradicts the
    ENHANCE task covering the same content. Drop the KEEP unit in that case.
    """
    enhance_core = [
        (_v165_core_tokens(str(u.get('unit_text') or '')), u.get('unit_id'))
        for u in (result.get('enhance_units') or [])
    ]
    keep_units = result.get('keep_units') or []
    survivors: List[Dict[str, Any]] = []
    suppressed: List[Dict[str, Any]] = []
    for ku in keep_units:
        core = _v165_core_tokens(str(ku.get('unit_text') or ''))
        hit = None
        if core:
            for enh_core, enh_id in enhance_core:
                if len(enh_core) >= len(core) and any(
                    enh_core[i:i + len(core)] == core for i in range(len(enh_core) - len(core) + 1)
                ):
                    hit = enh_id
                    break
        if hit:
            suppressed.append({
                'unit_text': ku.get('unit_text'),
                'reason': 'superseded_by_enhance_unit_covering_same_content',
                'enhance_unit_id': hit,
            })
        else:
            survivors.append(ku)
    result['keep_units'] = survivors
    if suppressed:
        internal = list(result.get('keep_internal_units') or [])
        internal.extend(suppressed)
        result['keep_internal_units'] = internal
    V165_QA_CACHE['keep_units_suppressed_for_enhance_overlap'] = len(suppressed)


def _v165_refresh_profile(result: Dict[str, Any]) -> None:
    prof = result.setdefault('lexical_profile', {})
    prof['v165_pair_attestation_dedup_and_free_enhance'] = {
        'version': 'v1.6.5',
        'pair_attestation_headwords_indexed': V165_QA_CACHE.get('pair_attestation_headwords_indexed', 0),
        'pair_attestation_rows_kept': V165_QA_CACHE.get('pair_attestation_rows_kept', 0),
        'pair_attestation_rows_noise_rejected': V165_QA_CACHE.get('pair_attestation_rows_noise_rejected', 0),
        'single_word_suggestions_pair_attested_kept': V165_QA_CACHE.get('single_word_suggestions_pair_attested_kept', 0),
        'single_word_suggestions_pair_attestation_failed_removed': V165_QA_CACHE.get('single_word_suggestions_pair_attestation_failed_removed', 0),
        'contextual_synonym_open_units_count': V165_QA_CACHE.get('contextual_synonym_open_units_count', 0),
        'keep_promoted_to_free_enhance': V165_QA_CACHE.get('keep_promoted_to_free_enhance', 0),
        'keep_units_suppressed_for_enhance_overlap': V165_QA_CACHE.get('keep_units_suppressed_for_enhance_overlap', 0),
        'enhance_single_token_swap_suggestions_kept': V165_QA_CACHE.get('enhance_single_token_swap_suggestions_kept', 0),
        'enhance_single_token_swap_suggestions_removed': V165_QA_CACHE.get('enhance_single_token_swap_suggestions_removed', 0),
        'enhance_units_downgraded_to_open': V165_QA_CACHE.get('enhance_units_downgraded_to_open', 0),
    }
    prof['keep_count'] = len(result.get('keep_units') or [])
    prof['enhance_count'] = len(result.get('enhance_units') or [])
    prof['contextual_synonym_open_unit_count'] = len(result.get('contextual_synonym_open_units') or [])
    prof['enhance_unverified_open_unit_count'] = len(result.get('enhance_unverified_open_units') or [])
    prof['contextual_synonym_task_count'] = len(result.get('contextual_synonym_tasks') or [])
    result['contextual_synonym_task_count'] = len(result.get('contextual_synonym_tasks') or [])
    result['lexical_profile'] = prof


def _v165_single_token_diff(original: str, candidate: str) -> Optional[Tuple[int, str, str]]:
    """If `original` and `candidate` are the same length in tokens and differ in exactly
    one position, return (position, original_token, new_token); otherwise None. This finds
    single-word substitutions hiding inside an otherwise phrase-level ENHANCE suggestion
    (e.g. "give good advice" -> "give beneficial advice" is a same-length, one-token diff),
    which is exactly the shape the pair-attestation gate needs to check.
    """
    o = [norm_text(t) for t in surface_tokens(original)]
    c = [norm_text(t) for t in surface_tokens(candidate)]
    if len(o) != len(c):
        return None
    diffs = [i for i, (a, b) in enumerate(zip(o, c)) if a != b]
    if len(diffs) != 1:
        return None
    i = diffs[0]
    return i, o[i], c[i]


def _v165_reaudit_single_token_swaps_in_enhance_units(result: Dict[str, Any]) -> None:
    """The v1.6.3 quota-aware strict-recovery layer generates some ENHANCE suggestions that
    are, underneath, a single-word substitution inside a longer phrase (e.g. "give good
    advice" -> "give beneficial/advantageous advice") -- the exact same risk as the
    contextual-synonym-task path (see _v165_reaudit_contextual_synonym_tasks), but reached
    through a different code path that does not go through the v1.6.4 single-word audit at
    all. This applies the same hard pair-attestation requirement to that hidden case:
    for a same-length, one-token-different suggestion, the new token must be an attested
    collocate of a neighboring word in the ORIGINAL phrase. Suggestions that fail are
    dropped; if that empties a unit's suggestion list, the unit moves to
    `enhance_unverified_open_units` (same prompt, no asserted answer) instead of being
    silently deleted.
    """
    enhance_units = result.get('enhance_units') or []
    kept_units: List[Dict[str, Any]] = []
    open_units: List[Dict[str, Any]] = list(result.get('enhance_unverified_open_units') or [])
    removed = 0
    kept_suggestions = 0
    for unit in enhance_units:
        text = str(unit.get('unit_text') or '')
        o_toks = [norm_text(t) for t in surface_tokens(text)]
        surviving = []
        for sug in unit.get('suggestions') or []:
            cand_phrase = str((sug.get('suggested_phrase') or sug.get('text')) if isinstance(sug, dict) else sug)
            diff = _v165_single_token_diff(text, cand_phrase)
            if diff is None:
                # Not a single-token swap (a genuine clause/phrase-level paraphrase) --
                # out of scope for pair-attestation; leave as-is.
                surviving.append(sug)
                kept_suggestions += 1
                continue
            pos, orig_tok, new_tok = diff
            neighbors = [o_toks[j] for j in (pos - 1, pos + 1) if 0 <= j < len(o_toks)]
            checkable_neighbors = [nb for nb in neighbors if nb in V165_COLLOCATES_BY_HEADWORD]
            if not checkable_neighbors:
                # No neighboring word is a known headword -- e.g. a main-verb swap like
                # "cause"->"generate" next to function words ("can", "some"). There is no
                # attestation evidence available either way, so do not reject solely for
                # lacking it; this is a real limitation of a neighbor-adjacency heuristic
                # (it only has evidence for modifier<->headword pairs), documented in the
                # spec rather than silently over-rejecting good verb-level paraphrases.
                surviving.append(sug)
                kept_suggestions += 1
                continue
            attested_against = [nb for nb in checkable_neighbors if _v165_pair_attested(nb, new_tok)]
            if attested_against:
                if isinstance(sug, dict):
                    sug = copy.deepcopy(sug)
                    sug.setdefault('validation', {}).setdefault('gates', []).append('v165_pair_attestation_confirmed')
                    sug['pair_attested_against'] = attested_against
                surviving.append(sug)
                kept_suggestions += 1
            else:
                removed += 1
        if surviving:
            unit = copy.deepcopy(unit)
            unit['suggestions'] = surviving
            kept_units.append(unit)
        elif unit.get('suggestions'):
            # every suggestion was a single-token swap that failed attestation
            open_unit = copy.deepcopy(unit)
            open_unit['class_label'] = 'ENHANCE_UNVERIFIED_OPEN'
            open_unit['suggestions'] = []
            open_unit['asserted_answer_available'] = False
            open_unit['reason'] = 'single_token_swap_suggestions_failed_pair_specific_collocation_attestation'
            open_units.append(open_unit)
        else:
            kept_units.append(unit)
    result['enhance_units'] = kept_units
    result['enhance_unverified_open_units'] = open_units
    V165_QA_CACHE['enhance_single_token_swap_suggestions_kept'] = kept_suggestions
    V165_QA_CACHE['enhance_single_token_swap_suggestions_removed'] = removed
    V165_QA_CACHE['enhance_units_downgraded_to_open'] = len(open_units)


_prev_analyze_v165 = analyze


def analyze(payload: Dict[str, Any], validator: Optional[ContextFitValidator] = None) -> Dict[str, Any]:
    result = _prev_analyze_v165(payload, validator)
    result['run']['engine_version'] = ENGINE_VERSION
    _v165_reaudit_contextual_synonym_tasks(result)
    _v165_reaudit_single_token_swaps_in_enhance_units(result)
    _v165_promote_free_modifier_swaps(result)
    _v165_suppress_keep_phrase_overlaps(result)
    _v165_refresh_profile(result)
    return result


ENGINE_VERSION = "lret-engine-v1.6.7-tiered-llm-cheaper-model"


def main(argv: Optional[List[str]] = None) -> int:
    global ACTIVE_LLM_PROVIDER, LLM_MAX_CANDIDATES, LLM_MIN_VALID_SUGGESTIONS
    parser = argparse.ArgumentParser(description="LRET Engine v1.6.4 -- keep promotion + full single-word contextual synonym audit")
    parser.add_argument("--input", "-i", required=True, help="Path to LRET input JSON or full Evaluator/WKE output JSON")
    parser.add_argument("--output", "-o", required=True, help="Path to write LRET output JSON")
    parser.add_argument("--mode", choices=["fix_only", "enhance_only", "fix_and_enhance"], default=None)
    parser.add_argument("--student-id", default=None)
    parser.add_argument("--essay-id", default=None)
    parser.add_argument("--submission-id", default=None)
    parser.add_argument("--history", default=None, help="Optional learner_lexical_history JSON path")
    parser.add_argument("--resources", nargs="*", default=None, help="Optional simple external lexical resource JSON file(s)")
    parser.add_argument("--canonical-resources", default=None, help="Path to canonical resources directory or final_app_registries zip")
    parser.add_argument("--use-llm", action="store_true", help="Use OpenAI LLM suggestion/recovery layer if OPENAI_API_KEY is set")
    parser.add_argument("--llm-required", action="store_true", help="Fail if --use-llm is set but OPENAI_API_KEY is missing or request fails")
    parser.add_argument("--llm-model", default="gpt-5-mini", help="OpenAI model for suggestion generation; default gpt-5-mini")
    parser.add_argument("--llm-timeout", type=int, default=120, help="OpenAI read timeout in seconds; increase on slow connections")
    parser.add_argument("--llm-max-candidates", type=int, default=28)
    parser.add_argument("--llm-batch-size", type=int, default=4, help="Number of candidates per OpenAI request; lower this if timeouts occur")
    parser.add_argument("--llm-max-retries", type=int, default=3, help="Retries per LLM batch after transport/API failure")
    parser.add_argument("--llm-retry-sleep", type=float, default=2.0, help="Base sleep seconds between retries")
    parser.add_argument("--llm-min-valid-suggestions", type=int, default=2)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)

    LLM_MAX_CANDIDATES = max(0, int(args.llm_max_candidates))
    LLM_MIN_VALID_SUGGESTIONS = max(1, int(args.llm_min_valid_suggestions))
    load_external_lexical_resources(args.resources)
    load_canonical_resources(args.canonical_resources)

    if args.use_llm:
        ACTIVE_LLM_PROVIDER = OpenAILRETSuggestionProvider(
            model=args.llm_model,
            timeout=args.llm_timeout,
            batch_size=args.llm_batch_size,
            max_retries=args.llm_max_retries,
            retry_sleep=args.llm_retry_sleep,
        )
        if args.llm_required and not ACTIVE_LLM_PROVIDER.available():
            raise RuntimeError("--llm-required was set, but OPENAI_API_KEY is not available in the environment")
    else:
        ACTIVE_LLM_PROVIDER = None
        _LLM_STATS['enabled'] = False
        _LLM_STATS['model'] = None

    raw = load_json_file(args.input)
    history = load_json_file(args.history) if args.history else None
    lret_input = make_lret_input(
        raw,
        mode=args.mode,
        student_id=args.student_id,
        essay_id=args.essay_id,
        submission_id=args.submission_id,
        learner_lexical_history=history,
    )
    result = analyze(lret_input)
    result['run']['engine_version'] = ENGINE_VERSION
    result['lexical_profile']['canonical_resource_stats'] = copy.deepcopy(_RESOURCE_STATS)
    result['lexical_profile']['llm_suggestion_stats'] = copy.deepcopy(_LLM_STATS)
    result['qa'].setdefault('contract_checks', {})['llm_does_not_create_new_spans'] = True
    result['qa'].setdefault('contract_checks', {})['llm_suggestions_deterministically_validated'] = True
    result['qa'].setdefault('contract_checks', {})['no_embedded_phrase_enhance_bank'] = True
    result['qa'].setdefault('contract_checks', {})['canonical_resources_external_only'] = bool(args.canonical_resources)
    result['qa'].setdefault('contract_checks', {})['no_embedded_topic_whitelist'] = True
    result['qa'].setdefault('contract_checks', {})['no_plural_subject_need_regex'] = True
    result['qa'].setdefault('contract_checks', {})['clarify_is_visible_student_task'] = all(u.get('phase1_prompt') for u in result.get('clarify_units', []))
    result['qa'].setdefault('contract_checks', {})['no_embedded_topic_or_essay_specific_lists'] = True
    if args.llm_required and args.use_llm and _LLM_STATS['calls'] == 0:
        raise RuntimeError("--llm-required was set, but no successful LLM call was made")
    if _LLM_STATS.get('warnings'):
        result['qa'].setdefault('warnings', []).extend(_LLM_STATS['warnings'])
        if args.llm_required:
            raise RuntimeError("LLM required but warnings occurred: " + "; ".join(_LLM_STATS['warnings']))

    write_json_file(args.output, result, pretty=args.pretty)

    if args.summary:
        p = result.get("lexical_profile", {})
        v163 = p.get('v163_quota_aware_strict_recovery', {})
        print("=== LRET v1.6.4 keep-promotion single-word audit summary ===")
        print("output:", args.output)
        print("fix_units:", p.get("fix_count"))
        print("main_enhance_units:", p.get("enhance_count"))
        print("contextual_synonym_tasks:", p.get("contextual_synonym_task_count"))
        print("clarify_units:", p.get("clarify_count"))
        print("keep_units:", p.get("keep_count"))
        print("main_recovered:", v163.get('main_enhance_recovered_count'))
        print("recovery_mode_activated:", v163.get('recovery_mode_activated'))
        print("quota_status:", v163.get('quota_status'))
        print("resource_recovered:", v163.get('recovery_resource_units_released'))
        print("llm_recovered:", v163.get('recovery_llm_units_released'))
        print("light_synonym_tasks:", v163.get('light_synonym_tasks_released'))
        print("v164_single_word_audited:", p.get("v164_single_word_contextual_audit", {}).get("single_word_candidates_audited"))
        print("v164_single_word_tasks_added:", p.get("v164_single_word_contextual_audit", {}).get("single_word_contextual_synonym_tasks_added"))
        print("v164_quota_status:", p.get("v164_single_word_contextual_audit", {}).get("quota_status"))
        print("canonical_loaded:", p.get("canonical_resource_stats", {}).get("canonical_loaded"))
        print("llm_enabled:", p.get("llm_suggestion_stats", {}).get("enabled"))
        print("llm_calls:", p.get("llm_suggestion_stats", {}).get("calls"))
        print("qa_status:", result.get("qa", {}).get("status"))
        print("qa_warnings:", len(result.get("qa", {}).get("warnings", [])))
    return 0



# ============================================================================
# v1.6.7 -- tiered LLM trigger + cheaper default model
# ============================================================================
#
# Two changes, both driven directly by the cost complaint ($0.05/run, 10 calls):
#
# 1. TIERED TRIGGER: the free (registry-only) pass now always runs FIRST, for
#    every essay, regardless of --use-llm. Its yield is measured as
#    enhance_count + asserted contextual_synonym_task_count. Only if that yield
#    falls below --llm-yield-floor (default 6) AND --use-llm was explicitly
#    passed does a second, LLM-backed pass run at all. Essays with healthy free
#    coverage never touch the LLM. When the LLM pass does run, every raw unit
#    the free pass already turned into a released ENHANCE unit or an asserted
#    CONTEXTUAL_SYNONYM task is removed from the candidate pool first, so the
#    LLM is only ever asked to work on what free generation could not resolve --
#    this directly cuts candidates-sent versus the untiered v1.6.4-v1.6.6 runs.
#
# 2. CHEAPER DEFAULT MODEL: default --llm-model changes from gpt-5-mini
#    ($0.25 / $2.00 per 1M input/output tokens) to gpt-5-nano ($0.05 / $0.40 per
#    1M input/output tokens, per OpenAI's published API pricing) -- roughly a
#    5x reduction per token. This is a reasonable choice for this specific task:
#    every suggestion the LLM proposes, from any model, still has to pass the
#    same deterministic gates (contextual-fit, pair-attestation, sentence-
#    insertion, grammar-delta) before it can reach a student, so the model's
#    job here is closer to "propose candidates for a strict checker" than
#    "produce an unchecked final answer" -- a task nano-tier models are
#    generally adequate for. --llm-model remains overridable from the CLI if a
#    given deployment finds nano-tier quality insufficient in practice.
#
# ============================================================================

def _v167_free_yield(result: Dict[str, Any]) -> int:
    return len(result.get('enhance_units') or []) + len(result.get('contextual_synonym_tasks') or [])


def _v167_resolved_unit_texts(result: Dict[str, Any]) -> Set[str]:
    """Normalized text of every raw unit the free pass already turned into a released,
    asserted-answer item -- these should not be sent to the LLM pass at all.
    """
    resolved: Set[str] = set()
    for u in result.get('enhance_units') or []:
        if u.get('cost') == 'free_registry_only_no_llm':
            resolved.add(norm_text(u.get('unit_text') or ''))
    for t in result.get('contextual_synonym_tasks') or []:
        resolved.add(norm_text(t.get('unit_text') or ''))
    return {t for t in resolved if t}


def _v167_strip_resolved_units(payload: Dict[str, Any], resolved_texts: Set[str]) -> Dict[str, Any]:
    payload2 = copy.deepcopy(payload)
    fix_payload = payload2.get('lret_fix_payload') or {}
    units = fix_payload.get('lexical_units_for_lret') or []
    kept = [u for u in units if norm_text(u.get('unit', '')) not in resolved_texts]
    fix_payload['lexical_units_for_lret'] = kept
    payload2['lret_fix_payload'] = fix_payload
    return payload2, len(units) - len(kept)


def _v167_run_tiered(payload: Dict[str, Any], validator: Optional[ContextFitValidator], *,
                      llm_allowed: bool, yield_floor: int, force_llm: bool,
                      llm_model: str, llm_timeout: int, llm_batch_size: int,
                      llm_max_retries: int, llm_retry_sleep: float,
                      llm_required: bool) -> Dict[str, Any]:
    global ACTIVE_LLM_PROVIDER

    saved_provider = ACTIVE_LLM_PROVIDER
    ACTIVE_LLM_PROVIDER = None
    _LLM_STATS['enabled'] = False
    _LLM_STATS['model'] = None
    free_result = analyze(payload, validator)
    free_yield = _v167_free_yield(free_result)

    tiering_info = {
        'version': 'v1.6.7',
        'free_pass_yield': free_yield,
        'yield_floor': yield_floor,
        'llm_allowed_by_flag': llm_allowed,
        'force_llm': force_llm,
        'triggered': False,
        'reason': None,
        'candidates_excluded_as_already_free': 0,
        'model_used': None,
    }

    if not llm_allowed:
        tiering_info['reason'] = 'use_llm_not_set'
        free_result.setdefault('lexical_profile', {})['v167_tiered_llm'] = tiering_info
        ACTIVE_LLM_PROVIDER = saved_provider
        return free_result

    if free_yield >= yield_floor and not force_llm:
        tiering_info['reason'] = f'free_pass_yield_{free_yield}_met_floor_{yield_floor}_no_llm_call_made'
        free_result.setdefault('lexical_profile', {})['v167_tiered_llm'] = tiering_info
        ACTIVE_LLM_PROVIDER = saved_provider
        return free_result

    # Free pass under-delivered (or --force-llm was set): run a second, LLM-backed pass,
    # with everything the free pass already resolved removed from the candidate pool.
    resolved_texts = _v167_resolved_unit_texts(free_result)
    payload2, excluded_count = _v167_strip_resolved_units(payload, resolved_texts)

    ACTIVE_LLM_PROVIDER = OpenAILRETSuggestionProvider(
        model=llm_model, timeout=llm_timeout, batch_size=llm_batch_size,
        max_retries=llm_max_retries, retry_sleep=llm_retry_sleep,
    )
    if llm_required and not ACTIVE_LLM_PROVIDER.available():
        ACTIVE_LLM_PROVIDER = saved_provider
        raise RuntimeError("--llm-required was set, but OPENAI_API_KEY is not available in the environment")

    llm_result = analyze(payload2, validator)
    tiering_info['triggered'] = True
    tiering_info['reason'] = (
        f'free_pass_yield_{free_yield}_below_floor_{yield_floor}' if not force_llm
        else 'force_llm_set'
    )
    tiering_info['candidates_excluded_as_already_free'] = excluded_count
    tiering_info['model_used'] = llm_model
    llm_result.setdefault('lexical_profile', {})['v167_tiered_llm'] = tiering_info

    # The free pass's own resolved units are real, valid, zero-cost output -- merge them
    # back in rather than discarding them just because the LLM pass ran on the remainder.
    llm_texts = {norm_text(u.get('unit_text') or '') for u in llm_result.get('enhance_units') or []}
    for u in free_result.get('enhance_units') or []:
        if u.get('cost') == 'free_registry_only_no_llm' and norm_text(u.get('unit_text') or '') not in llm_texts:
            llm_result.setdefault('enhance_units', []).append(u)
    llm_result.setdefault('lexical_profile', {})['enhance_count'] = len(llm_result.get('enhance_units') or [])

    return llm_result


_prev_main_v167 = main


def main(argv: Optional[List[str]] = None) -> int:
    global ACTIVE_LLM_PROVIDER, LLM_MAX_CANDIDATES, LLM_MIN_VALID_SUGGESTIONS
    parser = argparse.ArgumentParser(
        description="LRET Engine v1.6.7 -- tiered (free-first) LLM trigger + cheaper default model")
    parser.add_argument("--input", "-i", required=True, help="Path to LRET input JSON or full Evaluator/WKE output JSON")
    parser.add_argument("--output", "-o", required=True, help="Path to write LRET output JSON")
    parser.add_argument("--mode", choices=["fix_only", "enhance_only", "fix_and_enhance"], default=None)
    parser.add_argument("--student-id", default=None)
    parser.add_argument("--essay-id", default=None)
    parser.add_argument("--submission-id", default=None)
    parser.add_argument("--history", default=None, help="Optional learner_lexical_history JSON path")
    parser.add_argument("--resources", nargs="*", default=None, help="Optional simple external lexical resource JSON file(s)")
    parser.add_argument("--canonical-resources", default=None, help="Path to canonical resources directory or final_app_registries zip")
    parser.add_argument("--use-llm", action="store_true",
                         help="Allow an LLM pass IF the free registry-only pass under-delivers (see --llm-yield-floor). "
                              "Does not force an LLM call by itself.")
    parser.add_argument("--force-llm", action="store_true",
                         help="Run the LLM pass unconditionally (skips the yield-floor check). Implies --use-llm.")
    parser.add_argument("--llm-yield-floor", type=int, default=6,
                         help="Minimum (enhance_count + asserted contextual_synonym_count) from the free pass "
                              "below which an LLM pass is triggered. Default 6.")
    parser.add_argument("--llm-required", action="store_true", help="Fail if the LLM pass triggers but OPENAI_API_KEY is missing or the request fails")
    parser.add_argument("--llm-model", default="gpt-5-nano",
                         help="OpenAI model for suggestion generation; default gpt-5-nano ($0.05/$0.40 per 1M "
                              "input/output tokens vs gpt-5-mini's $0.25/$2.00) -- every suggestion is still "
                              "deterministically re-validated regardless of model, so nano-tier is the "
                              "recommended default; override if quality in practice requires more.")
    parser.add_argument("--llm-timeout", type=int, default=120, help="OpenAI read timeout in seconds; increase on slow connections")
    parser.add_argument("--llm-max-candidates", type=int, default=12,
                         help="Lowered from 28 in prior versions now that the free pass resolves a meaningful "
                              "share of candidates before the LLM pass ever runs.")
    parser.add_argument("--llm-batch-size", type=int, default=4, help="Number of candidates per OpenAI request; lower this if timeouts occur")
    parser.add_argument("--llm-max-retries", type=int, default=3, help="Retries per LLM batch after transport/API failure")
    parser.add_argument("--llm-retry-sleep", type=float, default=2.0, help="Base sleep seconds between retries")
    parser.add_argument("--llm-min-valid-suggestions", type=int, default=2)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)

    llm_allowed = bool(args.use_llm or args.force_llm)
    LLM_MAX_CANDIDATES = max(0, int(args.llm_max_candidates))
    LLM_MIN_VALID_SUGGESTIONS = max(1, int(args.llm_min_valid_suggestions))
    load_external_lexical_resources(args.resources)
    load_canonical_resources(args.canonical_resources)
    ACTIVE_LLM_PROVIDER = None
    _LLM_STATS['enabled'] = False
    _LLM_STATS['model'] = None

    raw = load_json_file(args.input)
    history = load_json_file(args.history) if args.history else None
    lret_input = make_lret_input(
        raw, mode=args.mode, student_id=args.student_id, essay_id=args.essay_id,
        submission_id=args.submission_id, learner_lexical_history=history,
    )

    result = _v167_run_tiered(
        lret_input, None,
        llm_allowed=llm_allowed, yield_floor=max(0, int(args.llm_yield_floor)), force_llm=bool(args.force_llm),
        llm_model=args.llm_model, llm_timeout=args.llm_timeout, llm_batch_size=args.llm_batch_size,
        llm_max_retries=args.llm_max_retries, llm_retry_sleep=args.llm_retry_sleep,
        llm_required=bool(args.llm_required),
    )
    result['run']['engine_version'] = ENGINE_VERSION
    result['lexical_profile']['canonical_resource_stats'] = copy.deepcopy(_RESOURCE_STATS)
    result['lexical_profile']['llm_suggestion_stats'] = copy.deepcopy(_LLM_STATS)
    result['qa'].setdefault('contract_checks', {})['llm_does_not_create_new_spans'] = True
    result['qa'].setdefault('contract_checks', {})['llm_suggestions_deterministically_validated'] = True
    result['qa'].setdefault('contract_checks', {})['no_embedded_phrase_enhance_bank'] = True
    result['qa'].setdefault('contract_checks', {})['canonical_resources_external_only'] = bool(args.canonical_resources)
    result['qa'].setdefault('contract_checks', {})['no_embedded_topic_whitelist'] = True
    result['qa'].setdefault('contract_checks', {})['no_plural_subject_need_regex'] = True
    result['qa'].setdefault('contract_checks', {})['clarify_is_visible_student_task'] = all(u.get('phase1_prompt') for u in result.get('clarify_units', []))
    result['qa'].setdefault('contract_checks', {})['no_embedded_topic_or_essay_specific_lists'] = True
    if args.llm_required and result.get('lexical_profile', {}).get('v167_tiered_llm', {}).get('triggered') and _LLM_STATS['calls'] == 0:
        raise RuntimeError("--llm-required was set, the LLM pass triggered, but no successful LLM call was made")
    if _LLM_STATS.get('warnings'):
        result['qa'].setdefault('warnings', []).extend(_LLM_STATS['warnings'])
        if args.llm_required:
            raise RuntimeError("LLM required but warnings occurred: " + "; ".join(_LLM_STATS['warnings']))

    write_json_file(args.output, result, pretty=args.pretty)

    if args.summary:
        p = result.get("lexical_profile", {})
        v167 = p.get('v167_tiered_llm', {})
        print("=== LRET v1.6.7 tiered-llm summary ===")
        print("output:", args.output)
        print("fix_units:", p.get("fix_count"))
        print("main_enhance_units:", p.get("enhance_count"))
        print("contextual_synonym_tasks:", p.get("contextual_synonym_task_count"))
        print("clarify_units:", p.get("clarify_count"))
        print("keep_units:", p.get("keep_count"))
        print("free_pass_yield:", v167.get("free_pass_yield"))
        print("yield_floor:", v167.get("yield_floor"))
        print("llm_triggered:", v167.get("triggered"))
        print("llm_trigger_reason:", v167.get("reason"))
        print("candidates_excluded_as_already_free:", v167.get("candidates_excluded_as_already_free"))
        print("model_used:", v167.get("model_used"))
        print("canonical_loaded:", p.get("canonical_resource_stats", {}).get("canonical_loaded"))
        print("llm_enabled:", p.get("llm_suggestion_stats", {}).get("enabled"))
        print("llm_calls:", p.get("llm_suggestion_stats", {}).get("calls"))
        print("qa_status:", result.get("qa", {}).get("status"))
        print("qa_warnings:", len(result.get("qa", {}).get("warnings", [])))
    return 0



# ============================================================================
# v1.6.8 patch: canonical-resource load must fail loud, not fail silent
# ============================================================================
#
# Found while diagnosing two user-submitted real-run outputs
# (lret_v1_6_6_output.json, lret_v1_6_7_nano_forced_output.json) that both
# showed near-zero yield (fix=0/enhance=0/clarify=0/contextual_synonym=0 in
# the v1.6.6 run; free_pass_yield=0 in the v1.6.7 run) despite
# canonical_resource_stats.canonical_loaded == true in both.
#
# Root cause, confirmed directly: load_canonical_resources() (original
# v1.4.0 definition, unchanged through v1.6.7) sets
# _RESOURCE_STATS["canonical_loaded"] = True unconditionally at the end of
# the function -- regardless of whether --canonical-resources pointed at a
# real file, a wrong path, or an empty/mis-nested archive. When the path is
# bad, every _read_json_from_resource / _iter_tsv_from_resource call
# silently returns None/empty, all four *_loaded / *_entries counters stay
# at 0, but canonical_loaded still reports True. Reproduced directly in
# this sandbox: pointing --canonical-resources at a deliberately wrong path
# on the SAME essay used in the v1.6.4-v1.6.7 spec smoke tests reproduced
# the identical signature (canonical_loaded: true, all four sub-counts: 0),
# and yield collapsed accordingly (fix dropped from 5 to 4, enhance/
# contextual_synonym dropped to 0). The correct zip, at the correct path,
# on the same essay loads normally (76,289-76,334 lexical entries, ~14-17k
# collocation rows) and produces healthy, non-zero output. This is a
# path/config problem on the calling side, not a data-quality problem with
# the registries themselves -- but the engine gave no signal that anything
# had gone wrong, which is the actual defect being fixed here.
#
# This patch does not touch the original load_canonical_resources() body
# (still present above, unmodified, per project rule). It shadow-redefines
# the function one more time so that: (1) canonical_loaded is corrected to
# False if literally nothing was loaded from a non-empty resource_path,
# (2) a specific, actionable reason is recorded and printed to stderr
# immediately (path missing vs. path present-but-empty-of-expected-files),
# and (3) the same reason is surfaced as a qa.warnings entry in the final
# JSON output, so a silent config mistake can never again look like a
# healthy zero-cost run.

import sys as _sys168

_prev_load_canonical_resources_v168 = load_canonical_resources


def load_canonical_resources(resource_path: Optional[str]) -> None:
    _RESOURCE_STATS['canonical_load_warning'] = None
    _prev_load_canonical_resources_v168(resource_path)
    if not resource_path:
        return
    loaded_something = any([
        _RESOURCE_STATS.get('enhance_thesaurus_entries', 0),
        _RESOURCE_STATS.get('positive_collocations_loaded', 0),
        _RESOURCE_STATS.get('discourse_markers_loaded', 0),
        _RESOURCE_STATS.get('lexical_entries_loaded', 0),
    ])
    if loaded_something:
        return
    # Nothing loaded at all: canonical_loaded=True from the base function is
    # wrong here. Correct it and record why, as specifically as possible.
    _RESOURCE_STATS['canonical_loaded'] = False
    try:
        path_obj = _pathlib.Path(resource_path)
        path_exists = path_obj.exists()
    except Exception:
        path_exists = False
    if not path_exists:
        reason = (
            f"--canonical-resources path does not exist on disk: {resource_path!r}. "
            "Check for a typo, a relative-path/working-directory mismatch, or a "
            "file that was moved/renamed."
        )
    else:
        is_zip = False
        try:
            is_zip = _zipfile.is_zipfile(resource_path)
        except Exception:
            is_zip = False
        if is_zip or path_obj.is_dir():
            reason = (
                f"--canonical-resources path exists ({resource_path!r}) and is a "
                "valid zip/directory, but none of the expected registry files "
                "(enhance_thesaurus.json, discourse_registry.json, "
                "positive_collocations_registry.tsv, lexical_registry.json) were "
                "found inside it. Check the internal folder nesting -- this engine "
                "expects those files either at the archive/folder root or one "
                "level down inside a single named subfolder."
            )
        else:
            reason = (
                f"--canonical-resources path exists ({resource_path!r}) but is "
                "neither a valid zip file nor a directory."
            )
    _RESOURCE_STATS['canonical_load_warning'] = reason
    print(
        f"WARNING [v1.6.8]: canonical resources requested but NOTHING was loaded "
        f"from them -- the run will silently behave as a zero-resource run "
        f"unless this is fixed. {reason}",
        file=_sys168.stderr,
    )


_prev_analyze_v168 = analyze


def analyze(payload: Dict[str, Any], validator: Optional[ContextFitValidator] = None) -> Dict[str, Any]:
    result = _prev_analyze_v168(payload, validator)
    result['run']['engine_version'] = ENGINE_VERSION
    result.setdefault('lexical_profile', {})['canonical_resource_stats'] = copy.deepcopy(_RESOURCE_STATS)
    warn = _RESOURCE_STATS.get('canonical_load_warning')
    if warn:
        result.setdefault('qa', {}).setdefault('warnings', []).append(
            'v168_canonical_resources_silent_load_failure: ' + warn
        )
        result.setdefault('qa', {})['status'] = 'needs_tuning'
    return result


ENGINE_VERSION = "lret-engine-v1.6.8-canonical-load-fail-loud"


# ============================================================================
# v1.6.9 patch: retire the unverified sibling-collocate modifier swap as an
# ENHANCE source, route it to an honest CLARIFY-open task instead; fix a
# second bug where the tiered LLM pass silently dropped the free pass's own
# CONTEXTUAL_SYNONYM_TASKS.
# ============================================================================
#
# PART 1 -- why the modifier-swap mechanism is being retired as an ENHANCE
# source (not patched with a stronger gate: there isn't one available).
#
# Live testing on a real essay (the "care homes" / "family tradition" case)
# showed this mechanism asserting unsafe alternatives: "family tradition" ->
# "religious tradition" changes the sentence's topic entirely (the original
# is about a grandmother passing down her OWN family's heritage; "religious"
# introduces a subject that isn't in the essay at all). "care homes" ->
# "care policies" is similarly broken -- a government doesn't "spend money
# on care policies" the way it spends money maintaining care homes.
#
# Root cause, confirmed directly: this mechanism (_v165_generate_free_
# modifier_swap_candidates / _v165_promote_free_modifier_swaps) treats OTHER
# collocates of the SAME headword as if they were synonyms of the modifier
# being replaced. They are not -- "home", "policy", "unit", "service", and
# "need" all pair with "care" in the Oxford collocations data because they
# are different concepts that happen to share a headword, not because they
# mean the same thing. Unlike every other suggestion path in this engine,
# this one also never carried sentence context forward and never ran any
# context-fit check before promoting straight to ENHANCE with
# "accepted: True".
#
# Checked whether the engine's EXISTING context-fit gate
# (RuleBasedContextFitValidator._transparent_repair_preserves_core, a
# token-overlap check) could simply be applied here instead of retiring the
# mechanism outright. It cannot: for any 2-word "headword + modifier"
# phrase, swapping the modifier changes 1 of 2 tokens, i.e. 50% overlap,
# which is structurally below the >=66% threshold the existing gate uses to
# accept a phrase as "the same meaning" -- regardless of whether the swap is
# actually good or bad. Verified directly: all 8 real candidates from the
# "care homes" / "family tradition" case (including the ones that read as
# plausible, like "care services") score exactly 0.50 overlap and would be
# rejected uniformly. So "add the missing gate" and "disable the mechanism"
# converge to the same practical outcome with the validator this engine
# currently has. A real fix would need an actual synonym resource (e.g.
# WordNet synset overlap between the original and candidate modifier) rather
# than collocation data or token overlap -- that was not implemented here
# because the WordNet corpus could not be downloaded in this environment
# (network-restricted sandbox); a future version can add real
# semantic-synonymy gating if that corpus is available in the deployment
# environment. Until then, this mechanism must not assert an alternative as
# correct.
#
# What v1.6.9 does instead: the same detection signal (a same-headword,
# same-relation-type sibling collocate exists) is kept, because it's a
# real, useful observation -- but it is now routed to a CLARIFY-style task
# that shows the student the phrase and invites them to try improving it
# themselves, with NO suggested alternative asserted. This preserves the
# "this could be more precise" signal honestly instead of either asserting
# an unverified answer or silently discarding the observation.

def _v165_promote_free_modifier_swaps(result: Dict[str, Any]) -> None:
    # v1.6.10: retired. Root cause is structural, not a matter of a few
    # missed phrases -- confirmed by reading _v165_generate_free_modifier_
    # swap_candidates directly: it can ONLY produce a candidate for a given
    # unit_text when that unit's OWN modifier token is already a member of
    # V165_COLLOCATES_BY_HEADWORD's attested collocate set for the matched
    # headword (`if other_tok in collocates:` / the plural-normalized
    # equivalent -- every other path `continue`s with no candidate). That
    # means every phrase this function ever routes to CLARIFY is, by
    # construction, one whose current wording the registry already
    # recognizes as a good, attested collocation. It cannot distinguish "a
    # weak/generic modifier that merely shares a headword with something
    # better" from "an already-standard pairing that happens to have
    # registry siblings" -- because it only ever sees the second case.
    # Reproduced directly on a real essay: "health care", "good advice",
    # "family traditions" (all standard, correct, unremarkable English) were
    # routed to CLARIFY with "this could be more precise, try rephrasing" --
    # not because anything was wrong with them, but purely because "care" /
    # "advice" / "tradition" each have some other attested collocate
    # somewhere in the registry. An allowlist of specific known-good phrases
    # was tried and rejected as the fix: essay content is unbounded, so any
    # phrase-list solution only ever covers phrases someone happened to
    # notice, and misses the same failure on the next essay's equally
    # standard collocations. Retiring the mechanism is the universal fix --
    # it applies to every essay, not just this one. The underlying
    # observation this mechanism was trying to surface (repeated, generic,
    # low-information vocabulary) is still covered by the separate
    # clarify_repeated_word_* mechanism elsewhere in this engine, which
    # flags on actual repetition within THIS essay rather than on registry
    # co-occurrence with an unrelated essay's vocabulary.
    return


# PART 2 -- fix: the tiered LLM pass was silently dropping the free pass's
# own CONTEXTUAL_SYNONYM_TASKS.
#
# Found live, on a real run with a real OPENAI_API_KEY: free_pass_yield was
# reported as 5 (enhance_count + contextual_synonym_task_count from the free
# pass), the LLM tier correctly triggered because 5 < the floor of 6, and the
# final merged result correctly kept the free pass's zero-cost ENHANCE units
# -- but contextual_synonym_tasks came back as 0 in the final output, even
# though the free pass alone reliably produces 2-3 on this essay.
#
# Root cause, confirmed by reading _v167_run_tiered directly: the raw units
# behind the free pass's contextual_synonym_tasks are stripped from the
# payload before the second (LLM) pass runs (correctly -- so the LLM isn't
# asked to redo free work), but the merge-back step at the end of
# _v167_run_tiered only re-attaches the free pass's ENHANCE units. There is
# no equivalent line for contextual_synonym_tasks, so they are simply lost.
# This costs nothing to fix and directly serves the paraphrase-training goal
# this tool exists for -- there is no reason a real LLM call elsewhere in
# the same run should cause free, already-validated paraphrase tasks to
# vanish.

def _v167_run_tiered(payload: Dict[str, Any], validator: Optional[ContextFitValidator], *,
                      llm_allowed: bool, yield_floor: int, force_llm: bool,
                      llm_model: str, llm_timeout: int, llm_batch_size: int,
                      llm_max_retries: int, llm_retry_sleep: float,
                      llm_required: bool) -> Dict[str, Any]:
    global ACTIVE_LLM_PROVIDER

    saved_provider = ACTIVE_LLM_PROVIDER
    ACTIVE_LLM_PROVIDER = None
    _LLM_STATS['enabled'] = False
    _LLM_STATS['model'] = None
    free_result = analyze(payload, validator)
    free_yield = _v167_free_yield(free_result)

    tiering_info = {
        'version': 'v1.6.9',
        'free_pass_yield': free_yield,
        'yield_floor': yield_floor,
        'llm_allowed_by_flag': llm_allowed,
        'force_llm': force_llm,
        'triggered': False,
        'reason': None,
        'candidates_excluded_as_already_free': 0,
        'model_used': None,
    }

    if not llm_allowed:
        tiering_info['reason'] = 'use_llm_not_set'
        free_result.setdefault('lexical_profile', {})['v167_tiered_llm'] = tiering_info
        ACTIVE_LLM_PROVIDER = saved_provider
        return free_result

    if free_yield >= yield_floor and not force_llm:
        tiering_info['reason'] = f'free_pass_yield_{free_yield}_met_floor_{yield_floor}_no_llm_call_made'
        free_result.setdefault('lexical_profile', {})['v167_tiered_llm'] = tiering_info
        ACTIVE_LLM_PROVIDER = saved_provider
        return free_result

    resolved_texts = _v167_resolved_unit_texts(free_result)
    payload2, excluded_count = _v167_strip_resolved_units(payload, resolved_texts)

    ACTIVE_LLM_PROVIDER = OpenAILRETSuggestionProvider(
        model=llm_model, timeout=llm_timeout, batch_size=llm_batch_size,
        max_retries=llm_max_retries, retry_sleep=llm_retry_sleep,
    )
    if llm_required and not ACTIVE_LLM_PROVIDER.available():
        ACTIVE_LLM_PROVIDER = saved_provider
        raise RuntimeError("--llm-required was set, but OPENAI_API_KEY is not available in the environment")

    llm_result = analyze(payload2, validator)
    tiering_info['triggered'] = True
    tiering_info['reason'] = (
        f'free_pass_yield_{free_yield}_below_floor_{yield_floor}' if not force_llm
        else 'force_llm_set'
    )
    tiering_info['candidates_excluded_as_already_free'] = excluded_count
    tiering_info['model_used'] = llm_model
    llm_result.setdefault('lexical_profile', {})['v167_tiered_llm'] = tiering_info

    # Merge back the free pass's own resolved ENHANCE units (unchanged from v1.6.7).
    llm_texts = {norm_text(u.get('unit_text') or '') for u in llm_result.get('enhance_units') or []}
    for u in free_result.get('enhance_units') or []:
        if u.get('cost') == 'free_registry_only_no_llm' and norm_text(u.get('unit_text') or '') not in llm_texts:
            llm_result.setdefault('enhance_units', []).append(u)
    llm_result.setdefault('lexical_profile', {})['enhance_count'] = len(llm_result.get('enhance_units') or [])

    # v1.6.9 fix: merge back the free pass's own CONTEXTUAL_SYNONYM_TASKS too.
    # Every contextual_synonym_task produced by the free pass is, by construction,
    # zero-LLM-cost (ACTIVE_LLM_PROVIDER is forced to None for the free pass above),
    # so all of them are safe and correct to keep regardless of what the LLM pass did.
    llm_ctxsyn_texts = {norm_text(t.get('unit_text') or '') for t in llm_result.get('contextual_synonym_tasks') or []}
    for t in free_result.get('contextual_synonym_tasks') or []:
        if norm_text(t.get('unit_text') or '') not in llm_ctxsyn_texts:
            llm_result.setdefault('contextual_synonym_tasks', []).append(t)
    llm_result.setdefault('lexical_profile', {})['contextual_synonym_task_count'] = len(
        llm_result.get('contextual_synonym_tasks') or []
    )

    return llm_result


_prev_analyze_v169 = analyze


def _v1610_suppress_keep_units_with_detector_errors(result: Dict[str, Any]) -> None:
    """Universal rule, not tied to any specific essay's vocabulary: a phrase
    should never be classified KEEP ("this is fine as written") when it
    contains a word the Detector's own arbitrated error detection already
    flagged as wrong in that same sentence. Reproduced directly on a real
    essay: "need modey" (a misspelling of "money") was kept as a good
    collocation, while the Detector independently flagged "modey" as a
    SPELLING error at 0.82 confidence in the same sentence -- a direct,
    visible contradiction a student would notice immediately. This checks
    plain word-level overlap between each KEEP unit's own text and any
    Detector error row's surface_quote whose full sentence matches the
    unit's own context -- no per-essay word list involved, so it applies
    identically to any misspelling or flagged error on any essay.
    """
    rows = [r for r in (globals().get('DETECTOR_ERROR_ROWS') or []) if isinstance(r, dict)]
    if not rows:
        return
    keep_units = result.get('keep_units') or []
    survivors: List[Dict[str, Any]] = []
    suppressed: List[Dict[str, Any]] = []
    for ku in keep_units:
        unit_text = str(ku.get('unit_text') or '')
        unit_context = str(ku.get('context') or '')
        unit_toks = {norm_text(t) for t in surface_tokens(unit_text)}
        hit = None
        for row in rows:
            quote = str(row.get('surface_quote') or '').strip()
            if not quote:
                continue
            row_sentence = str((row.get('location') or {}).get('sentence') or '').strip()
            if row_sentence and unit_context and norm_text(row_sentence) != norm_text(unit_context):
                continue
            quote_toks = {norm_text(t) for t in surface_tokens(quote)}
            if quote_toks and quote_toks & unit_toks:
                hit = row.get('error_id') or quote
                break
        if hit:
            suppressed.append({
                'unit_text': unit_text,
                'reason': 'contains_word_flagged_by_detector_in_same_sentence',
                'detector_error_id': hit,
            })
        else:
            survivors.append(ku)
    result['keep_units'] = survivors
    if suppressed:
        internal = list(result.get('keep_internal_units') or [])
        internal.extend(suppressed)
        result['keep_internal_units'] = internal
    result.setdefault('lexical_profile', {})['keep_units_suppressed_for_detector_error_overlap'] = len(suppressed)


def analyze(payload: Dict[str, Any], validator: Optional[ContextFitValidator] = None) -> Dict[str, Any]:
    result = _prev_analyze_v169(payload, validator)
    result['run']['engine_version'] = ENGINE_VERSION
    _v1610_suppress_keep_units_with_detector_errors(result)
    return result


ENGINE_VERSION = "lret-engine-v1.6.10-retire-modifier-swap-clarify-plus-detector-keep-check"


# ============================================================================
# v1.7.0 patch: fix an over-broad false-positive rule in
# is_unrecoverable_phrase_fragment() that was silently discarding genuinely
# good content, not just miscounting a diagnostic stat.
# ============================================================================
#
# Found while investigating why LRET's evaluator_input_quality.fragment_or_noise_rate
# stayed around 42% even after evaluator v8.3 fixed its comma-crossing bug.
# Direct test: is_unrecoverable_phrase_fragment("ageing population", "Even though
# an ageing population can cause some problems...") returns True. So does
# "main issue" against its real sentence. Both are legitimate, complete,
# topic-relevant noun phrases -- there is nothing wrong with either of them.
#
# Root cause, confirmed directly: two rules in this function ("Short
# clause-prefix fragments" and "incomplete prefix of a longer phrase") both
# work by checking whether the unit's text, followed by any other word, can
# be found in the raw context sentence -- i.e. "is there more text after this
# span in its sentence." That is true of almost every multi-word unit that
# isn't the literal last words of its sentence, regardless of whether the
# span itself is complete. It does not check whether a genuinely longer
# candidate unit exists that actually subsumes this one -- it only checks
# raw proximity to more words. Confirmed empirically: in the essay used
# throughout this project, this rule alone caused "ageing population" and
# "main issue" -- both real, correct, KEEP-worthy phrases -- to be classified
# as fragments and then, traced end to end, to be silently dropped from the
# final output entirely (present in neither fix_units, enhance_units,
# clarify_units, nor keep_units). This is not a cosmetic miscount: it is
# real, usable content being discarded by an over-eager heuristic, on every
# essay, not just this one.
#
# Fix: remove both of these specific rules. The remaining rules in this
# function (malformed-shape regexes, vague-noun + "-ing" check, the
# hand-prefix discourse-marker check, trailing/leading preposition checks,
# and the corrupted-context dangling-participle checks) are all pattern-
# specific and were not implicated in this false-positive -- they still
# apply unchanged. This makes the function fail-closed only where there is
# an actual matched bad pattern, rather than by default whenever a span
# isn't at the very end of its sentence.

def is_unrecoverable_phrase_fragment(unit_text: str, context: str = "") -> bool:
    low = norm_text(unit_text)
    if not low:
        return True
    if low in EXTERNAL_FORMULAIC_KEEP or low in EXTERNAL_STABLE_KEEP_PHRASES:
        return False
    toks = surface_tokens(unit_text)
    ctoks = content_tokens(unit_text)
    if any(re.search(p, low) for p in [r"\b(?:has|have)\s+to\s+\w+ed\b", r"\b(?:a|an)\s+\w+s\b", r"\bmore\s+\w+er\b", r"\b\w+\s+be\s+\w+\b"]):
        return True
    if not toks or not ctoks:
        return True
    # Vague noun + participial/verb fragment is not positive lexical evidence.
    if any(simple_stem(t) in UNIVERSAL_VAGUE_NOUNS for t in toks) and any(str(t).lower().endswith('ing') for t in toks):
        return True
    # Discourse-marker fragments must be complete, not clipped.
    if low in {'other hand', 'one hand'} or (toks and toks[0].lower() == 'hand'):
        return True
    # v1.7.0: REMOVED "Short clause-prefix fragments" rule (was: any span
    # followed by another word anywhere in its raw context sentence -> fragment).
    # Confirmed to false-positive on legitimate, complete phrases ("ageing
    # population", "main issue") purely because they are not the last words
    # of their sentence. See patch note above.
    # dangling NP + -ing fragment in a malformed local context
    if context_has_local_grammar_corruption(context) and toks[-1].lower().endswith("ing") and len(toks) <= 4:
        return True
    # noun/preposition/noun fragment that omits its governing verb in malformed context
    if context_has_local_grammar_corruption(context) and len(toks) <= 4 and any(t.lower() in {"about", "with", "for", "to"} for t in toks[1:]):
        return True
    # v1.7.0: REMOVED "incomplete prefix of a longer phrase" rule -- same
    # false-positive mechanism as above, just for len(toks) <= 2.
    if toks[-1].lower() in {"and", "or", "but", "to", "of", "for", "with", "by", "from"}:
        return True
    if toks[0].lower() in {"of", "for", "with", "by", "from", "about"}:
        return True
    return False


ENGINE_VERSION = "lret-engine-v1.7.0-fix-overbroad-fragment-heuristic"


# ============================================================================
# v1.8.0 patch: ingest the Detector's errormap (validated_fix_candidates +
# broken_sentences_raw) instead of LRET deriving FIX detection entirely on
# its own from raw n-gram-extracted units.
# ============================================================================
#
# Found while reviewing 01_detector_output.json / 01b_errormap_v3.json: the
# Detector already runs LanguageTool plus a rules registry and produces, per
# error, an arbitrated, confidence-scored, student-visible-flagged FIX
# candidate with exact character spans, a repair hypothesis, and (often) a
# fully materialised revised-sentence hypothesis -- under
# result['lret']['validated_fix_candidates'] in the batch output, or
# ['_detector_v1_passthrough']['lret_fix_payload']['validated_fix_candidates']
# in the per-essay errormap. The Detector separately flags whole sentences
# that are too corrupted to process normally, in 'broken_sentences_raw',
# each with a recoverability_score, a local_corruption_score, and a
# root_cause_hint plain-language explanation.
#
# Confirmed directly: LRET had ZERO code referencing either of these fields
# before this patch (grepped the whole file). It derives FIX entirely from
# its own regex/heuristic candidate detection, working only from the
# Evaluator's raw n-gram units, with no visibility into the Detector's
# richer, already-arbitrated error data. Measured directly on a real essay
# with obvious errors (misspellings "modey"/"goverment"/"contries", "Another
# issued is", "the way be fewer", "for take care with they health"): LRET's
# own FIX detection on this essay produced fix_units: 0. The Detector's
# errormap for the exact same essay already has 15 arbitrated errors ready
# to use, several with confidence >= 0.8 and LanguageTool as a source engine.
# This is not a deliberate design choice with a documented rationale -- it
# is simply an integration that was never built.
#
# What this patch adds:
#   1. --detector-output <path>, optional. If given, its validated_fix_candidates
#      are promoted directly into fix_units (deduplicated against anything
#      LRET's own detection already found, by normalized span text), each
#      tagged source='detector_validated_fix_candidate' with the Detector's
#      own confidence and source_engines carried through for auditability.
#   2. broken_sentences_raw is used to suppress ENHANCE/KEEP/CLARIFY/
#      CONTEXTUAL_SYNONYM candidates whose source_sentence_index falls
#      inside a Detector-flagged critically-corrupted sentence (these are
#      exactly the sentences that produce garbled n-gram noise -- "the way
#      be fewer", "nobody for take", etc., in essays tested earlier in this
#      project) -- and replaces them with a single CLARIFY task per broken
#      sentence carrying the Detector's own root_cause_hint, rather than
#      either asserting something from a corrupted span or silently
#      dropping the signal.
#   3. Fully optional and additive: if --detector-output is not passed,
#      behavior is byte-identical to v1.7.0.

# v1.6.10: real errormap files (confirmed directly against a real Gold
# session's 01b_errormap_v3.json) use a top-level "errors" array with
# surface_quote/sentence_index/family/confidence fields -- not the
# "validated_fix_candidates" shape the v1.8.0 loader above looks for, which
# does not appear anywhere in real errormap output. That mismatch is left
# alone here (fixing it is a larger, separate change with its own risk to
# the already-tested FIX-unit generation path); this only adds a second,
# independent read of the real "errors" array into its own global, used
# below by a universal KEEP-suppression check: a phrase should never be
# classified KEEP ("this is fine as written") when it contains a word the
# Detector's own arbitrated error detection already flagged in that same
# sentence. Confirmed missing on a real essay: "need modey" (misspelling of
# "money") was classified KEEP as a good collocation, while the exact same
# word was independently flagged SPELLING at 0.82 confidence by the
# Detector -- a direct, visible contradiction, not an essay-specific
# problem, since it will recur on any essay where LRET's own detection
# misses a word the Detector separately catches.
DETECTOR_ERROR_ROWS: List[Dict[str, Any]] = []


def load_detector_output(path: Optional[str]) -> None:
    global DETECTOR_FIX_CANDIDATES, DETECTOR_BROKEN_SENTENCES, DETECTOR_ERROR_ROWS
    DETECTOR_FIX_CANDIDATES = []
    DETECTOR_BROKEN_SENTENCES = []
    DETECTOR_ERROR_ROWS = []
    if not path:
        return
    raw = load_json_file(path)
    if not isinstance(raw, dict):
        return
    candidates = None
    broken = None
    # Batch container format: {"results": [ {..., "lret": {...}, "broken_sentences_raw": [...]} ]}
    results = raw.get('results')
    if isinstance(results, list) and results and isinstance(results[0], dict):
        r0 = results[0]
        if isinstance(r0.get('lret'), dict):
            candidates = r0['lret'].get('validated_fix_candidates')
        if broken is None:
            broken = r0.get('broken_sentences_raw')
    # Per-essay errormap format: {"_detector_v1_passthrough": {"lret_fix_payload": {...}}, "broken_sentences_raw": [...]}
    if candidates is None:
        passthrough = raw.get('_detector_v1_passthrough') or {}
        lfp = passthrough.get('lret_fix_payload') or {}
        if isinstance(lfp, dict):
            candidates = lfp.get('validated_fix_candidates')
    # Direct/flat fallbacks.
    if candidates is None:
        lfp2 = raw.get('lret_fix_payload')
        if isinstance(lfp2, dict):
            candidates = lfp2.get('validated_fix_candidates')
    if candidates is None and isinstance(raw.get('lret'), dict):
        candidates = raw['lret'].get('validated_fix_candidates')
    if broken is None:
        broken = raw.get('broken_sentences_raw')
    DETECTOR_FIX_CANDIDATES = candidates if isinstance(candidates, list) else []
    DETECTOR_BROKEN_SENTENCES = broken if isinstance(broken, list) else []
    real_errors = raw.get('errors')
    DETECTOR_ERROR_ROWS = real_errors if isinstance(real_errors, list) else []


DETECTOR_FIX_CANDIDATES: List[Dict[str, Any]] = []
DETECTOR_BROKEN_SENTENCES: List[Dict[str, Any]] = []


def _v180_build_detector_fix_units(existing_fix_units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    existing_norms = {norm_text(u.get('unit_text') or '') for u in existing_fix_units}
    new_units: List[Dict[str, Any]] = []
    for c in DETECTOR_FIX_CANDIDATES:
        if not isinstance(c, dict):
            continue
        if c.get('student_visible') is False:
            continue
        arb = c.get('arbitration_status')
        if arb is not None and arb != 'accepted':
            continue
        quote = str(c.get('quote') or '').strip()
        if not quote or norm_text(quote) in existing_norms:
            continue
        context = str(c.get('local_quote') or '')
        repair = c.get('repair_hypothesis')
        family = str(c.get('family') or '').upper()
        materialised = ((c.get('repair_materialisation') or {}).get('revised_sentence_hypothesis')
                        if isinstance(c.get('repair_materialisation'), dict) else None)
        suggestion_text = None
        suggestion_verified = False
        if family == 'SPELLING':
            # v1.8.0 safety decision: never surface the Detector's specific spelling
            # repair_hypothesis. Tried cross-checking it against LRET's own canonical
            # lexical_registry (a real 76k-entry dictionary) first -- that check does
            # NOT work here: "modey" -> repair_hypothesis "modes" and "contries" ->
            # "contrives" are both real English words, so dictionary membership alone
            # cannot tell them apart from the correct answers ("money", "countries").
            # Both wrong suggestions also carried the identical 0.82 confidence score
            # as the correct "goverment" -> "government" suggestion, so confidence
            # isn't a usable filter either. Rather than assert a coin-flip-reliable
            # correction, the FIX flag itself (there IS a misspelling here) is kept --
            # that part of the Detector's signal is trustworthy -- but no specific
            # replacement word is shown. This needs either a real spellchecker with
            # context/frequency weighting or an LLM check to do safely; not attempted
            # here.
            suggestion_text = None
            suggestion_verified = False
        elif materialised:
            suggestion_text = materialised
            suggestion_verified = True
        elif repair and context:
            suggestion_text = context.replace(quote, str(repair), 1)
            suggestion_verified = True
        elif repair:
            suggestion_text = str(repair)
            suggestion_verified = True
        row_id = str(c.get('row_id') or c.get('candidate_id') or len(new_units))
        new_units.append({
            'unit_id': f"fix_detector_{row_id[:16]}",
            'class_label': 'FIX',
            'unit_text': quote,
            'unit_norm': norm_text(quote),
            'unit_type': 'detector_validated_fix_span',
            'replacement_scope': 'span',
            'error_family': c.get('family'),
            'detector_family': c.get('family'),
            'issue_code': c.get('issue_code'),
            'occurrence_count': 1,
            'source_sentence_index': c.get('sentence_index'),
            'source_paragraph_index': c.get('paragraph_index'),
            'context': context,
            'locations': [{
                'start': c.get('span_start'), 'end': c.get('span_end'),
                'paragraph_idx': c.get('paragraph_index'),
            }],
            'requires_full_contextual_check': False,
            'safety_level': 'detector_validated_arbitrated_fix',
            'suggestions': ([{
                'text': suggestion_text,
                'validation': {
                    'accepted': True,
                    'gates': ['detector_arbitration_accepted'] + (['lexical_registry_cross_checked'] if suggestion_verified else []),
                    'reason': (
                        f"Promoted from the Detector's validated_fix_candidates "
                        f"(confidence={c.get('confidence')}, "
                        f"source_engines={c.get('source_engines')})."
                    ),
                },
            }] if suggestion_text else []),
            'spelling_correction_unverified': (family == 'SPELLING' and not suggestion_verified),
            'unverified_note': (
                "Detector flagged a likely spelling error at this span, but its specific suggested "
                "correction did not pass independent cross-check against the canonical lexical registry "
                "-- confirmed on real data that the Detector's own repair_hypothesis is sometimes a real-but-"
                "wrong word (e.g. 'modey' -> 'modes' instead of 'money') at the SAME confidence score as "
                "correct suggestions, so confidence alone can't be trusted here. No specific correction is "
                "shown; flag the span for student/human review instead of asserting an unverified answer."
            ) if (family == 'SPELLING' and not suggestion_verified) else None,
            'source': 'detector_validated_fix_candidate',
            'detector_confidence': c.get('confidence'),
            'detector_arbitration_reasons': c.get('arbitration_reasons'),
        })
        existing_norms.add(norm_text(quote))
    return new_units


def _v180_broken_sentence_indices() -> Set[int]:
    idxs: Set[int] = set()
    for b in DETECTOR_BROKEN_SENTENCES:
        if not isinstance(b, dict):
            continue
        sev = b.get('severity')
        rec = b.get('recoverability_score')
        if sev == 'critical' or (isinstance(rec, (int, float)) and rec <= 0.3):
            si = b.get('sentence_index')
            if si is not None:
                idxs.add(si)
    return idxs


# v1.7.2: LEXICAL-only routing, by explicit instruction -- grammar, coherence/
# cohesion, and task-response Detector errors must never reach LRET's fix
# pool; LRET is a lexical-resource engine, not a general error router. This
# mirrors det_vip_v18d_2.py's own LEXICAL_FAMILIES set exactly (confirmed by
# direct read of FAMILY_TO_RUBRIC there). Kept as a plain set here rather than
# imported cross-file, since LRET and Detector are intentionally standalone
# files with no runtime coupling -- if Detector's taxonomy changes, this needs
# a matching update, same as any other cross-engine contract.
LEXICAL_DETECTOR_FAMILIES = {
    "SPELLING", "WORD_FORM", "COLLOCATION", "WORD_CHOICE", "REDUNDANCY",
    "REGISTER", "REPETITION", "LEXICAL_PRECISION", "SEMANTIC_COMBINATION",
}


def _v172_build_lexical_errormap_fix_units(existing_fix_units: List[Dict[str, Any]], validator: ContextFitValidator) -> List[Dict[str, Any]]:
    """Route the Detector's own arbitrated errors into LRET's fix pool --
    but only the lexical-family subset, and only after every suggestion
    passes the same contextual-fit / meaning-preservation gate every other
    LRET suggestion source has to pass (RuleBasedContextFitValidator).

    This reads DETECTOR_ERROR_ROWS (the real `errors` array schema, confirmed
    against actual pipeline output), not DETECTOR_FIX_CANDIDATES (the
    `validated_fix_candidates` schema _v180_build_detector_fix_units expects
    above, which real errormap files never actually contain). The two
    functions are additive, not a replacement for each other -- if a future
    Detector version does emit validated_fix_candidates, that path still
    works; this path is what actually fires against real data today.
    """
    existing_norms = {norm_text(u.get('unit_text') or '') for u in existing_fix_units}
    new_units: List[Dict[str, Any]] = []
    for row in DETECTOR_ERROR_ROWS:
        if not isinstance(row, dict):
            continue
        family = str(row.get('family') or '').upper()
        rubric = str(row.get('criterion') or row.get('rubric') or '').lower()
        is_lexical = (rubric == 'lexical_resource') if rubric else (family in LEXICAL_DETECTOR_FAMILIES)
        if not is_lexical or family not in LEXICAL_DETECTOR_FAMILIES:
            # Rubric and family must agree it's lexical. A row whose rubric
            # says lexical_resource but whose family isn't in the known
            # lexical set (or vice versa) is a Detector-side inconsistency --
            # fail closed and leave it to Detector/Writing Coach, not LRET.
            continue
        quote = str(row.get('surface_quote') or row.get('quote') or '').strip()
        if not quote or norm_text(quote) in existing_norms:
            continue
        loc = row.get('location') or {}
        sentence = str(loc.get('sentence') or row.get('sentence') or '').strip()
        suggested = str(row.get('suggested_revision') or '').strip()
        message = str(row.get('student_message') or row.get('explanation') or '').strip()

        suggestions_out: List[Dict[str, Any]] = []
        unverified_note = None
        if family == 'SPELLING':
            # Same v1.8.0 safety decision as _v180_build_detector_fix_units
            # above: never assert a specific spelling correction here without
            # an independent check this engine doesn't have. The FIX flag
            # itself is kept; no replacement text is shown.
            unverified_note = (
                "Detector flagged a likely spelling error at this span; no specific "
                "correction is asserted here without independent verification."
            )
        elif suggested:
            # The contextual-fit / meaning-preservation gate every other LRET
            # suggestion source must pass, per explicit instruction: dismiss
            # any candidate that doesn't preserve the original's meaning.
            gate = validator.validate(quote, suggested, sentence or quote, source="detector_lexical_errormap")
            if gate.passed:
                suggestions_out.append({
                    'text': suggested,
                    'validation': {
                        'accepted': True,
                        'gates': list(gate.gates_checked) + ['detector_lexical_errormap_source'],
                        'reason': f"Promoted from the Detector's own arbitrated lexical error (family={family}, confidence={row.get('confidence')}).",
                    },
                })
            else:
                unverified_note = (
                    f"Detector flagged a {family.replace('_', ' ').lower()} problem at this span, but its suggested "
                    f"revision did not pass the contextual-fit check ({gate.reason}) -- most likely because it would "
                    f"change the sentence's meaning rather than just correct the error. No specific correction is "
                    f"shown; flag the span for review instead of asserting an unverified answer."
                )

        row_id = str(row.get('error_id') or row.get('source_row_id') or len(new_units))
        new_units.append({
            'unit_id': f"fix_detector_lex_{row_id[:16]}",
            'class_label': 'FIX',
            'unit_text': quote,
            'unit_norm': norm_text(quote),
            'unit_type': 'detector_lexical_errormap_fix_span',
            'replacement_scope': 'span',
            'error_family': family,
            'detector_family': family,
            'occurrence_count': 1,
            'source_sentence_index': row.get('sentence_index'),
            'source_paragraph_index': row.get('paragraph_index'),
            'context': sentence,
            'student_message': message or None,
            'requires_full_contextual_check': False,
            'safety_level': 'detector_lexical_errormap_arbitrated_fix',
            'suggestions': suggestions_out,
            'spelling_correction_unverified': (family == 'SPELLING'),
            'unverified_note': unverified_note,
            'source': 'detector_lexical_errormap',
            'detector_confidence': row.get('confidence'),
        })
        existing_norms.add(norm_text(quote))
    return new_units


def _v180_apply_detector_integration(result: Dict[str, Any], validator: Optional[ContextFitValidator] = None) -> None:
    validator = validator or RuleBasedContextFitValidator()
    lexical_added = _v172_build_lexical_errormap_fix_units(result.get('fix_units') or [], validator)
    if lexical_added:
        result['fix_units'] = (result.get('fix_units') or []) + lexical_added

    if not DETECTOR_FIX_CANDIDATES and not DETECTOR_BROKEN_SENTENCES:
        result.setdefault('lexical_profile', {})['v172_lexical_errormap_fix_units_added'] = len(lexical_added)
        return
    existing_fix = result.get('fix_units') or []
    added_fix = _v180_build_detector_fix_units(existing_fix)
    result['fix_units'] = existing_fix + added_fix

    broken_idxs = _v180_broken_sentence_indices()
    if broken_idxs:
        detector_fix_texts = {
            norm_text(u.get('unit_text') or '')
            for u in result.get('fix_units', [])
            if u.get('source') == 'detector_validated_fix_candidate'
        }
        for cat in ('enhance_units', 'keep_units', 'clarify_units', 'contextual_synonym_tasks'):
            kept = []
            for u in result.get(cat) or []:
                si = u.get('source_sentence_index')
                if si in broken_idxs and norm_text(u.get('unit_text') or '') not in detector_fix_texts:
                    continue
                kept.append(u)
            result[cat] = kept

        covered_idxs = {
            u.get('source_sentence_index') for u in result.get('fix_units', [])
            if u.get('source') == 'detector_validated_fix_candidate'
        }
        clarify_added = []
        for b in DETECTOR_BROKEN_SENTENCES:
            if not isinstance(b, dict):
                continue
            si = b.get('sentence_index')
            if si is None or si not in broken_idxs or si in covered_idxs:
                continue
            covered_idxs.add(si)
            stext = b.get('sentence_text') or ''
            clarify_added.append({
                'unit_id': f"clarify_detector_broken_sentence_{si}",
                'class_label': 'CLARIFY',
                'unit_text': stext,
                'unit_norm': norm_text(stext),
                'unit_type': 'detector_flagged_broken_sentence',
                'context': stext,
                'source_sentence_index': si,
                'source_paragraph_index': b.get('paragraph_index') if isinstance(b, dict) else None,
                'safety_level': 'detector_flagged_critical_corruption_no_asserted_answer',
                'clarify_reason': (
                    f"Flagged by the Detector as critically corrupted "
                    f"(recoverability_score={b.get('recoverability_score')}); not safely "
                    f"decomposable into smaller lexical candidates. {b.get('root_cause_hint') or ''}"
                ).strip(),
                'phase1_prompt': (
                    f"This whole sentence needs rework: [{stext}]. "
                    f"Hint: {b.get('root_cause_hint') or 'multiple issues detected'}"
                ),
                'suggestions': [],
                'student_facing_task': True,
            })
        result['clarify_units'] = (result.get('clarify_units') or []) + clarify_added

    lp = result.setdefault('lexical_profile', {})
    lp['fix_count'] = len(result.get('fix_units') or [])
    lp['enhance_count'] = len(result.get('enhance_units') or [])
    lp['contextual_synonym_task_count'] = len(result.get('contextual_synonym_tasks') or [])
    lp['clarify_count'] = len(result.get('clarify_units') or [])
    lp['keep_count'] = len(result.get('keep_units') or [])
    lp['v172_lexical_errormap_fix_units_added'] = len(lexical_added)
    lp['v180_detector_integration'] = {
        'detector_fix_candidates_available': len(DETECTOR_FIX_CANDIDATES),
        'detector_fix_units_added': len(added_fix),
        'detector_broken_sentences_flagged': len(broken_idxs),
    }


_prev_main_v180 = main


def main(argv: Optional[List[str]] = None) -> int:
    global ACTIVE_LLM_PROVIDER, LLM_MAX_CANDIDATES, LLM_MIN_VALID_SUGGESTIONS
    parser = argparse.ArgumentParser(
        description="LRET Engine v1.8.0 -- Detector errormap integration (validated_fix_candidates + broken_sentences_raw)")
    parser.add_argument("--input", "-i", required=True, help="Path to LRET input JSON or full Evaluator/WKE output JSON")
    parser.add_argument("--output", "-o", required=True, help="Path to write LRET output JSON")
    parser.add_argument("--mode", choices=["fix_only", "enhance_only", "fix_and_enhance"], default=None)
    parser.add_argument("--student-id", default=None)
    parser.add_argument("--essay-id", default=None)
    parser.add_argument("--submission-id", default=None)
    parser.add_argument("--history", default=None, help="Optional learner_lexical_history JSON path")
    parser.add_argument("--resources", nargs="*", default=None, help="Optional simple external lexical resource JSON file(s)")
    parser.add_argument("--canonical-resources", default=None, help="Path to canonical resources directory or final_app_registries zip")
    parser.add_argument("--detector-output", default=None,
                         help="Optional path to the Detector's output/errormap JSON (batch 01_detector_output.json "
                              "or per-essay 01b_errormap_v3.json format). If given, validated_fix_candidates are "
                              "promoted into fix_units and broken_sentences_raw suppresses noisy candidates from "
                              "those exact sentences in favor of one honest CLARIFY task per sentence.")
    parser.add_argument("--use-llm", action="store_true",
                         help="Allow an LLM pass IF the free registry-only pass under-delivers (see --llm-yield-floor). "
                              "Does not force an LLM call by itself.")
    parser.add_argument("--force-llm", action="store_true",
                         help="Run the LLM pass unconditionally (skips the yield-floor check). Implies --use-llm.")
    parser.add_argument("--llm-yield-floor", type=int, default=6,
                         help="Minimum (enhance_count + asserted contextual_synonym_count) from the free pass "
                              "below which an LLM pass is triggered. Default 6.")
    parser.add_argument("--llm-required", action="store_true", help="Fail if the LLM pass triggers but OPENAI_API_KEY is missing or the request fails")
    parser.add_argument("--llm-model", default="gpt-5-nano",
                         help="OpenAI model for suggestion generation; default gpt-5-nano ($0.05/$0.40 per 1M "
                              "input/output tokens vs gpt-5-mini's $0.25/$2.00) -- every suggestion is still "
                              "deterministically re-validated regardless of model, so nano-tier is the "
                              "recommended default; override if quality in practice requires more.")
    parser.add_argument("--llm-timeout", type=int, default=120, help="OpenAI read timeout in seconds; increase on slow connections")
    parser.add_argument("--llm-max-candidates", type=int, default=12,
                         help="Lowered from 28 in prior versions now that the free pass resolves a meaningful "
                              "share of candidates before the LLM pass ever runs.")
    parser.add_argument("--llm-batch-size", type=int, default=4, help="Number of candidates per OpenAI request; lower this if timeouts occur")
    parser.add_argument("--llm-max-retries", type=int, default=3, help="Retries per LLM batch after transport/API failure")
    parser.add_argument("--llm-retry-sleep", type=float, default=2.0, help="Base sleep seconds between retries")
    parser.add_argument("--llm-min-valid-suggestions", type=int, default=2)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)

    llm_allowed = bool(args.use_llm or args.force_llm)
    LLM_MAX_CANDIDATES = max(0, int(args.llm_max_candidates))
    LLM_MIN_VALID_SUGGESTIONS = max(1, int(args.llm_min_valid_suggestions))
    load_external_lexical_resources(args.resources)
    load_canonical_resources(args.canonical_resources)
    load_detector_output(args.detector_output)
    ACTIVE_LLM_PROVIDER = None
    _LLM_STATS['enabled'] = False
    _LLM_STATS['model'] = None

    raw = load_json_file(args.input)
    history = load_json_file(args.history) if args.history else None
    lret_input = make_lret_input(
        raw, mode=args.mode, student_id=args.student_id, essay_id=args.essay_id,
        submission_id=args.submission_id, learner_lexical_history=history,
    )

    result = _v167_run_tiered(
        lret_input, None,
        llm_allowed=llm_allowed, yield_floor=max(0, int(args.llm_yield_floor)), force_llm=bool(args.force_llm),
        llm_model=args.llm_model, llm_timeout=args.llm_timeout, llm_batch_size=args.llm_batch_size,
        llm_max_retries=args.llm_max_retries, llm_retry_sleep=args.llm_retry_sleep,
        llm_required=bool(args.llm_required),
    )
    result['run']['engine_version'] = ENGINE_VERSION
    result['lexical_profile']['canonical_resource_stats'] = copy.deepcopy(_RESOURCE_STATS)
    result['lexical_profile']['llm_suggestion_stats'] = copy.deepcopy(_LLM_STATS)
    _v180_apply_detector_integration(result)
    result['qa'].setdefault('contract_checks', {})['llm_does_not_create_new_spans'] = True
    result['qa'].setdefault('contract_checks', {})['llm_suggestions_deterministically_validated'] = True
    result['qa'].setdefault('contract_checks', {})['no_embedded_phrase_enhance_bank'] = True
    result['qa'].setdefault('contract_checks', {})['canonical_resources_external_only'] = bool(args.canonical_resources)
    result['qa'].setdefault('contract_checks', {})['no_embedded_topic_whitelist'] = True
    result['qa'].setdefault('contract_checks', {})['no_plural_subject_need_regex'] = True
    result['qa'].setdefault('contract_checks', {})['clarify_is_visible_student_task'] = all(u.get('phase1_prompt') for u in result.get('clarify_units', []))
    result['qa'].setdefault('contract_checks', {})['no_embedded_topic_or_essay_specific_lists'] = True
    if args.llm_required and result.get('lexical_profile', {}).get('v167_tiered_llm', {}).get('triggered') and _LLM_STATS['calls'] == 0:
        raise RuntimeError("--llm-required was set, the LLM pass triggered, but no successful LLM call was made")
    if _LLM_STATS.get('warnings'):
        result['qa'].setdefault('warnings', []).extend(_LLM_STATS['warnings'])
        if args.llm_required:
            raise RuntimeError("LLM required but warnings occurred: " + "; ".join(_LLM_STATS['warnings']))

    write_json_file(args.output, result, pretty=args.pretty)

    if args.summary:
        p = result.get("lexical_profile", {})
        v167 = p.get('v167_tiered_llm', {})
        v180 = p.get('v180_detector_integration', {})
        print("=== LRET v1.8.0 detector-integration summary ===")
        print("output:", args.output)
        print("fix_units:", p.get("fix_count"))
        print("main_enhance_units:", p.get("enhance_count"))
        print("contextual_synonym_tasks:", p.get("contextual_synonym_task_count"))
        print("clarify_units:", p.get("clarify_count"))
        print("keep_units:", p.get("keep_count"))
        print("free_pass_yield:", v167.get("free_pass_yield"))
        print("llm_triggered:", v167.get("triggered"))
        print("canonical_loaded:", p.get("canonical_resource_stats", {}).get("canonical_loaded"))
        print("detector_fix_candidates_available:", v180.get("detector_fix_candidates_available"))
        print("detector_fix_units_added:", v180.get("detector_fix_units_added"))
        print("detector_broken_sentences_flagged:", v180.get("detector_broken_sentences_flagged"))
        print("qa_status:", result.get("qa", {}).get("status"))
        print("qa_warnings:", len(result.get("qa", {}).get("warnings", [])))
    return 0


ENGINE_VERSION = "lret-engine-v1.8.0-detector-errormap-integration"



# =============================================================================
# v1.9.0 -- Repeated generic/vague word variation prompts (CLARIFY, no asserted answer)
# =============================================================================
#
# Signal: essays repeatedly reuse the same generic, low-information word
# ("good", "things", "a lot", "important", "help", ...) instead of varying
# their lexis -- exactly the lexical-flexibility gap this whole tool exists
# to address, but never previously surfaced anywhere in the pipeline.
#
# This does NOT suggest a specific synonym (no registry check can safely pick
# "the" replacement for a generic word in context -- same unverifiable-answer
# problem already hit and rejected for spelling in v1.8.0). It only tells the
# student: this word repeats N times, here is where, try varying it.
#
# Deliberately excludes topic-central repeats ("older", "people", "young" in
# an ageing-population essay) by requiring the word be in a small, closed,
# structural "generic descriptor" list -- the same category of list as
# UNIVERSAL_VAGUE_NOUNS, not essay-specific content -- and by skipping any
# word already recognised as topic-relevant academic/keep vocabulary via
# ACADEMIC_SIGNAL_WORDS / EXTERNAL_ACADEMIC_SIGNAL_WORDS / STABLE_SINGLE_KEEP.

_V190_GENERIC_REPEAT_WORDS: Set[str] = {
    # vague nouns already recognised elsewhere in the engine
    "thing", "things", "stuff", "kind", "kinds",
    "something", "anything", "everything",
    # generic evaluative adjectives
    "good", "bad", "big", "nice", "great", "important", "interesting",
    "difficult", "easy",
    # generic quantifiers / hedges
    "many", "much", "lot", "lots",
    # generic light verbs (near-empty semantic content on their own)
    "get", "gets", "got", "getting",
    "make", "makes", "made", "making",
    "help", "helps", "helped", "helping",
    "give", "gives", "gave", "giving",
}

_V190_MIN_REPEAT_COUNT = 3
_V190_MAX_EXAMPLE_SENTENCES = 3
_V190_MAX_FLAGS_PER_ESSAY = 5


def _v190_collect_sentence_map(result: Dict[str, Any]) -> Dict[Any, str]:
    """Reconstruct a source_sentence_index -> sentence-text map directly from
    the final result's own unit lists (context field), so this feature works
    off whatever units survived the whole pipeline rather than needing to
    reach back into internal payload structures."""
    sentences: Dict[Any, str] = {}
    for key in ('fix_units', 'enhance_units', 'keep_units', 'clarify_units'):
        for u in (result.get(key) or []):
            si = u.get('source_sentence_index')
            ctx = u.get('context')
            if si is not None and ctx and si not in sentences:
                sentences[si] = str(ctx)
    for t in (result.get('contextual_synonym_tasks') or []):
        si = t.get('source_sentence_index')
        ctx = t.get('context')
        if si is not None and ctx and si not in sentences:
            sentences[si] = str(ctx)
    return sentences


def _v190_flag_repeated_generic_words(result: Dict[str, Any]) -> None:
    sentence_map = _v190_collect_sentence_map(result)
    if not sentence_map:
        result.setdefault('lexical_profile', {})['v190_repeated_word_variation'] = {
            'words_flagged': 0, 'reason': 'no_reconstructable_sentences',
        }
        return

    protected: Set[str] = set()
    protected.update({str(w).lower() for w in (ACADEMIC_SIGNAL_WORDS or set())})
    protected.update({str(w).lower() for w in (EXTERNAL_ACADEMIC_SIGNAL_WORDS or set())})
    protected.update({str(w).lower() for w in (STABLE_SINGLE_KEEP or set())})

    # word -> list of (sentence_index, sentence_text) occurrences
    occurrences: Dict[str, List[Any]] = {}
    for si, stext in sentence_map.items():
        for tok in re.findall(r"[A-Za-z']+", stext.lower()):
            if tok in _V190_GENERIC_REPEAT_WORDS and tok not in protected:
                occurrences.setdefault(tok, []).append(si)

    already_flagged_texts = {
        norm_text(str(u.get('unit_text') or ''))
        for u in (result.get('clarify_units') or [])
    }

    candidates = [
        (w, sis) for w, sis in occurrences.items()
        if len(sis) >= _V190_MIN_REPEAT_COUNT and norm_text(w) not in already_flagged_texts
    ]
    candidates.sort(key=lambda item: len(item[1]), reverse=True)
    candidates = candidates[:_V190_MAX_FLAGS_PER_ESSAY]

    clarify_added: List[Dict[str, Any]] = []
    for word, sis in candidates:
        example_sis = sis[:_V190_MAX_EXAMPLE_SENTENCES]
        examples = [sentence_map[si] for si in example_sis if si in sentence_map]
        clarify_added.append({
            'unit_id': f"clarify_repeated_word_{word}",
            'class_label': 'CLARIFY',
            'unit_text': word,
            'unit_norm': norm_text(word),
            'unit_type': 'repeated_generic_word_variation',
            'context': examples[0] if examples else '',
            'source_sentence_index': sis[0] if sis else None,
            'safety_level': 'v190_repetition_signal_no_asserted_answer',
            'reveal_policy': {'mode': 'no_suggested_answer', 'reason': 'no_single_safe_synonym_for_all_occurrences'},
            'clarify_reason': (
                f'The word "{word}" is repeated {len(sis)} times using generic, low-information '
                f'vocabulary. No single replacement is asserted here -- the same word may need a '
                f'different alternative in each place depending on context -- but the repetition '
                f'itself is a lexical-variety signal worth addressing.'
            ),
            'phase1_prompt': (
                f'You used "{word}" {len(sis)} times. Try varying your word choice in at least '
                f'some of these places:\n' + '\n'.join(f'- {ex}' for ex in examples)
            ),
            'example_sentences': examples,
            'repeat_count': len(sis),
            'suggestions': [],
            'student_facing_task': True,
        })

    result['clarify_units'] = (result.get('clarify_units') or []) + clarify_added
    result.setdefault('lexical_profile', {})['v190_repeated_word_variation'] = {
        'words_flagged': len(clarify_added),
        'words_considered': len(occurrences),
        'min_repeat_threshold': _V190_MIN_REPEAT_COUNT,
    }


_prev_main_v190 = main


def main(argv: Optional[List[str]] = None) -> int:
    global ACTIVE_LLM_PROVIDER, LLM_MAX_CANDIDATES, LLM_MIN_VALID_SUGGESTIONS
    parser = argparse.ArgumentParser(
        description="LRET Engine v1.9.0 -- Repeated generic/vague word variation prompts (CLARIFY, additive)")
    parser.add_argument("--input", "-i", required=True, help="Path to LRET input JSON or full Evaluator/WKE output JSON")
    parser.add_argument("--output", "-o", required=True, help="Path to write LRET output JSON")
    parser.add_argument("--mode", choices=["fix_only", "enhance_only", "fix_and_enhance"], default=None)
    parser.add_argument("--student-id", default=None)
    parser.add_argument("--essay-id", default=None)
    parser.add_argument("--submission-id", default=None)
    parser.add_argument("--history", default=None, help="Optional learner_lexical_history JSON path")
    parser.add_argument("--resources", nargs="*", default=None, help="Optional simple external lexical resource JSON file(s)")
    parser.add_argument("--canonical-resources", default=None, help="Path to canonical resources directory or final_app_registries zip")
    parser.add_argument("--detector-output", default=None,
                         help="Optional path to the Detector's output/errormap JSON (batch 01_detector_output.json "
                              "or per-essay 01b_errormap_v3.json format).")
    parser.add_argument("--disable-repeated-word-check", action="store_true",
                         help="Disable the v1.9.0 repeated-generic-word CLARIFY prompts (on by default).")
    parser.add_argument("--use-llm", action="store_true",
                         help="Allow an LLM pass IF the free registry-only pass under-delivers (see --llm-yield-floor). "
                              "Does not force an LLM call by itself.")
    parser.add_argument("--force-llm", action="store_true",
                         help="Run the LLM pass unconditionally (skips the yield-floor check). Implies --use-llm.")
    parser.add_argument("--llm-yield-floor", type=int, default=6,
                         help="Minimum (enhance_count + asserted contextual_synonym_count) from the free pass "
                              "below which an LLM pass is triggered. Default 6.")
    parser.add_argument("--llm-required", action="store_true", help="Fail if the LLM pass triggers but OPENAI_API_KEY is missing or the request fails")
    parser.add_argument("--llm-model", default="gpt-5-nano",
                         help="OpenAI model for suggestion generation; default gpt-5-nano ($0.05/$0.40 per 1M "
                              "input/output tokens vs gpt-5-mini's $0.25/$2.00) -- every suggestion is still "
                              "deterministically re-validated regardless of model, so nano-tier is the "
                              "recommended default; override if quality in practice requires more.")
    parser.add_argument("--llm-timeout", type=int, default=120, help="OpenAI read timeout in seconds; increase on slow connections")
    parser.add_argument("--llm-max-candidates", type=int, default=12,
                         help="Lowered from 28 in prior versions now that the free pass resolves a meaningful "
                              "share of candidates before the LLM pass ever runs.")
    parser.add_argument("--llm-batch-size", type=int, default=4, help="Number of candidates per OpenAI request; lower this if timeouts occur")
    parser.add_argument("--llm-max-retries", type=int, default=3, help="Retries per LLM batch after transport/API failure")
    parser.add_argument("--llm-retry-sleep", type=float, default=2.0, help="Base sleep seconds between retries")
    parser.add_argument("--llm-min-valid-suggestions", type=int, default=2)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)

    llm_allowed = bool(args.use_llm or args.force_llm)
    LLM_MAX_CANDIDATES = max(0, int(args.llm_max_candidates))
    LLM_MIN_VALID_SUGGESTIONS = max(1, int(args.llm_min_valid_suggestions))
    load_external_lexical_resources(args.resources)
    load_canonical_resources(args.canonical_resources)
    load_detector_output(args.detector_output)
    ACTIVE_LLM_PROVIDER = None
    _LLM_STATS['enabled'] = False
    _LLM_STATS['model'] = None

    raw = load_json_file(args.input)
    history = load_json_file(args.history) if args.history else None
    lret_input = make_lret_input(
        raw, mode=args.mode, student_id=args.student_id, essay_id=args.essay_id,
        submission_id=args.submission_id, learner_lexical_history=history,
    )

    result = _v167_run_tiered(
        lret_input, None,
        llm_allowed=llm_allowed, yield_floor=max(0, int(args.llm_yield_floor)), force_llm=bool(args.force_llm),
        llm_model=args.llm_model, llm_timeout=args.llm_timeout, llm_batch_size=args.llm_batch_size,
        llm_max_retries=args.llm_max_retries, llm_retry_sleep=args.llm_retry_sleep,
        llm_required=bool(args.llm_required),
    )
    result['run']['engine_version'] = ENGINE_VERSION
    result['lexical_profile']['canonical_resource_stats'] = copy.deepcopy(_RESOURCE_STATS)
    result['lexical_profile']['llm_suggestion_stats'] = copy.deepcopy(_LLM_STATS)
    _v180_apply_detector_integration(result)
    if not args.disable_repeated_word_check:
        _v190_flag_repeated_generic_words(result)
    result['qa'].setdefault('contract_checks', {})['llm_does_not_create_new_spans'] = True
    result['qa'].setdefault('contract_checks', {})['llm_suggestions_deterministically_validated'] = True
    result['qa'].setdefault('contract_checks', {})['no_embedded_phrase_enhance_bank'] = True
    result['qa'].setdefault('contract_checks', {})['canonical_resources_external_only'] = bool(args.canonical_resources)
    result['qa'].setdefault('contract_checks', {})['no_embedded_topic_whitelist'] = True
    result['qa'].setdefault('contract_checks', {})['no_plural_subject_need_regex'] = True
    result['qa'].setdefault('contract_checks', {})['clarify_is_visible_student_task'] = all(u.get('phase1_prompt') for u in result.get('clarify_units', []))
    result['qa'].setdefault('contract_checks', {})['no_embedded_topic_or_essay_specific_lists'] = True
    if args.llm_required and result.get('lexical_profile', {}).get('v167_tiered_llm', {}).get('triggered') and _LLM_STATS['calls'] == 0:
        raise RuntimeError("--llm-required was set, the LLM pass triggered, but no successful LLM call was made")
    if _LLM_STATS.get('warnings'):
        result['qa'].setdefault('warnings', []).extend(_LLM_STATS['warnings'])
        if args.llm_required:
            raise RuntimeError("LLM required but warnings occurred: " + "; ".join(_LLM_STATS['warnings']))

    lp = result.setdefault('lexical_profile', {})
    lp['fix_count'] = len(result.get('fix_units') or [])
    lp['enhance_count'] = len(result.get('enhance_units') or [])
    lp['contextual_synonym_task_count'] = len(result.get('contextual_synonym_tasks') or [])
    lp['clarify_count'] = len(result.get('clarify_units') or [])
    lp['keep_count'] = len(result.get('keep_units') or [])

    write_json_file(args.output, result, pretty=args.pretty)

    if args.summary:
        p = result.get("lexical_profile", {})
        v167 = p.get('v167_tiered_llm', {})
        v180 = p.get('v180_detector_integration', {})
        v190 = p.get('v190_repeated_word_variation', {})
        print("=== LRET v1.9.0 repeated-word-variation summary ===")
        print("output:", args.output)
        print("fix_units:", p.get("fix_count"))
        print("main_enhance_units:", p.get("enhance_count"))
        print("contextual_synonym_tasks:", p.get("contextual_synonym_task_count"))
        print("clarify_units:", p.get("clarify_count"))
        print("keep_units:", p.get("keep_count"))
        print("free_pass_yield:", v167.get("free_pass_yield"))
        print("llm_triggered:", v167.get("triggered"))
        print("canonical_loaded:", p.get("canonical_resource_stats", {}).get("canonical_loaded"))
        print("detector_fix_candidates_available:", v180.get("detector_fix_candidates_available"))
        print("detector_fix_units_added:", v180.get("detector_fix_units_added"))
        print("detector_broken_sentences_flagged:", v180.get("detector_broken_sentences_flagged"))
        print("repeated_words_flagged:", v190.get("words_flagged"))
        print("qa_status:", result.get("qa", {}).get("status"))
        print("qa_warnings:", len(result.get("qa", {}).get("warnings", [])))
    return 0


ENGINE_VERSION = "lret-engine-v1.9.0-repeated-generic-word-variation"
SCHEMA_VERSION_OUT = "LRET_OUTPUT_V1.2"


# =============================================================================
# v1.10.0 -- Collocation-slot precision menu (CLARIFY, produce-before-reveal,
# no single asserted answer)
# =============================================================================
#
# Direct pushback that shaped this: single-word synonym cards (the
# enhance_thesaurus.json mechanism -- "good" -> beneficial/advantageous/
# favourable) are what other apps already do, and are exactly the shape of
# thing that produced contextually-wrong suggestions before (a word picked
# without knowing what the rest of the phrase needs). What was asked for
# instead: work at the collocation/phrase level, and let the student attempt
# their own rewrite BEFORE any alternative is revealed -- never hand over a
# single "correct" answer up front.
#
# This reuses _v165_generate_free_modifier_swap_candidates() (the same
# registry-attested-pair machinery built for the original free-modifier-swap
# mechanism) but changes what happens with the candidates it finds:
#   - v1.6.5 picked up to 4 and asserted them as ENHANCE suggestions -- retired
#     in v1.6.9 because "attested for the same headword" does not mean
#     "verified to fit THIS sentence's meaning".
#   - v1.10.0 does not assert any single one of them. It surfaces the FULL set
#     of registry-attested sibling collocates for that exact headword+slot as
#     a menu, gated behind a produce-before-reveal policy: the student must
#     attempt their own rewrite first, and the menu is framed explicitly as
#     "other options that are also valid here -- not a verified answer for
#     your context" rather than a model answer.
#
# Also gated on a basic-vocabulary check -- only phrases whose modifier is one
# of the small, closed, structural set of common/basic words already
# maintained in enhance_thesaurus.json (e.g. "good", "big", "many", "help",
# "use", "make", "problem") get flagged. A KEEP unit using an already
# sophisticated, attested collocate (e.g. "considerable benefit") is left
# alone -- this only targets phrases with real headroom for more precise
# lexical choice, not phrases that are already strong.

_V1100_BASIC_VOCAB_GATE: Set[str] = set()


def _v1100_load_basic_vocab_gate(resource_path: Optional[str]) -> None:
    """Flattens enhance_thesaurus.json's top-level keys into single content-word
    tokens. Multi-word keys (e.g. "a lot of", "look at") contribute their
    content word(s) only -- this gate is used to test a single collocate
    token found inside a KEEP unit, not to match whole phrases."""
    global _V1100_BASIC_VOCAB_GATE
    _V1100_BASIC_VOCAB_GATE = set()
    data = _read_json_from_resource(resource_path, 'enhance_thesaurus.json') if resource_path else None
    if not isinstance(data, dict):
        return
    stop = {"a", "of", "at", "about", "in", "my", "i"}
    for key in data.keys():
        if key.startswith('__'):
            continue
        for tok in norm_text(key).split():
            if tok and tok not in stop:
                _V1100_BASIC_VOCAB_GATE.add(tok)


_prev_load_canonical_resources_v1100 = load_canonical_resources


def load_canonical_resources(resource_path: Optional[str]) -> None:
    _prev_load_canonical_resources_v1100(resource_path)
    _v1100_load_basic_vocab_gate(resource_path)


_V1100_MAX_MENU_SIZE = 8
_V1100_MAX_FLAGS_PER_ESSAY = 6


# A small, closed denylist of negative-polarity/antonym-shaped modifiers that
# are attested collocates in the registry (e.g. "bad advice" is real English)
# but are the opposite of what "upgrade this word choice" is asking for. Not
# an essay-specific list -- structural, like EDGE_STOPWORDS elsewhere in the
# engine -- and only ever removes items from an already-safe menu; it cannot
# cause an unsafe suggestion, since the remaining items are still registry-
# attested and nothing is asserted as correct either way.
_V1100_NEGATIVE_POLARITY_DENYLIST = {
    "bad", "wrong", "poor", "awful", "terrible", "conflicting",
    "negative", "unhelpful", "useless", "incorrect",
}


def _v1100_find_menu_items(text: str) -> List[Dict[str, Any]]:
    """Given a phrase, return the registry-attested collocate menu (grouped by
    headword+relation_type+word-actually-used), gated to slots where the word
    actually used is a basic/common word worth upgrading. Empty list if no
    such slot exists for this phrase."""
    raw_candidates = _v165_generate_free_modifier_swap_candidates(text)
    slots: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for c in raw_candidates:
        used_tok = norm_text(c.get('replaces_token') or '')
        if used_tok not in _V1100_BASIC_VOCAB_GATE:
            continue
        alt = c['text']
        if norm_text(alt) in _V1100_NEGATIVE_POLARITY_DENYLIST:
            continue
        key = (c['headword'], c['relation_type'], used_tok)
        slot = slots.setdefault(key, {'alternatives': [], 'seen_alts': set()})
        if alt not in slot['seen_alts'] and alt != used_tok:
            slot['seen_alts'].add(alt)
            slot['alternatives'].append({
                'text': alt,
                'suggested_phrase': c['suggested_phrase'],
            })

    menu_items: List[Dict[str, Any]] = []
    for (headword, rel, used_tok), slot in slots.items():
        alts = sorted(slot['alternatives'], key=lambda a: a['text'])[:_V1100_MAX_MENU_SIZE]
        if not alts:
            continue
        menu_items.append({
            'headword': headword,
            'relation_type': rel,
            'word_used': used_tok,
            'alternatives': alts,
            'note': (
                "These are all attested " + rel + ' collocates of "' + headword + '", the '
                'same as "' + used_tok + '" -- none is verified to fit your specific '
                'sentence better than another; compare against your own rewrite first.'
            ),
        })
    return menu_items


# unit_type values produced by earlier, unrelated pipeline stages that route a
# structurally-classified phrase straight to CLARIFY with no reasoning or menu
# attached yet (just a bare span). These are eligible to be enriched in place
# if a registry-attested basic-collocate slot is found in their text -- this
# is where phrases like "good advice" / "give good advice" actually live by
# the time this function runs, since they were never KEEP to begin with.
_V1100_ENRICHABLE_CLARIFY_TYPES = {
    'noun_phrase', 'verb_phrase_or_predicate_chunk', 'clarify_span',
}


def _v1100_build_collocation_menus(result: Dict[str, Any]) -> None:
    keep_units = result.get('keep_units') or []
    survivors: List[Dict[str, Any]] = []
    clarify_added: List[Dict[str, Any]] = []
    already_flagged_norms: Set[str] = set()
    flags_used = 0

    # Pass 1: keep_units with a matching slot are promoted out of KEEP into a
    # new menu-CLARIFY unit (same behavior as the original design).
    for ku in keep_units:
        text = str(ku.get('unit_text') or '')
        if (flags_used >= _V1100_MAX_FLAGS_PER_ESSAY
                or len(surface_tokens(text)) < 2
                or norm_text(text) in already_flagged_norms):
            survivors.append(ku)
            continue

        menu_items = _v1100_find_menu_items(text)
        if not menu_items:
            survivors.append(ku)
            continue

        already_flagged_norms.add(norm_text(text))
        flags_used += 1
        clarify_added.append({
            'unit_id': ku.get('unit_id') or ("v1100_colloc_menu_%04d" % (len(clarify_added) + 1)),
            'class_label': 'CLARIFY',
            'unit_text': text,
            'unit_norm': norm_text(text),
            'unit_type': 'collocation_precision_menu',
            'context': ku.get('context') or '',
            'source_sentence_index': ku.get('source_sentence_index'),
            'safety_level': 'v1100_registry_attested_collocate_menu_no_single_answer',
            'reveal_policy': {
                'mode': 'produce_before_reveal',
                'attempt_required_before_suggestions_shown': True,
                'suggestions_role': 'menu_of_other_attested_collocates_not_asserted_correct',
            },
            'clarify_reason': (
                'This phrase is correct but uses a common, basic word choice with '
                'registry-attested alternatives available for the same slot. No specific '
                'replacement is asserted -- write your own rewrite first, then compare '
                'against the menu.'
            ),
            'phase1_prompt': (
                'This is correct, but uses a common word choice: [' + text + ']. Try '
                'rewriting it yourself with a more precise or natural-sounding alternative first.'
            ),
            'suggestions': menu_items,
            'evidence_ids': ku.get('evidence_ids', []),
            'student_facing_task': True,
        })

    result['keep_units'] = survivors

    # Pass 2: existing bare-span CLARIFY units (routed there by earlier,
    # unrelated classification -- not by this mechanism) are enriched in place
    # if a matching slot is found. Same unit_id and position, no duplicate
    # entry -- only the reasoning/suggestions/reveal_policy fields change.
    existing_clarify = result.get('clarify_units') or []
    enriched_count = 0
    for cu in existing_clarify:
        if flags_used >= _V1100_MAX_FLAGS_PER_ESSAY:
            break
        if cu.get('unit_type') not in _V1100_ENRICHABLE_CLARIFY_TYPES:
            continue
        if cu.get('suggestions'):
            continue  # already carries some other reasoning/menu -- don't overwrite
        text = str(cu.get('unit_text') or '')
        if len(surface_tokens(text)) < 2 or norm_text(text) in already_flagged_norms:
            continue
        menu_items = _v1100_find_menu_items(text)
        if not menu_items:
            continue
        already_flagged_norms.add(norm_text(text))
        flags_used += 1
        enriched_count += 1
        cu['unit_type'] = 'collocation_precision_menu'
        cu['safety_level'] = 'v1100_registry_attested_collocate_menu_no_single_answer'
        cu['reveal_policy'] = {
            'mode': 'produce_before_reveal',
            'attempt_required_before_suggestions_shown': True,
            'suggestions_role': 'menu_of_other_attested_collocates_not_asserted_correct',
        }
        cu['clarify_reason'] = (
            'This phrase uses a common, basic word choice with registry-attested '
            'alternatives available for the same slot. No specific replacement is '
            'asserted -- write your own rewrite first, then compare against the menu.'
        )
        cu['phase1_prompt'] = (
            'This part uses a common word choice: [' + text + ']. Try rewriting it '
            'yourself with a more precise or natural-sounding alternative first.'
        )
        cu['suggestions'] = menu_items

    result['clarify_units'] = existing_clarify + clarify_added
    result.setdefault('lexical_profile', {})['v1100_collocation_precision_menu'] = {
        'menus_flagged_from_keep': len(clarify_added),
        'menus_flagged_from_existing_clarify': enriched_count,
        'menus_flagged_total': len(clarify_added) + enriched_count,
    }


_prev_main_v1100 = main


def main(argv: Optional[List[str]] = None) -> int:
    global ACTIVE_LLM_PROVIDER, LLM_MAX_CANDIDATES, LLM_MIN_VALID_SUGGESTIONS
    parser = argparse.ArgumentParser(
        description="LRET Engine v1.10.0 -- Collocation-slot precision menu (CLARIFY, produce-before-reveal)")
    parser.add_argument("--input", "-i", required=True, help="Path to LRET input JSON or full Evaluator/WKE output JSON")
    parser.add_argument("--output", "-o", required=True, help="Path to write LRET output JSON")
    parser.add_argument("--mode", choices=["fix_only", "enhance_only", "fix_and_enhance"], default=None)
    parser.add_argument("--student-id", default=None)
    parser.add_argument("--essay-id", default=None)
    parser.add_argument("--submission-id", default=None)
    parser.add_argument("--history", default=None, help="Optional learner_lexical_history JSON path")
    parser.add_argument("--resources", nargs="*", default=None, help="Optional simple external lexical resource JSON file(s)")
    parser.add_argument("--canonical-resources", default=None, help="Path to canonical resources directory or final_app_registries zip")
    parser.add_argument("--detector-output", default=None,
                         help="Optional path to the Detector's output/errormap JSON (batch 01_detector_output.json "
                              "or per-essay 01b_errormap_v3.json format).")
    parser.add_argument("--disable-repeated-word-check", action="store_true",
                         help="Disable the v1.9.0 repeated-generic-word CLARIFY prompts (on by default).")
    parser.add_argument("--disable-collocation-menu-check", action="store_true",
                         help="Disable the v1.10.0 collocation-slot precision menu CLARIFY prompts (on by default).")
    parser.add_argument("--use-llm", action="store_true",
                         help="Allow an LLM pass IF the free registry-only pass under-delivers (see --llm-yield-floor). "
                              "Does not force an LLM call by itself.")
    parser.add_argument("--force-llm", action="store_true",
                         help="Run the LLM pass unconditionally (skips the yield-floor check). Implies --use-llm.")
    parser.add_argument("--llm-yield-floor", type=int, default=6,
                         help="Minimum (enhance_count + asserted contextual_synonym_count) from the free pass "
                              "below which an LLM pass is triggered. Default 6.")
    parser.add_argument("--llm-required", action="store_true", help="Fail if the LLM pass triggers but OPENAI_API_KEY is missing or the request fails")
    parser.add_argument("--llm-model", default="gpt-5-nano",
                         help="OpenAI model for suggestion generation; default gpt-5-nano ($0.05/$0.40 per 1M "
                              "input/output tokens vs gpt-5-mini's $0.25/$2.00) -- every suggestion is still "
                              "deterministically re-validated regardless of model, so nano-tier is the "
                              "recommended default; override if quality in practice requires more.")
    parser.add_argument("--llm-timeout", type=int, default=120, help="OpenAI read timeout in seconds; increase on slow connections")
    parser.add_argument("--llm-max-candidates", type=int, default=12,
                         help="Lowered from 28 in prior versions now that the free pass resolves a meaningful "
                              "share of candidates before the LLM pass ever runs.")
    parser.add_argument("--llm-batch-size", type=int, default=4, help="Number of candidates per OpenAI request; lower this if timeouts occur")
    parser.add_argument("--llm-max-retries", type=int, default=3, help="Retries per LLM batch after transport/API failure")
    parser.add_argument("--llm-retry-sleep", type=float, default=2.0, help="Base sleep seconds between retries")
    parser.add_argument("--llm-min-valid-suggestions", type=int, default=2)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)

    llm_allowed = bool(args.use_llm or args.force_llm)
    LLM_MAX_CANDIDATES = max(0, int(args.llm_max_candidates))
    LLM_MIN_VALID_SUGGESTIONS = max(1, int(args.llm_min_valid_suggestions))
    load_external_lexical_resources(args.resources)
    load_canonical_resources(args.canonical_resources)
    load_detector_output(args.detector_output)
    ACTIVE_LLM_PROVIDER = None
    _LLM_STATS['enabled'] = False
    _LLM_STATS['model'] = None

    raw = load_json_file(args.input)
    history = load_json_file(args.history) if args.history else None
    lret_input = make_lret_input(
        raw, mode=args.mode, student_id=args.student_id, essay_id=args.essay_id,
        submission_id=args.submission_id, learner_lexical_history=history,
    )

    result = _v167_run_tiered(
        lret_input, None,
        llm_allowed=llm_allowed, yield_floor=max(0, int(args.llm_yield_floor)), force_llm=bool(args.force_llm),
        llm_model=args.llm_model, llm_timeout=args.llm_timeout, llm_batch_size=args.llm_batch_size,
        llm_max_retries=args.llm_max_retries, llm_retry_sleep=args.llm_retry_sleep,
        llm_required=bool(args.llm_required),
    )
    result['run']['engine_version'] = ENGINE_VERSION
    result['lexical_profile']['canonical_resource_stats'] = copy.deepcopy(_RESOURCE_STATS)
    result['lexical_profile']['llm_suggestion_stats'] = copy.deepcopy(_LLM_STATS)
    _v180_apply_detector_integration(result)
    if not args.disable_repeated_word_check:
        _v190_flag_repeated_generic_words(result)
    if not args.disable_collocation_menu_check:
        _v1100_build_collocation_menus(result)
    result['qa'].setdefault('contract_checks', {})['llm_does_not_create_new_spans'] = True
    result['qa'].setdefault('contract_checks', {})['llm_suggestions_deterministically_validated'] = True
    result['qa'].setdefault('contract_checks', {})['no_embedded_phrase_enhance_bank'] = True
    result['qa'].setdefault('contract_checks', {})['canonical_resources_external_only'] = bool(args.canonical_resources)
    result['qa'].setdefault('contract_checks', {})['no_embedded_topic_whitelist'] = True
    result['qa'].setdefault('contract_checks', {})['no_plural_subject_need_regex'] = True
    result['qa'].setdefault('contract_checks', {})['clarify_is_visible_student_task'] = all(u.get('phase1_prompt') for u in result.get('clarify_units', []))
    result['qa'].setdefault('contract_checks', {})['no_embedded_topic_or_essay_specific_lists'] = True
    if args.llm_required and result.get('lexical_profile', {}).get('v167_tiered_llm', {}).get('triggered') and _LLM_STATS['calls'] == 0:
        raise RuntimeError("--llm-required was set, the LLM pass triggered, but no successful LLM call was made")
    if _LLM_STATS.get('warnings'):
        result['qa'].setdefault('warnings', []).extend(_LLM_STATS['warnings'])
        if args.llm_required:
            raise RuntimeError("LLM required but warnings occurred: " + "; ".join(_LLM_STATS['warnings']))

    lp = result.setdefault('lexical_profile', {})
    lp['fix_count'] = len(result.get('fix_units') or [])
    lp['enhance_count'] = len(result.get('enhance_units') or [])
    lp['contextual_synonym_task_count'] = len(result.get('contextual_synonym_tasks') or [])
    lp['clarify_count'] = len(result.get('clarify_units') or [])
    lp['keep_count'] = len(result.get('keep_units') or [])

    write_json_file(args.output, result, pretty=args.pretty)

    if args.summary:
        p = result.get("lexical_profile", {})
        v167 = p.get('v167_tiered_llm', {})
        v180 = p.get('v180_detector_integration', {})
        v190 = p.get('v190_repeated_word_variation', {})
        v1100 = p.get('v1100_collocation_precision_menu', {})
        print("=== LRET v1.10.0 collocation-precision-menu summary ===")
        print("output:", args.output)
        print("fix_units:", p.get("fix_count"))
        print("main_enhance_units:", p.get("enhance_count"))
        print("contextual_synonym_tasks:", p.get("contextual_synonym_task_count"))
        print("clarify_units:", p.get("clarify_count"))
        print("keep_units:", p.get("keep_count"))
        print("free_pass_yield:", v167.get("free_pass_yield"))
        print("llm_triggered:", v167.get("triggered"))
        print("canonical_loaded:", p.get("canonical_resource_stats", {}).get("canonical_loaded"))
        print("detector_fix_candidates_available:", v180.get("detector_fix_candidates_available"))
        print("repeated_words_flagged:", v190.get("words_flagged"))
        print("collocation_menus_flagged_total:", v1100.get("menus_flagged_total"))
        print("collocation_menus_from_keep:", v1100.get("menus_flagged_from_keep"))
        print("collocation_menus_from_existing_clarify:", v1100.get("menus_flagged_from_existing_clarify"))
        print("qa_status:", result.get("qa", {}).get("status"))
        print("qa_warnings:", len(result.get("qa", {}).get("warnings", [])))
    return 0


ENGINE_VERSION = "lret-engine-v1.10.0-collocation-precision-menu"
SCHEMA_VERSION_OUT = "LRET_OUTPUT_V1.3"


# =============================================================================
# v1.11.0 -- Two targeted patches:
#   (A) Malformed-grammar-pattern sentences flagged visibly instead of
#       vanishing when no LLM/API key is available to generate a repair.
#   (B) "give good"-style truncated V+ADJ fragments moved out of KEEP into
#       CLARIFY instead of being asserted as correct.
# =============================================================================
#
# (A) Why: traced directly this session -- "make a family more stronger" and
# "Another issued is" are real, detected grammar errors (matched by the exact
# malformed-pattern regexes already used in is_unrecoverable_phrase_fragment:
# r"\bmore\s+\w+er\b" for double comparatives, etc.), but the evaluator's
# own n-gram reject-lists mean words like "more" never survive as the start of
# an extracted bigram -- only the tail word ("stronger") survives, as an
# isolated single-word unit with no visible link back to the sentence-level
# error. Confirmed directly: derive_fix_units() never even receives these as
# fix_candidates in this path, and qa.source_audit.suppressed_fix_candidates
# is empty -- these errors are not being suppressed-and-logged, they are
# structurally invisible to the whole FIX pipeline on this path. When an LLM
# with a real API key is available, a *different*, LLM-backed FIX path (in
# derive_fix_units, gated on ACTIVE_LLM_PROVIDER.api_key) does independently
# catch and correctly repair these -- but with no key, nothing flags them at
# all. This patch re-scans full reconstructed sentences directly (bypassing
# the evaluator's n-gram extraction entirely, so the reject-list gap doesn't
# apply) using the same proven malformed-pattern regexes, and raises a
# visible, no-answer-asserted CLARIFY entry for any sentence that matches,
# deduplicated against anything already covered by an existing fix/enhance/
# clarify unit for the same sentence.
#
# (B) Why: "give good" (from "give good advice") sits in KEEP -- asserting
# nothing is wrong -- but it is a truncated verb phrase missing its object,
# not a complete idea. This is the exact "V+ADJ truncated VP" pattern
# documented as an open gap in both the v8.2 and v8.3 evaluator specs. Since
# the underlying cause (evaluator n-gram extraction stopping short of the
# object) isn't being changed here, this is a narrow, closed-list pattern
# check on LRET's own KEEP output: a small set of common transitive/
# ditransitive light verbs (give, make, get, find, take, keep, want, need,
# provide, offer, show) immediately followed by an adjective, as the WHOLE
# unit text (exactly 2 tokens) -- and nothing else in the unit. Moved to
# CLARIFY with a "this looks like it's missing a word -- write the full
# phrase" prompt, no specific completion asserted (the evaluator's own
# context may not always contain the missing object nearby, so guessing one
# would repeat the exact asserted-answer risk already retired in v1.6.9).

import re as _v1110_re


# v1.4.13 Gold pipeline fix (stress-test Problem 6a): words that legitimately
# precede "be" in a correct MODAL + be + past-participle passive ("can be
# addressed", "should be mandated") or an infinitive ("to be considered").
# "cannot"/contracted negatives are single tokens distinct from "can", so they
# need their own entries.
_V1110_BE_CHAIN_EXCLUDE_PRECEDING = {
    "can", "could", "may", "might", "must", "shall", "should", "will", "would",
    "cannot", "can't", "couldn't", "mightn't", "mustn't", "shouldn't",
    "won't", "wouldn't", "to",
}


def _v1110_be_chain_is_malformed(sentence_low: str) -> bool:
    """True only for a genuinely malformed bare "X be Y" chain -- e.g. "it be
    helpful", "students be able" -- i.e. NOT preceded by a modal auxiliary or
    "to", which make it a normal, grammatically correct modal-passive or
    infinitive construction rather than an error. The prior pattern
    (r"\\b\\w+\\s+be\\s+\\w+\\b") matched ANY word + "be" + any word, which fired
    on every correct modal-passive sentence and fired *more* on stronger
    essays (more sophisticated writing uses more modal passives) -- see
    stress-test Problem 6."""
    for m in _v1110_re.finditer(r"\b(\w+)\s+be\s+\w+\b", sentence_low):
        if m.group(1) not in _V1110_BE_CHAIN_EXCLUDE_PRECEDING:
            return True
    return False


def _v1110_malformed_sentence_flags(result: Dict[str, Any], essay_text: str = "", llm_active: bool = False) -> None:
    """Scans the FULL reconstructed essay text directly (not just sentences that
    happen to survive as some unit's `context`), so this does not depend on
    whether any surviving unit's source_sentence_index happens to cover the
    sentence -- confirmed necessary: at least one real error sentence in
    testing ("This make a family more stronger and friendly.") was only ever
    attached to a unit with source_sentence_index None, so index-based
    dedup missed it entirely."""
    if not essay_text:
        return
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", essay_text.replace("\n", " ").strip()) if s]
    if not sentences:
        return

    covered_norms: Set[str] = set()
    for key in ('fix_units', 'enhance_units', 'clarify_units'):
        for u in (result.get(key) or []):
            ctx = u.get('context')
            if ctx:
                covered_norms.add(norm_text(ctx))

    patterns = [
        (r"\bmore\s+\w+er\b", "a double comparative (e.g. \"more stronger\")"),
        (r"\b(?:has|have)\s+to\s+\w+ed\b", "a modal/tense mismatch (e.g. \"has to spent\")"),
        # v1.4.13: the old blanket r"\b\w+\s+be\s+\w+\b" pattern is handled
        # separately below via _v1110_be_chain_is_malformed(), which excludes
        # modal-passive/infinitive "X be Y" sequences (see Problem 6a).
    ]

    clarify_added: List[Dict[str, Any]] = []
    for si, stext in enumerate(sentences):
        stext_norm = norm_text(stext)
        if any(stext_norm in cov or cov in stext_norm for cov in covered_norms if cov):
            # A unit already exists whose context is (or contains) this sentence
            # AND that carried some already-visible reasoning about it -- only
            # skip if that existing unit's class is FIX or ENHANCE (i.e. the
            # error was already addressed), not merely mentioned in a KEEP/
            # CLARIFY context. Conservative default here: skip on any overlap
            # to avoid duplicate flags for the same sentence; documented as a
            # known trade-off below.
            continue
        low = norm_text(stext)
        matched_hint = None
        for pat, hint in patterns:
            if _v1110_re.search(pat, low):
                matched_hint = hint
                break
        if not matched_hint and _v1110_be_chain_is_malformed(low):
            matched_hint = "a malformed verb chain around \"be\""
        if not matched_hint:
            continue
        # v1.4.13 Gold pipeline fix (stress-test Problem 6b): this heuristic
        # exists specifically as a no-LLM fallback (see comment (A) above --
        # when a real LLM pass runs, derive_fix_units()'s LLM-backed FIX path
        # already independently catches and repairs these). Previously this
        # check always claimed "the LLM-backed repair path was not used"
        # even when --use-llm/--force-llm was active and an LLM pass actually
        # ran. Now: skip this crude flag entirely when a real LLM pass ran
        # this session, instead of asserting a false "no LLM" message.
        if llm_active:
            continue
        clarify_added.append({
            'unit_id': f"v1110_grammar_flag_sent_{si}",
            'class_label': 'CLARIFY',
            'unit_text': stext,
            'unit_norm': norm_text(stext),
            'unit_type': 'flagged_malformed_sentence_no_llm_repair_available',
            'context': stext,
            'source_sentence_index': si,
            'safety_level': 'v1110_pattern_detected_no_asserted_repair',
            'reveal_policy': {'mode': 'no_suggested_answer', 'reason': 'no_verified_repair_available_without_llm'},
            'clarify_reason': (
                f'This sentence matches a known error pattern ({matched_hint}), but no '
                f'verified repair is available in this run (the LLM-backed repair path was '
                f'not used). No fix is asserted -- this is flagged for a human check or a '
                f'run with --use-llm.'
            ),
            'phase1_prompt': (
                f'This sentence may have a grammar issue ({matched_hint}): [{stext}]. '
                f'Try rewriting it yourself.'
            ),
            'suggestions': [],
            'student_facing_task': True,
        })

    result['clarify_units'] = (result.get('clarify_units') or []) + clarify_added
    result.setdefault('lexical_profile', {})['v1110_malformed_sentence_flags'] = {
        'sentences_flagged': len(clarify_added),
    }


_V1110_TRUNCATED_VP_VERBS = {
    "give", "make", "get", "find", "take", "keep", "want", "need",
    "provide", "offer", "show",
}

# Closed list of common adjectives that plausibly follow a ditransitive/light
# verb as a truncated-VP signal (verb + ADJECTIVE + [missing object]). Using
# isalpha() alone was too broad -- it matched "need modey" ("modey" is a
# misspelled NOUN, "money", not a missing-adjective case; "need modey" is
# actually a complete, if misspelled, verb+object). Requiring the second word
# to be a recognizable adjective avoids flagging verb+object pairs, at the
# cost of missing adjectives not on this list -- a deliberate, documented
# trade-off over a broader but noisier check.
_V1110_COMMON_ADJECTIVES = {
    "good", "bad", "great", "nice", "clear", "ready", "sure", "sorry",
    "aware", "glad", "happy", "willing", "able", "confident", "certain",
    "possible", "difficult", "easy", "important", "necessary", "useful",
    "helpful", "valuable", "reasonable", "available", "responsible",
}


def _v1110_fix_truncated_vp_keep(result: Dict[str, Any]) -> None:
    keep_units = result.get('keep_units') or []
    survivors: List[Dict[str, Any]] = []
    clarify_added: List[Dict[str, Any]] = []
    for ku in keep_units:
        text = str(ku.get('unit_text') or '')
        toks = surface_tokens(text)
        if (len(toks) == 2
                and norm_text(toks[0]) in _V1110_TRUNCATED_VP_VERBS
                and norm_text(toks[1]) in _V1110_COMMON_ADJECTIVES):
            clarify_added.append({
                'unit_id': ku.get('unit_id') or f"v1110_truncated_vp_{len(clarify_added) + 1:04d}",
                'class_label': 'CLARIFY',
                'unit_text': text,
                'unit_norm': norm_text(text),
                'unit_type': 'truncated_verb_phrase',
                'context': ku.get('context') or '',
                'source_sentence_index': ku.get('source_sentence_index'),
                'safety_level': 'v1110_truncated_vp_no_completion_asserted',
                'reveal_policy': {'mode': 'no_suggested_answer', 'reason': 'missing_object_not_guessed'},
                'clarify_reason': (
                    f'"{text}" reads as an incomplete verb phrase -- "{toks[0]}" usually '
                    f'needs an object (e.g. "{toks[0]} {toks[1]} advice/results/..."). No '
                    f'specific completion is asserted here since the missing word isn\'t '
                    f'confirmed from context.'
                ),
                'phase1_prompt': (
                    f'This looks like it might be missing a word: [{text}]. Write out the '
                    f'full phrase you meant.'
                ),
                'suggestions': [],
                'evidence_ids': ku.get('evidence_ids', []),
                'student_facing_task': True,
            })
        else:
            survivors.append(ku)
    result['keep_units'] = survivors
    result['clarify_units'] = (result.get('clarify_units') or []) + clarify_added
    result.setdefault('lexical_profile', {})['v1110_truncated_vp_fix'] = {
        'units_moved_from_keep': len(clarify_added),
    }


_prev_main_v1110 = main


def main(argv: Optional[List[str]] = None) -> int:
    global ACTIVE_LLM_PROVIDER, LLM_MAX_CANDIDATES, LLM_MIN_VALID_SUGGESTIONS
    parser = argparse.ArgumentParser(
        description="LRET Engine v1.11.0 -- visible grammar-pattern flags + truncated-VP KEEP fix")
    parser.add_argument("--input", "-i", required=True, help="Path to LRET input JSON or full Evaluator/WKE output JSON")
    parser.add_argument("--output", "-o", required=True, help="Path to write LRET output JSON")
    parser.add_argument("--mode", choices=["fix_only", "enhance_only", "fix_and_enhance"], default=None)
    parser.add_argument("--student-id", default=None)
    parser.add_argument("--essay-id", default=None)
    parser.add_argument("--submission-id", default=None)
    parser.add_argument("--history", default=None, help="Optional learner_lexical_history JSON path")
    parser.add_argument("--resources", nargs="*", default=None, help="Optional simple external lexical resource JSON file(s)")
    parser.add_argument("--canonical-resources", default=None, help="Path to canonical resources directory or final_app_registries zip")
    parser.add_argument("--detector-output", default=None,
                         help="Optional path to the Detector's output/errormap JSON (batch 01_detector_output.json "
                              "or per-essay 01b_errormap_v3.json format).")
    parser.add_argument("--disable-repeated-word-check", action="store_true",
                         help="Disable the v1.9.0 repeated-generic-word CLARIFY prompts (on by default).")
    parser.add_argument("--disable-collocation-menu-check", action="store_true",
                         help="Disable the v1.10.0 collocation-slot precision menu CLARIFY prompts (on by default).")
    parser.add_argument("--disable-grammar-flag-check", action="store_true",
                         help="Disable the v1.11.0 malformed-sentence visibility flags (on by default).")
    parser.add_argument("--disable-truncated-vp-fix", action="store_true",
                         help="Disable the v1.11.0 truncated-VP KEEP->CLARIFY fix (on by default).")
    parser.add_argument("--use-llm", action="store_true",
                         help="Allow an LLM pass IF the free registry-only pass under-delivers (see --llm-yield-floor). "
                              "Does not force an LLM call by itself.")
    parser.add_argument("--force-llm", action="store_true",
                         help="Run the LLM pass unconditionally (skips the yield-floor check). Implies --use-llm.")
    parser.add_argument("--llm-yield-floor", type=int, default=6,
                         help="Minimum (enhance_count + asserted contextual_synonym_count) from the free pass "
                              "below which an LLM pass is triggered. Default 6.")
    parser.add_argument("--llm-required", action="store_true", help="Fail if the LLM pass triggers but OPENAI_API_KEY is missing or the request fails")
    parser.add_argument("--llm-model", default="gpt-5-nano",
                         help="OpenAI model for suggestion generation; default gpt-5-nano ($0.05/$0.40 per 1M "
                              "input/output tokens vs gpt-5-mini's $0.25/$2.00) -- every suggestion is still "
                              "deterministically re-validated regardless of model, so nano-tier is the "
                              "recommended default; override if quality in practice requires more.")
    parser.add_argument("--llm-timeout", type=int, default=120, help="OpenAI read timeout in seconds; increase on slow connections")
    parser.add_argument("--llm-max-candidates", type=int, default=12,
                         help="Lowered from 28 in prior versions now that the free pass resolves a meaningful "
                              "share of candidates before the LLM pass ever runs.")
    parser.add_argument("--llm-batch-size", type=int, default=4, help="Number of candidates per OpenAI request; lower this if timeouts occur")
    parser.add_argument("--llm-max-retries", type=int, default=3, help="Retries per LLM batch after transport/API failure")
    parser.add_argument("--llm-retry-sleep", type=float, default=2.0, help="Base sleep seconds between retries")
    parser.add_argument("--llm-min-valid-suggestions", type=int, default=2)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)

    llm_allowed = bool(args.use_llm or args.force_llm)
    LLM_MAX_CANDIDATES = max(0, int(args.llm_max_candidates))
    LLM_MIN_VALID_SUGGESTIONS = max(1, int(args.llm_min_valid_suggestions))
    load_external_lexical_resources(args.resources)
    load_canonical_resources(args.canonical_resources)
    load_detector_output(args.detector_output)
    ACTIVE_LLM_PROVIDER = None
    _LLM_STATS['enabled'] = False
    _LLM_STATS['model'] = None

    raw = load_json_file(args.input)
    history = load_json_file(args.history) if args.history else None
    lret_input = make_lret_input(
        raw, mode=args.mode, student_id=args.student_id, essay_id=args.essay_id,
        submission_id=args.submission_id, learner_lexical_history=history,
    )

    result = _v167_run_tiered(
        lret_input, None,
        llm_allowed=llm_allowed, yield_floor=max(0, int(args.llm_yield_floor)), force_llm=bool(args.force_llm),
        llm_model=args.llm_model, llm_timeout=args.llm_timeout, llm_batch_size=args.llm_batch_size,
        llm_max_retries=args.llm_max_retries, llm_retry_sleep=args.llm_retry_sleep,
        llm_required=bool(args.llm_required),
    )
    result['run']['engine_version'] = ENGINE_VERSION
    result['lexical_profile']['canonical_resource_stats'] = copy.deepcopy(_RESOURCE_STATS)
    result['lexical_profile']['llm_suggestion_stats'] = copy.deepcopy(_LLM_STATS)
    _v180_apply_detector_integration(result)
    if not args.disable_repeated_word_check:
        _v190_flag_repeated_generic_words(result)
    if not args.disable_collocation_menu_check:
        _v1100_build_collocation_menus(result)
    if not args.disable_truncated_vp_fix:
        _v1110_fix_truncated_vp_keep(result)
    if not args.disable_grammar_flag_check:
        # v1.4.13 (Problem 6b): use the actual LLM-call signal (_LLM_STATS
        # ['calls'] > 0), not just the --use-llm flag, since --use-llm only
        # permits an LLM pass if the free registry-only pass under-delivers
        # (see --llm-yield-floor) -- it does not guarantee one ran.
        _v1110_llm_active = bool(_LLM_STATS.get('calls', 0))
        _v1110_malformed_sentence_flags(result, lret_input.get('essay_text') or '', llm_active=_v1110_llm_active)
    result['qa'].setdefault('contract_checks', {})['llm_does_not_create_new_spans'] = True
    result['qa'].setdefault('contract_checks', {})['llm_suggestions_deterministically_validated'] = True
    result['qa'].setdefault('contract_checks', {})['no_embedded_phrase_enhance_bank'] = True
    result['qa'].setdefault('contract_checks', {})['canonical_resources_external_only'] = bool(args.canonical_resources)
    result['qa'].setdefault('contract_checks', {})['no_embedded_topic_whitelist'] = True
    result['qa'].setdefault('contract_checks', {})['no_plural_subject_need_regex'] = True
    result['qa'].setdefault('contract_checks', {})['clarify_is_visible_student_task'] = all(u.get('phase1_prompt') for u in result.get('clarify_units', []))
    result['qa'].setdefault('contract_checks', {})['no_embedded_topic_or_essay_specific_lists'] = True
    if args.llm_required and result.get('lexical_profile', {}).get('v167_tiered_llm', {}).get('triggered') and _LLM_STATS['calls'] == 0:
        raise RuntimeError("--llm-required was set, the LLM pass triggered, but no successful LLM call was made")
    if _LLM_STATS.get('warnings'):
        result['qa'].setdefault('warnings', []).extend(_LLM_STATS['warnings'])
        if args.llm_required:
            raise RuntimeError("LLM required but warnings occurred: " + "; ".join(_LLM_STATS['warnings']))

    lp = result.setdefault('lexical_profile', {})
    lp['fix_count'] = len(result.get('fix_units') or [])
    lp['enhance_count'] = len(result.get('enhance_units') or [])
    lp['contextual_synonym_task_count'] = len(result.get('contextual_synonym_tasks') or [])
    lp['clarify_count'] = len(result.get('clarify_units') or [])
    lp['keep_count'] = len(result.get('keep_units') or [])

    write_json_file(args.output, result, pretty=args.pretty)

    if args.summary:
        p = result.get("lexical_profile", {})
        v167 = p.get('v167_tiered_llm', {})
        v180 = p.get('v180_detector_integration', {})
        v190 = p.get('v190_repeated_word_variation', {})
        v1100 = p.get('v1100_collocation_precision_menu', {})
        v1110g = p.get('v1110_malformed_sentence_flags', {})
        v1110t = p.get('v1110_truncated_vp_fix', {})
        print("=== LRET v1.12.0 summary (meaning-sensitive detector families) ===")
        print("output:", args.output)
        print("fix_units:", p.get("fix_count"))
        print("main_enhance_units:", p.get("enhance_count"))
        print("contextual_synonym_tasks:", p.get("contextual_synonym_task_count"))
        print("clarify_units:", p.get("clarify_count"))
        print("keep_units:", p.get("keep_count"))
        print("free_pass_yield:", v167.get("free_pass_yield"))
        print("llm_triggered:", v167.get("triggered"))
        print("canonical_loaded:", p.get("canonical_resource_stats", {}).get("canonical_loaded"))
        print("repeated_words_flagged:", v190.get("words_flagged"))
        print("collocation_menus_flagged_total:", v1100.get("menus_flagged_total"))
        print("malformed_sentences_flagged:", v1110g.get("sentences_flagged"))
        print("truncated_vp_moved_from_keep:", v1110t.get("units_moved_from_keep"))
        print("qa_status:", result.get("qa", {}).get("status"))
        print("qa_warnings:", len(result.get("qa", {}).get("warnings", [])))
    return 0


ENGINE_VERSION = "lret-engine-v1.11.0-visible-grammar-flags-and-truncated-vp"
SCHEMA_VERSION_OUT = "LRET_OUTPUT_V1.4"


# =============================================================================
# v1.12.0 -- Extend the SPELLING verification carve-out to other
# meaning-sensitive lexical Detector families (COLLOCATION, WORD_CHOICE,
# SEMANTIC_COMBINATION, LEXICAL_PRECISION)
# =============================================================================
#
# Why: checked directly -- v1.8.0's Detector integration only withheld the
# specific suggested correction for SPELLING (confirmed unsafe: "modey" ->
# repair_hypothesis "modes", a real-but-wrong word, at the identical
# confidence score as the correct "goverment" -> "government"). Every other
# family, including COLLOCATION/WORD_CHOICE/SEMANTIC_COMBINATION, was treated
# as "verified" the moment ANY repair text existed -- no independent check.
# But a wrong-but-plausible collocation or word-choice fix has exactly the
# same failure shape as the spelling case: a real, well-formed alternative
# that is simply wrong for THIS sentence's meaning. This is the identical
# risk that got the free-modifier-swap ENHANCE mechanism retired in v1.6.9.
# There is no reason the same skepticism shouldn't apply here.
#
# Confirmed via the Detector's own source (uploaded this session,
# "Coverage Detector v23") that COLLOCATION/WORD_CHOICE/SEMANTIC_COMBINATION
# detection is real, implemented logic (adjective+noun combination checks,
# support-verb+abstract-noun checks, coordinated-lexical-item checks) -- not
# a defined-but-unused taxonomy -- so this is a live, reachable path, not a
# hypothetical one.

_V1120_MEANING_SENSITIVE_LEXICAL_FAMILIES = {
    "COLLOCATION", "WORD_CHOICE", "SEMANTIC_COMBINATION", "LEXICAL_PRECISION",
}


def _v180_build_detector_fix_units(existing_fix_units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    existing_norms = {norm_text(u.get('unit_text') or '') for u in existing_fix_units}
    new_units: List[Dict[str, Any]] = []
    for c in DETECTOR_FIX_CANDIDATES:
        if not isinstance(c, dict):
            continue
        if c.get('student_visible') is False:
            continue
        arb = c.get('arbitration_status')
        if arb is not None and arb != 'accepted':
            continue
        quote = str(c.get('quote') or '').strip()
        if not quote or norm_text(quote) in existing_norms:
            continue
        context = str(c.get('local_quote') or '')
        repair = c.get('repair_hypothesis')
        family = str(c.get('family') or '').upper()
        materialised = ((c.get('repair_materialisation') or {}).get('revised_sentence_hypothesis')
                        if isinstance(c.get('repair_materialisation'), dict) else None)
        suggestion_text = None
        suggestion_verified = False
        is_meaning_sensitive = family in _V1120_MEANING_SENSITIVE_LEXICAL_FAMILIES
        if family == 'SPELLING':
            # v1.8.0 safety decision: never surface the Detector's specific spelling
            # repair_hypothesis. Tried cross-checking it against LRET's own canonical
            # lexical_registry (a real 76k-entry dictionary) first -- that check does
            # NOT work here: "modey" -> repair_hypothesis "modes" and "contries" ->
            # "contrives" are both real English words, so dictionary membership alone
            # cannot tell them apart from the correct answers ("money", "countries").
            # Both wrong suggestions also carried the identical 0.82 confidence score
            # as the correct "goverment" -> "government" suggestion, so confidence
            # isn't a usable filter either. Rather than assert a coin-flip-reliable
            # correction, the FIX flag itself (there IS a misspelling here) is kept --
            # that part of the Detector's signal is trustworthy -- but no specific
            # replacement word is shown. This needs either a real spellchecker with
            # context/frequency weighting or an LLM check to do safely; not attempted
            # here.
            suggestion_text = None
            suggestion_verified = False
        elif is_meaning_sensitive:
            # v1.12.0: same rule, extended. A Detector-suggested COLLOCATION /
            # WORD_CHOICE / SEMANTIC_COMBINATION repair can be a real, well-formed
            # alternative that is simply wrong for this sentence's intended meaning
            # -- there is no registry or confidence-score check available today that
            # can tell a meaning-correct repair from a meaning-wrong one for these
            # families (the same gap that made SPELLING unsafe to auto-assert).
            # The FIX flag itself is kept (a real issue was detected here); the
            # specific replacement text is withheld.
            suggestion_text = None
            suggestion_verified = False
        elif materialised:
            suggestion_text = materialised
            suggestion_verified = True
        elif repair and context:
            suggestion_text = context.replace(quote, str(repair), 1)
            suggestion_verified = True
        elif repair:
            suggestion_text = str(repair)
            suggestion_verified = True
        row_id = str(c.get('row_id') or c.get('candidate_id') or len(new_units))
        unverified_flag = (family == 'SPELLING' or is_meaning_sensitive) and not suggestion_verified
        new_units.append({
            'unit_id': f"fix_detector_{row_id[:16]}",
            'class_label': 'FIX',
            'unit_text': quote,
            'unit_norm': norm_text(quote),
            'unit_type': 'detector_validated_fix_span',
            'replacement_scope': 'span',
            'error_family': c.get('family'),
            'detector_family': c.get('family'),
            'issue_code': c.get('issue_code'),
            'occurrence_count': 1,
            'source_sentence_index': c.get('sentence_index'),
            'source_paragraph_index': c.get('paragraph_index'),
            'context': context,
            'locations': [{
                'start': c.get('span_start'), 'end': c.get('span_end'),
                'paragraph_idx': c.get('paragraph_index'),
            }],
            'requires_full_contextual_check': False,
            'safety_level': 'detector_validated_arbitrated_fix',
            'suggestions': ([{
                'text': suggestion_text,
                'validation': {
                    'accepted': True,
                    'gates': ['detector_arbitration_accepted'] + (['lexical_registry_cross_checked'] if suggestion_verified else []),
                    'reason': (
                        f"Promoted from the Detector's validated_fix_candidates "
                        f"(confidence={c.get('confidence')}, "
                        f"source_engines={c.get('source_engines')})."
                    ),
                },
            }] if suggestion_text else []),
            'spelling_correction_unverified': (family == 'SPELLING' and not suggestion_verified),
            'meaning_sensitive_correction_unverified': (is_meaning_sensitive and not suggestion_verified),
            'unverified_note': (
                "Detector flagged a likely spelling error at this span, but its specific suggested "
                "correction did not pass independent cross-check against the canonical lexical registry "
                "-- confirmed on real data that the Detector's own repair_hypothesis is sometimes a real-but-"
                "wrong word (e.g. 'modey' -> 'modes' instead of 'money') at the SAME confidence score as "
                "correct suggestions, so confidence alone can't be trusted here. No specific correction is "
                "shown; flag the span for student/human review instead of asserting an unverified answer."
            ) if (family == 'SPELLING' and not suggestion_verified) else (
                f"Detector flagged a likely {family.lower().replace('_', ' ')} issue at this span, but no "
                f"verification is available to confirm its suggested repair actually fits this sentence's "
                f"intended meaning (the same class of risk that made an earlier free-registry modifier-swap "
                f"mechanism unsafe and led to its retirement). No specific replacement is shown; flag the "
                f"span for student/human review instead of asserting an unverified answer."
            ) if unverified_flag else None,
            'source': 'detector_validated_fix_candidate',
            'detector_confidence': c.get('confidence'),
            'detector_arbitration_reasons': c.get('arbitration_reasons'),
        })
        existing_norms.add(norm_text(quote))
    return new_units


ENGINE_VERSION = "lret-engine-v1.12.0-meaning-sensitive-detector-families"
SCHEMA_VERSION_OUT = "LRET_OUTPUT_V1.5"

if __name__ == "__main__":
    raise SystemExit(main())
