#!/usr/bin/env python3
"""
Gold Session Continuity Loader v1.0
====================================

Standalone bridge. Runs at the start of a Gold session, before Priority
Engine. It is the missing half of the continuity loop described in the
master blueprint: Premium already reloads a persisted per-student profile
every run (load_prior_learner_profile / load_previous_directive in
pipeline_runner_v14l.py); Gold's orchestrator had no equivalent.

What it does:
- Looks for a previously persisted profile at
    {learner-profiles-dir}/{student_id}_gold_profile.json
  (written by gold_profile_persist_v1.py at the end of a prior run).
- Looks for a previously persisted progress-tracker profile at
    {learner-profiles-dir}/{student_id}_gold_progress_profile.json
- Extracts a previous_directive snapshot from the persisted profile, if one
  was embedded by gold_profile_persist_v1.py.
- Ensures a placeholder Writing Coach state file exists, because Writing
  Coach v1.2.17's --coach-state has no cold-start-tolerant read path of its
  own and errors on a missing file.
- Emits a single prior_context.json artifact that downstream stages read:
  directive_adapter (--learner-profile), the adaptive Practice Engine
  bridge (--prior-profile). Writing Coach's own --coach-state/--state-output
  continuity is handled via the fixed per-student state file this loader
  ensures exists.

Boundary:
- Does not compute new priorities, scores, or learning state.
- Does not invent a profile if none exists -- returns an explicit cold-start
  marker instead.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

ENGINE_ID = "VA_STELLA_GOLD_SESSION_CONTINUITY_LOADER"
ENGINE_VERSION = "1.0.0-standalone-no-imports"
SCHEMA_VERSION = "GOLD_PRIOR_CONTEXT_V1"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_json(path: str, data: Any, pretty: bool = False) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2 if pretty else None)
        f.write("\n")


def build_prior_context(student_id, learner_profile, progress_profile):
    cold_start = learner_profile is None
    continuity = (learner_profile or {}).get("_continuity") or {}
    sessions_analyzed = int(continuity.get("sessions_analyzed", 0) or 0)
    previous_directive = continuity.get("previous_directive_snapshot")

    return {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "student_id": student_id,
        "cold_start": cold_start,
        "sessions_analyzed": sessions_analyzed,
        "prior_learner_profile": learner_profile,
        "prior_progress_profile": progress_profile,
        "previous_directive": previous_directive,
        "note": (
            "cold_start=true means no persisted profile was found for this "
            "student_id; downstream engines should behave cautiously "
            "(cold-start mode) rather than assume high-confidence history."
        ),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Load persisted per-student continuity context for a new Gold session.")
    ap.add_argument("--student-id", required=True)
    ap.add_argument("--learner-profiles-dir", required=True)
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)

    profiles_dir = Path(args.learner_profiles_dir)
    profile_path = profiles_dir / f"{args.student_id}_gold_profile.json"
    progress_path = profiles_dir / f"{args.student_id}_gold_progress_profile.json"

    learner_profile = read_json(profile_path)
    progress_profile = read_json(progress_path)

    coach_state_path = profiles_dir / f"{args.student_id}_writing_coach_state.json"
    if not coach_state_path.exists():
        write_json(str(coach_state_path), {}, pretty=True)

    out = build_prior_context(args.student_id, learner_profile, progress_profile)
    write_json(args.output, out, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
