#!/usr/bin/env python3
"""Gold ER V1.7.1 Regression/Artifact Check.

This is a lightweight release-candidate checker for ER outputs. It does not
score essays and does not replace Detector/Scorer/Evaluator. It only verifies
that ER artifacts are internally consistent and student-safe enough for the
V1.7.1 frozen architecture.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

EXPECTED_AI_SCHEMA = "GOLD_REVISION_AI_COMPARISON_V1_7_1"
EXPECTED_REPORT_SCHEMA = "GOLD_REVISION_STUDENT_REPORT_V1_4_1"
EXPECTED_ACTIVE_VERSION = "v1_7_1"
VALID_MODEL_STATUSES = {
    "generated_with_llm_passed_structure_gate",
    "generated_with_repaired_llm_passed_structure_gate",
    "generated_with_schema_fallback_passed_structure_gate",
}

GENERIC_BAD_COMMENTS = {
    "introduction": ["develops one main idea", "uses a specific example", "uses a specific example and link"],
    "conclusion": ["develops one main idea", "uses a specific example", "uses a specific example and link"],
}


def read_json(path: Optional[Path]) -> Dict[str, Any]:
    if not path or not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def deep_get(obj: Any, path: List[Any], default: Any = None) -> Any:
    cur = obj
    for key in path:
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif isinstance(cur, list) and isinstance(key, int) and 0 <= key < len(cur):
            cur = cur[key]
        else:
            return default
        if cur is None:
            return default
    return cur


def words(text: str) -> int:
    import re
    return len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text or ""))


def check(manifest: Dict[str, Any], ai: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    flags: List[str] = []
    warnings: List[str] = []

    if manifest:
        if manifest.get("active_ai_comparison_version") != EXPECTED_ACTIVE_VERSION:
            flags.append("manifest_active_ai_comparison_version_not_v1_7_1")
        for key in ["ui_should_use_ai_comparison_json", "ui_should_use_ai_comparison_markdown", "ui_should_use_ai_comparison_html"]:
            val = str(manifest.get(key) or "")
            if val and "v1_7_1" not in val:
                flags.append(f"manifest_{key}_does_not_point_to_v1_7_1")
        integrity = manifest.get("ai_comparison_artifact_integrity") or {}
        if integrity and integrity.get("qa_status") != "pass":
            flags.append("manifest_ai_integrity_qa_not_pass")
    else:
        warnings.append("manifest_not_provided")

    if not ai:
        flags.append("ai_comparison_missing")
    else:
        if ai.get("schema_version") != EXPECTED_AI_SCHEMA:
            flags.append("ai_schema_not_v1_7_1")
        if deep_get(ai, ["model_source_policy", "source_for_model_generation"]) != "original_essay":
            flags.append("ai_model_source_not_original_essay")
        if ai.get("generation_status") not in VALID_MODEL_STATUSES:
            flags.append("ai_generation_status_not_valid_model")
        if ai.get("model_available_to_student") is not True:
            flags.append("ai_model_not_available_to_student")
        if deep_get(ai, ["qa", "status"]) != "pass":
            flags.append("ai_qa_not_pass")
        wc = int(ai.get("full_model_word_count") or words(ai.get("full_model_essay") or ""))
        if not (250 <= wc <= 320):
            flags.append(f"ai_model_word_count_outside_wt2_bounds:{wc}")
        if int(ai.get("generated_model_paragraph_count") or 0) != 4:
            flags.append("ai_model_paragraph_count_not_4")
        for item in ai.get("items") or []:
            role = str(item.get("role") or "").lower()
            comments = " ".join(str(x).lower() for x in (item.get("why_structure_is_better") or []))
            for bad in GENERIC_BAD_COMMENTS.get(role, []):
                if bad in comments:
                    flags.append(f"generic_comment_in_{role}_paragraph_{item.get('paragraph_number')}")
            if role == "body" and not item.get("specific_example_used"):
                flags.append(f"body_paragraph_{item.get('paragraph_number')}_missing_example_design_label")

    if report:
        if report.get("schema_version") != EXPECTED_REPORT_SCHEMA:
            flags.append("student_report_schema_not_v1_4_1")
        report_available = bool(deep_get(report, ["student_view", "ai_model_comparison", "model_comparison_available_now"], False))
        ai_available = bool(ai.get("model_available_to_student")) if ai else False
        if report_available != ai_available:
            flags.append("student_report_ai_availability_not_synced")
    else:
        warnings.append("student_report_not_provided")

    status = "pass" if not flags else "fail"
    return {
        "schema_version": "GOLD_ER_V1_7_1_REGRESSION_CHECK_V1",
        "status": status,
        "flags": flags,
        "warnings": warnings,
        "summary": {
            "ai_schema": ai.get("schema_version") if ai else None,
            "ai_generation_status": ai.get("generation_status") if ai else None,
            "ai_model_available": ai.get("model_available_to_student") if ai else None,
            "ai_word_count": ai.get("full_model_word_count") if ai else None,
            "student_report_schema": report.get("schema_version") if report else None,
            "manifest_active_version": manifest.get("active_ai_comparison_version") if manifest else None,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Check ER V1.7.1 output artifacts")
    ap.add_argument("--work-dir", help="Directory containing revision_run_manifest.json, revision_ai_comparison_v1_7_1.json, revision_student_report.json")
    ap.add_argument("--manifest")
    ap.add_argument("--ai-comparison")
    ap.add_argument("--student-report")
    ap.add_argument("--output", required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    work = Path(args.work_dir) if args.work_dir else None
    manifest_path = Path(args.manifest) if args.manifest else (work / "revision_run_manifest.json" if work else None)
    ai_path = Path(args.ai_comparison) if args.ai_comparison else (work / "revision_ai_comparison_v1_7_1.json" if work else None)
    report_path = Path(args.student_report) if args.student_report else (work / "revision_student_report.json" if work else None)

    result = check(read_json(manifest_path), read_json(ai_path), read_json(report_path))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None), encoding="utf-8")
    print(json.dumps({"status": result["status"], "flags": result["flags"], "output": str(out)}, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
