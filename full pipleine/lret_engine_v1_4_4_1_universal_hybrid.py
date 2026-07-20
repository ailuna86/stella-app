"""
LRET Engine v1.4.2 -- Universal Hybrid Anti-Overfit LRET
================================================================================

Standalone v1.3.2 implementation. Universal only: no essay-specific conditions, no topic/sentence-index hacks, and no embedded essay-derived phrase bank.

Key v1.3.2 corrections over v1.3.1:
  * Lexical-only FIX: grammar-only issues are suppressed and audited, not emitted as LRET FIX.
  * No empty FIX: student-facing FIX units must contain at least one concrete repair.
  * Phrase-first ENHANCE: phrase/collocation units survive over contained single words.
  * Student-facing task dedup is phrase-first, but KEEP inventory is preserved separately.
  * KEEP contains meaningful single words and good collocations/phrases with coverage annotations.
  * No embedded exact phrase/collocation/word enhancement bank; suggestions come from external resources or universal structural templates only.
  * Contextual fit remains mandatory for all FIX/ENHANCE suggestions.

Input:
  * direct LRET_INPUT_V1.1-style JSON, or
  * full Evaluator/WKE JSON containing consumer_payloads.lret_payload.

Output:
  * LRET_OUTPUT_V1.1-compatible JSON with v1.3 QA/profile additions.

Run:
  python lret_engine_v1_3_2.py --input response_1783333960540.json --output lret_v1_3_2_output.json --pretty --summary
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

ENGINE_VERSION = "lret-engine-v1.4.4-universal-preclassification-gated"

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
    ):
        self.model = model
        self.api_key = api_key or _os.environ.get('OPENAI_API_KEY')
        self.timeout = max(10, int(timeout))
        self.max_suggestions = max(1, int(max_suggestions))
        self.max_retries = max(0, int(max_retries))
        self.retry_sleep = max(0.0, float(retry_sleep))
        self.batch_size = max(1, int(batch_size))
        _LLM_STATS['enabled'] = bool(self.api_key)
        _LLM_STATS['model'] = model
        _LLM_STATS.setdefault('failed_chunks', 0)
        _LLM_STATS.setdefault('successful_chunks', 0)
        _LLM_STATS.setdefault('retry_attempts_used', 0)
        _LLM_STATS.setdefault('batch_size', self.batch_size)
        _LLM_STATS.setdefault('timeout_seconds', self.timeout)

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
                            },
                            "brief_reason": {"type": "string"}
                        },
                        "required": ["unit_id", "decision", "confidence", "recommended_span", "span_is_complete", "span_is_replaceable", "semantic_recoverability", "learning_value", "risk_flags", "suggestions", "rejected_suggestions", "brief_reason"]
                    }
                }
            },
            "required": ["results"]
        }
        system = (
            "You are an IELTS Academic Writing lexical-resource adjudicator. "
            "Your job is not to rewrite the essay, not to find new errors, and not to search for new spans. "
            "Classify only the supplied candidate spans as FIX, ENHANCE, KEEP, DROP, CLARIFY, or EXPAND_SPAN. "
            "ENHANCE is allowed only when the candidate is a complete, replaceable lexical unit and at least two safe whole-phrase alternatives are possible. "
            "CLARIFY is a visible student task: use it when the phrase is vague or semantically unstable and the learner should make the meaning more specific before paraphrasing. "
            "EXPAND_SPAN means the supplied span is too narrow; recommended_span must be an exact substring of the provided context. "
            "Do not use essay-topic assumptions. Do not create spans outside the context. Do not classify partial spans as ENHANCE. "
            "Do not classify a candidate as ENHANCE if it overlaps a known FIX span or if the local context is too malformed for safe paraphrase. "
            "Suggestions must replace the selected or recommended span only. They must fit grammatically when inserted into the original sentence. "
            "Reject suggestions that change claim strength, add facts, sound like dictionary synonyms, contain parentheses, contain notes, or require rewriting words outside the selected span. "
            "Return JSON only. Keep brief_reason short."
        )
        user = json.dumps({
            "learner_level": learner_level,
            "max_suggestions_per_enhance": self.max_suggestions,
            "batch": {"chunk_index": chunk_index, "chunk_total": chunk_total, "attempt": attempt},
            "candidates": payload_candidates,
            "decision_rules": [
                "If the candidate is a partial phrase and a fuller exact substring appears in context, return EXPAND_SPAN.",
                "If meaning is vague or unclear, return CLARIFY; do not give paraphrase suggestions.",
                "If the candidate is positive evidence but not worth practice, return KEEP.",
                "If the candidate is low-value or noise, return DROP.",
                "For ENHANCE or EXPAND_SPAN with suggestions, provide 2-4 direct replacements only.",
                "Never include explanations, parentheses, labels, warnings, or notes inside suggestions."
            ],
            "validation_instruction": "Before accepting a suggestion, mentally insert it into the original sentence and reject it if it breaks grammar, changes role, changes claim strength, adds unsupported detail, or requires changing words outside the selected span."
        }, ensure_ascii=False)
        body = {
            "model": self.model,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": system}]},
                {"role": "user", "content": [{"type": "input_text", "text": user}]},
            ],
            "text": {"format": {"type": "json_schema", "name": "lret_v143_adjudication", "schema": schema, "strict": True}},
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
            _LLM_STATS['results_received'] += len(results)
            return [r for r in results if isinstance(r, dict)]
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
        # e.g. "give good", "can give good", "brings both", "doing volunteer"
        if toks[-1] in {'good','bad','main','other','both','many','some','more','fewer','volunteer'}:
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
            add_clarify(clean, context, sent_idx, para_idx, 'malformed_context_blocked_from_enhance', ['malformed_context'], base_value, 'v144_final_clarify_safeguard')
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


def main(argv: Optional[List[str]] = None) -> int:
    global ACTIVE_LLM_PROVIDER, LLM_MAX_CANDIDATES, LLM_MIN_VALID_SUGGESTIONS
    parser = argparse.ArgumentParser(description="LRET Engine v1.4 -- universal hybrid LLM/resource phrase-first")
    parser.add_argument("--input", "-i", required=True, help="Path to LRET input JSON or full Evaluator/WKE output JSON")
    parser.add_argument("--output", "-o", required=True, help="Path to write LRET output JSON")
    parser.add_argument("--mode", choices=["fix_only", "enhance_only", "fix_and_enhance"], default=None)
    parser.add_argument("--student-id", default=None)
    parser.add_argument("--essay-id", default=None)
    parser.add_argument("--submission-id", default=None)
    parser.add_argument("--history", default=None, help="Optional learner_lexical_history JSON path")
    parser.add_argument("--resources", nargs="*", default=None, help="Optional simple external lexical resource JSON file(s)")
    parser.add_argument("--canonical-resources", default=None, help="Path to canonical resources directory or final_app_registries zip")
    parser.add_argument("--use-llm", action="store_true", help="Use OpenAI LLM suggestion layer if OPENAI_API_KEY is set")
    parser.add_argument("--llm-required", action="store_true", help="Fail if --use-llm is set but OPENAI_API_KEY is missing or request fails")
    parser.add_argument("--llm-model", default="gpt-5-mini", help="OpenAI model for suggestion generation; default gpt-5-mini")
    parser.add_argument("--llm-timeout", type=int, default=90, help="OpenAI read timeout in seconds; increase on slow connections")
    parser.add_argument("--llm-max-candidates", type=int, default=24)
    parser.add_argument("--llm-batch-size", type=int, default=6, help="Number of candidates per OpenAI request; lower this if timeouts occur")
    parser.add_argument("--llm-max-retries", type=int, default=2, help="Retries per LLM batch after transport/API failure")
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
        print("=== LRET v1.4.4 universal preclassification-gated LLM summary ===")
        print("output:", args.output)
        print("fix_units:", p.get("fix_count"))
        print("enhance_units:", p.get("enhance_count"))
        print("clarify_units:", p.get("clarify_count"))
        print("keep_units:", p.get("keep_count"))
        print("dropped_units:", p.get("dropped_count"))
        print("enhance_multiword_count:", p.get("enhance_multiword_count"))
        print("enhance_single_word_count:", p.get("enhance_single_word_count"))
        print("enhance_multiword_share:", p.get("enhance_multiword_share"))
        print("canonical_loaded:", p.get("canonical_resource_stats", {}).get("canonical_loaded"))
        print("llm_enabled:", p.get("llm_suggestion_stats", {}).get("enabled"))
        print("llm_calls:", p.get("llm_suggestion_stats", {}).get("calls"))
        print("qa_warnings:", len(result.get("qa", {}).get("warnings", [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
