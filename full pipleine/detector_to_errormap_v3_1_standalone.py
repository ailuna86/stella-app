#!/usr/bin/env python3
"""
Detector → ErrorMap v3 Standalone
=================================

Standalone ErrorMap builder. Imports no previous versions.
It flattens detector evidence rows into an ErrorMap artifact with counts and
broken sentence signals. It does not detect new errors, score, teach, or classify
lexical units.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "ERRORMAP_V3_STANDALONE"
ENGINE_ID = "VA_STELLA_DETECTOR_TO_ERRORMAP_STANDALONE"
ENGINE_VERSION = "3.1.0-standalone-no-imports-topic-alignment-risk-passthrough"

CAPACITY_BY_CRITERION = {
    "grammar": "sentence_control",
    "grammatical_range_accuracy": "sentence_control",
    "lexical_resource": "lexical_precision",
    "cohesion_coherence": "cohesion_control",
    "argumentation": "argument_development",
    "task_response": "task_response_control",
    "academic_style": "academic_style",
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


def sid(prefix: str, *parts: Any) -> str:
    return f"{prefix}_{hashlib.sha1('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()[:12]}"


def extract_results(detector: Any) -> List[Dict[str, Any]]:
    if isinstance(detector, dict) and isinstance(detector.get("results"), list):
        return detector["results"]
    if isinstance(detector, dict):
        return [detector]
    if isinstance(detector, list):
        return detector
    return []


def extract_rows(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates = []
    if isinstance(result.get("scorer_payload"), dict):
        candidates.extend(result["scorer_payload"].get("chargeable_detector_rows") or [])
    if not candidates and isinstance(result.get("evaluator_payload"), dict):
        candidates.extend(result["evaluator_payload"].get("all_detector_evidence") or [])
    if not candidates:
        candidates.extend(result.get("student_rows") or [])
    if not candidates:
        candidates.extend(result.get("errors") or [])
    return [r for r in candidates if isinstance(r, dict)]


def normalize_error(row: Dict[str, Any], result: Dict[str, Any], index: int) -> Dict[str, Any]:
    essay_id = str(row.get("essay_id") or result.get("essay_id") or "essay_unknown")
    family = str(row.get("family") or row.get("error_type") or row.get("issue_type") or "UNCLASSIFIED")
    criterion = str(row.get("criterion") or row.get("rubric") or "unknown")
    capacity = str(row.get("capacity_domain") or CAPACITY_BY_CRITERION.get(criterion, "unknown"))
    quote = row.get("surface_quote") or row.get("quote") or row.get("excerpt") or row.get("text") or ""
    source_row_id = str(row.get("row_id") or row.get("source_row_id") or sid("row", essay_id, family, quote, index))
    error_id = str(row.get("error_id") or sid("err", source_row_id, family, quote))
    sentence_index = row.get("sentence_index")
    try:
        sentence_index = int(sentence_index) if sentence_index is not None else None
    except Exception:
        sentence_index = None

    return {
        "error_id": error_id,
        "source_row_id": source_row_id,
        "essay_id": essay_id,
        "sentence_index": sentence_index,
        "criterion": criterion,
        "family": family,
        "capacity_domain": capacity,
        "surface_quote": quote,
        "suggested_revision": row.get("suggested_revision") or row.get("correction") or row.get("replacement"),
        "severity": row.get("severity") or "medium",
        "confidence": row.get("confidence") or "medium",
        "student_message": row.get("student_message") or row.get("message") or row.get("explanation") or "Review this issue.",
        "chargeable": bool(row.get("chargeable", True)),
        "location": {
            "char_start": row.get("span_start") if row.get("span_start") is not None else row.get("char_start"),
            "char_end": row.get("span_end") if row.get("span_end") is not None else row.get("char_end"),
            "sentence": row.get("local_quote") or row.get("sentence"),
        },
    }


def _span_overlap_ratio(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """Overlap length relative to the shorter of the two spans, 0.0-1.0.
    Returns 0.0 if either span is missing/degenerate or they don't overlap.
    """
    try:
        s1, e1 = int(a.get("char_start")), int(a.get("char_end"))
        s2, e2 = int(b.get("char_start")), int(b.get("char_end"))
    except (TypeError, ValueError):
        return 0.0
    if e1 <= s1 or e2 <= s2:
        return 0.0
    overlap = min(e1, e2) - max(s1, s2)
    if overlap <= 0:
        return 0.0
    shorter = min(e1 - s1, e2 - s2)
    return overlap / shorter if shorter > 0 else 0.0


def _resolve_grammar_lexical_span_overlaps(errors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Universal rule, not tied to any specific essay's vocabulary: when a
    grammar-criterion error and a lexical_resource-criterion error occupy
    overlapping character spans within the same sentence, they are almost
    always two labels for the SAME underlying mistake, not two distinct
    chargeable errors.

    Confirmed directly on real pipeline output: "more stronger" (chars
    1082-1095) correctly tagged criterion=grammar, family=COMPARATIVE_FORM,
    fully contained inside "make a family more stronger" (chars 1068-1095),
    which was ALSO independently tagged criterion=lexical_resource,
    family=COLLOCATION, with a suggested_revision ("make a family stronger")
    that is the identical fix as the grammar row's. Same mistake, charged
    against both criteria.

    Grammar wins on overlap: the grammar-family checkers in this pipeline
    tend to flag the exact malformed token span (a double comparative, a
    wrong verb form), while the broader local-language/collocation checker
    tends to flag the whole surrounding phrase and can mis-explain a grammar
    problem as a "words don't pair" problem. A narrower, correctly-classified
    span is more actionable for a student than a broader, mis-classified one.

    The superseded lexical row is not deleted -- it is moved out of the
    chargeable `errors` list into `errors_superseded_by_overlap` with a
    pointer back to the grammar row it duplicates, so nothing silently
    disappears from the artifact; it is just excluded from chargeable counts
    and from downstream scoring/LRET routing.
    """
    by_sentence: Dict[Any, List[Dict[str, Any]]] = {}
    for e in errors:
        by_sentence.setdefault(e.get("sentence_index"), []).append(e)
    superseded_ids = set()
    superseded_by: Dict[str, str] = {}
    for _, group in by_sentence.items():
        grammar_rows = [e for e in group if e.get("criterion") == "grammar"]
        lexical_rows = [e for e in group if e.get("criterion") == "lexical_resource"]
        if not grammar_rows or not lexical_rows:
            continue
        for lex in lexical_rows:
            eid = lex.get("error_id")
            if eid in superseded_ids:
                continue
            for gram in grammar_rows:
                if _span_overlap_ratio(lex.get("location") or {}, gram.get("location") or {}) >= 0.5:
                    superseded_ids.add(eid)
                    superseded_by[eid] = gram.get("error_id")
                    break
    kept: List[Dict[str, Any]] = []
    superseded: List[Dict[str, Any]] = []
    for e in errors:
        eid = e.get("error_id")
        if eid in superseded_ids:
            e["chargeable"] = False
            e["superseded_by"] = superseded_by.get(eid)
            e["suppression_reason"] = (
                "span_overlaps_grammar_criterion_error_in_same_sentence: treated as the same "
                "underlying mistake; the grammar classification is kept as authoritative"
            )
            superseded.append(e)
        else:
            kept.append(e)
    return kept, superseded


def _extract_topic_alignment_risk(detector: Any) -> Dict[str, Any]:
    """V3 Section 3 addition: surface the Detector's cheap topic-alignment safety-net
    flag into errormap, same path every other Detector signal already takes, so it
    becomes visible to Priority Engine, Directive, and Feedback Report -- a genuinely
    off-topic essay should trigger a visible student-facing warning, not just a silently
    adjusted number. Single-essay runs have one result; if a batch ever has more than
    one, the first result that actually ran the check wins (fail-safe default otherwise).
    """
    default = {"checked": False, "risk_flag": False, "confidence": 0.0, "reason": "not_present_on_detector_output"}
    for result in extract_results(detector):
        tar = result.get("topic_alignment_risk")
        if isinstance(tar, dict) and tar.get("checked"):
            return tar
    for result in extract_results(detector):
        tar = result.get("topic_alignment_risk")
        if isinstance(tar, dict):
            return tar
    return default


def build_errormap(detector: Any) -> Dict[str, Any]:
    errors: List[Dict[str, Any]] = []
    broken: List[Dict[str, Any]] = []
    topic_alignment_risk = _extract_topic_alignment_risk(detector)
    for result in extract_results(detector):
        rows = extract_rows(result)
        for i, row in enumerate(rows):
            err = normalize_error(row, result, i)
            if err.get("chargeable", True):
                errors.append(err)
            severity = str(err.get("severity") or "").lower()
            if severity == "high" and str(err.get("capacity_domain")) == "sentence_control":
                broken.append({
                    "sentence_index": err.get("sentence_index"),
                    "quote": err.get("surface_quote"),
                    "family": err.get("family"),
                    "source_error_id": err.get("error_id"),
                })
    errors, superseded = _resolve_grammar_lexical_span_overlaps(errors)
    family_counts = Counter(e.get("family") for e in errors)
    capacity_counts = Counter(e.get("capacity_domain") for e in errors)
    criterion_counts = Counter(e.get("criterion") for e in errors)
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "errors": errors,
        "errors_superseded_by_overlap": superseded,
        "topic_alignment_risk": topic_alignment_risk,
        "broken_sentences_raw": broken,
        "counts": dict(family_counts),
        "counts_by_capacity": dict(capacity_counts),
        "counts_by_criterion": dict(criterion_counts),
        "summary": {
            "error_count": len(errors),
            "superseded_by_overlap_count": len(superseded),
            "broken_sentence_signal_count": len(broken),
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build ErrorMap v3 from detector output; standalone, no previous imports.")
    ap.add_argument("--input", "-i", required=True, help="Detector output JSON")
    ap.add_argument("--output", "-o", required=True, help="ErrorMap output JSON")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)
    detector = read_json(args.input)
    errormap = build_errormap(detector)
    write_json(args.output, errormap, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
