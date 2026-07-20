# VA / ST.ELLA Gold Full Pipeline — Master Architecture Blueprint

**Document type:** Consolidated architecture reference + target-architecture design
**Covers:** Premium diagnostic/scoring core + Gold learning layer (Evaluator/WKE, LRET, Priority Engine, Writing Coach, Practice Engine, Progress Tracker, Essay Revision, Learning Intelligence)
**Baseline orchestrator:** `gold_full_pipeline_orchestrator_v1_4_7.py` (orchestration-only)

**Revision note (v3):** This version documents the pipeline as a **closed loop across essays**, not a single-essay report generator. Verification against the actual code turned up a structural gap: Gold's current orchestrator has no cross-essay continuity mechanism at all (no prior-profile reload, no persisted per-student profile, no previous-directive linkage) — unlike Premium, which already has this working. This blueprint now specifies the target design for that loop, a standalone Progress Tracker fed by Verifier, and an adaptive Practice Engine ported from Premium, per product decisions confirmed for this document. It also corrects two upstream contracts: Evaluator's primary input is the essay text itself (Detector/Scorer are optional enrichment, not the source), and LRET's primary inputs are Detector **and** Evaluator together, not Evaluator alone.

**Canonical learning-layer engine versions for this blueprint:**

| Engine | Canonical version | Status vs. current orchestrator wiring |
|---|---|---|
| Evaluator / WKE | **v8.3** | Not present as a file yet; orchestrator config currently calls `va_premium_evaluator_v7_3c_wke_lret_clean.py`. Treated here as the target version — see §9. |
| LRET | **v1.12.0** | Not present as a file yet. Closest available reference is `lret_engine_v1_11_0_visible_grammar_flags_and_truncated_vp.py`, whose later internal revision already adds a `--detector-output` argument alongside its Evaluator input — i.e. it already demonstrates the Detector+Evaluator dual-input design this blueprint specifies as canonical. Orchestrator config still points at `lret_engine_v1_4_6_universal_hybrid.py`, which takes Evaluator only. See §10. |
| Priority Engine | current (`priority_engine_v4_4_selfcontained.py`) | Code already supports Evaluator input (`evaluator_payload.strengths_profile`) and needs to also accept LIE history — the wired bridge does neither yet. See §5. |
| Writing Coach | **v1.2.17** (`writing_coach_v1_2_17_freeze_candidate.py`) | Present and frozen; orchestrator config still calls `writing_coach_v1_2_7_cli.py`. See §8. |
| Practice Engine | **target: ported from Premium's `practice_engine_v5c.py`** | Gold's current `gold_practice_session_builder_standalone_v1_4_3.py` only does static exercise-bank selection. Per product decision, Gold should get Premium's adaptive session engine (start → serve → collect → score → results), enriched with Evaluator's `practice_engine_payload`. See §11. |
| Progress Tracker | **target: new standalone engine, ported from Premium's `progress_tracker_v2_scorer_feed.py` / `progress_tracker_v5.py`, fed by Verifier** | Gold currently has no standalone Progress Tracker — snapshot generation is folded into the LIE/learner-profile builder. See §12. |
| Essay Revision | **v1.7.1** (`gold_revision_universal_engine_v1_7_1.py`) | Matches current wiring; confirmed intentionally independent of Feedback Engine (reads Detector + ErrorMap + Evaluator + Score Contract directly). See §13. |

---

## 1. What this pipeline is — a loop, not a report generator

The Gold pipeline is meant to run every time a student submits an essay, and each run should leave the system smarter about that student than it was before. The loop has two halves:

1. **Per-essay diagnosis and competence evaluation** — Detector, Scorer, Verifier, Adjudicator, Score Contract, and Evaluator/WKE. Evaluator's mandatory input is the essay text itself; Detector and Scorer sharpen its confidence but are not its primary source.
2. **Learning and continuity** — Priority Engine, Feedback Engine, LRET, Writing Coach, Practice Engine, Essay Revision, Progress Tracker, and Learning Intelligence (LIE). LIE is the hub: it persists a per-student profile after every essay, and that profile — plus the previous directive — feeds back into the *next* essay's Priority Engine, Directive, Practice Engine, and Writing Coach. This is what makes it a program, not a one-off report.

```text
 ESSAY N
    │
    ▼
 ┌─────────────────────────────────────────────────────────────┐
 │  DIAGNOSIS: Detector → Scorer → Verifier → Adjudicator →      │
 │  Score Contract                                                │
 └───────────────┬─────────────────────────────┬─────────────────┘
                 │ (essay text, primary)         │ (confirmed score)
                 ▼                               ▼
 ┌───────────────────────────────┐   ┌────────────────────────────┐
 │  EVALUATOR / WKE (v8.3)        │   │  PROGRESS TRACKER (new,     │
 │  essay text = mandatory input  │   │  §12) — fed by VERIFIER,    │
 │  Detector+Scorer = optional     │   │  records this essay's       │
 │  produces skill_observation_   │   │  confirmed score into        │
 │  profile + 6 consumer payloads │   │  per-student history         │
 └───────────────┬────────────────┘   └──────────────┬──────────────┘
                 │                                     │
                 ▼                                     │
 ┌─────────────────────────────────────────────┐       │
 │  PRIORITY ENGINE                              │      │
 │  reads Detector + Scorer + Evaluator          │      │
 │  + LIE history (prior profile, §14)            │      │
 └───────────────┬────────────────────────────────┘     │
                 ▼                                       │
           DIRECTIVE (+ previous-directive continuity)   │
                 │                                       │
   ┌─────────────┼───────────────┬─────────────────┐     │
   ▼             ▼               ▼                 ▼     │
 Feedback      LRET           Writing Coach     Practice  │
 Engine     (Detector +      (Priority +        Engine    │
            Evaluator)        Detector +        (adaptive,│
                               Evaluator,        §11, +LIE│
                               §8)               history) │
   │             │               │                 │      │
   └─────────────┴───────┬───────┴─────────────────┘      │
                         ▼                                │
          Essay Revision (independent — Detector +         │
          ErrorMap + Evaluator + Score Contract, §13)      │
                         │                                 │
                         ▼                                 │
   ┌─────────────────────────────────────────────┐         │
   │  LEARNING INTELLIGENCE (LIE) — the hub        │◀────────┘
   │  merges Feedback + LRET + Writing Coach +     │
   │  Practice + Essay Revision + this essay's      │
   │  evidence into the persisted per-student       │
   │  profile (§14)                                 │
   └───────────────┬────────────────────────────────┘
                    │  profile persisted to {student_id}_profile.json
                    ▼
       Skills Progress Report + Learning Roadmap +
       Progress Snapshot (reads history + updated profile)
                    │
                    ▼
        ═══════ STUDENT SUBMITS ESSAY N+1 ═══════
                    │
                    ▼
       prior_profile + previous_directive reloaded ──▶ back to Priority Engine
```

---

## 2. Premium vs. Gold — what's actually different

| Capability | Premium | Gold |
|---|---|---|
| Error detection | ✅ `det_vip_v18d_2.py` | ✅ same family (`detector_cli_v1_4_4.py`) |
| IELTS-style scoring | ✅ `premium_unified_scorer_v1_4_1_fixed.py` | ✅ same |
| Verification / adjudication | ✅ | ✅ |
| **Progress Tracker** | ✅ `progress_tracker_v2_scorer_feed.py` + `progress_tracker_v5.py`, fed by Verifier/Score Contract right after scoring | ❌ no standalone engine today — folded into the LIE/learner-profile builder. **Target: port Premium's engine, fed by Verifier** (§12). |
| **Cross-essay continuity** | ✅ `load_prior_learner_profile()`, `load_previous_directive()`, persisted `{student_id}_profile.json`, re-fed every run | ❌ does not exist in the orchestrator today. **Target: design this loop** (§14). |
| Priority / directive | ✅ (`pe_to_priority_directive_v2_v6.py`), directive continuity via `previous_directive` | ✅ (`directive_adapter_cli_v1_4_3.py`) — should also consume Evaluator + LIE history, currently doesn't (§5) |
| Generic feedback report | ✅ (`feedback_engine_v6_4.py` / v6c adapter) | ✅ same lineage |
| Practice | ✅ `PracticeEngineV5` — adaptive live session (start → serve → collect → score → results), reads directive + learner_profile | ✅ `gold_practice_session_builder` — static exercise-bank selection only, no session mechanics. **Target: port Premium's adaptive engine, enrich with Evaluator's `practice_engine_payload`** (§11). |
| Session-1 intake | ✅ `intake_assessment_v1.py` | ✅ same, wired as stage 0 |
| **Independent writing-competence evaluator (Evaluator/WKE)** | ❌ absent | ✅ reads essay text as primary source (Detector/Scorer optional); produces per-skill competence vectors and six consumer payloads that feed everything downstream |
| **Lexical Repair & Enhancement (LRET)** | ❌ absent | ✅ classifies lexical spans as KEEP / FIX / ENHANCE / CLARIFY, fed by Detector + Evaluator together |
| **Writing Coach (adaptive micromissions)** | ❌ absent | ✅ daily, one-skill, move-bank-driven mission engine; reads Priority + Evaluator + Detector together |
| **Essay Revision (original vs. revised vs. AI model)** | ❌ absent | ✅ three-way comparison engine, independently fed by Detector + Evaluator, second learner action |
| **Service routing** | ❌ absent | ✅ `gold_service_routing_builder` |

The diagnostic/scoring core is the same lineage in both. What Gold adds is Evaluator plus everything built on it — and what Gold is still missing, relative to Premium's own proven pattern, is the cross-essay continuity loop and a standalone Progress Tracker. Both are specified below as target design.

---

## 3. Architecture rule (applies to every stage)

- The **orchestrator coordinates only** — normalizes input, creates the session directory, runs external engines as subprocesses, validates artifacts, writes QA/manifest files.
- **Bridge/adapter files normalize contracts only** — translate one engine's output shape into another's expected input shape. They must not invent diagnosis, scores, or content.
- **Core engines own their own logic** — Detector owns error detection, Scorer owns IELTS banding, Evaluator/WKE owns competence evaluation, LRET owns lexical classification, Priority Engine owns limiter identification, Writing Coach owns mission selection, Practice Engine owns session mechanics, Progress Tracker owns trend history, Essay Revision owns the three-way comparison, LIE owns longitudinal learner state.
- No engine embeds essay-specific, topic-specific, or hardcoded sample content.

---

## 4. Evaluator/WKE — the central competence-evaluation engine

Verified directly against `va_premium_evaluator_v7_3c_wke_lret_clean.py` (canonical target: v8.3).

### 4.1 What it reads — essay text is primary, Detector/Scorer are optional

```python
class EvaluatorRequest(BaseModel):
    essay_text:            str                          # required — no default
    detector_output:       Optional[Dict[str, Any]] = None
    scorer_output:         Optional[Dict[str, Any]] = None
```

`essay_text` is the only mandatory content field. Core competence-extraction functions (`extract_text_maps`, `extract_lexical_units`, `baseline_features`, `extract_grammar_features`) all operate directly on the essay text and do not require Detector or Scorer. The engine's own docstring: *"Independent grammar surface analysis (no detector required)."* It self-reports which mode it ran in — `detector_scorer_assisted`, `detector_assisted`, or `essay_only`. The currently wired command runs it in the enriched `detector_scorer_assisted` mode, where Detector's confirmed error families sharpen competence vectors and Scorer output informs confidence — but the underlying read is Evaluator's own independent pass over the essay, not a reinterpretation of Detector's rows.

### 4.2 What it produces

- **`skill_observation_profile`** — a competence-vector observation per ontology skill (grammar, lexical, argumentation, cohesion, thinking, advanced lexical, etc.), each with `status`, `skill_signal`, `capacity_signal`, `priority_index`, `diagnostic_confidence`, `competence_vector`.
- **`evidence_graph`** — paragraph/sentence/argument/cohesion maps.
- **`lexical_unit_profile`** — cleaned candidate lexical spans (no KEEP/FIX/ENHANCE labels — that's LRET's job).
- **Six `consumer_payloads`, one per downstream engine:**

| Payload | Consumer |
|---|---|
| `writing_coach_payload` | Writing Coach |
| `lret_payload` | LRET |
| `practice_engine_payload` | Practice Engine |
| `essay_revision_payload` | Essay Revision |
| `learning_intelligence_payload` | LIE / learner profile |
| `progress_tracker_payload` | Progress Tracker |

### 4.3 What it explicitly does not do

Does not score IELTS bands, does not detect grammar errors (Detector's job — Evaluator only adds independent rule-based grammar *signal*), does not classify LRET units. Enforced by its own `boundary_policy` block and forbidden-field QA checks.

---

## 5. Priority Engine — needs two more inputs than it has today

Priority Engine's job is "what's most limiting in this essay, given everything we know about this student." That requires three things it doesn't currently receive in the wired pipeline:

| Input | Design intent | Verified actual state |
|---|---|---|
| Detector + Scorer | ✅ | **Matches.** `priority_input_builder_standalone_v1_4_7.py` supplies both. |
| Evaluator | ✅ | **Gap.** `priority_engine_v4_4_selfcontained.py` already has a read path — `extract_strengths_profile()` looks for `evaluator_payload.strengths_profile`, and `extract_semantic()` reads `layer0_5_semantic_recoverability` / semantic-summary fields — both are Evaluator-shaped data. But `priority_input_builder_standalone_v1_4_7.py` has no `--evaluator` argument, and `priority` runs at stage 11, before `evaluator` at stage 16 in `STAGE_ORDER`. Priority Engine's own code is ready; the wiring and ordering are not. |
| LIE / prior-essay history | ✅ (this is the continuity loop, §14) | **Gap — doesn't exist yet.** No bridge passes a persisted learner profile into Priority Engine or its input builder today. |

**Fix shape:** move `evaluator` earlier in `STAGE_ORDER` (it only needs `detector_for_evaluator` + `scorer`, both ready by stage 6), add an `--evaluator` argument to a v1.4.8-style `priority_input_builder`, and add a `--prior-profile` argument once the continuity loop (§14) exists.

---

## 6. LRET — fed by Detector **and** Evaluator together

Confirmed: this is a two-input relationship, not Evaluator alone.

- **Evaluator** supplies the `lret_payload` — cleaned, pre-classified candidate lexical spans, fix candidates, and a payload-quality profile.
- **Detector** supplies confirmed, concrete lexical-family error rows directly. The later internal revision of `lret_engine_v1_11_0_visible_grammar_flags_and_truncated_vp.py` already exposes a `--detector-output` argument alongside its primary `--input` (Evaluator) argument, confirming this dual-input design is already partially built, even though the currently wired `lret_engine_v1_4_6_universal_hybrid.py` only accepts `--input {evaluator}`.

LRET's job is unchanged regardless of exact version: classify each lexical unit as **KEEP / FIX / ENHANCE / CLARIFY**, restricted to lexical-only repair families (spelling, word form, collocation, register, redundancy, preposition pattern) — grammar-only families stay excluded by a blocklist. See §10 for the version-number housekeeping issue inside the v1.11.0 file.

---

## 7. Feedback Engine

Reads Directive + ErrorMap + Score Contract, same lineage as Premium's `generate_feedback_v2` / `feedback_engine_v6_4`. Produces the student-facing generic feedback report. Confirmed **not** a source for Essay Revision (§13) — each consumes upstream evidence independently rather than one downstream of the other, which keeps their logic from duplicating.

---

## 8. Writing Coach — two documented designs, one canonical build

- **`writing_coach_v1_gold_spec.md`** describes the aspirational, fully ontology-driven engine (142 microskills, 13 macro-domains, mastery state machine). Long-term target design.
- **`micro_writing_move_bank_v1_spec.md`** + the `writing_coach_v1_2_x` build line describe what's actually implemented: a **Micro-Writing Move Bank** of 40–60 reusable moves, each mapped to one primary microskill. **v1.2.17 (frozen candidate)** is the canonical current build.

Confirmed wiring: `writing_coach_v1_2_17_freeze_candidate.py` accepts `--priority`, `--evaluator`, and `--detector` as separate arguments, and the wired `writing_coach_raw` command passes all three simultaneously (plus errormap, scorer, verifier, adjudicated, score-contract, directive, feedback). It is not "Priority instead of Evaluator" — it receives both today.

Known gaps in the v1.2.17 freeze: content-aware grading exists for only 1 of 44 bank moves (the rest use a placeholder score); the hint-ladder policy hides the "upgrade" suggestion until the grammar fix is confirmed first (a deliberate v1.2.12 decision, not revisited). **Open item:** the orchestrator config still invokes `writing_coach_v1_2_7_cli.py` — needs updating to v1.2.17 plus a compatibility check against `micro_writing_move_bank_gold_v1.json`.

---

## 9. Evaluator — version gap

Canonical target **v8.3**; no `va_premium_evaluator_v8*` file exists yet. Currently wired: `va_premium_evaluator_v7_3c_wke_lret_clean.py`, whose own release note (v1.4.5) was scoped narrowly to LRET-payload cleaning — that narrow scope should not be read as Evaluator's full role, which is documented in full in §4. Track as a required external dependency, same pattern as prior LRET gaps.

---

## 10. LRET — version gap and internal versioning debt

Canonical target **v1.12.0**; no file with that version exists yet. The closest available reference is `lret_engine_v1_11_0_visible_grammar_flags_and_truncated_vp.py`, which already demonstrates the Detector+Evaluator dual-input pattern in its later internal revision (§6). Currently wired: `lret_engine_v1_4_6_universal_hybrid.py`, which only accepts Evaluator input.

Worth flagging: the v1.11.0 file's own docstring header says `"LRET Engine v1.6.2"`, its `ENGINE_VERSION` constant reads `"lret-engine-v1.4.2-universal-hybrid-anti-overfit"`, and the file itself appears to contain several sequential CLI-argument blocks at different line numbers (suggesting multiple historical revisions concatenated rather than a single clean version) — filename, docstring, and constant all disagree, and the argument surface actually grew across the file (`--detector-output`, `--disable-repeated-word-check`, `--disable-collocation-menu-check`, `--disable-grammar-flag-check`, `--disable-truncated-vp-fix` all appear only in later blocks). This is a housekeeping problem, not a functional one, but the orchestrator's v1.4.6 QA gate checks for the literal string `"1.4.6"` in LRET's output — that gate will need updating once v1.12.0 is wired, and the source file itself should probably be cleaned up to a single coherent version before that gate can be trusted.

---

## 11. Practice Engine — needs to be ported from Premium

Gold's current `gold_practice_session_builder_standalone_v1_4_3.py` only selects a static batch of exercises from `va_exercise_bank_v11d_approved.jsonl` by directive focus — it does not generate new exercises and has no session mechanics.

Premium's `practice_engine_v5c.py` (`PracticeEngineV5`) already runs a full adaptive live session:

```text
start_session(student_id, directive, learner_profile, session_id)
    → set_session_length(session_id, minutes)
    → loop: get_next_exercise(session_id) → submit_answer(...) → next
    → get_session_results(session_id)
    → submit_survey(...)
```

**Target design for Gold:** port this adaptive engine, keeping its session mechanics, and add two Gold-specific inputs it doesn't have today: Evaluator's `practice_engine_payload` (practice-relevant targets, practice-evidence-required signals, gap targets) for better exercise targeting, and the persisted LIE profile (§14) for continuity across sessions — Premium already passes `learner_profile=prior_profile` into `start_session()`, so this part of the pattern transfers directly.

---

## 12. Progress Tracker — needs to exist as its own engine, fed by Verifier

Gold has no standalone Progress Tracker today; snapshot/roadmap generation is folded into `gold_lie_profile_builder_standalone_v1_4_3.py`. Per product decision, Gold should get its own Progress Tracker, modeled on Premium's `progress_tracker_v2_scorer_feed.py` (`ProgressTrackerV2`) and `progress_tracker_v5.py`, with one correction to how Premium actually built it: **the recorded value should come from Verifier's confirmed score, not raw Scorer output.**

Premium's actual sequence (`pipeline_runner_v14l.py`) calls `ProgressTrackerV2.log_band_scores()` and `append_metric_history_v3()` immediately after Score Contract is built — using the adjudicated/confirmed band, gated by the contract's `progress_tracking_allowed` flag. For Gold, this recording step should sit **right after Verifier** (i.e., use Verifier's sanity-checked score, not the raw Scorer pass), still gated by Score Contract's `progress_tracking_allowed` flag, so a score that later fails adjudication doesn't pollute long-term trend history.

Progress Tracker has two touchpoints in the cycle, same as Premium's pattern:

1. **Recording** (early in the run): log this essay's verified score into per-student history (`band_history.jsonl`, `metric_history.jsonl`-equivalent).
2. **Reporting** (late in the run, after LIE update): read back accumulated history plus the just-updated LIE profile to build `band_trend`, `metric_trends`, `skill_progress_narratives`, and the final `progress_snapshot`.

---

## 13. Essay Revision — confirmed independent, second learner action

Essay Revision is deliberately **not** downstream of Feedback Engine. Its wired inputs are Detector + ErrorMap + Score Contract + Evaluator only (`--detector-output`, `--errormap-output`, `--score-contract`, `--evaluator-output` on `gold_revision_universal_engine_v1_7_1.py`). It builds its own revision-specific annotations and recommendations directly from that evidence rather than reusing Feedback Engine's report — confirmed as the intended design, avoiding duplicated logic between the two.

It is triggered as a **second, separate learner action** — after the student submits a revised essay — using `revision_launch_packet.json` as the hand-off contract from the original session:

```text
Original essay
    → Detector + Scorer + Evaluator
    → Revision workspace: annotated original + self-revision guidance
      (built from Detector + Evaluator, independent of Feedback Engine)
    → [student writes a revised essay — separate learner action]
    → Revision comparator: original vs. revised
    → AI model rewrite generated from the ORIGINAL essay only
      (student's revision used for comparison/reflection, never as generation source)
    → Three-way display: Original paragraph → Student revision → AI model rewrite
```

v1.7.1 is a stabilization release: fixes AI-comparison generation source (must be original, not revision), replaces generic paragraph-role comments with role-specific ones, adds a deterministic schema-fallback path so a student-visible model essay is always produced even if live LLM generation fails (provided it passes the 250–320 word / paragraph-role structure gate). Essay Revision's own output (revision comparison, improved/stable/deteriorated signal) feeds into LIE like every other learning-layer engine (§14).

---

## 14. The continuity loop — Learning Intelligence as the hub

This is the piece that was missing from the previous draft of this blueprint, and it's the reason a single-essay stage list can't answer "what happens on essay 2." Modeled directly on Premium's working implementation (`pipeline_runner_v14l.py`), extended for Gold's learning-layer engines.

### 14.1 What Premium already proves works

```python
# start of every run
prior_profile  = load_prior_learner_profile()      # reads {student_id}_profile.json
prev_directive = load_previous_directive()

# ... diagnosis, priority, directive (uses prev_directive for continuity) ...

# Practice Engine reads prior_profile directly
pe_engine.start_session(student_id, directive, learner_profile=prior_profile, session_id)

# end of every run, gated on lie_update_allowed
updated_profile = update_learner_profile_v4(
    student_id, session_id,
    priority_directive=directive,
    practice_session_result=psr,
    previous_learner_profile=prior_profile,
)
profile_path.write_text(json.dumps(updated_profile, ...))   # persisted for NEXT run
```

The profile is written to a fixed, discoverable per-student path every run and reloaded at the start of the next run. This is the entire mechanism — no database, no session server, just a persisted JSON keyed by `student_id`.

### 14.2 Target design for Gold

Extend the same mechanism, with Gold's additional learning-layer engines feeding the profile update and reading from 