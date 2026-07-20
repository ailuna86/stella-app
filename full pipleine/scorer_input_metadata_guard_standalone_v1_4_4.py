#!/usr/bin/env python3
"""
VA / ST.ELLA Scorer Input Metadata Guard v1.4.4
===============================================

Standalone targeted bridge.

Purpose:
- Verify that Detector output provides scorer-readable length metadata.
- Enrich Detector output with word_count, sentence_count, and paragraph_count
  only when those metadata fields are missing or zero.
- Fail fast in strict mode if metadata cannot be recovered from essay_text.

Boundary:
- This file does not detect writing errors.
- This file does not score IELTS bands.
- This file does not change detector rows or their classifications.
- This file does not generate feedback, LRET, practice, revision, or learner-profile logic.
- It only normalizes structural metadata required by downstream scorer contracts.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ENGINE_ID = "VA_STELLA_SCORER_INPUT_METADATA_GUARD"
ENGINE_VERSION = "1.4.4-standalone-metadata-contract"
SCHEMA_VERSION = "DETECTOR_OUTPUT_SCORER_METADATA_GUARDED_V1_4_4"


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


def sentence_spans(text: str) -> List[Tuple[int, int, str]]:
    spans: List[Tuple[int, int, str]] = []
    for m in re.finditer(r"[^.!?\n]+(?:[.!?]+|$)", text or "", flags=re.M):
        sent = m.group(0).strip()
        if sent:
            spans.append((m.start(), m.end(), sent))
    if not spans and str(text or "").strip():
        spans.append((0, len(text), text.strip()))
    return spans


def paragraph_spans(text: str) -> List[Tuple[int, int, str]]:
    text = text or ""
    spans: List[Tuple[int, int, str]] = []
    for m in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", text, flags=re.M):
        para = m.group(0).strip()
        if para:
            spans.append((m.start(), m.end(), para))
    if not spans and text.strip():
        spans.append((0, len(text), text.strip()))
    return spans


def count_words(text: str) -> int:
    return len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text or ""))


def computed_metadata(text: str) -> Dict[str, Any]:
    return {
        "word_count": count_words(text),
        "sentence_count": len(sentence_spans(text)),
        "paragraph_count": len(paragraph_spans(text)),
        "character_count": len(text or ""),
        "task_schema_status": "complete",
        "task_schema_confidence": 0.72,
        "metadata_source": "scorer_input_metadata_guard_v1_4_4_from_essay_text",
    }


def get_nested(d: Dict[str, Any], path: List[str]) -> Any:
    cur: Any = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def coerce_positive_int(value: Any) -> int:
    try:
        v = int(float(value))
        return v if v > 0 else 0
    except Exception:
        return 0


def existing_metadata(record: Dict[str, Any]) -> Dict[str, int]:
    locations = [
        [],
        ["metadata"],
        ["generated_metadata"],
        ["detector_metric_profile", "shared"],
        ["scorer_payload", "metadata"],
        ["scorer_payload", "premium_metric_profile_mapped_metrics", "shared"],
    ]
    out = {"word_count": 0, "sentence_count": 0, "paragraph_count": 0}
    for field in list(out.keys()):
        for path in locations:
            src = record if not path else get_nested(record, path)
            if isinstance(src, dict):
                val = coerce_positive_int(src.get(field))
                if val:
                    out[field] = val
                    break
    return out


def ensure_dict_path(d: Dict[str, Any], path: List[str]) -> Dict[str, Any]:
    cur = d
    for key in path:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    return cur


def attach_metadata(record: Dict[str, Any], meta: Dict[str, Any], *, source: str, changed_fields: List[str]) -> None:
    shared = {
        "word_count": int(meta.get("word_count") or 0),
        "sentence_count": int(meta.get("sentence_count") or 0),
        "paragraph_count": int(meta.get("paragraph_count") or 0),
        "task_schema_status": str(meta.get("task_schema_status") or "complete"),
        "task_schema_confidence": float(meta.get("task_schema_confidence") or 0.72),
    }

    # Root fields read directly by scorer adapter.
    record.update({
        "word_count": shared["word_count"],
        "sentence_count": shared["sentence_count"],
        "paragraph_count": shared["paragraph_count"],
    })

    for path in (["metadata"], ["generated_metadata"], ["detector_metric_profile", "shared"],
                 ["scorer_payload", "metadata"], ["scorer_payload", "premium_metric_profile_mapped_metrics", "shared"]):
        node = ensure_dict_path(record, path)
        node.update(shared)

    sp = ensure_dict_path(record, ["scorer_payload"])
    sp["word_count"] = shared["word_count"]
    sp["sentence_count"] = shared["sentence_count"]
    sp["paragraph_count"] = shared["paragraph_count"]

    mapped = ensure_dict_path(record, ["scorer_payload", "premium_metric_profile_mapped_metrics"])
    mapped.update(shared)

    record["scorer_metadata_guard"] = {
        "status": "ok",
        "source": source,
        "changed_fields": changed_fields,
        "metadata_contract_complete": all(shared[k] > 0 for k in ("word_count", "sentence_count", "paragraph_count")),
        "guard_engine_id": ENGINE_ID,
        "guard_engine_version": ENGINE_VERSION,
    }


def extract_submission_text(submission: Optional[Dict[str, Any]]) -> str:
    if not isinstance(submission, dict):
        return ""
    if isinstance(submission.get("essays"), list) and submission["essays"]:
        rec = submission["essays"][0] if isinstance(submission["essays"][0], dict) else {}
        return str(rec.get("essay_text") or rec.get("text") or "")
    return str(submission.get("essay_text") or submission.get("text") or "")


def enrich_detector(detector: Dict[str, Any], submission: Optional[Dict[str, Any]], strict: bool = False) -> Dict[str, Any]:
    out = copy.deepcopy(detector)
    if not isinstance(out.get("results"), list):
        raise ValueError("Detector output must contain results[].")

    qa_records: List[Dict[str, Any]] = []
    fallback_text = extract_submission_text(submission)

    for idx, rec in enumerate(out.get("results") or []):
        if not isinstance(rec, dict):
            continue
        text = str(rec.get("essay_text") or rec.get("text") or fallback_text or "")
        computed = computed_metadata(text)
        existing = existing_metadata(rec)
        final = {}
        changed: List[str] = []
        for key in ("word_count", "sentence_count", "paragraph_count"):
            if existing.get(key, 0) > 0:
                final[key] = existing[key]
            else:
                final[key] = int(computed.get(key) or 0)
                changed.append(key)
        final["task_schema_status"] = "complete"
        final["task_schema_confidence"] = 0.72

        if strict and any(int(final.get(k) or 0) <= 0 for k in ("word_count", "sentence_count", "paragraph_count")):
            raise ValueError(f"Cannot recover positive scorer metadata for results[{idx}].")

        attach_metadata(rec, final, source="existing_detector_metadata" if not changed else "recovered_from_essay_text", changed_fields=changed)
        qa_records.append({
            "essay_id": rec.get("essay_id"),
            "before": existing,
            "after": {k: int(final.get(k) or 0) for k in ("word_count", "sentence_count", "paragraph_count")},
            "changed_fields": changed,
            "status": "ok" if all(int(final.get(k) or 0) > 0 for k in ("word_count", "sentence_count", "paragraph_count")) else "invalid",
        })

    out["schema_version"] = SCHEMA_VERSION
    out["engine_id"] = out.get("engine_id") or "UNKNOWN_DETECTOR"
    out["source_detector_engine_id"] = detector.get("engine_id")
    out["source_detector_schema_version"] = detector.get("schema_version")
    out["scorer_metadata_guard"] = {
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "boundary": "Metadata-only guard; detector evidence rows are preserved.",
        "record_count": len(qa_records),
        "records": qa_records,
        "all_records_metadata_complete": all(r.get("status") == "ok" for r in qa_records),
    }
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Scorer input metadata guard v1.4.4.")
    ap.add_argument("--detector", required=True, help="Detector output JSON.")
    ap.add_argument("--submission", required=False, help="Optional normalized submission JSON fallback.")
    ap.add_argument("--output", "-o", required=True, help="Output guarded detector JSON.")
    ap.add_argument("--strict", action="store_true", help="Fail if positive metadata cannot be recovered.")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)

    detector = read_json(args.detector)
    submission = read_json(args.submission) if args.submission else None
    out = enrich_detector(detector, submission, strict=args.strict)
    write_json(args.output, out, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
