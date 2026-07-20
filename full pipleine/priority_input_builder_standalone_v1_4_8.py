#!/usr/bin/env python3
"""
VA / ST.ELLA Priority Input Builder v1.4.8
==========================================

Standalone targeted bridge. Extends v1.4.7 with one addition: it can now also
accept Evaluator/WKE output and map it into the field shapes Priority Engine
(priority_engine_v4_4_selfcontained.py) already knows how to read:

  - evaluator_payload.strengths_profile
      <- consumer_payloads.writing_coach_payload.current_strength_signals
  - layer0_5_semantic_recoverability.semantic_summary
      <- aggregated from consumer_payloads.essay_revision_control_payload.sentence_control

Both target paths already exist as read paths inside Priority Engine
(extract_strengths_profile / extract_semantic) but were previously always
empty because nothing populated them. This bridge does not invent new
priority logic -- it only maps Evaluator's existing output into the shape
Priority Engine already expects, and it does so from generic field names, not
essay-specific content.

Everything from v1.4.7 is unchanged and --evaluator is optional, so this file
is a drop-in replacement: a run without --evaluator behaves exactly like
v1.4.7.

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
ENGINE_VERSION = "1.4.8-adds-evaluator-strengths-and-semantic-mapping"
SCHEMA_VERSION = "PRIORITY_INPUT_BRIDGE_V1_4_8"

# Recoverability categories, as produced by Evaluator's per-sentence
# sentence_control assessment, mapped to a 0..1 scale for aggregation only.
_RECOVERABILITY_SCALE = {
    "full": 1.0,
    "partial": 0.5,
    "low": 0.0,
    "blocked": 0.0,
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


def _strengths_profile_from_evaluator(evaluator: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map Evaluator's writing_coach_payload.current_strength_signals into the
    flat strengths_profile shape Priority Engine's extract_strengths_profile()
    already looks for at evaluator_payload.strengths_profile."""
    payloads = (evaluator.get("consumer_payloads") or {})
    signals = (payloads.get("writing_coach_payload") or {}).get("current_strength_signals") or []
    out: List[Dict[str, Any]] = []
    for s in signals:
        if not isinstance(s, dict):
            continue
        out.append({
            "skill_id": s.get("skill_id"),
            "skill_name": s.get("skill_name"),
            "domain": s.get("domain"),
            "status": s.get("status"),
            "confidence": s.get("diagnostic_confidence"),
            "priority_index": s.get("priority_index"),
            "source": "evaluator_skill_observation_profile",
        })
    return out


def _semantic_summary_from_evaluator(evaluator: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate Evaluator's per-sentence sentence_control assessments (each
    carrying a categorical semantic_recoverability value) into the aggregate
    shape Priority Engine's extract_semantic() already looks for at
    layer0_5_semantic_recoverability.semantic_summary. This is arithmetic
    aggregation over Evaluator's own existing per-sentence output, not a new
    diagnosis."""
    payloads = (evaluator.get("consumer_payloads") or {})
    ercp = payloads.get("essay_revision_control_payload") or {}
    sentence_control = ercp.get("sentence_control") or []
    if not isinstance(sentence_control, list) or not sentence_control:
        return {}

    recov_values: List[float] = []
    blocked = 0
    limited = 0
    affected = 0
    for s in sentence_control:
        if not isinstance(s, dict):
            continue
        recov_cat = str(s.get("semantic_recoverability") or "").lower()
        if recov_cat in _RECOVERABILITY_SCALE:
            recov_values.append(_RECOVERABILITY_SCALE[recov_cat])
        if recov_cat in ("low", "blocked"):
            blocked += 1
        if recov_cat == "partial":
            limited += 1
        status = str(s.get("language_control_status") or "").lower()
        if status in ("yellow", "red"):
            affected += 1

    n = len(sentence_control)
    mean_recoverability = round(sum(recov_values) / len(recov_values), 3) if recov_values else None

    return {
        "mean_recoverability": mean_recoverability,
        "blocked_sentence_count": blocked,
        "limited_sentence_count": limited,
        "affected_discourse_ratio": round(affected / n, 3) if n else None,
        "sentence_count": n,
        "sentence_assessments": sentence_control,
        "source": "evaluator_essay_revision_control_payload.sentence_control (aggregated)",
    }


def build_priority_input(
    detector: Dict[str, Any],
    submission: Optional[Dict[str, Any]],
    scorer: Optional[Dict[str, Any]] = None,
    evaluator: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out = copy.deepcopy(detector)
    submission_record = extract_submission_record(submission)
    scorer_by_id = score_profile_by_id(scorer)
    prompt_text = submission_record.get("prompt_text") or submission_record.get("prompt") or ""
    essay_text = submission_record.get("essay_text") or submission_record.get("text") or ""
    task_type = submission_record.get("task_type") or "WT2"

    strengths_profile: List[Dict[str, Any]] = []
    semantic_summary: Dict[str, Any] = {}
    evaluator_available = isinstance(evaluator, dict) and bool(evaluator)
    if evaluator_available:
        strengths_profile = _strengths_profile_from_evaluator(evaluator)
        semantic_summary = _semantic_summary_from_evaluator(evaluator)

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

        # v1.4.8: map Evaluator's output into the paths Priority Engine already
        # knows how to read. Both are additive -- absent if --evaluator wasn't
        # supplied, so v1.4.7 behavior is preserved exactly when it isn't.
        if evaluator_available:
            rec["evaluator_payload"] = {
                **(rec.get("evaluator_payload") or {}),
                "strengths_profile": strengths_profile,
            }
            if semantic_summary:
                rec["layer0_5_semantic_recoverability"] = {
                    **(rec.get("layer0_5_semantic_recoverability") or {}),
                    "semantic_summary": semantic_summary,
                }
            rec["evaluator_available"] = True
        else:
            rec["evaluator_available"] = False

        families = sorted({str(r.get("family") or "") for r in rec.get("student_rows", []) if isinstance(r, dict)})
        unknown_count = sum(1 for r in rec.get("student_rows", []) if isinstance(r, dict) and str(r.get("family") or "").upper().startswith("UNKNOWN"))
        quality_records.append({
            "essay_id": eid,
            "row_count": len(rec.get("student_rows", []) if isinstance(rec.get("student_rows"), list) else []),
            "unique_family_count": len([f for f in families if f]),
            "unknown_family_count": unknown_count,
            "task_type": rec.get("task_type"),
            "prompt_present": bool(rec.get("prompt_text")),
            "evaluator_available": rec["evaluator_available"],
            "status": "ok" if rec.get("task_type") and rec.get("prompt_text") and unknown_count == 0 else "needs_attention",
        })

    out["schema_version"] = SCHEMA_VERSION
    out["source_detector_schema_version"] = detector.get("schema_version")
    out["priority_input_builder"] = {
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "boundary": "Input-contract normalization only; Priority Engine still owns priority inference.",
        "evaluator_input_supplied": evaluator_available,
        "records": quality_records,
        "all_records_priority_ready": all(r["status"] == "ok" for r in quality_records),
    }
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build Priority Engine input v1.4.8.")
    ap.add_argument("--detector", required=True, help="Guarded detector JSON.")
    ap.add_argument("--submission", required=True, help="Normalized submission JSON.")
    ap.add_argument("--scorer", required=False, help="Optional scorer output JSON.")
    ap.add_argument("--evaluator", required=False, help="Optional Evaluator/WKE output JSON (v8.3+).")
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)

    detector = read_json(args.detector)
    submission = read_json(args.submission)
    scorer = read_json(args.scorer) if args.scorer else None
    evaluator = read_json(args.evaluator) if args.evaluator else None
    out = build_priority_input(detector, submission, scorer, evaluator)
    if args.strict and not (out.get("priority_input_builder") or {}).get("all_records_priority_ready"):
        raise SystemExit("Priority input is not ready.")
    write_json(args.output, out, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
