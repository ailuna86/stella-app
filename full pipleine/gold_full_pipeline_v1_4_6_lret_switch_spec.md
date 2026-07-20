# Gold Full Pipeline v1.4.6 — LRET v1.4.6 Algorithm Switch

## Purpose
v1.4.6 is a narrow integration release. It keeps the v1.4.4/v1.4.5 orchestration architecture but switches the LRET stage to the new `lret_engine_v1_4_6_universal_hybrid.py` algorithm.

This release does not embed LRET logic inside the Gold orchestrator. The orchestrator remains orchestration-only.

## Inputs
The run still starts from the normalized Gold submission and upstream Detector artifacts. The currently supplied upstream files show that detector metadata is present and non-zero:

- `word_count = 272`
- `sentence_count = 18`
- `paragraph_count = 5`

## Engine changes

### Changed
- `gold_engine_commands_full_v1_4_6.json`
  - LRET command now calls `lret_engine_v1_4_6_universal_hybrid.py`.

### Added
- `gold_full_pipeline_orchestrator_v1_4_6.py`
  - Same orchestration-only structure as v1.4.4.
  - QA now fails if the produced LRET artifact does not report an engine version containing `1.4.6`.

## Not changed
- Detector logic is not changed.
- Scorer logic is not changed.
- Priority logic is not changed.
- Writing Coach logic is not changed.
- Evaluator LRET-clean extraction from v7.3c remains the expected upstream payload source.

## Required local file
The actual LRET engine file must be present in the full pipeline folder:

```text
lret_engine_v1_4_6_universal_hybrid.py
```

This pack does not include that engine file because it was referenced by name but not uploaded into the sandbox for packaging.

## Run command

```powershell
python gold_full_pipeline_orchestrator_v1_4_6.py `
  --input "submission.json" `
  --essay-index 0 `
  --engine-config "gold_engine_commands_full_v1_4_6.json" `
  --output-root "gold_sessions" `
  --pretty
```

Strict run:

```powershell
python gold_full_pipeline_orchestrator_v1_4_6.py `
  --input "submission.json" `
  --essay-index 0 `
  --engine-config "gold_engine_commands_full_v1_4_6.json" `
  --output-root "gold_sessions" `
  --pretty `
  --strict
```

## Freeze criteria for v1.4.6
A run is acceptable only if:

1. QA status is `passed`.
2. `07d_lret_session.json` reports an LRET engine version containing `1.4.6`.
3. LRET receives the cleaned Evaluator v7.3c payload.
4. LRET output has usable student-facing units and does not regress to high fragment/noise rate.
5. Scorer metadata remains non-zero.

## Known remaining blockers outside this release
The scorer-evidence weighting issue may still remain if scorer output continues to report `chargeable_count = 0` or local weights as zero despite ErrorMap evidence. This release does not alter scorer internals.
