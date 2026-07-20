#!/usr/bin/env python3
"""
Premium Feedback Engine v6c CLI — standalone compatibility implementation
========================================================================

Standalone CLI for generating a feedback artifact from directive + errormap +
score contract. It imports no previous versions.

Boundary:
- This is a targeted Feedback Engine CLI, not the Gold full pipeline.
- It does not score essays.
- It does not run Detector or ErrorMap.
- It does not generate LRET labels, Writing Coach missions, Practice sessions,
  or Essay Revision plans.
- It uses only upstream evidence already present in directive/errormap.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "FEEDBACK_ENGINE_V6C_CLI_STANDALONE"
ENGINE_ID = "VA_STELLA_FEEDBACK_ENGINE_V6C_CLI"
ENGINE_VERSION = "1.0.0-standalone-no-imports"

TITLE_BY_DOMAIN = {
    "sentence_control": "Sentence control",
    "lexical_precision": "Lexical precision",
    "academic_style": "Academic style",
    "argument_development": "Argument development",
    "cohesion_control": "Cohesion control",
    "task_response_control": "Task response control",
}

NEXT_STEP_BY_DOMAIN = {
    "sentence_control": "Practise accurate clauses before writing longer paragraphs.",
    "lexical_precision": "Repair unclear or imprecise word choices before upgrading style.",
    "academic_style": "Replace informal wording with precise academic expressions.",
    "argument_development": "Add a clear reason and one concrete explanation to each main claim.",
    "cohesion_control": "Use linking words only when the logical relationship is clear.",
    "task_response_control": "Make the position and answer to the question explicit.",
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


def get_score_summary(score_contract: Dict[str, Any]) -> Dict[str, Any]:
    released = score_contract.get("released_score") or {}
    if not released and isinstance(score_contract.get("final_score_profile"), dict):
        fp = score_contract["final_score_profile"]
        released = {
            "overall_band": fp.get("overall_band_estimate"),
            "criteria_bands": fp.get("official_criteria_bands") or fp.get("criteria_bands"),
        }
    return {
        "overall_band": released.get("overall_band"),
        "criteria_bands": released.get("criteria_bands") or {},
        "score_confidence": score_contract.get("score_confidence"),
        "adjudication_status": score_contract.get("adjudication_status"),
    }


def normalize_errors(errormap: Dict[str, Any]) -> List[Dict[str, Any]]:
    errors = errormap.get("errors") or []
    out = []
    for e in errors:
        if not isinstance(e, dict):
            continue
        out.append({
            "error_id": e.get("error_id"),
            "source_row_id": e.get("source_row_id"),
            "essay_id": e.get("essay_id"),
            "sentence_index": e.get("sentence_index"),
            "criterion": e.get("criterion"),
            "family": e.get("family"),
            "capacity_domain": e.get("capacity_domain"),
            "surface_quote": e.get("surface_quote"),
            "suggested_revision": e.get("suggested_revision"),
            "severity": e.get("severity"),
            "confidence": e.get("confidence"),
            "student_message": e.get("student_message"),
            "chargeable": e.get("chargeable", True),
        })
    return out


def focus_areas_from_directive(directive: Dict[str, Any]) -> List[Dict[str, Any]]:
    focus = directive.get("focus_areas") or []
    if not isinstance(focus, list):
        return []
    return [fa for fa in focus if isinstance(fa, dict)]


def build_bundles(directive: Dict[str, Any], errormap: Dict[str, Any]) -> List[Dict[str, Any]]:
    errors = [e for e in normalize_errors(errormap) if e.get("chargeable", True)]
    by_domain: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_family: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for e in errors:
        by_domain[str(e.get("capacity_domain") or "unknown")].append(e)
        by_family[str(e.get("family") or "unknown")].append(e)

    bundles: List[Dict[str, Any]] = []
    focus_areas = focus_areas_from_directive(directive)
    if not focus_areas:
        # fallback: highest evidence domains from errormap, still source-driven.
        for domain, group in sorted(by_domain.items(), key=lambda kv: len(kv[1]), reverse=True)[:5]:
            focus_areas.append({"capacity_domain": domain, "skill_tag": domain, "evidence_count": len(group), "top_families": []})

    for fa in focus_areas:
        domain = fa.get("capacity_domain") or fa.get("skill_tag") or "unknown"
        families = fa.get("top_families") or []
        selected = list(by_domain.get(domain, []))
        if families:
            fam_selected = []
            for fam in families:
                fam_selected.extend(by_family.get(str(fam), []))
            # include family-selected first, then same-domain remaining without duplicates.
            seen = set()
            merged = []
            for e in fam_selected + selected:
                key = e.get("error_id") or (e.get("family"), e.get("surface_quote"), e.get("sentence_index"))
                if key in seen:
                    continue
                seen.add(key)
                merged.append(e)
            selected = merged
        examples = selected[:5]
        bundles.append({
            "status": "ok" if selected else "no_direct_examples",
            "capacity_domain": domain,
            "skill_tag": fa.get("skill_tag") or domain,
            "criterion": fa.get("criterion"),
            "title": TITLE_BY_DOMAIN.get(domain, str(domain).replace("_", " ").title()),
            "priority_reason": fa.get("priority_reason") or "Selected by upstream priority evidence.",
            "summary": f"{len(selected)} chargeable signal(s) are linked to this area in the current ErrorMap.",
            "examples": examples,
            "next_step": NEXT_STEP_BY_DOMAIN.get(domain, "Practise this skill with a controlled task."),
        })
    return bundles


def build_report(feedback: Dict[str, Any]) -> Dict[str, Any]:
    bundles = feedback.get("bundles") or []
    priorities = []
    for b in bundles[:3]:
        priorities.append({
            "capacity_domain": b.get("capacity_domain"),
            "title": b.get("title"),
            "why_this_matters": b.get("priority_reason"),
            "next_step": b.get("next_step"),
            "example_count": len(b.get("examples") or []),
        })
    return {
        "schema_version": "FEEDBACK_REPORT_V6C_CLI_STANDALONE",
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "performance_summary": feedback.get("performance_summary"),
        "top_learning_priorities": priorities,
        "focus_area_feedback": bundles,
        "boundary": "Student-facing feedback is generated from upstream directive and errormap evidence only.",
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Standalone Feedback Engine v6c CLI compatibility implementation.")
    ap.add_argument("--directive", required=True)
    ap.add_argument("--errormap", required=True)
    ap.add_argument("--score-contract", required=True)
    ap.add_argument("--output", "-o", required=True, help="Feedback engine output JSON, e.g. 05_fe_output.json")
    ap.add_argument("--report-output", help="Optional report JSON, e.g. 06_feedback_report_v6c.json")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)

    directive = read_json(args.directive)
    errormap = read_json(args.errormap)
    score_contract = read_json(args.score_contract)
    feedback = {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "performance_summary": get_score_summary(score_contract),
        "bundles": build_bundles(directive, errormap),
        "boundary": "Feedback Engine output; no scoring, LRET classification, coaching, practice, or revision logic.",
    }
    write_json(args.output, feedback, pretty=args.pretty)
    if args.report_output:
        write_json(args.report_output, build_report(feedback), pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
