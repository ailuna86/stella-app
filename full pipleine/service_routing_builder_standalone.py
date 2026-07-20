#!/usr/bin/env python3
"""
Gold Service Routing Builder — standalone
=========================================

Builds the final Gold service-routing artifact from directive, score contract,
learner profile, and generated service artifacts. Imports no previous versions.

Boundary:
- Does not score, detect, evaluate, classify lexical candidates, coach, or build practice.
- Only records what service outputs exist and what the next routing action is.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, List

SCHEMA_VERSION = "GOLD_SERVICE_ROUTING_STANDALONE_V1"
ENGINE_ID = "VA_STELLA_SERVICE_ROUTING_BUILDER"
ENGINE_VERSION = "1.0.0-standalone-no-imports"


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


def write_json(path: str, data: Any, pretty: bool = False) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2 if pretty else None) + "\n", encoding="utf-8")


def artifact_record(path: Optional[str], obj: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    exists = bool(path and Path(path).exists())
    return {
        "path": str(Path(path).resolve()) if path else None,
        "exists": exists,
        "schema_version": obj.get("schema_version") if isinstance(obj, dict) else None,
        "engine_id": obj.get("engine_id") if isinstance(obj, dict) else None,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build final Gold service routing artifact.")
    ap.add_argument("--directive", required=True)
    ap.add_argument("--score-contract", required=True)
    ap.add_argument("--learner-profile", required=True)
    ap.add_argument("--lret")
    ap.add_argument("--writing-coach")
    ap.add_argument("--practice")
    ap.add_argument("--revision-workspace")
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)

    directive = read_json(args.directive)
    contract = read_json(args.score_contract)
    profile = read_json(args.learner_profile)
    lret = read_json(args.lret, False)
    coach = read_json(args.writing_coach, False)
    practice = read_json(args.practice, False)
    revision = read_json(args.revision_workspace, False)

    gld = directive.get("gold_learning_directive") or {}
    next_action = profile.get("next_best_action") or {}
    routing = {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "student_id": contract.get("student_id") or profile.get("student_id"),
        "essay_id": contract.get("essay_id") or profile.get("latest_essay_id"),
        "released_score": contract.get("released_score"),
        "score_status": contract.get("score_status"),
        "student_score_release": contract.get("student_score_release"),
        "next_best_service": gld.get("recommended_service") or next_action.get("recommended_service"),
        "next_best_capacity_domain": gld.get("next_best_capacity_domain") or next_action.get("capacity_domain"),
        "next_best_skill": gld.get("next_best_skill") or next_action.get("skill_tag"),
        "available_service_outputs": {
            "lret_session": artifact_record(args.lret, lret),
            "writing_coach": artifact_record(args.writing_coach, coach),
            "practice_session": artifact_record(args.practice, practice),
            "revision_workspace": artifact_record(args.revision_workspace, revision),
        },
        "recommended_sequence": [
            "read_feedback_report",
            "complete_next_best_service",
            "complete_practice_session",
            "revise_essay",
            "submit_revision_for_comparison",
        ],
        "boundary": "Routing artifact only; all educational/service outputs are produced by their own engines.",
    }
    write_json(args.output, routing, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
