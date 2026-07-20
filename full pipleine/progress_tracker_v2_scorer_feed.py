#!/usr/bin/env python3
"""
Progress Tracker v2 Scorer Feed — standalone
============================================

Standalone progress tracker for appending released score-contract events to a
learner progress profile. Imports no previous versions.

Boundary:
- Tracks score events and simple criterion history only.
- Does not score essays.
- Does not perform LIE skill inference.
- Gold LIE may replace this in the final Gold product; this file exists to
  remove missing dependencies from older progress-tracker workflows.

2.0.1 fix: update_profile() previously used profile.setdefault(...) for
schema_version/engine_id/engine_version. If --profile pointed at any JSON
that already had those keys set (e.g. a foreign-schema profile, or the
gold_session_continuity_loader_v1.py prior_context wrapper), this script's
own identity was silently discarded and the output artifact reported
someone else's schema_version/engine_id instead of its own. These three
fields are now force-overwritten every run, regardless of what --profile
contained, so this script's output always self-identifies correctly. The
Gold orchestrator also now wires --profile to this script's own persisted
prior output (via gold_progress_tracker_persist_v1.py), not prior_context,
so score_events actually accumulate across essays instead of resetting.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

SCHEMA_VERSION = "PROGRESS_TRACKER_V2_SCORER_FEED_STANDALONE"
ENGINE_ID = "VA_STELLA_PROGRESS_TRACKER_SCORER_FEED"
ENGINE_VERSION = "2.0.1-standalone-no-imports-identity-fix"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Optional[str], required: bool = True) -> Any:
    if not path:
        if required:
            raise ValueError("missing path")
        return None
    p = Path(path)
    if not p.exists():
        if required:
            raise FileNotFoundError(str(p))
        return None
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, data: Any, pretty: bool = False) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2 if pretty else None)
        f.write("\n")


def released_score_from_contract(contract: Dict[str, Any]) -> Dict[str, Any]:
    score = contract.get("released_score") or {}
    if not score and isinstance(contract.get("final_score_profile"), dict):
        fp = contract["final_score_profile"]
        score = {
            "overall_band": fp.get("overall_band_estimate"),
            "criteria_bands": fp.get("official_criteria_bands") or fp.get("criteria_bands") or {},
        }
    return score


def update_profile(contract: Dict[str, Any], previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    profile = previous if isinstance(previous, dict) else {}
    student_id = contract.get("student_id") or profile.get("student_id") or "student_unknown"
    essay_id = contract.get("essay_id") or "essay_unknown"
    created = now_iso()
    # Force-overwrite this script's own identity every run (see 2.0.1 fix
    # note above) -- setdefault() previously let a foreign --profile input
    # (e.g. prior_context.json) leak its schema_version/engine_id through.
    profile["schema_version"] = SCHEMA_VERSION
    profile["engine_id"] = ENGINE_ID
    profile["engine_version"] = ENGINE_VERSION
    profile["student_id"] = student_id
    profile.setdefault("score_events", [])
    event = {
        "essay_id": essay_id,
        "created_at": contract.get("created_at") or created,
        "recorded_at": created,
        "released_score": released_score_from_contract(contract),
        "score_confidence": contract.get("score_confidence"),
        "score_status": contract.get("score_status"),
        "verifier_status": contract.get("verifier_status"),
        "adjudication_status": contract.get("adjudication_status"),
        "stable_for_trend": bool(contract.get("progress_tracking_allowed", True)),
    }
    # Avoid duplicate essay_id+created_at events.
    existing_keys = {(e.get("essay_id"), e.get("created_at")) for e in profile["score_events"] if isinstance(e, dict)}
    if (event["essay_id"], event["created_at"]) not in existing_keys:
        profile["score_events"].append(event)
    profile["latest_score_event"] = event
    profile["stable_score_events"] = [e for e in profile["score_events"] if e.get("stable_for_trend")]
    profile["last_updated_at"] = created
    return profile


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Append score contract to standalone progress profile.")
    ap.add_argument("--score-contract", required=True)
    ap.add_argument("--profile", help="Existing progress profile JSON")
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)
    contract = read_json(args.score_contract)
    previous = read_json(args.profile, required=False)
    profile = update_profile(contract, previous)
    write_json(args.output, profile, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
