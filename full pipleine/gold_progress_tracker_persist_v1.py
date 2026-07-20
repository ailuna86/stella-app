#!/usr/bin/env python3
"""
Gold Progress Tracker Persist v1.0
====================================

Standalone bridge. Runs immediately after the "progress_tracker" stage
(progress_tracker_v2_scorer_feed.py) in every Gold session. This is the
write-side of Progress Tracker's OWN continuity -- separate from, and
upstream of, gold_profile_persist_v1.py (which persists the LIE learner
profile, not the Progress Tracker score-event history).

Why this exists (bug found via a real orchestrator run against student_123):
progress_tracker_v2_scorer_feed.py accumulates score_events by merging onto
whatever --profile it's given. Nothing was ever writing that output back to
a fixed per-student path, so every session's --profile input was either
empty or (worse, in v1.4.8) the unrelated prior_context.json wrapper --
score_events could never actually accumulate across essays, and the
progress_tracker artifact even inherited prior_context's schema_version/
engine_id (see the 2.0.1 fix in progress_tracker_v2_scorer_feed.py).

This script closes that loop:
- Takes this session's freshly-built progress-tracker artifact
  (02e_gold_progress_tracker.json).
- Writes it verbatim (plus a small _persist stamp) to a fixed per-student
  path: {learner_profiles_dir}/{student_id}_gold_progress_profile.json.
- The Gold orchestrator wires the NEXT session's "progress_tracker" stage's
  --profile argument directly at that same fixed path, so
  progress_tracker_v2_scorer_feed.py's update_profile() receives its own
  real prior history (score_events, stable_score_events, ...) instead of a
  foreign or empty object.

Boundary:
- Does not compute scores, trends, or skill inference -- purely persists
  what progress_tracker_v2_scorer_feed.py already built for this session.
- Does not gate on progress_tracking_allowed itself -- that gating already
  happened inside progress_tracker_v2_scorer_feed.py's own
  stable_for_trend flag per event; this script persists history
  unconditionally so even not-stable-for-trend events remain visible for
  audit, matching Progress Tracker's own design.
"""
from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

ENGINE_ID = "VA_STELLA_GOLD_PROGRESS_TRACKER_PERSIST"
ENGINE_VERSION = "1.0.0-standalone-no-imports"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str) -> Any:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, data: Any, pretty: bool = False) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2 if pretty else None)
        f.write("\n")


def build_persisted(progress_tracker: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    out = copy.deepcopy(progress_tracker)
    out["_persist"] = {
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "persisted_at": now_iso(),
        "last_session_id": session_id,
        "score_event_count": len(out.get("score_events") or []),
    }
    return out


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Persist this session's Progress Tracker profile to the fixed per-student continuity path.")
    ap.add_argument("--progress-tracker", required=True, help="This session's 02e_gold_progress_tracker.json")
    ap.add_argument("--student-id", required=True)
    ap.add_argument("--session-id", required=True)
    ap.add_argument("--learner-profiles-dir", required=True, help="Fixed directory to persist {student_id}_gold_progress_profile.json into.")
    ap.add_argument("--output", "-o", required=True, help="Also write this session's own copy of the persisted artifact here.")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)

    progress_tracker = read_json(args.progress_tracker)
    out = build_persisted(progress_tracker, args.session_id)

    fixed_path = Path(args.learner_profiles_dir) / f"{args.student_id}_gold_progress_profile.json"
    write_json(str(fixed_path), out, pretty=args.pretty)
    write_json(args.output, out, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
