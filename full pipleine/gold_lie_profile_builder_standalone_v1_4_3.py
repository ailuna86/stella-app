#!/usr/bin/env python3
"""
Gold LIE Profile Builder v1.4.3 — standalone
============================================

Aggregates completed Gold artifacts into a learner-profile snapshot and simple
progress views. Imports no previous versions.

Boundary:
- Does not score, detect, generate feedback, classify LRET candidates, coach, or create practice.
- Only aggregates completed artifact evidence and preserves routing metadata.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "GOLD_LEARNER_PROFILE_STANDALONE_V1_4_3"
ENGINE_ID = "VA_STELLA_GOLD_LIE_PROFILE_BUILDER"
ENGINE_VERSION = "1.4.3-standalone-no-imports"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Optional[str], required: bool = True) -> Optional[Dict[str, Any]]:
    if not path:
        if required:
            raise ValueError("missing path")
        return None
    p = Path(path)
    if not p.exists():
        if required:
            raise FileNotFoundError(str(p))
        return None
    obj = json.loads(p.read_text(encoding="utf-8"))
    return obj if isinstance(obj, dict) else {"value": obj}


def write_json(path: Optional[str], data: Any, pretty: bool = False) -> None:
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2 if pretty else None) + "\n", encoding="utf-8")


def artifact_status(obj: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "available": isinstance(obj, dict),
        "schema_version": obj.get("schema_version") if isinstance(obj, dict) else None,
        "engine_id": obj.get("engine_id") if isinstance(obj, dict) else None,
    }


def latest_error_profile(errormap: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    counts = errormap.get("counts") if isinstance(errormap, dict) and isinstance(errormap.get("counts"), dict) else {}
    by_capacity = errormap.get("counts_by_capacity") if isinstance(errormap, dict) and isinstance(errormap.get("counts_by_capacity"), dict) else {}
    by_criterion = errormap.get("counts_by_criterion") if isinstance(errormap, dict) and isinstance(errormap.get("counts_by_criterion"), dict) else {}
    total = int((errormap.get("summary") or {}).get("error_count") or sum(counts.values()) if isinstance(errormap, dict) else 0)
    return {"by_family": counts, "by_capacity": by_capacity, "by_criterion": by_criterion, "total_chargeable": total}


def focus_areas(priority: Optional[Dict[str, Any]], directive: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for obj in (directive, priority):
        if isinstance(obj, dict) and isinstance(obj.get("focus_areas"), list) and obj.get("focus_areas"):
            return [x for x in obj["focus_areas"] if isinstance(x, dict)]
    return []


def next_action_from(directive: Optional[Dict[str, Any]], focus: List[Dict[str, Any]], errormap: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(directive, dict):
        gld = directive.get("gold_learning_directive") or {}
        if gld.get("next_best_capacity_domain") or gld.get("recommended_service"):
            return {
                "capacity_domain": gld.get("next_best_capacity_domain"),
                "skill_tag": gld.get("next_best_skill"),
                "recommended_service": gld.get("recommended_service"),
                "source": "directive",
            }
    primary = focus[0] if focus else None
    if isinstance(primary, dict):
        return {
            "capacity_domain": primary.get("capacity_domain"),
            "skill_tag": primary.get("skill_tag") or primary.get("skill_id"),
            "recommended_service": primary.get("recommended_service") or "writing_coach",
            "source": "priority_normalized",
        }
    profile = latest_error_profile(errormap)
    bycap = profile.get("by_capacity") or {}
    if bycap:
        cap = max(bycap.items(), key=lambda kv: kv[1])[0]
        return {"capacity_domain": cap, "skill_tag": cap, "recommended_service": "writing_coach", "source": "errormap_fallback"}
    return {"capacity_domain": None, "skill_tag": None, "recommended_service": None, "source": "none"}


def build_skill_progress(error_profile: Dict[str, Any], next_action: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for cap, count in sorted((error_profile.get("by_capacity") or {}).items(), key=lambda kv: (-kv[1], kv[0])):
        service = "writing_coach" if cap in {"sentence_control", "argument_development", "task_response_control"} else "lret" if cap in {"lexical_precision", "academic_style"} else "practice"
        rows.append({
            "capacity_domain": cap,
            "latest_error_count": int(count),
            "status": "priority_gap" if int(count) >= 5 else "monitor",
            "recommended_next_service": next_action.get("recommended_service") if cap == next_action.get("capacity_domain") else service,
        })
    return rows


def build(args: argparse.Namespace) -> Dict[str, Any]:
    submission = read_json(args.submission)
    contract = read_json(args.score_contract)
    errormap = read_json(args.errormap)
    priority = read_json(args.priority, required=False)
    directive = read_json(args.directive, required=False)
    feedback = read_json(args.feedback, required=False)
    evaluator = read_json(args.evaluator, required=False)
    lret = read_json(args.lret, required=False)
    coach = read_json(args.writing_coach, required=False)
    practice = read_json(args.practice, required=False)

    error_profile = latest_error_profile(errormap)
    focus = focus_areas(priority, directive)
    next_action = next_action_from(directive, focus, errormap)
    released = contract.get("released_score") or {}
    profile = {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "student_id": contract.get("student_id") or submission.get("student_id"),
        "latest_essay_id": contract.get("essay_id") or submission.get("essay_id"),
        "latest_score": {
            "overall_band": released.get("overall_band"),
            "criteria_bands": released.get("criteria_bands"),
            "score_confidence": contract.get("score_confidence"),
            "score_status": contract.get("score_status"),
            "progress_tracking_allowed": contract.get("progress_tracking_allowed"),
            "lie_update_allowed": contract.get("lie_update_allowed"),
        },
        "latest_error_profile": error_profile,
        "latest_priority_focus": focus,
        "service_artifact_status": {
            "feedback": artifact_status(feedback),
            "evaluator": artifact_status(evaluator),
            "lret": artifact_status(lret),
            "writing_coach": artifact_status(coach),
            "practice": artifact_status(practice),
        },
        "next_best_action": next_action,
        "learning_state": {
            "profile_update_allowed": bool(contract.get("lie_update_allowed")),
            "trend_update_allowed": bool(contract.get("progress_tracking_allowed")),
            "mastery_update_policy": "evidence_accumulation_only_until_student_attempts_practice_or_coach_tasks",
        },
        "boundary": "LIE profile aggregates completed Gold artifacts only; no scoring, detection, feedback generation, or exercise generation is performed here.",
    }
    return profile


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build Gold learner profile and progress artifacts.")
    ap.add_argument("--submission", required=True)
    ap.add_argument("--score-contract", required=True)
    ap.add_argument("--errormap", required=True)
    ap.add_argument("--priority")
    ap.add_argument("--directive")
    ap.add_argument("--feedback")
    ap.add_argument("--evaluator")
    ap.add_argument("--lret")
    ap.add_argument("--writing-coach")
    ap.add_argument("--practice")
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--skills-progress-output")
    ap.add_argument("--learning-roadmap-output")
    ap.add_argument("--progress-snapshot-output")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)
    profile = build(args)
    write_json(args.output, profile, args.pretty)
    skills = {
        "schema_version": "GOLD_SKILLS_PROGRESS_STANDALONE_V1_4_3",
        "created_at": now_iso(),
        "student_id": profile.get("student_id"),
        "latest_essay_id": profile.get("latest_essay_id"),
        "skills": build_skill_progress(profile.get("latest_error_profile") or {}, profile.get("next_best_action") or {}),
        "boundary": "Progress view derived from latest profile evidence only.",
    }
    roadmap = {
        "schema_version": "GOLD_LEARNING_ROADMAP_STANDALONE_V1_4_3",
        "created_at": now_iso(),
        "student_id": profile.get("student_id"),
        "roadmap": [
            {"phase": 1, "focus": (profile.get("next_best_action") or {}).get("capacity_domain"), "service": (profile.get("next_best_action") or {}).get("recommended_service"), "goal": "Stabilize the highest-priority weakness from the latest essay."},
            {"phase": 2, "focus": "controlled_transfer", "service": "practice", "goal": "Transfer the skill to short controlled tasks."},
            {"phase": 3, "focus": "revision", "service": "essay_revision", "goal": "Apply the skill in a revised essay."},
        ],
        "boundary": "Roadmap orders upstream priorities; it does not generate lessons or scores.",
    }
    progress = {
        "schema_version": "GOLD_PROGRESS_SNAPSHOT_STANDALONE_V1_4_3",
        "created_at": now_iso(),
        "student_id": profile.get("student_id"),
        "latest_score": profile.get("latest_score"),
        "next_best_action": profile.get("next_best_action"),
        "boundary": "Snapshot only; no scoring or mastery update beyond aggregation.",
    }
    write_json(args.skills_progress_output, skills, args.pretty)
    write_json(args.learning_roadmap_output, roadmap, args.pretty)
    write_json(args.progress_snapshot_output, progress, args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
