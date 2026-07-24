# ST.ELLA Vocabulary Coach — Architecture Spec v4

Status: **spec only, not yet implemented.** Builds on `GOLD_PIPELINE_SPEC_V2.md` (engine responsibility principles) and `GOLD_PIPELINE_SPEC_V3_TASK_RELEVANCE.md` (Evaluator/Detector division of labor). Answers the specific questions raised this session: merge with Writing Coach or not, topic/task-type resource design, selection+evaluation algorithm, LRET integration, whether this is worth building at all, how many new items per session, and a time estimate.

---

## 1. Merge with Writing Coach, or a separate engine?

**Separate engine. Do not fold this into Writing Coach's existing architecture.**

Writing Coach and Vocabulary Coach both deliver short writing micro-tasks and could look similar from the outside (a mission, a text box, feedback) — but they target and evaluate fundamentally different things, and this project has already been burned twice by blurring engine boundaries (V2 Principle #3, and Writing Coach's own hardcoded-pattern-chain problem in Section 4.3 of V2). Concretely:

- **Different target domain.** Writing Coach targets rhetorical/structural moves — topic sentence control, paragraph planning, clause structure — sourced from `micro_writing_move_bank_v1_flat_adapted.json`. Vocabulary Coach targets specific lexical items — named collocations, phrases, function language — sourced from the canonical resource files (`positive_collocations_registry.tsv`, `lexical_registry.json`, etc.) plus LRET's per-student diagnosed gaps. These are different data sources feeding different selection logic; merging them would mean one engine juggling two unrelated "what should today's mission be" algorithms, which is exactly the kind of scaffold-on-scaffold growth V2 Principle #2 flags as a problem before it starts.
- **Different evaluation rubric.** Writing Coach judges sentence/paragraph craft quality (structure, cohesion, register) via its existing LLM judges. Vocabulary Coach needs an evaluator that checks, per target item: was it used, was it grammatically correct, and — the part that doesn't exist anywhere in this pipeline yet — was it semantically appropriate in context. That's a narrower, more mechanical-sounding check on the surface but actually the hardest one to get right (Section 4), and it deserves its own dedicated judge rather than being bolted onto Writing Coach's existing judges as one more responsibility.
- **Different retention mechanism.** Writing Coach picks "today's mission" from a priority queue; it does not currently re-test the same specific item on a schedule. Vocabulary Coach's whole value proposition is spaced re-testing of specific lexical items (Section 5) — that's new, dedicated infrastructure (a per-student, per-item ledger) that has no equivalent anywhere in Writing Coach today, and grafting it onto Writing Coach's freeze-candidate file would be a second concern living inside a file that's already flagged as needing simplification, not more responsibility.

**Correction from an earlier draft of this section: Practice Session is not the shared layer.** `gold_practice_engine_bridge_v1.py`/`practice_session` selects from a pre-developed, static exercise bank (`va_exercise_bank_v11d_approved.jsonl`) — fixed, pre-authored content, chosen by a selection algorithm but not generated or LLM-graded per submission. That's a different job with a different data source and a different evaluation model than either Writing Coach or Vocabulary Coach, both of which generate a task and grade the specific response with an LLM judge. Folding Vocabulary Coach's generated PEEL micro-tasks into the practice engine's selection pool would mean treating dynamically-generated, LLM-graded items as if they were the same kind of thing as static pre-authored exercises — same category error as merging Vocabulary Coach into Writing Coach, just one layer over. Practice Session, Writing Coach, and Vocabulary Coach are three separate, standalone systems. None is nested inside another.

Vocabulary Coach gets its own delivery surface and its own cadence — not slotted into practice_session, not routed through Writing Coach. It's deliberately not being subsumed into an existing queue, since its value depends on being a dedicated, visible, first-class part of the product, not one more item type competing for a slot in someone else's exercise list. The one thing worth sharing loosely across all three, later, is the priority signal that decides *which* of the three surfaces gets emphasis on a given day (Directive's `next_best_skill`) — but that's a lightweight coordination point, not a shared scheduler or a shared data model.

So the shape is: **PEEL engine + LRET (improved) = the Vocabulary Coach**, a new, separately-versioned engine, coordinated with Writing Coach at the scheduler level only. That matches what you described — "we have both, PEEL engine + LRET in current version" is the right mental model, not "PEEL absorbed into Writing Coach."

---

## 2. Resource design: topic AND task-type, confirmed necessary — and confirmed not yet built

Checked directly against the actual resource files rather than assuming: `positive_collocations_registry.tsv` (the main collocation source LRET already loads) is headword/collocate pairs with `relation_type`, `pattern`, `source` (Oxford Collocations Dictionary), and `confidence` — general-purpose academic collocation data, no topic tagging, no task-type tagging. `lexical_registry.json` (76,334 entries) is the same shape — sourced from AWL/general frequency lists, tagged by register and CEFR-adjacent metadata, not by IELTS topic or rhetorical function. The writing ontology (`writing_competency_ontology_v3.json`) has a genre concept (essay vs. letter vs. report) but nothing at the finer grain of IELTS Task 2 sub-types (opinion / discuss-both-views / problem-solution / advantages-disadvantages / two-part question).

**Conclusion: your instinct is right, and this is real, unbuilt curation work, not a query you can run against existing data.** Two dimensions needed:

1. **Task-type function-language bank** — indexed by the ~5-6 IELTS Task 2 rhetorical types, each with a curated set of phrases that do that rhetorical job (opinion: "it could be argued that," "I am firmly convinced that"; cause-effect: "stems from," "gives rise to," "a direct consequence of"; problem-solution: "one viable measure would be to," "this issue could be mitigated by"). This is genre-function language, independent of subject matter.
2. **Topic content bank** — indexed by the recurring IELTS topic categories (education, environment, technology, crime/justice, health, government/economy, work/employment, media, urbanization, etc.), each with topic-specific collocations and content words ("compulsory education," "carbon footprint," "recidivism rates," "sedentary lifestyle").

A given practice task's target set = task-type phrases (2-3) + topic content items (3-4) for the specific prompt category assigned, cross-checked against the existing collocation registry for frequency/confidence rather than invented from scratch — the registry is a good validation source even though it isn't organized the right way today.

**Build approach recommendation:** don't hand-author 6 x 15 lists from nothing. Use an LLM-assisted first pass (generate candidate phrase sets per task-type and per topic, cross-reference each candidate against `positive_collocations_registry.tsv`/`lexical_registry.json` to confirm it's a real, attested collocation and pull its existing confidence score), then a human spot-check pass on the merged result. This is materially faster than manual curation and still grounded in real lexicographic data rather than LLM invention.

---

## 3. Selection + task generation + evaluation algorithm

1. **Determine focus.** Pull: (a) the student's most recent real essay's topic + task-type (immediate relevance — reinforce what they just struggled with), (b) LRET's persisted diagnostic history across sessions — recurring FIX/CLARIFY patterns for this student (which collocations/phrases they get wrong repeatedly), (c) Directive's `next_best_skill` if it names a lexical-resource-family skill. Weight recent-essay topic highest for the very next session, then rotate topic/task-type coverage over subsequent sessions so the student isn't only ever drilled on one category.
2. **Select target items.** From the two banks (Section 2), pull items matching the chosen topic + task-type. Prefer items LRET has flagged this student misusing or underusing; fill remaining slots with fresh items appropriate to the student's current band (lower band → higher-frequency, more common collocations; higher band → more sophisticated, less common ones — this is where your original band-based quota idea belongs: gating *target selection difficulty*, not post-hoc reclassifying LRET's noisy output).
3. **Item count per task: keep it small.** See Section 6 for the specific number and the reasoning.
4. **Generate the PEEL micro-prompt.** A short, specific scenario/sentence-starter requiring the selected items, targeting a 3-5 sentence PEEL paragraph (Point-Evidence-Explain-Link) — short enough to keep cognitive load on retrieval and usage, not on managing a full essay's structure at the same time.
5. **Student writes.**
6. **Evaluate (Section 4) — per-item outcome: used correctly, used but imprecise/unnatural, attempted incorrectly, omitted.** Also a whole-paragraph naturalness/coherence check, not just item-presence (Section 4).
7. **Persist per-item outcome to a retrieval ledger** (Section 5) and schedule the next re-test.
8. **Independent transfer check:** the next time this student submits a *real* full essay, LRET checks whether previously-taught items reappear and whether they're used correctly there too — this is the actual test of durable learning (did it transfer to free writing), not just the controlled micro-task. This requires LRET's `recurs_across_essays`-style field to actually be checked against the Vocabulary Coach's taught-item ledger, which is new wiring (today the two systems don't talk to each other).

---

## 4. Evaluation: meaning must matter, not mechanical presence-matching

This is the part most likely to be under-built if rushed, so it gets its own section. A naive evaluator that just checks "does the target phrase appear as a substring" is gameable and useless — a student could staple every target phrase into a single ungrammatical, meaningless run-on and pass. Three checks are needed per submission, in this order:

1. **Presence + grammatical well-formedness.** Is the item there, in a correct grammatical form (right preposition, right verb form, correct pluralization)? Mechanical, can be partly rule-based, partly LLM.
2. **Semantic appropriateness in context — the real bar.** Does the surrounding sentence actually mean what the collocation is supposed to mean, and does the item fit the sentence's actual claim? E.g., a student who writes "crime rates have arisen due to unemployment" gets marked wrong on both a grammar level ("risen," not "arisen") and — separately, and more importantly — the evaluator must confirm the *reason given* genuinely supports the claim, not just that the collocation-shaped string is present. This needs an LLM judge prompted specifically to check meaning-fit, not just surface form — the same category of judgment Writing Coach's existing register-judge/upgrade-generator calls already make elsewhere in this pipeline, so there's a working pattern to follow, not a cold start.
3. **Naturalness / anti-gaming check at the paragraph level.** Flag stapled, unnatural, or list-like use of target phrases with no real connective logic between them ("I believe that, in my view, additionally, moreover, education should be free" is not a sentence). This should reuse the Evaluator's existing argument-map machinery (claim/reason/support extraction, already built) — a paragraph where target phrases don't sit inside a genuine claim-reason-support structure should be scored as mechanical use even if every individual item is technically correct.

None of this is a bigger-model problem in the sense Section 3 of V2 discusses for LRET — it's a prompt-design and rubric-design problem, and it needs real essays (or real micro-task submissions) to calibrate against before trusting it, same as every other LLM judge in this pipeline.

---

## 5. Retention mechanism (the actual point of building this)

Per-student, per-item ledger: `{item, first_taught_session, last_seen_session, last_outcome, current_interval, next_due_session}`. Suggested schedule, Leitner-box style:

- First correct-and-confident use → review again in ~3 sessions, with a *harder* cue (give only the base word, student must reconstruct the collocation) rather than the same cue type.
- Imprecise/unnatural use → review again in ~2 sessions, same cue difficulty.
- Incorrect or omitted → review again next session, with an *easier* cue (fill-in-the-blank, phrase given, student places it correctly in an original sentence).
- Two consecutive correct-and-confident uses at the harder cue level → mark item "acquired," drop from active rotation but keep on a long-interval spot-check (e.g., every ~10 sessions) to catch decay.

This ledger is new persistent state — nothing like it exists in `learner_profiles_dir` today. It should live alongside the existing persisted profile, keyed by student ID, same directory convention as everything else.

---

## 6. How many new items per session?

Keep it small — **3-4 new items per micro-task, plus 1-2 due-for-review items pulled from the ledger, so total working set per session is around 5-6 items.** This isn't arbitrary: productive vocabulary mastery (correctly generating a collocation in original context, not just recognizing it) is much more cognitively demanding than receptive recognition, and vocabulary-acquisition research consistently finds small batches outperform large ones for durable productive use — pushing 15-20 new items at once (roughly what a full LRET pass currently surfaces per essay) all but guarantees shallow, non-transferable exposure. This is the same lesson as your "41 keep candidates is too many" observation from the LRET audit — less volume, deeper retention, applied here at the front end (task design) instead of after the fact (output filtering).

---

## 7. LRET and PEEL — the internal division of labor within Vocabulary Coach

Worth restating plainly, since the rest of this document mostly talks about "Vocabulary Coach" as shorthand for the new PEEL half: **Vocabulary Coach is not a new engine that merely consumes LRET — LRET is one of its two constituent engines, PEEL is the other.** They are not separate systems with an integration boundary between them; they are the two halves of one product feature, and this section describes how those two halves divide the work, not how an external engine "integrates with" another external engine.

1. **LRET's half: diagnosis from real essays.** LRET does what it already does — extract and classify lexical units from a student's actual submitted essays (FIX/ENHANCE/CLARIFY/KEEP) — and that diagnostic output is Vocabulary Coach's own internal signal for what PEEL should drill next (Section 3, step 1-2), personalized to this student's real errors rather than a generic curriculum. This is not LRET "feeding" an external consumer; it's the diagnostic half of the same feature handing its findings to the practice half.
2. **PEEL's half: controlled retrieval practice.** PEEL generates the short target-lexis micro-tasks, evaluates them (Section 4), and manages the spaced-retrieval ledger (Section 5) — the productive-practice half, downstream of LRET's diagnosis within the same feature.
3. **The loop closes back through LRET.** After PEEL has taught an item, LRET's next pass on the student's *next real essay* checks whether that item reappears, and correctly — transfer verification (Section 3, step 8). This is PEEL handing a signal back to LRET, the other internal half, not Vocabulary Coach querying an outside system twice.

This still requires LRET's own known bugs to be fixed first — the fragment/duplicate bugs and the canonical-resources-loading regression from the session audit (`GOLD_PIPELINE_SPEC_V5_AUDIT_REMEDIATION.md`, Findings 2-4) feed directly into both directions of this loop: a personalization signal built on duplicated/fragmented FIX data will mis-target PEEL's item selection, and a transfer check run through a degraded LRET won't reliably detect real usage. **Fix LRET's known bugs before building PEEL's dependency on its output**, not in parallel — but that's sequencing within one feature, not a dependency on an external team's roadmap.

---

## 7a. Data contract — what Vocabulary Coach reads, what it feeds

Written in the same shape as `gold_engine_commands_full_v1_4_13.json`'s existing stage definitions, so this can be dropped in as a real stage once built, not just described in prose. Per Section 7, remember LRET is PEEL's internal diagnostic half, not an external system — the table below marks that row accordingly rather than listing it as a plain outside dependency.

### Reads (inputs)

| Source | File / artifact | What Vocabulary Coach (PEEL half) uses it for |
|---|---|---|
| Submission | `00_submission.json` | `essay_text` + `prompt` of the student's most recent real essay — drives topic/task-type selection (Section 3, step 1) |
| Evaluator (external) | `07_evaluator_output.json` | (a) task-type + topic tag — see corrected dependency note below; (b) `lexical_resource` microskill scores (range/precision/collocation naturalness specifically) — a more precise difficulty-tier signal than the blended overall band, since it isolates the LR dimension the way Section 3 step 2 actually needs |
| Score contract (external) | `02d_final_score_contract.json` | overall band, as a fallback difficulty signal when Evaluator's LR-specific score isn't available |
| Directive (external) | `04_directive_v2.json` | `next_best_skill` — if it names a lexical-resource-family skill, weights target selection toward it |
| **LRET session (internal — Vocabulary Coach's own diagnostic half, see Section 7)** | `07d_lret_session.json` | this session's FIX/CLARIFY units — the internal hand-off from Vocabulary Coach's LRET side to its PEEL side, immediate per-item diagnostic signal |
| Persisted learner profile | `08a_gold_persisted_profile.json` | cross-session recurring lexical error patterns (**dependency gap, not yet built** — see note below) |
| Canonical resources (external) | `positive_collocations_registry.tsv`, `lexical_registry.json` | cross-check for real, attested collocations when curating/validating target-lexis banks (Section 2) |
| Vocabulary Coach's own seed banks | new files, e.g. `vocab_coach_task_type_bank.json`, `vocab_coach_topic_bank.json` | the curated task-type/topic target-lexis sets (Section 2, Section 9b day 1) |
| Vocabulary Coach's own ledger | `{learner_profiles_dir}/{student_id}_vocab_coach_ledger.json` | per-item retention state from prior sessions — which items are due, at what cue difficulty (Section 5 / 9b day 2) |

**Dependency gap, corrected from an earlier draft of this section.** No engine today classifies a prompt's IELTS task-type (opinion/discussion/problem-solution/etc.) or topic category (education/environment/etc.) — confirmed in Section 2, nothing in `writing_competency_ontology_v3.json` goes finer than genre (essay/letter/report). An earlier draft of this spec had Vocabulary Coach build its own lightweight classifier to fill the gap — that was the wrong owner. Task-type and topic classification is the same *kind* of job as the `identify_genre` skill Evaluator already owns, just finer-grained, and per `GOLD_PIPELINE_SPEC_V3_TASK_RELEVANCE.md`, Evaluator is now the confirmed owner of task-understanding judgment generally (that's where the Task Response relevance fix lives too). Consolidating both in the same owner avoids a second, redundant classifier living inside Vocabulary Coach.

**Revised plan:** extend Evaluator's task-understanding skill group to emit a `task_type` + `topic_category` tag alongside its existing genre classification, and have Vocabulary Coach read it from `07_evaluator_output.json` rather than computing its own. This is a small, additive change to Evaluator (one more field on an existing skill-evidence pass, not new architecture) and it means Vocabulary Coach never has two independent opinions about what topic an essay is about floating around the pipeline. If this extension can't land before Vocabulary Coach's own build starts, the fallback for v1 only is a temporary local classification inside Vocabulary Coach, explicitly marked as a stopgap to be deleted once Evaluator's version ships — not a permanent second implementation.

### Feeds (outputs)

| Output | File / artifact | Consumed by |
|---|---|---|
| Generated task | new artifact, e.g. `07g_vocabulary_coach_task.json` — target items, filled PEEL prompt, topic/task-type tags, difficulty tier | Frontend mission-card component (Section 9b day 5) |
| Evaluation result | new artifact, e.g. `07h_vocabulary_coach_result.json` — per-item outcome (correct/imprecise/incorrect/omitted), one-line reason each, naturalness flag | Frontend feedback panel; also written back into the ledger (below) |
| Updated ledger | `{learner_profiles_dir}/{student_id}_vocab_coach_ledger.json` | Vocabulary Coach's own next-session selection step (Section 3) — this is a read-modify-write loop back into its own input |
| Taught-item list | appended into `07g`/`07h` or a small dedicated `taught_items` field | **LRET's next real-essay pass — the internal PEEL-to-LRET hand-off within Vocabulary Coach (Section 7, step 3), not a call to an outside engine.** LRET checks whether these specific items reappear, and correctly, in the student's next full essay. LRET does not read this today and needs a new `--vocab-coach-taught-items` input wired in before the transfer-check loop actually closes — a small addition to Vocabulary Coach's own LRET half, same as any other internal wiring gap in this pipeline. |
| Learner profile contribution | new `--vocabulary-coach {vocabulary_coach_result}` input to `gold_lie_profile_builder_standalone_v1_4_3.py` (the `learner_profile` stage), same pattern as its existing `--lret`/`--writing-coach`/`--practice` flags | `08_gold_learner_profile.json` → `08a_gold_persisted_profile.json` → the frontend progress page. This is what makes vocabulary acquisition visible in the same place as grammar/structure progress, and closes part of the Finding-5 "progress tracker" gap from the audit — vocabulary gains should show up there, not just live in Vocabulary Coach's own ledger. |
| Priority signal (later, not v1) | a rollup like "recurring failure on collocation family X after N attempts" | Directive/Priority Engine, so persistent lexical struggles can eventually raise `next_best_skill` the same way Detector/Evaluator signals do today — deferred per Section 9b, not required for the one-week loop to close |

The two genuinely new pieces of cross-engine wiring this creates — `learner_profile` gaining a `--vocabulary-coach` input, and LRET gaining a `--vocab-coach-taught-items` input — are both small, additive changes to existing CLI bridges (new optional flag, not a rewrite), consistent with how every other engine in this pipeline takes its inputs. Neither is required for the Day 5 smoke test in Section 9b to pass; both are needed before the feature's actual value (visible progress, verified transfer) is real rather than just logged internally.

---

## 8. Do students actually need this?

Yes, and worth saying plainly why, since it's a real build investment: the current pipeline's only vocabulary-facing mechanism (LRET) is one-shot, reactive, and has no retention design at all — a student gets a list of suggestions on their essay and nothing re-tests whether any of it stuck. That's a real, currently-unaddressed gap, and it matters specifically for the score this product is sold against: IELTS's own Lexical Resource band descriptors reward "natural and sophisticated control of a wide range of vocabulary" at the top bands and penalize "limited range" lower down — that's a command-of-specific-phrases problem, not a general fluency problem, and command of specific phrases is exactly what spaced productive retrieval builds and one-shot feedback doesn't. This is a stronger direct lever on the score students are paying for than most of the bug-fix work in the V2/V3 specs, which is diagnostic/quality-of-feedback work rather than an acquisition mechanism.

---

## 9a. Scope boundary — lexical precision only, not general grammar

**Vocabulary Coach checks lexical precision/accuracy of the target items, not general sentence grammar.** That's already Writing Coach's and Detector's job, and the user's own framing is the right one: Writing Coach exists to build writing competence step by step (structure, clause control, register), and duplicating grammar-checking inside Vocabulary Coach would re-create exactly the overlap V2 Principle #3 warns against — two engines independently judging the same thing, with no shared source of truth.

The boundary in practice: **grammar that is intrinsic to the target item itself is in scope; grammar that is incidental to the rest of the sentence is not.** If a student writes "crime rates have arisen," the wrong verb form ("arisen" vs. "risen") is inseparable from whether the collocation "rates have risen" was used correctly — that has to be caught, it's not optional, it's the same judgment as "was this item used correctly" (Section 4, tier 1). But if the same sentence has an unrelated article error or a tense slip elsewhere that doesn't touch a target item, Vocabulary Coach should ignore it entirely — not score it, not mention it. If a submission is so grammatically broken elsewhere that the target item's usage genuinely can't be judged, the evaluator should return an explicit "ungradable, sentence unclear" state rather than attempting a full grammar pass it isn't built for.

Practical effect: no full Detector run against these micro-task submissions in v1. That's meaningfully less scope (Detector's error-detection pipeline is a heavier, multi-stage LLM process) and it's the right cut — these are small, disposable practice items, not graded essays, and a focused lexical-precision judge is faster, cheaper, and more precisely targeted than routing every 3-sentence paragraph through the full Detector.

---

## 9b. One-week MVP plan

3-4 weeks was the estimate for the full version with complete topic/task-type coverage, a real spaced-repetition ledger, and scheduler-level blending with Writing Coach. One week means cutting to a genuinely minimal but real version — not a mockup, a working end-to-end loop, deliberately narrow in coverage. What gets deferred is called out explicitly below so the cuts are visible, not silent.

**Day 1 — Target-lexis seed set + prompt template.** Skip full bank-building (that was the 3-5 day item). Instead, hand-pick a small real set: 3 task-types (opinion, cause/problem-solution merged into one, advantages-disadvantages) x 4-5 common topics (education, environment, technology, crime-or-health) — roughly 15 combinations, 6-8 items each, LLM-drafted and cross-checked against `positive_collocations_registry.tsv`/`lexical_registry.json` for real attested collocations rather than invented ones. Design one PEEL micro-prompt template per task-type (a fill-in-the-scenario sentence-starter), not per topic — topic just substitutes into the template. This is a few focused hours, not the multi-day curation project the full version needs — deliberately narrow, expand coverage later.

**Day 2 — Selection algorithm + minimal ledger.** Selection: pull topic from the student's most recent real essay if available, else rotate through the seed set; pick 3-4 new items + 1-2 due-for-review items (Section 6 numbers unchanged — this part isn't where the cut happens); fill the template. Ledger: a single JSON file per student, `{item, last_seen_session, last_outcome, next_due_session}` — skip the full Leitner-box interval design (Section 5's multi-tier schedule); replace with one simple rule: wrong or omitted → due again next session; correct → due again in 3 sessions with the harder cue (base word only, no full phrase given). That's the whole spaced-repetition system for v1 — a real one, just a two-state version instead of the full graded schedule.

**Day 3-4 — The evaluation judge.** This is the piece that can't be cut, since it's the actual point of the feature. One LLM call per submission: given the target items and the student's paragraph, return per-item status (correct / imprecise / incorrect / omitted) with a one-line reason each, folding in a lightweight naturalness check (is this a real sentence or stapled keywords) as part of the same call rather than a separate pass — combine what Section 4 described as three tiers into one well-structured prompt rather than three round-trips, to keep both latency and scope down. Test it before trusting it: hand-write 3-4 sample paragraphs yourself covering the failure modes that matter (one clean correct use, one keyword-stapling attempt, one subtle collocation error like "arisen"/"risen", one that just omits an item) and confirm the judge calls each one correctly before pointing it at real students.

**Day 5 — Wire into delivery + one real end-to-end smoke test.** Reuse only the mission-card *frontend component shape* (prompt, text box, submit, feedback panel) as a piece of UI code — not Writing Coach's backend. The submission must post to a new, dedicated Vocabulary Coach API route, get scored by the Vocabulary Coach's own evaluation judge (Section 4), and write to the Vocabulary Coach's own ledger (Section 9b, day 2) — none of it touches Writing Coach's mission state machine or grading pipeline, since that would quietly re-merge the two engines exactly one layer below where the architectural separation (Section 1) was decided. A `missionType` field on the task record tells the frontend which component variant and endpoint to use; that's a routing detail, not a shared engine. Vocabulary Coach gets its own entry point in the student's daily flow — not a slot inside practice_session, not routed through Writing Coach — same standing as Writing Coach's mission today, just a separate one. Skip any cross-surface priority coordination for v1 (Section 1's "shared priority signal decides emphasis" is a later refinement) — for now, one Vocabulary Coach micro-task is simply always available each session, independent of what Practice Session or Writing Coach are doing that day. Run one full real loop end to end: task generated, student submits, judge scores, ledger updates, next session actually pulls the correct review item back — confirm that loop closes before calling it done.

**Explicitly deferred past week one:** full topic x task-type coverage (Section 2's ~90-combination target), the graded multi-tier spaced-repetition schedule (Section 5), scheduler-level smart blending with Writing Coach and Directive's priority signal (Section 1), LRET transfer-verification wiring (Section 7, step 2 — checking whether taught items reappear correctly in the student's next real essay), and the separate paragraph-level naturalness pass as its own judge call rather than folded into the main one. None of these are abandoned — they're the Week 2+ backlog once the core loop is proven against real submissions.

---

## 10. Time estimate

Rough engineering-judgment estimate for a first working version, phased, assuming solo focused work and reuse of existing patterns (LLM judge calls, `learner_profiles_dir` persistence conventions, practice-engine plumbing) rather than building everything from scratch:

| Phase | Work | Estimate |
|---|---|---|
| 1 | Curate topic x task-type target-lexis banks (LLM-assisted draft + human spot-check against existing registries, Section 2) | 3-5 days |
| 2 | Selection algorithm + PEEL prompt template engine | 2-3 days |
| 3 | Evaluation engine — presence/grammar + semantic-appropriateness + naturalness judge (Section 4), iterated against real submissions | 3-4 days |
| 4 | Spaced-retrieval ledger + scheduling logic (Section 5) | 2-3 days |
| 5 | Integration with the practice-engine scheduler + LRET wiring (Section 7) + minimal frontend delivery | 3-5 days |
| 6 | Testing/calibration against the existing stress-test essays plus a handful of real sessions | 2-3 days |

**Total: roughly 3-4 weeks of focused solo engineering for a first solid version.** This is an estimate, not a commitment — same caveat every other spec in this series has carried: none of this has been run against real API calls or real students yet, and Phase 3 in particular (the meaning-appropriateness judge) is the piece most likely to need extra iteration once you see real, ungraded student output rather than clean test essays.

**Sequencing recommendation:** fix LRET's known bugs first (Section 7 dependency), then Phase 1-2 in parallel with finishing the Task Response relevance fix from V3 (unrelated systems, no reason to serialize them), then Phase 3-6 in order.
