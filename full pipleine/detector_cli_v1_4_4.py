#!/usr/bin/env python3
"""
VA / ST.ELLA Detector CLI v1.4.4 — standalone metadata-complete bridge
===================================================

Standalone detector-style CLI. It imports no previous detector versions.
It is intended as a runnable CLI component for the Gold orchestrator while the
production Detector service/CLI is finalized.

Boundary:
- This file is a Detector component, not the Gold full pipeline.
- It uses universal, topic-independent surface rules only.
- It does not score IELTS bands.
- It does not generate teaching missions, LRET labels, or revision plans.
- It does not contain essay-specific topic patterns.

Input:  one essay JSON or batch JSON with essays[].
Output: detector-compatible JSON with results[].student_rows and scorer_payload.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ENGINE_ID = "VA_STELLA_DETECTOR_CLI_STANDALONE"
ENGINE_VERSION = "1.4.4-universal-surface-rules-metadata-complete"
SCHEMA_VERSION = "DETECTOR_OUTPUT_STANDALONE_V1_4_4"

CAPACITY_BY_FAMILY = {
    "G_VERB_FORM": "sentence_control",
    "G_VERB_PATTERN": "sentence_control",
    "G_NOUN_NUMBER": "sentence_control",
    "G_ARTICLE": "sentence_control",
    "G_SV_AGREEMENT": "sentence_control",
    "G_COMPARATIVE_FORM": "sentence_control",
    "G_SPACING": "sentence_control",
    "G_COMMA_TRANSITION": "sentence_control",
    "G_MISSING_VERB": "sentence_control",
    "L_REPETITION": "lexical_precision",
    "L_INFORMAL_VOCAB": "academic_style",
    "L_LIMITED_VOCAB": "lexical_precision",
    "C_SIMPLE_CONNECTORS": "cohesion_control",
    "A_UNDERDEVELOPED": "argument_development",
    "A_OVERGENERALIZATION": "argument_development",
    "S_INFORMAL_TONE": "academic_style",
    "S_HEDGING": "academic_style",
}

CRITERION_BY_FAMILY = {
    "G_VERB_FORM": "grammar",
    "G_VERB_PATTERN": "grammar",
    "G_NOUN_NUMBER": "grammar",
    "G_ARTICLE": "grammar",
    "G_SV_AGREEMENT": "grammar",
    "G_COMPARATIVE_FORM": "grammar",
    "G_SPACING": "grammar",
    "G_COMMA_TRANSITION": "grammar",
    "G_MISSING_VERB": "grammar",
    "L_REPETITION": "lexical_resource",
    "L_INFORMAL_VOCAB": "lexical_resource",
    "L_LIMITED_VOCAB": "lexical_resource",
    "C_SIMPLE_CONNECTORS": "cohesion_coherence",
    "A_UNDERDEVELOPED": "argumentation",
    "A_OVERGENERALIZATION": "argumentation",
    "S_INFORMAL_TONE": "academic_style",
    "S_HEDGING": "academic_style",
}

MESSAGE_BY_FAMILY = {
    "G_VERB_FORM": "Check the verb form after modal or semi-modal expressions.",
    "G_VERB_PATTERN": "Check the verb pattern after this preposition or structure.",
    "G_NOUN_NUMBER": "Check plural and singular noun forms.",
    "G_ARTICLE": "Check article use with singular/plural nouns.",
    "G_SV_AGREEMENT": "Check subject-verb agreement.",
    "G_COMPARATIVE_FORM": "Check comparative adjective form.",
    "G_SPACING": "Remove unnecessary spacing before punctuation.",
    "G_COMMA_TRANSITION": "Add punctuation after an introductory transition where needed.",
    "G_MISSING_VERB": "Check whether the clause needs a main verb.",
    "L_REPETITION": "Reduce unnecessary repetition where it weakens precision.",
    "L_INFORMAL_VOCAB": "Use a more formal or precise expression in academic writing.",
    "L_LIMITED_VOCAB": "Use a more precise lexical choice where meaning is too general.",
    "C_SIMPLE_CONNECTORS": "Use linking devices accurately and avoid mechanical repetition.",
    "A_UNDERDEVELOPED": "Develop this idea with explanation or support.",
    "A_OVERGENERALIZATION": "Qualify broad claims or support them with clearer evidence.",
    "S_INFORMAL_TONE": "Rewrite this part in a more academic style.",
    "S_HEDGING": "Use cautious wording for broad claims where appropriate.",
}

BASIC_CONNECTORS = {"and", "but", "so", "because", "also", "for example"}
INTRO_TRANSITIONS = ["for example", "for instance", "in conclusion", "on the other hand", "in addition", "therefore", "however"]
INFORMAL_EXPRESSIONS = ["a lot", "things", "everything", "really", "very good", "kids", "stuff"]
VAGUE_WORDS = {"thing", "things", "good", "bad", "nice", "big", "small", "many", "different"}
COMMON_CONTENT_STOPWORDS = {
    "the", "and", "that", "this", "with", "from", "have", "has", "had", "was", "were", "are", "is", "to", "of", "in", "on", "for", "as", "it", "they", "them", "their", "there", "which", "will", "can", "may", "not", "more", "very", "also", "some", "many", "much", "than", "then", "too", "all"
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, data: Any, pretty: bool = False) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2 if pretty else None)
        f.write("\n")


def normalize_submission(raw: Any, essay_index: int = 0) -> Dict[str, Any]:
    if isinstance(raw, dict) and isinstance(raw.get("essays"), list):
        essays = raw.get("essays") or []
        if not essays:
            raise ValueError("essays[] is empty")
        rec = dict(essays[essay_index])
    elif isinstance(raw, dict):
        rec = dict(raw)
    else:
        raise ValueError("input must be JSON object or {essays:[...]}")
    essay_text = str(rec.get("essay_text") or rec.get("text") or "").strip()
    prompt_text = str(rec.get("prompt_text") or rec.get("prompt") or "").strip()
    if not essay_text:
        raise ValueError("missing essay_text")
    return {
        "essay_id": str(rec.get("essay_id") or "essay_001"),
        "student_id": str(rec.get("student_id") or "student_unknown"),
        "task_type": str(rec.get("task_type") or "WT2"),
        "prompt_text": prompt_text,
        "essay_text": essay_text,
    }


def sentence_spans(text: str) -> List[Tuple[int, int, str]]:
    # Simple robust segmentation: split after terminal punctuation or paragraph breaks.
    spans: List[Tuple[int, int, str]] = []
    pattern = re.compile(r"[^.!?\n]+(?:[.!?]+|$)", re.M)
    for m in pattern.finditer(text):
        sent = m.group(0).strip()
        if sent:
            spans.append((m.start(), m.end(), sent))
    if not spans and text.strip():
        spans.append((0, len(text), text.strip()))
    return spans


def paragraph_spans(text: str) -> List[Tuple[int, int, str]]:
    """Return non-empty paragraph spans split on blank lines.

    This is metadata only. It does not evaluate paragraph quality.
    """
    spans: List[Tuple[int, int, str]] = []
    if not text.strip():
        return spans
    pattern = re.compile(r"[^\n]+(?:\n(?!\n)[^\n]+)*", re.M)
    for m in pattern.finditer(text):
        para = m.group(0).strip()
        if para:
            spans.append((m.start(), m.end(), para))
    if not spans:
        spans.append((0, len(text), text.strip()))
    return spans


def count_words(text: str) -> int:
    return len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text or ""))


def build_length_metadata(text: str) -> Dict[str, Any]:
    """Metadata for scorer/evaluator routing.

    Detector is the authoritative first runtime component that sees the essay text,
    so it must export word/paragraph/sentence counts in fields that downstream
    scorers can read directly.
    """
    sents = sentence_spans(text)
    paras = paragraph_spans(text)
    return {
        "word_count": count_words(text),
        "sentence_count": len(sents),
        "paragraph_count": len(paras),
        "character_count": len(text or ""),
        "non_empty_paragraph_count": len(paras),
        "metadata_source": "detector_cli_v1_4_4_from_essay_text",
        "metadata_quality": {
            "word_count_positive": count_words(text) > 0,
            "sentence_count_positive": len(sents) > 0,
            "paragraph_count_positive": len(paras) > 0,
            "length_metadata_complete": count_words(text) > 0 and len(sents) > 0 and len(paras) > 0,
        },
    }


def build_metric_profile_shared(length_meta: Dict[str, Any]) -> Dict[str, Any]:
    """Shared metadata in the exact locations used by the premium scorer adapter."""
    return {
        "word_count": int(length_meta.get("word_count") or 0),
        "sentence_count": int(length_meta.get("sentence_count") or 0),
        "paragraph_count": int(length_meta.get("paragraph_count") or 0),
        "task_schema_status": "complete",
        "task_schema_confidence": 0.72,
    }


def stable_id(prefix: str, *parts: Any) -> str:
    h = hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{h}"


def add_row(rows: List[Dict[str, Any]], essay_id: str, sent_idx: int, sentence: str, base_start: int, quote: str, family: str, severity: str = "medium", confidence: str = "medium", suggested_revision: Optional[str] = None) -> None:
    if not quote:
        return
    local_pos = sentence.lower().find(quote.lower())
    span_start = base_start + max(local_pos, 0)
    span_end = span_start + len(quote)
    row = {
        "row_id": stable_id("det", essay_id, sent_idx, quote, family),
        "essay_id": essay_id,
        "sentence_index": sent_idx,
        "criterion": CRITERION_BY_FAMILY.get(family, "unknown"),
        "family": family,
        "error_type": family,
        "capacity_domain": CAPACITY_BY_FAMILY.get(family, "unknown"),
        "quote": quote,
        "excerpt": quote,
        "surface_quote": quote,
        "local_quote": sentence,
        "span_start": span_start,
        "span_end": span_end,
        "severity": severity,
        "confidence": confidence,
        "student_message": MESSAGE_BY_FAMILY.get(family, "Review this expression."),
        "suggested_revision": suggested_revision,
        "chargeable": True,
    }
    rows.append(row)


def detect_sentence(essay_id: str, sent_idx: int, sentence: str, start: int, rows: List[Dict[str, Any]]) -> None:
    s = sentence.strip()
    low = s.lower()

    # Universal grammar-pattern checks.
    for m in re.finditer(r"\b(?:has|have|had)\s+to\s+([a-z]+ed)\b", low):
        add_row(rows, essay_id, sent_idx, s, start, s[m.start():m.end()], "G_VERB_FORM", "high", "high")

    for m in re.finditer(r"\bfor\s+([a-z]+ing|[a-z]+)\b", low):
        token = m.group(1)
        # Common false positives like "for example" are excluded by form.
        if token not in {"example", "instance", "people", "children", "students", "society", "government"}:
            add_row(rows, essay_id, sent_idx, s, start, s[m.start():m.end()], "G_VERB_PATTERN", "high", "medium")
            break

    for m in re.finditer(r"\b(?:a|an)\s+([a-z]+s|children|people)\b", low):
        add_row(rows, essay_id, sent_idx, s, start, s[m.start():m.end()], "G_ARTICLE", "high", "high")

    for m in re.finditer(r"\b(?:this|that|it)\s+make\b", low):
        add_row(rows, essay_id, sent_idx, s, start, s[m.start():m.end()], "G_SV_AGREEMENT", "high", "high")

    for m in re.finditer(r"\bmore\s+\w+er\b", low):
        add_row(rows, essay_id, sent_idx, s, start, s[m.start():m.end()], "G_COMPARATIVE_FORM", "high", "high")

    for m in re.finditer(r"\s+[,.;:]", s):
        add_row(rows, essay_id, sent_idx, s, start, s[m.start():m.end()], "G_SPACING", "low", "high")

    for tr in INTRO_TRANSITIONS:
        if low.startswith(tr + " ") and not low.startswith(tr + ","):
            add_row(rows, essay_id, sent_idx, s, start, s[:len(tr)], "G_COMMA_TRANSITION", "low", "high", suggested_revision=s[:len(tr)] + "," + s[len(tr):])
            break

    # Clause with likely missing verb after a subject-like phrase.
    if re.search(r"\b(the|this|that)\s+\w+\s+be\b", low):
        m = re.search(r"\b(the|this|that)\s+\w+\s+be\b", low)
        if m:
            add_row(rows, essay_id, sent_idx, s, start, s[m.start():m.end()], "G_MISSING_VERB", "high", "medium")

    # Academic style / lexical precision checks.
    for expr in INFORMAL_EXPRESSIONS:
        for m in re.finditer(r"\b" + re.escape(expr) + r"\b", low):
            add_row(rows, essay_id, sent_idx, s, start, s[m.start():m.end()], "L_INFORMAL_VOCAB", "low", "medium")
            if expr in {"a lot", "things", "everything"}:
                add_row(rows, essay_id, sent_idx, s, start, s[m.start():m.end()], "S_INFORMAL_TONE", "medium", "medium")

    # Repetition inside the same sentence.
    words = [w.lower() for w in re.findall(r"[A-Za-z']+", s)]
    content = [w for w in words if len(w) > 3 and w not in COMMON_CONTENT_STOPWORDS]
    counts = Counter(content)
    for w, c in counts.items():
        if c >= 2:
            add_row(rows, essay_id, sent_idx, s, start, w, "L_REPETITION", "medium", "medium")

    # Vague lexical items if repeated or used in short generic phrases.
    for w in VAGUE_WORDS:
        if re.search(r"\b" + re.escape(w) + r"\b", low):
            add_row(rows, essay_id, sent_idx, s, start, w, "L_LIMITED_VOCAB", "low", "medium")
            break

    # Argument signal: very short body sentence with no explanation marker can be underdeveloped.
    if 6 <= len(words) <= 11 and not any(x in low for x in ["because", "therefore", "for example", "for instance", "which", "so that"]):
        add_row(rows, essay_id, sent_idx, s, start, s[:120], "A_UNDERDEVELOPED", "medium", "low")


def detect_discourse(essay_id: str, text: str, rows: List[Dict[str, Any]]) -> None:
    low = text.lower()
    connector_hits = []
    for conn in BASIC_CONNECTORS:
        connector_hits.extend([conn] * len(re.findall(r"\b" + re.escape(conn) + r"\b", low)))
    if len(connector_hits) >= 8:
        q = ", ".join(connector_hits[:8])
        add_row(rows, essay_id, 0, text[:300].replace("\n", " "), 0, q, "C_SIMPLE_CONNECTORS", "low", "medium")

    broad_markers = ["everything", "all countries", "all the", "always", "never", "best plan"]
    for bm in broad_markers:
        idx = low.find(bm)
        if idx >= 0:
            sent = next((s for a, b, s in sentence_spans(text) if a <= idx <= b), text[max(0, idx-80):idx+120])
            add_row(rows, essay_id, 0, sent, max(0, idx - sent.lower().find(bm)), bm, "A_OVERGENERALIZATION", "low", "medium")
            break


def build_detector_output(submission: Dict[str, Any]) -> Dict[str, Any]:
    essay_id = submission["essay_id"]
    text = submission["essay_text"]
    rows: List[Dict[str, Any]] = []
    spans = sentence_spans(text)
    for i, (start, end, sent) in enumerate(spans, start=1):
        detect_sentence(essay_id, i, sent, start, rows)
    detect_discourse(essay_id, text, rows)

    family_counts = Counter(r["family"] for r in rows)
    capacity_counts = Counter(r["capacity_domain"] for r in rows)
    criterion_counts = Counter(r["criterion"] for r in rows)
    length_meta = build_length_metadata(text)
    shared_metrics = build_metric_profile_shared(length_meta)

    result = {
        "essay_id": essay_id,
        "student_id": submission["student_id"],
        "task_type": submission["task_type"],
        "prompt_text": submission.get("prompt_text", ""),
        "essay_text": text,

        # Scorer-readable metadata at the record root.
        "word_count": shared_metrics["word_count"],
        "sentence_count": shared_metrics["sentence_count"],
        "paragraph_count": shared_metrics["paragraph_count"],

        # Scorer adapter also checks metadata/generated_metadata.
        "metadata": dict(shared_metrics),
        "generated_metadata": dict(shared_metrics),

        "student_rows": rows,
        "all_rows": rows,
        "detector_rows": rows,

        "detector_metric_profile": {
            "shared": dict(shared_metrics),
            "source": "detector_cli_v1_4_4",
            "confidence": 0.72,
        },
        "scorer_payload": {
            "metadata": dict(shared_metrics),
            "chargeable_detector_rows": rows,
            "family_counts": dict(family_counts),
            "capacity_counts": dict(capacity_counts),
            "criterion_counts": dict(criterion_counts),
            "sentence_count": shared_metrics["sentence_count"],
            "word_count": shared_metrics["word_count"],
            "paragraph_count": shared_metrics["paragraph_count"],
            "premium_metric_profile_mapped_metrics": {
                "shared": dict(shared_metrics),
                "word_count": shared_metrics["word_count"],
                "sentence_count": shared_metrics["sentence_count"],
                "paragraph_count": shared_metrics["paragraph_count"],
                "task_schema_status": "complete",
                "task_schema_confidence": 0.72,
            },
        },
        "evaluator_payload": {
            "all_detector_evidence": rows,
        },
        "metadata_quality": length_meta.get("metadata_quality", {}),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "detector_mode": "standalone_cli_universal_surface_rules_metadata_complete",
        "metadata_contract": {
            "detector_provides_scorer_length_metadata": True,
            "required_fields": ["word_count", "sentence_count", "paragraph_count"],
            "metadata_locations": [
                "results[].word_count",
                "results[].sentence_count",
                "results[].paragraph_count",
                "results[].metadata",
                "results[].generated_metadata",
                "results[].detector_metric_profile.shared",
                "results[].scorer_payload.metadata",
                "results[].scorer_payload.premium_metric_profile_mapped_metrics.shared",
            ],
        },
        "batch_id": str(uuid.uuid4()),
        "student_id": submission["student_id"],
        "result_count": 1,
        "failure_count": 0,
        "summary_metadata": dict(shared_metrics),
        "results": [result],
        "failures": [],
    }

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Standalone Detector CLI, universal surface rules only.")
    ap.add_argument("--input", "-i", required=True)
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--essay-index", type=int, default=0)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)
    raw = read_json(args.input)
    submission = normalize_submission(raw, essay_index=args.essay_index)
    output = build_detector_output(submission)
    write_json(args.output, output, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
