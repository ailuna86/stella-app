#!/usr/bin/env python3
"""
VA / ST.ELLA Priority Input Builder v1.4.7
==========================================

Standalone targeted bridge.

Purpose:
- Build a Priority-Engine-ready detector/scorer input from the guarded detector
  artifact.
- Ensure task_type and prompt presence are visible to Priority Engine.
- Ensure Priority Engine sees canonical family names and scorer-readable row
  fields so it does not collapse to UNKNOWN_SKILL because of prefixed detector
  families such as G_VERB_PATTERN.

Boundary:
- This file does not infer new priorities.
- This file does not score, detect, teach, generate exercises, or modify row
  classifications.
- It only prepares a stable input contract for the existing Priority Engine.
"""
from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ENGINE_ID = "VA_STELLA_PRIORITY_INPUT_BUILDER"
ENGINE_VERSION = "1.4.7-standalone-priority-contract"
SCHEMA_VERSION = "PRIORITY_INPUT_BRIDGE_V1_4_7"


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


def extract_submission_record(submission: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(submission, dict):
        return {}
    essays = submission.get("essays")
    if isinstance(essays, list) and essays and isinstance(essays[0], dict):
        return essays[0]
    return submission


def first_result(obj: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(obj.get("results"), list) and obj["results"] and isinstance(obj["results"][0], dict):
        return obj["results"][0]
    return obj


def as_results(obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(obj.get("results"), list):
        return [x for x in obj["results"] if isinstance(x, dict)]
    return [obj] if isinstance(obj, dict) else []


def score_profile_by_id(scorer: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(scorer, dict):
        return out
    results = scorer.get("results") if isinstance(scorer.get("results"), list) else [scorer]
    for idx, r in enumerate(results):
        if not isinstance(r, dict):
            continue
        eid = str(r.get("essay_id") or ((r.get("identity") or {}).get("essay_id")) or idx + 1)
        out[eid] = r
    return out


def build_priority_input(detector: Dict[str, Any], submission: Optional[Dict[str, Any]], scorer: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = copy.deepcopy(detector)
    submission_record = extract_submission_record(submission)
    scorer_by_id = score_profile_by_id(scorer)
    prompt_text = submission_record.get("prompt_text") or submission_record.get("prompt") or ""
    essay_text = submission_record.get("essay_text") or submission_record.get("text") or ""
    task_type = submission_record.get("task_type") or "WT2"

    records = as_results(out)
    quality_records: List[Dict[str, Any]] = []
    for idx, rec in enumerate(records):
        eid = str(rec.get("essay_id") or ((rec.get("identity") or {}).get("essay_id")) or idx + 1)
        rec["essay_id"] = eid
        rec["student_id"] = rec.get("student_id") or submission_record.get("student_id")
        rec["task_type"] = rec.get("task_type") or task_type
        rec["prompt_text"] = rec.get("prompt_text") or prompt_text
        rec["essay_text"] = rec.get("essay_text") or essay_text
        rec["intake_record"] = {
            "prompt_text": rec.get("prompt_text") or "",
            "essay_text": rec.get("essay_text") or "",
            "task_type": rec.get("task_type") or task_type,
        }
        rec["task_profile"] = {**(rec.get("task_profile") or {}), "task_type": rec.get("task_type") or task_type, "prompt_present": bool(rec.get("prompt_text")), "score_ready": True}
        rec["meta"] = {**(rec.get("meta") or {}), "prompt_present": bool(rec.get("prompt_text")), "task_type": rec.get("task_type") or task_type}

        # Priority Engine prefers student_rows before scorer_payload rows. Make sure
        # this path contains canonical rows produced by the v1.4.7 evidence guard.
        sp = rec.get("scorer_payload") if isinstance(rec.get("scorer_payload"), dict) else {}
        chargeable = sp.get("chargeable_detector_rows") if isinstance(sp.get("chargeable_detector_rows"), list) else []
        review = sp.get("review_only_detector_rows") if isinstance(sp.get("review_only_detector_rows"), list) else []
        if chargeable:
            rec["student_rows"] = chargeable + review
            rec["all_rows"] = chargeable + review

        scorer_record = scorer_by_id.get(eid)
        if scorer_record:
            # Duplicate the fields that Priority Engine can read even if --scorer
            # merge behavior changes in a future version.
            if "score_profile" in scorer_record:
                rec["score_profile"] = scorer_record["score_profile"]
            if "rubric_impact_map" in scorer_record:
                rec["rubric_impact_map"] = scorer_record["rubric_impact_map"]
            if "score_explanation_payload" in scorer_record:
                rec["score_explanation_payload"] = scorer_record["score_explanation_payload"]

        families = sorted({str(r.get("family") or "") for r in rec.get("student_rows", []) if isinstance(r, dict)})
        unknown_count = sum(1 for r in rec.get("student_rows", []) if isinstance(r, dict) and str(r.get("family") or "").upper().startswith("UNKNOWN"))
        quality_records.append({
            "essay_id": eid,
            "row_count": len(rec.get("student_rows", []) if isinstance(rec.get("student_rows"), list) else []),
            "unique_family_count": len([f for f in families if f]),
            "unknown_family_count": unknown_count,
            "task_type": rec.get("task_type"),
            "prompt_present": bool(rec.get("prompt_text")),
            "status": "ok" if rec.get("task_type") and rec.get("prompt_text") and unknown_count == 0 else "needs_attention",
        })

    out["schema_version"] = SCHEMA_VERSION
    out["source_detector_schema_version"] = detector.get("schema_version")
    out["priority_input_builder"] = {
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "boundary": "Input-contract normalization only; Priority Engine still owns priority inference.",
        "records": quality_records,
        "all_records_priority_ready": all(r["status"] == "ok" for r in quality_records),
    }
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build Priority Engine input v1.4.7.")
    ap.add_argument("--detector", required=True, help="Guarded detector JSON.")
    ap.add_argument("--submission", required=True, help="Normalized submission JSON.")
    ap.add_argument("--scorer", required=False, help="Optional scorer output JSON.")
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)

    detector = read_json(args.detector)
    submission = read_json(args.submission)
    scorer = read_json(args.scorer) if args.scorer else None
    out = build_priority_input(detector, submission, scorer)
    if args.strict and not (out.get("priority_input_builder") or {}).get("all_records_priority_ready"):
        raise SystemExit("Priority input is not ready.")
    write_json(args.output, out, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
