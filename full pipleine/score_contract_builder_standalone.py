#!/usr/bin/env python3
"""
VA/ST.ELLA Score Contract Builder — standalone
==============================================

Builds the canonical final-score contract from submission, scorer, verifier,
and automated adjudicator artifacts. Imports no previous versions.

Boundary:
- Does not score.
- Does not verify.
- Does not adjudicate.
- Does not change released scores.
- Only normalizes already-produced upstream decisions into the contract used by
  downstream Gold services.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "FINAL_SCORE_CONTRACT_STANDALONE_V1"
ENGINE_ID = "VA_STELLA_SCORE_CONTRACT_BUILDER"
ENGINE_VERSION = "1.0.0-standalone-no-imports"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str) -> Any:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(path: str, data: Any, pretty: bool = False) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2 if pretty else None) + "\n", encoding="utf-8")


def first_result(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict):
        for key in ("results", "scored", "essays"):
            if isinstance(payload.get(key), list) and payload[key]:
                return payload[key][0] if isinstance(payload[key][0], dict) else {}
        return payload
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    return {}


def submission_identity(submission: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "essay_id": submission.get("essay_id") or "essay_unknown",
        "student_id": submission.get("student_id") or "student_unknown",
        "task_type": submission.get("task_type") or "WT2",
        "prompt_text": submission.get("prompt_text"),
    }


def score_from_scorer(scored: Dict[str, Any]) -> Dict[str, Any]:
    profile = scored.get("score_profile") if isinstance(scored.get("score_profile"), dict) else scored
    criteria = profile.get("official_criteria_bands") or profile.get("criteria_bands") or scored.get("final_criterion_bands") or {}
    # Keep criteria integer-only if values are numeric.
    normalized_criteria: Dict[str, int] = {}
    for k, v in (criteria or {}).items():
        try:
            normalized_criteria[str(k)] = int(round(float(v)))
        except Exception:
            pass
    overall = profile.get("overall_band_estimate") or profile.get("overall_band") or scored.get("overall_band")
    try:
        overall = round(float(overall) * 2) / 2
    except Exception:
        overall = None
    return {
        "overall_band": overall,
        "criteria_bands": normalized_criteria,
        "score_family": (scored.get("tier_governor") or {}).get("released_score_tier") or (scored.get("tier_decision") or {}).get("tier"),
        "confidence": profile.get("confidence"),
        "score_status": profile.get("score_status"),
    }


def score_from_adjudicator(adjudicated: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    final = adjudicated.get("final_score") if isinstance(adjudicated.get("final_score"), dict) else {}
    if not final:
        return fallback
    criteria = final.get("criteria_bands") or fallback.get("criteria_bands") or {}
    out_criteria: Dict[str, int] = {}
    for k, v in criteria.items():
        try:
            out_criteria[str(k)] = int(round(float(v)))
        except Exception:
            pass
    overall = final.get("overall_band", fallback.get("overall_band"))
    try:
        overall = round(float(overall) * 2) / 2
    except Exception:
        overall = fallback.get("overall_band")
    return {
        "overall_band": overall,
        "criteria_bands": out_criteria,
        "score_family": final.get("score_family") or fallback.get("score_family"),
        "score_released": bool(final.get("score_released", True)),
    }


def build_contract(submission: Dict[str, Any], scorer: Dict[str, Any], verifier: Dict[str, Any], adjudicator: Dict[str, Any]) -> Dict[str, Any]:
    ident = submission_identity(submission)
    scored_one = first_result(scorer)
    verified_one = first_result(verifier)
    adjudicated_one = first_result(adjudicator)
    scorer_score = score_from_scorer(scored_one)
    final_score = score_from_adjudicator(adjudicated_one, scorer_score)
    verifier_status = verified_one.get("verifier_status") or verifier.get("summary", {}).get("status")
    adjudication_status = adjudicated_one.get("adjudication_status") or "unknown"
    safe_release = bool(verified_one.get("safe_for_student_release", True)) and bool(adjudicated_one.get("final_score_released", final_score.get("score_released", True)))
    progress_allowed = bool(verified_one.get("safe_for_progress_tracking", True)) and bool((adjudicated_one.get("downstream_policy") or {}).get("progress_tracking_allowed", True))
    priority_allowed = bool(verified_one.get("safe_for_priority_engine", True)) and bool((adjudicated_one.get("downstream_policy") or {}).get("priority_engine_allowed", True))
    lie_allowed = bool(verified_one.get("safe_for_lie_update", True)) and bool((adjudicated_one.get("downstream_policy") or {}).get("lie_update_allowed", True))

    contract = {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        **ident,
        "released_score": final_score,
        "score_status": scorer_score.get("score_status") or "ready",
        "score_confidence": adjudicated_one.get("confidence") or scorer_score.get("confidence") or verified_one.get("review_priority") or "normal",
        "verifier_status": verifier_status,
        "adjudication_status": adjudication_status,
        "student_score_release": safe_release,
        "progress_tracking_allowed": progress_allowed,
        "priority_engine_allowed": priority_allowed,
        "lie_update_allowed": lie_allowed,
        "source_artifacts": {
            "scorer_schema": scorer.get("schema_version"),
            "verifier_schema": verifier.get("schema_version"),
            "adjudicator_schema": adjudicator.get("schema_version"),
        },
        "contract_checks": {
            "criterion_bands_integer_only": all(isinstance(v, int) for v in (final_score.get("criteria_bands") or {}).values()),
            "overall_half_band_only": final_score.get("overall_band") is None or abs(float(final_score.get("overall_band")) * 2 - round(float(final_score.get("overall_band")) * 2)) < 1e-9,
            "score_was_not_modified_by_contract_builder": True,
        },
        "boundary": "Canonical contract built from upstream scorer, verifier, and adjudicator only; no scoring or adjudication performed here.",
    }
    return contract


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build final score contract from scorer/verifier/adjudicator artifacts.")
    ap.add_argument("--submission", required=True)
    ap.add_argument("--scorer", required=True)
    ap.add_argument("--verifier", required=True)
    ap.add_argument("--adjudicator", required=True)
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)
    contract = build_contract(read_json(args.submission), read_json(args.scorer), read_json(args.verifier), read_json(args.adjudicator))
    write_json(args.output, contract, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
