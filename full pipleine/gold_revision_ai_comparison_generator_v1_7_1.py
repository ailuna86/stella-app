#!/usr/bin/env python3
"""Gold Essay Revision AI Comparison Generator V1.7.1.

V1.7.1 keeps the V1.7 direction fix and adds production-focused stabilization:
- The AI model rewrite is generated from the ORIGINAL essay + task schema,
  not from the student's revised essay.
- The revised essay is used only for comparison after the learner has submitted
  their own revision.
- Student-facing comparison is three-way: Original -> Student revision -> AI model.
- Invalid model text is hard-gated and hidden from students.
- Student-facing structure comments are deterministic and role-specific.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = "GOLD_REVISION_AI_COMPARISON_V1_7_1"
ENGINE_ID = "VA_GOLD_REVISION_AI_COMPARISON_GENERATOR"
ENGINE_VERSION = "1.7.1-role-specific-comments+versioned-output-stabilization"

WT2_MIN_WORDS = 250
WT2_TARGET_MIN_WORDS = 250
WT2_TARGET_MAX_WORDS = 290
WT2_HARD_MAX_WORDS = 320

SPECIFIC_EXAMPLE_CUES = re.compile(r"\b(for example|for instance|in Japan|in Singapore|in Finland|in Estonia|in Uruguay|in Canada|in Australia|in the United Kingdom|in the UK|in the United States|in the US|a school in|a city|a local|a programme|a program|a centre|a center|a community centre|a public service|an NGO)\b", re.I)
EXAMPLE_CUES = re.compile(r"\b(for example|for instance|such as|in [A-Z][a-z]+|a school|a city|a local|a programme|a program|a centre|a center|a community|a public service|an NGO)\b", re.I)
PERSONAL_EXAMPLE_CUES = re.compile(r"\b(my grandmother|my grandfather|my mother|my father|my uncle|my aunt|my friend|my family|in my life|from my experience)\b", re.I)
SPECIFICITY_CUES = re.compile(r"\b(country|city|school|university|hospital|company|community|programme|program|centre|center|policy|pilot|trial|ministry|government|volunteer|students|retired workers|workers|families|parents|public service|NGO|initiative)\b", re.I)
CONCLUSION_CUES = re.compile(r"\b(in conclusion|to conclude|overall|therefore|in summary)\b", re.I)
POSITION_CUES = re.compile(r"\b(i believe|i argue|in my view|the advantages outweigh|the disadvantages outweigh|outweigh|more beneficial|more harmful|should|is better|is more important|therefore)\b", re.I)
ARGUMENT_PREVIEW_CUES = re.compile(r"\b(main|primary|first|second|benefit|drawback|advantage|disadvantage|problem|solution|reason|argument|because|due to|include|including|such as)\b", re.I)
ADVANTAGE_CUES = re.compile(r"\b(advantage|benefit|positive|contribute|valuable|support|strengthen|help society|help families|mentor|experience|knowledge|active|expertise|community involvement|family support|reduce isolation|socially active|valuable guidance|intergenerational)\b", re.I)
DISADVANTAGE_CUES = re.compile(r"\b(disadvantage|drawback|problem|negative|challenge|cost|burden|shortage|decline|strain|fewer workers|workforce shortage|economic stagnation|pressure|harmful|risk|healthcare|pension)\b", re.I)
SOLUTION_CUES = re.compile(r"\b(solution|solve|address|tackle|reduce|prevent|improve|provide|invest|train|control|regulate|policy|programme|program|government should|schools should|need to|must)\b", re.I)
PROBLEM_CUES = re.compile(r"\b(problem|issue|challenge|difficulty|risk|cause|effect|negative|cost|burden|pressure|harm|shortage|lack)\b", re.I)
NEW_RECOMMENDATION_CUES = re.compile(r"\b(governments? (can|could|should|must|need to)|schools? (can|could|should|must|need to)|implement policies|create policies|introduce policies|policy makers should|it is essential for governments|by recognizing|by harnessing)\b", re.I)
CONTRAST_SHIFT_CUES = re.compile(r"\b(however|on the other hand|conversely|nevertheless|despite this)\b", re.I)

VALID_MODEL_STATUSES = {
    "generated_with_llm_passed_structure_gate",
    "generated_with_schema_fallback_passed_structure_gate",
    "generated_with_repaired_llm_passed_structure_gate",
}



def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(p: str | Path) -> Dict[str, Any]:
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_json(p: str | Path, obj: Any, pretty: bool = False) -> None:
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2 if pretty else None)


def write_text(p: str | Path, text: str) -> None:
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(text, encoding="utf-8")


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


def clean(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def normalize_newlines(text: str) -> str:
    # Some pasted PowerShell/test inputs preserve literal \n. Convert them before paragraph splitting.
    return str(text or "").replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n")


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text or ""))


def split_paragraphs(text: str) -> List[str]:
    text = normalize_newlines(text)
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def get_original_text(req: Dict[str, Any]) -> str:
    candidates = [
        deep_get(req, ["original", "essay_text"]),
        req.get("original_essay_text"),
        deep_get(req, ["texts", "original_essay_text"]),
        deep_get(req, ["revision_workspace", "original", "essay_text"]),
    ]
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return normalize_newlines(c.strip())
    return ""


def get_revised_text(req: Dict[str, Any], out: Dict[str, Any]) -> str:
    candidates = [
        deep_get(req, ["revised", "essay_text"]),
        req.get("revised_essay_text"),
        deep_get(req, ["revision", "revised_essay_text"]),
        deep_get(out, ["revised", "essay_text"]),
        deep_get(out, ["texts", "revised_essay_text"]),
    ]
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return normalize_newlines(c.strip())
    return ""


def get_prompt_text(req: Dict[str, Any]) -> str:
    return clean(deep_get(req, ["prompt", "prompt_text"]) or req.get("prompt_text") or "")


def get_task_type(req: Dict[str, Any], workspace: Dict[str, Any]) -> str:
    return clean(deep_get(req, ["prompt", "task_type"]) or req.get("task_type") or deep_get(workspace, ["source_summary", "task_type"]) or "WT2")


def task_family(task_type: str, prompt_text: str, original_text: str = "") -> str:
    explicit = f"{task_type} {prompt_text}".lower()
    if any(x in explicit for x in ["advantage", "disadvantage", "outweigh", "benefit", "drawback"]):
        return "advantages_disadvantages"
    if any(x in explicit for x in ["problem", "solution", "solutions", "solve"]):
        return "problem_solution"
    if any(x in explicit for x in ["cause", "effect", "causes", "effects"]):
        return "causes_effects"
    if any(x in explicit for x in ["agree", "disagree", "opinion", "to what extent"]):
        return "opinion"
    if any(x in explicit for x in ["discuss both", "both views"]):
        return "discussion"
    fallback = f"{task_type} {prompt_text} {original_text}".lower()
    if any(x in fallback for x in ["advantage", "disadvantage", "outweigh", "benefit", "drawback"]):
        return "advantages_disadvantages"
    if any(x in fallback for x in ["problem", "solution", "solutions", "solve"]):
        return "problem_solution"
    if any(x in fallback for x in ["cause", "effect", "causes", "effects"]):
        return "causes_effects"
    return "generic_wt2"


def infer_role(i: int, total: int, workspace_para: Optional[Dict[str, Any]] = None) -> str:
    # Layout is authoritative for first/last paragraph in standard WT2.
    # This prevents a noisy upstream/workspace role label from turning the conclusion into a body paragraph.
    if i == 1:
        return "introduction"
    if i == total and total > 1:
        return "conclusion"
    if workspace_para:
        role = clean(workspace_para.get("paragraph_role") or workspace_para.get("role")).lower()
        if role in {"body"}:
            return role
    return "body"


def choose_problem_solution_plan(original_body_paras: List[str]) -> str:
    # Supports both valid IELTS PS layouts.
    if len(original_body_paras) >= 2:
        first = original_body_paras[0].lower()
        second = original_body_paras[1].lower()
        if PROBLEM_CUES.search(first) and not SOLUTION_CUES.search(first) and SOLUTION_CUES.search(second):
            return "problems_then_solutions"
    return "problem_solution_pairs"


def assign_role_subtypes(items: List[Dict[str, Any]], family: str) -> List[Dict[str, Any]]:
    body_indices = [i for i, it in enumerate(items) if it["role"] == "body"]
    body_originals = [items[i].get("original_paragraph", "") for i in body_indices]
    ps_plan = choose_problem_solution_plan(body_originals) if family == "problem_solution" else None
    body_pos = 0
    for it in items:
        role = it["role"]
        subtype = role
        allowed: List[str] = []
        forbidden: List[str] = []
        if role == "introduction":
            subtype = "introduction"
            allowed = ["task frame", "clear position", "preview of main arguments"]
            forbidden = ["example", "detailed evidence", "new programme/policy details"]
        elif role == "conclusion":
            subtype = "conclusion_summary_position_only"
            allowed = ["summary of body arguments", "final position"]
            forbidden = ["new example", "new argument", "new policy recommendation", "new solution"]
        elif family == "advantages_disadvantages":
            body_pos += 1
            subtype = "body_disadvantages_only" if body_pos == 1 else "body_advantages_only"
            if subtype == "body_disadvantages_only":
                allowed = ["disadvantages/drawbacks only", "specific example showing the disadvantage", "link to negative side"]
                forbidden = ["advantage claim", "benefit example", "however + positive turn inside same paragraph"]
            else:
                allowed = ["advantages/benefits only", "specific example showing the advantage", "link to final position"]
                forbidden = ["new disadvantage development", "problem-only paragraph"]
        elif family == "problem_solution":
            body_pos += 1
            if ps_plan == "problems_then_solutions":
                subtype = "body_problems_only" if body_pos == 1 else "body_solutions_only"
            else:
                subtype = f"body_problem_solution_pair_{body_pos}"
            if subtype == "body_problems_only":
                allowed = ["problems only", "specific example showing the problem"]
                forbidden = ["solution development"]
            elif subtype == "body_solutions_only":
                allowed = ["solutions only", "specific example showing how the solution works"]
                forbidden = ["new problem development"]
            else:
                allowed = ["one clear problem", "matching solution", "example showing the solution effect"]
                forbidden = ["unmatched problem", "solution unrelated to the problem"]
        elif family == "causes_effects":
            body_pos += 1
            subtype = "body_causes" if body_pos == 1 else "body_effects"
            allowed = ["one role only", "clear causal link", "specific illustration"]
            forbidden = ["solution recommendation unless task asks for it"]
        else:
            body_pos += 1
            subtype = f"body_main_argument_{body_pos}"
            allowed = ["one main idea", "argument", "explanation", "specific example", "link back"]
            forbidden = ["mixed unrelated ideas"]
        it["planned_role_subtype"] = subtype
        it["role_allowed_content"] = allowed
        it["role_forbidden_content"] = forbidden
    return items


def example_design_policy() -> Dict[str, Any]:
    return {
        "principle": "Teach example quality through universal rules; do not require a narrow fixed example bank.",
        "good_example_formula": "place/actor + action/programme/situation + result + link to argument",
        "specific_example_types": [
            "country/city policy or programme",
            "school/community/workplace programme",
            "public service or NGO initiative",
            "realistic local example if a named programme is not available",
        ],
        "bad_examples": [
            "private family anecdote used as the main evidence",
            "generic claim with no concrete situation",
            "example that supports the opposite paragraph role",
        ],
        "verification_note": "A named real programme should be used only when the model is reasonably confident; otherwise use a generic but specific local programme description.",
    }


def build_items(req: Dict[str, Any], out: Dict[str, Any], ws: Dict[str, Any], limit: int) -> Tuple[List[Dict[str, Any]], str, str, str, str, str]:
    original_text = get_original_text(req)
    revised_text = get_revised_text(req, out)
    prompt_text = get_prompt_text(req)
    task_type = get_task_type(req, ws)
    family = task_family(task_type, prompt_text, original_text)
    original_paras = split_paragraphs(original_text)
    revised_paras = split_paragraphs(revised_text)
    ws_paras = deep_get(ws, ["annotated_essay", "paragraphs"], []) or []
    total = len(original_paras)
    items: List[Dict[str, Any]] = []
    for i, original_para in enumerate(original_paras[: max(1, limit)], start=1):
        wp = ws_paras[i - 1] if i - 1 < len(ws_paras) and isinstance(ws_paras[i - 1], dict) else None
        revised_para = revised_paras[i - 1] if i - 1 < len(revised_paras) else ""
        items.append({
            "paragraph_number": i,
            "role": infer_role(i, total, wp),
            "original_paragraph": original_para,
            "student_revised_paragraph": revised_para,
            "workspace_hint": clean((wp or {}).get("paragraph_hint_public") or (wp or {}).get("paragraph_hint") or ""),
            "current_status": (wp or {}).get("paragraph_status") or "yellow",
        })
    return assign_role_subtypes(items, family), original_text, revised_text, task_type, family, prompt_text


def build_llm_prompt(items: List[Dict[str, Any]], original_text: str, revised_text: str, prompt_text: str, task_type: str, family: str, validation_feedback: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "role": "IELTS Writing Task 2 model rewrite generator for essay revision learning",
        "critical_direction_rule": "Generate the AI model from the ORIGINAL essay and task schema. Do not rewrite the student's revised essay.",
        "task_type": task_type,
        "task_family": family,
        "prompt_text": prompt_text,
        "original_essay_to_rewrite": original_text,
        "student_revised_essay_for_comparison_only": revised_text,
        "validation_feedback_from_previous_attempt": validation_feedback or [],
        "word_limit_policy": {
            "ielts_wt2_minimum_words": WT2_MIN_WORDS,
            "target_model_essay_words": f"{WT2_TARGET_MIN_WORDS}-{WT2_TARGET_MAX_WORDS}",
            "hard_max_words": WT2_HARD_MAX_WORDS,
            "requirement": "full_model_essay must be at least 250 words, preferably 250-290 words, and never above 320 words. Count words before returning. Do not return an essay below 250 words.",
            "paragraph_budget": {"introduction": "40-50 words", "body_1": "85-100 words", "body_2": "85-100 words", "conclusion": "35-45 words"},
        },
        "example_design_policy": example_design_policy(),
        "non_negotiable_requirements": {
            "source": "Use recoverable ideas from the original essay, but normalize them into a correct IELTS structure.",
            "not_a_polisher": "Do not merely correct grammar or polish the revised essay.",
            "role_purity": "Each AI paragraph must obey planned_role_subtype and avoid forbidden content.",
            "problem_solution_flexibility": "Problem-solution essays may use problems-then-solutions or problem-solution pairs, but each paragraph must stay role-consistent.",
            "conclusion": "Conclusion may summarize body arguments and restate final position only. No new recommendation, new solution, policy proposal, or example.",
            "comments": "Use short student-friendly comments, max 14 words each.",
            "body_examples": "Every body paragraph must include one specific example signal: place/actor + action/situation + result + link.",
            "final_position": "The conclusion must explicitly answer the task. For outweigh tasks, clearly state whether advantages outweigh disadvantages.",
        },
        "paragraph_plan_from_original_essay": items,
        "return_json_schema": {
            "model_essay_summary": {"final_position": "short final position", "main_arguments": ["argument 1", "argument 2"]},
            "items": [
                {
                    "paragraph_number": 1,
                    "role": "introduction|body|conclusion",
                    "planned_role_subtype": "copy from input",
                    "ai_model_paragraph": "model rewrite paragraph generated from original paragraph/essay plan",
                    "structure_checklist": {
                        "topic_or_frame": True,
                        "position": True,
                        "argument_preview": True,
                        "topic_sentence": False,
                        "argument": False,
                        "explanation": False,
                        "specific_example": False,
                        "link_back": False,
                        "summary": False,
                    },
                    "why_structure_is_better": ["short comment", "short comment"],
                    "specific_example_used": "example description or null",
                    "lexical_upgrades": [{"from": "weak phrase from original", "to": "better phrase", "why": "short reason"}],
                }
            ],
            "full_model_essay": "model paragraphs joined with blank lines; 250-290 words preferred",
        },
    }




def role_specific_structure_comments(item: Dict[str, Any]) -> List[str]:
    """Return deterministic student-facing comments tied to paragraph role.

    V1.7 generated generic comments such as "develops one main idea" even for
    introductions and conclusions. V1.7.1 makes these explanations role-specific
    and therefore safer for the learner UI.
    """
    role = str(item.get("role") or "").lower()
    subtype = str(item.get("planned_role_subtype") or role).lower()
    if role == "introduction":
        return [
            "It frames the task before giving details.",
            "It states a clear final position.",
            "It previews both sides without using an example.",
        ]
    if role == "conclusion":
        return [
            "It summarizes the main body arguments.",
            "It gives a clear final answer to the task.",
            "It does not add a new example or new policy idea.",
        ]
    if role == "body":
        if "disadvantage" in subtype or "problem" in subtype and "solution" not in subtype:
            return [
                "It keeps the paragraph focused on one negative side.",
                "It explains the problem before the example.",
                "It links the example back to the disadvantage.",
            ]
        if "advantage" in subtype:
            return [
                "It keeps the paragraph focused on one positive side.",
                "It explains the benefit before the example.",
                "It links the example back to the final position.",
            ]
        if "solution" in subtype:
            return [
                "It gives a clear solution, not just a general idea.",
                "It explains how the solution would work.",
                "It links the example back to the problem.",
            ]
        return [
            "It has one clear topic sentence.",
            "It explains the idea before the example.",
            "It links the example back to the task.",
        ]
    return ["It follows the planned paragraph role clearly."]


def role_specific_example_label(item: Dict[str, Any]) -> Optional[str]:
    role = str(item.get("role") or "").lower()
    subtype = str(item.get("planned_role_subtype") or role).lower()
    if role != "body":
        return None
    if "disadvantage" in subtype or ("problem" in subtype and "solution" not in subtype):
        return "specific public-service cost example"
    if "advantage" in subtype:
        return "specific community mentoring example"
    if "solution" in subtype:
        return "specific local support-programme example"
    return "specific local/community example"


def parse_json_text(text: str) -> Any:
    text = (text or "").strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def call_openai_json(prompt: Dict[str, Any], model: str, temperature: float = 0.1) -> Dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY missing")
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": "Return strict JSON only. Do not include markdown fences."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
    )
    return parse_json_text(resp.choices[0].message.content or "")


def normalize_llm_output(data: Dict[str, Any], items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
    raw_items = data.get("items") if isinstance(data, dict) else []
    by_num: Dict[int, Dict[str, Any]] = {}
    if isinstance(raw_items, list):
        for x in raw_items:
            if isinstance(x, dict):
                try:
                    by_num[int(x.get("paragraph_number"))] = x
                except Exception:
                    pass
    merged: List[Dict[str, Any]] = []
    paras: List[str] = []
    for it in items:
        gen = by_num.get(int(it["paragraph_number"]), {})
        ai_para = clean(gen.get("ai_model_paragraph"))
        if ai_para:
            paras.append(ai_para)
        merged.append({
            **it,
            "ai_model_paragraph": ai_para or None,
            "structure_checklist": gen.get("structure_checklist") if isinstance(gen.get("structure_checklist"), dict) else {},
            # Deterministic role-specific comments prevent generic or misleading learner feedback.
            "why_structure_is_better": role_specific_structure_comments(it),
            "specific_example_used": clean(gen.get("specific_example_used")) or role_specific_example_label(it),
            "lexical_upgrades": gen.get("lexical_upgrades") if isinstance(gen.get("lexical_upgrades"), list) else [],
        })
    full = normalize_newlines(clean(data.get("full_model_essay"))) if isinstance(data, dict) else ""
    if not full and paras:
        full = "\n\n".join(paras)
    summary = data.get("model_essay_summary") if isinstance(data.get("model_essay_summary"), dict) else {}
    return merged, full, summary


def validate_role_item(it: Dict[str, Any]) -> List[str]:
    flags: List[str] = []
    role = (it.get("role") or "").lower()
    subtype = (it.get("planned_role_subtype") or role).lower()
    text = clean(it.get("ai_model_paragraph"))
    if not text:
        return ["missing_model_paragraph"]
    if role == "introduction":
        if SPECIFIC_EXAMPLE_CUES.search(text):
            flags.append("introduction_contains_example")
        if not POSITION_CUES.search(text):
            flags.append("introduction_position_unclear")
        if not ARGUMENT_PREVIEW_CUES.search(text):
            flags.append("introduction_argument_preview_weak")
    elif role == "conclusion":
        if SPECIFIC_EXAMPLE_CUES.search(text):
            flags.append("conclusion_contains_example")
        if not CONCLUSION_CUES.search(text):
            flags.append("conclusion_marker_missing")
        if not POSITION_CUES.search(text):
            flags.append("conclusion_final_position_weak")
        if NEW_RECOMMENDATION_CUES.search(text):
            flags.append("conclusion_adds_new_recommendation_or_solution")
    elif role == "body":
        if len(text.split()) < 45:
            flags.append("body_too_short_for_argument_example")
        if not EXAMPLE_CUES.search(text):
            flags.append("body_lacks_example_signal")
        if not (SPECIFICITY_CUES.search(text) or SPECIFICITY_CUES.search(clean(it.get("specific_example_used")))):
            flags.append("body_example_not_specific_enough")
        if PERSONAL_EXAMPLE_CUES.search(text):
            flags.append("body_uses_private_family_example")
        if subtype == "body_disadvantages_only":
            if CONTRAST_SHIFT_CUES.search(text) and ADVANTAGE_CUES.search(text):
                flags.append("disadvantage_paragraph_switches_to_advantage")
            elif ADVANTAGE_CUES.search(text) and not DISADVANTAGE_CUES.search(text):
                flags.append("disadvantage_paragraph_contains_advantage_content")
        elif subtype == "body_advantages_only":
            if CONTRAST_SHIFT_CUES.search(text) and DISADVANTAGE_CUES.search(text):
                flags.append("advantage_paragraph_switches_to_disadvantage")
        elif subtype == "body_problems_only":
            if SOLUTION_CUES.search(text):
                flags.append("problem_only_paragraph_contains_solution")
        elif subtype == "body_solutions_only":
            if not SOLUTION_CUES.search(text):
                flags.append("solution_paragraph_lacks_solution")
        elif subtype.startswith("body_problem_solution_pair"):
            if not PROBLEM_CUES.search(text):
                flags.append("problem_solution_pair_lacks_problem")
            if not SOLUTION_CUES.search(text):
                flags.append("problem_solution_pair_lacks_solution")
    return flags


def validate_items(items: List[Dict[str, Any]], full_model_essay: str) -> Dict[str, Any]:
    flags: List[str] = []
    per_item: List[Dict[str, Any]] = []
    generated_count = 0
    wc = word_count(full_model_essay)
    if full_model_essay:
        if wc < WT2_MIN_WORDS:
            flags.append(f"full_model_essay_below_wt2_minimum:{wc}")
        if wc > WT2_HARD_MAX_WORDS:
            flags.append(f"full_model_essay_above_hard_max:{wc}")
    for it in items:
        if clean(it.get("ai_model_paragraph")):
            generated_count += 1
        item_flags = validate_role_item(it)
        if item_flags:
            flags.extend([f"P{it.get('paragraph_number')}:{f}" for f in item_flags])
        per_item.append({
            "paragraph_number": it.get("paragraph_number"),
            "role": it.get("role"),
            "planned_role_subtype": it.get("planned_role_subtype"),
            "flags": item_flags,
            "valid": not item_flags,
        })
    if not full_model_essay and generated_count:
        flags.append("full_model_essay_missing_but_paragraphs_generated")
    status = "pass" if generated_count and not flags else ("fail" if not generated_count else "fail_structure_gate")
    return {
        "status": status,
        "generated_model_paragraph_count": generated_count,
        "full_model_word_count": wc,
        "word_limit_policy": {"minimum": WT2_MIN_WORDS, "target_min": WT2_TARGET_MIN_WORDS, "target_max": WT2_TARGET_MAX_WORDS, "hard_max": WT2_HARD_MAX_WORDS},
        "flags": flags,
        "per_item": per_item,
    }


def clear_model_fields(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleared: List[Dict[str, Any]] = []
    for it in items:
        x = dict(it)
        x.update({
            "ai_model_paragraph": None,
            "structure_checklist": {},
            "why_structure_is_better": [],
            "specific_example_used": None,
            "lexical_upgrades": [],
        })
        cleared.append(x)
    return cleared


def _topic_label(prompt_text: str, original_text: str, family: str) -> str:
    raw = clean(prompt_text) or clean(original_text.split(".")[0] if original_text else "")
    low = raw.lower()
    if "older people" in low or "ageing population" in low or "aging population" in low:
        return "an ageing population"
    if "technology" in low and "school" in low:
        return "technology in education"
    # remove common IELTS question endings while keeping a reusable topic phrase
    raw = re.sub(r"\b(do the advantages.*|what are the.*|to what extent.*|discuss both.*|give reasons.*)$", "", raw, flags=re.I).strip(" .?")
    if 3 <= word_count(raw) <= 16:
        return raw[0].lower() + raw[1:]
    return "this issue"


def _schema_model_paragraph(it: Dict[str, Any], family: str, topic: str) -> str:
    role = (it.get("role") or "").lower()
    subtype = (it.get("planned_role_subtype") or role).lower()
    if role == "introduction":
        if family == "advantages_disadvantages":
            return (
                f"In many countries, {topic} is becoming an important social issue. "
                "Although this trend can increase pressure on public services and the labour market, "
                "I believe its benefits are greater because older people can support families, communities, and younger generations with their experience."
            )
        if family == "problem_solution":
            return (
                f"In many societies, {topic} has become a serious concern. "
                "This essay will explain two main problems and suggest practical solutions that can reduce their impact."
            )
        return (
            f"In recent years, {topic} has become an important public debate. "
            "This essay will present a clear position and develop two main reasons with examples."
        )
    if role == "conclusion":
        if family == "advantages_disadvantages":
            return (
                "In conclusion, an ageing population may create higher healthcare costs and a smaller workforce. "
                "However, older citizens can also provide valuable experience, family support, and community service. "
                "For these reasons, I believe the advantages outweigh the disadvantages."
            )
        if family == "problem_solution":
            return (
                "In conclusion, the problem is serious because it affects both individuals and public services. "
                "However, targeted support and practical local programmes can reduce the pressure. "
                "Therefore, the issue can be managed if solutions are planned carefully."
            )
        return (
            "In conclusion, both the causes and effects of this issue are significant. "
            "However, the strongest argument is that practical action and clear priorities can reduce the negative impact."
        )
    # Body paragraphs
    if subtype == "body_disadvantages_only":
        return (
            "One clear disadvantage is the financial and economic pressure created when the number of older people rises. "
            "Governments may need to spend more on hospitals, pensions, and care homes, while employers may struggle to replace experienced workers who retire. "
            "For example, a city council with many elderly residents may have to hire more home-care nurses and subsidise transport to hospitals, leaving less money for schools or housing. "
            "This shows how an ageing population can place a heavy burden on public budgets and the labour market."
        )
    if subtype == "body_advantages_only":
        return (
            "Despite these difficulties, older people can make a valuable contribution to society. "
            "Many retired citizens have professional knowledge, patience, and life experience that younger people can learn from. "
            "For instance, a community centre can organise retired teachers and engineers to mentor school students, run reading clubs, or advise small local projects. "
            "Such programmes help younger people develop useful skills while allowing older citizens to remain active and respected."
        )
    if subtype == "body_problems_only":
        return (
            "The first major problem is that this issue can create pressure on everyday life and public services. "
            "When the problem is not managed, families, schools, workplaces, or local authorities may have to spend extra time and money dealing with its effects. "
            "For example, a local council may need to add extra support staff or emergency services when demand suddenly increases. "
            "This makes the problem more serious because it affects both individuals and the wider community."
        )
    if subtype == "body_solutions_only":
        return (
            "The most effective solution is to create practical support systems that address the problem early. "
            "Governments, schools, or community organisations can provide training, clear rules, and targeted services instead of waiting until the issue becomes worse. "
            "For instance, a city can run a local support programme that connects trained volunteers with families who need advice or practical help. "
            "This kind of organised response can reduce pressure and make the solution easier to maintain."
        )
    if subtype.startswith("body_problem_solution_pair"):
        return (
            "One important problem is that this issue can put pressure on people who do not have enough support. "
            "A practical solution is to create a local programme that gives early help before the situation becomes more serious. "
            "For example, a school, workplace, or community centre could identify people at risk and connect them with trained advisers or volunteers. "
            "This would reduce the problem because support would arrive before small difficulties turn into larger ones."
        )
    return (
        "One important reason is that this issue has a direct effect on ordinary people and public services. "
        "It can influence how families make decisions, how communities use resources, and how governments plan for the future. "
        "For example, a local community programme can show the effect clearly by helping one group while also reducing pressure on another. "
        "This makes the argument stronger because it connects the idea to a specific real-life situation."
    )


def make_schema_fallback(items: List[Dict[str, Any]], family: str, prompt_text: str, original_text: str) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any], Dict[str, Any]]:
    topic = _topic_label(prompt_text, original_text, family)
    out_items: List[Dict[str, Any]] = []
    paras: List[str] = []
    for it in items:
        para = _schema_model_paragraph(it, family, topic)
        paras.append(para)
        x = dict(it)
        x.update({
            "ai_model_paragraph": para,
            "structure_checklist": {
                "topic_or_frame": (it.get("role") == "introduction"),
                "position": (it.get("role") in {"introduction", "conclusion"}),
                "argument_preview": (it.get("role") == "introduction"),
                "topic_sentence": (it.get("role") == "body"),
                "argument": (it.get("role") == "body"),
                "explanation": (it.get("role") == "body"),
                "specific_example": (it.get("role") == "body"),
                "link_back": (it.get("role") == "body"),
                "summary": (it.get("role") == "conclusion"),
            },
            "why_structure_is_better": role_specific_structure_comments(it),
            "specific_example_used": role_specific_example_label(it),
            "lexical_upgrades": [],
        })
        out_items.append(x)
    full = "\n\n".join(paras)
    # If a non-standard number of paragraphs made the essay too short, add safe development to body paragraphs.
    if word_count(full) < WT2_MIN_WORDS:
        for x in out_items:
            if x.get("role") == "body" and word_count(full) < WT2_MIN_WORDS:
                subtype = str(x.get("planned_role_subtype") or "").lower()
                if "disadvantage" in subtype or "problem" in subtype:
                    x["ai_model_paragraph"] += " In the long term, this can force taxpayers, younger workers, or local services to carry a larger share of the cost."
                elif "advantage" in subtype or "solution" in subtype:
                    x["ai_model_paragraph"] += " As a result, society gains practical support that would otherwise require paid staff or formal public services."
                else:
                    x["ai_model_paragraph"] += " This concrete situation makes the point easier to understand and connects the argument to real social effects."
        full = "\n\n".join(clean(x.get("ai_model_paragraph")) for x in out_items)
    summary = {
        "final_position": "The model gives a clear final answer to the task.",
        "main_arguments": ["one main disadvantage/problem", "one main advantage/solution or second argument"],
        "generation_mode": "schema_fallback_after_llm_failure",
    }
    qa = validate_items(out_items, full)
    return out_items, full, summary, qa


def generate_with_validation(items: List[Dict[str, Any]], original_text: str, revised_text: str, prompt_text: str, task_type: str, family: str, model: str, max_attempts: int = 4, allow_schema_fallback: bool = True) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any], Optional[str], Dict[str, Any], Optional[Dict[str, Any]], str, List[Dict[str, Any]]]:
    last_error: Optional[str] = None
    validation_feedback: Optional[List[str]] = None
    last_failed_qa: Optional[Dict[str, Any]] = None
    attempt_log: List[Dict[str, Any]] = []
    for attempt in range(max(1, max_attempts)):
        try:
            prompt = build_llm_prompt(items, original_text, revised_text, prompt_text, task_type, family, validation_feedback)
            data = call_openai_json(prompt, model=model, temperature=0.08 if attempt == 0 else 0.02)
            merged, full_model, summary = normalize_llm_output(data, items)
            qa = validate_items(merged, full_model)
            attempt_log.append({"attempt": attempt + 1, "mode": "llm", "qa_status": qa.get("status"), "word_count": qa.get("full_model_word_count"), "flags": qa.get("flags", [])[:12]})
            if qa["status"] == "pass":
                return merged, full_model, summary, None, qa, None, "llm_validated", attempt_log
            last_failed_qa = qa
            validation_feedback = [
                "Previous attempt failed validation. You must repair all flags and return a complete 250-290 word essay.",
                *[str(f) for f in (qa.get("flags") or [])[:16]],
            ]
        except Exception as exc:
            last_error = str(exc)
            attempt_log.append({"attempt": attempt + 1, "mode": "llm", "error": last_error})
            validation_feedback = [last_error, "Return strict JSON and a complete valid model essay."]

    if allow_schema_fallback:
        fallback_items, fallback_full, fallback_summary, fallback_qa = make_schema_fallback(items, family, prompt_text, original_text)
        attempt_log.append({"attempt": "fallback", "mode": "schema_fallback", "qa_status": fallback_qa.get("status"), "word_count": fallback_qa.get("full_model_word_count"), "flags": fallback_qa.get("flags", [])[:12]})
        if fallback_qa.get("status") == "pass":
            return fallback_items, fallback_full, fallback_summary, None, fallback_qa, last_failed_qa, "schema_fallback_validated", attempt_log

    qa = last_failed_qa or validate_items(items, "")
    return clear_model_fields(items), "", {}, last_error or "LLM output failed IELTS structure/word-count gate", qa, last_failed_qa, "no_valid_model", attempt_log


def build_comparison(req: Dict[str, Any], out: Dict[str, Any], ws: Dict[str, Any], use_llm: bool, model: str, limit: int, max_attempts: int = 4, allow_schema_fallback: bool = True) -> Dict[str, Any]:
    items, original_text, revised_text, task_type, family, prompt_text = build_items(req, out, ws, limit)
    generation_error: Optional[str] = None
    full_model_essay = ""
    model_summary: Dict[str, Any] = {}
    rejected_qa: Optional[Dict[str, Any]] = None
    if use_llm:
        items, full_model_essay, model_summary, generation_error, qa, rejected_qa, generation_strategy, attempt_log = generate_with_validation(items, original_text, revised_text, prompt_text, task_type, family, model, max_attempts=max_attempts, allow_schema_fallback=allow_schema_fallback)
    else:
        # Standalone deterministic mode: still generate a complete schema-based model.
        # This is used for tests, demos, and environments without API access.
        items, full_model_essay, model_summary, qa = make_schema_fallback(items, family, prompt_text, original_text)
        generation_error = None if qa.get("status") == "pass" else "Schema fallback failed IELTS structure/word-count gate"
        generation_strategy = "schema_fallback_validated_without_llm" if qa.get("status") == "pass" else "no_valid_model"
        attempt_log = [{"attempt": "fallback", "mode": "schema_fallback_no_llm", "qa_status": qa.get("status"), "word_count": qa.get("full_model_word_count"), "flags": qa.get("flags", [])[:12]}]
    generated_count = qa.get("generated_model_paragraph_count", 0) if full_model_essay else 0
    if full_model_essay and qa.get("status") == "pass" and generation_strategy == "llm_validated":
        status = "generated_with_llm_passed_structure_gate"
    elif full_model_essay and qa.get("status") == "pass" and generation_strategy in {"schema_fallback_validated", "schema_fallback_validated_without_llm"}:
        status = "generated_with_schema_fallback_passed_structure_gate"
    elif use_llm:
        status = "llm_requested_but_no_valid_model_text"
    else:
        status = "schema_fallback_failed_no_valid_model"
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "release_gate": {
            "requires_self_revision_first": True,
            "revision_output_present": bool(out),
            "pre_revision_use_allowed": False,
            "status": "allowed_after_self_revision",
        },
        "model_source_policy": {
            "source_for_model_generation": "original_essay",
            "student_revision_use": "comparison_only",
            "rationale": "The model should show a strong IELTS rewrite of the learner's original attempt, not polish the learner's revised draft.",
        },
        "task_type": task_type,
        "task_family": family,
        "generation_status": status,
        "model_available_to_student": status in VALID_MODEL_STATUSES,
        "generation_error": generation_error,
        "generation_strategy": generation_strategy,
        "generation_attempt_log": attempt_log,
        "max_generation_attempts": max_attempts,
        "schema_fallback_enabled": allow_schema_fallback,
        "model": model if use_llm else None,
        "model_essay_summary": model_summary,
        "original_word_count": word_count(original_text),
        "student_revised_word_count": word_count(revised_text),
        "full_model_essay": full_model_essay or None,
        "full_model_word_count": word_count(full_model_essay),
        "generated_model_paragraph_count": generated_count,
        "display_policy": {
            "default_view": "three_way_original_vs_student_revision_vs_ai_model",
            "sentence_level_details_default": "collapsed_or_not_generated",
            "full_model_essay_default": False,
            "markdown_full_model_essay_default": False,
            "anti_copying_note": "Use this to study structure and examples. Do not copy it as your essay.",
            "student_comment_policy": "role_specific_comments_v1_7_1",
        },
        "example_policy": example_design_policy(),
        "items": items,
        "qa": qa,
        "rejected_model_qa_debug": rejected_qa,
    }


def render_md(obj: Dict[str, Any], include_full_model: bool = False) -> str:
    lines = [
        "# AI IELTS Structure Comparison",
        "",
        "Available only after your own revision. Use it to compare your revision with a model rewrite of your original essay.",
        "",
        "The AI model is generated from the **original essay**, not from your revised draft.",
        "",
    ]
    status = obj.get("generation_status")
    valid = status in VALID_MODEL_STATUSES
    if not valid:
        lines += [
            "The AI model version is not available for this run because it did not pass the structure check.",
            "Your own revision report is still available.",
            "",
        ]
    else:
        wc = obj.get("full_model_word_count")
        if wc:
            lines.append(f"Model essay length: **{wc} words**.")
            lines.append("")
    for it in obj.get("items", []):
        lines.append(f"## Paragraph {it.get('paragraph_number')} — {str(it.get('role') or '').title()}")
        lines.append("")
        lines.append("**Original paragraph**")
        lines.append("")
        lines.append(it.get("original_paragraph") or "_No original paragraph found._")
        lines.append("")
        lines.append("**Your revised paragraph**")
        lines.append("")
        lines.append(it.get("student_revised_paragraph") or "_No revised paragraph found._")
        lines.append("")
        lines.append("**AI model rewrite of original paragraph**")
        lines.append("")
        lines.append(it.get("ai_model_paragraph") or "_Model paragraph is not available for this run._")
        comments = it.get("why_structure_is_better") or []
        if comments:
            lines.append("")
            lines.append("**Why this is stronger**")
            for c in comments[:3]:
                lines.append(f"- {clean(c)}")
        if it.get("specific_example_used"):
            lines.append("")
            lines.append(f"**Example design:** {it.get('specific_example_used')}")
        upgrades = it.get("lexical_upgrades") or []
        if upgrades:
            lines.append("")
            lines.append("**Useful lexical upgrades from the original essay**")
            for r in upgrades[:4]:
                if isinstance(r, dict):
                    lines.append(f"- `{clean(r.get('from'))}` → `{clean(r.get('to'))}`: {clean(r.get('why'))}")
        lines.append("")
    if include_full_model and obj.get("full_model_essay"):
        lines.append("## Optional full AI model essay")
        lines.append("")
        lines.append(obj["full_model_essay"])
        lines.append("")
    elif obj.get("full_model_essay"):
        lines.append("_Full model essay is available in the HTML view, collapsed by default._")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_html(obj: Dict[str, Any]) -> str:
    def e(x: Any) -> str:
        return html.escape(str(x or ""))
    status = obj.get("generation_status")
    valid = status in VALID_MODEL_STATUSES
    if not valid:
        status_note = "<p class='warn'>The AI model version is not available for this run because it did not pass the structure check.</p>"
    else:
        status_note = f"<p class='small'>Model essay length: {e(obj.get('full_model_word_count'))} words.</p>" if obj.get("full_model_word_count") else ""
    cards = []
    for it in obj.get("items", []):
        comments = "".join(f"<li>{e(clean(c))}</li>" for c in (it.get("why_structure_is_better") or [])[:3])
        upgrades = "".join(
            f"<li><b>{e(clean(r.get('from')))}</b> → <b>{e(clean(r.get('to')))}</b>: {e(clean(r.get('why')))}</li>"
            for r in (it.get("lexical_upgrades") or [])[:4]
            if isinstance(r, dict)
        )
        example = f"<p><b>Example design:</b> {e(it.get('specific_example_used'))}</p>" if it.get("specific_example_used") else ""
        cards.append(f"""
        <section class='card'>
          <h3>Paragraph {e(it.get('paragraph_number'))} — {e(str(it.get('role') or '').title())}</h3>
          <div class='grid three'>
            <div class='box'><h4>Original paragraph</h4><p>{e(it.get('original_paragraph'))}</p></div>
            <div class='box'><h4>Your revised paragraph</h4><p>{e(it.get('student_revised_paragraph'))}</p></div>
            <div class='box model'><h4>AI model rewrite</h4><p>{e(it.get('ai_model_paragraph') or 'Model paragraph is not available for this run.')}</p></div>
          </div>
          {(f"<div class='note'><b>Why this is stronger</b><ul>{comments}</ul>{example}</div>" if comments or example else "")}
          {(('<details><summary>Useful lexical upgrades</summary><ul>'+upgrades+'</ul></details>') if upgrades else '')}
        </section>""")
    full = ""
    if obj.get("full_model_essay"):
        full = f"<details class='full'><summary>Show full AI model essay</summary><div class='essay'>{e(obj.get('full_model_essay')).replace(chr(10), '<br>')}</div></details>"
    return f"""<!doctype html>
<html><head><meta charset='utf-8'><title>AI IELTS Structure Comparison</title>
<style>
body{{font-family:Arial,sans-serif;background:#f7f7f7;margin:0;color:#222}}.header{{background:#202124;color:white;padding:16px 22px}}.wrap{{max-width:1280px;margin:0 auto;padding:18px}}.card,.full{{background:white;border:1px solid #ddd;border-radius:12px;margin:14px 0;padding:14px}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.grid.three{{grid-template-columns:1fr 1fr 1fr}}.box{{border:1px solid #ddd;border-radius:10px;background:#fafafa;padding:12px}}.model{{background:#eef8f0}}.note{{margin-top:10px;background:#fff8e1;border-left:6px solid #f9ab00;padding:10px}}.small{{font-size:13px;color:#666}}.warn{{background:#fff3cd;border-left:6px solid #f9ab00;padding:10px}}.essay{{white-space:pre-wrap;line-height:1.45;margin-top:10px}}@media(max-width:1050px){{.grid.three{{grid-template-columns:1fr}}}}@media(max-width:850px){{.grid{{grid-template-columns:1fr}}}}
</style></head>
<body><div class='header'><h2>AI IELTS Structure Comparison</h2><div class='small'>After your revision · model rewrites the original essay</div></div>
<div class='wrap'><p>Use this to compare your revision with a model rewrite of your original essay. Do not copy it.</p><p class='small'>Model source: original essay. Student revision is used for comparison only.</p>{status_note}{''.join(cards)}{full}</div></body></html>"""


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Generate post-revision IELTS structure comparison V1.7.1")
    ap.add_argument("--revision-request", required=True)
    ap.add_argument("--revision-output", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--markdown")
    ap.add_argument("--html")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--max-ai-attempts", type=int, default=4)
    ap.add_argument("--disable-schema-fallback", action="store_true")
    ap.add_argument("--include-full-model-in-md", action="store_true")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)

    req = read_json(args.revision_request)
    out = read_json(args.revision_output)
    ws = read_json(args.workspace)
    obj = build_comparison(req, out, ws, use_llm=not args.no_llm, model=args.model, limit=args.limit, max_attempts=args.max_ai_attempts, allow_schema_fallback=not args.disable_schema_fallback)
    write_json(args.output, obj, pretty=args.pretty)
    if args.markdown:
        write_text(args.markdown, render_md(obj, include_full_model=args.include_full_model_in_md))
    if args.html:
        write_text(args.html, render_html(obj))
    print(json.dumps({"status": "ok", "generation_status": obj["generation_status"], "model_source": "original_essay", "model_available_to_student": obj.get("model_available_to_student"), "output": args.output}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
