"""
detector_to_errormap_v3.py
==========================
NEW FILE — wraps detector_to_errormap_v2.py (frozen, preserved unchanged).

CHANGES vs v2
-------------
F9-A  char_start / char_end fixed
      v2 reads row.get("char_start") / row.get("char_end") — fields that don't exist
      on detector rows. Detector stores span_start / span_end.
      Result: all 29 errors across sessions 9–10 have char_start=0, char_end=0.
      Fix: after v2 builds the base errormap, re-iterate chargeable_rows and patch
      each error's location.char_start from row["span_start"], char_end from
      row["span_end"].

F9-B  sentence field added
      v2 drops row["local_quote"] (full containing sentence) from every row.
      The PE passes it through in student_safe_rows. The FE adapter ignores it.
      Fix: location.sentence ← row["local_quote"]; location.sentence_index ←
      row["sentence_index"] (kept separate from paragraph_index).

F9-C  broken_sentences_raw extracted
      layer0_5_semantic_recoverability.sentence_assessments is computed but never
      surfaces downstream. v3 extracts sentences where local_corruption_score >= 0.55
      OR discourse_evaluation_allowed == "blocked" and stores them as
      errormap["broken_sentences_raw"] for use by feedback_engine_v4_adapter.

USAGE
-----
    from detector_to_errormap_v3 import detector_output_to_errormap_v3

    error_map = detector_output_to_errormap_v3(det_out, essay_result_index=0,
                                                submission_id=submission_id)
    # error_map["errors"][i]["location"]["char_start"]  ← correct now
    # error_map["errors"][i]["location"]["sentence"]    ← full sentence added
    # error_map["broken_sentences_raw"]                 ← new field
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from detector_to_errormap_v2 import detector_output_to_errormap  # noqa: E402  (frozen v2)

# Threshold: sentences above this corruption score surface in broken_sentences_raw
_CORRUPTION_THRESHOLD = 0.55


# ── Row lookup helpers ────────────────────────────────────────────────────────

def _get_chargeable_rows(essay_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract chargeable rows from essay_result using same priority order as v2."""
    sp = essay_result.get("scorer_payload", {})
    ep = essay_result.get("evaluator_payload", {})
    return (
        sp.get("chargeable_detector_rows", [])
        or ep.get("all_detector_evidence", [])
        or essay_result.get("student_rows", [])
    )


def _build_row_lookup(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Build lookup dicts for patching: by row_id and by excerpt (quote).
    A row can be found by either key.
    """
    by_id: Dict[str, Dict] = {}
    by_excerpt: Dict[str, Dict] = {}
    for row in rows:
        rid = str(row.get("row_id", ""))
        if rid:
            by_id[rid] = row
        quote = (row.get("quote") or row.get("excerpt") or "")[:200]
        if quote:
            # Use first row for a given excerpt (in case of duplicates)
            if quote not in by_excerpt:
                by_excerpt[quote] = row
    return by_id, by_excerpt


def _find_row_for_error(
    error: Dict[str, Any],
    by_id: Dict[str, Dict],
    by_excerpt: Dict[str, Dict],
) -> Optional[Dict[str, Any]]:
    """
    Find the original detector row that produced this error entry.
    Tries row_id first, then excerpt match.
    """
    # error_id is str(row.get("row_id") or uuid.uuid4()) from v2
    eid = error.get("error_id", "")
    if eid and eid in by_id:
        return by_id[eid]
    # Fallback: match by excerpt
    excerpt = (error.get("location") or {}).get("excerpt", "")
    if excerpt and excerpt in by_excerpt:
        return by_excerpt[excerpt]
    return None


# ── Broken sentences extractor ────────────────────────────────────────────────

def _extract_broken_sentences(essay_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract sentences that are semantically broken (high corruption / blocked discourse).
    Combines data from layer0_5_semantic_recoverability and segmentation.

    Returns list of BrokenSentenceRaw dicts, sorted by sentence_index.
    Only includes sentences above the corruption threshold.
    """
    sem_rec = essay_result.get("layer0_5_semantic_recoverability", {})
    # FIX-SA-001: sentence_assessments is a dict {"1": {...}, "2": {...}},
    # not a list — normalise to a list of dicts before iterating.
    _sa_raw = sem_rec.get("sentence_assessments", [])
    if isinstance(_sa_raw, dict):
        sent_assessments: List[Dict] = list(_sa_raw.values())
    else:
        sent_assessments = list(_sa_raw)
    if not sent_assessments:
        return []

    # Build sentence text lookup from segmentation
    segmentation = essay_result.get("segmentation", {})
    seg_sentences: List[Dict] = segmentation.get("sentences", [])
    sent_text_by_idx: Dict[int, str] = {}
    sent_chars_by_idx: Dict[int, tuple] = {}
    for s in seg_sentences:
        idx = s.get("sentence_index", s.get("index", -1))
        if idx >= 0:
            sent_text_by_idx[idx] = s.get("text", s.get("sentence_text", ""))
            sent_chars_by_idx[idx] = (
                s.get("char_start", s.get("start", 0)),
                s.get("char_end", s.get("end", 0)),
            )

    broken: List[Dict[str, Any]] = []
    for sa in sent_assessments:
        corruption  = float(sa.get("local_corruption_score", 0.0))
        discourse   = str(sa.get("discourse_evaluation_allowed", "full"))
        rec_score   = float(sa.get("recoverability_score", 1.0))
        sent_idx    = int(sa.get("sentence_index", -1))

        qualifies = (corruption >= _CORRUPTION_THRESHOLD) or (discourse == "blocked")
        if not qualifies:
            continue

        # Determine severity label
        if corruption >= 0.75 and discourse == "blocked":
            severity = "critical"
        elif corruption >= 0.58 or discourse in ("blocked", "limited"):
            severity = "serious"
        else:
            severity = "moderate"

        # Error families from this sentence
        families: List[str] = sa.get("error_families", [])
        if not families:
            # Fall back to root_cause_hint parsing
            hint = sa.get("root_cause_hint", "")
            for token in ("VERB_FORM", "CLAUSE_STRUCTURE", "PREPOSITION",
                          "SUBJECT_VERB_AGREEMENT", "ARTICLE_DETERMINER",
                          "COMPARATIVE_FORM", "WORD_FORM", "COLLOCATION"):
                if token.lower().replace("_", " ") in hint.lower() or token in hint:
                    families.append(token)

        char_start, char_end = sent_chars_by_idx.get(sent_idx, (0, 0))
        sent_text = (
            sent_text_by_idx.get(sent_idx, "")
            or sa.get("sentence_text", "")
        )

        broken.append({
            "sentence_index":              sent_idx,
            "sentence_text":               sent_text,
            "char_start":                  char_start,
            "char_end":                    char_end,
            "recoverability_score":        rec_score,
            "local_corruption_score":      corruption,
            "discourse_evaluation_allowed": discourse,
            "root_cause_hint":             sa.get("root_cause_hint", ""),
            "error_families":              families,
            "severity":                    severity,
        })

    broken.sort(key=lambda x: x["sentence_index"])
    return broken


# ── Main function ─────────────────────────────────────────────────────────────

def detector_output_to_errormap_v3(
    detector_output: Dict[str, Any],
    essay_result_index: int = 0,
    submission_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Drop-in replacement for detector_output_to_errormap() that adds:
    - Correct char_start / char_end from span_start / span_end (F9-A)
    - sentence field from local_quote (F9-B)
    - broken_sentences_raw from semantic recoverability data (F9-C)

    All v2 logic (criterion mapping, severity, flags, error_summary) is preserved
    exactly — v2 is called first and its output is patched.

    Args:
        detector_output:      Full dict returned by det_vip_v18d_2.py
        essay_result_index:   Which essay in results[] to convert (default 0)
        submission_id:        Optional submission_id override

    Returns:
        ErrorMap v3 dict — superset of v2 format.
        New fields per error.location: char_start (fixed), char_end (fixed),
        sentence (str), sentence_index (int).
        New top-level field: broken_sentences_raw (list).
    """
    # Step 1: run frozen v2 to get the base errormap
    base = detector_output_to_errormap(
        detector_output,
        essay_result_index=essay_result_index,
        submission_id=submission_id,
    )

    # Step 2: get the essay_result we need for patching
    if "results" in detector_output:
        results = detector_output.get("results", [])
        essay_result: Dict[str, Any] = (
            results[essay_result_index] if results and essay_result_index < len(results)
            else {}
        )
    else:
        essay_result = detector_output

    # Step 3: build row lookups for patching
    chargeable_rows = _get_chargeable_rows(essay_result)
    by_id, by_excerpt = _build_row_lookup(chargeable_rows)

    # Step 4: patch each error's location block
    patched_count = 0
    for error in base.get("errors", []):
        row = _find_row_for_error(error, by_id, by_excerpt)
        if not row:
            continue

        loc = error.setdefault("location", {})

        # F9-A: fix char offsets
        span_start = row.get("span_start", row.get("char_start", None))
        span_end   = row.get("span_end",   row.get("char_end",   None))
        if span_start is not None:
            loc["char_start"] = int(span_start)
        if span_end is not None:
            loc["char_end"] = int(span_end)

        # F9-B: add sentence and sentence_index
        local_quote = (
            row.get("local_quote")
            or row.get("full_sentence")
            or row.get("sentence_text")
            or ""
        )
        loc["sentence"]       = local_quote
        loc["sentence_index"] = int(
            row.get("sentence_index", loc.get("paragraph_index", -1))
        )
        patched_count += 1

    base["_errormap_version"] = "v3"
    base["_v3_patched_errors"] = patched_count

    # Step 5: extract broken_sentences_raw (F9-C)
    base["broken_sentences_raw"] = _extract_broken_sentences(essay_result)

    return base
