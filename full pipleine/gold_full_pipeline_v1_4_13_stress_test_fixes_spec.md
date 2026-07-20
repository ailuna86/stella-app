# VA / ST.ELLA Gold Full Pipeline v1.4.13 — Stress-Test Bug-Fix Spec

## 1. Purpose

v1.4.13 is a targeted bug-fix release driven by a 3-essay stress test (weak / medium / strong, same
prompt, same task type, three separate first-session students) run end-to-end through v1.4.12 with all
real resources loaded (spaCy `en_core_web_sm`, LanguageTool `en-US`, LLM enabled, `rule_registry_v1.json`
loaded, 47 rules). All 21 stages completed with zero crashes on all three essays. The bugs below are
**data-quality / evidence-routing bugs**, not stability bugs — nothing in this release changes the
orchestrator's stage graph or artifact contracts.

Architecture rule stays intact: the orchestrator coordinates only; bridge files normalize contracts only;
core engines (Scorer, Priority, Evaluator, LRET, Writing Coach, Practice, Revision, LIE) keep owning their
own logic. No essay-specific or topic-specific rules are introduced.

## 2. Stress-test evidence (why these problems were invisible until now)

Prior single-essay runs never exercised more than one point on the quality spectrum in the same session,
so several defaults-masquerading-as-signals never surfaced. Running weak/medium/strong side by side
exposed them directly:

| Signal | Weak | Medium | Strong |
|---|---|---|---|
| Chargeable errors (errormap) | 21 | 2 | 1 (see Problem 4) |
| `support_quality` | 0.55 | 0.55 | 0.55 |
| `idea_extension_depth` | 0.55 | 0.55 | 0.55 |
| `relevance_ratio` | 0.55 | 0.55 | 0.55 |
| `global_progression` | 0.55 | 0.55 | 0.55 |
| `paragraphing_appropriacy` | 0.55 | 0.55 | 0.55 |
| `local_language_damage_index` | 0.0 | 0.0 | 0.0 |
| `serious_error_sentence_ratio` | 0.0 | 0.0 | 0.091 |
| Official `task_response` / `coherence_cohesion` bands | 5 / 5 | 5 / 5 | 5 / 5 |
| `pre_governor_overall` → released overall | 5.0 → 5.0 | 6.0 → 5.0 | 6.0 → 5.0 |
| `tier_governor.action` | none | adjusted | adjusted |
| `criteria_math_valid` | true | **false** | **false** |
| Practice-session exercises delivered | 1 | 1 | **0** |

Five identical decimal values (0.55) across three essays that differ by word count (208/252/325), error
density (21/2/1), and register (basic/intermediate/academic) is not a coincidence — it is five metrics
that are never actually computed from essay content. Everything downstream that looks like differentiated
scoring for `task_response`/`coherence_cohesion` is currently riding on `serious_error_sentence_ratio`,
`semantic_recoverability`, and word/paragraph count alone.

## 3. Problems fixed

### Problem 1 — Practice engine criterion-label mismatch (0 exercises delivered on strong)

`practice_engine_v5b.py::filter_bank()` resolves bank stock via:

```python
target_cats     = _CRITERION_TO_CATEGORIES.get(criterion, set())
target_families = set(_CRITERION_FAMILIES.get(criterion, []))
```

Both lookup tables are keyed by the four **internal** criterion IDs: `grammatical_range_accuracy`,
`lexical_resource`, `task_achievement`, `coherence_cohesion`. But `compute_allocation()` (line ~419)
passes `criterion` straight through from `directive.focus_areas[i]["criterion"]`, which carries
**display labels** produced upstream by the priority/directive layer: `"grammar"`, `"Organization"`,
`"Argumentation"`, `"Reasoning Competence"`. Only `"lexical_resource"` happens to be spelled identically
in both schemes, so it is the only criterion that has ever resolved to a non-empty pool. Every other
criterion has silently returned zero stock on every run since the practice engine was wired in — it just
never fully emptied a session before, because weak and medium each happened to have exactly one
`lexical_resource` slot in their 10-11-item target lists. Strong's target list (`grammar` × 1,
`Organization` × 5, `Argumentation` × 4, `Reasoning Competence` × 1) has zero `lexical_resource` slots,
so it returned 0 of 11 — a fully empty, `no_exercises_available` session, flagged high-severity by QA.

This is **not** a bank-stock gap. `va_exercise_bank_v11d_approved.jsonl` has 12,710 exercises with strong
coverage in the exact families the mismatched criteria need (`COUNTERARGUMENT` 225, `ARGUMENT_STRUCTURE`
200, `INTRODUCTION_CONCLUSION` 200, `TOPIC_SENTENCE` 200, `PARAGRAPH_PROGRESS` 240, `CLAIM_SUPPORT` 268),
spread across all four CEFR levels.

**Fix**: add a translation step at the same call site that already translates `skill_tag` via
`_SKILL_TAG_TO_FAMILY` (the I-3 fix pattern), so `criterion` is normalized before it reaches
`_CRITERION_TO_CATEGORIES`/`_CRITERION_FAMILIES`:

```python
_FOCUS_CRITERION_TO_INTERNAL: Dict[str, str] = {
    "grammar":              "grammatical_range_accuracy",
    "lexical_resource":     "lexical_resource",
    "Organization":         "coherence_cohesion",
    "Argumentation":        "task_achievement",
    "Reasoning Competence": "task_achievement",
}
```

Applied as `criterion = _FOCUS_CRITERION_TO_INTERNAL.get(criterion, criterion)` at the top of
`filter_bank()`, before `target_cats`/`target_families` are computed. `"Reasoning Competence"` is mapped
to `task_achievement` provisionally — it doesn't cleanly correspond to any of the four IELTS criteria as
currently modeled, and the mapping should be revisited once Priority Engine's domain taxonomy is
finalized (see Open Question 1).

### Problem 2 — Scorer's content-quality sub-metrics are never populated

`premium_unified_scorer_v1_4_1_fixed.py::_features()` (line ~761) reads:

```python
"support_quality":          profile.get_float("TR6_support_quality", 0.55),
"idea_extension_depth":     profile.get_float("TR5_idea_extension_depth", 0.55),
"relevance_ratio":          profile.get_float("TR4_relevance_ratio", 0.55),
"global_progression":       profile.get_float("CC1_global_logical_progression", 0.55),
"paragraphing_appropriacy": profile.get_float("CC3_paragraphing_appropriacy", 0.55),
```

`adapt_record()` (line ~607) only ever calls `_set_if()` for: `semantic_recoverability`,
`weak_writing_probability`, `high_band_readiness`, `local_language_damage_index`,
`serious_error_sentence_ratio`, `word_count`, `paragraph_count`, `sentence_count`,
`proposition_stability`, `affected_discourse_ratio`, `task_schema_status`, `task_schema_confidence`. None
of the `TR1`–`TR8`, `CC1`–`CC7` keys are ever set from the Gold detector/evaluator payload. There is a
backfill path (`RUBRIC_TO_COMPOSITE_METRICS`, line ~407) that would populate them from
`record["score_profile"]["rubrics"][x]["metric_composite_score"]` — but nothing in the current Gold
wiring (`scorer_input_evidence_guard_standalone_v1_4_7.py` → `{detector_for_scorer}`) produces a
`score_profile.rubrics` block, so the backfill never fires. Result: `TR6_support_quality`,
`TR5_idea_extension_depth`, `TR4_relevance_ratio`, `CC1_global_logical_progression`,
`CC3_paragraphing_appropriacy` sit at their hardcoded 0.55 default on **every** run, for every essay,
regardless of actual content quality. This is the direct cause of `task_response` and
`coherence_cohesion` being flat-band-5 across all three stress essays.

**Fix requires a decision before implementation** — see Open Question 2. Two candidate sources:

- **(a) Evaluator-derived.** `evaluator_cli_bridge_standalone_v1_4_3.py` already produces
  `skill_observation_profile` (121 skill entries per essay, includes `support`/`development`/`cohesion`-
  adjacent skill tags) and `evidence_graph`. A new small bridge
  (`evaluator_to_scorer_rubric_bridge_v1.py`) could map relevant `skill_observation_profile` entries into
  `score_profile.rubrics.{task_response,coherence_cohesion}.metric_composite_score`, feeding the existing
  (currently dead) backfill path with no scorer changes needed.
- **(b) LLM rubric rater.** Add a direct LLM-scored rubric pass inside the scorer or a dedicated
  pre-scorer stage, producing the same `TR*`/`CC*` keys from a structured prompt against the essay text
  and prompt. Higher cost per run, more directly interpretable, duplicates some of what the Evaluator
  already estimates.

This spec recommends (a) as the first attempt, since it reuses evidence the pipeline already computes and
keeps the scorer itself free of a second LLM dependency. Not implemented in this release pending your
decision.

### Problem 3 — Safety-net damage signal not wired to real detector evidence

`local_language_damage_index` was `0.0` on all three essays regardless of chargeable-error count (21 vs 2
vs 1) — it should be the pipeline's cheapest, most reliable indicator of "this essay actually has a lot of
grammar/lexical damage," and it currently carries no signal at all. `serious_error_sentence_ratio` moved
in the wrong direction: `0.0` for weak (21 chargeable errors including 9 `SUBJECT_VERB_AGREEMENT`) vs
`0.091` for strong (1 disputed `CLAUSE_STRUCTURE` flag, see Problem 4). Both values are read via `_first()`
from `pm_shared`/`sp_shared`/`det_shared`/`sem_sum` dict paths (`adapt_record()`, line ~617-629) — none of
which `det_vip_v18d_2.py`, `detector_to_errormap_v3_standalone.py`, or
`scorer_input_evidence_guard_standalone_v1_4_7.py` currently populate with a value derived from
chargeable-error density or severity. They fall through to scorer-side defaults that don't track the
essay at all.

**Fix**: `scorer_input_evidence_guard_standalone_v1_4_7.py` should compute and emit
`local_language_damage_index` and `serious_error_sentence_ratio` from its own chargeable-row data (it
already has `chargeable_for_scoring` rows with severity/family) before handing off to the scorer, e.g. a
simple weighted ratio of chargeable rows classified as high-severity local damage (SVA, verb form, clause
structure, spelling) over total sentence count. This keeps the "safety net" honest without requiring
Problem 2's larger rubric-source decision.

### Problem 4 — Detector false positive on subject + parenthetical + verb constructions

Strong essay's one surviving chargeable error:

> `"A mandatory scheme, by contrast, ensures that every student, regardless of background, is afforded
> the same formative exposure to civic responsibility."`
> flagged: `CLAUSE_STRUCTURE`, *"Unnecessary comma between subject and finite verb may break clause
> skeleton"* on the span `"background, is"`.

This is a standard non-restrictive parenthetical (`every student, regardless of background, is
afforded...`) — grammatically correct, not a clause-skeleton break. Notably, det_vip's own QA correctly
rejected 100% of spaCy's candidates (2/2) and 100% of LanguageTool's candidates (5/5) on this essay as
false positives (`source_contribution_audit`) — the rejection layer worked as intended for those two
engines. This one flag came through the rule-registry/LLM path instead.

**Fix**: `det_vip_v18d_2.py`'s `CLAUSE_STRUCTURE` rule family needs a guard for comma-bounded
non-restrictive parentheticals between subject and verb (a comma immediately preceded by a noun phrase
and immediately followed by a finite verb should not trigger if there is a matching opening comma earlier
in the same noun phrase span, e.g. `NP, <parenthetical>, VP`). This is a det_vip rule-level fix, out of
scope for the Gold orchestrator/scorer files owned in this spec — flagged here for tracking, not fixed in
v1.4.13.

### Problem 5 (diagnostic note, not a new bug) — `criteria_math_valid: false` is a symptom of Problem 2

Both medium and strong show `tier_governor.action: adjusted` (`pre_governor_overall: 6.0` →
`post_governor_overall: 5.0`) and `audit.criteria_math_valid: false` in the adjudicator output. The
governor lowers `overall_band` without touching the four `criteria_bands`, so `(5+5+6+5)/4 = 5.25` no
longer rounds to the released `5.0` under standard IELTS averaging. Once Problem 2 is fixed and
`task_response`/`coherence_cohesion` actually vary by essay, the tier governor should stop conflating
medium and strong (it currently applies the identical cap to both because it's reading identical flat
inputs) and this arithmetic mismatch should become rare. Until then, `premium_automated_adjudicator_v1_2.py`
should treat `criteria_math_valid: false` co-occurring with `reason_codes: ["large_governor_movement"]` as
an **expected, allowed** exception rather than a raw inconsistency flag — right now it has no way to
distinguish "governor did its job" from "real arithmetic bug," which is itself worth a small fix:

```python
# in the audit block construction
"criteria_math_valid": criteria_math_valid or (
    tier_governor_action == "adjusted" and "large_governor_movement" in reason_codes
),
"criteria_math_valid_note": "governor_adjusted_overall_without_rebalancing_criteria"
    if (not criteria_math_valid and tier_governor_action == "adjusted") else None,
```

### Problem 6 — LRET's `v1110` grammar-flag rule misfires on correct modal-passive sentences, and worsens with essay quality

LRET's `v1110_grammar_flag_sent_*` units (`unit_type: flagged_malformed_sentence_no_llm_repair_available`,
inside `clarify_units`) fire on a "malformed verb chain around 'be'" pattern. All three flagged instances
across the stress test are grammatically correct **modal + be + past participle** (passive voice)
constructions:

- weak: 0 flags
- medium: 1 flag — *"This is a fair concern, but it **can be addressed** by allowing flexible
  scheduling..."* (correct passive)
- strong: 2 flags — *"...whether unpaid community service **should be mandated**..."* and *"...genuine
  altruism **cannot be manufactured** through obligation..."* (both correct passives)

The flag count rises with essay sophistication (simple sentences in the weak essay contain fewer
modal-passive constructions to misfire on), so this rule actively penalizes the strongest writing the
most. Each flagged sentence is shown to the student as `"This sentence may have a grammar issue... Try
rewriting it yourself"` (`student_facing_task: true`) — i.e. a strong student is told their correct,
well-formed topic sentences are broken.

Separately, `clarify_reason` on every instance says *"no verified repair is available in this run (the
LLM-backed repair path was not used)"* — even though `gold_engine_commands_full_v1_4_12.json`'s
`lret_session` command already passes `--use-llm`. The `v1110` rule-flag path does not appear to consult
the same LLM-enabled flag the rest of LRET's suggestion generation uses; it always reports "no LLM" for
this specific check regardless of how the engine was invoked.

**Fix**: (a) tighten the `v1110` pattern so it excludes standard modal-passive (`MODAL + be + past
participle`) sequences — these are a normal, correct construction, not an error signature; (b) wire the
`v1110` path to the same `--use-llm` flag as the rest of `lret_engine_v1_12_0_meaning_sensitive_detector_families.py`,
so it either gets a real repair check or is suppressed when LLM is available, instead of always reporting
itself as LLM-less.

### Problem 7 — LRET's FIX suggestions can reduce precision, not just fix errors

Strong essay's single `FIX` unit flags `"civic equity"` (from *"...offers a rare opportunity to cultivate
both empathy and **civic equity** among the next generation"*) as a `LEXICAL_PRECISION` error and suggests
replacing it with `"civic engagement"` — validated and accepted through all of LRET's contextual-fit gates.
But `"civic equity"` is a deliberate, correct, and thematically load-bearing word choice: paragraph 3 of
this essay's entire argument is that compulsory service works as *"a powerful equalising mechanism"*
ensuring *"every student, regardless of background, is afforded the same formative exposure."* `"civic
equity"` in the conclusion directly closes that thread. `"civic engagement"` is a generic substitute that
drops the equity/equalizing-access connection the essay spent a paragraph building. The suggestion passes
every mechanical gate (`grammar_role_preserved`, `register_preserved_or_improved`, `no_topic_drift`, etc.)
because those gates check surface fit, not whether the replacement preserves the essay's specific
argumentative thread — something none of the current gates are designed to check.

**Fix**: no code change proposed here yet — this is a genuine gap in what "contextual fit" means for
`LEXICAL_PRECISION` FIX suggestions on already-strong essays; flagging for your input on whether it's worth
adding an argument-thread-preservation check, or whether FIX-class suggestions should simply have a
higher confidence bar (e.g. require corroboration from a second signal, not just LLM contextual-fit gates)
before being shown as "must repair" on essays with very few chargeable errors overall.

### Problem 8 — Writing Coach's `micro_lesson` ships empty teaching content

`07e_writing_coach_output.json.micro_lesson` on the strong essay run resolves to:

```json
{"skill_id": "formality_control", "title": "