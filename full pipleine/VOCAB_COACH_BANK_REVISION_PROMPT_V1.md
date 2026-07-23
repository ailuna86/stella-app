# Vocabulary Coach Bank Revision — Generation Prompt v1

Use this as a standalone instruction set (paste into whatever process — LLM call, offline script, or a fresh assistant session — regenerates these banks). It does not assume any other context.

## Background

ST.ELLA's Vocabulary Coach (LRET + PEEL) selects target lexis for students by topic and by IELTS Task 2 rhetorical function, then teaches it through short PEEL micro-tasks with retention retesting. Two source files currently drive selection:

- `vocab_coach_topic_bank_v1_2_0.json` — 610 items across 10 topics (environment, technology, education, crime_and_justice, health, government_and_economy, work_and_employment, media_and_communication, urbanization_and_housing, tourism_and_travel). Each item: `phrase`, `type` (`collocation`/`noun_phrase`/`phrasal_verb`), `headword`, `relation_type` (`adjective_modifier`/`verb_object_or_verb_head`/`head_noun_compound`/`phrasal_or_governance_pattern`), `collocate`, `confidence`, `tier` (`curated_core`/`registry_expansion`), `cefr_estimate` (A1–C2), `difficulty_tier`, `difficulty_basis`, `registry_source`.
- `vocab_coach_task_type_bank_v1_1_0.json` — 56 items across 3 task types (opinion, cause_effect_problem_solution, advantages_disadvantages). Each item: `phrase`, `type` (`fixed_phrase`/`verb`/`collocation`), `level` (basic/intermediate/advanced), `pattern` (for verbs), `registry_check`, `registry_source` (where confirmed).

**Critical constraint on how this bank is actually used:** every PEEL micro-task is one paragraph, one idea (Point-Evidence-Example-Link). A task built from `advantages_disadvantages` asks the student to develop **one** advantage *or* **one** disadvantage — never both in the same paragraph. A task built from `cause_effect_problem_solution` asks for **one** cause-and-its-effect *or* **one** problem-and-its-solution — not the full causal chain. This must shape how items are tagged and selected (see Problem 9).

Both files' `scope_note` should read as a running changelog (each version states what changed and why, same convention already used). **Do not edit the existing files in place** — write new versioned files (`vocab_coach_topic_bank_v1_3_0.json`, `vocab_coach_task_type_bank_v1_2_0.json`) and leave v1.2.0/v1.1.0 untouched.

An audit of v1.2.0/v1.1.0 found eight concrete problems. Fix all eight.

---

## Problem 1 — CEFR/difficulty estimates are unreliable, not just skewed

`cefr_estimate` is derived from a single `hardest_content_word` lookup against `lexical_registry.json`, falling back to a frequency-band heuristic when a word lacks a direct CEFR tag. Spot-checking this against real word difficulty found systematic errors in **both directions**:

Rated too hard (should be 1–2 bands lower): "ease" → C1 (`ease congestion`), "chaos" → C1 (`traffic chaos`), "statistics" → C1 (`trade statistics`), "tremendous" → C1, "heritage" → C1 (appears in 7 separate items, all wrong), "lenient" → C2, "gain"/"brief"/"minor"/"district"/"aid"/"decent" → B2.

Rated too easy (should be 1–2 bands higher): "distinguished" → A2 (`distinguished career`), "discreet" → A2, "atmospheric" → A2, "abatement" → B1, "destabilize" → B1, "terrestrial" → B1, "retrieval" → B1.

The same word always gets the same tag everywhere it's the hardest word in an item (verified — zero internal inconsistencies), which means a single bad lookup entry contaminates every item built on it. This compounds: at C1, just 4 words ("rehabilitation" ×9, "heritage" ×7, "tuition" ×3, "literacy" ×3) account for ~30% of the entire C1 bucket bank-wide. At B2, 6 words ("housing" ×11, "offender" ×8, "budget" ×8, "offence" ×5, "emission" ×3, "establish" ×3) account for ~27%. So a meaningful share of the "advanced" content isn't advanced — it's mislabeled B1/B2 material, and it isn't even lexically diverse when you look past the surface count.

**Fix required:**
1. Re-derive `cefr_estimate` by cross-checking the internal lookup against a real published source (English Vocabulary Profile / EVP CEFR levels is the standard reference; Oxford 3000/5000 is an acceptable fallback where EVP has no entry). Do not rely solely on an internal frequency-band fallback — that's the mechanism that produced the errors above.
2. Explicitly re-check and correct the twenty-odd words named above as worked examples; then re-run the same audit method that surfaced them (see verification checklist) and fix whatever else it turns up.
3. Add a targeted check: any word that is the `hardest_content_word` for **3 or more items within the same difficulty tier** gets manually reviewed before shipping — this exact pattern (a handful of repeated words inflating a tier's apparent size) is what caused the worst errors here, and is cheap to catch mechanically.
4. Known limitation to carry forward honestly (don't try to fully solve this, just don't hide it): CEFR-tagging the "hardest single word" ignores collocation-pairing difficulty — two very common words can still form a non-obvious pairing a learner gets wrong (wrong preposition, wrong verb choice). If a pairing-naturalness signal is available (e.g. co-occurrence frequency in a large native corpus, if the registries expose it), use it as a secondary check; if not, say explicitly in the new scope_note that `cefr_estimate` reflects single-word difficulty only, not collocation-production difficulty, so downstream consumers don't over-trust it.

## Problem 2 — CEFR/difficulty distribution is skewed toward levels the target population doesn't need

Anyone submitting an essay through this pipeline can already produce a full IELTS Task 2 response — realistically B1 minimum, with most candidates targeting B1–C1 (IELTS bands 5.5–8). Current distribution is A1 15.7% / A2 26.4% / B1 21.6% / B2 23.1% / C1 12.0% / C2 1.1% — 42% of the bank is A1/A2, content most real users already know.

**Target distribution after Problem 1's re-tagging is fixed:** roughly A1 ~5% (retained only as deliberate remediation items for genuinely weak submissions, not general rotation), A2 ~15%, B1 ~27%, B2 ~28%, C1 ~20%, C2 ~5%.

**Per-topic floor:** every topic should reach at least 60% B1-or-above share. Three topics are currently far below that and should be prioritized first: `work_and_employment` (38.2% B1+), `media_and_communication` (40.0% B1+), `education` (45.7% B1+). Add B1–C1 items to these three specifically rather than distributing new items evenly across all ten topics.

## Problem 3 — Near-duplicate items evade string-based dedup

Nine pairs exist where a `curated_core` item and a `registry_expansion` item describe the exact same `(headword, collocate, relation_type)` triple but differ only in surface wording (usually singular/plural): `toxic emissions`/`toxic emission`, `exhaust emissions`/`exhaust emission`, `pollution levels`/`pollution level`, `establish a network`/`establish network`, `complete (one's) education`/`complete education`, `obtain a qualification`/`obtain qualification`, `train teachers`/`train teacher`, `improve (one's) health`/`improve health`, `treatment options`/`treatment option`. These pass exact-phrase dedup but are pedagogically the same item, wasting two selection slots.

**Fix required:** dedup on the tuple `(headword.lower(), collocate.lower(), relation_type)`, not on the raw `phrase` string. Where a duplicate pair exists, keep one item — prefer the `curated_core` wording (it's the naturalized one) over the `registry_expansion` wording, unless the `registry_expansion` phrasing is demonstrably more natural for that specific pair. **If Problem 9 (subtopics) ships in this same pass, this dedup key must be applied across the whole topic, not just within one subtopic** — the same `(headword, collocate, relation_type)` triple must not appear in two different subtopics of the same topic. See Problem 9's single-assignment rule.

## Problem 4 — `media_and_communication`'s type balance is an outlier

Every other topic runs roughly 55–66% collocation / 27–33% noun_phrase / 4–14% phrasal_verb. `media_and_communication` is 82% collocation / 15% noun_phrase / 4% phrasal_verb — noticeably collocation-heavy and thin on the other two types relative to the rest of the bank.

**Fix required:** when adding new items to this topic (which will happen anyway per Problem 2's B1+ expansion), weight the additions toward `noun_phrase` and `phrasal_verb` until this topic's mix is within the same range as the other nine.

## Problem 5 — The promised addendum doesn't exist

`scope_note` says "see addendum" twice — once for the `registry_source` methodology, once for the CEFR-derivation methodology and its limitations — but no addendum exists anywhere in the file.

**Fix required:** either produce the addendum as a real, separate section or file (recommended: a short `vocab_coach_topic_bank_v1_3_0_METHODOLOGY.md` covering exactly how `registry_source` is populated, exactly how `cefr_estimate`/`difficulty_tier` are now derived post-Problem-1-fix, and the known collocation-pairing-difficulty limitation from Problem 1 item 4), or remove the "see addendum" references if no addendum will ship. Don't ship a third version that still promises documentation it doesn't deliver.

## Problem 6 — `confidence` isn't a real per-item signal

`confidence` currently takes exactly 3 values (0.82 / 0.85 / 0.92) that map 1:1 to `relation_type`, not to anything about the specific item. It reads like a per-item quality score but isn't one — there's no way to tell a good extraction from an awkward one within the same relation_type.

**Fix required:** either compute a genuine per-item confidence (e.g. from the source registry's own per-row confidence/frequency field, if one exists, or from a naturalness check against a reference corpus), or rename the field / document it plainly as a pattern-type constant so downstream code doesn't treat it as item-level signal it doesn't actually carry.

## Problem 7 — Task-type bank is missing a whole rhetorical function

The bank covers opinion, cause_effect_problem_solution, and advantages_disadvantages, but not **discussion / "discuss both views."** That's a genuinely different rhetorical job from straight opinion — it needs balanced-both-sides language ("some people argue that... whereas others believe...", "there are valid points on both sides", "while it is true that... it can equally be argued that...") that agree/disagree phrasing doesn't cover.

**Fix required:** add a fourth `task_types` entry, `discussion`, with the same item-type spread (`fixed_phrase`/`verb`/`collocation`) and level spread (basic/intermediate/advanced) as the existing three, sized similarly (~15–20 items).

## Problem 8 — Task-type bank's balance and grounding are both weaker than the topic bank's

- Type mix is fixed_phrase-heavy (55.4%) with collocation thin (12.5%, only 7 items total across all 3 task types). Add more collocations, especially for cause_effect_problem_solution and advantages_disadvantages.
- Level mix is advanced-heavy (41.1%) with intermediate thinnest (25.0%) — but intermediate (~B1–B2) is where the largest share of real candidates sits. Add intermediate items preferentially.
- Only 26.8% of items have any `registry_check` confirmation (73.2% are `not_in_registry_curated`), versus the topic bank's ~100% `registry_source` coverage after its own fix. Push new items — and re-check existing ones — against the same registries the topic bank cites (`preposition_governance_registry.tsv`, `positive_collocations_registry.tsv`, `discourse_registry.json`) before falling back to hand-authored/uncited phrasing.

## Problem 9 — Task-type items need an `angle` tag; some current items don't fit any single-idea paragraph at all

PEEL micro-tasks are one paragraph, one idea (see the constraint stated in Background). `advantages_disadvantages` and `cause_effect_problem_solution` each actually bundle **two different single-idea sub-jobs**, and today's flat item list doesn't distinguish which item belongs to which — so a task scoped to "describe one cause" could randomly draw solution-language, or a task scoped to "describe one advantage" could draw a phrase that only makes sense once both sides are already on the table.

**Fix required:**

1. Add an `angle` field to every item in `advantages_disadvantages` and `cause_effect_problem_solution` (and to `discussion` once Problem 7 adds it): `advantage` / `disadvantage` for the former, `cause` / `effect` / `problem` / `solution` for the latter, `side_a` / `side_b` for discussion. Selection must only draw items matching the single angle assigned to that specific micro-task instance.
2. **Some existing items are inherently two-sided/comparative and cannot be assigned any single angle** — they presuppose both sides already being on the page, which never happens in a one-paragraph, one-idea task. In the current `advantages_disadvantages` list, remove from single-idea target-lexis selection (or move to a separate essay-level "balance/comparison" bank used only for whole-essay contexts, not PEEL micro-tasks):
   - `"outweigh"` and `"outweighs the disadvantages"` — both inherently compare two things (X outweighs Y); there's no single-sided use.
   - `"on the other hand"` — a pure contrast connector that presupposes a previously stated side; nothing to contrast against in an isolated one-idea paragraph.
   - Flag `"a further benefit is that"` and `"a further downside worth noting is"` as borderline — the word "further" implies a prior point already made elsewhere in the essay, which is a mismatch for a standalone single-idea micro-task even though it's not strictly two-sided. Either drop the "further" framing for the PEEL version of these items or tag them for essay-level use only.
3. In `cause_effect_problem_solution`, tagging by angle also surfaces a real imbalance that's currently invisible: 15 of the 19 items are cause/effect-language (`this causes`, `result in`, `contribute to`, `stem from`, `account for`, etc.) and only 4 are solution-language (`one viable measure would be to`, `this issue could be mitigated by`, `a more effective approach would be to`, `unless action is taken to address this`). A "describe one problem and its solution" micro-task needs roughly as much solution-side vocabulary as cause-side — add more `solution`-angle items so the two angles are reasonably balanced, not 4-vs-15.
4. Apply the same angle-tagging discipline to the new `discussion` task type from Problem 7 from the start — write its items as `side_a`/`side_b` framings for a single stance ("some people argue that...", "it could be said that...") rather than dual-clause "while X, Y" connectors that require both sides in one sentence, since those don't fit a single-idea paragraph either.

## Problem 10 — Topics are too heterogeneous; introduce subtopics (selectively)

A topic like `environment` bundles several materially different IELTS sub-themes into one flat pool (climate change, pollution, energy/resources, waste and recycling, conservation/wildlife). A student writing specifically about, say, deforestation currently has a high chance of being served vocabulary from an unrelated sub-theme (e.g. "toxic emissions") as their target lexis — which they have no natural occasion to use in that essay. That directly works against the core PEEL design principle (teach language the student can actually deploy in what they're writing, not topic-adjacent vocabulary they'll never touch).

**Fix required — but scoped deliberately, not a uniform 10-way fan-out:**

1. **Roll out selectively.** Identify which of the ten topics are genuinely heterogeneous (multiple materially distinct sub-themes commonly tested as separate IELTS Task 2 prompts) versus already fairly narrow. Strong first candidates: `environment` (climate_change, pollution, energy_and_resources, waste_and_recycling, conservation_and_wildlife), `technology` (e.g. internet_and_social_media, automation_and_ai, digital_privacy_and_security, technology_in_education), `health` (e.g. healthcare_systems_and_access, lifestyle_and_prevention, mental_health, medical_advances), `education` (e.g. access_and_equality, teaching_methods_and_technology, funding_and_policy, skills_and_curriculum). Topics like `tourism_and_travel` or `urbanization_and_housing` may not need splitting at all — don't force it where the topic is already reasonably homogeneous.
2. **Structure:** `topics.<topic>.subtopics.<subtopic_name>.items[]`, each subtopic carrying the same item schema as today. Keep a topic-level `subtopics_index` or similar listing the subtopic names and one-line rhetorical/thematic description, so a classifier (or a human) can map an essay to the right one.
3. **Size per subtopic:** ~15–25 items, not a fraction of the old 55–72-item topic target — the goal is depth proportionate to what a subtopic actually needs, not mechanically dividing the old topic count by the number of subtopics. Apply Problem 2's B1+ floor (≥60%) at the subtopic level too, or the "some buckets are thin" problem just relocates one level down.
4. **Classification confidence gating.** Subtopic classification is a finer-grained, more error-prone call than topic classification. Whatever emits the subtopic tag (per the existing architecture decision, this should be the Evaluator, not a separate classifier Vocabulary Coach builds itself) must carry a confidence score. Below a confidence threshold, selection must fall back to the topic-level union of all its subtopics rather than committing to a specific subtopic guess — a confident wrong topic-level match is fine; a confident wrong subtopic-level match actively serves irrelevant vocabulary.
5. **Cross-cutting vocabulary — single-assignment rule.** Some items are genuinely topic-wide rather than subtopic-specific (e.g. "environmental policy" is relevant to both climate_change and conservation_and_wildlife). Do **not** duplicate such an item across subtopics. Instead: (a) assign every item to exactly one best-fit subtopic as the default, and (b) maintain one small explicit `general` bucket per topic (not a subtopic in the classification sense, just a shared pool) for items that are genuinely too broad to assign to a single subtopic. This keeps the item→home mapping one-to-one and prevents duplication from creeping back in through a new door.
6. **Dedup still applies across the whole topic.** See Problem 3's updated fix — the `(headword, collocate, relation_type)` key must be deduplicated across all of a topic's subtopics plus its `general` bucket combined, not just within one subtopic.

---

## Forward-looking note (Phase 2 — out of scope for this revision pass)

Once this revision ships (subtopics + angle tags in place), the next phase is a separate **prompt bank**: curated PEEL micro-task prompts, each tagged with `topic`, one *or more* `subtopics` (a prompt may legitimately span two subtopics — e.g. a prompt combining renewable energy investment with public transport expansion spans `energy_and_resources` and `urbanization_and_housing`), `task_type`, and `angle`. Unlike vocabulary items (single-assigned to one subtopic each, per Problem 10), a prompt's subtopic tag is many-to-many — selection for a given prompt should draw its candidate vocabulary from the **union** of all subtopics the prompt is tagged with, not from just one. Do not start this until the current revision (Problems 1–10) ships, since the prompt bank's tags depend on subtopics and angles existing first — building it now would mean re-tagging it once this pass lands.

---

## Verification checklist (run before calling this done)

1. Re-run the word→CEFR consistency check (same word, same tag, everywhere it's the `hardest_content_word`) — should still pass, but also confirm the corrected tags for the ~20 named words above are actually different from v1.2.0.
2. Recompute per-topic B1+ share; confirm all ten topics are ≥60%, with `work_and_employment`, `media_and_communication`, and `education` no longer the worst three by a wide margin.
3. Recompute the "words repeated ≥3× within one difficulty tier" check; confirm no single word accounts for more than ~2 items within any tier's total, or if it does, confirm it was manually reviewed and is actually correct.
4. Re-run dedup keyed on `(headword, collocate, relation_type)`; confirm 0 collisions remain.
5. Recompute `media_and_communication`'s type mix; confirm it's within the same range as the other nine topics.
6. Confirm the addendum (or its removal) actually shipped — no dangling "see addendum" references.
7. Confirm every item still has 100% field coverage (`registry_source`, `cefr_estimate`, `difficulty_tier`, etc. — no regressions from v1.2.0's clean baseline).
8. Confirm the task-type bank now has 4 task types, and recompute its type/level/registry-grounding balance against the targets in Problems 7–8.
9. Confirm every item in `advantages_disadvantages`, `cause_effect_problem_solution`, and `discussion` carries an `angle` tag, that the two flagged comparative items (`outweigh`, `outweighs the disadvantages`) and the pure contrast connector (`on the other hand`) are no longer selectable as single-idea target lexis, and that cause/effect vs. problem/solution angle counts within `cause_effect_problem_solution` are reasonably balanced (not 15-vs-4 as in v1.1.0).
10. If Problem 10 (subtopics) shipped: confirm every item belongs to exactly one subtopic or the topic's `general` bucket (no duplication across subtopics), and confirm subtopic-level B1+ share and item counts meet the targets set there.
11. Confirm both new files are new versioned files, byte-identical originals (`v1_2_0`/`v1_1_0`) untouched.
