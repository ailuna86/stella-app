#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VA / ST.ELLA Gold Revision Student Report Renderer V1.4.1
=====================================================

Converts the technical revision comparator output into an A2-B1 friendly
post-revision result. It avoids punitive/technical wording and preserves
technical metrics for internal payload only.

Input:
  --revision-output revision_output.json
  --workspace revision_workspace.json (optional but recommended)

Output:
  --output revision_student_report.json
  --markdown revision_student_report.md
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ENGINE_ID = "VA_GOLD_REVISION_STUDENT_REPORT_RENDERER"
ENGINE_VERSION = "1.4.1-a2b1-friendly-ai-availability-synced-v171"

VALID_MODEL_STATUSES = {
    "generated_with_llm_passed_structure_gate",
    "generated_with_repaired_llm_passed_structure_gate",
    "generated_with_schema_fallback_passed_structure_gate",
}
SCHEMA_VERSION = "GOLD_REVISION_STUDENT_REPORT_V1_4_1"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any, pretty: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2 if pretty else None)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def deep_get(obj: Any, path: Iterable[Any], default: Any = None) -> Any:
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


FRIENDLY_FAMILY_LABELS = {
    "ARTICLE_DETERMINER": "Articles",
    "CLAUSE_STRUCTURE": "Sentence structure",
    "CONSTRUCTION": "Sentence structure",
    "FRAGMENT": "Sentence structure",
    "RUN_ON": "Sentence structure",
    "COMPARATIVE_FORM": "Comparison forms",
    "COLLOCATION": "Natural phrases",
    "SPELLING": "Spelling",
    "SUBJECT_VERB_AGREEMENT": "Subject-verb agreement",
    "VERB_FORM": "Verb forms",
    "WORD_FORM": "Word forms",
}


def family_label(fam: str) -> str:
    fam = (fam or "").upper()
    return FRIENDLY_FAMILY_LABELS.get(fam, (fam or "problem").replace("_", " ").title())


def simple_family_advice(fam: str) -> str:
    fam = (fam or "").upper()
    if fam == "COMPARATIVE_FORM":
        return "Use one comparison pattern at a time. Check phrases with 'than' and -er words."
    if fam == "COLLOCATION":
        return "Keep the idea, but make the phrase simpler and more natural."
    if fam == "SPELLING":
        return "Read the revised essay once slowly and correct spelling before submitting."
    if fam == "SUBJECT_VERB_AGREEMENT":
        return "Check the subject and verb in each changed sentence."
    if fam == "ARTICLE_DETERMINER":
        return "Check a/an/the with nouns, especially plural nouns."
    if fam in {"CLAUSE_STRUCTURE", "CONSTRUCTION", "FRAGMENT", "RUN_ON"}:
        return "Rewrite the sentence as one short clear sentence: who does what?"
    if fam == "VERB_FORM":
        return "Check the verb form after words like 'to', 'has to', and modal verbs."
    if fam == "WORD_FORM":
        return "Check whether you need a noun, verb, adjective, or adverb."
    return "Fix this pattern in the highlighted sentence before making bigger changes."


def build_student_report(revision_output: Dict[str, Any], workspace: Optional[Dict[str, Any]] = None, ai_comparison: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    identity = revision_output.get("identity") or {}
    score = revision_output.get("score_movement") or {}
    scope = revision_output.get("revision_scope") or {}
    movement = revision_output.get("mistake_movement") or {}
    family_delta = revision_output.get("family_control_delta") or {}

    original_band = score.get("original_overall_band")
    revised_band = score.get("revised_overall_band")
    delta = score.get("overall_delta")
    revised_wc = deep_get(revision_output, ["revision_scope", "revised_word_count"], None)
    revised_sentences = deep_get(revision_output, ["revision_scope", "revised_sentence_count"], None)

    continuity_label = scope.get("continuity_label")
    comparison_reliability = scope.get("comparison_reliability")
    low_continuity = comparison_reliability == "score_reliable_repair_tracking_unreliable" or continuity_label in {"new_attempt_or_different_draft", "major_rewrite"}

    completion_message = "Your revised essay was complete enough to check."
    if revised_wc:
        completion_message = f"Your revised essay was complete enough to check: {revised_wc} words."

    continuity_message = None
    if low_continuity:
        continuity_message = "This looks like a very different draft. We can score it, but we cannot fairly judge whether you fixed all original highlighted parts."

    score_message = f"Your score stayed at Band {revised_band}." if delta == 0 else (
        f"Your score increased from Band {original_band} to Band {revised_band}." if isinstance(delta, (int, float)) and delta > 0 else
        f"Your score changed from Band {original_band} to Band {revised_band}."
    )

    improved = []
    still = []
    new = []
    for fam, data in family_delta.items():
        signal = data.get("learning_signal")
        if signal in {"resolved_family_control", "improved_control", "slightly_improved_control"}:
            label = family_label(fam)
            if fam.upper() == "COLLOCATION":
                msg = "Your natural phrasing improved."
            else:
                msg = f"You reduced problems with {label.lower()}."
            improved.append({"family": fam, "text": msg})
        elif signal in {"stable", "slightly_worsened_control", "worsened_control"} and (data.get("revised_count") or 0) > 0:
            label = family_label(fam)
            verb = "needs" if label in {"Sentence structure", "Spelling", "Subject-verb agreement"} else "need"
            still.append({"family": fam, "text": f"{label} still {verb} attention. {simple_family_advice(fam)}"})
        elif signal == "new_family_problem":
            label = family_label(fam).lower()
            if fam.upper() == "SUBJECT_VERB_AGREEMENT":
                msg = f"New subject-verb problems appeared. {simple_family_advice(fam)}"
            else:
                msg = f"New {label} problems appeared. {simple_family_advice(fam)}"
            new.append({"family": fam, "text": msg})

    # More direct movement counts.
    new_errors = int(movement.get("new_errors") or 0)
    retained = int(movement.get("retained_original_errors") or 0)
    resolved = int(movement.get("resolved_original_errors") or 0)

    what_improved = []
    if improved:
        what_improved.extend([i["text"] for i in improved[:3]])
    elif resolved:
        what_improved.append(f"You fixed {resolved} original problem(s).")
    else:
        what_improved.append("You kept the essay complete, but the main highlighted patterns still need more careful revision.")

    what_still_needs_work = []
    if retained:
        what_still_needs_work.append(f"{retained} original highlighted problem(s) are still present.")
    if still:
        what_still_needs_work.extend([i["text"] for i in still[:3]])
    if not what_still_needs_work:
        what_still_needs_work.append("Some changed sentences still need a final check for grammar and clarity.")

    new_problem_messages = []
    if new_errors:
        new_problem_messages.append(f"You introduced {new_errors} new problem(s) while revising. This is common when a student rewrites a lot, but next time revise fewer sentences more carefully.")
    if new:
        new_problem_messages.extend([i["text"] for i in new[:3]])
    if not new_problem_messages:
        new_problem_messages.append("No major new problem pattern is highlighted in the simple report.")

    # Select next small target.
    next_family = None
    if retained and still:
        next_family = still[0]["family"]
    elif new:
        next_family = new[0]["family"]
    elif family_delta:
        next_family = max(family_delta.items(), key=lambda kv: kv[1].get("revised_count") or 0)[0]
    next_target = {
        "title": family_label(next_family) if next_family else "Focused sentence repair",
        "student_instruction": simple_family_advice(next_family) if next_family else "Choose three yellow/red sentences and rewrite them as short clear sentences.",
        "success_condition": "In the next revision, this pattern should appear less often and no new errors should appear in the changed sentence.",
    }

    # V1.4.1: synchronize the student message with the actual AI comparison artifact.
    ai_status = (ai_comparison or {}).get("generation_status") if isinstance(ai_comparison, dict) else None
    ai_qa_status = deep_get(ai_comparison or {}, ["qa", "status"], None)
    ai_available = bool((ai_comparison or {}).get("model_available_to_student")) and ai_status in VALID_MODEL_STATUSES and ai_qa_status == "pass"
    ai_policy = {
        "model_comparison_available_now": bool(ai_available),
        "generation_status": ai_status or "not_generated_yet",
        "qa_status": ai_qa_status or "not_available",
        "student_generation_label": "quality-checked model rewrite" if ai_available else "not available",
        "default_display": "three_way_original_vs_student_revision_vs_ai_model",
        "model_source": "original_essay",
        "full_model_essay_default": False,
        "student_message": (
            "Now that you have submitted your own revision, you can compare the original paragraph, your revision, and an AI model rewrite of the original essay. Use it to study structure, examples, and useful wording; do not copy it."
            if ai_available else
            "The AI model comparison is not available for this run because the model answer did not pass the quality check. Your revision report is still available."
        ),
    }

    # Use workspace colors to add a personalized reminder if available.
    workspace_focus = None
    if workspace:
        counts = deep_get(workspace, ["source_summary", "displayed_sentence_status_counts"], {}) or {}
        workspace_focus = f"Original workspace showed {counts.get('red', 0)} red sentence(s) and {counts.get('yellow', 0)} yellow sentence(s)."

    report = {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "identity": identity,
        "student_view": {
            "headline": score_message,
            "completion_result": completion_message,
            "continuity_note": continuity_message,
            "workspace_focus_note": workspace_focus,
            "score_movement": {
                "original_overall_band": original_band,
                "revised_overall_band": revised_band,
                "overall_delta": delta,
                "student_message": score_message,
            },
            "what_improved": what_improved,
            "what_still_needs_work": what_still_needs_work,
            "new_problems": new_problem_messages,
            "next_small_revision_target": next_target,
            "ai_model_comparison": ai_policy,
        },
        "internal_payload": {
            "technical_revision_scope": scope,
            "technical_mistake_movement": movement,
            "technical_family_control_delta": family_delta,
            "comparison_reliability": comparison_reliability,
        },
        "tone_policy": {
            "a2_b1_default": True,
            "avoid_terms": ["weighted resolution rate", "revision competence weak", "unmatchable major rewrite"],
        },
    }
    return report


def render_markdown(report: Dict[str, Any]) -> str:
    sv = report.get("student_view") or {}
    lines = ["# Your Revision Result", ""]
    lines.append(f"**{sv.get('headline', '')}**")
    lines.append("")
    if sv.get("completion_result"):
        lines.append(f"✅ {sv.get('completion_result')}")
        lines.append("")
    if sv.get("continuity_note"):
        lines.append(f"⚠️ {sv.get('continuity_note')}")
        lines.append("")
    if sv.get("workspace_focus_note"):
        lines.append(sv.get("workspace_focus_note"))
        lines.append("")

    sm = sv.get("score_movement") or {}
    lines.append("## Score")
    lines.append(f"Original: **{sm.get('original_overall_band')}**")
    lines.append(f"Revised: **{sm.get('revised_overall_band')}**")
    lines.append(f"Change: **{sm.get('overall_delta')}**")
    lines.append("")

    def add_list(title: str, items: List[str]) -> None:
        lines.append(f"## {title}")
        for item in items or []:
            lines.append(f"- {item}")
        lines.append("")

    add_list("What improved", sv.get("what_improved") or [])
    add_list("What still needs work", sv.get("what_still_needs_work") or [])
    add_list("New problems", sv.get("new_problems") or [])

    nt = sv.get("next_small_revision_target") or {}
    lines.append("## Next small target")
    lines.append(f"**{nt.get('title')}**")
    lines.append("")
    lines.append(nt.get("student_instruction", ""))
    lines.append("")
    lines.append(f"Success check: {nt.get('success_condition', '')}")
    lines.append("")

    ai = sv.get("ai_model_comparison") or {}
    lines.append("## AI model comparison")
    lines.append(ai.get("student_message", ""))
    lines.append("")
    if ai.get("model_comparison_available_now"):
        lines.append("Default: compare original paragraph → your revision → AI model rewrite. The full model essay should stay optional/collapsed.")
    else:
        lines.append("Default: keep working from your revision report and workspace; do not show an invalid model answer.")
    return "\n".join(lines).strip() + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Render A2-B1 friendly Gold revision student report")
    parser.add_argument("--revision-output", required=True, help="Technical comparator revision_output.json")
    parser.add_argument("--workspace", help="Optional revision_workspace.json")
    parser.add_argument("--ai-comparison", help="Optional revision_ai_comparison_v1_7.json; used only to synchronize model-availability message")
    parser.add_argument("--output", required=True, help="Output student report JSON")
    parser.add_argument("--markdown", help="Optional student report Markdown")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    rev = read_json(Path(args.revision_output))
    ws = read_json(Path(args.workspace)) if args.workspace else None
    ai = read_json(Path(args.ai_comparison)) if args.ai_comparison and Path(args.ai_comparison).exists() else None
    report = build_student_report(rev, ws, ai)
    write_json(Path(args.output), report, pretty=args.pretty)
    print(f"[student-report] JSON written to {args.output}")
    if args.markdown:
        write_text(Path(args.markdown), render_markdown(report))
        print(f"[student-report] Markdown written to {args.markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
