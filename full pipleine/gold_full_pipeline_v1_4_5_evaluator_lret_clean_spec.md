# VA/ST.ELLA Gold Pipeline v1.4.5 — Evaluator→LRET Payload Quality Release

## Release decision

v1.4.5 is a narrow targeted release. It does not change the Gold orchestrator, scoring engines, verifier, adjudicator, feedback, Writing Coach, Practice Engine, or LRET logic.

The release upgrades the Evaluator/WKE only:

- from `va_premium_evaluator_v7_3b_wke_v7_3b_3.py`
- to `va_premium_evaluator_v7_3c_wke_lret_clean.py`

and updates the full-chain config so LRET uses the user-selected engine:

- `lret_engine_v1_4_4_1_universal_hybrid.py`

## Problem fixed

In v1.4.4, LRET received 203 lexical units from Evaluator/WKE. LRET then reported high upstream noise:

- `fragment_or_noise_estimate = 145`
- `fragment_or_noise_rate = 0.714`
- `recommended_action = needs_evaluator_prompt_update`

This meant LRET was spending too much effort suppressing fragments that should never have reached it.

## Boundary policy

Evaluator/WKE still does **not** classify LRET units as KEEP, FIX, ENHANCE, or CLARIFY.

Evaluator/WKE is allowed to:

- extract candidate lexical spans;
- suppress incomplete/noisy/open-boundary spans;
- pass detector-supported lexical repair candidates to LRET when the repair family is lexical-only;
- provide a payload-quality audit.

Evaluator/WKE is not allowed to:

- score IELTS bands;
- perform LRET classification;
- generate LRET replacement suggestions except passing detector-provided concrete span replacements;
- embed essay-specific phrase rules;
- use topic/sentence-index hacks.

## v7.3c Evaluator changes

### 1. LRET-clean lexical extraction

The previous broad extractor exported too many units:

- isolated words;
- generic person-group topic mentions;
- subject+verb prefixes;
- open-boundary chunks;
- cross-clause windows;
- weak adjacent 2-grams;
- discourse-frame fragments;
- malformed person-plural topic spans.

The new extractor exports only more complete, replaceable lexical spans.

### 2. Single-word suppression

Single-word vocabulary evidence remains available inside Evaluator metrics, but it is not exported to LRET by default. This avoids converting LRET into a noisy vocabulary list.

### 3. Phrase-first payload cleaning

When a shorter same-sentence unit is covered by a stronger phrase, the shorter unit is suppressed before LRET receives the payload.

### 4. Lexical-only detector fix candidate mapping

The Evaluator now maps detector families into LRET lexical repair families only when appropriate.

Examples:

- `G_COMPARATIVE_FORM` + `more + adjective-er` → `WORD_FORM_LEXICAL`
- `L_REPETITION` with concrete span-level suggestion → `REDUNDANCY`
- spelling / lexical word-form families with concrete suggestions → LRET FIX candidates

Grammar-only families remain excluded.

### 5. Payload-quality profile

The LRET payload now includes:

- unit count;
- single-word share;
- edge/open-boundary rate;
- fix-candidate audit;
- suppression counts;
- extraction-source counts.

## Smoke-test result on current essay

Using the current ageing-population essay and LRET v1.4.4.1:

### Before v7.3c

- Evaluator LRET units: 203
- LRET fragment/noise rate: 0.714
- LRET FIX units: 0
- LRET warning: upstream evaluator units need cleanup

### After v7.3c

- Evaluator LRET units: 8
- Single-word share: 0.0
- Edge/open-boundary rate: 0.0
- LRET fragment/noise rate: 0.0
- LRET FIX units: 1
- LRET CLARIFY units: 2
- LRET KEEP units: 4
- LRET QA warnings: 0

Observed LRET output after v7.3c:

- FIX: `more stronger` → `stronger`
- CLARIFY: `excited things`, `kinds of things`
- KEEP: `cause some problems`, `give good advice`, `family traditions`, `need care`

## Files

- `va_premium_evaluator_v7_3c_wke_lret_clean.py`
- `gold_engine_commands_full_v1_4_5.json`

## Run command

```powershell
python gold_full_pipeline_orchestrator_v1_4_4.py `
  --input "submission.json" `
  --essay-index 0 `
  --engine-config "gold_engine_commands_full_v1_4_5.json" `
  --output-root "gold_sessions" `
  --pretty
```

Use `--strict` for release QA.

## Remaining known blockers outside this release

v1.4.5 does not fix:

- scorer local-error weights showing zero despite ErrorMap evidence;
- raw Priority Engine still producing `UNKNOWN_SKILL` before normalization;
- Writing Coach sometimes overriding Directive focus without explicit override explanation.

These belong to separate targeted engine releases.
