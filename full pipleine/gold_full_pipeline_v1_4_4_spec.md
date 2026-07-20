# VA / ST.ELLA Gold Full Pipeline v1.4.4 Specification

## Release purpose

v1.4.4 is a narrow quality-alignment release after v1.4.3.

v1.4.3 proved that the Gold pipeline can run end-to-end, but two blockers remained:

1. The scorer received missing length metadata and therefore used unsafe fallback values:
   - `word_count = 0`
   - `paragraph_count = 0`
   - `sentence_count = 0` / unavailable in scorer-facing features

2. The pipeline still referenced the older LRET engine in the command config.

v1.4.4 fixes these without turning the Gold orchestrator into a detector, scorer, LRET, coach, practice, or learner-model engine.

---

## Non-negotiable boundaries

The Gold orchestrator remains orchestration-only.

It may:

- normalize the submission;
- create the session folder;
- run configured external engines;
- copy precomputed artifacts for QA;
- validate artifact presence and JSON contracts;
- run metadata-level quality gates;
- build metadata-level evidence fusion.

It must not:

- detect writing errors;
- score IELTS bands;
- adjudicate scores;
- classify LRET units;
- generate lexical suggestions;
- generate Writing Coach tasks;
- select practice exercises;
- revise essays;
- update mastery models directly;
- contain essay-specific/topic-specific rules.

All targeted work stays inside targeted standalone engines.

---

## v1.4.4 components

### 1. `detector_cli_v1_4_4.py`

Standalone Detector bridge.

New responsibility:

- provide scorer-readable length metadata directly from `essay_text`.

It emits:

- `word_count`
- `sentence_count`
- `paragraph_count`

in all scorer-readable locations:

- `results[].word_count`
- `results[].sentence_count`
- `results[].paragraph_count`
- `results[].metadata`
- `results[].generated_metadata`
- `results[].detector_metric_profile.shared`
- `results[].scorer_payload.metadata`
- `results[].scorer_payload.premium_metric_profile_mapped_metrics.shared`

It does not change the detector boundary: it still uses universal detector rules only and does not score.

---

### 2. `scorer_input_metadata_guard_standalone_v1_4_4.py`

Standalone metadata guard before the scorer.

Purpose:

- verify that Detector metadata is present;
- enrich missing/zero metadata from `essay_text` only if needed;
- fail in strict mode if positive metadata cannot be recovered.

It does not:

- detect errors;
- score;
- change detector row classifications;
- generate feedback or practice.

This guard prevents the scorer from silently using fallback length defaults.

---

### 3. `gold_full_pipeline_orchestrator_v1_4_4.py`

Standalone orchestration-only Gold runner.

New artifact:

- `01d_detector_for_scorer.json`

New stage order:

```text
submission
→ detector
→ detector_for_scorer
→ errormap
→ detector_for_evaluator
→ scorer
→ verifier
→ adjudicator
→ score_contract
→ priority
→ priority_normalized
→ directive
→ feedback
→ evaluator
→ LRET
→ Writing Coach
→ practice
→ learner profile
→ service routing
→ evidence fusion
```

New QA gates:

- Detector/scorer metadata must be positive:
  - `word_count > 0`
  - `paragraph_count > 0`
  - `sentence_count > 0` at Detector/guard level
- Scorer must no longer report:
  - `word_count = 0`
  - `paragraph_count = 0`
- If Detector word count is valid, scorer must not trigger false short-response/catastrophic-short-response signals.
- LRET output must come from v1.4.4, not the older v1.3.x engine.
- Evidence fusion must still see:
  - LRET
  - Writing Coach
  - Practice
- Existing v1.4.3 gates remain:
  - Priority focus exists;
  - no `UNKNOWN_SKILL` after normalization;
  - Directive has primary focus and routing fields;
  - Evaluator consumes detector rows;
  - Practice has a primary focus and exercises.

---

### 4. `gold_engine_commands_full_v1_4_4.json`

Main command changes:

```text
detector uses detector_cli_v1_4_4.py
scorer input uses 01d_detector_for_scorer.json
errormap input uses 01d_detector_for_scorer.json
detector_for_evaluator input uses 01d_detector_for_scorer.json
priority input uses 01d_detector_for_scorer.json
LRET uses lret_engine_v1_4_4_universal_hybrid.py
```

The attached LRET file is used directly:

```text
lret_engine_v1_4_4_universal_hybrid.py
```

The LRET command is:

```text
python lret_engine_v1_4_4_universal_hybrid.py
  --input {evaluator}
  --output {lret_session}
  --student-id {student_id}
  --essay-id {essay_id}
  --canonical-resources {project_root}
  --pretty
  --summary
```

No OpenAI LLM call is required by default. If later needed, `--use-llm` can be added to the LRET command config.

---

## Expected scoring effect

With v1.4.3, the scorer saw `word_count = 0` and `paragraph_count = 0`, so it could apply false short-response constraints.

With v1.4.4, the scorer should receive the real essay length. For the current test essay, the Detector/guard computes approximately:

```text
word_count: 272
paragraph_count: 5
sentence_count: 18
```

Therefore, the scorer should not trigger:

```text
catastrophic_short_response = true
short_response_cap
```

The final released band may change because the false short-response penalty is removed. That is intended: v1.4.4 corrects the evidence supplied to the scorer.

---

## Freeze policy

v1.4.4 may be frozen as a metadata-correct integration release if:

- `QA_gold_report.json` has `qa_status = passed`;
- `01d_detector_for_scorer.json` contains positive metadata;
- scorer `tier_decision.features.word_count` and `paragraph_count` are positive;
- no false short-response signal remains;
- LRET run version contains `1.4.4`;
- downstream service artifacts are present and valid.

v1.4.4 should not be frozen if:

- scorer metadata is still zero;
- scorer still applies short-response logic to a valid-length essay;
- LRET output is produced by v1.3.x;
- Evaluator detector row count returns to zero;
- Priority normalization returns `UNKNOWN_SKILL`;
- Practice has no primary focus.

---

## Run command

```powershell
python gold_full_pipeline_orchestrator_v1_4_4.py `
  --input "submission.json" `
  --essay-index 0 `
  --engine-config "gold_engine_commands_full_v1_4_4.json" `
  --output-root "gold_sessions" `
  --pretty
```

Strict production check:

```powershell
python gold_full_pipeline_orchestrator_v1_4_4.py `
  --input "submission.json" `
  --essay-index 0 `
  --engine-config "gold_engine_commands_full_v1_4_4.json" `
  --output-root "gold_sessions" `
  --pretty `
  --strict
```

---

## QA interpretation

`qa_status = passed` now means more than artifact presence.

It means the pipeline passed the critical v1.4.4 evidence-contract checks:

```text
Detector metadata → scorer metadata → score reasoning
LRET v1.4.4 actually used
downstream services present
priority/directive/practice routing usable
```
