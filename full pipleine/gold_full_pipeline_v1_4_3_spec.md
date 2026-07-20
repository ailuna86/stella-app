# VA/ST.ELLA Gold Full Pipeline v1.4.3 — Specification

## Release purpose

v1.4.3 is a quality-gated integration release. v1.4.2 proved that the full chain could create all required artifacts and return `qa_status: passed`, but the downstream learning artifacts were not fully usable because:

1. Priority Engine output could collapse into `UNKNOWN_SKILL`.
2. Directive could have `primary_focus: null` and no recommended service.
3. Practice could be created with `primary_focus: null`, making it untargeted.
4. Evaluator could report `detector_available: true` but `detector_row_count: 0` because detector rows were not in the schema shape expected by Evaluator/WKE.
5. Evidence fusion was built before service artifacts, so `lret_present`, `writing_coach_present`, and `practice_present` could remain false after a full run.

v1.4.3 fixes these integration-contract problems without embedding targeted-engine logic inside the full orchestrator.

## Architecture boundary

The Gold orchestrator remains orchestration-only. It may:

- normalize a submission;
- create the session directory;
- run external standalone engines;
- copy artifacts when explicitly requested;
- validate artifact presence and JSON validity;
- run metadata-level quality gates;
- build metadata-only evidence fusion;
- write QA and manifest files.

It must not:

- detect language errors;
- score IELTS bands;
- classify LRET units;
- generate Writing Coach tasks;
- generate feedback content;
- generate new exercise content;
- perform essay-specific correction rules;
- contain lexical/collocation phrase banks.

## New/updated files

### `gold_full_pipeline_orchestrator_v1_4_3.py`
Standalone orchestrator. Adds:

- new artifact `01c_detector_for_evaluator.json`;
- new artifact `03b_priority_normalized_v1_4_3.json`;
- evidence fusion moved to the end of the stage order;
- quality gate checks in addition to artifact-presence checks.

### `detector_for_evaluator_adapter_standalone.py`
Standalone targeted bridge. Converts Detector/Errormap evidence into `diagnostic_rows`, the shape expected by Evaluator/WKE. It does not create new errors or reclassify errors.

### `priority_output_normalizer_standalone.py`
Standalone targeted bridge. Converts raw Priority output plus ErrorMap evidence into a stable directive-ready `focus_areas` contract. It repairs `UNKNOWN_SKILL` by using universal `capacity_domain` and `family` evidence from ErrorMap, not essay-specific rules.

### `directive_adapter_cli_v1_4_3.py`
Standalone directive adapter. Builds `primary_focus`, `focus_areas`, and `gold_learning_directive` from normalized priority and score contract.

### `evaluator_cli_bridge_standalone_v1_4_3.py`
Standalone Evaluator subprocess bridge. Uses the detector-for-evaluator artifact and records bridge quality context in the request.

### `gold_practice_session_builder_standalone_v1_4_3.py`
Standalone practice session builder. Selects existing exercise-bank items using directive focus. It does not generate new exercises.

### `gold_lie_profile_builder_standalone_v1_4_3.py`
Standalone learner-profile builder. Aggregates completed artifacts, focus areas, next-best action, skills progress, learning roadmap, and progress snapshot.

### `service_routing_builder_standalone_v1_4_3.py`
Standalone service-routing builder. Records final next service and available service outputs.

### `gold_engine_commands_full_v1_4_3.json`
Full command configuration for v1.4.3.

## Required artifact chain

The complete production chain is:

1. `00_submission.json`
2. `01_detector_output.json`
3. `01b_errormap_v3.json`
4. `01c_detector_for_evaluator.json`
5. `02a_premium_scorer_v1_4_1_output.json`
6. `02b_premium_verifier_v1_4_3_output.json`
7. `02c_final_adjudicated_v1_2.json`
8. `02d_final_score_contract.json`
9. `03_pe_output.json`
10. `03b_priority_normalized_v1_4_3.json`
11. `04_directive_v2.json`
12. `05_fe_output.json`
13. `06_feedback_report_v6c.json`
14. `07_evaluator_output.json`
15. `07d_lret_session.json`
16. `07e_writing_coach_output.json`
17. `07f_gold_practice_session.json`
18. `08_gold_learner_profile.json`
19. `08d_gold_service_routing.json`
20. `07b_gold_evidence_fusion.json`
21. `QA_gold_report.json`
22. `gold_run_manifest.json`

## v1.4.3 quality gates

QA passes only if all required artifacts are present and valid JSON and all quality gates pass:

1. Detector evidence is exported into `01c_detector_for_evaluator.json`.
2. Evaluator consumes detector rows when detector evidence exists.
3. Normalized priority has non-empty `focus_areas`.
4. No `UNKNOWN_SKILL` remains in normalized priority focus.
5. Directive has `primary_focus`.
6. Directive has `recommended_service` and `next_best_capacity_domain`.
7. Practice session has `primary_focus`.
8. Practice session has at least one exercise.
9. Evidence fusion is built after service outputs and records LRET, Writing Coach, and Practice as present.
10. Service routing has a next-best service.

## Expected v1.4.3 success result

A successful run should return:

```json
{
  "qa_status": "passed",
  "session_dir": "...",
  "manifest": "...gold_run_manifest.json",
  "qa_report": "...QA_gold_report.json"
}
```

The QA report should contain:

```json
"missing_required_artifacts": [],
"invalid_required_artifacts": [],
"boundary_issues": [],
"quality_gate_issues": []
```

## Run command

From the Gold folder:

```powershell
python gold_full_pipeline_orchestrator_v1_4_3.py `
  --input "submission.json" `
  --essay-index 0 `
  --engine-config "gold_engine_commands_full_v1_4_3.json" `
  --output-root "gold_sessions" `
  --pretty
```

Use strict mode only when all resource files and external engines are present:

```powershell
python gold_full_pipeline_orchestrator_v1_4_3.py `
  --input "submission.json" `
  --essay-index 0 `
  --engine-config "gold_engine_commands_full_v1_4_3.json" `
  --output-root "gold_sessions" `
  --pretty `
  --strict
```

## Freeze rule

v1.4.3 can be frozen as an integration milestone only if:

- `qa_status` is `passed`;
- `quality_gate_issues` is empty;
- Evaluator reports detector row consumption greater than zero when Detector has evidence;
- normalized Priority has meaningful focus areas;
- Directive has non-null `primary_focus`;
- Practice has non-null `primary_focus` and targeted exercises;
- Service routing has a next-best service;
- no full-pipeline file contains embedded Detector/Scorer/LRET/Coach/Practice logic.

This does not mean every targeted engine is final. It means the Gold orchestration and cross-engine contracts are safe enough to continue product testing.
