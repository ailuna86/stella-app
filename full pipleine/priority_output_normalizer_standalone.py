#!/usr/bin/env python3
"""
Priority Output Normalizer v1.4.3 — standalone
==============================================

This targeted bridge creates a directive-ready priority contract from the raw
Priority Engine output plus ErrorMap evidence. It imports no previous versions.

Why it exists:
Some Priority Engine versions can output UNKNOWN_SKILL when detector-family
names do not match its registry. This bridge does not invent essay-specific
rules; it uses universal ErrorMap capacity_domain/family evidence to build a
safe focus_areas list for downstream routing.

Boundary:
- Does not detect errors.
- Does not score essays.
- Does not generate feedback, exercises, LRET labels, or coaching tasks.
- Only normalizes priority evidence into a stable downstream contract.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = "PRIORITY_NORMALIZED_V1_4_3"
ENGINE_ID = "VA_STELLA_PRIORITY_OUTPUT_NORMALIZER"
ENGINE_VERSION = "1.4.3-standalone-no-imports"

CAPACITY_TO_SKILL = {
    "sentence_control": ("sentence_control", "Sentence Control", "grammar"),
    "lexical_precision": ("lexical_precision", "Lexical Precision", "lexical_resource"),
    "academic_style": ("academic_style", "Academic Style", "lexical_resource"),
    "argument_development": ("argument_development", "Argument Development", "task_response"),
    "cohesion_control": ("cohesion_control", "Cohesion Control", "coherence_cohesion"),
    "task_response_control": ("task_response_control", "Task Response Control", "task_response"),
}
CRITERION_BY_FAMILY_PREFIX = {
    "G_": "grammar",
    "L_": "lexical_resource",
    "S_": "lexical_resource",
    "A_": "task_response",
    "C_": "coherence_cohesion",
}
SEVERITY_WEIGHT = {"critical": 1.4, "high": 1.25, "medium": 1.0, "low": 0.55}
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


def read_json(path: str) -> Any:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(path: str, data: Any, pretty: bool = False) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2 if pretty else None) + "\n", encoding="utf-8")


def is_unknown(value: Any) -> bool:
    return str(value or "").upper().startswith("UNKNOWN")


def raw_priority_usable(priority: Dict[str, Any]) -> bool:
    candidates: List[Dict[str, Any]] = []
    if isinstance(priority.get("primary_limiter"), dict):
        candidates.append(priority["primary_limiter"])
    if isinstance(priority.get("results"), list) and priority["results"]:
        first = priority["results"][0]
        if isinstance(first, dict) and isinstance(first.get("primary_limiter"), dict):
            candidates.append(first["primary_limiter"])
        for item in (first.get("skill_profiles") if isinstance(first, dict) else []) or []:
            if isinstance(item, dict):
                candidates.append(item)
    for item in candidates:
        skill_values = [item.get("skill"), item.get("skill_tag"), item.get("student_label"), item.get("rubric")]
        if any(is_unknown(v) for v in skill_values):
            continue
        if item.get("skill") or item.get("skill_tag") or item.get("capacity_domain"):
            return True
    return False


def criterion_for_family(family: str) -> str:
    fam = str(family or "")
    for prefix, criterion in CRITERION_BY_FAMILY_PREFIX.items():
        if fam.startswith(prefix):
            return criterion
    return "unknown"


def collect_errors(errormap: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = errormap.get("errors") if isinstance(errormap, dict) else []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def capacity_from_error(row: Dict[str, Any]) -> str:
    cap = str(row.get("capacity_domain") or "").strip()
    if cap:
        return cap
    criterion = str(row.get("criterion") or "").strip()
    fam = str(row.get("family") or "")
    if criterion == "grammar" or fam.startswith("G_"):
        return "sentence_control"
    if criterion == "lexical_resource" or fam.startswith("L_"):
        return "lexical_precision"
    if criterion == "academic_style" or fam.startswith("S_"):
        return "academic_style"
    if criterion == "argumentation" or fam.startswith("A_"):
        return "argument_development"
    if criterion == "cohesion_coherence" or fam.startswith("C_"):
        return "cohesion_control"
    return "task_response_control"


def severity_weight(row: Dict[str, Any]) -> float:
    return SEVERITY_WEIGHT.get(str(row.get("severity") or "").lower(), 1.0)


def build_focus_from_errormap(errormap: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = collect_errors(errormap)
    by_capacity: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("chargeable") is False:
            continue
        by_capacity[capacity_from_error(row)].append(row)

    focus: List[Dict[str, Any]] = []
    for cap, cap_rows in by_capacity.items():
        skill_id, label, default_criterion = CAPACITY_TO_SKILL.get(cap, (cap, cap.replace("_", " ").title(), "unknown"))
        fam_counts = Counter(str(r.get("family") or "UNKNOWN_FAMILY") for r in cap_rows)
        weighted_pressure = round(sum(severity_weight(r) for r in cap_rows), 3)
        evidence_samples = []
        for r in cap_rows[:5]:
            evidence_samples.append({
                "error_id": r.get("error_id"),
                "source_row_id": r.get("source_row_id"),
                "family": r.get("family"),
                "surface_quote": r.get("surface_quote"),
                "sentence_index": r.get("sentence_index"),
                "severity": r.get("severity"),
                "confidence": r.get("confidence"),
            })
        top_families = [fam for fam, _ in fam_counts.most_common(5)]
        criterion = default_criterion
        if top_families:
            criterion = criterion_for_family(top_families[0]) if criterion == "unknown" else criterion
        focus.append({
            "rank": 0,
            "capacity_domain": cap,
            "skill_tag": skill_id,
            "skill_id": skill_id,
            "student_label": label,
            "criterion": criterion,
            "evidence_count": len(cap_rows),
            "weighted_pressure": weighted_pressure,
            "priority_level": "very_high" if weighted_pressure >= 10 else "high" if weighted_pressure >= 6 else "medium" if weighted_pressure >= 3 else "monitor",
            "top_families": top_families,
            "family_counts": dict(fam_counts.most_common()),
            "recommended_service": SERVICE_BY_CAPACITY.get(cap, "writing_coach"),
            "recommended_difficulty": "controlled" if weighted_pressure >= 6 else "guided",
            "priority_reason": "Selected from chargeable ErrorMap capacity evidence because downstream routing requires a stable skill focus.",
            "evidence_samples": evidence_samples,
        })
    focus.sort(key=lambda x: (-float(x.get("weighted_pressure") or 0), -int(x.get("evidence_count") or 0), str(x.get("capacity_domain"))))
    for i, item in enumerate(focus, start=1):
        item["rank"] = i
    return focus


def score_context(score_contract: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(score_contract, dict):
        return {}
    return {
        "released_score": score_contract.get("released_score"),
        "score_status": score_contract.get("score_status"),
        "score_confidence": score_contract.get("score_confidence"),
        "progress_tracking_allowed": score_contract.get("progress_tracking_allowed"),
        "lie_update_allowed": score_contract.get("lie_update_allowed"),
    }


def build(priority: Dict[str, Any], errormap: Dict[str, Any], score_contract: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    focus = build_focus_from_errormap(errormap)
    primary = focus[0] if focus else None
    usable = raw_priority_usable(priority)
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "boundary": "Priority contract normalization only; no new scoring, detection, teaching, or exercise generation.",
        "source_priority_schema": priority.get("schema_version") if isinstance(priority, dict) else None,
        "source_priority_usable_without_repair": usable,
        "normalization_reason": "raw_priority_usable" if usable else "raw_priority_unknown_skill_or_missing_focus_repaired_from_errormap_capacity_evidence",
        "score_context": score_context(score_contract),
        "focus_areas": focus,
        "primary_focus": primary,
        "gold_learning_directive_seed": {
            "next_best_skill": primary.get("skill_tag") if primary else None,
            "next_best_capacity_domain": primary.get("capacity_domain") if primary else None,
            "recommended_service": primary.get("recommended_service") if primary else None,
            "priority_level": primary.get("priority_level") if primary else None,
        },
        "quality_flags": {
            "has_focus_areas": bool(focus),
            "unknown_skill_remaining": any(is_unknown(x.get("skill_tag")) or is_unknown(x.get("student_label")) for x in focus),
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Normalize Priority Engine output into directive-ready focus areas.")
    ap.add_argument("--priority", required=True)
    ap.add_argument("--errormap", required=True)
    ap.add_argument("--score-contract")
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)
    priority = read_json(args.priority)
    errormap = read_json(args.errormap)
    score_contract = read_json(args.score_contract) if args.score_contract else None
    out = build(priority, errormap, score_contract)
    write_json(args.output, out, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
