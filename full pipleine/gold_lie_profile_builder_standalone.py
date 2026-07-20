#!/usr/bin/env python3
"""
Gold Learning Intelligence Profile Builder — standalone
======================================================

Builds the Gold learner-profile artifacts from completed downstream engine
outputs. Imports no previous versions.

Boundary:
- Does not score essays.
- Does not detect errors.
- Does not generate feedback, LRET, Writing Coach, or Practice content.
- It aggregates and normalizes evidence from upstream Gold artifacts into a
  learner profile, skills-progress view, roadmap, and progress snapshot.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "GOLD_LEARNER_PROFILE_STANDALONE_V1"
ENGINE_ID = "VA_STELLA_GOLD_LIE_PROFILE_BUILDER"
ENGINE_VERSION = "1.0.0-standalone-no-imports"


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


def write_json(path: Optional[str], data: Any, pretty: bool = False) -> None:
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2 if pretty else None) + "\n", encoding="utf-8")


def score_summary(contract: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "overall_band": (contract.get("released_score") or {}).get("overall_band"),
        "criteria_bands": (contract.get("released_score") or {}).get("criteria_bands") or {},
        "score_confidence": contract.get("score_confidence"),
        "score_status": contract.get("score_status"),
        "progress_tracking_allowed": contract.get("progress_tracking_allowed"),
        "lie_update_allowed": contract.get("lie_update_allowed"),
    }


def errormap_counts(errormap: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    by_family, by_capacity, by_criterion = Counter(), Counter(), Counter()
    if isinstance(errormap, dict):
        for e in errormap.get("errors", []) or []:
            if not isinstance(e, dict) or not e.get("chargeable", True):
                continue
            if e.get("family"):
                by_family[str(e.get("family"))] += 1
            if e.get("capacity_domain"):
                by_capacity[str(e.get("capacity_domain"))] += 1
            if e.get("criterion"):
                by_criterion[str(e.get("criterion"))] += 1
    return {
        "by_family": dict(by_family),
        "by_capacity": dict(by_capacity),
        "by_criterion": dict(by_criterion),
        "total_chargeable": sum(by_family.values()),
    }


def evaluator_summary(evaluator: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(evaluator, dict):
        return {"available": False}
    profile = evaluator.get("writing_knowledge_profile") or evaluator.get("skill_profile") or {}
    payloads = evaluator.get("consumer_payloads") or {}
    strengths = payloads.get("current_strengths") or profile.get("current_strengths") or []
    priorities = payloads.get("priority_learning_targets") or payloads.get("learning_priorities") or []
    return {
        "available": True,
        "schema_version": evaluator.get("schema_version"),
        "qa_status": (evaluator.get("qa") or {}).get("status"),
        "current_strength_count": len(strengths) if isinstance(strengths, list) else 0,
        "learning_priority_count": len(priorities) if isinstance(priorities, list) else 0,
        "consumer_payloads_present": bool(payloads),
    }


def artifact_available(obj: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        return {"available": False}
    return {"available": True, "schema_version": obj.get("schema_version"), "engine_id": obj.get("engine_id")}


def build_profile(submission: Dict[str, Any], contract: Dict[str, Any], priority: Optional[Dict[str, Any]], feedback: Optional[Dict[str, Any]], evaluator: Optional[Dict[str, Any]], lret: Optional[Dict[str, Any]], coach: Optional[Dict[str, Any]], practice: Optional[Dict[str, Any]], errormap: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    student_id = submission.get("student_id") or contract.get("student_id") or "student_unknown"
    essay_id = submission.get("essay_id") or contract.get("essay_id") or "essay_unknown"
    counts = errormap_counts(errormap)
    score = score_summary(contract)
    focus = []
    if isinstance(priority, dict):
        focus = priority.get("focus_areas") or priority.get("priorities") or priority.get("top_priorities") or []
    profile = {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "student_id": student_id,
        "latest_essay_id": essay_id,
        "latest_score": score,
        "latest_error_profile": counts,
        "latest_priority_focus": focus[:8] if isinstance(focus, list) else [],
        "service_artifact_status": {
            "feedback": artifact_available(feedback),
            "evaluator": evaluator_summary(evaluator),
            "lret": artifact_available(lret),
            "writing_coach": artifact_available(coach),
            "practice": artifact_available(practice),
        },
        "next_best_action": None,
        "learning_state": {
            "profile_update_allowed": bool(contract.get("lie_update_allowed", True)),
            "trend_update_allowed": bool(contract.get("progress_tracking_allowed", True)),
            "mastery_update_policy": "evidence_accumulation_only_until_student_attempts_practice_or_coach_tasks",
        },
        "boundary": "LIE profile aggregates completed Gold artifacts only; no scoring, detection, feedback generation, or exercise generation is performed here.",
    }
    if profile["latest_priority_focus"]:
        first = profile["latest_priority_focus"][0]
        if isinstance(first, dict):
            profile["next_best_action"] = {
                "capacity_domain": first.get("capacity_domain"),
                "skill_tag": first.get("skill_tag"),
                "recommended_service": (priority.get("recommended_service") if isinstance(priority, dict) else None) or ((priority.get("gold_learning_directive") or {}).get("recommended_service") if isinstance(priority, dict) else None),
            }
    if not profile["next_best_action"]:
        top_capacity = next(iter(counts["by_capacity"].keys()), None)
        profile["next_best_action"] = {"capacity_domain": top_capacity, "skill_tag": top_capacity, "recommended_service": "writing_coach"}
    return profile


def build_skills_progress(profile: Dict[str, Any]) -> Dict[str, Any]:
    counts = profile.get("latest_error_profile", {}).get("by_capacity", {})
    rows = []
    for cap, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
        rows.append({
            "capacity_domain": cap,
            "latest_error_count": count,
            "status": "priority_gap" if count >= 5 else "monitor",
            "recommended_next_service": profile.get("next_best_action", {}).get("recommended_service") if cap == profile.get("next_best_action", {}).get("capacity_domain") else "practice",
        })
    return {
        "schema_version": "GOLD_SKILLS_PROGRESS_STANDALONE_V1",
        "created_at": now_iso(),
        "student_id": profile.get("student_id"),
        "latest_essay_id": profile.get("latest_essay_id"),
        "skills": rows,
        "boundary": "Progress view derived from latest profile evidence only.",
    }


def build_roadmap(profile: Dict[str, Any]) -> Dict[str, Any]:
    next_action = profile.get("next_best_action") or {}
    return {
        "schema_version": "GOLD_LEARNING_ROADMAP_STANDALONE_V1",
        "created_at": now_iso(),
        "student_id": profile.get("student_id"),
        "roadmap": [
            {"phase": 1, "focus": next_action.get("capacity_domain"), "service": next_action.get("recommended_service"), "goal": "Stabilize the highest-priority weakness from the latest essay."},
            {"phase": 2, "focus": "controlled_transfer", "service": "practice", "goal": "Transfer the skill to short controlled writing tasks."},
            {"phase": 3, "focus": "revision", "service": "essay_revision", "goal": "Apply the skill in a revised essay."},
        ],
        "boundary": "Roadmap orders upstream priorities; it does not generate lessons or scores.",
    }


def build_snapshot(profile: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": "GOLD_PROGRESS_SNAPSHOT_STANDALONE_V1",
        "created_at": now_iso(),
        "student_id": profile.get("student_id"),
        "latest_essay_id": profile.get("latest_essay_id"),
        "latest_score": profile.get("latest_score"),
        "next_best_action": profile.get("next_best_action"),
        "update_allowed": profile.get("learning_state", {}).get("trend_update_allowed"),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build Gold learner profile and optional progress artifacts.")
    ap.add_argument("--submission", required=True)
    ap.add_argument("--score-contract", required=True)
    ap.add_argument("--errormap")
    ap.add_argument("--priority")
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
    profile = build_profile(
        read_json(args.submission), read_json(args.score_contract), read_json(args.priority, False),
        read_json(args.feedback, False), read_json(args.evaluator, False), read_json(args.lret, False),
        read_json(args.writing_coach, False), read_json(args.practice, False), read_json(args.errormap, False),
    )
    write_json(args.output, profile, pretty=args.pretty)
    write_json(args.skills_progress_output, build_skills_progress(profile), pretty=args.pretty)
    write_json(args.learning_roadmap_output, build_roadmap(profile), pretty=args.pretty)
    write_json(args.progress_snapshot_output, build_snapshot(profile), pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
