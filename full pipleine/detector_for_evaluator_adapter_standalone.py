#!/usr/bin/env python3
"""
Detector → Evaluator Adapter v1.4.3 — standalone
================================================

This targeted bridge converts detector outputs into the compact detector schema
that Evaluator/WKE v7.3b can consume. It imports no previous versions.

Boundary:
- Does not detect new errors.
- Does not score or teach.
- Does not change detector evidence.
- Only normalizes field names and exports diagnostic_rows for Evaluator.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = "DETECTOR_EVALUATOR_BRIDGE_V1_4_3"
ENGINE_ID = "VA_STELLA_DETECTOR_FOR_EVALUATOR_ADAPTER"
ENGINE_VERSION = "1.4.3-standalone-no-imports"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Optional[str], required: bool = True) -> Any:
    if not path:
        if required:
            raise ValueError("missing required path")
        return None
    p = Path(path)
    if not p.exists():
        if required:
            raise FileNotFoundError(str(p))
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(path: str, data: Any, pretty: bool = False) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2 if pretty else None) + "\n", encoding="utf-8")


def iter_detector_rows(detector: Dict[str, Any]) -> List[Dict[str, Any]]:
    roots: List[Dict[str, Any]] = []
    if isinstance(detector, dict):
        roots.append(detector)
        if isinstance(detector.get("results"), list):
            roots.extend([r for r in detector["results"] if isinstance(r, dict)])
    rows: List[Dict[str, Any]] = []
    seen_sources = set()
    for root in roots:
        for key in ("diagnostic_rows", "student_rows", "survived_candidates", "validated_rows", "rows", "errors"):
            value = root.get(key)
            if isinstance(value, list):
                for r in value:
                    if isinstance(r, dict):
                        rid = r.get("row_id") or r.get("candidate_id") or r.get("error_id") or id(r)
                        key_id = (str(rid), r.get("sentence_index"), r.get("quote") or r.get("surface_quote"))
                        if key_id not in seen_sources:
                            rows.append(r)
                            seen_sources.add(key_id)
        payload = root.get("evaluator_payload")
        if isinstance(payload, dict):
            value = payload.get("all_detector_evidence")
            if isinstance(value, list):
                for r in value:
                    if isinstance(r, dict):
                        rid = r.get("row_id") or r.get("candidate_id") or r.get("error_id") or id(r)
                        key_id = (str(rid), r.get("sentence_index"), r.get("quote") or r.get("surface_quote"))
                        if key_id not in seen_sources:
                            rows.append(r)
                            seen_sources.add(key_id)
    return rows


def iter_errormap_rows(errormap: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(errormap, dict):
        return []
    value = errormap.get("errors")
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def normalize_row(r: Dict[str, Any], i: int, source: str) -> Dict[str, Any]:
    family = r.get("family") or r.get("error_type") or r.get("issue_code")
    criterion = r.get("criterion") or r.get("rubric") or r.get("category")
    quote = r.get("quote") or r.get("surface_quote") or r.get("excerpt") or r.get("local_quote")
    local_quote = r.get("local_quote") or r.get("location", {}).get("sentence") if isinstance(r.get("location"), dict) else r.get("local_quote")
    if not local_quote:
        local_quote = quote
    row_id = r.get("row_id") or r.get("source_row_id") or r.get("candidate_id") or r.get("error_id") or f"det_eval_{i:04d}"
    start = r.get("span_start")
    end = r.get("span_end")
    loc = r.get("location") if isinstance(r.get("location"), dict) else {}
    if start is None:
        start = loc.get("char_start") or r.get("start")
    if end is None:
        end = loc.get("char_end") or r.get("end")
    return {
        "row_id": str(row_id),
        "detector_evidence_id": str(row_id),
        "source": source,
        "essay_id": r.get("essay_id"),
        "sentence_index": r.get("sentence_index"),
        "paragraph_index": r.get("paragraph_index"),
        "rubric": criterion,
        "category": criterion,
        "criterion": criterion,
        "family": family,
        "error_family": family,
        "issue_code": family,
        "quote": quote,
        "surface_quote": quote,
        "local_quote": local_quote,
        "span_text": quote,
        "start": start,
        "end": end,
        "span_start": start,
        "span_end": end,
        "confidence": r.get("confidence"),
        "severity": r.get("severity"),
        "suggestion": r.get("suggested_revision") or r.get("suggestion") or r.get("repair_hypothesis"),
        "chargeable": r.get("chargeable", True),
    }


def build(detector: Dict[str, Any], errormap: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    source_rows = iter_detector_rows(detector)
    if not source_rows:
        source_rows = iter_errormap_rows(errormap)
        source_kind = "errormap_fallback"
    else:
        source_kind = "detector"
    normalized: List[Dict[str, Any]] = []
    seen: set = set()
    for i, row in enumerate(source_rows, start=1):
        nr = normalize_row(row, i, source_kind)
        key = (nr.get("row_id"), nr.get("sentence_index"), nr.get("quote"), nr.get("family"))
        if key in seen:
            continue
        seen.add(key)
        normalized.append(nr)
    essay_id = None
    student_id = None
    if isinstance(detector, dict):
        if isinstance(detector.get("results"), list) and detector["results"]:
            first = detector["results"][0] if isinstance(detector["results"][0], dict) else {}
            essay_id = first.get("essay_id")
            student_id = first.get("student_id") or detector.get("student_id")
        essay_id = essay_id or detector.get("essay_id")
        student_id = student_id or detector.get("student_id")
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "boundary": "Field normalization only; source detector evidence is not changed or reclassified.",
        "source_detector_schema": detector.get("schema_version") if isinstance(detector, dict) else None,
        "source_errormap_schema": errormap.get("schema_version") if isinstance(errormap, dict) else None,
        "essay_id": essay_id,
        "student_id": student_id,
        "diagnostic_rows": normalized,
        "results": [
            {
                "essay_id": essay_id,
                "student_id": student_id,
                "diagnostic_rows": normalized,
            }
        ],
        "summary": {
            "source_kind": source_kind,
            "diagnostic_row_count": len(normalized),
            "families": sorted({str(r.get("family")) for r in normalized if r.get("family")}),
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Normalize detector output for Evaluator/WKE consumption.")
    ap.add_argument("--detector", required=True)
    ap.add_argument("--errormap")
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)
    detector = read_json(args.detector)
    errormap = read_json(args.errormap, required=False)
    out = build(detector, errormap)
    write_json(args.output, out, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
