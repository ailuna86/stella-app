# ST.ELLA Gold Pipeline — Architecture Spec v2

Status: **spec only, not yet implemented.** This document describes target architecture. Nothing described here has been built except where explicitly marked "(already fixed this session)."

Written after a live debugging session against real student data (`gold_web_sessions/student_a6b7ca6d-.../gold_20260719_212849_...`), not from assumptions about how the engines are supposed to work. Every defect cited below has a concrete example from that real run.

---

## 0. Design principles (apply to every engine, every file, no exceptions)

1. **Universal rules only. No essay-specific patterns.** A rule is only allowed to exist if you can state it without referencing a specific sentence, topic, or phrase from any one essay. Confirmed violation this session: `_line_explanation`/`_how_to_improve` in Writing Coach are chains of `if error_code == X: return "hardcoded sentence about government/health care"` — literally written against one practice essay's vocabulary. Same shape of bug, already fixed once this session in LRET (`_v165_promote_free_modifier_swaps`, retired rather than patched with a phrase list) — the fix in both cases is the same: replace pattern-matched literal text with logic that reads the actual signal (error type + the real words involved, which every engine already captures) and generates instruction from that, not from a lookup table of specific sentences.

2. **Standalone files, not scaffolds.** Every production engine file should be readable top-to-bottom as one coherent implementation of its current version — not a base implementation plus 5-12 sequential monkey-patch blocks (`_prevX = X; def newX(...): ...; ClassName.method = newX`) where only the last redefinition in file order is actually live and the earlier ones sit there as dead, misleading weight. This is not cosmetic: it caused a real mistake this session (editing a function that turned out to be shadowed by a later redefinition, requiring a second pass to find the actually-live copy). Every engine listed below should ship as a single, linear, current implementation. Historical patch reasoning belongs in a CHANGELOG file or git history, not as 300-line comment blocks preceding the code they explain, repeated at every subsequent redefinition site.

3. **Extraction and classification stay separated across engine boundaries**, per the existing (correct) design: Detector finds candidate errors, Evaluator extracts and scores rubric evidence, LRET classifies lexical units, Writing Coach/Essay Revision consume the classified output. No engine should re-derive from raw text what an upstream engine already computed and exported — every defect found this session involving "the right signal existed but wasn't used" (essay-wide hints ignored by the frontend, evaluator's `semantic_recoverability_status` not driving revision hint content, Detector's own arbitrated errors not reaching LRET's fix pool) was this principle being violated in the plumbing, not in any single engine's own logic.

4. **Every classification decision must be traceable to a concrete signal**, never to "no other branch matched, return generic text." Every hardcoded catch-all string found this session (in Writing Coach, in LRET's old CLARIFY path) was a symptom of a decision tree with insufficient branches papering over the gap with vague filler. The fix is never a better catch-all string — it's routing the case to the signal that should have driven it (an LLM call, an upstream field that was already computed, or an honest "insufficient signal" state that the frontend visibly marks as such rather than asserting confidence it doesn't have).

---

## 1. Model tier assignment

Verified directly against OpenAI's own model catalog (`developers.openai.com/api/docs/models`) as of this writing. Prices are per 1M tokens, input/output.

| Model | Model ID | Input | Output | Role |
|---|---|---|---|---|
| GPT-5.6 Sol | `gpt-5.6-sol` | $5 | $30 | Frontier — complex reasoning/judgment |
| GPT-5.6 Terra | `gpt-5.6-terra` | $2.50 | $15 | Balanced — most default LLM calls |
| GPT-5.6 Luna | `gpt-5.6-luna` | $1 | $6 | Cost-sensitive, high-volume |
| GPT-4o-mini | `gpt-4o-mini` | $0.15 | $0.60 | Current baseline across the pipeline |

"GPT-5 or more advanced" → **GPT-5.6 Sol is the current official flagship** (alias `gpt-5.6`). GPT-5.6 Terra is the "balance intelligence and cost" tier OpenAI itself recommends as the default when Sol isn't required.

Proposed assignment (this is the part that needs your sign-off before I touch any code, since it's real recurring cost):

Note: none of the model names below get written into any engine file by me. Both Detector and Evaluator already read their model from an environment variable (`VIP_CHEAP_MODEL`/`VIP_STRONG_MODEL`, `DEFAULT_MODEL`); you set the actual value in PowerShell. What follows is a recommendation, not a code change.

- **Detector** (`det_vip_v18d_2.py`): already has a working two-tier `VIP_CHEAP_MODEL`/`VIP_STRONG_MODEL` switch, both currently pointed at `gpt-4o-mini`. Recommendation: Terra for the Stage-7 audit judge (the one high-stakes decision point per essay), leave the cheap tier as-is for the high-volume L0-L3 detection passes.
- **Evaluator** (`va_premium_evaluator_v8_3_wke_standalone.py`, currently `gpt-4o-mini` via `DEFAULT_MODEL`): recommendation is Terra. This is the component every downstream engine's microskill evidence depends on — worth the upgrade regardless of the Detector-side miscategorization bug (which is Detector's, not Evaluator's — see 4.1/4.2).
- **Scorer** (`premium_unified_scorer_v1_4_1_fixed.py`) and **Verifier** (`premium_verifier_v1_4_3.py`): confirmed via grep, **neither currently makes any LLM call at all** — both are pure deterministic/rule-based. "Use a stronger model for scorer+verifier" isn't a config change, it's new integration work: deciding what judgment task in each engine would actually benefit from a model call, then building the call, the prompt, and the fallback path. Tell me if you want this scoped out as a real feature or if you'd rather leave these deterministic (there's a legitimate argument for keeping the final score/verification step non-LLM, for consistency/auditability reasons in a graded assessment context).
- **Writing Coach**, currently `gpt-4o-mini` across all 5 of its LLM flags (judge, response-quality, register-judge, correction-generator, upgrade-generator): leave as-is for now. It's the highest LLM-call-volume engine per essay (up to 5 calls per submitted item), and the fixes needed there (Section 4.3) are architectural, not model-strength issues — a stronger model won't fix a hardcoded phrase-matching fallback chain.
- **LRET**: see Section 3 — explicitly not a "bigger model" problem.

I have not run any of these against real essays yet. Cost estimate below is per-call token math, not a measured run.

---

## 2. Detector's lexical errors → fix pool (confirmed broken, not yet fixed)

**Scope, corrected: LEXICAL-family Detector errors only.** Grammar, coherence/cohesion, and task-response families stay entirely out of LRET's fix pool — LRET is a lexical-resource engine, not a general error router. Only Detector families that are lexical in nature (SPELLING, WORD_FORM, COLLOCATION, LEXICAL_PRECISION — final list needs confirmation against `FAMILY_TO_RUBRIC`'s `lexical_resource` bucket in `det_vip_v18d_2.py`) should ever reach `fix_units`. VERB_FORM, SUBJECT_VERB_AGREEMENT, ARTICLE_DETERMINER, COMPARATIVE_FORM, CLAUSE_STRUCTURE, and anything else Detector tags `rubric: grammar` or a CC/TR rubric never routes to LRET at all — that's Detector's/Writing Coach's/Essay Revision's territory, not LRET's.

Concrete finding: LRET's `--detector-output` ingestion (`load_detector_output()` in `lret_engine_v1_12_0_meaning_sensitive_detector_families.py`) looks for a JSON key called `validated_fix_candidates`, in four different possible nesting locations. Grepped the real errormap file (`01b_errormap_v3.json`) for that exact string: zero matches, anywhere. The real schema is a top-level `errors` array with `surface_quote`/`sentence_index`/`family`/`rubric`/`confidence`/`location.sentence` fields — a completely different shape than what the loader expects.

Practical effect: the "v1.8.0 Detector-errormap integration" feature (the comment block documents it as intentionally built to promote the Detector's own already-arbitrated errors straight into LRET's `fix_units`) has never actually run against real pipeline output. `DETECTOR_FIX_CANDIDATES` is empty on every real run. I confirmed this directly: on the test essay, LRET's own independent FIX detection found 4 units, none of which were the "modey" spelling error — while the Detector's real errormap had 18 arbitrated errors sitting right there, unused (only some of which — the lexical-family ones — should have reached LRET at all).

Fix (already partially done): I added a second, independent read of the real `errors` schema (`DETECTOR_ERROR_ROWS`) this session, but scoped it narrowly — only to stop LRET from marking a phrase KEEP when it shares a word with a Detector-confirmed error (this check is family-agnostic, which is fine, since it's a suppression check, not a fix-pool promotion). The fuller version, scoped correctly this time: filter `DETECTOR_ERROR_ROWS` to `rubric == "lexical_resource"` (equivalently, `family` in the lexical bucket) before routing anything into `fix_units`, replacing LRET's own weaker independent FIX detection for anything the Detector already caught in that lexical set, and falling back to LRET's own detection only for what the Detector didn't flag or flagged as non-lexical. Real scoped engineering work, not a one-liner — needs testing against several real essays' errormaps.

---

## 3. LRET: the "train a model" question — my actual recommendation

Corrected from the first draft: Option B below isn't a fresh proposal — you're already running it (`--llm-model` defaults to `gpt-5-nano` in the live CLI path, confirmed at the last of six redefinitions in the file, and matches what you said you're using). The real question isn't "should LRET use a cheap classifier model," it's "why is the already-cheap-classifier setup still unsatisfying," and that has a concrete architectural answer, not a bigger-model answer.

**Root cause found: classification and generation are coupled into one call.** `OpenAILRETSuggestionProvider.classify_and_suggest` sends every candidate to nano *once*, and that single call has to both decide the category (FIX/ENHANCE/KEEP/DROP/CLARIFY/EXPAND_SPAN — a constrained, closed-set judgment nano should be fine at) and write the actual suggestion text (an open-ended generation task, which is where quality-sensitivity actually lives, especially for ENHANCE). One model, one budget, doing two tasks of very different difficulty in the same breath. That's almost certainly why classification-adjacent behavior feels okay-ish but ENHANCE output quality doesn't.

**Fix: split the call, don't swap the model everywhere.** Keep nano for classification only, on every candidate (cheap, high-volume, that part is fine as-is). For whatever nano classifies FIX or ENHANCE — a small fraction of the total candidate pool, since KEEP/DROP/CLARIFY get filtered out first — send *only those* to a stronger model (Terra) for suggestion-text generation. Cost stays close to nano's per-essay baseline because the expensive path only ever sees the subset that actually needs generated text, not all 188 candidates.

This is testable without touching Detector/Evaluator at all, and without a labeled dataset or fine-tuning — it's a call-splitting change inside `OpenAILRETSuggestionProvider`, testable on the same essays already analyzed this session.

Separately, the retired `_v165_promote_free_modifier_swaps` mechanism (provably backward by construction, not just occasionally wrong) is proof that not every LRET defect is a model problem — some are pure logic gaps. Worth keeping both tracks open: logic fixes for defects that are structurally provable, and the classify/generate split for the "still not satisfied on output quality" complaint specifically.

---

## 4. Per-engine findings and target spec

### 4.1 Detector (`det_vip_v18d_2.py`, "18.d2")
- Current: two-tier CHEAP/STRONG model split already exists, both at `gpt-4o-mini`. `errors` array schema confirmed as the real, current output shape (used above). `rubric` is assigned deterministically from `family` via a static `FAMILY_TO_RUBRIC` lookup (confirmed at multiple call sites, e.g. lines 1167, 2097, 2142, 3092, 3134) — so `rubric` itself is never the bug; whatever `family` gets assigned upstream is what actually determines rubric.
- **Confirmed miscategorization, and this is Detector's bug, not Evaluator's** (corrected from the first draft, which wrongly attributed this to the Evaluator — see 4.2). Direct evidence from the real run's errormap: the phrase **"more stronger"** (a double comparative — a grammar error) appears twice — once correctly tagged `rubric: grammar, family: COMPARATIVE_FORM`, and once, inside the larger span "make a family more stronger," tagged `rubric: lexical_resource, family: COLLOCATION`. Same underlying mistake, charged against both criteria. Separately, **"Older peoples"/"young peoples"** (plural-of-an-already-plural-noun — a morphology/word-form error) is tagged `rubric: lexical_resource, family: COLLOCATION`, which isn't a pairing problem at all.
- Root cause, scoped but not yet located line-by-line: since `rubric` follows `family` deterministically, the actual bug is that Detector's collocation-checking pass is independently tagging a span as COLLOCATION without checking whether a grammar checker (COMPARATIVE_FORM, WORD_FORM/agreement) already claimed an overlapping span in the same sentence. This is a span-overlap/priority-resolution gap between two of Detector's internal checkers, not a rubric-mapping bug and not anything Evaluator touches. Target: when two checkers propose overlapping spans, grammar-shaped families (COMPARATIVE_FORM, agreement/word-form) should win over COLLOCATION for the overlapping region. Locating the exact overlap-resolution (or lack of it) between checkers is the next concrete investigation.
- Target for model tier: `VIP_STRONG_MODEL=gpt-5.6-terra` for the Stage-7 audit judge only (recommendation only — actual env var to be set by you, not written into the file).
- No other defects found this session (wasn't in scope — worth a dedicated pass later).

### 4.2 Evaluator (`va_premium_evaluator_v8_3_wke_standalone.py`, v8.3)
- **Corrected role description** (my first draft had this wrong): the Evaluator does not do error-family or rubric tagging at all — that's exclusively Detector's job. The Evaluator measures writing competence/skill/capacity as **microskills** — it evaluates each sentence, paragraph, and the whole text/idea as evidence of ~121 catalogued microskills (`skill_observation_profile`, e.g. `agreement_control`, `arg_claim_generation`, grouped under domains like "Grammar Production," "Argumentation," "Style & Reader Impact"). No families, no errors, no rubric — skill evidence only. The `evidence_graph.detector_evidence_sample` field that looked like Evaluator's own error analysis is literally just a sample of Detector's own rows passed through for visibility — not the Evaluator's output. That was the source of the earlier misattribution.
- The Evaluator's real second job: extracting lexical units for LRET (`lexical_unit_profile.lexical_units_for_lret`, 188 units on the test essay). Each carries a `candidate_route_hint` (FIX/ENHANCE/CLARIFY/KEEP) but explicitly under `"classification_policy": "extraction_only_lret_must_classify"` — the Evaluator proposes, LRET makes the final call. Worth confirming LRET actually weights these hints as a signal rather than ignoring them — not verified this session.
- **Microskill ontology and clustering registry: confirmed loading correctly.** Real run's `07_evaluator_output.json` metadata shows `ontology_files_loaded: VA_microskill_clustering_v3.json` (the file the evaluator's own v7.3b policy comment names as canonical), `ontology_skill_count: 121`. Not a wiring gap.

### 4.3 Writing Coach (`writing_coach_v1_2_17_freeze_candidate.py`, v1.2.17)
- Fixed this session: raw internal codes in "what went well"/"fix first" (translation table + description-preservation instead of discarding text); the upgrade-only explanation now uses the real per-sentence explanation instead of generic filler; grading timeout raised to 5 minutes; model sentence hidden behind a disclosure instead of always shown.
- **Not yet fixed, real remaining gap**: `_line_explanation`/`_how_to_improve` (used whenever a sentence has an actual correctness issue, which is most items) are still hardcoded per-topic pattern chains — the exact universal-rules violation described in principle #1. Target: replace with logic driven by the actual `issues` list content, which already contains real per-sentence text (e.g. `llm_flagged_issue: tense error: 'become' should be 'became'`) — generate the explanation from that data, not from matching against a fixed error-code list tied to one essay's vocabulary.

### 4.4 Essay Revision (`gold_revision_universal_engine_v1_7_1.py`, v1.7.1)
- **Paragraph-wide and essay-wide feedback**: confirmed this exists in engine output right now and is a real gap in the frontend, not the engine. `overall_revision_hints` (essay-wide: e.g. "Rewrite 5 red sentence(s) before improving style," plus paragraph-function-level notes) is computed by the engine but never read by `goldPipeline.ts`/the frontend at all. `paragraph_hint` (paragraph-wide) IS wired through already. This is a same-session, low-risk frontend fix (add `overallRevisionHints` to the `RevisionWorkspace` interface and render it, e.g. above the paragraph list) — I can do this immediately, separate from the bigger architecture item below.
- **Sentence-level suggestion content should weight recoverability/evaluability, not just detected errors** (your direct ask): confirmed the gap. `semantic_recoverability_status` and `function_status` are read from the Evaluator and used to set a sentence's red/yellow/green *color*, but hint *content* is built purely from Detector errormap rows, with only a generic fallback (which I improved, not redesigned, this session) when no specific error exists. Target: three distinct hint *types* driven by which signal is actually the problem — low recoverability → "a reader can't follow this, rewrite for clarity" (not a grammar note); fine recoverability but poor function/role fit → "this is clear but doesn't do what a [paragraph role] sentence needs to do"; specific errormap hit → today's targeted correction. This is a real, scoped rewrite of the hint-selection logic, not a patch — estimate: one focused engineering pass once you confirm the three-way split above matches what you had in mind.

### 4.5 Scorer, Verifier, Adjudicator (v1.4.1, v1.4.3, v1.2)
- No LLM calls found in any of the three. Deterministic/rule-based. Not otherwise investigated this session — no known defects, but also no depth of review to claim confidence either way.

---

## 5. Status (updated) and what's still open

Resolved this session, no longer open questions:
1. LRET's canonical resources (positive collocations, lexical registry, discourse markers) — confirmed loading successfully in real runs.
2. Evaluator's microskill ontology/clustering registry — confirmed loading correctly (121 skills from the canonical `VA_microskill_clustering_v3.json`).
3. Fix-pool routing scope — corrected to lexical-family-only (Section 2).
4. Detector-vs-Evaluator division of labor and the "more stronger"/"Older peoples" bug attribution — corrected, it's Detector's overlap-resolution gap (Section 4.1), Evaluator does microskill measurement only (Section 4.2).
5. LRET's classifier model — confirmed already `gpt-5-nano`; the real fix is splitting classification from generation into two calls (Section 3), not swapping models.
6. Scorer/Verifier/Adjudicator confirmed deterministic, no LLM calls in any of the three.

Done since the list above, all verified against real session data (re-ran the affected engines chained together, not just individually):
1. LRET classify/generate call-splitting (Section 3) — implemented in `OpenAILRETSuggestionProvider`; classification and suggestion-generation are two separate calls, model for the second controlled by `LRET_SUGGESTION_MODEL` (unset = today's behavior, unchanged).
2. Lexical-only Detector→LRET fix-pool routing (Section 2) — implemented; only SPELLING/WORD_FORM/COLLOCATION/WORD_CHOICE/REDUNDANCY/REGISTER/REPETITION/LEXICAL_PRECISION/SEMANTIC_COMBINATION route to LRET, every promoted suggestion passes the contextual-fit validator first (confirmed rejecting 2 of 5 candidates on the real test essay for not preserving meaning).
3. Detector's span-overlap/priority-resolution fix (4.1) — implemented in `detector_to_errormap_v3_standalone.py` (a genuinely standalone file, no rewrite needed there) as a final overlap-reconciliation pass; grammar wins over lexical_resource on overlapping spans. Confirmed catching both the "more stronger" case you named and a second, previously-unreported instance ("good ability to") on the same essay.
4. Real per-sentence function-fit signal in the Evaluator (Section 4.2 gap) — implemented in `va_premium_evaluator_v8_3_wke_standalone.py`; each sentence now gets its own role-fit check instead of inheriting the whole paragraph's status. Wired through to the essay-revision engine's hint text.

Still open, needs your call:
1. Whether Scorer/Verifier should get real LLM integration or stay deterministic — still your call, no strong recommendation either way from me (there's a legitimate audit/consistency argument for keeping the final score/verification step non-LLM).
2. None of today's fixes have been tested against a real OpenAI API call (this sandbox has no network path to OpenAI) — the LRET split was verified structurally with a mocked provider, not against real model output. Worth running one real essay through with your API key before trusting it in front of pilot users.
3. Deployment off local PowerShell (Railway/Render + Dockerfile) — offered, not yet built; still needs your go-ahead.
4. Writing Coach's `_line_explanation`/`_how_to_improve` hardcoded per-topic pattern chains (Section 4.3) — the one remaining known "universal rules" violation in the codebase, not yet fixed.
