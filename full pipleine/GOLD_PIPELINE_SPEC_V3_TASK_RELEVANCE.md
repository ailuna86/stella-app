# ST.ELLA Gold Pipeline — Task Relevance Ownership Spec v3

Status: **Section 2 (the primary Evaluator fix) is implemented. Section 3 (Detector safety-net flag + Scorer ceiling) is now also implemented** — see "3.1 Implemented (this pass)" below. Extends `GOLD_PIPELINE_SPEC_V2.md` — read that first for the design principles (Section 0) and the Detector/Evaluator division-of-labor findings (Section 4.1/4.2), both of which this document depends on directly.

**Implementation note, correcting one assumption in this document:** Section 2.1 originally said the evaluator command has no `--use-llm` flag "so today, in every run, these two skills are scored by the heuristics above with no model in the loop." Checked directly this pass: `evaluator_cli_bridge_standalone_v1_4_3.py`'s `build_request()` actually defaults `use_llm=not args.no_llm` — since the command config never passes `--no-llm`, the LLM refinement pass (`call_llm_refinement()`) was already running by default. The real, more precise bug: `_build_llm_payload()` (the function that builds what gets sent to that already-running LLM) only ever included `essay_text`, never `prompt_text` — so even with the LLM pass active, it had no way to know what the assigned prompt was and could only judge the essay's internal argument quality, never actual topical relevance. This is a narrower, more surgical bug than "the flag is off," and the fix below targets that precise gap.

Trigger: real production run scored a completely off-topic essay (submitted against "should university be free" but actually written about politicians' privacy) at `task_response: 6` raw / `5` official, `relevance_ratio: 0.9`, `task_schema_status: "complete"`. Root cause located in the live session-quality audit: `arg_claim_relevance` and `maintain_task_focus` — the two Evaluator microskills that feed `TR4_relevance_ratio` — never compare the essay to the prompt at all. They return full marks if the essay contains *any* argumentative claim, regardless of what it's about (`va_premium_evaluator_v8_3_wke_standalone.py`, lines 1773-1775 and 1898-1902).

---

## 1. The ownership question, answered

**Evaluator owns task-prompt relevance. Not Detector.** This isn't a new decision — it's what Section 4.2 of the V2 spec already established and this document is holding the line on: *"The Evaluator does not do error-family or rubric tagging at all — that's exclusively Detector's job. The Evaluator measures writing competence/skill/capacity as microskills."* Task Response, including relevance, is a microskill-evidence domain (TR1-TR7 are already Evaluator-sourced via `evaluator_rubric_bridge_v1.py`), not an error-span domain. Detector's job is finding candidate errors at the span/sentence level (grammar, lexical, mechanics) — it has no architectural business rendering a whole-essay semantic judgment like "is this on topic," and Design Principle #3 (extraction and classification stay separated across engine boundaries) says exactly that: don't let a second engine re-derive a judgment that belongs to one owner.

Concretely, this was never actually an architecture ambiguity — `evaluator_rubric_bridge_v1.py` already routes 100% of TR1-TR7 through Evaluator skill evidence, and the command config already passes `prompt_text` into the Evaluator (`extract_text_maps(req.essay_text, req.prompt_text or "")`, line 4707). The Evaluator was always the intended owner. **The bug is that its implementation of the two relevance-bearing microskills never uses the prompt it's already been handed.** Fixing ownership isn't the task; fixing the implementation inside the correct owner is.

That said, there is one legitimate role for Detector here — not as an alternate owner, but as a cheap safety net (Section 3 below), because the Evaluator's real fix requires an LLM call, and every LLM-dependent judgment in this pipeline needs a non-LLM fallback that fails safe rather than fails confident.

---

## 2. Fix inside the Evaluator (primary fix, does the real work)

### 2.1 What's broken today
- `arg_claim_relevance` (line ~1773): `return DEPTH_2 if claims else DEPTH_0` — counts claims, never reads `prompt_text`.
- `maintain_task_focus`: falls through to `_depth_generic`'s `"task"/"prompt"/"component"` keyword branch (line ~1898), which checks `claim_candidate_count >= 3 and paragraph_count >= 3` — again structural, never semantic.
- The Evaluator stage's command definition (`gold_engine_commands_full_v1_4_13.json`, `"evaluator"` key) has no `--use-llm` flag at all, unlike `lret_session`, which does. So today, in every run, these two skills are scored by the heuristics above with no model in the loop — this isn't a today-only failure mode, it's the permanent behavior until this ships.

### 2.2 Target implementation
Add a real semantic comparison, gated on an LLM call, specifically for the task-understanding skill group (`arg_claim_relevance`, `maintain_task_focus`, and any other skill matched by the existing `"task","genre","purpose","audience","prompt","component"` keyword branch in `_depth_generic`):

- New function, e.g. `_depth_task_relevance(maps, base, prompt_text)`, called instead of the current claim-counting path when `prompt_text` is non-empty and `--use-llm` is set.
- LLM call: give the model the prompt and the essay's extracted claims (`maps["argument_map"]["claim_candidates"]`, already computed — no new extraction work needed), ask for a graded judgment: does each claim address the assigned prompt, tangential, or unrelated — plus one overall on-topic/partially-on-topic/off-topic call for the essay as a whole. Map that to DEPTH_0 (off-topic) / DEPTH_1 (tangential/partial) / DEPTH_2 (on-topic) / DEPTH_3 reserved for on-topic-with-nuanced-engagement, consistent with the existing DEPTH_0..3 scale used everywhere else in this file.
- No-LLM fallback: do **not** default to DEPTH_2 (today's effective behavior). Default to DEPTH_1 with an explicit `"relevance_confidence": "low_no_llm_check_ran"` flag surfaced in the output, so downstream consumers (Scorer, Feedback Report) can visibly distinguish "verified on-topic" from "we didn't actually check." This is the same "honest insufficient-signal state" principle already established in V2 Principle #4.
- Turn on `--use-llm` for the `"evaluator"` command in `gold_engine_commands_full_v1_4_13.json`. This also finally activates the Evaluator's existing but currently-inert LLM path for every other microskill that isn't heuristic-only — worth confirming that doesn't regress anything else before shipping (see Section 5, testing plan).

### 2.3 Model tier
Per V2 Section 1, Evaluator's recommended tier is already Terra (`gpt-5.6-terra`), not the cheap tier — this is the one job in the whole pipeline that most needs a model actually reading the prompt side-by-side with the essay, so it should not be downgraded to a cheaper tier even under cost pressure. If cost forces a cheaper model somewhere, this is the wrong place to cut it.

### 2.4 Implemented (this pass) — what actually shipped, vs. 2.2's original plan
Section 2.2's plan (a new dedicated `_depth_task_relevance` function, a separate LLM call) turned out to be more machinery than necessary once the existing `call_llm_refinement()` infrastructure was inspected directly — that pass already sends selected skills to an LLM with a pre-rule ceiling it can only lower, already covers both `arg_claim_relevance` (domain "Argumentation") and `maintain_task_focus` (domain "Task Understanding," both in `_select_skills_for_llm`'s eligible-domain list), and already runs by default (see the implementation note at the top of this document). The actual fix was three smaller, more surgical changes inside `va_premium_evaluator_v8_3_wke_standalone.py`:

1. **`_build_llm_payload()`** now accepts and forwards `prompt_text`, included in the LLM request as `assigned_prompt` (truncated to 1500 chars) — previously this function only ever sent `essay_text`, so the already-running LLM pass had no prompt to compare against. `call_llm_refinement()` now passes `req.prompt_text` through.
2. **`LLM_SYSTEM_V72`** (the system prompt for that LLM pass) gained an explicit rule 7: for task-relevance skills specifically, compare the essay's actual content against `assigned_prompt`, not just argument quality — off-topic essays must score DEPTH_0 regardless of how well-written they are, tangential engagement scores DEPTH_1, and only genuine engagement with what the prompt actually asks scores DEPTH_2.
3. **The heuristic ceilings were lowered from DEPTH_2 to DEPTH_1** for both skills — `arg_claim_relevance` in `_depth_claim()`, and a new dedicated `maintain_task_focus` branch carved out of `estimate_skill_depth()`'s dispatcher (previously it fell into `_depth_generic`'s shared "task/genre/purpose/prompt" keyword bucket, which also handles genuinely structural skills like `identify_genre` that don't need a relevance check — carving out just this one skill_id avoids under-scoring those unrelated skills). This matters as a safety net independent of the LLM fix: since the LLM can only lower a ceiling, never raise it, a DEPTH_2 ceiling silently stays DEPTH_2 whenever the LLM pass doesn't touch a given skill for any reason (missing API key, `--no-llm`, selection cap). DEPTH_1 is the honest default now — "unverified," not "confirmed relevant."

Not yet re-run against a real essay with a live OpenAI key — syntax-checked clean, logic traced by hand against the real dispatch chain, but the actual LLM judgment quality (does it correctly call the audited off-topic essay DEPTH_0?) needs a real API call to confirm, per Section 5's testing plan below.

**Correction, file convention:** this fix now lives in a new file, `va_premium_evaluator_v8_4_wke_standalone.py` (`ENGINE_ID: VA_PREMIUM_EVALUATOR_WKE_V8_4`) — the original `va_premium_evaluator_v8_3_wke_standalone.py` was left untouched, per project convention (never edit existing engine files in place; write new versioned ones). `gold_engine_commands_full_v1_4_14.json` (also new, `v1_4_13` untouched) points the `evaluator` stage at the new file, and `lib/server/goldPipeline.ts`'s `ENGINE_CONFIG` constant was updated to reference `v1_4_14` — that frontend pointer-update is the one in-place edit involved, since the "write new versions" rule applies to the Python engines specifically, not the Next.js app.

---

## 3. Cheap safety-net flag in Detector (secondary, not a duplicate owner)

Rationale: the Evaluator's real fix depends on an LLM call succeeding. If that call fails, times out, or `--use-llm` is later turned off for cost reasons on some tier, there is currently zero protection against exactly the failure mode that triggered this spec. A second, independent, non-LLM (or cheap-LLM) tripwire closes that gap without making Detector a second owner of the TR rubric.

- New Detector-level signal, e.g. `topic_alignment_risk_flag` (boolean + confidence), computed once per essay from a cheap check — embedding cosine similarity between essay text and prompt text, or a single fast classification call on the cheap tier (`VIP_CHEAP_MODEL`) asking only "same topic, yes/no." This is a candidate signal, the same shape as every other error candidate Detector already emits — it does not render a graded TR judgment, it raises a flag for something else to act on. Consistent with Design Principle #3.
- This flag flows into `errormap` (same path every other Detector signal already takes) and becomes visible to Priority Engine, Directive, and Feedback Report — meaning a genuinely off-topic essay can trigger an explicit, visible "this may not address the assigned prompt" warning to the student, not just a silently wrong number. That visibility is arguably as important as the score fix itself.
- Scorer consumes this flag as a hard ceiling: if `topic_alignment_risk_flag` is true (regardless of what the Evaluator's own relevance judgment came back with), cap `task_response` at band 2-3. This protects the score even if the Evaluator's LLM check is unavailable or itself wrong.

This is a small, scoped addition to Detector — it does not touch `family`/`rubric` tagging logic (Section 4.1 of V2) and should not grow into a second relevance-scoring implementation. One boolean flag, one consumer (Scorer's ceiling logic), nothing else.

### 3.1 Implemented (this pass)

Per the confirmed project rule (Python engines are never edited in place — new versioned files only), this shipped as four new files, none of the v1.4.14-referenced originals touched:

1. **`det_vip_v18d_3_topic_alignment_risk.py`** (was `det_vip_v18d_2.py`) — new `detect_topic_alignment_risk(prompt_text, essay_text, tracker, llm_enabled)` function, called right after `idea_map` is computed inside `analyze()`. One cheap `CHEAP_MODEL` JSON call asking only "does the essay address the topic of the prompt" (subject matter only, explicitly not writing quality) — returns `{"checked", "risk_flag", "confidence", "same_topic", "reason"}`. Fails safe (`risk_flag: false`) on missing prompt/essay text, `llm_enabled=False`, or an unparseable response — never blocks or penalizes an essay on an inconclusive check. Surfaced as a new top-level `topic_alignment_risk` key on every per-essay result (both the normal-completion return and the word-limit-reject early return, so downstream consumers never see a missing key).
2. **`det_vip_cli_bridge_v1_1.py`** (was `det_vip_cli_bridge_v1.py`) — only change is importing `PremiumDetectorV9` from the new module; `build_bridge_output()`'s `result = dict(det_result)` already keeps every native det_vip key, so the new field needed no further wiring here.
3. **`detector_to_errormap_v3_1_standalone.py`** (was `detector_to_errormap_v3_standalone.py`) — new `_extract_topic_alignment_risk()` helper, called from `build_errormap()`, adds `"topic_alignment_risk"` as a new top-level key on the returned errormap dict. This file needed an explicit change (unlike the two below) because `build_errormap()` builds its return value from scratch rather than passing the input through — it does not natively carry forward unrecognized top-level keys the way the other stages do.
4. **`premium_unified_scorer_v1_4_2_topic_ceiling.py`** (was `premium_unified_scorer_v1_4_1_fixed.py`) — new `_extract_topic_alignment_risk()` reads the flag straight off `score_input.upstream_record` (confirmed via direct code read that it survives the full `detector_for_scorer` / `evaluator_rubric_bridge` chain as a permissive pass-through — `scorer_input_evidence_guard_standalone_v1_4_7.py`'s `enrich_detector()` does `copy.deepcopy(detector)`, and `evaluator_rubric_bridge_v1.py`'s `apply_to_record()` mutates the same record objects in place and writes back the full `detector_for_scorer` — neither strips unrecognized keys, so zero changes were needed to either file). If `risk_flag` is true at confidence ≥ 0.55, `task_response` is hard-capped at band 3 and `overall_band` is recomputed from the four (now-capped) criteria via the scorer's own `_overall_half()`.

   Deliberately **not** wired into the existing `_infer_task_schema_status()` / `resolve_task_status()` / `TASK_TRUE_FAIL_HARD` machinery, even though that machinery already has a `tr_cap` concept for a `task_schema_status == "true_fail"` state. That machinery's `high_review` / `clean6` / `soft55` rescue paths exist specifically to protect against a *different* failure mode — a false-positive structural coverage fail on an otherwise strong, genuinely on-topic essay — and would rescue exactly the well-written-but-off-topic case this ceiling exists to catch, defeating "regardless of what the Evaluator's own relevance judgment came back with." The ceiling is instead applied as an unconditional post-processing step, right after `apply_tier_governor()`, with its own decision surfaced separately as `topic_alignment_risk_ceiling` in the scorer's output for auditability.

   Verified with a synthetic record (no live OpenAI call): an essay that would otherwise score `task_response=7` / `overall=6.5` is correctly capped to `task_response=3` / `overall=5.5` when the flag fires at confidence 0.88; a `risk_flag=False` signal and a below-threshold-confidence signal (0.4) both correctly leave the score untouched; no signal at all is identical to pre-fix behavior. Not yet run against a real essay through a live LLM call — same caveat as Section 2.4's item.

New `gold_engine_commands_full_v1_4_15.json` wires all three consumer-side changes in (`detector` → bridge v1_1, `errormap` → errormap v3_1, `scorer` → scorer v1_4_2); `lib/server/goldPipeline.ts`'s `ENGINE_CONFIG` (frontend, edited in place per the same confirmed convention) now points at v1_4_15.

---

## 4. Responsibility matrix (task-relevance-adjacent checks specifically)

| Check | Owner | Mechanism | Status |
|---|---|---|---|
| TR1 prompt-part coverage (did the essay address all parts of a multi-part prompt) | Evaluator | `identify_required_components` skill, structural — already reasonably suited to heuristics since it's about counting addressed sub-parts, not topic match | not audited this pass, assumed working per V2 |
| TR2/TR3 position clarity/consistency | Evaluator | `thesis_construction`/`arg_position_consistency` skills | not audited this pass |
| **TR4 relevance-to-prompt (the bug)** | **Evaluator** | `arg_claim_relevance` + `maintain_task_focus`, needs the Section 2 fix | **broken today, fix specified above** |
| Catastrophic off-topic tripwire | **Detector** (new) | cheap `CHEAP_MODEL` classification flag, feeds Scorer ceiling | **implemented, Section 3.1** (`det_vip_v18d_3_topic_alignment_risk.py`) |
| Hard score ceiling on flagged essays | Scorer | consumes Detector's flag unconditionally (independent of Evaluator's own TR4 judgment) | **implemented, Section 3.1** (`premium_unified_scorer_v1_4_2_topic_ceiling.py`) |
| Visible student-facing warning | Feedback Report / Priority-Directive | flag now flows through `errormap` (`detector_to_errormap_v3_1_standalone.py`); Feedback Report/Directive UI surfacing of it is a separate, not-yet-built follow-up | **flag reaches errormap; UI surfacing not built** |

---

## 5. Testing plan before shipping

1. Re-run the actual off-topic essay from the audit through the fixed Evaluator (LLM on) and confirm `arg_claim_relevance`/`maintain_task_focus` return DEPTH_0 or DEPTH_1, not DEPTH_2.
2. Run the existing weak/medium/strong stress-test essays (already on disk under `gold_sessions/stress_*`) through the same fix and confirm no regression — these are all on-topic essays of varying quality, and the fix must not lower their scores; it should only affect essays that are actually off-topic.
3. Build one deliberately tangential (not fully off-topic, but weak engagement) essay as a fourth stress case, to confirm DEPTH_1 triggers correctly at the partial-relevance boundary, not just the binary on/off-topic case.
4. Confirm the no-LLM fallback path (Section 2.2) by forcing `--use-llm` off and checking the output surfaces `low_no_llm_check_ran` rather than silently defaulting to full marks.
5. Confirm Detector's new flag (Section 3) fires on the off-topic essay independently of whether the Evaluator's LLM call succeeds — test by disabling the Evaluator's LLM call for that one run and confirming the Scorer ceiling still applies via Detector's flag alone.

Section 2 and Section 3 are both now implemented in code (new versioned files, wired via `gold_engine_commands_full_v1_4_15.json`) and syntax/logic-verified by hand and with synthetic-record tests, but **neither has been run against a real OpenAI call yet** — that live-LLM verification (items 1-5 above) is the remaining step before this can be called fully shipped, same caveat as V2 Section 5, item 2.
