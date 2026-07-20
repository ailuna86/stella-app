#!/usr/bin/env python3
"""
Directive Adapter v1.4.3 — standalone
=====================================

Converts normalized priority evidence + score contract into a Gold learning
routing directive. Imports no previous versions.

Boundary:
- Does not score, detect, evaluate, coach, classify LRET units, or create practice.
- Only builds a routing/directive artifact from upstream evidence.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "DIRECTIVE_ADAPTER_STANDALONE_V1_4_3"
ENGINE_ID = "VA_STELLA_DIRECTIVE_ADAPTER_CLI"
ENGINE_VERSION = "1.4.3-standalone-no-imports"

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
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(path: str, data: Any, pretty: bool = False) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2 if pretty else None) + "\n", encoding="utf-8")


def get_score_context(score_contract: Dict[str, Any]) -> Dict[str, Any]:
    released = score_contract.get("released_score") or {}
    return {
        "released_score": released,
        "score_confidence": score_contract.get("score_confidence"),
        "score_status": score_contract.get("score_status"),
        "verifier_status": score_contract.get("verifier_status"),
        "adjudication_status": score_contract.get("adjudication_status"),
        "progress_tracking_allowed": score_contract.get("progress_tracking_allowed"),
        "lie_update_allowed": score_contract.get("lie_update_allowed"),
    }


def focus_from_priority(priority: Dict[str, Any]) -> List[Dict[str, Any]]:
    focus = priority.get("focus_areas") if isinstance(priority, dict) else []
    if isinstance(focus, list) and focus:
        out = [dict(x) for x in focus if isinstance(x, dict)]
    else:
        primary = priority.get("primary_focus") if isinstance(priority, dict) else None
        out = [dict(primary)] if isinstance(primary, dict) else []
    cleaned: List[Dict[str, Any]] = []
    for i, item in enumerate(out, start=1):
        cap = item.get("capacity_domain") or item.get("skill_tag") or item.get("skill_id") or "task_response_control"
        skill = item.get("skill_tag") or item.get("skill_id") or cap
        service = item.get("recommended_service") or SERVICE_BY_CAPACITY.get(str(cap), "writing_coach")
        cleaned.append({
            "rank": int(item.get("rank") or i),
            "capacity_domain": cap,
            "skill_tag": skill,
            "skill_id": item.get("skill_id") or skill,
            "student_label": item.get("student_label") or str(skill).replace("_", " ").title(),
            "criterion": item.get("criterion") or item.get("rubric") or "unknown",
            "evidence_count": int(item.get("evidence_count") or 0),
            "weighted_pressure": item.get("weighted_pressure"),
            "priority_level": item.get("priority_level") or "medium",
            "top_families": item.get("top_families") or [],
            "family_counts": item.get("family_counts") or {},
            "recommended_service": service,
            "recommended_difficulty": item.get("recommended_difficulty") or "controlled",
            "priority_reason": item.get("priority_reason") or "Selected by upstream normalized priority evidence.",
            "evidence_samples": item.get("evidence_samples") or [],
        })
    cleaned.sort(key=lambda x: (x.get("rank", 999), -int(x.get("evidence_count") or 0)))
    return cleaned


def fallback_focus_from_errormap(errormap: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(errormap, dict):
        return []
    counts = Counter()
    family_counts: Dict[str, Counter] = {}
    for e in errormap.get("errors", []) or []:
        if not isinstance(e, dict) or e.get("chargeable") is False:
            continue
        cap = str(e.get("capacity_domain") or "task_response_control")
        counts[cap] += 1
        family_counts.setdefault(cap, Counter())[str(e.get("family") or "UNKNOWN_FAMILY")] += 1
    focus = []
    for i, (cap, count) in enumerate(counts.most_common(5), start=1):
        focus.append({
            "rank": i,
            "capacity_domain": cap,
            "skill_tag": cap,
            "skill_id": cap,
            "student_label": cap.replace("_", " ").title(),
            "criterion": "unknown",
            "evidence_count": count,
            "priority_level": "high" if count >= 6 else "medium",
            "top_families": [f for f, _ in family_counts.get(cap, Counter()).most_common(5)],
            "family_counts": dict(family_counts.get(cap, Counter()).most_common()),
            "recommended_service": SERVICE_BY_CAPACITY.get(cap, "writing_coach"),
            "recommended_difficulty": "controlled",
            "priority_reason": "Fallback from ErrorMap capacity counts because no normalized priority focus was available.",
            "evidence_samples": [],
        })
    return focus


def build(priority: Dict[str, Any], score_contract: Dict[str, Any], errormap: Optional[Dict[str, Any]], learner_profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    focus = focus_from_priority(priority)
    source = "priority_normalized"
    if not focus:
        focus = fallback_focus_from_errormap(errormap)
        source = "errormap_fallback"
    primary = focus[0] if focus else None
    score_context = get_score_context(score_contract or {})
    gld = {
        "next_best_skill": primary.get("skill_tag") if primary else None,
        "next_best_capacity_domain": primary.get("capacity_domain") if primary else None,
        "recommended_service": primary.get("recommended_service") if primary else None,
        "learning_update_allowed": bool(score_context.get("lie_update_allowed")),
        "mastery_update_allowed": False,
        "recommended_difficulty": primary.get("recommended_difficulty") if primary else None,
        "reason": primary.get("priority_reason") if primary else "No primary focus available.",
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "boundary": "Directive adapter converts upstream priority and score contract into routing metadata only.",
        "source_priority_schema": priority.get("schema_version") if isinstance(priority, dict) else None,
        "focus_source": source,
        "focus_areas": focus,
        "primary_focus": primary,
        "score_context": score_context,
        "score_confidence": score_context.get("score_confidence"),
        "adjudication_status": score_context.get("adjudication_status"),
        "progress_tracking_allowed": score_context.get("progress_tracking_allowed"),
        "gold_learning_directive": gld,
        "learner_profile_context": {
            "profile_supplied": isinstance(learner_profile, dict),
            "profile_version": learner_profile.get("schema_version") if isinstance(learner_profile, dict) else None,
        },
        "quality_flags": {
            "has_primary_focus": isinstance(primary, dict),
            "has_recommended_service": bool(gld.get("recommended_service")),
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build Gold directive from normalized priority and score contract.")
    ap.add_argument("--priority", required=True)
    ap.add_argument("--score-contract", required=True)
    ap.add_argument("--errormap")
    ap.add_argument("--learner-profile")
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)
    priority = read_json(args.priority)
    contract = read_json(args.score_contract)
    errormap = read_json(args.errormap, required=False)
    profile = read_json(args.learner_profile, required=False)
    out = build(priority, contract, errormap, profile)
    write_json(args.output, out, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
