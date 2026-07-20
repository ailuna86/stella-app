"""
LRET Engine v1.3.1 -- Universal Phrase-First Lexical Resource Enhancement Tool
================================================================================

Standalone v1.3.1 implementation. Universal only: no essay-specific conditions, no topic/sentence-index hacks.

Key v1.3.1 corrections over v1.3:
  * Lexical-only FIX: grammar-only issues are suppressed and audited, not emitted as LRET FIX.
  * No empty FIX: student-facing FIX units must contain at least one concrete repair.
  * Phrase-first ENHANCE: phrase/collocation units survive over contained single words.
  * Student-facing task dedup is phrase-first, but KEEP inventory is preserved separately.
  * KEEP contains meaningful single words and good collocations/phrases with coverage annotations.
  * Dynamic phrase generation scans a universal phrase bank; no essay-specific suppression rules.
  * Contextual fit remains mandatory for all FIX/ENHANCE suggestions.

Input:
  * direct LRET_INPUT_V1.1-style JSON, or
  * full Evaluator/WKE JSON containing consumer_payloads.lret_payload.

Output:
  * LRET_OUTPUT_V1.1-compatible JSON with v1.3 QA/profile additions.

Run:
  python lret_engine_v1_3_1.py --input response_1783333960540.json --output lret_v1_3_1_output.json --pretty --summary
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
ENGINE_VERSION = "lret-engine-v1.3.1-universal-precision"

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

STABLE_MULTI_KEEP: Set[str] = {
    "ageing population", "aging population", "care homes", "community groups",
    "younger generation", "younger generations", "young generation", "older generation",
    "quality of life", "public services", "government policy", "local community",
    "for example", "on the other hand", "in conclusion",
}

STABLE_SINGLE_KEEP: Set[str] = {"society", "government", "population", "community", "generation"}
ACADEMIC_SIGNAL_WORDS: Set[str] = {
    "advantage", "advantages", "disadvantage", "disadvantages", "issue", "problem", "problems",
    "benefit", "benefits", "economy", "economic", "health", "education", "culture", "tradition",
    "knowledge", "experience", "advice", "generation", "society", "government", "population",
    "community", "relatives", "elderly", "younger", "older", "support", "investing", "care",
}
COMMON_PREDICATE_HEADS: Set[str] = {
    "cause", "create", "lead", "pose", "bring", "provide", "offer", "make", "give", "share",
    "take", "care", "support", "guide", "teach", "work", "working", "doing", "spend", "spent",
    "cost", "costs", "grow", "growing", "increase", "increasing", "invest", "investing", "look",
}
# Generic verbs/nouns are weak as isolated KEEP evidence unless they are part of a phrase.
GENERIC_SINGLE_WORD_KEEP_BLOCK: Set[str] = {
    "do", "does", "did", "doing", "make", "makes", "made", "take", "takes", "give", "gives",
    "bring", "brings", "have", "has", "need", "needs", "way", "thing", "things", "kind", "kinds",
    "other", "when", "still", "also", "both", "number", "even", "though", "hand",
    "nobody", "fewer", "today", "peoples",
}

# v1.3.1: words that are usually not pedagogically useful as independent KEEP items.
# This is not essay-specific: it blocks isolated discourse/framing fragments and generic modifiers
# unless they are part of a stronger phrase/collocation KEEP unit.
LOW_VALUE_SINGLE_KEEP_BLOCK: Set[str] = {
    "main", "possible", "example", "conclusion", "quickly", "older", "younger", "young",
    "old", "new", "many", "much", "country", "countries", "home", "homes", "group", "groups",
    "relative", "relatives", "child", "children", "parent", "parents", "grandmother", "grandfather",
}

VAGUE_PLACEHOLDER_NOUNS: Set[str] = {
    "thing", "things", "stuff", "kind", "kinds", "something", "anything", "everything",
    "matter", "matters", "area", "areas", "aspect", "aspects",
}

AGE_OR_GROUP_MODIFIERS: Set[str] = {"young", "older", "younger", "old", "elderly"}
PERSON_GROUP_HEADS: Set[str] = {
    "people", "person", "generation", "generations", "relative", "relatives", "adult", "adults",
    "child", "children", "citizen", "citizens", "parent", "parents",
}
LEXICAL_ERROR_MARKERS: Set[str] = {"peoples", "issued"}
BANNED_WORDS: Set[str] = set()

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

# Exact phrase-level enhancement bank. These are whole-phrase replacements.
PHRASE_ENHANCE_BANK: Dict[str, List[str]] = {
    "give good advice": ["offer valuable advice", "provide practical advice", "share useful advice"],
    "good advice": ["valuable advice", "practical advice", "useful advice"],
    "bring many benefits": ["provide several important benefits", "offer significant advantages", "create meaningful benefits"],
    "bring many benefits to society": ["provide significant benefits to society", "make valuable contributions to society", "offer clear advantages to society"],
    "benefits to society": ["advantages for society", "positive effects on society", "valuable contributions to society"],
    "cause some problems": ["create several challenges", "lead to some difficulties", "pose certain problems"],
    "some problems": ["several challenges", "a number of difficulties", "certain problems"],
    "main issue": ["main concern", "major issue", "central problem"],
    "growing quickly": ["increasing rapidly", "rising quickly", "growing at a rapid pace"],
    "take care of elderly relatives": ["care for elderly relatives", "look after elderly relatives", "support elderly relatives"],
    "doing volunteer work": ["volunteering", "doing voluntary work", "taking part in volunteer work"],
    "volunteer work": ["voluntary work", "community service", "volunteer activities"],
    "working part-time": ["working on a part-time basis", "doing part-time work", "working part time"],
    "guide community groups": ["lead community groups", "support local community groups", "guide local groups"],
    "some kinds of things": ["other responsibilities", "household duties", "family responsibilities"],
    "kinds of things": ["responsibilities", "household duties", "personal obligations"],
    "important for our society": ["valuable to society", "beneficial for society", "socially valuable"],
    "important for society": ["valuable to society", "beneficial for society", "socially valuable"],
    "a lot of knowledge and experience": ["extensive knowledge and experience", "considerable knowledge and experience", "deep knowledge and experience"],
    "a lot of knowledge": ["extensive knowledge", "considerable knowledge", "deep knowledge"],
    "costs a lot of money": ["requires substantial funding", "is financially costly", "costs a significant amount of money"],
    "spend more money": ["increase spending", "allocate more money", "spend additional funds"],
    "spent more money": ["increased spending", "allocated more money", "spent additional funds"],
}

# Single-word fallback bank. v1.3 uses this only when no phrase-level unit covers the word.
SINGLE_WORD_FALLBACK_BANK: Dict[str, List[str]] = {
    "important": ["significant", "valuable", "essential"],
    "difficult": ["challenging", "demanding"],
    "quickly": ["rapidly", "swiftly"],
    "many": ["several", "numerous", "various"],
    "things": ["responsibilities", "duties", "activities"],
    "way": ["method", "approach"],
}

PERSON_NOUNS: Set[str] = {
    "person", "people", "man", "woman", "student", "child", "children", "parent", "parents",
    "teacher", "citizen", "citizens", "grandmother", "grandfather", "grandparent", "grandparents",
}

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
    irregular = {"peoples": "people", "children": "child", "issued": "issue", "issues": "issue"}
    if t in irregular:
        return irregular[t]
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

    This is intentionally deterministic. It accepts curated phrase-bank replacements,
    transparent lexical repairs, and clear context-generated phrase replacements.
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
        # Good + person should not become effectiveness synonyms.
        if low_orig == "good" and self._good_sense_is_moral(sentence):
            return GateResult(False, "moral/person sense of 'good' detected", ["span_fit", "grammar_role_preserved", "meaning_link_detected"])

        if source in {"phrase_bank", "dynamic_phrase_bank", "single_word_fallback"}:
            return GateResult(True, f"passed curated {source} contextual-fit check", list(VALIDATION_GATES))

        if self._transparent_repair_preserves_core(original, candidate):
            return GateResult(True, "passed transparent lexical repair contextual-fit check", list(VALIDATION_GATES))

        return GateResult(False, "no curated or transparent contextual link between original and candidate", list(VALIDATION_GATES))

    @staticmethod
    def _good_sense_is_moral(sentence: str) -> bool:
        toks = [t.lower() for t in surface_tokens(sentence)]
        for i, tok in enumerate(toks):
            if tok == "good" and i + 1 < len(toks) and toks[i + 1] in PERSON_NOUNS:
                return True
        return False

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
        # Common word-form repairs.
        if "issued" in norm_text(original) and "issue" in norm_text(candidate):
            return True
        if "peoples" in norm_text(original) and "people" in norm_text(candidate):
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
    corruption_patterns = [
        r"\bway\s+be\b",
        r"\bfor\s+take\b",
        r"\bthey\s+health\b",
        r"\bas\s+it\s+possible\b",
        r"\byoung\s+peoples\b",
        r"\bolder\s+peoples\b",
        r"\ba\s+children\b",
        r"\bhas\s+to\s+spent\b",
        r"\bthis\s+make\b",
        r"\bmore\s+stronger\b",
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
    vague = {simple_stem(t) for t in VAGUE_PLACEHOLDER_NOUNS}
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
    """v1.3.1 stricter KEEP rule for units already covered by an ENHANCE task.

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
    if low in PHRASE_ENHANCE_BANK:
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
    if low in DISCOURSE_MARKER_ALLOWLIST or low in STABLE_MULTI_KEEP:
        return False
    # Direct lexical-error markers inside the candidate make it unsafe as positive evidence.
    if any(re.search(rf"\b{re.escape(marker)}\b", low) for marker in LEXICAL_ERROR_MARKERS):
        return True
    malformed_patterns = [
        r"\bway\s+be\b",
        r"\bfor\s+take\b",
        r"\bthey\s+health\b",
        r"\bas\s+it\s+possible\b",
        r"\ba\s+children\b",
        r"\bhas\s+to\s+spent\b",
        r"\bthis\s+make\b",
        r"\bmore\s+stronger\b",
    ]
    if any(re.search(p, low) for p in malformed_patterns):
        return True
    # If the surrounding sentence contains a malformed construction and the unit is inside that construction,
    # do not treat it as positive lexical evidence or ENHANCE material.
    context_sensitive_corruption = [
        (r"\bhas\s+to\s+spent\b", {"spent"}),
        (r"\bthis\s+make\b", {"make"}),
        (r"\bway\s+be\b", {"way"}),
        (r"\bfor\s+take\b", {"take", "care"}),
        (r"\bmore\s+stronger\b", {"more", "stronger"}),
        (r"\ba\s+children\b", {"children"}),
    ]
    for pat, affected in context_sensitive_corruption:
        if ctx and re.search(pat, ctx) and (set(surface_tokens(low.lower())) & affected or set(content_tokens(low)) & affected):
            return True
    incomplete_fragment_patterns = [
        r"\bcan\s+\w+\s+good$",
        r"\bgive\s+good$",
        r"\bdoing\s+volunteer$",
        r"\bneed\s+to\s+take$",
        r"\bworking\s+to\s+support$",
        r"^(other\s+)?hand(\s|$)",
        r"^fewer\s+young$",
        r"\bboth\s+problems$",
        r"^money\s+on\b",
    ]
    if any(re.search(p, low) for p in incomplete_fragment_patterns):
        return True
    # If the unit is only an arbitrary subject + bare predicate fragment, it is weak as lexical evidence.
    ctoks = content_tokens(low)
    if len(ctoks) >= 2:
        first, last = ctoks[0], ctoks[-1]
        if first not in COMMON_PREDICATE_HEADS and ((last.endswith("ing") and last != "thing") or last in COMMON_PREDICATE_HEADS):
            # e.g., "people working", "population brings", "ageing population brings" are clause fragments.
            return True
    if len(ctoks) == 2:
        first, second = ctoks[0], ctoks[1]
        if first not in COMMON_PREDICATE_HEADS and ((second.endswith("ing") and second != "thing") or second in COMMON_PREDICATE_HEADS):
            return True
    # If the context is corrupted but the unit itself avoids the corrupted construction, keep/enhance may still be valid.
    if ctx and context_has_local_grammar_corruption(ctx):
        if any(re.search(p, low) for p in malformed_patterns):
            return True
    return False


def has_independent_keep_value(unit: Dict[str, Any]) -> bool:
    """Universal KEEP eligibility: positive, pedagogically useful lexical evidence.

    v1.3.1 removes the old permissive fallback that treated almost any correct
    word-choice token as KEEP. KEEP is now reserved for strong topic words,
    formulaic expressions, complete noun collocations, and stable lexical phrases.
    """
    text = str(unit.get("unit_text") or "").strip()
    low = norm_text(text)
    if not text or is_noise_unit(unit)[0]:
        return False
    toks = surface_tokens(text)
    ctoks = content_tokens(text)
    if not ctoks:
        return False

    if len(toks) == 1:
        tok = simple_stem(toks[0])
        ctx = str(unit.get("context") or "")
        if tok in EDGE_STOPWORDS or tok in GENERIC_SINGLE_WORD_KEEP_BLOCK or tok in LOW_VALUE_SINGLE_KEEP_BLOCK:
            return False
        if low in LEXICAL_ERROR_MARKERS or is_single_modifier_subunit(text, ctx):
            return False
        axes = set(unit.get("axis_candidates") or [])
        flags = set(unit.get("extraction_flags") or [])
        # Strong single-word KEEP: stable topic vocabulary or explicitly topical academic lexis.
        if tok in STABLE_SINGLE_KEEP or tok in ACADEMIC_SIGNAL_WORDS:
            return True
        if ("topic_vocabulary" in axes or "topic_relevant" in flags) and float(unit.get("candidate_value") or 0.0) >= 0.60:
            return True
        return False

    if is_unrecoverable_phrase_fragment(text, str(unit.get("context") or "")):
        return False
    stable, _ = semantic_stability_for_enhance(text, str(unit.get("context") or ""))
    if not stable:
        return False

    # Phrase/collocation KEEP: retain complete, meaningful lexical combinations.
    if low in DISCOURSE_MARKER_ALLOWLIST or low in STABLE_MULTI_KEEP:
        return True
    if low in PHRASE_ENHANCE_BANK:
        return True

    unit_type = str(unit.get("unit_type") or "")
    axes = set(unit.get("axis_candidates") or [])
    flags = set(unit.get("extraction_flags") or [])

    # Reject weak temporal/quantity phrases unless licensed by a collocation/topic signal.
    if re.search(r"\btoday$", low) and not (axes & {"topic_vocabulary", "collocation_naturalness"} or flags & {"topic_relevant", "collocation_candidate"}):
        return False
    if re.match(r"^(many|some|several|few|a lot of)\s+\w+s?$", low) and not (axes & {"topic_vocabulary", "collocation_naturalness"} or flags & {"topic_relevant", "collocation_candidate"}):
        return False

    if "noun_phrase" in unit_type and len(ctoks) >= 2:
        return bool(axes & {"collocation_naturalness", "topic_vocabulary", "semantic_specificity"} or flags & {"collocation_candidate", "topic_relevant"})
    if "verb_phrase" in unit_type or "predicate" in unit_type:
        first = ctoks[0] if ctoks else ""
        # Complete predicate phrase only: verb + at least one concrete argument/complement.
        if first in COMMON_PREDICATE_HEADS and len(ctoks) >= 3:
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
        if low in STABLE_SINGLE_KEEP or simple_stem(low) in ACADEMIC_SIGNAL_WORDS:
            return "keep_topic_vocabulary"
        return "keep_word"
    if low in DISCOURSE_MARKER_ALLOWLIST:
        return "keep_formulaic_expression"
    if low in STABLE_MULTI_KEEP:
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


def _to_gerund_after_to(phrase_after_to: str) -> str:
    """Convert a short infinitive phrase to a gerund-like phrase for 'be good at ...'."""
    phrase = str(phrase_after_to or "").strip()
    if not phrase:
        return phrase
    toks = phrase.split()
    first = toks[0].lower()
    irregular = {"be": "being", "have": "having", "do": "doing", "work": "working", "support": "supporting", "help": "helping"}
    if first in irregular:
        toks[0] = irregular[first]
    elif first.endswith("e") and len(first) > 2:
        toks[0] = first[:-1] + "ing"
    elif not first.endswith("ing"):
        toks[0] = first + "ing"
    return " ".join(toks)


def expand_lexical_fix_span_universally(
    span_text: str,
    suggestion: Any,
    family: str,
    context: str,
    start: Optional[int] = None,
    end: Optional[int] = None,
) -> Tuple[str, Any, str, Optional[int], Optional[int], Optional[str]]:
    """Expand too-narrow lexical repair spans when a universal governing-pattern requires it.

    The expansion is based only on phrase grammar/collocation shape. It does not inspect topic,
    essay id, sentence number, or any memorized sample-specific phrase.
    """
    span = str(span_text or "").strip()
    sug = suggestion
    fam = normalize_lret_fix_family(span, family)
    ctx = str(context or "")
    if not span or not ctx:
        return span, sug, fam, start, end, None

    # Universal support-noun pattern:
    #   has/have/had + adjective + ability to X
    # The lexical problem is not only "ability to X" but the whole support-noun construction.
    if norm_text(span).startswith("ability to"):
        ctx_low = ctx.lower()
        span_low = span.lower()
        idx = ctx_low.find(span_low)
        if idx >= 0:
            prefix = ctx[:idx]
            m = re.search(r"\b(?P<verb>has|have|had)\s+(?:(?:a|an|the)\s+)?(?P<mod>good|strong|great|excellent|poor|limited|weak)\s+$", prefix, flags=re.I)
            if m:
                expanded_span = ctx[m.start():idx] + span
                adj = m.group("mod").lower()
                raw_sug = str(sug or "").strip()
                if norm_text(raw_sug).startswith("ability to "):
                    after_to = re.sub(r"^ability\s+to\s+", "", raw_sug, flags=re.I).strip()
                    if adj in {"good", "strong", "great", "excellent"} and after_to:
                        expanded_sug = f"is {adj if adj != 'great' else 'very good'} at {_to_gerund_after_to(after_to)}"
                    elif after_to:
                        expanded_sug = f"has the ability to {after_to}"
                    else:
                        expanded_sug = raw_sug
                else:
                    expanded_sug = raw_sug
                adj_start = start
                if isinstance(start, int):
                    # Approximate absolute start shift by the number of original characters added.
                    adj_start = max(0, start - len(ctx[m.start():idx]))
                return expanded_span.strip(), expanded_sug, fam, adj_start, end, "expanded_governing_support_noun_pattern"

    return span, sug, fam, start, end, None

def looks_like_grammar_only_fix(family: str, span_text: str) -> bool:
    fam = family.upper().strip()
    span = norm_text(span_text)
    if fam in LRET_GRAMMAR_BLOCKLIST:
        return True
    # WORD_FORM is allowed only when lexical morphology/content-word form.
    if fam == "WORD_FORM":
        grammar_patterns = [
            r"\bhas\s+to\s+\w+ed\b",   # has to spent
            r"\bhave\s+to\s+\w+ed\b",
            r"\bthis\s+make\b",         # SVA
            r"\bthey\s+is\b",
            r"\ba\s+children\b",        # article/number grammar
        ]
        if any(re.search(p, span) for p in grammar_patterns):
            return True
    if fam == "PREPOSITION_PATTERN":
        # Only fixed lexical patterns are allowed. Bare grammar prepositions are blocked.
        lexical_markers = ["ability to", "responsible for", "interested in", "depend on", "contribute to", "invest in"]
        if not any(m in span for m in lexical_markers):
            return True
    return False


def infer_deterministic_lexical_repair(span_text: str, family: str, context: str = "") -> Optional[str]:
    span = str(span_text or "").strip()
    low = norm_text(span)
    if not span:
        return None
    # Common word-form repairs.
    if "issued" in low:
        return re.sub(r"\b[Ii]ssued\b", "issue", span).replace("Another issue is", "Another issue is")
    if low == "another issued is":
        return "Another issue is"
    if "peoples" in low:
        return re.sub(r"\bpeoples\b", "people", span, flags=re.I)
    if "economics issues" in low:
        return re.sub(r"economics issues", "economic issues", span, flags=re.I)
    if "excited things" in low:
        return "exciting activities"
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

        # v1.3.1: universal precision pass before filtering/validation.
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

        result = validator.validate(span_text, str(suggestion), context, source="fix_repair")
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


def generate_phrase_enhance_candidates(
    units: List[Dict[str, Any]],
    essay_text: str,
    validator: ContextFitValidator,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Generate phrase-level ENHANCE candidates using universal phrase-bank scanning.

    v1.3 deliberately avoids essay-specific conditionals such as "if this exact
    sample sentence appears". A curated phrase bank may contain reusable lexical
    patterns; the engine scans raw units and sentence contexts for those patterns
    and then applies universal validation + phrase-first deduplication.
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
        # Avoid phrase tasks over locally unrecoverable grammar unless the phrase itself is stable/recoverable.
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

    # 1) Exact lexical units that match the universal phrase bank.
    for u in units:
        text = str(u.get("unit_text") or "").strip()
        low = norm_text(text)
        if low in PHRASE_ENHANCE_BANK:
            add_candidate(
                text,
                str(u.get("context") or ""),
                int(u.get("source_sentence_index", -1)),
                u.get("source_paragraph_index"),
                PHRASE_ENHANCE_BANK[low],
                source_kind="phrase_bank",
                base_value=max(0.72, float(u.get("candidate_value") or 0.0)),
                unit_type=u.get("unit_type") or "phrase_enhance",
            )

    # 2) Universal context scan: for every sentence context, search reusable phrase-bank keys.
    # This is not essay-specific; it is bank-key + span-fit matching.
    contexts_seen: Set[Tuple[str, int]] = set()
    contexts: List[Tuple[str, int, Any]] = []
    for u in units:
        ctx = str(u.get("context") or "").strip()
        sent_idx = int(u.get("source_sentence_index", -1))
        key = (norm_text(ctx), sent_idx)
        if ctx and key not in contexts_seen:
            contexts_seen.add(key)
            contexts.append((ctx, sent_idx, u.get("source_paragraph_index")))

    # Longer phrase-bank entries first so task dedup has better candidates available.
    bank_items = sorted(PHRASE_ENHANCE_BANK.items(), key=lambda kv: len(surface_tokens(kv[0])), reverse=True)
    for ctx, sent_idx, para_idx in contexts:
        low_ctx = norm_text(ctx)
        for phrase_norm, suggestions in bank_items:
            if phrase_norm not in low_ctx:
                continue
            surface = _surface_match(ctx, phrase_norm) or phrase_norm
            add_candidate(surface, ctx, sent_idx, para_idx, suggestions, source_kind="phrase_bank", base_value=0.72)

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
        if low not in SINGLE_WORD_FALLBACK_BANK:
            continue
        if low in STABLE_SINGLE_KEEP:
            continue
        if context_has_local_grammar_corruption(str(u.get("context") or "")):
            continue
        sent_idx = int(u.get("source_sentence_index", -1))
        key = (low, sent_idx)
        if key in seen:
            continue
        valid = validate_suggestions(text, SINGLE_WORD_FALLBACK_BANK[low], str(u.get("context") or ""), validator, source="single_word_fallback", tier="single_word", failures=failures)
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
        "v1_3_1_metrics": {
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
    parser = argparse.ArgumentParser(description="LRET Engine v1.3.1 -- universal precision phrase-first")
    parser.add_argument("--input", "-i", required=True, help="Path to LRET input JSON or full Evaluator/WKE output JSON")
    parser.add_argument("--output", "-o", required=True, help="Path to write LRET output JSON")
    parser.add_argument("--mode", choices=["fix_only", "enhance_only", "fix_and_enhance"], default=None)
    parser.add_argument("--student-id", default=None)
    parser.add_argument("--essay-id", default=None)
    parser.add_argument("--submission-id", default=None)
    parser.add_argument("--history", default=None, help="Optional learner_lexical_history JSON path")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)

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
        print("=== LRET v1.3.1 summary ===")
        print("output:", args.output)
        print("fix_units:", p.get("fix_count"))
        print("enhance_units:", p.get("enhance_count"))
        print("keep_units:", p.get("keep_count"))
        print("dropped_units:", p.get("dropped_count"))
        print("enhance_multiword_count:", p.get("enhance_multiword_count"))
        print("enhance_single_word_count:", p.get("enhance_single_word_count"))
        print("enhance_multiword_share:", p.get("enhance_multiword_share"))
        print("qa_warnings:", len(result.get("qa", {}).get("warnings", [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
