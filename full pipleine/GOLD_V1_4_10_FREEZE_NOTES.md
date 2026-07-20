# Gold Pipeline v1.4.10 — Freeze Notes

**Status: FROZEN as of 2026-07-11.** This is the checkpoint to build the frontend/API layer around going forward.

**Orchestrator:** `gold_full_pipeline_orchestrator_v1_4_9.py`
**Engine config:** `gold_engine_commands_full_v1_4_10.json`
**Run command:**
```
python gold_full_pipeline_orchestrator_v1_4_9.py \
  --input <submission.json> \
  --essay-index 0 \
  --engine-config gold_engine_commands_full_v1_4_10.json \
  --output-root gold_sessions \
  --pretty
```

## What's in this freeze

The full cyclical Gold pipeline: Detector → Scorer/Verifier/Adjudicator/Score Contract → Progress Tracker → Evaluator → Priority Engine → Directive → Feedback → LRET → Writing Coach → Practice → Learning Intelligence profile → persisted continuity, with a working cross-essay loop (a student's second, third, fourth... essay correctly reloads prior state).

Detector is `det_vip_v18d_2.py`'s `PremiumDetectorV9`, wired in via a new CLI bridge (`det_vip_cli_bridge_v1.py`), replacing the earlier placeholder regex-only detector. LLM is enabled on Detector only (`--require-llm`, `gpt-4o-mini`, det_vip's own default). Evaluator and LRET still run with their LLM flags off; Writing Coach's four LLM toggles (grammar judge, register judge, correction generator, upgrade generator) are untouched/off.

## What's verified

- Four consecutive real sessions for the same student (student_123) across v1.4.8 → v1.4.9 → v1.4.10, confirming the continuity loop actually accumulates state correctly session over session (`sessions_analyzed`, Progress Tracker's own `score_events`, previous directive snapshot).
- One clean end-to-end run of the det_vip LLM-detector config: all 28 stages `ok`, `qa_status: passed`, zero QA gate issues.
- The review-only vs. chargeable row bug (found during bridge development, where non-chargeable candidates were leaking through as scoreable evidence) was fixed and re-verified at real detection volume — 22 review-only rows, all correctly excluded.
- errormap correctly resolved all real detector rows to a known criterion/capacity_domain (zero `unknown`), confirming det_vip's `rubric` field and the existing `CANONICAL_RUBRIC_BY_FAMILY`/`CAPACITY_BY_CRITERION` fallback tables in the downstream consumers are compatible with det_vip's native output — no new mapping tables were needed.
- LRET's canonical resources load correctly from the real registry path and produce genuine fix/enhance/clarify output (not the zero-resource fallback seen earlier in development).
- Score Contract, Verifier, Adjudicator, Progress Tracker identity, and Writing Coach's move selection all check out on real data.

## Known open items (not blocking the freeze, but not resolved)

1. **spaCy / LanguageTool report "unavailable" inside the pipeline run**, despite both working when tested standalone in an interactive shell on the same machine. Likely an environment difference between that shell and the subprocess environment the orchestrator launches (PATH, working directory, or Java visibility for LanguageTool's subprocess). Until this is root-caused, Detector is running on LLM + rule passes only, not the full rule+spaCy+LanguageTool+LLM stack it was designed for.
2. **44% of det_vip's LLM calls failed** in the one real run so far (32 attempted, 18 succeeded), with no per-call error detail captured by det_vip's own `LLMTracker`. Cost impact was negligible ($0.0069) but it represents lost detection signal. Worth monitoring on the next few runs; if it persists, det_vip's tracker may need extending to log failure reasons.
3. **Single-essay, single-student validation only.** Every real run so far has been against the same student/essay pattern. No coverage yet for different task types, score bands, essay lengths, or multiple concurrent students.
4. **Practice session produced only 1 exercise** in the last real run — not confirmed as a bug, but not sanity-checked against the directive's focus areas either.
5. **Evaluator and LRET's own LLM flags are still off.** Both already default to sensible models (`gpt-4o-mini`, `gpt-5-nano` respectively) if enabled later — no code changes needed, just flags in the engine config.
6. **Writing Coach's four LLM features are untouched.** Per the earlier writing-coach freeze notes, only 1/44 moves have real content-aware grading, and without `--llm-upgrade-generator` its "upgraded academic version" suggestions fall back to old per-topic hardcoded templates.

## Contract surface for frontend integration

Every session writes a fixed, versioned set of JSON artifacts to `{output-root}/{student_id}/{session_id}/` — the same filenames regardless of which engine-config version is active (v1.4.9's no-LLM baseline and v1.4.10's det_vip config produce identically-shaped artifacts). Key ones a frontend will likely read from:

- `QA_gold_report.json` — `qa_status` (`passed` / `needs_attention`), gate-level issues.
- `02d_final_score_contract.json` — `released_score`, `score_confidence`, gating flags.
- `04_directive_v2.json` — routing/focus recommendations.
- `07e_writing_coach_output.json`, `07f_gold_practice_session.json`, `07d_lret_session.json` — the three student-facing service outputs.
- `08_gold_learner_profile.json` / `08a_gold_persisted_profile.json` — the learner's running profile.
- `10_revision_workspace.json` — essay revision support material.

Per-student continuity state persists separately at `{learner_profiles_dir}/{student_id}_gold_profile.json`, `{student_id}_gold_progress_profile.json`, and `{student_id}_writing_coach_state.json` — these should not be deleted between sessions.
