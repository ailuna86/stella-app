#!/usr/bin/env python3
"""
vocab_coach_response_grader_v1_0.py
=====================================

Implements Component 2 of VOCABULARY_COACH_ENGINE_BUILD_PROMPT_V1.md: grades
a student's one-paragraph PEEL submission against the target vocabulary from
a `vocab_coach_session` artifact (produced by
`vocab_coach_selection_engine_v1_0.py`).

Scope boundary (Requirement/Section 4 of the build prompt): lexical
precision/accuracy only. This is NOT a grammar checker -- general sentence
grammar is Writing Coach's job. Grammar that is intrinsic to the target item
itself (e.g. a wrong preposition inside a target phrasal verb) is in scope;
everything else in the paragraph is not evaluated here.

Anti-gaming requirement (non-negotiable per the build prompt): a naive
regex/string-match implementation is explicitly rejected as gameable. This
grader always attempts a semantic check before ever awarding
`used_correctly`; if no LLM is available, it fails safe to `needs_review`
rather than defaulting to a false-positive pass.

LLM call pattern: mirrors `vocab_coach_engine_v1_0_0.py`'s own
`_call_llm_judge` (same repo, same project, already confirmed to exist and
run) -- gated on `OPENAI_API_KEY` being present, returns None (not a
fabricated result) on any failure/timeout/missing key. The build prompt asked
this to mirror `det_vip_v18d_3_topic_alignment_risk.py`'s `llm_json()`
pattern, but that file does not exist in any connected folder (confirmed
directly); this project's own `vocab_coach_engine_v1_0_0.py` precedent is
used instead, since it is real, already-established, and implements the same
fail-safe contract.

No OPENAI_API_KEY is present in this sandbox (checked directly: `env | grep
-i openai` returns nothing, and the `openai` package itself is not
installed). Every verdict produced by a run in this environment will
therefore be an honest `needs_review` mechanical-fallback result, not a
fabricated `used_correctly` -- this is the required fail-safe behaviour,
demonstrated for real, not simulated.

CLI:
    --session PATH            (vocab_coach_session artifact from the selection engine)
    --response PATH_OR_TEXT   (a file path if it exists on disk, else treated as literal text)
    --output PATH
    --use-llm                 (flag; default off even if a key exists, must be explicitly enabled)
    --model STR                (default: CHEAP_MODEL)
"""
import argparse
import json
import os
import re
import sys

ENGINE_VERSION = "vocab-coach-response-grader-v1.0"
CHEAP_MODEL = os.environ.get("VOCAB_COACH_LLM_MODEL", "gpt-5-nano")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def read_response(arg):
    if os.path.exists(arg):
        with open(arg, "r", encoding="utf-8") as f:
            return f.read()
    return arg


def normalize_phrase_for_matching(phrase):
    """Strips parenthetical placeholders/example-fillers and sb/sth markers
    so 'agree with (an idea/statement)' can still mechanically match 'agree
    with' in text, and 'result in sth' matches 'result in'. This is only used
    for the cheap not_used pre-filter below -- it never decides
    used_correctly on its own (that always requires the semantic check)."""
    p = re.sub(r"\([^)]*\)", "", phrase)
    p = re.sub(r"\bsb/sth\b|\bsb\b|\bsth\b", "", p)
    p = re.sub(r"\s+", " ", p).strip().lower()
    return p


def mechanical_presence(paragraph, phrase):
    text = paragraph.lower()
    core = normalize_phrase_for_matching(phrase)
    if not core:
        return False
    # match on the core words appearing in order within a short span, not
    # necessarily contiguous (covers "result in a rise" for "result in")
    words = core.split()
    if len(words) == 1:
        return re.search(rf"\b{re.escape(words[0])}\b", text) is not None
    pattern = r"\b" + r"\W+(?:\w+\W+){0,4}?".join(re.escape(w) for w in words) + r"\b"
    return re.search(pattern, text) is not None


def _call_llm_judge(prompt):
    """Mirrors vocab_coach_engine_v1_0_0.py's _call_llm_judge fail-safe
    contract: only attempts a call if a key is actually present, returns
    None (never a fabricated result) on any missing-key/error/timeout."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        import openai  # type: ignore
        client = openai.OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=CHEAP_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
        )
        content = resp.choices[0].message.content
        return json.loads(content)
    except Exception as exc:
        print(f"[vocab_coach_grader] LLM judge call failed, falling back to mechanical-only: {exc}", file=sys.stderr)
        return None


def build_judge_prompt(paragraph, items):
    item_lines = "\n".join(f"- \"{it['phrase']}\"" for it in items)
    return f"""You are grading whether a student correctly used specific target vocabulary items in ONE paragraph, with their correct MEANING in a sensible context -- not just whether the literal string appears.

Paragraph:
\"\"\"{paragraph}\"\"\"

Target items:
{item_lines}

For each target item, decide ONE of:
- "used_correctly": the item appears, used with its correct meaning, in a sensible, natural context relevant to the paragraph's topic.
- "used_but_awkward": the item appears with roughly the right meaning but the phrasing is unnatural, ungrammatical in a way intrinsic to the item itself (e.g. wrong preposition inside a phrasal verb), or slightly forced.
- "attempted_incorrectly": the item (or a close variant) appears but is used with the wrong meaning, in a nonsensical way, or just dropped into an unrelated sentence without real integration.
- "not_used": the item does not appear at all, in any form.

Be strict about "used_correctly" -- a student pasting the exact phrase into a sentence that doesn't actually reflect its meaning should be marked "attempted_incorrectly", not "used_correctly".

Also give ONE short paragraph-level note on whether the paragraph stayed focused on a single idea/angle (not a full rubric -- just yes/uncertain/no and why).

Return ONLY this JSON:
{{
  "per_item": [
    {{"phrase": "...", "verdict": "used_correctly|used_but_awkward|attempted_incorrectly|not_used", "evidence": "one line quoting or describing what you found"}}
  ],
  "paragraph_scope": {{"one_idea_ok": true/false/null, "note": "one line"}}
}}"""


def grade(session, paragraph, use_llm):
    prompt_items = session["prompt"]["suggested_vocabulary"]
    review_items = session.get("review_items", [])
    all_items = (
        [{"phrase": it["phrase"], "source": "new"} for it in prompt_items]
        + [{"phrase": it["phrase"], "source": "review"} for it in review_items]
    )

    # Cheap deterministic pre-check: anything that isn't even present as a
    # string is not_used -- no need to spend an LLM call confirming that.
    present_items = []
    verdicts = {}
    for it in all_items:
        if mechanical_presence(paragraph, it["phrase"]):
            present_items.append(it)
        else:
            verdicts[it["phrase"]] = {
                "phrase": it["phrase"],
                "source": it["source"],
                "verdict": "not_used",
                "evidence": "Phrase (or its recognisable core form) does not appear in the submission.",
                "llm_checked": False,
            }

    llm_result = None
    if use_llm and present_items:
        judge_prompt = build_judge_prompt(paragraph, present_items)
        llm_result = _call_llm_judge(judge_prompt)

    paragraph_note = {"one_idea_ok": None, "note": "Not checked -- no LLM available/enabled in this run."}

    if llm_result and "per_item" in llm_result:
        by_phrase = {row["phrase"]: row for row in llm_result["per_item"]}
        for it in present_items:
            row = by_phrase.get(it["phrase"])
            if row:
                verdicts[it["phrase"]] = {
                    "phrase": it["phrase"],
                    "source": it["source"],
                    "verdict": row.get("verdict", "needs_review"),
                    "evidence": row.get("evidence", ""),
                    "llm_checked": True,
                }
            else:
                verdicts[it["phrase"]] = {
                    "phrase": it["phrase"],
                    "source": it["source"],
                    "verdict": "needs_review",
                    "evidence": "LLM did not return a verdict for this item.",
                    "llm_checked": False,
                }
        ps = llm_result.get("paragraph_scope", {})
        paragraph_note = {"one_idea_ok": ps.get("one_idea_ok"), "note": ps.get("note", "")}
    else:
        # Fail-safe: string is present but meaning is NOT verified. Never
        # award used_correctly without a semantic check having actually run.
        for it in present_items:
            verdicts[it["phrase"]] = {
                "phrase": it["phrase"],
                "source": it["source"],
                "verdict": "needs_review",
                "evidence": (
                    "Phrase is present as a string, but no LLM semantic check ran "
                    "(disabled, no API key, or call failed) -- meaning/context is "
                    "NOT verified, so this is intentionally not marked used_correctly."
                ),
                "llm_checked": False,
            }

    return [verdicts[it["phrase"]] for it in all_items], paragraph_note


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--response", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--use-llm", action="store_true")
    ap.add_argument("--model", default=CHEAP_MODEL)
    args = ap.parse_args()

    globals()["CHEAP_MODEL"] = args.model

    session = load_json(args.session)
    paragraph = read_response(args.response)

    item_verdicts, paragraph_note = grade(session, paragraph, args.use_llm)

    result = {
        "artifact_type": "vocab_coach_grading",
        "schema_version": "vocab_coach_grading_v1.0",
        "engine_version": ENGINE_VERSION,
        "session_id": session.get("session_id"),
        "student_id": session.get("student_id"),
        "use_llm": args.use_llm,
        "model": CHEAP_MODEL if args.use_llm else None,
        "llm_key_present": bool(os.environ.get("OPENAI_API_KEY")),
        "item_verdicts": item_verdicts,
        "paragraph_note": paragraph_note,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"[vocab_coach_grader] wrote grading result to {args.output}")


if __name__ == "__main__":
    main()
