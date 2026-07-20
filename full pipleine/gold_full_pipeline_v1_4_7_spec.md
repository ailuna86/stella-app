# VA / ST.ELLA Gold Full Pipeline v1.4.7 — Production-Readiness Bug-Fix Release

## 1. Purpose

v1.4.7 is a targeted integration bug-fix release for the Gold full pipeline. It fixes the remaining production blockers observed after v1.4.4–v1.4.6 without embedding Detector, Scorer, Priority, Evaluator, LRET, Writing Coach, Practice, Revision, or LIE logic inside the orchestrator.

The release keeps the architecture rule intact:

- the orchestrator coordinates only;
- bridge files normalize contracts only;
- core engines still own their own scoring, detection, teaching, LRET classification, and practice-generation logic;
- no essay-specific rules, topic-specific rules, or hardcoded sample hacks are introduced.

## 2. Problems fixed

### Problem 1 — Scorer local evidence was effectively zeroed

Previous output showed that the scorer could receive `n_items` but still report:

- `chargeable_count = 0`
- `local_root_weight = 0.0`
- `grammar_weight = 0.0`
- `lr_weight = 0.0`

This happened because Detector rows used fields such as `chargeable: true`, `family: G_VERB_PATTERN`, and `criterion: grammar`, while the scorer expected scorer-readable fields such as:

- `chargeable_for_scoring`
- `score_weight` / `score_charge_weight`
- canonical family names like `VERB_PATTERN`, `REGISTER`, `LEXICAL_PRECISION`, `GRAMMAR_PUNCTUATION`

v1.4.7 adds `scorer_input_evidence_guard_standalone_v1_4_7.py`.

It preserves metadata and produces scorer-readable evidence rows.

### Problem 2 — Raw Priority Engine collapsed to `UNKNOWN_SKILL`

Previous Priority output still produced `UNKNOWN_SKILL` because prefixed detector families such as `G_VERB_PATTERN`, `L_INFORMAL_VOCAB`, and `A_UNDERDEVELOPED` were not visible to Priority Engine’s fallback family map.

v1.4.7 adds `priority_input_builder_standalone_v1_4_7.py`.

It feeds Priority Engine canonical families and visible task metadata:

- `task_type`
- `prompt_present`
- canonical `student_rows`
- scorer fields where available

Smoke test result: raw Priority changed from `UNKNOWN_SKILL` to `GRAMMAR_CONTROL / GRA`.

### Problem 3 — Writing Coach / Directive mismatch was silent

Previous output had this mismatch:

- Directive primary focus: `sentence_control`
- Writing Coach selected skill: `arg_reason_generation`

The mismatch may sometimes be educationally reasonable, but it must not be silent.

v1.4.7 adds `writing_coach_alignment_guard_standalone_v1_4_7.py`.

It preserves the original Writing Coach decision but adds:

- `directive_alignment.status`
- `directive_primary_focus`
- `coach_selected_focus`
- `silent_mismatch_prevented`
- `effective_focus_for_gold_routing`
- explicit override explanation when needed

### Problem 4 — QA could pass when learning-quality contracts were unsafe

v1.4.7 extends orchestrator QA gates. `qa_status: passed` now requires more than JSON artifact presence.

New QA gates check:

- scorer evidence is not zero when ErrorMap has errors;
- scorer local-root pressure is not zero when local errors exist;
- Priority input is ready;
- raw Priority no longer outputs `UNKNOWN_SKILL`;
- Writing Coach has alignment metadata;
- score contract is not blocking progress tracking / LIE without being reported;
- LRET output reports v1.4.6 engine version;
- previous metadata and downstream-service gates remain active.

## 3. New / changed files

### 3.1 `gold_full_pipeline_orchestrator_v1_4_7.py`

Standalone orchestration-only runner.

Changed from v1.4.6:

- added artifact key `priority_input`;
- added artifact key `writing_coach_raw`;
- added stage `priority_input` before `priority`;
- added stage `writing_coach_raw` before aligned `writing_coach`;
- added QA gates for scorer evidence, raw Priority, Writing Coach alignment, and score contract restrictions;
- retained LRET v1.4.6 version gate.

Boundary:

- does not score;
- does not detect;
- does not infer priorities;
- does not align coach content semantically;
- does not generate feedback, practice, LRET units, or revisions.

### 3.2 `scorer_input_evidence_guard_standalone_v1_4_7.py`

Input:

- `--detector 01_detector_output.json`
- `--submission 00_submission.json`

Output:

- `01d_detector_for_scorer.json`

Main contract:

- preserves `word_count`, `sentence_count`, `paragraph_count`;
- deduplicates exact repeated detector rows;
- canonicalizes universal family names;
- adds scorer-readable chargeable row fields;
- writes `scorer_payload.chargeable_detector_rows` and `scorer_payload.review_only_detector_rows`.

Universal family examples:

| Source family | Canonical family |
|---|---|
| `G_VERB_PATTERN` | `VERB_PATTERN` |
| `G_MISSING_VERB` | `CLAUSE_STRUCTURE` |
| `G_SPACING` | `GRAMMAR_PUNCTUATION` |
| `G_SV_AGREEMENT` | `SUBJECT_VERB_AGREEMENT` |
| `G_ARTICLE` | `ARTICLE_DETERMINER` |
| `L_INFORMAL_VOCAB` | `REGISTER` |
| `S_INFORMAL_TONE` | `REGISTER` |
| `L_LIMITED_VOCAB` | `LEXICAL_PRECISION` |
| `A_UNDERDEVELOPED` | `UNSUPPORTED_CLAIM` |
| `C_SIMPLE_CONNECTORS` | `TRANSITION` |

This is a universal family-alias contract, not an essay-specific rule.

Smoke test result on the current essay:

- metadata preserved: `272 words`, `18 sentences`, `5 paragraphs`;
- scorer-readable chargeable rows: `42`;
- exact duplicate rows removed: `2`.

### 3.3 `priority_input_builder_standalone_v1_4_7.py`

Input:

- `--detector 01d_detector_for_scorer.json`
- `--submission 00_submission.json`
- optional `--scorer 02a_premium_scorer_v1_4_1_output.json`

Output:

- `03a_priority_input_v1_4_7.json`

Main contract:

- exposes `task_profile.task_type`;
- exposes prompt presence;
- places canonical scorer-readable rows on `student_rows`;
- overlays scorer fields when available;
- fails in strict mode if the Priority input is not ready.

Smoke test result:

- raw Priority primary limiter became `GRAMMAR_CONTROL / GRA` instead of `UNKNOWN_SKILL`.

### 3.4 `writing_coach_alignment_guard_standalone_v1_4_7.py`

Input:

- raw Writing Coach output: `07e_writing_coach_raw.json`
- directive: `04_directive_v2.json`

Output:

- aligned Writing Coach artifact: `07e_writing_coach_output.json`

Main contract:

- preserves Writing Coach’s selected task;
- annotates alignment with the Gold Directive;
- prevents silent mismatch;
- provides `effective_focus_for_gold_routing`.

If the coach selects a different skill, the output is not silently accepted. It receives:

```json
{
  "directive_alignment": {
    "status": "explained_override",
    "silent_mismatch_prevented": true
  }
}
```

### 3.5 `gold_engine_commands_full_v1_4_7.json`

Full command config for v1.4.7.

Main command changes:

- `detector_for_scorer` now uses `scorer_input_evidence_guard_standalone_v1_4_7.py`;
- new `priority_input` stage uses `priority_input_builder_standalone_v1_4_7.py`;
- `priority` now reads `{priority_input}`;
- `lret_session` uses `lret_engine_v1_4_6_universal_hybrid.py`;
- original Writing Coach writes to `{writing_coach_raw}`;
- final `{writing_coach}` is produced by the alignment guard.

## 4. Validation behavior

A v1.4.7 run should fail QA if any of the following happens:

- Detector/scorer length metadata is zero;
- scorer shows zero chargeable evidence while ErrorMap has errors;
- scorer local-root weight is zero while local errors exist;
- Priority input is missing task type, prompt, or canonical rows;
- raw Priority still outputs `UNKNOWN_SKILL`;
- Writing Coach output has no directive-alignment record;
- LRET output does not report engine version containing `1.4.6`;
- practice has no primary focus or no exercises;
- evidence fusion cannot see LRET, Writing Coach, or Practice outputs.

## 5. Smoke-test results available during implementation

Using the current uploaded essay and detector output:

### Scorer evidence guard

```text
metadata: 272 words / 18 sentences / 5 paragraphs
chargeable rows for scoring: 42
duplicate rows removed: 2
```

### Scorer after v1.4.7 input guard

```text
n_items: 42
chargeable_count: 34
local_root_weight: 11.155
grammar_weight: 7.15
lr_weight: 4.005
criteria: TR 5, CC 5, LR 5, GRA 5
overall: 5.0
```

This fixes the previous `chargeable_count = 0` problem.

### Verifier / adjudicator / score contract after v1.4.7 scorer input

```text
verifier_status: pass
release_decision: release
adjudication_status: confirmed
score_confidence: normal
progress_tracking_allowed: true
lie_update_allowed: true
```

This fixes the previous reduced-confidence / no-progress-tracking issue for this run.

### Priority after v1.4.7 input builder

```text
raw primary limiter: GRAMMAR_CONTROL / GRA
metadata task_type: WT2
prompt_present: true
```

This fixes the previous raw `UNKNOWN_SKILL` issue.

## 6. Remaining external dependency

The command config expects this file in the full pipeline folder:

```text
lret_engine_v1_4_6_universal_hybrid.py
```

It is not embedded in the v1.4.7 bridge code. LRET classification remains owned by the LRET engine.

## 7. Run command

```powershell
python gold_full_pipeline_orchestrator_v1_4_7.py `
  --input "submission.json" `
  --essay-index 0 `
  --engine-config "gold_engine_commands_full_v1_4_7.json" `
  --output-root "gold_sessions" `
  --pretty
```

Strict production check:

```powershell
python gold_full_pipeline_orchestrator_v1_4_7.py `
  --input "submission.json" `
  --essay-index 0 `
  --engine-config "gold_engine_commands_full_v1_4_7.json" `
  --output-root "gold_sessions" `
  --pretty `
  --strict
```

## 8. Freeze rule for v1.4.7

v1.4.7 can be considered production-ready only if the actual full run produces:

- `qa_status: passed`;
- scorer `chargeable_count > 0`;
- scorer `local_root_weight > 0` when local errors exist;
- raw Priority primary limiter not `UNKNOWN_SKILL`;
- Writing Coach alignment status present;
- final score confidence not reduced;
- progress tracking and LIE update allowed;
- LRET engine version contains `1.4.6`.
