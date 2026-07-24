# Vocabulary Coach PEEL Prompt Bank v1.0.0 — Addendum

**New file:** `vocab_coach_prompt_bank_v1_0_0.json` (228 prompt entries).
**Untouched:** `vocab_coach_topic_bank_v1_3_0.json` and `vocab_coach_task_type_bank_v1_2_0.json` — both confirmed byte-identical before and after this build via `md5sum` (topic bank `64dc25b9...`, task-type bank `097ebddb...`).
**Source:** implements `VOCAB_COACH_PROMPT_BANK_GENERATION_PROMPT_V1.md`.

## A correction to that prompt's own Requirement 8

Requirement 8 states: *"Vocabulary Coach has no implementation yet (confirmed directly — no `vocab_coach_engine*.py` or equivalent exists anywhere in the project)."* This is factually wrong. `vocab_coach_engine_v1_0_0.py` through `vocab_coach_engine_v1_3_0.py` already exist in this same folder, built earlier in this working session.

The substance of Requirement 8 still holds and was honoured here: none of those engine files currently read this new prompt bank, the topic bank's `subtopics`/`subtopics_index` structure, or the task-type bank's `angle` field — a gap already flagged directly in `VOCAB_COACH_BANK_REVISION_V1_addendum.md`. This bank is written as a standalone, human-reviewable content artifact, same as the two vocabulary banks, not as something that assumes a reading engine exists. Forward-looking note, framed correctly this time: when an engine is updated to consume this bank, it will need to read `suggested_vocabulary` directly rather than re-deriving it, and will need to match a student's essay topic/subtopic + directive task_type/angle against `prompts[].topic`/`subtopics`/`task_type`/`angle`.

## What's in the bank

228 prompt entries:
- 220 single-unit prompts across all 22 real units (6 flat topics + 16 real subtopics across environment/technology/health/education) — each unit carries 10 prompts: 2 `opinion`, 2 `advantages_disadvantages` (one advantage-angle, one disadvantage-angle), 4 `cause_effect_problem_solution` (full angle coverage: cause/effect/problem/solution), 2 `discussion` (side_a/side_b). This clears the "2-3 prompts per (unit × task_type)" coverage floor everywhere — verified programmatically, minimum coverage across all 88 (unit × task_type) combinations is 2, most are higher.
- 8 multi-subtopic worked examples, one pair per subtopic-bearing topic (`environment::energy_and_resources+pollution`, `technology::devices_and_innovation+data_and_security`, `health::lifestyle_and_prevention+mental_health`, `education::skills_and_curriculum+access_and_equality`), each authored as two `advantages_disadvantages` entries (advantage / disadvantage) sharing one `scenario_text`, drawing vocabulary from the union of both tagged subtopics.

Each entry ships its own `suggested_vocabulary` (4-6 items: 2-4 from the topic bank matching that prompt's topic/subtopic(s), 1-2 from the task-type bank matching that prompt's task_type/angle) — every single item was looked up and confirmed against the live bank files by the build script itself (not hand-typed and hoped to be correct); the script exits with an error rather than writing the file if any item fails that check. `general` topic-bank buckets were deliberately excluded from unit selection, consistent with how they're already treated as a non-classification pool in the topic bank itself.

## Verification checklist (all passed, checked programmatically)

1. Coverage: no (unit × task_type) combination is below 2 prompts.
2. Every `instruction_template` contains an explicit singular marker ("ONE advantage", "ONE cause", "ONE problem", "ONE effect", etc.) matching its `angle` — checked via regex, not eyeballed.
3. At least one genuine 2-subtopic prompt exists for each of environment/technology/health/education (8 total, 2 each).
4. Zero duplicate `(scenario_text, task_type, angle)` combinations.
5. No `cefr`/`cefr_estimate` field anywhere in the prompt bank.
6. Every prompt entry has 4-6 `suggested_vocabulary` items, all re-verified as real entries against the live bank files.
7. Both existing bank files confirmed byte-identical (checksummed before and after).

## Known limitations (stated plainly)

- Each unit reuses only 2 authored scenario texts (A/B) across its 10 task-type/angle prompts. This keeps dedup clean and the bank tractable to author by hand, but a student doing many sessions on one topic will eventually see a familiar scenario paired with a new angle. More scenario variety per unit is a natural v1.1.0 expansion.
- Task-type vocabulary (opinion/cause-effect/advantages-disadvantages/discussion phrases) is drawn from small rotating pools shared across all 22 units, since these phrases are topic-agnostic by design — the same phrase can legitimately appear in more than one prompt's `suggested_vocabulary`. This doesn't violate the dedup rule, which is keyed on `(scenario_text, task_type, angle)`, not on vocabulary contents.
- The 4 multi-subtopic examples pair two subtopics within the same topic, per the spec's own example phrasing. Cross-topic scenario pairing (e.g. environment + urbanization) was not attempted in this pass.
