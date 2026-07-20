#!/usr/bin/env python3
"""
Priority → Directive Adapter CLI v1.0 — standalone
==================================================

Standalone directive adapter. Imports no previous versions.
It converts Priority Engine output + score contract + optional learner profile
into a routing/directive artifact. It does not detect, score, provide LRET
suggestions, generate writing-coach missions, or create exercises.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "DIRECTIVE_ADAPTER_STANDALONE_V1"
ENGINE_ID = "VA_STELLA_DIRECTIVE_ADAPTER_CLI"
ENGINE_VERSION = "1.0.0-standalone-no-imports"

SERVICE_BY_CAPACITY = {
    "sentence_control": "writing_coach",
    "argument_development": "writing_coach",
    "cohesion_control": "practice",
    "lexical_precision": "lret",
    "academic_style": "lret",
    "task_response_control": "writing_coach",
}


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
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, data: Any, pretty: bool = False) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2 if pretty else None)
        f.write("\n")


def get_score_context(score_contract: Dict[str, Any]) -> Dict[str, Any]:
    released = score_contract.get("released_score") or {}
    if not released and isinstance(score_contract.get("final_score_profile"), dict):
        fp = score_contract["final_score_profile"]
        released = {
            "overall_band": fp.get("overall_band_estimate"),
            "criteria_bands": fp.get("official_criteria_bands") or fp.get("criteria_bands"),
        }
    return {
        "released_score": released,
        "score_confidence": score_contract.get("score_confidence") or score_contract.get("confidence"),
        "score_status": score_contract.get("score_status"),
        "verifier_status": score_contract.get("verifier_status"),
        "adjudication_status": score_contract.get("adjudication_status"),
        "progress_tracking_allowed": score_contract.get("progress_tracking_allowed"),
        "lie_update_allowed": score_contract.get("lie_update_allowed"),
    }


def normalize_focus_areas(priority: Dict[str, Any]) -> List[Dict[str, Any]]:
    focus = priority.get("focus_areas") or priority.get("top_priorities") or priority.get("priorities") or []
    out: List[Dict[str, Any]] = []
    for i, fa in enumerate(focus, start=1):
        if not isinstance(fa, dict):
            continue
        capacity = fa.get("capacity_domain") or fa.get("domain") or fa.get("skill_tag") or "unknown"
        skill = fa.get("skill_tag") or fa.get("skill_id") or capacity
        criterion = fa.get("criterion") or fa.get("rubric") or "unknown"
        evidence = fa.get("evidence_count")
        try:
            evidence = int(evidence)
        except Exception:
            evidence = 0
        out.append({
            "rank": int(fa.get("rank") or i),
            "capacity_domain": capacity,
            "skill_tag": skill,
            "criterion": criterion,
            "evidence_count": evidence,
            "top_families": fa.get("top_families") or fa.get("families") or [],
            "recommended_difficulty": fa.get("recommended_difficulty") or fa.get("difficulty") or "controlled_transfer",
            "priority_reason": fa.get("priority_reason") or fa.get("reason") or "Selected by the Priority Engine.",
        })
    out.sort(key=lambda x: (x.get("rank", 999), -x.get("evidence_count", 0)))
    return out


def build_directive(priority: Dict[str, Any], score_contract: Dict[str, Any], learner_profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    focus_areas = normalize_focus_areas(priority)
    primary = focus_areas[0] if focus_areas else None
    score_context = get_score_context(score_contract or {})
    recommended_service = SERVICE_BY_CAPACITY.get(primary.get("capacity_domain") if primary else "", "writing_coach") if primary else None
    directive = {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "boundary": "Directive adapter converts upstream priority and score contract into routing metadata only.",
        "focus_areas": focus_areas,
        "primary_focus": primary,
        "score_context": score_context,
        "score_confidence": score_context.get("score_confidence"),
        "adjudication_status": score_context.get("adjudication_status"),
        "progress_tracking_allowed": score_context.get("progress_tracking_allowed"),
        "gold_learning_directive": {
            "next_best_skill": primary.get("skill_tag") if primary else None,
            "next_best_capacity_domain": primary.get("capacity_domain") if primary else None,
            "recommended_service": recommended_service,
            "learning_update_allowed": bool(score_context.get("lie_update_allowed", True)),
            "mastery_update_allowed": False,
            "reason": primary.get("priority_reason") if primary else "No primary focus available.",
        },
        "learner_profile_context": {
            "profile_supplied": learner_profile is not None,
            "profile_version": learner_profile.get("profile_version") if isinstance(learner_profile, dict) else None,
        },
    }
    return directive


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build directive from Priority Engine output and score contract; standalone.")
    ap.add_argument("--priority", required=True)
    ap.add_argument("--score-contract", required=True)
    ap.add_argument("--learner-profile", default=None)
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)
    priority = read_json(args.priority)
    contract = read_json(args.score_contract)
    profile = read_json(args.learner_profile, required=False)
    directive = build_directive(priority, contract, profile)
    write_json(args.output, directive, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
