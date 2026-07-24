# Vocabulary Coach Engine — Build Prompt v1

Use this as a standalone instruction set (paste into whatever process — LLM call, offline script, or a fresh assistant session — builds this). It does not assume any other context, but references real, existing files by exact name; read them before writing code rather than guessing their shape.

## Background

ST.ELLA's Vocabulary Coach consists of two internal halves, not two separate systems: **LRET** (diagnostic — classifies lexical units the student actually used in a submitted essay into FIX/CLARIFY/ENHANCE/KEEP) and **PEEL** (productive retrieval practice — short one-paragraph, one-idea micro-tasks that drill target vocabulary and retest retention over time). LRET already exists and runs as a pipeline stage on every essay (`lret_engine_v1_12_1_meaning_sensitive_detector_families.py`, wired into `gold_engine_commands_full_v1_4_15.json`'s `lret_session` command). **PEEL does not exist in any form yet** — no Python engine, no ledger, no frontend submission flow. This prompt builds it.

Vocabulary Coach is a standalone, third system alongside Writing Coach and Practice Session — none of the three is nested inside another, and none shares backend state or grading logic with the others. Reuse only generic frontend *component shape* (a mission-card-style layout: prompt, textarea, submit, feedback) if convenient — never Writing Coach's actual API route, engine, or ledger.

### Resources already built and verified, ready to consume

- `vocab_coach_topic_bank_v1_3_0.json` — 789 lexical items across 10 topics. 4 topics (`environment`, `technology`, `health`, `education`) are split into subtopics (`topics.<topic>.subtopics.<name>.items[]` + a `subtopics_index` + a `general` cross-cutting bucket); the other 6 are flat (`topics.<topic>.items[]`). Every item has `cefr_estimate` (A1–C2).
- `vocab_coach_task_type_bank_v1_2_0.json` — 87 items across 4 task types (`opinion`, `cause_effect_problem_solution`, `advantages_disadvantages`, `discussion`). Items in the latter three carry an `angle` field (`cause`/`effect`/`problem`/`solution`, `advantage`/`disadvantage`, `side_a`/`side_b`). A separate `essay_level_only` bucket holds items excluded from single-idea selection.
- `vocab_coach_prompt_bank_v1_0_0.json` — 228 curated PEEL prompts. Each entry has `topic`, `subtopics` (1 or 2), `task_type`, `angle` (or none for `opinion`), a ready-to-render `instruction_template` (already bakes in "ONE cause"/"ONE advantage"/etc. — no further templating needed beyond substituting `{target_items}`), and its own pre-curated `suggested_vocabulary` (4–6 real items, already verified against the two banks above — do not re-derive or re-select vocabulary from scratch, use what's attached to the chosen prompt).

### Existing frontend (read before touching)

`app/vocabulary-coach/page.tsx` and `components/VocabularyCoachView.tsx` already exist — they render LRET's FIX/ENHANCE/CLARIFY/KEEP output only, per-essay. This build **adds** the PEEL practice flow alongside this existing view; it does not replace it. Read both files first.

### A course-correction from earlier design (important — do not build the old version)

An earlier design assumed the Evaluator would emit a topic/subtopic/task-type tag per essay, and PEEL would select vocabulary to match whatever the student's most recent essay was about. **This is rejected.** It would make Vocabulary Coach only ever reflect the single most recent essay's topic — no breadth, no rotation, and it would break the moment a student goes a while between essays. Vocabulary Coach's selection must be **independent of any single essay's classification** — see Architecture below.

## Architecture decisions (confirmed, build to these)

1. **Selection is Vocabulary Coach's own responsibility, not essay-driven — but LRET's accumulated diagnostic history still drives *what* gets prioritized.** These are two different axes, don't conflate them:
   - **Which topic/subtopic/task_type to serve (rotation/coverage):** not essay-driven, no dependency on the Evaluator emitting anything new, not based on a single last essay. Selection reads its own ledger's exposure history and rotates for breadth, per point 1a below.
   - **Which vocabulary/skill families to prioritize within that rotation (the actual diagnostic-coaching part):** this is where LRET matters, and it was missing from the first draft of this prompt. LRET has already classified real lexical units from the student's *own* essays into FIX/CLARIFY/ENHANCE/KEEP, accumulated across every essay they've submitted — that's exactly the longitudinal signal (not a last-essay snapshot) that should bias which specific items PEEL drills. A student whose LRET history clusters around collocation errors in argument-related language should get PEEL sessions weighted toward collocation practice in whatever topic comes up in rotation, not vocabulary picked blind. See component 1's expanded logic below — this is a required part of the build, not a nice-to-have.
1a. Selection also reads the student's persisted score history for a CEFR/band-appropriate difficulty gate. Read `score_contract_builder_standalone.py`'s actual output schema directly to find the right field (likely `lexical_resource` criterion band, with `overall_band` as fallback) rather than guessing the exact key name.
2. **Cadence: daily/bi-daily practice rhythm, not per-essay-triggered, not unlimited on-demand.** A new session is generated lazily (when the student visits/requests it), but gated by a cooldown (config default: 24 hours since the last *completed* session; make it a config value, not a hardcoded constant, so it can be tuned to bi-daily later). If the cooldown hasn't elapsed, the API returns the last completed session's result plus a "next session available at `<timestamp>`" state instead of generating a new one.
3. **Full stack, this pass.** Backend engine(s) + new frontend page/section + new API routes, together.
4. **Full Leitner-box, multiple tiers, for retention.** Per-item state machine: `new → box_1 → box_2 → box_3 → mastered`, with demotion back to `box_1` on a failed retest. Suggested spacing (tune as needed, but keep it day-based to match the daily cadence): `box_1` retested at the next available session (~1 day later), `box_2` at ~3 sessions/days later, `box_3` at ~7 sessions/days later, `mastered` after a successful `box_3` retest (no longer served as new, but not deleted from history).

## Components to build

All new Python engine files — **never edit an existing engine file in place; write a new version-numbered file, leave the original byte-for-byte untouched** (standing project rule). All new files are standalone (no importing from other engine files, matching the "standalone, no previous imports" convention used throughout this pipeline) — mirror the *pattern* of existing engines (e.g. `det_vip_v18d_3_topic_alignment_risk.py`'s `llm_json()`/`CHEAP_MODEL` call style) where relevant, don't import from them directly.

### 1. `vocab_coach_selection_engine_v1_0.py`

Inputs (CLI args): `--ledger <path>` (the student's Vocabulary Coach ledger, may not exist yet on first run — handle gracefully), `--topic-bank vocab_coach_topic_bank_v1_3_0.json`, `--task-type-bank vocab_coach_task_type_bank_v1_2_0.json`, `--prompt-bank vocab_coach_prompt_bank_v1_0_0.json`, `--score-contract <path>` (for level gating, optional — fail-safe default to a mid-level band if absent), `--lret-sessions <one or more paths>` (every past LRET session artifact for this student, oldest to newest — the frontend/API layer already knows how to enumerate a student's past submissions and session directories, same mechanism `app/vocabulary-coach/page.tsx` already uses via `submissionsFor()`/`loadLretSession()`; pass the full list in), `--student-id`, `--output`.

Logic:
- Load the ledger (or initialize an empty one if this is the student's first session).
- Check cooldown: if `now < ledger.next_session_available_at`, output a "not yet available" result (include the timestamp) and exit cleanly — do not generate a new session.
- Determine due review items: scan the ledger's per-item ledger for anything at `box_1`/`box_2`/`box_3` whose scheduled retest date has arrived.
- **Aggregate the student's LRET history** (this is the part that was missing from the first draft — required, not optional): across every `--lret-sessions` artifact passed in, tally FIX/CLARIFY/ENHANCE flags by rubric/family/skill domain (read LRET's actual output schema to find the right grouping field — likely `family`/`rubric` on each flagged unit, the same fields `detector_to_errormap_v3_1_standalone.py` already groups by). This produces a per-student "what does this student's real writing keep getting wrong or could upgrade" profile, distinct from and complementary to the topic/subtopic rotation below.
- Determine the topic/subtopic/task_type for **new** items this session: use the ledger's exposure-count history to prefer the least-recently/least-frequently covered `(topic-or-subtopic, task_type)` combination for breadth — this stays rotation-based, not last-essay-driven, per Architecture point 1.
- **Within that rotated topic/subtopic, let the LRET-aggregated family profile bias which specific prompt/vocabulary gets chosen** — when multiple candidate prompts exist for the rotated topic/subtopic/task_type (the prompt bank has 2–4 per combination), prefer the one whose `suggested_vocabulary` best matches the family/skill domain LRET has most frequently flagged for this student (e.g. if `COLLOCATION` or `LEXICAL_PRECISION`-family flags dominate their LRET history, prefer prompts whose topic-bank items are collocations over noun_phrases for this session). If no meaningful bias exists yet (e.g. a brand-new student with little or no LRET history), fall back to picking at random/round-robin among the candidates — don't force a spurious bias from noise.
- Use the student's level (from `--score-contract`) to prefer prompts whose `suggested_vocabulary` items' `cefr_estimate` values are appropriate (roughly at-or-one-band-above the student's current level — i+1) — if the score contract is missing or this is the student's first session, default to a mid-range band (B1/B2) rather than guessing high or low.
- Select one prompt from the prompt bank matching the chosen topic/subtopic/task_type/angle (rotate angle choice too, don't always pick the same one for a given task_type).
- If any review items are due, include them explicitly in the output (their original `suggested_vocabulary` phrase, its ledger state, and which specific item needs retesting) alongside the new prompt's own vocabulary — per the earlier V4 spec's per-session shape (roughly 3–4 new items + 1–2 review items), but let due-review-count drive the actual number naturally rather than forcing an exact count every session.
- Output: a `vocab_coach_session` artifact — the rendered prompt (topic, subtopics, task_type, angle, scenario_text, final instruction with `{target_items}` substituted from the prompt's own `suggested_vocabulary`), the review items (if any) with a note on how to weave a retest into the same paragraph or as a lightweight secondary check, a `session_id`, the LRET-family bias actually applied this session (for auditability — so it's possible to check later whether the bias logic is doing anything sensible), and the ledger state needed downstream.

### 2. `vocab_coach_response_grader_v1_0.py`

Inputs: `--session <path>` (the artifact from step 1), `--response <path or inline text>` (the student's submitted paragraph), `--output`.

Logic:
- **Scope boundary: lexical precision/accuracy only, not general grammar.** Writing Coach already owns step-by-step grammar competence — do not build a second grammar checker here. Grammar that's intrinsic to the target item itself (e.g. a wrong preposition inside a target collocation) is in scope; general sentence grammar elsewhere in the paragraph is not.
- **Meaning must matter — evaluation cannot be mechanical string-matching.** Do not just regex-check whether the target phrase's literal string appears in the response; that's gameable (a student could paste the phrase in an unrelated sentence) and doesn't test real command of the item. Implement a small, standalone LLM-based semantic check per target item: does the response use this item with its correct meaning, in a sensible context, not just as a dropped-in string? Mirror the `llm_json(prompt, system, model, tag, tracker, enabled, max_tokens)` call pattern already established in `det_vip_v18d_3_topic_alignment_risk.py` (same `CHEAP_MODEL` default, same fail-safe-on-missing/timeout behavior — never crash or silently pass an item just because the LLM call failed; default to "needs review" rather than false-positive "correct").
- Per-item verdict: `used_correctly` / `used_but_awkward` / `attempted_incorrectly` / `not_used`. One paragraph-level note (not a full rubric — that's Writing Coach's job) on whether the paragraph stayed within the assigned one-idea/one-angle scope.
- Output: a grading result artifact with per-item verdicts + the paragraph note. Do not update the ledger here — pass this to step 3, keep the concerns separated (grading judges; the ledger tracks scheduling state).

### 3. `vocab_coach_ledger_update_v1_0.py`

Inputs: `--session <path>`, `--grading <path>` (output of step 2), `--ledger <path>` (may not exist yet), `--output` (the updated ledger, written back to the same student-specific path).

Logic:
- For each **new** item this session: add it to the ledger at `box_1` if `used_correctly`, or keep it flagged for reteaching (still `new`, not yet promoted) if not.
- For each **review** item this session: promote (`box_1→box_2→box_3→mastered`) on `used_correctly`, demote back to `box_1` on anything else.
- Update topic/subtopic/task_type exposure counts (for the next session's rotation decision).
- Set `next_session_available_at` to `now + cooldown` (the config value from Architecture point 2).
- Ledger file path convention, matching the existing project pattern (`{student_id}_gold_progress_profile.json`, `{student_id}_writing_coach_state.json`): **`{learner_profiles_dir}/{student_id}_vocab_coach_ledger.json`**.

### 4. New pipeline wiring

- Do **not** add these as stages in the automatic per-essay `STAGE_ORDER` — Vocabulary Coach is decoupled from essay submission (Architecture point 2), so it must not run as part of the main per-essay orchestrator sequence the way `lret_session`/`writing_coach`/`evaluator` do.
- Instead, add them as new named commands in a new `gold_engine_commands_full_v1_4_16.json` (new versioned file, `v1_4_15` untouched), the same way `mission_response_grading` already exists as an independently-invokable command without being part of the fixed per-essay chain. Name them `vocab_coach_selection`, `vocab_coach_grading`, `vocab_coach_ledger_update`.
- Update `lib/server/goldPipeline.ts`'s `ENGINE_CONFIG` constant to point at `v1_4_16` (frontend file, edited in place per the established convention — Python engines are never edited in place, frontend config pointers are).
- New versioned copy of `gold_lie_profile_builder_standalone_v1_4_3.py` → `gold_lie_profile_builder_standalone_v1_4_4.py`, adding an optional `--vocabulary-coach <path>` input (the ledger or latest session artifact) so Vocabulary Coach's progress genuinely surfaces in the learner profile / progress page, per the existing data contract. Wire the new flag into the new `v1_4_16` command config's `learner_profile` entry.
- **This is the other required half of the LRET↔PEEL loop, not optional:** feed the ledger's taught-item list back so a future LRET run can check whether an item PEEL already drilled shows up correctly in a *real* essay later — the strongest signal of genuine transfer, not just drilled recall. Concretely: `lret_engine_v1_12_1_meaning_sensitive_detector_families.py` needs a new versioned copy (`lret_engine_v1_13_0...py`) that accepts an optional `--vocabulary-coach-ledger <path>` input and, when a KEEP-classified unit in a new essay matches an item the ledger shows was recently drilled via PEEL, annotates it as `confirmed_transfer_from_peel` rather than just a generic KEEP — this is genuinely useful signal for the progress page and for deciding whether an item is truly `mastered` versus just successfully recalled once in a drill. If this specific LRET-side annotation proves too large for this pass, it's acceptable to defer *only this one sub-piece* — but the ledger must still export the taught-item list in a shape ready for LRET to consume later, and the addendum must say explicitly whether the LRET-side annotation was actually built or left for a follow-up.

### 5. Frontend

- New API routes: `app/api/vocabulary-coach/session/route.ts` (GET — calls `vocab_coach_selection_engine_v1_0.py` via the same spawn pattern `goldPipeline.ts` already uses for other engines; returns the session or the cooldown "come back later" state) and `app/api/vocabulary-coach/submit/route.ts` (POST — takes the student's paragraph, calls the grader then the ledger-update engine in sequence, returns the feedback).
- New component, e.g. `components/VocabCoachPeelSession.tsx` — prompt display (scenario + instruction + target vocabulary shown clearly, not buried), a textarea for the one-paragraph response, submit, and a feedback view (per-item verdicts, paragraph note, updated ledger/streak indicator). Reuse the mission-card *shape* from Writing Coach's UI if convenient (visual layout only).
- Add this as a new section/tab on the existing `app/vocabulary-coach/page.tsx` / `VocabularyCoachView.tsx`, alongside (not replacing) the existing LRET FIX/ENHANCE/CLARIFY/KEEP display. Read both existing files first so the new section matches the page's existing visual language rather than looking bolted on.

## Requirements (discipline, consistent with the rest of this project)

1. File-versioning: every new Python engine file listed above is a brand-new file; nothing existing is edited in place. Frontend TypeScript files (`goldPipeline.ts`, the vocabulary-coach page/component, new API routes) are edited/created in place as normal.
2. Model tier: default every LLM call to `CHEAP_MODEL` (matching the rest of this pipeline's cost-conscious convention), gated behind a `--use-llm` style flag with a documented fail-safe default (never silently award full credit when a call fails or is disabled).
3. Anti-gaming is not optional: re-read Section 4's "meaning must matter, not mechanical" requirement before implementing the grader — a naive string-match implementation fails this build's whole purpose.
4. Do not modify any existing upstream engine (`det_vip`, any Evaluator version, Scorer, LRET) — Vocabulary Coach only *reads* their existing outputs (persisted profile, score contract) where needed; it does not require changes to any of them.
5. State plainly in the addendum which pieces were actually built vs. deferred (e.g. if the optional PEEL→LRET transfer-check hand-off was skipped) — do not imply something works end-to-end if it doesn't. This project has twice now had addenda make confident, specific, false claims about engine files (once claiming a nonexistent engine broke, once claiming nonexistent engines already existed) — do not repeat that pattern. Every claim about what exists or was tested must be independently verifiable by re-reading the actual files, not just asserted.

## Verification checklist (run before calling this done)

1. Confirm the cooldown logic actually works: simulate two calls to the selection engine within the cooldown window and confirm the second returns the "not yet available" state, not a fresh session.
1a. **Confirm the LRET→PEEL bias actually changes selection, with real evidence.** Construct two synthetic students with different LRET flag-family histories (e.g. one dominated by collocation flags, one by noun-phrase/topic-vocabulary flags) but identical ledgers/rotation state otherwise, and show that the selection engine picks different candidate prompts for each — not the same output regardless of LRET history. If the bias logic is a no-op in practice, that's a build failure, not a minor gap.
2. Confirm the Leitner promotion/demotion logic with a synthetic ledger: an item at `box_2` that's retested successfully moves to `box_3`; one retested unsuccessfully drops back to `box_1`. Show the actual before/after ledger state, not just a description.
3. Confirm rotation actually spreads across topics over multiple simulated sessions (not stuck repeating the same topic) using a synthetic multi-session ledger.
4. Confirm the grader is not a string-matcher: construct a test case where a target phrase's literal string is dropped into an unrelated sentence, and confirm the grader flags it as `attempted_incorrectly` or `used_but_awkward`, not `used_correctly`.
5. Confirm no existing engine file changed (checksum before/after, same discipline used for the two vocabulary banks).
6. Confirm `tsc --noEmit` is clean on the frontend after the new routes/components are added.
7. Confirm the new page section coexists with, and doesn't visually or functionally break, the existing LRET FIX/ENHANCE/CLARIFY/KEEP view.
