# ST.ELLA Gold Pipeline — Audit Remediation Spec v5

Status: **implemented for Findings 3-6 and the code portion of Finding 2**, as of this pass — details in each section below. Operationalizes `ST.ELLA Gold Pipeline - Session Quality Audit.pdf` (the live-session findings report) into engine-owned fixes, the same way V3 already did for that report's Finding 1 (Finding 1 itself is not yet implemented, still spec-only). Builds on the engine-ownership principles in `GOLD_PIPELINE_SPEC_V2.md` and does not overlap with `GOLD_PIPELINE_SPEC_V4_VOCABULARY_COACH.md`, which is a separate, parallel initiative.

**Implementation summary:**
- Finding 2: the Render dashboard env-var check is still an action item for Ailuna, not code. The code-side hardening recommendation **is implemented** — see Finding 2 below for what changed.
- Findings 3-4 (LRET dedup): **implemented** — `find_covering_task_for_keep()`/`covers_unit()` in `lret_engine_v1_12_0_meaning_sensitive_detector_families.py` now treat a direct token-subsequence or substring relationship between two units as covering evidence independent of `same_sentence()`, since that was the actual point of failure, not the overlap logic itself. Not yet re-tested against a real essay with resources loaded — do that once Finding 2's dashboard check is done.
- Finding 5 (progress page): **implemented** — new `lib/server/progress.ts` reads the real `02f_gold_progress_tracker_persisted.json`/`02e_gold_progress_tracker.json`, `app/progress/page.tsx` now renders a per-skill trend section alongside the existing overall-band chart.
- Finding 6 (Writing Coach/Directive alignment): **implemented** — `loadWritingCoach()` in `goldPipeline.ts` now reads `directive_alignment`, and `app/writing-coach/page.tsx` shows the explicit override note when Writing Coach's selected skill differs from the Directive's.
- Finding 1 (Task Response relevance, per V3): both the primary Evaluator fix (Section 2 of V3) and the defense-in-depth Detector safety-net + Scorer ceiling (Section 3 of V3) **are now implemented** — see V3's own updated status and its new "3.1 Implemented" section. Live-LLM verification (V3 Section 5) is the one remaining step before this is fully shipped.
- Finding 7 (model tier): unchanged, still open, awaiting your go-ahead per V2 Section 1.
- **No further open code-level items remain from this document or from V3** other than Finding 7 (your own model-tier decision) and Finding 2's Render environment-variable action item (your own infra step) — everything else audited in the original session-quality-audit is now implemented in code, pending live-LLM verification.

Two findings turned out to need correction once the actual frontend code was checked directly rather than reasoned about from the backend artifacts alone — Findings 5 and 6 are both frontend plumbing gaps, not backend logic bugs, and that changes what needs to be built for each. Corrected below.

---

## Finding 1 — Task Response relevance (ref only, not repeated here)

Fully speced in `GOLD_PIPELINE_SPEC_V3_TASK_RELEVANCE.md`: owner is Evaluator (`arg_claim_relevance`/`maintain_task_focus`), plus a Detector-level safety-net flag, plus a Scorer ceiling. Sequencing note for this document: this is independent of Findings 2-6 below and can ship in parallel with any of them.

---

## Finding 2 — Canonical resources not loading in production

**Owner: deployment/config, not an engine.** This is not a bug in any engine's logic — `lret_engine_v1_12_0...py`'s `--canonical-resources` flag has no hardcoded fallback, and `gold_engine_commands_full_v1_4_13.json`'s command template is correct. The live run's QA warning (`v168_canonical_resources_silent_load_failure`, path `/app/resources`) means the value the orchestrator resolved for `canonical_resources_dir` at run time was still the pre-fix path — either a stale container or a `STELLA_CANONICAL_RESOURCES_DIR` override sitting directly in Render's dashboard Environment tab, silently taking precedence over the Dockerfile's `ENV`.

**Action, owner Ailuna (Render dashboard), not code:** check the Environment tab for that variable; correct or remove it; redeploy; confirm on the next real run that `lret_session.qa.warnings` is empty and `resource_support.unit_resource_score` is nonzero on at least some units.

**Implemented.** Turned out the orchestrator already had more than a buried warning: `validate_quality_gates()` in `gold_full_pipeline_orchestrator_v1_4_9.py` already appends `canonical_resources_not_loaded_zero_resource_run` to `quality_gate_issues` (added in v1.4.9, per its own comment), which already flips `qa_status` to `"needs_attention"`, which `goldPipeline.ts` already read and used to set `report.escalate_to_human_review = true` — and that flag was already wired into a real banner in `components/ReportView.tsx` and into the trainer queue sort in `app/trainer/page.tsx`. The actual gap was narrower than "silent": the banner that already existed said only "flagged as ambiguous, worth a closer look," with no way to tell this specific case apart from any other QA issue. Fix shipped: `goldPipeline.ts` now reads the QA report's `quality_gate_issues` on the success path too (previously only read on the failure path), and when it recognizes `canonical_resources_not_loaded_zero_resource_run` specifically, sets a new `report.quality_notice` string ("Vocabulary suggestions in this report ran with reduced resource support...") that `ReportView.tsx` now shows in place of the generic message. `lib/types.ts` carries the new optional field. No orchestrator changes were needed — the QA gate was already correct, the signal just needed to survive the trip through to the actual banner text.

---

## Finding 3 & 4 — LRET FIX/KEEP contradiction and fragments

**Owner: LRET (`lret_engine_v1_12_0_meaning_sensitive_detector_families.py`), specifically `find_covering_task_for_keep()`.** The intent is already correct — `build_keep_units()` (line ~1462) explicitly drops a KEEP candidate when `find_covering_task_for_keep()` reports it's covered by a FIX span. The real case ("sound mental" landing in both buckets) means the coverage check failed to recognize the overlap between the two candidates.

**Concrete fix design, not yet implemented:** the coverage check most likely relies on exact text/sentence-index matching rather than genuine span-range overlap. Target implementation: compute a character-offset (or token-index) range for every candidate unit at extraction time, before classification — every unit already has `source_sentence_index`, so this is additive, not a rework of extraction. Then `find_covering_task_for_keep()` should treat any two units whose offset ranges overlap by more than a threshold (e.g., the shorter span's range is >40% contained within the longer span's range) as covering each other, regardless of whether their surface text strings match exactly. This directly targets the fragment-boundary case that caused the miss: "sound mental" (KEEP candidate) and "sound mental health" (the FIX span) share overlapping character ranges even though their strings don't match.

**Implemented.** `find_covering_task_for_keep()`'s exact-duplicate check no longer requires `same_sentence()` to agree for identical normalized text, and `covers_unit()` now checks token-subsequence/substring overlap *before* and independent of the same-sentence gate, falling back to the sentence-gated looser checks (shared-token FIX suppression, content-overlap ratio) only when there's no strong direct text overlap. Both changes are additive — the existing looser signals still require same_sentence() exactly as before, so this only widens what counts as "covering," it doesn't loosen anything that was already working. Syntax-checked clean; not yet re-run against real essay data.

**Sequencing dependency, still applies:** re-test this specifically after Finding 2's dashboard check lands. A working collocation registry may change which candidate windows even get proposed in the first place (i.e., "sound mental health" might get recognized as one coherent 3-word span once the registry is actually loaded, rather than getting fragmented into "sound mental" and "mental health" independently) — some of Finding 4's fragment cases may resolve on their own once resources load on top of this fix.

**Correction, file convention:** this fix lives in a new file, `lret_engine_v1_12_1_meaning_sensitive_detector_families.py` (`ENGINE_VERSION: lret-engine-v1.12.1-keep-fix-coverage-dedup-fix`) — the original `..._v1_12_0_...py` was left untouched. `gold_engine_commands_full_v1_4_14.json` points the `lret_session` stage at the new file; see Finding 1's updated section for the same convention applied to the Evaluator fix, and the shared config-bump note.

---

## Finding 5 — Progress page (corrected: confirmed frontend gap, with the exact code)

**Owner: frontend, `app/progress/page.tsx`.** Checked directly this pass, not just inferred: the page reads *only* `submissionsFor(user.id)` — each submission's lightweight `report.score_summary` (overall band + criteria bands) — to build the "Band trend" chart and "Evaluations" list. It never reads `02e_gold_progress_tracker.json`, `02f_gold_progress_tracker_persisted.json`, or `08a_gold_persisted_profile.json` — the actual per-skill trend data (`stable_for_trend` flags, skill-level score history) confirmed present and well-formed in the audit. With one evaluated essay, this page shows exactly one point on a chart and nothing skill-specific — which is a materially thinner experience than what the backend actually has, and reads as "empty" in the sense the complaint meant, even though the page technically renders something.

**Implemented.** New `lib/server/progress.ts`, same `getX(sessionDir)` pattern as `study-plan.ts`'s `getLearningRoadmap()`: reads `02f_gold_progress_tracker_persisted.json` (falling back to `02e_gold_progress_tracker.json`), exposes `getSkillProgress()` and `perSkillTrend()`. `app/progress/page.tsx` now renders a "Skill trend" card with per-criterion band history alongside the existing overall-band chart, distinguishing `stable_for_trend` events visually from not-yet-stable ones. Real field names confirmed against an actual `02f` artifact on disk (`task_response`/`coherence_cohesion`/`lexical_resource`/`grammar` — deliberately not reusing `lib/types.ts`'s `CRITERION_LABELS`, whose keys, `task_achievement`/`grammatical_range_accuracy`, don't match this artifact and would have silently produced blank labels).

---

## Finding 6 — Writing Coach vs. Directive mismatch (corrected: not a backend gap at all)

**Correction from the original audit finding.** The original framing was "Writing Coach doesn't consume Directive's priority signal." Checked directly this pass: that's wrong. `writing_coach_alignment_guard_standalone_v1_4_7.py`'s `align()` function already does exactly the right thing — it compares the Directive's primary focus against Writing Coach's own selected skill, and when they don't match, it does not silently pick one: it sets `status: "explained_override"`, preserves Writing Coach's own task (`coach_task_preserved: true`), and explicitly writes `effective_focus_for_gold_routing` (the Directive's focus) plus a `teacher_rationale` note stating the mismatch outright ("Gold Directive primary focus is X; Writing Coach selected Y... Override is explicit, not silent."). The backend already computed the honest, correctly-labeled signal this whole finding was asking for.

**The actual gap, confirmed by direct search: nothing in `stella-frontend` reads `directive_alignment` or `effective_focus_for_gold_routing` at all.** Zero matches anywhere in the codebase. The mission page shows Writing Coach's own selected skill with no indication that the Gold Directive wanted something else, and no explanation of why — not because the pipeline doesn't know, but because the frontend was never wired to ask it.

**Implemented.** `loadWritingCoach()` in `goldPipeline.ts` reads the top-level `directive_alignment` object from `07e_writing_coach_output.json` and maps it to a new `directiveAlignment` field on `WritingCoachMission` (status, aligned/not, both focus labels, the guard's own override rationale). `app/writing-coach/page.tsx` renders an amber note above the mission header when `!isAligned`, naming both the Directive's priority and what today's mission actually targets, plus the plain-language reason the guard already generated. No backend work needed — pure plumbing, same shape as Finding 5.

---

## Finding 7 — Model tier (ref only, still open)

Already covered in `GOLD_PIPELINE_SPEC_V2.md` Section 1: both Detector's `VIP_STRONG_MODEL` and Evaluator's `OPENAI_MODEL` still default to `gpt-4o-mini`; V2's recommendation (Terra for Evaluator, Terra for Detector's Stage-7 judge only) still stands and still needs a go-ahead — no change from this pass.

---

## Sequencing / build order

1. **Finding 2 (Render env check)** — five minutes, no code, unblocks honestly re-testing Findings 3-4.
2. **Findings 5 and 6 (frontend plumbing)** — both are read-and-render fixes against data that already exists and is already correctly computed; no backend risk, can ship independently and in parallel with everything else, and are probably the fastest real wins available right now.
3. **Finding 1 (Task Response relevance, per V3)** — independent, can run in parallel with the above.
4. **Findings 3-4 (LRET dedup)** — do this after Finding 2 is confirmed fixed and re-tested, since part of the symptom may already resolve once resources load; don't spend engineering time on `find_covering_task_for_keep()` before confirming what's left over.
5. **Finding 7 (model tier)** — your call, no urgency, can happen whenever.

---

## Updated responsibility note

This pass reinforces the same lesson V2 and V3 already documented from different angles: several of the worst-looking "the pipeline is wrong" complaints this session turned out to be correct backend signal that nothing downstream was reading (Findings 5, 6, and arguably 2 — a warning that existed but wasn't surfaced loudly enough to notice). Before assuming a new engine-level fix is needed, check whether the signal already exists and is just unread — that check is now cheaper than it used to be, since `session-dump`'s raw artifact dump and direct frontend greps are both fast, repeatable ways to confirm which side of the wire the gap is actually on.
