#!/usr/bin/env python3
"""
VA / ST.ELLA Writing Coach Alignment Guard v1.4.7
=================================================

Standalone targeted bridge.

Purpose:
- Prevent a silent mismatch between Gold Directive and Writing Coach output.
- Preserve the original Writing Coach decision, but add an explicit alignment
  record whenever the coach selects a skill different from the Directive's
  primary focus.
- Provide a stable effective_focus field for downstream UI/routing.

Boundary:
- This file does not choose a new writing-coach task.
- This file does not generate exercises or teaching content.
- This file does not score, detect, revise, or classify LRET units.
- It only audits and annotates service alignment.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

ENGINE_ID = "VA_STELLA_WRITING_COACH_ALIGNMENT_GUARD"
ENGINE_VERSION = "1.4.7-standalone-service-alignment-contract"

DOMAIN_TO_COACH_SKILL_HINTS = {
    "sentence_control": {"sentence", "grammar", "clause", "verb", "article", "punctuation", "sentence_control", "grammar_control", "sentence_construction"},
    "academic_style": {"register", "style", "formal", "academic", "academic_style", "register_control"},
    "lexical_precision": {"lexical", "vocabulary", "word", "precision", "collocation", "lexical_precision"},
    "argument_development": {"argument", "reason", "claim", "support", "example", "development", "arg_reason_generation"},
    "cohesion_control": {"cohesion", "coherence", "transition", "reference", "connector"},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, data: Any, pretty: bool = False) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2 if pretty else None)
        f.write("\n")


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def extract_directive_focus(directive: Dict[str, Any]) -> Dict[str, Any]:
    primary = directive.get("primary_focus") if isinstance(directive.get("primary_focus"), dict) else {}
    gld = directive.get("gold_learning_directive") if isinstance(directive.get("gold_learning_directive"), dict) else {}
    domain = primary.get("capacity_domain") or primary.get("skill_id") or primary.get("skill_tag") or gld.get("next_best_capacity_domain") or gld.get("next_best_skill")
    return {
        "capacity_domain": domain,
        "skill_id": primary.get("skill_id") or primary.get("skill_tag") or domain,
        "student_label": primary.get("student_label") or str(domain or "").replace("_", " ").title(),
        "recommended_service": primary.get("recommended_service") or gld.get("recommended_service"),
        "priority_level": primary.get("priority_level"),
        "evidence_count": primary.get("evidence_count"),
    }


def extract_coach_skill(coach: Dict[str, Any]) -> Dict[str, Any]:
    cd = coach.get("coach_decision") if isinstance(coach.get("coach_decision"), dict) else {}
    return {
        "selected_skill_id": cd.get("selected_skill_id"),
        "selected_skill_name": cd.get("selected_skill_name"),
        "selected_move_id": cd.get("selected_move_id"),
        "selected_move_name": cd.get("selected_move_name"),
    }


def is_aligned(directive_focus: Dict[str, Any], coach_skill: Dict[str, Any]) -> bool:
    d = norm(directive_focus.get("capacity_domain") or directive_focus.get("skill_id"))
    c_id = norm(coach_skill.get("selected_skill_id"))
    c_name = norm(coach_skill.get("selected_skill_name"))
    c_move = norm(coach_skill.get("selected_move_name"))
    if not d:
        return False
    if d and (d in c_id or d in c_name or d in c_move):
        return True
    hints = DOMAIN_TO_COACH_SKILL_HINTS.get(d, {d})
    coach_blob = " ".join([c_id, c_name, c_move]).replace("_", " ")
    return any(h.replace("_", " ") in coach_blob for h in hints)


def align(coach: Dict[str, Any], directive: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(coach)
    directive_focus = extract_directive_focus(directive)
    coach_skill = extract_coach_skill(out)
    aligned = is_aligned(directive_focus, coach_skill)
    status = "aligned" if aligned else "explained_override"

    alignment = {
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "status": status,
        "directive_primary_focus": directive_focus,
        "coach_selected_focus": coach_skill,
        "silent_mismatch_prevented": not aligned,
        "effective_focus_for_gold_routing": directive_focus,
        "coach_task_preserved": True,
        "policy": "Writing Coach output is preserved. If it differs from the Gold Directive, the mismatch is made explicit and downstream routing should treat the Directive primary focus as the Gold-level next focus unless the UI intentionally opens the coach task.",
        "override_reason": None if aligned else "Writing Coach selected a different microskill from its own dependency-aware planner. This may be useful as a subskill, but it is not the same as the Gold Directive primary focus.",
    }
    out["directive_alignment"] = alignment
    cd = out.get("coach_decision") if isinstance(out.get("coach_decision"), dict) else {}
    cd["directive_primary_focus"] = directive_focus
    cd["selected_skill_aligned_to_directive"] = aligned
    cd["directive_alignment_status"] = status
    if not aligned:
        teacher = str(cd.get("teacher_rationale") or "")
        note = f"Directive alignment note: Gold Directive primary focus is {directive_focus.get('capacity_domain')}; Writing Coach selected {coach_skill.get('selected_skill_id')}. Override is explicit, not silent."
        cd["teacher_rationale"] = (teacher + " " + note).strip()
    out["coach_decision"] = cd
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Writing Coach / Directive alignment guard v1.4.7.")
    ap.add_argument("--coach", required=True, help="Raw Writing Coach output JSON.")
    ap.add_argument("--directive", required=True, help="Gold Directive JSON.")
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)

    coach = read_json(args.coach)
    directive = read_json(args.directive)
    out = align(coach, directive)
    write_json(args.output, out, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
