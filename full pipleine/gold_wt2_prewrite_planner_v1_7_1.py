#!/usr/bin/env python3
"""Gold WT2 Pre-write Planner V1.7.1.

Learner-facing planning support before a student writes an IELTS Writing Task 2
essay. It gives structure choices, paragraph formula, word plan, and example
quality rules. It does not generate a model essay.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "GOLD_WT2_PREWRITE_PLANNER_V1_7_1"
ENGINE_ID = "VA_GOLD_WT2_PREWRITE_PLANNER"
ENGINE_VERSION = "1.7.1-prewrite-structure-example-guidance"
WT2_MIN_WORDS = 250
WT2_TARGET_RANGE = "260-290"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def write_json(p: str | Path, obj: Any, pretty: bool = False) -> None:
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2 if pretty else None)


def write_text(p: str | Path, text: str) -> None:
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(text, encoding="utf-8")


def task_family(task_type: str, prompt_text: str) -> str:
    t = f"{task_type} {prompt_text}".lower()
    if any(x in t for x in ["problem", "solution", "solutions", "solve"]):
        return "problem_solution"
    if any(x in t for x in ["cause", "effect", "causes", "effects"]):
        return "causes_effects"
    if any(x in t for x in ["advantage", "disadvantage", "outweigh", "benefit", "drawback"]):
        return "advantages_disadvantages"
    if any(x in t for x in ["agree", "disagree", "opinion", "to what extent"]):
        return "opinion"
    if any(x in t for x in ["discuss both", "both views"]):
        return "discussion"
    return "generic_wt2"


def structure_options(family: str) -> List[Dict[str, Any]]:
    if family == "advantages_disadvantages":
        return [
            {
                "name": "Balanced advantages/disadvantages structure",
                "best_for": "advantages/disadvantages or outweigh tasks",
                "paragraphs": [
                    "Introduction: paraphrase the task, state final position, preview main advantages and disadvantages.",
                    "Body 1: disadvantages only — topic sentence, drawback(s), explanation, specific example, link.",
                    "Body 2: advantages only — topic sentence, benefit(s), explanation, specific example, link to position.",
                    "Conclusion: summarize both sides and restate final position. No new idea.",
                ],
            }
        ]
    if family == "problem_solution":
        return [
            {
                "name": "Problems first, solutions second",
                "best_for": "tasks asking for problems and solutions in general",
                "paragraphs": [
                    "Introduction: paraphrase topic, say problems can be addressed, preview main problem and solution areas.",
                    "Body 1: problems only — explain one or two connected problems with a specific example.",
                    "Body 2: solutions only — explain matching solutions and how they solve the problems.",
                    "Conclusion: summarize the problem-solution logic. No new solution.",
                ],
            },
            {
                "name": "Problem-solution pairs",
                "best_for": "tasks with two clear problem areas",
                "paragraphs": [
                    "Introduction: paraphrase topic, preview two problem-solution pairs.",
                    "Body 1: problem 1 + solution 1 — explain both and connect them directly.",
                    "Body 2: problem 2 + solution 2 — explain both and connect them directly.",
                    "Conclusion: summarize both pairs. No new solution.",
                ],
            },
        ]
    if family == "causes_effects":
        return [
            {
                "name": "Causes then effects",
                "best_for": "cause/effect tasks",
                "paragraphs": [
                    "Introduction: paraphrase the issue and preview causes/effects.",
                    "Body 1: main causes — one or two causes with explanation.",
                    "Body 2: main effects — one or two effects with specific example or illustration.",
                    "Conclusion: summarize causes and effects. No new solution unless the task asks for one.",
                ],
            }
        ]
    if family == "discussion":
        return [
            {
                "name": "Both views + opinion",
                "best_for": "discuss both views and give your opinion",
                "paragraphs": [
                    "Introduction: paraphrase topic, mention both views, give your opinion.",
                    "Body 1: view A only — argument, explanation, example.",
                    "Body 2: view B + your opinion — argument, explanation, example, final preference.",
                    "Conclusion: summarize both views and repeat your opinion. No new argument.",
                ],
            }
        ]
    if family == "opinion":
        return [
            {
                "name": "Clear opinion structure",
                "best_for": "agree/disagree or opinion tasks",
                "paragraphs": [
                    "Introduction: paraphrase topic and state clear opinion.",
                    "Body 1: first reason for your opinion — explain and give example.",
                    "Body 2: second reason or concession + response — explain and give example.",
                    "Conclusion: summarize reasons and restate opinion. No new argument.",
                ],
            }
        ]
    return [
        {
            "name": "Universal IELTS Task 2 structure",
            "best_for": "general WT2 essays",
            "paragraphs": [
                "Introduction: task frame, clear answer/position, preview main ideas.",
                "Body 1: one main idea, explanation, specific example, link.",
                "Body 2: one main idea, explanation, specific example, link.",
                "Conclusion: summary + final answer. No new example or idea.",
            ],
        }
    ]


def paragraph_formula() -> Dict[str, Any]:
    return {
        "body_paragraph_formula": [
            "Topic sentence: say the paragraph's main idea.",
            "Argument/reason: explain why this idea matters.",
            "Development: add cause, effect, comparison, or consequence.",
            "Specific example: place/actor + action/situation + result.",
            "Link back: connect the example to the question or your position.",
        ],
        "example_quality": {
            "strong_example": "In a city school programme, retired engineers mentor students in robotics, which improves practical skills and keeps older adults socially involved.",
            "weak_example": "For example, my grandmother helps at home.",
            "why_strong": "It is specific, public, relevant, and shows a result.",
            "why_weak": "It is too private and does not clearly prove the IELTS argument.",
        },
    }


def build_plan(prompt_text: str, task_type: str) -> Dict[str, Any]:
    family = task_family(task_type, prompt_text)
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "task_type": task_type or "WT2",
        "task_family": family,
        "prompt_text": prompt_text,
        "student_view": {
            "headline": "Plan before you write.",
            "word_plan": {
                "minimum": WT2_MIN_WORDS,
                "recommended_range": WT2_TARGET_RANGE,
                "paragraph_word_targets": {
                    "introduction": "35-45 words",
                    "body_1": "80-100 words",
                    "body_2": "80-100 words",
                    "conclusion": "35-45 words",
                },
            },
            "structure_options": structure_options(family),
            "paragraph_formula": paragraph_formula(),
            "before_writing_checklist": [
                "I know the task type.",
                "I chose one essay structure and will keep paragraph roles consistent.",
                "Each body paragraph has one main idea.",
                "Each body paragraph has a specific example or realistic illustration.",
                "My conclusion will not introduce new information.",
                "My essay will be at least 250 words.",
            ],
        },
        "machine_payload": {"human_review_required": False, "model_essay_generated": False},
    }


def render_md(obj: Dict[str, Any]) -> str:
    sv = obj["student_view"]
    lines = ["# IELTS Task 2 Pre-writing Plan", "", sv["headline"], ""]
    wp = sv["word_plan"]
    lines += [f"Minimum: **{wp['minimum']} words**. Recommended: **{wp['recommended_range']} words**.", ""]
    lines.append("## Choose your essay structure")
    for opt in sv["structure_options"]:
        lines += ["", f"### {opt['name']}", f"Best for: {opt['best_for']}"]
        for p in opt["paragraphs"]:
            lines.append(f"- {p}")
    lines += ["", "## Body paragraph formula"]
    for step in sv["paragraph_formula"]["body_paragraph_formula"]:
        lines.append(f"- {step}")
    ex = sv["paragraph_formula"]["example_quality"]
    lines += ["", "## Example quality", f"Strong: {ex['strong_example']}", f"Weak: {ex['weak_example']}", f"Why: {ex['why_strong']}"]
    lines += ["", "## Before writing checklist"]
    for x in sv["before_writing_checklist"]:
        lines.append(f"- {x}")
    return "\n".join(lines).strip() + "\n"


def render_html(obj: Dict[str, Any]) -> str:
    def e(x: Any) -> str:
        return html.escape(str(x or ""))
    sv = obj["student_view"]
    options = []
    for opt in sv["structure_options"]:
        lis = "".join(f"<li>{e(p)}</li>" for p in opt["paragraphs"])
        options.append(f"<section class='card'><h3>{e(opt['name'])}</h3><p><b>Best for:</b> {e(opt['best_for'])}</p><ul>{lis}</ul></section>")
    formula = "".join(f"<li>{e(s)}</li>" for s in sv["paragraph_formula"]["body_paragraph_formula"])
    checklist = "".join(f"<li>{e(s)}</li>" for s in sv["before_writing_checklist"])
    ex = sv["paragraph_formula"]["example_quality"]
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>IELTS Pre-writing Plan</title>
<style>body{{font-family:Arial,sans-serif;background:#f7f7f7;margin:0;color:#222}}.header{{background:#202124;color:white;padding:16px 22px}}.wrap{{max-width:980px;margin:0 auto;padding:18px}}.card{{background:white;border:1px solid #ddd;border-radius:12px;margin:14px 0;padding:14px}}.good{{background:#eef8f0}}.bad{{background:#fdecea}}</style></head><body>
<div class='header'><h2>IELTS Task 2 Pre-writing Plan</h2></div><div class='wrap'>
<p>Minimum: <b>{e(sv['word_plan']['minimum'])} words</b>. Recommended: <b>{e(sv['word_plan']['recommended_range'])} words</b>.</p>
<h2>Choose your essay structure</h2>{''.join(options)}
<section class='card'><h2>Body paragraph formula</h2><ul>{formula}</ul></section>
<section class='card'><h2>Example quality</h2><p class='good'><b>Strong:</b> {e(ex['strong_example'])}</p><p class='bad'><b>Weak:</b> {e(ex['weak_example'])}</p><p>{e(ex['why_strong'])}</p></section>
<section class='card'><h2>Before writing checklist</h2><ul>{checklist}</ul></section>
</div></body></html>"""


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Generate IELTS WT2 pre-writing structure guide")
    ap.add_argument("--prompt-text", default="")
    ap.add_argument("--task-type", default="WT2")
    ap.add_argument("--output", required=True)
    ap.add_argument("--markdown")
    ap.add_argument("--html")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)
    obj = build_plan(args.prompt_text, args.task_type)
    write_json(args.output, obj, pretty=args.pretty)
    if args.markdown:
        write_text(args.markdown, render_md(obj))
    if args.html:
        write_text(args.html, render_html(obj))
    print(json.dumps({"status": "ok", "output": args.output, "schema_version": SCHEMA_VERSION}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
