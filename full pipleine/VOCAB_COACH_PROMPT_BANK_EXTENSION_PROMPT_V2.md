# Vocabulary Coach — PEEL Prompt Bank Extension Prompt v2

Use this as a standalone instruction set (paste into whatever process — LLM call, offline script, or a fresh assistant session — builds this bank). It does not assume any other context, but it extends a prior deliverable (`VOCAB_COACH_PROMPT_BANK_GENERATION_PROMPT_V1.md`, the prompt that built `vocab_coach_prompt_bank_v1_0_0.json`) rather than starting from scratch — read that file's schema/requirements/checklist too if it's available, since this prompt only states what's *different* for the extension, not the full rulebook again.

## Background: what changed since v1_0_0, and the real, verified gap this creates

Since `vocab_coach_prompt_bank_v1_0_0.json` (228 prompts) was built, the topic bank underneath it grew twice:

- `vocab_coach_topic_bank_v1_4_0.json` added 8 new top-level topics (`society_and_ageing`, `family_and_relationships`, `globalization_and_international_relations`, `sport_and_leisure`, `science_and_space`, `food_and_diet`, `transportation`, `language_and_culture`) plus a topic-anchored `academic_collocations` subtopic under every topic that has subtopics (Tier B academic vocabulary — see `Topic_Academic_Vocab_Expansion_Spec_v1.docx`).
- `vocab_coach_topic_bank_v1_5_0.json` (current) added a per-unit `academic_words` field (bare academic words, picked at selection-engine runtime, not prompt-bank content — see next section). This did not add or remove any topics/subtopics, so it does not change the coverage math below.

`vocab_coach_selection_engine_v1_1.py`'s `enumerate_units()` derives its rotation units directly from the topic bank's structure, excluding only the `general` subtopic. That means every subtopic the topic bank now has — including `academic_collocations` — is a real, selectable rotation unit that `vocab_coach_selection_engine_v1_2.py`'s `filter_candidates()` will look up in the prompt bank. **Confirmed directly, not estimated:** cross-checking `vocab_coach_topic_bank_v1_5_0.json`'s 57 total rotation units against `vocab_coach_prompt_bank_v1_0_0.json`'s actual coverage, **35 units have zero prompt-bank entries** and will crash the selection engine (`SystemExit: No prompt-bank candidates found for {unit} / {task_type} -- bank coverage gap.`) the first time rotation lands on them. The 35:

- The 8 new topics' own regular subtopics (23 total): `society_and_ageing::{demographic_change, elderly_care_and_support, generational_responsibility}`, `family_and_relationships::{parenting_and_child_rearing, family_structure_and_dynamics, technology_and_relationships}`, `globalization_and_international_relations::{trade_and_economy, culture_and_identity, cooperation_and_diplomacy}`, `sport_and_leisure::{competitive_sport_and_performance, leisure_and_recreation, sport_and_society}`, `science_and_space::{scientific_research_and_innovation, space_exploration, ethics_and_funding_priorities}`, `food_and_diet::{diet_and_health, food_industry_and_production, food_culture_and_choice}`, `transportation::{public_transport, traffic_and_urban_mobility, environmental_impact_of_transport}`, `language_and_culture::{language_learning_and_multilingualism, cultural_identity_and_heritage}`.
- The `academic_collocations` subtopic under all 12 subtopic-bearing topics (12 total): the 8 new topics above **plus the original 4** (`environment`, `technology`, `health`, `education`) — this half of the gap is *not* new, it has existed silently since `academic_collocations` was first added and would already crash today if rotation happened to land there.

This prompt's job is to close all 35, not just the 8-new-topics half — closing only the new topics and leaving the 4 old topics' `academic_collocations` gap open would just move the crash, not fix it.

## What this prompt does NOT cover (explicitly out of scope)

- **Academic words are not prompt-bank content.** Per `Academic_Words_Redesign_Spec_v1.docx` §3, bare `academic_words` are selected by the engine at session-generation time from the topic bank directly, not baked into any prompt's `suggested_vocabulary` at authoring time. Do not add an `academic_word`-sourced item to any `suggested_vocabulary` list in this deliverable — every item must be `source_bank: "topic"` or `source_bank: "task_type"`, exactly as in v1_0_0.
- **The `academic_collocations` subtopic's own vocabulary IS prompt-bank content, though** — it's Tier B, topic-anchored, authored collocations (`"mitigate the effects of climate change"`, etc.), exactly like any other subtopic's items. Prompts rotating to `topic::academic_collocations` should pull `suggested_vocabulary` from that subtopic's item pool the same way any other subtopic prompt does — nothing special here except that the "topic content" being practiced happens to already be academic-register.
- Renumbering, editing, or removing any of the 228 existing prompts in `vocab_coach_prompt_bank_v1_0_0.json`. This is additive only.

## Deliverable

New file: `vocab_coach_prompt_bank_v1_1_0.json`. Do not modify `vocab_coach_prompt_bank_v1_0_0.json`, `vocab_coach_topic_bank_v1_5_0.json`, or `vocab_coach_task_type_bank_v1_2_0.json` — new versioned file only, per this project's standing convention.

**Structure:** load `vocab_coach_prompt_bank_v1_0_0.json`, copy its `prompts` array byte-identical (all 228 entries, unchanged), append newly authored prompts for the 35 gap units, bump `bank_id` to `"vocab_coach_prompt_bank_v1.1.0"`, add `"supersedes": "vocab_coach_prompt_bank_v1.0.0"`, and append an entry to `scope_note_changelog` describing this extension (topics/subtopics added, count of new prompts, the `academic_collocations` gap closed for all 12 topics not just the 8 new ones). The output file must contain every original prompt plus the new ones — it replaces v1_0_0 as the engine's `--prompt-bank` input, it does not sit alongside it as a second file (the engine takes one `--prompt-bank` path).

### Schema (unchanged from v1_0_0 — repeated here for a self-contained reference)

```
{
  "bank_id": "vocab_coach_prompt_bank_v1.1.0",
  "supersedes": "vocab_coach_prompt_bank_v1.0.0",
  "scope_note_changelog": [...],
  "prompts": [
    {
      "prompt_id": "...",
      "topic": "society_and_ageing",
      "subtopics": ["elderly_care_and_support"],
      "task_type": "advantages_disadvantages",
      "angle": "disadvantage",
      "scenario_text": "Some countries are raising the retirement age in response to a growing elderly population.",
      "instruction_template": "In one paragraph, describe ONE disadvantage of this policy. Try to use at least two of these words naturally: {target_items}.",
      "suggested_vocabulary": [
        {"phrase": "...", "source_bank": "topic", "topic": "society_and_ageing", "subtopic": "elderly_care_and_support"},
        {"phrase": "...", "source_bank": "topic", "topic": "society_and_ageing", "subtopic": "elderly_care_and_support"},
        {"phrase": "...", "source_bank": "task_type", "task_type": "advantages_disadvantages", "angle": "disadvantage"}
      ],
      "registry_note": "..."
    }
  ]
}
```

Every field means exactly what it meant in v1_0_0 (see that prompt's schema notes if you need the full explanation of `subtopics`, `angle`, or `instruction_template`) — nothing about the shape changed, only the coverage.

## Requirements

1. **Close all 35 listed gap units, and only those 35** (plus any genuine multi-subtopic prompts per requirement 3 below, which necessarily also touch these units). Do not re-author or duplicate coverage for units v1_0_0 already covers.
2. **One-idea discipline, angle rules, and the `suggested_vocabulary` authoring discipline are unchanged from v1_0_0's requirements 1/1a** — read them there. In short: every `instruction_template` has an explicit singular marker matching its `angle`; a scenario supporting more than one angle gets separate prompt entries, never one entry covering both; every prompt ships 4–6 real, verifiable `suggested_vocabulary` items (2–4 from the topic bank matching this prompt's `topic`/`subtopics`, 1–2 from the task-type bank matching this prompt's `task_type`/`angle`) — never invented, paraphrased, or approximated, and never `source_bank: "academic_word"` (see scope note above).
3. **Coverage: at least 2–3 prompts per (unit × task_type) combination** across all 35 units — same bar v1_0_0 used, applied here to the new units. That's roughly 35 units × 4 task_types × 2–3 prompts ≈ 280–420 new prompts at the ceiling, though angle-bearing task_types (`advantages_disadvantages`, `cause_effect_problem_solution`, `discussion`) need entries per angle, not per task_type as a whole — don't undercount by treating one `advantages_disadvantages` entry as covering both `advantage` and `disadvantage`.
4. **Multi-subtopic prompts:** for each of the 8 new topics, include at least one prompt tagged with 2 of its own subtopics (e.g. `sport_and_leisure` combining `competitive_sport_and_performance` + `sport_and_society`), same discipline as v1_0_0 requirement 3. `academic_collocations` prompts don't need a multi-subtopic worked example — pair them with their own topic's regular subtopics if a genuine cross-subtopic scenario exists, but don't force one.
5. **Realistic scenarios.** Same bar as v1_0_0 requirement 4 — authentic-feeling IELTS Task 2 contexts for each of these 8 topics (all of which are common, real IELTS Task 2 categories — see `Topic_Academic_Vocab_Expansion_Spec_v1.docx` §1 for why each was prioritized), not fabricated setups to hit a count.
6. **No duplicate `(scenario_text, task_type, angle)` combinations** — check across the FULL merged 228 + new set, not just within the new prompts, in case a scenario idea accidentally reuses one already in v1_0_0 for a different topic.
7. **No CEFR field on prompt entries** — unchanged from v1_0_0 requirement 6, same reasoning (band-gating already lives on the vocabulary items themselves).
8. **Versioning discipline:** new file only, `scope_note_changelog` entry describing exactly what was added (topic/subtopic list, prompt count, the `academic_collocations`-for-4-old-topics gap explicitly called out as closed), honest disclosure if any of the 35 units ends up with thinner coverage than the 2–3-per-task_type bar (e.g. a genuinely low-content subtopic like `language_and_culture::cultural_identity_and_heritage` at 11 topic-bank items) — don't overclaim uniform depth across units that aren't actually uniform in size.

## Verification checklist (run before calling this done)

1. Recompute the exact 35-unit list from `vocab_coach_topic_bank_v1_5_0.json`'s structure (mirror `enumerate_units()`: every subtopic except `general`, plus every flat topic) crossed against `vocab_coach_prompt_bank_v1_0_0.json`'s coverage, the same way this prompt's Background section did — confirm the list still matches (18 topics is the current count; re-derive rather than trust the number 35 if the topic bank has changed again since this prompt was written).
2. Confirm every one of those 35 units has at least one prompt for every task_type/angle combination (4 for `opinion`; per-angle for the other three).
3. Confirm every new `instruction_template` contains an explicit singular marker matching its entry's `angle`.
4. Confirm at least one genuine 2-subtopic prompt exists for each of the 8 new topics.
5. Confirm 0 duplicate `(scenario_text, task_type, angle)` combinations across the full 228 + new merged set.
6. Confirm no CEFR field was added to any prompt entry.
7. Confirm every new prompt entry has a non-empty `suggested_vocabulary` list (4–6 items), zero of which are `source_bank: "academic_word"`, and spot-check a sample against the actual `vocab_coach_topic_bank_v1_5_0.json` / `vocab_coach_task_type_bank_v1_2_0.json` files to confirm they're real entries.
8. Confirm all 228 original prompts are present, byte-identical, in the output file (diff the two files' overlapping prompt_ids if easiest).
9. **Run `vocab_coach_selection_engine_v1_2.py` against the new file** (any student, forcing rotation onto a few of the 35 previously-crashing units via a synthetic ledger with the other units pre-exposed, the same technique used to smoke-test the engine itself) and confirm it no longer raises `SystemExit` for any of them.
