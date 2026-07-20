#!/usr/bin/env python3
"""
Gold Profile Persist v1.0
==========================

Standalone bridge. Runs at the end of a Gold session, after
gold_lie_profile_builder_standalone_v1_4_3.py has built this session's
learner profile. This is the write-side of the continuity loop (see
gold_session_continuity_loader_v1.py for the read-side, and the master
blueprint's continuity-loop section for the full design this mirrors --
Premium's update_learner_profile_v4 + persisted {student_id}_profile.json
pattern in pipeline_runner_v14l.py).

What it does:
- Takes this session's freshly-built learner profile
  (08_gold_learner_profile.json) and the prior_context artifact produced by
  gold_session_continuity_loader_v1.py at session start.
- Gated on the score contract's lie_update_allowed flag, same rule Premium
  uses: if the score confidence for this essay was reduced/low, the stable
  profile is not advanced -- the prior profile is carried forward unchanged
  and the session is recorded as observed-only.
- When allowed, merges a small `_continuity` block into the profile
  (sessions_analyzed incremented, this essay's directive snapshot saved for
  the next run's Priority Engine/Directive continuity, last session id/time)
  and writes the result to the fixed per-student path so the next session's
  continuity loader can find it.

Boundary:
- Does not recompute skill scores, priorities, or mastery -- it only carries
  forward and lightly annotates what gold_lie_profile_builder already built.
- Does not decide progress_tracking_allowed / lie_update_allowed -- those
  come from the score contract, same as every other Gold gate.
"""
from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

ENGINE_ID = "VA_STELLA_GOLD_PROFILE_PERSIST"
ENGINE_VERSION = "1.0.0-standalone-no-imports"
SCHEMA_VERSION = "GOLD_LEARNER_PROFILE_PERSISTED_V1"


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
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2 if pretty else None)
        f.write("\n")


def build_persisted_profile(
    learner_profile: Dict[str, Any],
    prior_context: Optional[Dict[str, Any]],
    score_contract: Dict[str, Any],
    directive: Optional[Dict[str, Any]],
    session_id: str,
) -> Dict[str, Any]:
    lie_update_allowed = bool(score_contract.get("lie_update_allowed", False))
    prior_profile = (prior_context or {}).get("prior_learner_profile") or {}
    prior_continuity = prior_profile.get("_continuity") or {}
    prior_sessions = int(prior_continuity.get("sessions_analyzed", 0) or 0)

    out = copy.deepcopy(learner_profile)
    out["schema_version"] = SCHEMA_VERSION
    out["_persist"] = {
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "persisted_at": now_iso(),
        "lie_update_allowed": lie_update_allowed,
    }

    if lie_update_allowed:
        out["_continuity"] = {
            "sessions_analyzed": prior_sessions + 1,
            "last_session_id": session_id,
            "last_updated_at": now_iso(),
            "previous_directive_snapshot": directive,
        }
        out["_persist"]["action"] = "profile_advanced"
    else:
        # Score confidence for this essay was reduced/low -- carry the prior
        # stable profile forward unchanged rather than advancing it on
        # untrustworthy evidence. This session is recorded as observed-only
        # (the caller should still keep this session's own artifact for
        # audit; only the persisted/reloaded copy is held back).
        if prior_profile:
            out = copy.deepcopy(prior_profile)
            out["schema_version"] = SCHEMA_VERSION
        out["_continuity"] = {
            **prior_continuity,
            "sessions_analyzed": prior_sessions,
            "last_observed_only_session_id": session_id,
            "last_observed_only_at": now_iso(),
        }
        out["_persist"]["action"] = "held_back_observed_only"
        out["_persist"]["reason"] = "lie_update_allowed=false (reduced/low score confidence for this essay)"

    return out


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Persist this session's learner profile to the fixed per-student continuity path.")
    ap.add_argument("--learner-profile", required=True, help="This session's 08_gold_learner_profile.json")
    ap.add_argument("--prior-context", required=False, help="Output of gold_session_continuity_loader_v1.py")
    ap.add_argument("--score-contract", required=True)
    ap.add_argument("--directive", required=False)
    ap.add_argument("--session-id", required=True)
    ap.add_argument("--learner-profiles-dir", required=True, help="Fixed directory to persist {student_id}_gold_profile.json into.")
    ap.add_argument("--output", "-o", required=True, help="Also write this session's own copy of the persisted artifact here.")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)

    learner_profile = read_json(args.learner_profile)
    prior_context = read_json(args.prior_context, required=False)
    score_contract = read_json(args.score_contract)
    directive = read_json(args.directive, required=False)

    out = build_persisted_profile(learner_profile, prior_context, score_contract, directive, args.session_id)

    student_id = out.get("student_id") or "student_unknown"
    fixed_path = Path(args.learner_profiles_dir) / f"{student_id}_gold_profile.json"
    write_json(str(fixed_path), out, pretty=args.pretty)
    write_json(args.output, out, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
