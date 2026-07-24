# Vocabulary Coach — PEEL Prompt Bank Generation Prompt v1

Use this as a standalone instruction set (paste into whatever process — LLM call, offline script, or a fresh assistant session — builds this bank). It does not assume any other context.

## Background

ST.ELLA's Vocabulary Coach (LRET + PEEL) teaches target lexis through short PEEL micro-tasks: **one paragraph, one idea** (Point-Evidence-Example-Link). Two resources already exist and are verified/ready:

- `vocab_coach_topic_bank_v1_3_0.json` — 789 items across 10 topics. Four topics (`environment`, `technology`, `health`, `education`) are split into subtopics (each with a `subtopics_index` + `subtopics.<name>.items[]` plus a small `general` bucket for cross-cutting items); the other six are flat (`topics.<topic>.items[]`). Every item carries `cefr_estimate` (A1–C2) for band-gating.
- `vocab_coach_task_type_bank_v1_2_0.json` — 87 items across 4 task types (`opinion`, `cause_effect_problem_solution`, `advantages_disadvantages`, `discussion`). Every item in the latter three carries an `angle` field: `cause`/`effect`/`problem`/`solution` for the first, `advantage`/`disadvantage` for the second, `side_a`/`side_b` for the third. `opinion` has no angle (it's already single-idea by nature). A separate `essay_level_only` bucket holds items that don't fit any single-idea paragraph (excluded from selection).

**What's missing, and what this prompt builds:** neither bank contains an actual scenario for the student to write about. Selection needs a curated bank of short PEEL micro-task prompts — realistic IELTS-style contexts, each tagged so it can be matched to (a) which topic/subtopic(s) a student's just-graded essay was about, and (b) which task_type + angle to exercise, so the resulting micro-task pulls the right slice of vocabulary from the two banks above.

## Deliverable

New file: `vocab_coach_prompt_bank_v1_0_0.json`. Do not touch either existing bank file.

### Schema

```
{
  "bank_id": "vocab_coach_prompt_bank_v1.0.0",
  "scope_note_changelog": [...],
  "prompts": [
    {
      "prompt_id": "...",
      "topic": "environment",
      "subtopics": ["pollution"],            // 1 or 2 entries; 2 means this prompt genuinely spans both
      "task_type": "advantages_disadvantages",
      "angle": "disadvantage",                // ONE angle this specific prompt entry targets -- see requirement 1a
      "scenario_text": "Many governments now impose strict fines on factories that exceed pollution limits.",
      "instruction_template": "In one paragraph, describe ONE disadvantage of this policy. Try to use at least two of these words naturally: {target_items}.",
      "suggested_vocabulary": [
        {"phrase": "a heavy fine", "source_bank": "topic", "topic": "environment", "subtopic": "pollution"},
        {"phrase": "combat pollution", "source_bank": "topic", "topic": "environment", "subtopic": "pollution"},
        {"phrase": "a further downside worth noting is", "source_bank": "task_type", "task_type": "advantages_disadvantages", "angle": "disadvantage"},
        {"phrase": "the main drawback is that", "source_bank": "task_type", "task_type": "advantages_disadvantages", "angle": "disadvantage"}
      ],
      "registry_note": "..."
    }
  ]
}
```

- `subtopics`: only populated for the 4 topics that have them; omit or leave empty for the other 6 (flat topics select from the topic's whole pool).
- `angle`: **one single angle per prompt entry**, not a list. If a scenario can support more than one angle (e.g. the same factory-fines scenario could also be used for an `advantage` paragraph), author **two separate prompt entries** sharing the same `scenario_text`/topic/subtopics but different `angle`, `instruction_template`, and `suggested_vocabulary` — never one entry that tries to cover both, since that's exactly the multi-idea framing this design exists to avoid. `opinion` entries omit `angle` entirely (no angle field applies).
- `instruction_template`: the actual, final, ready-to-show instruction text — not a placeholder to be resolved by a not-yet-built engine. The angle framing ("ONE disadvantage") is written directly into the sentence. The only substitution left for later is `{target_items}`, which gets filled from this entry's own `suggested_vocabulary` list (see below) — not selected fresh from the whole bank at runtime.
- **`suggested_vocabulary` — this is the part from your original request ("create prompts with suggested topic+task vocabulary") that must not be skipped.** Every prompt entry ships with its own concrete, pre-curated vocabulary set attached at authoring time — real items, each one an actual verifiable entry from the existing topic bank (matching this prompt's `topic`+`subtopics`) and the existing task-type bank (matching this prompt's `task_type`+`angle`), not invented or paraphrased. Target size: 4–6 items per prompt, a mix of topic-vocabulary (2–4 items) and task-type-vocabulary (1–2 items) so a single paragraph has both content words and rhetorical framing language available. This makes the prompt bank a complete, usable, human-reviewable artifact on its own — exactly like the two existing banks are — rather than inert tags waiting on an engine that doesn't exist yet. A future engine can still substitute an already-taught item out of this list per the student's retention ledger; that's a refinement on top of a real authored set, not a replacement for one.

## Requirements

1. **One-idea discipline is non-negotiable and must be baked into the phrasing itself, not left to the engine to enforce at runtime.** Every `instruction_template` must contain an explicit singular marker matching its `angle` — "ONE advantage", "ONE disadvantage", "ONE cause", "ONE problem", etc. Never phrase a template as "discuss the advantages and disadvantages of X" or "explain the causes and effects of X" — that is exactly the multi-idea framing this whole design exists to avoid. If the same `scenario_text` can support more than one angle, author separate prompt entries (see the schema note on `angle`) rather than one entry covering both.
1a. **Every prompt entry must carry its own `suggested_vocabulary` list — this is not optional.** Each item in it must be a real, verifiable entry copied from the actual topic bank (matching this prompt's `topic`/`subtopics`) or the actual task-type bank (matching this prompt's `task_type`/`angle`) — never invented, paraphrased, or approximated. 4–6 items per prompt: 2–4 from the topic bank, 1–2 from the task-type bank.
2. **Coverage:** at least 2–3 prompts per (topic-or-subtopic × task_type) combination, so a student doesn't see the identical scenario every time they write about, say, `environment::pollution` under `advantages_disadvantages`. Exact count can flex by topic size, but don't leave any combination at zero.
3. **Multi-subtopic prompts, explicitly included:** for each of the 4 subtopic-bearing topics, include at least one worked example of a prompt tagged with 2 subtopics (e.g. a prompt combining renewable energy investment with public transport expansion, tagged `["energy_and_resources", "urbanization_and_housing"]` if it genuinely crosses topics, or two subtopics within one topic like `["energy_and_resources", "pollution"]`). Selection for these prompts should draw candidate vocabulary from the **union** of all tagged subtopics' pools, not force a single-subtopic choice — this was agreed explicitly and must actually be exercised by real examples, not just theoretically supported by the schema.
4. **Realistic scenarios, not invented absurdities.** `scenario_text` should read like an authentic (if compressed) IELTS Task 2 context — adapt from genuine recurring IELTS themes for that topic/subtopic, don't fabricate implausible setups just to hit a coverage number.
5. **No duplicate `(scenario_text, task_type, angle)` combinations.** Dedup discipline stays consistent with the rest of this project — check before shipping, don't rely on eyeballing.
6. **CEFR/band-gating stays where it already lives.** Do not add a CEFR field to prompt entries — vocabulary-level band-gating is already solved via each vocabulary item's `cefr_estimate`; duplicating that concern here would create a second, likely-inconsistent source of truth. Prompts are topic/task/angle-scoped only.
7. **Versioning discipline:** new file only (`vocab_coach_prompt_bank_v1_0_0.json`), changelog-style `scope_note_changelog` matching the convention already used in both existing banks, honest disclosure of any limitations the way the topic-bank methodology doc modeled (don't overclaim coverage or grounding).
8. **State plainly, do not hide, that no engine currently reads any of these three banks.** Vocabulary Coach has no implementation yet (confirmed directly — no `vocab_coach_engine*.py` or equivalent exists anywhere in the project). This prompt bank is content authoring, same as the vocabulary banks were, and is not blocked by the engine not existing yet — but don't describe a hypothetical engine's function names or failure modes as though they're a real, currently-existing dependency. If you want to flag forward-looking integration concerns, frame them as "when an engine is eventually built, it will need to..." not "the engine currently does X and will break."

## Verification checklist (run before calling this done)

1. Recompute coverage counts per (topic-or-subtopic × task_type); confirm no combination is at zero.
2. Confirm every `instruction_template` contains an explicit singular marker matching its entry's `angle`.
3. Confirm at least one genuine 2-subtopic prompt exists per subtopic-bearing topic (environment, technology, health, education).
4. Confirm 0 duplicate `(scenario_text, task_type, angle)` combinations.
5. Confirm no CEFR field was added to prompt entries.
6. **Confirm every single prompt entry has a non-empty `suggested_vocabulary` list (4–6 items), and spot-check a sample of items against the actual topic bank / task-type bank files to confirm they're real entries, not fabricated.** A prompt with tags but no attached vocabulary fails this checklist outright.
7. Confirm the new file is the only file written; both existing bank files remain byte-identical.
