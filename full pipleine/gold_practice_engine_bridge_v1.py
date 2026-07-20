#!/usr/bin/env python3
"""
Gold Practice Engine Bridge v1.0
==================================

Standalone Gold-contract CLI wrapper around Premium's adaptive
PracticeEngineV5 (practice_engine_v5b.py -- the closest available version;
v5c is referenced by Premium's own current runner but was not present in
either the Gold folder or the uploaded Premium package, see the master
blueprint's open items).

This replaces gold_practice_session_builder_standalone_v1_4_3.py, which only
selected a static batch of exercises from the bank by directive focus and
had no session mechanics. This bridge ports PracticeEngineV5's adaptive
session-building mechanics (CEFR-floor/ceiling aware allocation, cross-
session seen-exercise exclusion, family-tag translation) into a Gold
JSON-in/JSON-out subprocess stage.

Scope decision: this bridge PREPARES a session (start_session +
set_session_length, which builds the adaptive exercise queue) but does not
call get_next_exercise()/submit_answer() in a loop to play the session out
synchronously. Practice, like Essay Revision, is a second, separate learner
action -- the student answers exercises interactively in the app after this
artifact is produced, not inside a single pipeline run. A companion "submit"
script would call PracticeEngineV5.submit_answer() per exercise when the
student actually answers; that is out of scope for this bridge.

Gold-specific enrichment over Premium's runner:
- --evaluator (optional): if supplied, Evaluator's practice_engine_payload
  gap_targets_for_practice are merged into directive.focus_areas before
  session building, so exercise selection can reflect Evaluator's
  competence-evaluation evidence, not only Priority/Directive's
  error-count-driven focus areas.
- --prior-profile (optional): passed straight through as `learner_profile`
  to start_session(), matching Premium's continuity pattern
  (pe_engine.start_session(..., learner_profile=prior_profile, ...)) --
  produces the "last time you..." recap text.

Boundary:
- Does not generate new exercises; selects from the supplied exercise bank.
- Does not score or grade; grading happens when answers are actually
  submitted, in a later action.
- Does not alter PracticeEngineV5's own allocation/selection logic.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ENGINE_ID = "VA_STELLA_GOLD_PRACTICE_ENGINE_BRIDGE"
ENGINE_VERSION = "1.0.0-wraps-practice_engine_v5b"
SCHEMA_VERSION = "GOLD_PRACTICE_SESSION_ADAPTIVE_V1"


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


def enrich_focus_areas_with_evaluator(
    directive: Dict[str, Any], evaluator: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Merge Evaluator's practice_engine_payload.gap_targets_for_practice into
    directive.focus_areas as additional low-confidence candidates, without
    displacing Directive's own ranked focus areas. Additive only."""
    if not isinstance(evaluator, dict):
        return directive

    payloads = evaluator.get("consumer_payloads") or {}
    pep = payloads.get("practice_engine_payload") or {}
    gap_targets = pep.get("gap_targets_for_practice") or []
    if not gap_targets:
        return directive

    existing_domains = {
        fa.get("capacity_domain") or fa.get("skill_tag")
        for fa in (directive.get("focus_areas") or [])
        if isinstance(fa, dict)
    }
    added = []
    next_rank = len(directive.get("focus_areas") or []) + 1
    for g in gap_targets:
        if not isinstance(g, dict):
            continue
        domain = g.get("skill_id") or g.get("domain")
        if not domain or domain in existing_domains:
            continue
        added.append({
            "rank": next_rank,
            "capacity_domain": domain,
            "skill_tag": domain,
            "skill_id": domain,
            "student_label": g.get("skill_name") or domain,
            "criterion": g.get("domain") or "unspecified",
            "evidence_count": 0,
            "priority_level": "monitor",
            "source": "evaluator_practice_engine_payload_gap_target",
        })
        next_rank += 1

    if not added:
        return directive

    out = dict(directive)
    out["focus_areas"] = list(directive.get("focus_areas") or []) + added
    out["_evaluator_gap_targets_merged"] = len(added)
    return out


def build_session_package(
    engine_cls,
    bank_path: str,
    session_dir: str,
    seen_ids_path: Optional[str],
    student_id: str,
    directive: Dict[str, Any],
    prior_profile: Optional[Dict[str, Any]],
    session_id: Optional[str],
    minutes: int,
) -> Dict[str, Any]:
    pe = engine_cls(bank_path=bank_path, session_dir=session_dir, seen_ids_path=seen_ids_path)

    welcome = pe.start_session(
        student_id=student_id,
        directive=directive,
        learner_profile=prior_profile,
        session_id=session_id,
    )
    sid = welcome["session_id"]
    plan = pe.set_session_length(sid, minutes=minutes)

    # Session mechanics store the built queue in the session file; read it
    # back directly rather than draining it through get_next_exercise(),
    # since answering is a separate, later learner action (see module
    # docstring).
    session_data = pe._store.load(sid) or {}
    exercise_details = session_data.get("exercise_details", {})
    exercise_queue_ids = session_data.get("exercise_queue", [])
    exercises = [exercise_details[eid] for eid in exercise_queue_ids if eid in exercise_details]

    # Student-facing exercise view: same shape get_next_exercise() would
    # have produced (answer/explanation retained -- these are already
    # revealed-on-completion fields in v5b, consistent with F25).
    student_view = []
    for idx, ex in enumerate(exercises):
        student_view.append({
            "exercise_number": idx + 1,
            "total_exercises": len(exercises),
            "exercise_id": ex.get("exercise_id"),
            "exercise_type": ex.get("exercise_type", ""),
            "family": ex.get("family", ""),
            "family_label": ex.get("family_label", ""),
            "cefr_level": ex.get("cefr_level", ""),
            "micro_skill": ex.get("micro_skill", ""),
            "prompt": ex.get("prompt", ""),
            "answer": ex.get("answer", ""),
            "explanation": ex.get("explanation", ""),
            "choices": ex.get("choices"),
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "wrapped_engine": "PracticeEngineV5 (practice_engine_v5b.py)",
        "student_id": student_id,
        "session_id": sid,
        "welcome": welcome,
        "session_plan": plan,
        "exercises": student_view,
        "exercise_count": len(student_view),
        "status": "ready_for_student" if student_view else "no_exercises_available",
        "next_action": "student_answers_exercises_then_submit_answer_and_get_session_results",
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build an adaptive Gold practice session (ported from Premium's PracticeEngineV5).")
    ap.add_argument("--directive", required=True)
    ap.add_argument("--score-contract", required=False)
    ap.add_argument("--evaluator", required=False, help="Optional Evaluator/WKE output; merges practice_engine_payload gap targets into focus areas.")
    ap.add_argument("--prior-profile", required=False, help="Optional prior_context.json (or persisted learner profile) for session-recap continuity.")
    ap.add_argument("--exercise-bank", required=True)
    ap.add_argument("--session-dir", required=True)
    ap.add_argument("--seen-ids-path", required=False)
    ap.add_argument("--student-id", required=True)
    ap.add_argument("--session-id", required=False)
    ap.add_argument("--minutes", type=int, default=10)
    ap.add_argument("--engine-module-dir", required=False, help="Directory containing practice_engine_v5b.py, if not already importable.")
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)

    if args.engine_module_dir:
        sys.path.insert(0, args.engine_module_dir)
    try:
        from practice_engine_v5b import PracticeEngineV5
    except ImportError as exc:
        raise SystemExit(
            f"Could not import PracticeEngineV5 from practice_engine_v5b.py "
            f"(pass --engine-module-dir if it isn't next to this bridge): {exc}"
        )

    directive = read_json(args.directive)
    evaluator = read_json(args.evaluator, required=False)
    prior_context = read_json(args.prior_profile, required=False)
    # Accept either a raw learner profile or a prior_context.json wrapper
    # (gold_session_continuity_loader_v1.py output) transparently.
    prior_profile = None
    if isinstance(prior_context, dict):
        prior_profile = prior_context.get("prior_learner_profile") if "prior_learner_profile" in prior_context else prior_context

    directive = enrich_focus_areas_with_evaluator(directive, evaluator)

    out = build_session_package(
        engine_cls=PracticeEngineV5,
        bank_path=args.exercise_bank,
        session_dir=args.session_dir,
        seen_ids_path=args.seen_ids_path,
        student_id=args.student_id,
        directive=directive,
        prior_profile=prior_profile,
        session_id=args.session_id,
        minutes=args.minutes,
    )
    write_json(args.output, out, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
