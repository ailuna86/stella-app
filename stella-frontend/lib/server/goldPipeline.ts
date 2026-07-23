// Bridges the web app to the Gold full pipeline orchestrator
// (gold_full_pipeline_orchestrator_v1_4_9.py + gold_engine_commands_full_v1_4_13.json),
// the hardened engine (Problems 1-9c + lexical_resource fix all verified live
// against weak/medium/strong stress essays — see the v1.4.13 verification
// reports). Gold-tier users only; Premium/premium_pilot still use
// pipeline_runner_v14j.py via pipeline.ts.
//
// IMPORTANT SCHEMA NOTE: the Gold orchestrator's 06_feedback_report_v6c.json
// does NOT share a schema with the older pipeline's file of the same name
// (they're produced by different versions of feedback_engine_v6c_cli.py).
// Gold's real shape is: performance_summary.overall_band,
// performance_summary.criteria_bands.{task_response,coherence_cohesion,
// lexical_resource,grammar}, top_learning_priorities[], focus_area_feedback[]
// shaped {status,capacity_domain,skill_tag,criterion,title,priority_reason,
// summary,examples[]}. mapGoldReportToFeedbackReport() below translates this
// into the app's FeedbackReport type so ReportView.tsx doesn't need to know
// which engine produced the report. This mapping involves a few judgment
// calls (noted inline) that are worth a human sanity-check against a real
// rendered report before the pilot goes live with real students.
import { spawn } from "child_process";
import fs from "fs";
import path from "path";
import type { AnnotatedError, FeedbackReport, FocusArea } from "@/lib/types";
import { rubricTarget } from "@/lib/types";
import {
  practiceResultsFor,
  missionResultsFor,
  submissionsFor,
  recordLearnerProfileRefreshAttempt,
  setSubmissionTopic,
} from "@/lib/server/store";

export const GOLD_PIPELINE_DIR =
  process.env.STELLA_GOLD_PIPELINE_DIR ?? path.resolve(process.cwd(), "..", "full_gold_v1");

const ORCHESTRATOR = "gold_full_pipeline_orchestrator_v1_4_9.py";
// v1.4.14: bumped from v1_4_13 to pick up two new versioned engine files
// (va_premium_evaluator_v8_4_wke_standalone.py, lret_engine_v1_12_1_...) that
// fix the session-audit Task Response relevance bug and the LRET FIX/KEEP
// duplicate bug. Per project convention, the fixes were written as new
// versioned engine files rather than edits to the v8_3/v1_12_0 originals —
// this config bump is what actually points the orchestrator at them.
// v1.4.15: bumped from v1_4_14 to pick up GOLD_PIPELINE_SPEC_V3_TASK_RELEVANCE.md
// Section 3 — the Detector-side topic_alignment_risk safety-net flag (a cheap,
// LLM-call-independent tripwire for genuinely off-topic essays) plus the
// Scorer's hard ceiling that consumes it, regardless of the Evaluator's own
// relevance judgment. Three new versioned engine files this bump:
// det_vip_cli_bridge_v1_1.py (wraps new det_vip_v18d_3_topic_alignment_risk.py),
// detector_to_errormap_v3_1_standalone.py (surfaces the flag on the errormap
// artifact so it's visible to Priority/Directive/Feedback Report), and
// premium_unified_scorer_v1_4_2_topic_ceiling.py (applies the cap).
// v1.4.16: bumped from v1_4_15 to register the new Vocabulary Coach (LRET +
// PEEL) engines and bump learner_profile to gold_lie_profile_builder_standalone_v1_4_4.py
// (adds an additive, optional --vocabulary-coach input). Per the same
// reasoning as mission_response_grading below, Vocabulary Coach's three
// engines (vocab_coach_selection_engine_v1_2.py as of the academic-words
// build below, vocab_coach_response_grader_v1_1.py,
// vocab_coach_ledger_update_v1_1.py) are registered in this config for
// documentation/consistency but are NOT part of the orchestrator's automatic
// per-essay STAGE_ORDER — they run on their own cooldown-gated cadence via
// the standalone runVocabCoach* functions below, not through a full
// 27-stage orchestrator invocation.
// v1.4.17: bumped from v1_4_16 to pick up
// gold_lie_profile_builder_standalone_v1_4_5.py's roadmap fix -- the
// 3-phase "learning_roadmap" it writes (study-plan/page.tsx's data source)
// never included a Vocabulary Coach step at all, despite v1_4_4 already
// accepting --vocabulary-coach as an input. Reported directly by the user
// against the live deployment ("I don't see vocabulary coach at all" on
// /study-plan). v1_4_5 inserts a real vocabulary_coach phase between
// practice and essay_revision; study-plan.ts's SERVICE_LABELS/SERVICE_ICONS
// and study-plan/page.tsx's href/button-label switch were updated to match.
// v1.4.18 (2026-07-23): bumped from v1_4_17 to pick up
// lret_engine_v1_13_0_llm_verified_clarify_and_enhance.py (LRET accuracy
// audit fix pack, see LRET_Accuracy_Audit_Findings_v1.docx). Per project
// convention the fixes were written as a new versioned engine file rather
// than edits to v1_12_1 -- this config bump is what actually points
// lret_session at it. --detector-output was already wired to {errormap}
// (01b_errormap_v3.json) in v1_4_13 onward and needed no change here --
// verified directly that 01b_errormap_v3.json (not 01_detector_output.json,
// which lacks the top-level "errors" array the engine's
// _v1610_suppress_keep_units_with_detector_errors() reads) is the file that
// actually makes the Detector-KEEP reconciliation fire. v1_13_0 added: the
// FIX->ENHANCE low-corroboration demotion no longer silently vanishes from
// output; the same-headword-registry-collocate CLARIFY mechanism now
// requires a scoped LLM vagueness confirmation instead of firing on any
// registry coincidence; ENHANCE substitutions get a final LLM grammar/
// naturalness check.
// v1_4_19 (2026-07-23): bumped lret_session to v1_13_1 -- the ENHANCE
// naturalness check above was fail-open (no OPENAI_API_KEY, or one
// candidate's LLM call failing, both left that suggestion shown
// unverified -- exactly the ungrammatical substitutions Fix 4 exists to
// catch). Now fails closed, same posture as the CLARIFY check: no verified
// judgment means no suggestion shown. Verified directly (not just read):
// ran _v1130_apply_enhance_naturalness_check with no API key -> every
// candidate dropped; ran it with a mocked partial-failure LLM response
// (one candidate judged, others returning no verdict) -> only the
// unverified ones were dropped, the verified-good one survived.
// v1_4_20 (2026-07-23): bumped to wire the LRET/Vocabulary-Coach-to-Priority-Engine
// cross-engine signal (Pipeline_Frontend_Spec_v2.docx section 6, LRET_v2_Spec.docx
// section 5.3) -- the audit-fix prerequisite this was blocked on (lret_engine_v1_13_1
// above) is now done. priority_input bumps to priority_input_builder_standalone_v1_4_9.py
// and gains --lret {prior_context} + --vocab-ledger
// {learner_profiles_dir}/{student_id}_vocab_coach_ledger.json (both optional/additive).
// --lret intentionally receives {prior_context}, not this run's own {lret_session}:
// checked directly in gold_full_pipeline_orchestrator_v1_4_9.py's STAGE_ORDER --
// priority_input is stage 16, lret_session is stage 22, so this essay's own LRET pass
// has not happened yet when priority_input runs; only the PRIOR essay's signal
// (carried forward via {prior_context}, already produced at stage 1) is actually
// available at this point. learner_profile bumps to
// gold_lie_profile_builder_standalone_v1_4_6.py, which adds one additive
// lexical_skill_signals field so THIS essay's LRET signal survives into the persisted
// profile for the NEXT essay's priority_input step to read. Verified: Priority Engine's
// extract_strengths_profile() genuinely reads the new evaluator_payload.strengths_profile
// entries this produces. NOT yet verified/wired (as of v1.4.20): priority_output_normalizer_standalone.py's
// focus_areas (what Directive/Writing Coach mission selection/LIE's next_best_action
// actually consume) is built only from ErrorMap and does not read Priority Engine's raw
// output at all -- so this signal does not yet change recommended_service. That is a
// separate, still-open gap this bump does not close (see
// priority_input_builder_standalone_v1_4_9.py's module docstring for the full trace).
// v23 (2026-07-23): closes the gap noted immediately above. priority_normalized now runs
// priority_output_normalizer_standalone_v1_4_4.py, which adds build_focus_from_evaluator()
// (reads consumer_payloads.writing_coach_payload.development_target_signals/gap_signals --
// confirmed against a real 07_evaluator_output.json; this is Evaluator's own holistic
// competence judgment, e.g. weak argumentation/organization, which ErrorMap alone would
// never surface) and build_focus_from_lexical_signal() (reads priority_input's
// lexical_coach_signal.families -- confirmed Priority Engine's own raw output does NOT
// carry this field at all, only the strength side reaches it via strengths_profile, so a
// new --priority-input arg was required, not just --priority). Both are merged with the
// pre-existing build_focus_from_errormap() into one ranked focus_areas list without
// conflating the three taxonomies (see that file's module docstring for the full
// merge/rank strategy). Verified end-to-end on a real session
// (gold_20260719_212849_sub_1784489329011_..._ed924a2c): focus_areas grew from 2
// (ErrorMap-only: sentence_control, lexical_precision) to 6, adding two genuinely new
// Evaluator-derived weaknesses (Organization, Argumentation) and two lexical-signal
// entries, and directive_adapter_cli_v1_4_3.py consumed the result correctly. Config
// bumps to gold_engine_commands_full_v1_4_21.json (adds --evaluator {evaluator} and
// --priority-input {priority_input} to the priority_normalized command; both artifacts
// already exist by that point in STAGE_ORDER).
const ENGINE_CONFIG =
  process.env.STELLA_GOLD_ENGINE_CONFIG ?? "gold_engine_commands_full_v1_4_21.json";
export const OUTPUT_ROOT = "gold_web_sessions";
// The orchestrator's --canonical-resources-dir used to default to a
// hardcoded absolute path baked into gold_full_pipeline_orchestrator_v1_4_9.py
// (tied to one specific machine). Fixed there to read
// STELLA_CANONICAL_RESOURCES_DIR; passed through explicitly here so any
// deployment only has to set the one env var, rather than relying on a
// Python-side default that won't exist on a new host.
const CANONICAL_RESOURCES_DIR = process.env.STELLA_CANONICAL_RESOURCES_DIR;
const TIMEOUT_MS = 15 * 60 * 1000; // measured real runs: 480-535s with LLM stages enabled

interface GoldFeedbackReportRaw {
  schema_version: string;
  created_at: string;
  performance_summary: {
    overall_band: number;
    criteria_bands: {
      task_response: number;
      coherence_cohesion: number;
      lexical_resource: number;
      grammar: number;
    };
    score_confidence: string; // "normal" | ...
    adjudication_status: string; // "confirmed" | ...
  };
  top_learning_priorities: Array<{
    capacity_domain: string;
    title: string;
    why_this_matters: string;
    next_step: string;
    example_count: number;
  }>;
  focus_area_feedback: Array<{
    status: string;
    capacity_domain: string;
    skill_tag: string;
    criterion: string;
    title: string;
    priority_reason: string;
    summary: string;
    next_step?: string;
    examples: Array<{
      error_id: string;
      sentence_index: number;
      criterion: string;
      family: string;
      surface_quote: string;
      suggested_revision: string;
      severity: string;
      confidence: number;
      student_message: string;
    }>;
  }>;
}

const GOLD_CRITERION_TO_APP: Record<string, keyof FeedbackReport["score_summary"]["criteria_bands"]> = {
  task_response: "task_achievement",
  coherence_cohesion: "coherence_cohesion",
  lexical_resource: "lexical_resource",
  grammar: "grammatical_range_accuracy",
};

function mapGoldReportToFeedbackReport(
  raw: GoldFeedbackReportRaw,
  input: { submissionId: string; studentId: string }
): FeedbackReport {
  const cb = raw.performance_summary.criteria_bands;
  const criteria_bands = {
    task_achievement: cb.task_response,
    coherence_cohesion: cb.coherence_cohesion,
    lexical_resource: cb.lexical_resource,
    grammatical_range_accuracy: cb.grammar,
  };

  // Gold doesn't emit a pre-written headline sentence — build one from the
  // overall band, and surface a soft caveat when the engine itself flagged
  // low confidence rather than staying silent about it.
  const confidenceNote =
    raw.performance_summary.score_confidence !== "normal"
      ? " This score has slightly lower confidence than usual — treat it as a solid estimate rather than final."
      : "";
  const headline_message = `Your estimated overall band is ${raw.performance_summary.overall_band}.${confidenceNote}`;

  // Gold's focus_area_feedback doesn't carry a per-item current/target band
  // (only the overall performance_summary does). We derive current_band from
  // the matching overall criterion and target_band via the same
  // rubricTarget() helper already used elsewhere in the app for consistency.
  const focus_area_feedback: FocusArea[] = raw.focus_area_feedback.map((f, i) => {
    const appCriterionKey = GOLD_CRITERION_TO_APP[f.criterion] ?? "task_achievement";
    const current_band = criteria_bands[appCriterionKey];
    const annotated_errors: AnnotatedError[] = f.examples.map((ex) => ({
      error_id: ex.error_id,
      excerpt: ex.surface_quote,
      issue: ex.student_message,
      correction: ex.suggested_revision,
      error_type: ex.family,
      criterion: ex.criterion,
      // Gold only gives a sentence_index, not the sentence text itself — the
      // surface_quote is the closest thing available to show the student.
      sentence: ex.surface_quote,
    }));
    return {
      rank: i + 1,
      criterion: f.criterion,
      skill_tag: f.skill_tag,
      // v24 (2026-07-23): was silently dropped here even though the raw Gold
      // report already carries it (see GoldFeedbackReportRaw above) -- see
      // lib/types.ts's FocusArea.capacity_domain doc comment for why this is
      // the field Practice now needs to bridge into the exercise bank.
      capacity_domain: f.capacity_domain,
      current_band,
      target_band: rubricTarget(current_band),
      summary: f.summary,
      annotated_errors,
    };
  });

  const recommendations = raw.top_learning_priorities.map((p) => p.next_step).filter(Boolean);

  return {
    report_id: `gold_${input.submissionId}`,
    student_id: input.studentId,
    generated_at: raw.created_at,
    score_summary: {
      holistic_band: raw.performance_summary.overall_band,
      criteria_bands,
      headline_message,
    },
    recommendations: recommendations.length ? recommendations : undefined,
    focus_area_feedback,
    // Gold's adjudication_status/score_confidence double as the escalation
    // signal the trainer QA tool (AlgorithmReviewForm) already surfaces first.
    escalate_to_human_review:
      raw.performance_summary.adjudication_status !== "confirmed" ||
      raw.performance_summary.score_confidence !== "normal",
  };
}

export async function runGoldEvaluation(input: {
  submissionId: string;
  studentId: string;
  prompt: string;
  essay: string;
}): Promise<{ report: FeedbackReport; sessionDir: string }> {
  const subDir = path.join(GOLD_PIPELINE_DIR, "web_submissions");
  fs.mkdirSync(subDir, { recursive: true });
  const subFile = path.join(subDir, `${input.submissionId}.json`);
  fs.writeFileSync(
    subFile,
    JSON.stringify(
      {
        essay_id: input.submissionId,
        student_id: input.studentId,
        prompt_text: input.prompt,
        essay_text: input.essay,
      },
      null,
      2
    )
  );

  // Deliberately no --pretty: main() prints one JSON line to stdout on exit
  // ({qa_status, session_dir, manifest, qa_report}); pretty-printing would
  // break the single-line parse below. --strict is intentionally omitted for
  // the pilot (would hard-fail a submission if any of 27 QA artifacts are
  // imperfect) — qa_status is still captured and worth logging/monitoring.
  const stdout = await new Promise<string>((resolve, reject) => {
    const proc = spawn(
      "python",
      [
        ORCHESTRATOR,
        "--input",
        subFile,
        "--engine-config",
        ENGINE_CONFIG,
        "--output-root",
        OUTPUT_ROOT,
        ...(CANONICAL_RESOURCES_DIR ? ["--canonical-resources-dir", CANONICAL_RESOURCES_DIR] : []),
      ],
      { cwd: GOLD_PIPELINE_DIR, env: process.env, windowsHide: true }
    );
    let out = "";
    let stderr = "";
    proc.stdout.on("data", (d) => (out += d.toString()));
    proc.stderr.on("data", (d) => (stderr += d.toString()));
    proc.on("error", reject);
    proc.on("close", (code) =>
      code === 0
        ? resolve(out)
        : reject(new Error(`gold pipeline exited ${code}: ${stderr.slice(-800)}`))
    );
    setTimeout(() => {
      proc.kill();
      reject(new Error("gold pipeline timeout after 15 minutes"));
    }, TIMEOUT_MS);
  });

  let parsed: { qa_status: string; session_dir: string };
  try {
    const lastLine = stdout.trim().split("\n").filter(Boolean).pop() ?? "";
    parsed = JSON.parse(lastLine);
  } catch {
    throw new Error(`could not parse gold orchestrator stdout: ${stdout.slice(-500)}`);
  }

  const reportFile = path.join(parsed.session_dir, "06_feedback_report_v6c.json");
  if (!fs.existsSync(reportFile)) {
    // The orchestrator's final stdout line only gives a qa_status + file
    // paths, not the actual reason a stage failed — that detail lives in
    // QA_gold_report.json (missing_required_artifacts, invalid_required,
    // boundary_issues, quality_gate_issues). Read it here so the thrown
    // error (which ends up in both the DB record and, since the route's
    // catch block now console.errors it, the host's logs) actually says
    // what broke instead of just "needs_attention".
    let qaDetail = "";
    try {
      const qaReportPath = (parsed as any).qa_report;
      if (qaReportPath && fs.existsSync(qaReportPath)) {
        const qa = JSON.parse(fs.readFileSync(qaReportPath, "utf8"));
        qaDetail = ` — missing_required_artifacts: ${JSON.stringify(qa.missing_required_artifacts ?? [])}, invalid_required: ${JSON.stringify(qa.invalid_required ?? [])}, boundary_issues: ${JSON.stringify(qa.boundary_issues ?? [])}, quality_gate_issues: ${JSON.stringify(qa.quality_gate_issues ?? [])}`;
      }
    } catch (qaErr) {
      qaDetail = ` (also failed to read QA report: ${qaErr instanceof Error ? qaErr.message : String(qaErr)})`;
    }
    // Still only tells us artifacts are missing, not why — the actual
    // reason (a Python traceback, a bad API key, etc.) is captured per-
    // stage as stderr and recorded on manifest.json's stage_results[].
    // Surface any failed stage's real error here instead of making
    // someone SSH into the disk to find gold_web_sessions/<dir>/manifest.json
    // by hand.
    let stageDetail = "";
    try {
      const manifestPath = (parsed as any).manifest;
      if (manifestPath && fs.existsSync(manifestPath)) {
        const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
        const failed = Array.isArray(manifest.stage_results)
          ? manifest.stage_results.filter((s: any) => s.status === "failed")
          : [];
        if (failed.length) {
          // Python tracebacks are "most recent call last" — the actual
          // exception type + message is at the END of the string, not the
          // start. slice(0, 600) was keeping the outer frames and cutting
          // off exactly the one line that matters. slice(-1500) keeps the
          // tail instead.
          stageDetail = ` — FAILED STAGES: ${failed
            .map((s: any) => `[${s.stage}] ${(s.error ?? "").slice(-1500)}`)
            .join(" ||| ")}`;
        }
      }
    } catch (mErr) {
      stageDetail = ` (also failed to read manifest: ${mErr instanceof Error ? mErr.message : String(mErr)})`;
    }
    throw new Error(
      `feedback report missing in ${parsed.session_dir} (qa_status: ${parsed.qa_status})${qaDetail}${stageDetail}`
    );
  }
  const raw = JSON.parse(fs.readFileSync(reportFile, "utf8")) as GoldFeedbackReportRaw;
  const report = mapGoldReportToFeedbackReport(raw, input);

  if (parsed.qa_status && parsed.qa_status !== "pass" && parsed.qa_status !== "PASS") {
    // Don't fail the submission over this — the required artifact (feedback
    // report) is present and valid — but flag it for the trainer QA queue
    // the same way a low-confidence Gold score already does.
    report.escalate_to_human_review = true;

    // v18 (session-audit Finding 2): this branch already ran on the real
    // off-topic-essay session and set escalate_to_human_review — but nothing
    // ever said which QA gate issue triggered it, so a real, specific problem
    // (LRET's canonical resources failing to load, confirmed via
    // canonical_resources_not_loaded_zero_resource_run in the orchestrator's
    // own quality_gate_issues) sat invisible behind a generic "ambiguous"
    // banner. Read the QA report here too, not just on the failure path, and
    // give the student/admin the specific reason when it's one we recognize.
    try {
      const qaReportPath = (parsed as any).qa_report;
      if (qaReportPath && fs.existsSync(qaReportPath)) {
        const qa = JSON.parse(fs.readFileSync(qaReportPath, "utf8"));
        const issues: Array<{ issue?: string; artifact?: string }> = Array.isArray(qa.quality_gate_issues)
          ? qa.quality_gate_issues
          : [];
        if (issues.some((i) => i.issue === "canonical_resources_not_loaded_zero_resource_run")) {
          report.quality_notice =
            "Vocabulary suggestions in this report ran with reduced resource support — some " +
            "collocation/lexical feedback may be thinner than usual. This doesn't affect your " +
            "band scores.";
        }
      }
    } catch (qaErr) {
      console.error(`[ST.ELLA] Could not read QA report for quality_notice detail:`, qaErr);
    }
  }

  // v22 (2026-07-23): Defect 1 fix (see refreshLearnerProfile()'s module
  // comment) -- this new essay's own learner_profile/roadmap/etc artifacts
  // are correct and complete at this point; reseed the per-student "current"
  // files from them now, so the roadmap/profile a student sees immediately
  // reflects THIS essay rather than a stale copy left over from whatever
  // Practice/Coach/Vocab/Revision activity happened around the PREVIOUS
  // essay. Subsequent refreshLearnerProfile() calls enrich this new baseline
  // further as the student does more of that activity around this essay.
  reseedCurrentProfileFromSession(input.studentId, parsed.session_dir);

  // v26 (2026-07-23): classify this essay's topic for Vocab Coach
  // topic-matching (see classifyEssayTopic()'s module comment). Best-effort,
  // never blocks a successful evaluation on a classification hiccup -- an
  // essay with no confident topic is a normal, expected state (undefined),
  // not a failure; only a genuine exception is worth logging.
  try {
    const topic = classifyEssayTopic(input.prompt, input.essay);
    setSubmissionTopic(input.submissionId, topic ?? null);
  } catch (e) {
    console.error(`[ST.ELLA] Essay topic classification failed for submission ${input.submissionId}:`, e);
  }

  return { report, sessionDir: parsed.session_dir };
}

// ---------------------------------------------------------------------------
// v10: Writing Coach + Essay Revision. These two stages (07e_writing_coach_
// output.json, 10_revision_workspace.json) have been completing successfully
// on every Gold submission all along — confirmed against a real session
// (qa_status: "passed", missing_required_artifacts: []). The frontend simply
// never read them: the dashboard showed a hardcoded "coming soon" card
// instead of the real daily mission, and there was no screen at all for the
// revision workspace. Nothing below changes what the pipeline produces —
// it just reads the same student-safe fields (*_public suffixed where the
// engine already curated them) the pipeline authors built for exactly this.
// ---------------------------------------------------------------------------

export interface WritingCoachMission {
  homeCard: {
    title: string;
    message: string;
    buttonText: string;
    missionTitle: string;
    timeboxMinutes: number;
    weeklyFocus: string;
    streakGoal: string;
    planSummary: string;
  };
  mission: {
    title: string;
    studentGoal: string;
    studentInstruction: string;
    steps: string[];
    modelExample: string | null;
    showModelOnFirstAttempt: boolean;
    stimulusItems: Array<{ roughInput: string; expectedMove: string; itemNumber: number }>;
    successChecklist: string[];
    timeboxMinutes: number;
    difficulty: string;
    requiredItems: number;
  };
  // v17 (session-audit Finding 6): writing_coach_alignment_guard_standalone_v1_4_7.py
  // already computes an honest, explicit signal whenever Writing Coach's own
  // selected skill differs from the Gold Directive's top-priority skill --
  // status "explained_override", the Directive's focus, Writing Coach's own
  // selected focus, and a plain-language rationale. Confirmed via direct
  // search that nothing in this codebase read directive_alignment before this
  // change -- the mismatch complaint was a frontend plumbing gap, not a
  // missing backend signal. Null when the guard didn't run (older sessions)
  // or when the two are already aligned and there's nothing to explain.
  directiveAlignment: {
    status: string;
    isAligned: boolean;
    directiveFocusLabel: string;
    coachFocusLabel: string;
    rationale: string;
  } | null;
}

export function loadWritingCoach(sessionDir: string): WritingCoachMission | undefined {
  const file = path.join(sessionDir, "07e_writing_coach_output.json");
  if (!fs.existsSync(file)) return undefined;
  try {
    const raw = JSON.parse(fs.readFileSync(file, "utf8"));
    const card = raw.student_home_card ?? {};
    const m = raw.today_mission ?? {};
    // v16: the engine always includes a literal worked example inside
    // mission.steps ("Model (for reference only, do not copy word-for-word):
    // ..."), regardless of the student's mastery/scaffold level — the
    // engine's own show_model_on_first_attempt flag only governs whether
    // *feedback after submission* reveals a correction, not whether the
    // upfront instructions show a model. The frontend used to dump every
    // step (including that literal example sentence) straight into the
    // mission instructions, which made missions trivial to solve by copying
    // the model's structure and swapping one word — this is the concrete
    // mechanism behind "missions are too easy, I needed just to add a verb".
    // Fix: pull the model line out of the visible step list and render it
    // behind a collapsed disclosure the student has to deliberately open,
    // so the default experience is a genuine first attempt from scratch.
    const rawSteps: string[] = Array.isArray(m.steps) ? m.steps : [];
    const modelStep = rawSteps.find((s) => /^Model \(/i.test(s)) ?? null;
    const steps = rawSteps.filter((s) => s !== modelStep);

    // v17: directive_alignment is written by writing_coach_alignment_guard_
    // standalone_v1_4_7.py at the top level of this same file, alongside a
    // duplicated subset inside coach_decision. Read the top-level object,
    // since it carries the fuller shape (directive_primary_focus,
    // coach_selected_focus, override_reason) the guard actually computes.
    const align = raw.directive_alignment ?? null;
    const labelFor = (focus: any): string => {
      const label = focus?.student_label || focus?.capacity_domain || focus?.skill_id || "";
      return String(label).replace(/_/g, " ").trim();
    };
    const directiveAlignment = align
      ? {
          status: String(align.status ?? ""),
          isAligned: align.status === "aligned",
          directiveFocusLabel: labelFor(align.directive_primary_focus),
          coachFocusLabel:
            align.coach_selected_focus?.selected_skill_name ||
            labelFor(align.coach_selected_focus) ||
            m.title ||
            "",
          rationale: String(align.override_reason ?? ""),
        }
      : null;

    return {
      homeCard: {
        title: card.title ?? "Today's Writing Coach",
        message: card.message ?? "",
        buttonText: card.button_text ?? "Start today's mission",
        missionTitle: card.mission_title ?? m.title ?? "",
        timeboxMinutes: card.timebox_minutes ?? m.timebox_minutes ?? 10,
        weeklyFocus: card.weekly_focus ?? "",
        streakGoal: card.streak_goal ?? "",
        planSummary: card.student_visible_plan_summary ?? "",
      },
      mission: {
        title: m.title ?? "",
        studentGoal: m.student_goal ?? "",
        studentInstruction: m.student_instruction ?? "",
        steps,
        modelExample: modelStep ? modelStep.replace(/^Model \([^)]*\):\s*/i, "") : null,
        showModelOnFirstAttempt: m.show_model_on_first_attempt ?? true,
        stimulusItems: Array.isArray(m.stimulus?.items)
          ? m.stimulus.items.map((it: any) => ({
              roughInput: it.rough_input ?? "",
              expectedMove: it.expected_move ?? "",
              itemNumber: it.original_item_number ?? 0,
            }))
          : [],
        successChecklist: Array.isArray(m.success_checklist) ? m.success_checklist : [],
        timeboxMinutes: m.timebox_minutes ?? 10,
        difficulty: m.difficulty ?? "controlled",
        requiredItems: m.required_output?.required_items ?? (Array.isArray(m.stimulus?.items) ? m.stimulus.items.length : 1),
      },
      directiveAlignment,
    };
  } catch (e) {
    console.error(`[ST.ELLA] Could not read Writing Coach output from ${sessionDir}:`, e);
    return undefined;
  }
}

export interface RevisionSpan {
  quote: string;
  explanation: string;
}

export interface RevisionSentence {
  index: number;
  text: string;
  status: string;
  statusLabel: string;
  hint: string;
  spans: RevisionSpan[];
}

export interface RevisionParagraph {
  index: number;
  role: string;
  status: string;
  statusLabel: string;
  hint: string;
  alerts: string[];
  sentences: RevisionSentence[];
}

export interface RevisionHintItem {
  level: string;
  text: string;
}

export interface RevisionWorkspace {
  wordCount: number;
  sentenceCounts: { yellow: number; red: number; green: number };
  // v13: overall_revision_hints existed in the raw JSON all along (engine
  // computes essay-wide language-repair priority + paragraph-function
  // notes) but was never read here — only paragraph.hint (paragraph-wide)
  // was wired through. This is the essay-wide layer above that.
  overallHints: {
    languageRepair: RevisionHintItem[];
    paragraphFunction: RevisionHintItem[];
  };
  paragraphs: RevisionParagraph[];
  // v12: was only exposing waves[0] — the real workspace has up to 4 (fix
  // red first, then yellow, then a final language pass), confirmed against
  // a real rendered workspace the user shared (revision_workspace.html).
  waves: Array<{ level: string; title: string; text: string }>;
  checklist: string[];
  cleanText: string;
  // v12: prewriting_guidance existed in the raw JSON all along but was never
  // read — word-count targets, the universal WT2 structure, the body-
  // paragraph formula, and strong/weak example guidance the reference HTML
  // shows in a "Before you write or revise" panel.
  prewriting: {
    minimumWords: number;
    recommendedRange: string;
    paragraphTargets: Record<string, string>;
    bodyParagraphFormula: string[];
    strongExampleRule: string;
    weakExampleRule: string;
  } | null;
}

export function loadRevisionWorkspace(sessionDir: string): RevisionWorkspace | undefined {
  const file = path.join(sessionDir, "10_revision_workspace.json");
  if (!fs.existsSync(file)) return undefined;
  try {
    const raw = JSON.parse(fs.readFileSync(file, "utf8"));
    const src = raw.source_summary ?? {};
    const paragraphs: RevisionParagraph[] = (raw.annotated_essay?.paragraphs ?? []).map((p: any) => ({
      index: p.paragraph_number ?? p.paragraph_index,
      role: p.paragraph_role ?? "",
      status: p.paragraph_status ?? "yellow",
      statusLabel: p.paragraph_status_label ?? "Improve",
      hint: p.paragraph_hint_public ?? p.paragraph_hint ?? "",
      alerts: Array.isArray(p.function_alerts)
        ? p.function_alerts.map((a: any) => a.text_public ?? a.text).filter(Boolean)
        : [],
      sentences: (p.sentences ?? []).map((s: any) => ({
        index: s.sentence_number ?? s.sentence_index,
        text: s.text ?? "",
        status: s.status ?? "yellow",
        statusLabel: s.status_label ?? "Improve",
        hint: s.student_hint_public ?? s.student_hint ?? "",
        spans: Array.isArray(s.span_annotations)
          ? s.span_annotations
              .map((sp: any) => ({ quote: sp.quote ?? sp.surface_quote ?? "", explanation: sp.explanation ?? sp.issue ?? "" }))
              .filter((sp: RevisionSpan) => sp.quote)
          : [],
      })),
    }));
    const pw = raw.prewriting_guidance;
    const orh = raw.overall_revision_hints ?? {};
    const mapHints = (arr: any): RevisionHintItem[] =>
      Array.isArray(arr)
        ? arr
            .map((h: any) => ({ level: h.level ?? "yellow", text: h.text_public ?? h.text ?? "" }))
            .filter((h: RevisionHintItem) => h.text)
        : [];
    return {
      wordCount: src.original_word_count ?? 0,
      sentenceCounts: {
        yellow: src.displayed_sentence_status_counts?.yellow ?? 0,
        red: src.displayed_sentence_status_counts?.red ?? 0,
        green: src.displayed_sentence_status_counts?.green ?? 0,
      },
      overallHints: {
        languageRepair: mapHints(orh.language_repair),
        paragraphFunction: mapHints(orh.paragraph_function),
      },
      paragraphs,
      waves: (raw.revision_waves ?? []).map((w: any) => ({
        level: w.level ?? "yellow",
        title: w.student_title ?? w.title ?? "",
        text: w.student_text ?? w.text ?? "",
      })),
      checklist: Array.isArray(raw.student_checklist) ? raw.student_checklist : [],
      cleanText: raw.copyable_clean_text ?? "",
      prewriting: pw
        ? {
            minimumWords: pw.word_plan?.minimum_words ?? 250,
            recommendedRange: pw.word_plan?.recommended_range ?? "",
            paragraphTargets: pw.word_plan?.paragraph_targets ?? {},
            bodyParagraphFormula: Array.isArray(pw.body_paragraph_formula) ? pw.body_paragraph_formula : [],
            strongExampleRule: pw.example_quality?.strong_example_rule ?? "",
            weakExampleRule: pw.example_quality?.weak_example_rule ?? "",
          }
        : null,
    };
  } catch (e) {
    console.error(`[ST.ELLA] Could not read revision workspace from ${sessionDir}:`, e);
    return undefined;
  }
}

// ---------------------------------------------------------------------------
// v11: submitting a Writing Coach mission response for grading.
//
// This does NOT go through gold_full_pipeline_orchestrator_v1_4_9.py — that
// orchestrator re-runs every one of its 27 stages on every invocation
// (confirmed by reading its stage loop: --copy-from-session only pre-seeds
// files, it does not skip re-running a stage that already has a command
// configured), so calling it here would silently re-score the entire essay
// from scratch via all the LLM stages again, ~8-15 minutes, just to grade a
// 4-sentence exercise. mission_response_grading is actually a standalone
// script call (see its entry in gold_engine_commands_full_v1_4_13.json):
// writing_coach_v1_2_17_freeze_candidate.py --evaluate-mission. Calling that
// script directly avoids the 27-stage re-run, but it is NOT fast in
// absolute terms — v15: root-caused a real "I didn't get any feedback"
// report by finding the actual attempt file on disk
// (mission_attempts/attempt_*.json) that the student never saw. The file
// was complete and correct — the 90s timeout below had already fired and
// returned a "Grading failed" error to the browser before the script
// finished, because this call enables 5 LLM flags
// (--llm-response-quality/--llm-judge/--llm-register-judge/
// --llm-correction-generator/--llm-upgrade-generator) per submitted item,
// which routinely takes well over 90s for a 4-item mission. proc.kill()
// on timeout doesn't stop the OpenAI calls already in flight, so the
// script kept running and wrote real output after the request had already
// failed. Raised the budget substantially rather than cutting any of the
// 5 LLM flags, since those are what make the feedback actually useful.
// ---------------------------------------------------------------------------

const WRITING_COACH_SCRIPT = "writing_coach_v1_2_17_freeze_candidate.py";
const MISSION_GRADE_TIMEOUT_MS = 5 * 60 * 1000;

export interface MissionItemFeedback {
  itemNumber: number;
  roughInput: string | null;
  studentSentence: string | null;
  status: string;
  issues: string[];
  suggestedRevision: string | null;
  explanation: string | null;
  howToImprove: string | null;
}

export interface MissionGradeResult {
  outcome: "pass" | "partial_pass" | "fail" | "invalid_empty_response" | "invalid_incomplete_output" | string;
  missionScore: number;
  completionMessage: string;
  overallComment: string;
  whatWentWell: string[];
  whatToFixFirst: string[];
  tryAgainInstruction: string | null;
  items: MissionItemFeedback[];
}

export async function runMissionGrading(
  sessionDir: string,
  responseText: string
): Promise<MissionGradeResult> {
  const missionFile = path.join(sessionDir, "07e_writing_coach_output.json");
  if (!fs.existsSync(missionFile)) {
    throw new Error("No Writing Coach mission found for this session.");
  }
  const attemptsDir = path.join(sessionDir, "mission_attempts");
  fs.mkdirSync(attemptsDir, { recursive: true });
  const outFile = path.join(attemptsDir, `attempt_${Date.now()}.json`);

  await new Promise<void>((resolve, reject) => {
    const proc = spawn(
      "python",
      [
        WRITING_COACH_SCRIPT,
        "--evaluate-mission",
        missionFile,
        "--student-response-text",
        responseText,
        "--lt-judge",
        "--llm-response-quality",
        "--llm-judge",
        "--llm-register-judge",
        "--llm-correction-generator",
        "--llm-upgrade-generator",
        "--output",
        outFile,
        "--pretty",
      ],
      { cwd: GOLD_PIPELINE_DIR, env: process.env, windowsHide: true }
    );
    let stderr = "";
    proc.stderr.on("data", (d) => (stderr += d.toString()));
    proc.on("error", reject);
    proc.on("close", (code) =>
      code === 0 ? resolve() : reject(new Error(`mission grading exited ${code}: ${stderr.slice(-800)}`))
    );
    setTimeout(() => {
      proc.kill();
      reject(new Error("mission grading timed out after 90 seconds"));
    }, MISSION_GRADE_TIMEOUT_MS);
  });

  if (!fs.existsSync(outFile)) throw new Error("Mission grading produced no output file.");
  const raw = JSON.parse(fs.readFileSync(outFile, "utf8"));
  const sf = raw.student_feedback ?? {};
  return {
    outcome: raw.outcome ?? "fail",
    missionScore: raw.mission_score ?? 0,
    completionMessage: raw.completion_gate?.message ?? "",
    overallComment: sf.overall_comment ?? "",
    whatWentWell: Array.isArray(sf.what_went_well) ? sf.what_went_well : [],
    whatToFixFirst: Array.isArray(sf.what_to_fix_first) ? sf.what_to_fix_first : [],
    tryAgainInstruction: sf.try_again_instruction ?? null,
    items: Array.isArray(sf.item_feedback)
      ? sf.item_feedback.map((it: any) => ({
          itemNumber: it.item_number,
          roughInput: it.rough_input ?? null,
          studentSentence: it.student_sentence ?? null,
          status: it.status ?? "missing",
          issues: Array.isArray(it.issues) ? it.issues : [],
          suggestedRevision: it.suggested_revision ?? null,
          explanation: it.explanation ?? null,
          howToImprove: it.how_to_improve ?? null,
        }))
      : [],
  };
}

// ---------------------------------------------------------------------------
// v16: Vocabulary Coach (LRET + PEEL) — per
// VOCABULARY_COACH_ENGINE_BUILD_PROMPT_V1.md. Same reasoning as
// runMissionGrading above: this is a small, cooldown-gated micro-task, not
// a full essay re-score, so it calls its three standalone engines directly
// via spawn() rather than going through the 27-stage orchestrator. The three
// engines are pure, independent CLI scripts (no shared state beyond the
// files passed on the command line), so no orchestrator/STAGE_ORDER wiring
// was needed or added — see gold_engine_commands_full_v1_4_16.json's
// description for the same note from the config side.
//
// Flow: runVocabCoachSession() generates (or returns "not yet available for
// N more hours") a session; the frontend renders its prompt; the student
// submits a paragraph; submitVocabCoachResponse() grades it AND updates the
// Leitner ledger in one call (grading fails to update, is truthful and
// simply never returned; there's no partial-write risk since ledger update
// only writes after grading succeeds).
// ---------------------------------------------------------------------------

// v1_2 (2026-07): adds selection-runtime academic-word picking + hint-on-
// request, reading the new academic_words pool in topic bank v1.5.0 (see
// Academic_Words_Redesign_Spec_v1.docx). Grader and ledger-update scripts
// are unchanged from v1_1 -- v1_1's suggested_vocabulary/items handling is
// already generic enough to cover the new source_bank: "academic_word"
// entries with no code changes (verified directly, not just assumed).
// VOCAB_COACH_TOPIC_BANK also bumps from v1_3_0 -- which had silently fallen
// behind the two intervening bank builds (v1_4_0's 8 new topics + Tier B
// academic collocations, v1_5_0's academic_words) -- to v1_5_0, the current
// bank on disk.
// v25 (2026-07-23): bumped to v1_3 -- combined 3-source family bias (LRET +
// Evaluator's D7/D9/D14 lexical/style domains + Practice engagement-history
// repeated_practice_families), per direct product-owner design discussion.
// See that file's module docstring for the full grounding. runVocabCoachSession()
// below now also passes --evaluator and --engagement-history (both optional,
// additive -- an old-shaped call with neither still works exactly as v1_2 did).
// v26 (2026-07-23): bumped to v1_4 -- adds optional --topic-lock, wired below
// from the latest essay's classifyEssayTopic() result (see that function's
// module comment). No essay yet, or topic not confidently classified -> arg
// omitted -> rotation across all topics, unchanged from v1_3's behavior.
const VOCAB_COACH_SELECTION_SCRIPT = "vocab_coach_selection_engine_v1_4.py";
const VOCAB_COACH_GRADER_SCRIPT = "vocab_coach_response_grader_v1_1.py";
const VOCAB_COACH_LEDGER_SCRIPT = "vocab_coach_ledger_update_v1_1.py";
const VOCAB_COACH_TOPIC_BANK = "vocab_coach_topic_bank_v1_5_0.json";
const VOCAB_COACH_TASK_TYPE_BANK = "vocab_coach_task_type_bank_v1_2_0.json";
// v1_1_0 (2026-07): closes all 35 rotation-unit gaps that v1_0_0 left open
// once the topic bank grew to 18 topics/57 units -- 31 from the 8 new topics
// (VOCAB_COACH_PROMPT_BANK_EXTENSION_PROMPT_V2.md) plus 4 from the
// academic_collocations subtopic on the original 4 subtopic topics, which had
// silently been uncovered since that subtopic was first added. Verified
// directly against vocab_coach_selection_engine_v1_2.py: 0 of the 57 real
// units now fail filter_candidates() for any task_type/angle. All 228
// original prompts carry over byte-identical; this is additive only.
const VOCAB_COACH_PROMPT_BANK = "vocab_coach_prompt_bank_v1_1_0.json";
const VOCAB_COACH_TIMEOUT_MS = 60 * 1000;
const VOCAB_COACH_MAX_LRET_SESSIONS = 5;

// v26 (2026-07-23): essay-topic classification for Vocab Coach topic-
// matching. Product-owner design: vocabulary practice should stay on the
// SAME topic as the essay being revised (so learned words can plausibly show
// up in the revision, making the whole Practice+Writing-Coach+Vocab-Coach
// loop's effect on revision quality actually testable) -- but only when
// there IS a specific essay to match; the no-essay session-flow keeps
// today's random-topic rotation unchanged (no work needed there).
//
// Deliberately deterministic keyword-match against the topic bank's OWN
// vocabulary, not an LLM call -- classification runs on every Gold
// evaluation, so a per-essay LLM cost/latency here would be a real recurring
// cost for a task simple keyword overlap already handles well; matches this
// codebase's established preference for narrow, cheap, explainable engines
// over LLM calls wherever one will do (see LRET's fail-closed narrow-LLM-
// fallback pattern for the same philosophy applied elsewhere).
let vocabTopicBankCache: any = null;
function loadVocabTopicBankRaw(): any {
  if (vocabTopicBankCache) return vocabTopicBankCache;
  const p = path.join(GOLD_PIPELINE_DIR, VOCAB_COACH_TOPIC_BANK);
  vocabTopicBankCache = JSON.parse(fs.readFileSync(p, "utf8"));
  return vocabTopicBankCache;
}

const STOPWORDS = new Set([
  "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
  "is", "are", "was", "were", "be", "been", "being", "this", "that", "these",
  "those", "it", "its", "as", "at", "by", "from", "into", "about", "than",
  "then", "so", "such", "not", "no", "do", "does", "did", "has", "have", "had",
  "will", "would", "can", "could", "should", "may", "might", "must", "if",
  "there", "their", "they", "them", "which", "who", "whom", "what", "when",
  "where", "how", "why", "some", "many", "most", "more", "much", "very", "also",
]);

function tokenize(text: string): string[] {
  return (text.toLowerCase().match(/[a-z][a-z'-]*/g) ?? []).filter(
    (w) => w.length > 2 && !STOPWORDS.has(w)
  );
}

// Extracts every unit (a flat topic, or one of its subtopics) as
// {topicKey, keywords: Set<string>} -- topicKey is always the TOP-LEVEL
// topic name (vocab_coach_selection_engine's --topic-lock operates at that
// granularity, matching enumerate_units()'s own topic/subtopic distinction),
// even when the keywords themselves come from a subtopic's items.
// `headword` is each collocation's real content word (e.g. "offence" for
// "cause offence") -- confirmed directly against vocab_coach_topic_bank_v1_5_0.json
// -- a much cleaner topic signal than tokenizing the whole phrase, which
// includes generic collocate words ("cause", "serious") that recur across
// many topics and would dilute the signal. Falls back to tokenizing `phrase`
// only for older bank entries that predate the `headword` field.
let topicKeywordIndexCache: Record<string, Set<string>> | null = null;
function buildTopicKeywordIndex(): Record<string, Set<string>> {
  if (topicKeywordIndexCache) return topicKeywordIndexCache;
  const bank = loadVocabTopicBankRaw();
  const index: Record<string, Set<string>> = {};

  function addItems(topicKey: string, items: any[] | undefined) {
    if (!Array.isArray(items)) return;
    const set = (index[topicKey] ??= new Set());
    for (const it of items) {
      if (it && typeof it.headword === "string") {
        set.add(it.headword.toLowerCase());
      } else if (it && typeof it.phrase === "string") {
        for (const w of tokenize(it.phrase)) set.add(w);
      }
    }
  }
  function addAcademicWords(topicKey: string, words: any[] | undefined) {
    if (!Array.isArray(words)) return;
    const set = (index[topicKey] ??= new Set());
    for (const w of words) {
      if (w && typeof w.word === "string") set.add(w.word.toLowerCase());
    }
  }

  const topics = bank?.topics ?? {};
  for (const [topicKey, tdata] of Object.entries<any>(topics)) {
    if (tdata && tdata.subtopics) {
      for (const sdata of Object.values<any>(tdata.subtopics)) {
        addItems(topicKey, sdata?.items);
        addAcademicWords(topicKey, sdata?.academic_words);
      }
    } else {
      addItems(topicKey, tdata?.items);
      addAcademicWords(topicKey, tdata?.academic_words);
    }
  }
  topicKeywordIndexCache = index;
  return index;
}

// Provisional thresholds, not tuned against real essays -- same
// not-yet-validated caveat this session has already flagged for
// EVALUATOR_PRIORITY_HIGH/MEDIUM and vocab_coach_selection_engine_v1_3's
// combined-tally merge. MIN_HITS guards against classifying a very short or
// generic essay off one incidental word match; MIN_MARGIN_RATIO guards
// against a near-tie between two topics (e.g. "technology" vs "education"
// both plausibly present) picking one arbitrarily -- returns unclassified
// (undefined) rather than guessing in either case, which is the safe
// fallback since Vocab Coach's existing random-topic rotation already
// handles "no confident topic" gracefully.
const TOPIC_CLASSIFY_MIN_HITS = 3;
const TOPIC_CLASSIFY_MIN_MARGIN_RATIO = 1.3;

export function classifyEssayTopic(promptText: string, essayText: string): string | undefined {
  const index = buildTopicKeywordIndex();
  const tokens = tokenize(`${promptText}\n${essayText}`);
  if (tokens.length === 0) return undefined;

  const scores: Record<string, number> = {};
  const seenPerTopic: Record<string, Set<string>> = {};
  for (const tok of tokens) {
    for (const [topicKey, keywords] of Object.entries(index)) {
      if (keywords.has(tok)) {
        const seen = (seenPerTopic[topicKey] ??= new Set());
        if (!seen.has(tok)) {
          seen.add(tok);
          scores[topicKey] = (scores[topicKey] ?? 0) + 1;
        }
      }
    }
  }

  const ranked = Object.entries(scores).sort((a, b) => b[1] - a[1]);
  if (ranked.length === 0) return undefined;
  const [topTopic, topScore] = ranked[0];
  if (topScore < TOPIC_CLASSIFY_MIN_HITS) return undefined;
  const runnerUpScore = ranked[1]?.[1] ?? 0;
  if (runnerUpScore > 0 && topScore / runnerUpScore < TOPIC_CLASSIFY_MIN_MARGIN_RATIO) return undefined;
  return topTopic;
}

function learnerProfilesDir(): string {
  const dir = path.join(GOLD_PIPELINE_DIR, OUTPUT_ROOT, "learner_profiles");
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function vocabCoachLedgerPath(studentId: string): string {
  return path.join(learnerProfilesDir(), `${studentId}_vocab_coach_ledger.json`);
}

// v22 (2026-07-23): Defect 1 fix -- see refreshLearnerProfile()'s module
// comment below for the full story. These are per-STUDENT "current" LIE
// artifacts, separate from any one essay's own frozen session-dir files
// (08_gold_learner_profile.json/08b/08c/09/08a) -- same per-student
// file-naming convention as vocabCoachLedgerPath above. refreshLearnerProfile
// writes here (never back into a session dir again); runGoldEvaluation's
// success path reseeds these from a brand-new essay's own fresh originals
// (see reseedCurrentProfileFromSession below); getLearningRoadmap() in
// study-plan.ts reads currentRoadmapPath() first, falling back to the
// session's own 08c only if no refresh/reseed has happened yet for this
// student.
export function currentLearnerProfilePath(studentId: string): string {
  return path.join(learnerProfilesDir(), `${studentId}_current_learner_profile.json`);
}
export function currentSkillsProgressPath(studentId: string): string {
  return path.join(learnerProfilesDir(), `${studentId}_current_skills_progress.json`);
}
export function currentRoadmapPath(studentId: string): string {
  return path.join(learnerProfilesDir(), `${studentId}_current_roadmap.json`);
}
export function currentProgressSnapshotPath(studentId: string): string {
  return path.join(learnerProfilesDir(), `${studentId}_current_progress_snapshot.json`);
}
export function currentPersistedProfilePath(studentId: string): string {
  return path.join(learnerProfilesDir(), `${studentId}_current_persisted_profile.json`);
}

// Copies a NEW essay's own fresh, correct, original learner-profile artifacts
// into the per-student "current" paths above. Called once, right after
// runGoldEvaluation()'s orchestrator run completes successfully -- this
// establishes a correct new baseline (this essay's real, just-computed
// profile) instead of leaving the PREVIOUS essay cycle's stale "current"
// files in place until the next Practice/Coach/Vocab/Revision action happens
// to trigger a refreshLearnerProfile() call. Best-effort per file (a single
// missing/unreadable artifact shouldn't fail the whole essay submission that
// is already otherwise successful at this point) -- logged, not thrown.
function reseedCurrentProfileFromSession(studentId: string, sessionDir: string): void {
  const copies: Array<[string, string]> = [
    [path.join(sessionDir, "08_gold_learner_profile.json"), currentLearnerProfilePath(studentId)],
    [path.join(sessionDir, "08b_gold_skills_progress_report.json"), currentSkillsProgressPath(studentId)],
    [path.join(sessionDir, "08c_gold_learning_roadmap.json"), currentRoadmapPath(studentId)],
    [path.join(sessionDir, "09_gold_progress_snapshot.json"), currentProgressSnapshotPath(studentId)],
    [path.join(sessionDir, "08a_gold_persisted_profile.json"), currentPersistedProfilePath(studentId)],
  ];
  for (const [src, dest] of copies) {
    try {
      if (fs.existsSync(src)) fs.copyFileSync(src, dest);
    } catch (e) {
      console.error(`[ST.ELLA] Could not reseed current profile file ${dest} from ${src}:`, e);
    }
  }
}

// Scans this student's own session directories ({OUTPUT_ROOT}/{studentId}/gold_*)
// for real 07d_lret_session.json artifacts, most recent first, so the
// selection engine's LRET-history bias (see VOCABULARY_COACH_ENGINE_BUILD_PROMPT_V1.md,
// Architecture point 1) has real diagnostic history to read instead of
// always falling back to "no bias applied".
function findRecentLretSessionPaths(studentId: string, limit = VOCAB_COACH_MAX_LRET_SESSIONS): string[] {
  const studentDir = path.join(GOLD_PIPELINE_DIR, OUTPUT_ROOT, studentId);
  if (!fs.existsSync(studentDir)) return [];
  const sessionDirs = fs
    .readdirSync(studentDir, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name)
    .sort()
    .reverse(); // gold_{stamp}_{essayId}_{hash} — lexicographic sort matches chronological
  const found: string[] = [];
  for (const name of sessionDirs) {
    const p = path.join(studentDir, name, "07d_lret_session.json");
    if (fs.existsSync(p)) found.push(p);
    if (found.length >= limit) break;
  }
  return found;
}

// Best-effort: the most recent essay's finalized score contract, used as the
// selection engine's optional --score-contract CEFR-gating input. Returns
// undefined (not a hard error) if none exists yet — the engine already has
// a documented fail-safe mid-band default for that case.
function findLatestScoreContractPath(studentId: string): string | undefined {
  const studentDir = path.join(GOLD_PIPELINE_DIR, OUTPUT_ROOT, studentId);
  if (!fs.existsSync(studentDir)) return undefined;
  const sessionDirs = fs
    .readdirSync(studentDir, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name)
    .sort()
    .reverse();
  for (const name of sessionDirs) {
    const p = path.join(studentDir, name, "02d_final_score_contract.json");
    if (fs.existsSync(p)) return p;
  }
  return undefined;
}

// v25 (2026-07-23): same pattern as findLatestScoreContractPath above, for
// vocab_coach_selection_engine_v1_3.py's new --evaluator input. Points at the
// raw 07_evaluator_output.json (the full payload, including
// consumer_payloads.writing_coach_payload.development_target_signals) -- NOT
// the trimmed evaluator_payload used elsewhere in this file.
function findLatestEvaluatorOutputPath(studentId: string): string | undefined {
  const studentDir = path.join(GOLD_PIPELINE_DIR, OUTPUT_ROOT, studentId);
  if (!fs.existsSync(studentDir)) return undefined;
  const sessionDirs = fs
    .readdirSync(studentDir, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name)
    .sort()
    .reverse();
  for (const name of sessionDirs) {
    const p = path.join(studentDir, name, "07_evaluator_output.json");
    if (fs.existsSync(p)) return p;
  }
  return undefined;
}

// v26 (2026-07-23): the latest "done" submission's classified topic (see
// classifyEssayTopic()), for Vocab Coach's --topic-lock. Uses submissionsFor
// (already ordered created_at DESC, per store.ts), not a session-dir scan --
// topic is a plain DB column, no file to locate. Returns undefined if there's
// no essay yet, or classification never produced a confident topic for it --
// both are normal, expected states (see classifyEssayTopic()'s doc comment).
function findLatestEssayTopic(studentId: string): string | undefined {
  const latest = submissionsFor(studentId).find((s) => s.status === "done" && s.topic);
  return latest?.topic;
}

function runPythonScript(script: string, args: string[], timeoutMs: number, label: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const proc = spawn("python", [script, ...args], {
      cwd: GOLD_PIPELINE_DIR,
      env: process.env,
      windowsHide: true,
    });
    let stderr = "";
    proc.stderr.on("data", (d) => (stderr += d.toString()));
    proc.on("error", reject);
    proc.on("close", (code) =>
      code === 0 ? resolve() : reject(new Error(`${label} exited ${code}: ${stderr.slice(-800)}`))
    );
    const timer = setTimeout(() => {
      proc.kill();
      reject(new Error(`${label} timed out after ${timeoutMs / 1000}s`));
    }, timeoutMs);
    proc.on("close", () => clearTimeout(timer));
  });
}

export interface VocabCoachSession {
  status: "generated" | "not_yet_available";
  nextSessionAvailableAt: string | null;
  sessionId: string | null;
  topic: string | null;
  subtopic: string | null;
  taskType: string | null;
  angle: string | null;
  scenarioText: string | null;
  instructionFinal: string | null;
  // source_bank distinguishes topic/task_type collocations from the new
  // (v1_2) bare academic_word entries; structural_hint is only ever present
  // on academic_word entries and is intentionally NOT shown by default in
  // the UI -- see VocabCoachPeelSession's "Need a hint?" affordance, per
  // Academic_Words_Redesign_Spec_v1.docx Section 4.
  suggestedVocabulary: Array<{
    phrase: string;
    topic?: string;
    subtopic?: string;
    source_bank?: string;
    part_of_speech?: string;
    structural_hint?: string;
  }>;
  reviewItems: Array<{ phrase: string; box: string; note: string }>;
  lretBiasApplied: boolean;
  lretBiasNote: string | null;
  // v25 (2026-07-23): lretBiasApplied/lretBiasNote above now reflect the
  // COMBINED bias (LRET + Evaluator + Practice engagement-history), not just
  // LRET -- names kept for backward compatibility with existing UI code.
  // biasSources breaks out each source's own raw tally for transparency (e.g.
  // a future trainer-facing "why was this word picked" view); evaluatorOnly
  // surfaces genuine Evaluator lexical/style weaknesses that don't have a
  // corresponding bank item type to bias toward yet (see
  // vocab_coach_selection_engine_v1_3.py's module docstring) -- shown so this
  // gap is visible rather than silently invisible.
  biasSources: {
    lretSessions: Record<string, number>;
    evaluatorDevelopmentTargets: Record<string, number>;
    engagementHistoryPractice: Record<string, number>;
  };
  evaluatorSurfacedOnly: Array<{ skill_id?: string; domain?: string; priority_index?: number }>;
  // v26 (2026-07-23): whether this session's rotation was restricted to the
  // latest essay's classified topic (topicLockRequested is what
  // classifyEssayTopic() produced; topicLockApplied is false if that topic
  // didn't match any real topic in the bank in use, or if there was no
  // essay/no confident classification at all -- either way rotation still
  // ran, just across all topics as before).
  topicLockRequested: string | null;
  topicLockApplied: boolean;
  filePath: string;
}

// v25 (2026-07-23): generates a FRESH engagement-history file right before
// vocab coach selection runs, rather than relying on refreshLearnerProfile()
// having run recently (that function writes to a per-refresh-call stamped
// path under a specific essay's sessionDir, not a stable well-known
// location -- unsuitable to depend on here). Same aggregator, same cheap
// cost (verified <1s in refreshLearnerProfile's own testing) -- reusing
// ENGAGEMENT_HISTORY_AGGREGATOR_SCRIPT rather than inventing a second one.
// Best-effort: returns undefined (not a throw) on any failure, since
// engagement-history is an optional, additive bias input for vocab coach
// selection -- same posture as the optional score-contract/lret-sessions
// inputs already handled below.
async function generateFreshEngagementHistory(studentId: string, sessionsDir: string): Promise<string | undefined> {
  try {
    const latest = submissionsFor(studentId).find((s) => s.status === "done" && s.sessionDir);
    const refreshDir = path.join(sessionsDir, "engagement_for_vocab_coach");
    fs.mkdirSync(refreshDir, { recursive: true });
    const stamp = Date.now();
    const exportFile = path.join(refreshDir, `export_${stamp}.json`);
    const engagementFile = path.join(refreshDir, `engagement_history_${stamp}.json`);
    fs.writeFileSync(
      exportFile,
      JSON.stringify({
        student_id: studentId,
        practice_results: practiceResultsFor(studentId),
        mission_results: missionResultsFor(studentId),
      })
    );
    await runPythonScript(
      ENGAGEMENT_HISTORY_AGGREGATOR_SCRIPT,
      [
        "--practice-mission-export",
        exportFile,
        "--exercise-bank",
        EXERCISE_BANK_PATH,
        ...(latest?.sessionDir ? ["--session-dir", latest.sessionDir] : []),
        "--student-id",
        studentId,
        "--output",
        engagementFile,
      ],
      REFRESH_TIMEOUT_MS,
      "engagement history for vocab coach"
    );
    return fs.existsSync(engagementFile) ? engagementFile : undefined;
  } catch (e) {
    console.error(`[ST.ELLA] Could not generate engagement history for vocab coach selection (${studentId}):`, e);
    return undefined;
  }
}

export async function runVocabCoachSession(studentId: string): Promise<VocabCoachSession> {
  const sessionsDir = path.join(GOLD_PIPELINE_DIR, OUTPUT_ROOT, "vocab_coach_sessions", studentId);
  fs.mkdirSync(sessionsDir, { recursive: true });
  const outFile = path.join(sessionsDir, `session_${Date.now()}.json`);
  const ledgerPath = vocabCoachLedgerPath(studentId);
  const lretSessions = findRecentLretSessionPaths(studentId);
  const scoreContract = findLatestScoreContractPath(studentId);
  // v25: three-source family bias -- see vocab_coach_selection_engine_v1_3.py's
  // module docstring. Both new inputs are optional/best-effort; a missing
  // evaluator file or a failed engagement-history generation just means that
  // source contributes nothing to the combined tally, same graceful
  // degradation as the pre-existing score-contract/lret-sessions inputs.
  const evaluatorOutput = findLatestEvaluatorOutputPath(studentId);
  const engagementHistory = await generateFreshEngagementHistory(studentId, sessionsDir);
  // v26: topic-matching -- see classifyEssayTopic()'s module comment and
  // vocab_coach_selection_engine_v1_4.py's --topic-lock. undefined (arg
  // omitted below) when there's no essay yet or its topic wasn't confidently
  // classified -- rotation falls back to across-all-topics, unchanged.
  const topicLock = findLatestEssayTopic(studentId);

  const args = [
    "--ledger",
    ledgerPath,
    "--topic-bank",
    VOCAB_COACH_TOPIC_BANK,
    "--task-type-bank",
    VOCAB_COACH_TASK_TYPE_BANK,
    "--prompt-bank",
    VOCAB_COACH_PROMPT_BANK,
    "--student-id",
    studentId,
    "--output",
    outFile,
  ];
  if (scoreContract) args.push("--score-contract", scoreContract);
  if (lretSessions.length > 0) args.push("--lret-sessions", ...lretSessions);
  if (evaluatorOutput) args.push("--evaluator", evaluatorOutput);
  if (engagementHistory) args.push("--engagement-history", engagementHistory);
  if (topicLock) args.push("--topic-lock", topicLock);

  await runPythonScript(VOCAB_COACH_SELECTION_SCRIPT, args, VOCAB_COACH_TIMEOUT_MS, "vocab coach selection");

  if (!fs.existsSync(outFile)) throw new Error("Vocabulary Coach selection produced no output file.");
  const raw = JSON.parse(fs.readFileSync(outFile, "utf8"));

  if (raw.status === "not_yet_available") {
    return {
      status: "not_yet_available",
      nextSessionAvailableAt: raw.next_session_available_at ?? null,
      sessionId: null,
      topic: null,
      subtopic: null,
      taskType: null,
      angle: null,
      scenarioText: null,
      instructionFinal: null,
      suggestedVocabulary: [],
      reviewItems: [],
      lretBiasApplied: false,
      lretBiasNote: null,
      biasSources: { lretSessions: {}, evaluatorDevelopmentTargets: {}, engagementHistoryPractice: {} },
      evaluatorSurfacedOnly: [],
      topicLockRequested: topicLock ?? null,
      topicLockApplied: false,
      filePath: outFile,
    };
  }

  const rotation = raw.rotation ?? {};
  const prompt = raw.prompt ?? {};
  const bias = raw.lret_family_bias ?? {};
  const biasSourcesRaw = bias.sources ?? {};
  const topicLockInfo = raw.topic_lock ?? {};
  return {
    status: "generated",
    nextSessionAvailableAt: null,
    sessionId: raw.session_id ?? null,
    topic: rotation.topic ?? null,
    subtopic: rotation.subtopic ?? null,
    taskType: rotation.task_type ?? null,
    angle: rotation.angle ?? null,
    scenarioText: prompt.scenario_text ?? null,
    instructionFinal: prompt.instruction_final ?? null,
    suggestedVocabulary: Array.isArray(prompt.suggested_vocabulary) ? prompt.suggested_vocabulary : [],
    reviewItems: Array.isArray(raw.review_items) ? raw.review_items : [],
    lretBiasApplied: Boolean(bias.bias_applied),
    lretBiasNote: bias.note ?? null,
    biasSources: {
      lretSessions: biasSourcesRaw.lret_sessions ?? {},
      evaluatorDevelopmentTargets: biasSourcesRaw.evaluator_development_targets ?? {},
      engagementHistoryPractice: biasSourcesRaw.engagement_history_practice ?? {},
    },
    evaluatorSurfacedOnly: Array.isArray(bias.evaluator_surfaced_only_no_bank_mapping)
      ? bias.evaluator_surfaced_only_no_bank_mapping
      : [],
    topicLockRequested: topicLockInfo.requested ?? null,
    topicLockApplied: Boolean(topicLockInfo.applied),
    filePath: outFile,
  };
}

export interface VocabCoachItemVerdict {
  phrase: string;
  source: "new" | "review" | string;
  verdict: "used_correctly" | "used_but_awkward" | "attempted_incorrectly" | "not_used" | "needs_review" | string;
  evidence: string;
}

export interface VocabCoachSubmitResult {
  itemVerdicts: VocabCoachItemVerdict[];
  paragraphNote: { oneIdeaOk: boolean | null; note: string };
  llmChecked: boolean;
}

export async function submitVocabCoachResponse(
  studentId: string,
  sessionFilePath: string,
  responseText: string
): Promise<VocabCoachSubmitResult> {
  if (!fs.existsSync(sessionFilePath)) {
    throw new Error("Vocabulary Coach session file not found — session may have expired or been cleaned up.");
  }
  const sessionsDir = path.dirname(sessionFilePath);
  const stamp = Date.now();
  const gradingFile = path.join(sessionsDir, `grading_${stamp}.json`);

  const useLlm = Boolean(process.env.OPENAI_API_KEY);
  const graderArgs = [
    "--session",
    sessionFilePath,
    "--response",
    responseText,
    "--output",
    gradingFile,
    ...(useLlm ? ["--use-llm"] : []),
  ];
  await runPythonScript(VOCAB_COACH_GRADER_SCRIPT, graderArgs, VOCAB_COACH_TIMEOUT_MS, "vocab coach grading");
  if (!fs.existsSync(gradingFile)) throw new Error("Vocabulary Coach grading produced no output file.");
  const grading = JSON.parse(fs.readFileSync(gradingFile, "utf8"));

  const ledgerArgs = [
    "--session",
    sessionFilePath,
    "--grading",
    gradingFile,
    "--ledger",
    vocabCoachLedgerPath(studentId),
  ];
  await runPythonScript(VOCAB_COACH_LEDGER_SCRIPT, ledgerArgs, VOCAB_COACH_TIMEOUT_MS, "vocab coach ledger update");

  return {
    itemVerdicts: Array.isArray(grading.item_verdicts)
      ? grading.item_verdicts.map((v: any) => ({
          phrase: v.phrase,
          source: v.source,
          verdict: v.verdict,
          evidence: v.evidence ?? "",
        }))
      : [],
    paragraphNote: {
      oneIdeaOk: grading.paragraph_note?.one_idea_ok ?? null,
      note: grading.paragraph_note?.note ?? "",
    },
    llmChecked: Boolean(grading.use_llm),
  };
}

// v16: Vocabulary Coach mastery view (Pipeline_Frontend_Spec_v2 §2). Reads the
// same ledger vocab_coach_ledger_update_v1_1.py already writes after every
// PEEL session — no new engine work, this is a rollup of existing state.
//
// One schema quirk worth flagging: an item's `next_due_session` is a SESSION
// INDEX (ledger.sessions_completed at the time it becomes due again), not a
// calendar date, so "due for review" below compares against sessionsCompleted
// rather than today's date.
//
// vocabularyBankCount is scoped honestly to what this ledger actually proves:
// an item only leaves the "new" box after a real used_correctly verdict (see
// update_new_item in the ledger engine), so "box !== new" is a true genuine-use
// count. It does NOT yet include correct use inside essays, Practice Engine,
// or Essay Revision — that cross-engine rollup isn't wired yet (see
// Pipeline_Frontend_Spec_v2 §2 and the companion LRET spec §5.3).
export interface VocabCoachMasterySummary {
  sessionsCompleted: number;
  boxCounts: { new: number; box_1: number; box_2: number; box_3: number; mastered: number };
  dueForReview: Array<{ phrase: string; box: string; topic: string | null }>;
  recentlyMastered: Array<{ phrase: string; topic: string | null }>;
  vocabularyBankCount: number;
}

export function loadVocabCoachMasterySummary(studentId: string): VocabCoachMasterySummary | null {
  const ledgerPath = vocabCoachLedgerPath(studentId);
  if (!fs.existsSync(ledgerPath)) return null;

  let ledger: any;
  try {
    ledger = JSON.parse(fs.readFileSync(ledgerPath, "utf8"));
  } catch {
    return null;
  }

  const items: Record<string, any> = ledger.items ?? {};
  const sessionsCompleted: number = ledger.sessions_completed ?? 0;
  const boxCounts = { new: 0, box_1: 0, box_2: 0, box_3: 0, mastered: 0 };
  const dueForReview: Array<{ phrase: string; box: string; topic: string | null }> = [];
  const masteredEntries: Array<{ phrase: string; topic: string | null; lastSeen: number }> = [];
  let vocabularyBankCount = 0;

  for (const [phrase, raw] of Object.entries(items)) {
    const entry = raw as any;
    const box: string = entry.box ?? "new";
    if (box in boxCounts) (boxCounts as Record<string, number>)[box] += 1;
    if (box !== "new") vocabularyBankCount += 1;

    if (
      box !== "mastered" &&
      box !== "new" &&
      typeof entry.next_due_session === "number" &&
      entry.next_due_session <= sessionsCompleted
    ) {
      dueForReview.push({ phrase, box, topic: entry.topic ?? null });
    }
    if (box === "mastered") {
      masteredEntries.push({ phrase, topic: entry.topic ?? null, lastSeen: entry.last_seen_session ?? 0 });
    }
  }

  masteredEntries.sort((a, b) => b.lastSeen - a.lastSeen);

  return {
    sessionsCompleted,
    boxCounts,
    dueForReview: dueForReview.slice(0, 8),
    recentlyMastered: masteredEntries.slice(0, 5).map(({ phrase, topic }) => ({ phrase, topic })),
    vocabularyBankCount,
  };
}

// v17: daily cross-engine digest — Pipeline_Frontend_Spec_v2 §4. Pulls from
// three separate places (practice_results + mission_results, both SQLite;
// vocab_coach_sessions/{studentId}/session_*.json on disk) and reads each
// with its own native notion of "today" -- this function is the one shared
// place that reconciles them into a single UTC calendar day, which is the
// "one shared activity-log format" gap the spec called out. Nothing here
// grades anything; it only counts what each engine already recorded.
export interface DailyDigest {
  exercisesCompleted: number;
  missionsCompleted: number;
  newWordsLearned: number;
  workOnNext: string | null;
  hasActivity: boolean;
}

function isToday(isoString: string): boolean {
  const d = new Date(isoString);
  const now = new Date();
  return (
    d.getUTCFullYear() === now.getUTCFullYear() &&
    d.getUTCMonth() === now.getUTCMonth() &&
    d.getUTCDate() === now.getUTCDate()
  );
}

// v19: factored out of loadDailyDigest so getSessionFlowStatus (below) can
// reuse the exact same "did a real graded Vocabulary Coach attempt happen
// today" signal instead of re-deriving it. session_*.json is written at
// session-GENERATION time (see runVocabCoachSession), not grading time, so a
// file existing today only proves a session was opened, not completed —
// this cross-references the ledger's last_seen_session (only ever set by a
// real graded submission, see vocab_coach_ledger_update_v1_1.py's
// update_new_item/update_item) against today's session indices to get an
// honest "graded today" signal.
function todaysGradedVocabSessionIndices(studentId: string): Set<number> {
  const todaysSessionIndices = new Set<number>();
  try {
    const sessionsDir = path.join(GOLD_PIPELINE_DIR, OUTPUT_ROOT, "vocab_coach_sessions", studentId);
    if (fs.existsSync(sessionsDir)) {
      for (const name of fs.readdirSync(sessionsDir)) {
        if (!name.startsWith("session_") || !name.endsWith(".json")) continue;
        const full = path.join(sessionsDir, name);
        const stat = fs.statSync(full);
        if (stat.mtime.toDateString() !== new Date().toDateString()) continue;
        try {
          const raw = JSON.parse(fs.readFileSync(full, "utf8"));
          if (typeof raw.session_index === "number") todaysSessionIndices.add(raw.session_index);
        } catch {
          // skip unreadable/partial session file
        }
      }
    }
  } catch {
    // best-effort — degrades to "no sessions found today"
  }
  return todaysSessionIndices;
}

function vocabCoachGradedToday(studentId: string): boolean {
  const todaysSessionIndices = todaysGradedVocabSessionIndices(studentId);
  if (todaysSessionIndices.size === 0) return false;
  try {
    const ledgerPath = vocabCoachLedgerPath(studentId);
    if (!fs.existsSync(ledgerPath)) return false;
    const ledger = JSON.parse(fs.readFileSync(ledgerPath, "utf8"));
    for (const entry of Object.values<any>(ledger.items ?? {})) {
      if (typeof entry.last_seen_session === "number" && todaysSessionIndices.has(entry.last_seen_session)) {
        return true;
      }
    }
  } catch {
    // best-effort
  }
  return false;
}

export function loadDailyDigest(studentId: string, workOnNext: string | null = null): DailyDigest {
  const todaysPractice = practiceResultsFor(studentId).filter((r) => isToday(r.at));
  const exercisesCompleted = todaysPractice.reduce((sum, r) => sum + (r.total ?? 0), 0);

  const missionsCompleted = missionResultsFor(studentId).filter(
    (m) => isToday(m.at) && (m.outcome === "pass" || m.outcome === "partial_pass")
  ).length;

  // v19: reuses todaysGradedVocabSessionIndices (factored out for
  // getSessionFlowStatus below) instead of re-deriving today's session
  // indices inline. "New words learned" still requires last_outcome ===
  // "used_correctly" specifically — an item only reaches box_1+ after that
  // real verdict (see update_new_item in vocab_coach_ledger_update_v1_1.py) —
  // so this is a true count, not an estimate.
  let newWordsLearned = 0;
  try {
    const todaysSessionIndices = todaysGradedVocabSessionIndices(studentId);
    if (todaysSessionIndices.size > 0) {
      const ledgerPath = vocabCoachLedgerPath(studentId);
      if (fs.existsSync(ledgerPath)) {
        const ledger = JSON.parse(fs.readFileSync(ledgerPath, "utf8"));
        for (const entry of Object.values<any>(ledger.items ?? {})) {
          if (
            entry.last_outcome === "used_correctly" &&
            typeof entry.last_seen_session === "number" &&
            todaysSessionIndices.has(entry.last_seen_session)
          ) {
            newWordsLearned += 1;
          }
        }
      }
    }
  } catch {
    // Best-effort — the digest degrades to 0 rather than failing the page.
  }

  return {
    exercisesCompleted,
    missionsCompleted,
    newWordsLearned,
    workOnNext,
    hasActivity: exercisesCompleted > 0 || missionsCompleted > 0 || newWordsLearned > 0,
  };
}

// v19: guided session-flow sequencing — Session_Flow_and_Vocab_Expansion_Spec_v1
// §0, per the student's own stated order: with a recent essay, practice →
// writing coach → vocabulary coach → essay revision, then a wrap-up; without
// one, practice → writing coach → vocabulary coach → wrap-up. Each step's
// "done today" signal reuses data every engine already records for its own
// purposes (practice_results / mission_results / the vocab ledger's
// last_seen_session) — no new tracking table. Essay revision isn't a daily
// thing like the other three, so it's scoped to "done for this specific
// essay" via revision_comparisons/ contents rather than "done today".
export type SessionFlowStepKey = "practice" | "writing_coach" | "vocabulary_coach" | "essay_revision";

export interface SessionFlowStep {
  key: SessionFlowStepKey;
  label: string;
  href: string;
  done: boolean;
}

export interface SessionFlowStatus {
  steps: SessionFlowStep[];
  currentIndex: number; // index of the first not-done step, or steps.length if all done
  cameFromEssay: boolean;
}

export function getSessionFlowStatus(
  studentId: string,
  opts: { sessionDir?: string | null; submissionId?: string | null } = {}
): SessionFlowStatus {
  const practiceDone = practiceResultsFor(studentId).some((r) => isToday(r.at));
  const writingCoachDone = missionResultsFor(studentId).some(
    (m) => isToday(m.at) && (m.outcome === "pass" || m.outcome === "partial_pass")
  );
  const vocabCoachDone = vocabCoachGradedToday(studentId);

  const steps: SessionFlowStep[] = [
    { key: "practice", label: "Practice", href: "/practice", done: practiceDone },
    { key: "writing_coach", label: "Writing Coach", href: "/writing-coach", done: writingCoachDone },
    { key: "vocabulary_coach", label: "Vocabulary Coach", href: "/vocabulary-coach", done: vocabCoachDone },
  ];

  const { sessionDir, submissionId } = opts;
  const cameFromEssay = !!(sessionDir && loadRevisionWorkspace(sessionDir));
  if (cameFromEssay && submissionId) {
    let revisionDone = false;
    try {
      const comparisonsDir = path.join(sessionDir!, "revision_comparisons");
      revisionDone = fs.existsSync(comparisonsDir) && fs.readdirSync(comparisonsDir).some((n) => n.startsWith("comparison_"));
    } catch {
      // best-effort
    }
    steps.push({
      key: "essay_revision",
      label: "Essay Revision",
      href: `/writing/revise/${submissionId}`,
      done: revisionDone,
    });
  }

  const currentIndex = steps.findIndex((s) => !s.done);
  return { steps, currentIndex: currentIndex === -1 ? steps.length : currentIndex, cameFromEssay };
}

// ---------------------------------------------------------------------------
// v12: essay revision AI comparison — the second stage of the revision
// engine, confirmed real via gold_revision_ai_comparison_generator_v1_7_1.py
// and a real rendered example the user shared (revision_ai_comparison_v1_7.html).
// Per the spec (gold_essay_revision_universal_engine_v1_7_1_spec.md): the
// model rewrite is generated from the ORIGINAL essay, never from the
// student's revision — the revision is used for comparison display only.
// This only makes sense to run after the student has written a revision, so
// it's a standalone script call (same reasoning as mission grading — no
// full-pipeline re-run needed) triggered separately from the resubmit-for-
// re-evaluation flow.
// ---------------------------------------------------------------------------

const REVISION_COMPARISON_SCRIPT = "gold_revision_ai_comparison_generator_v1_7_1.py";
const REVISION_COMPARISON_TIMEOUT_MS = 120 * 1000;

export interface RevisionComparisonItem {
  paragraphNumber: number;
  role: string;
  original: string;
  studentRevision: string;
  aiModel: string | null;
  whyStronger: string[];
  specificExampleUsed: string | null;
  lexicalUpgrades: Array<{ from: string; to: string; why: string }>;
}

export interface RevisionComparison {
  modelAvailable: boolean;
  generationStatus: string;
  fullModelEssay: string | null;
  fullModelWordCount: number;
  items: RevisionComparisonItem[];
}

export async function runRevisionComparison(
  sessionDir: string,
  opts: { originalText: string; revisedText: string; prompt: string; taskType?: string }
): Promise<RevisionComparison> {
  const workspaceFile = path.join(sessionDir, "10_revision_workspace.json");
  if (!fs.existsSync(workspaceFile)) {
    throw new Error("No revision workspace found for this essay.");
  }
  const attemptsDir = path.join(sessionDir, "revision_comparisons");
  fs.mkdirSync(attemptsDir, { recursive: true });
  const stamp = Date.now();
  const reqFile = path.join(attemptsDir, `request_${stamp}.json`);
  const outFileForEngine = path.join(attemptsDir, `output_${stamp}.json`);
  const resultFile = path.join(attemptsDir, `comparison_${stamp}.json`);

  fs.writeFileSync(
    reqFile,
    JSON.stringify({
      original: { essay_text: opts.originalText },
      revised: { essay_text: opts.revisedText },
      prompt: { prompt_text: opts.prompt, task_type: opts.taskType ?? "WT2" },
    })
  );
  // --revision-output is a required arg in the script's interface (normally
  // a separate revision-comparator artifact) but the fields it actually
  // reads are also accepted on --revision-request above; an empty-but-
  // truthy object here just satisfies release_gate.revision_output_present.
  fs.writeFileSync(outFileForEngine, JSON.stringify({ revised: { essay_text: opts.revisedText } }));

  await new Promise<void>((resolve, reject) => {
    const proc = spawn(
      "python",
      [
        REVISION_COMPARISON_SCRIPT,
        "--revision-request",
        reqFile,
        "--revision-output",
        outFileForEngine,
        "--workspace",
        workspaceFile,
        "--output",
        resultFile,
        "--pretty",
      ],
      { cwd: GOLD_PIPELINE_DIR, env: process.env, windowsHide: true }
    );
    let stderr = "";
    proc.stderr.on("data", (d) => (stderr += d.toString()));
    proc.on("error", reject);
    proc.on("close", (code) =>
      code === 0 ? resolve() : reject(new Error(`revision comparison exited ${code}: ${stderr.slice(-800)}`))
    );
    setTimeout(() => {
      proc.kill();
      reject(new Error("revision comparison timed out after 2 minutes"));
    }, REVISION_COMPARISON_TIMEOUT_MS);
  });

  if (!fs.existsSync(resultFile)) throw new Error("Revision comparison produced no output file.");
  const raw = JSON.parse(fs.readFileSync(resultFile, "utf8"));
  return {
    modelAvailable: !!raw.model_available_to_student,
    generationStatus: raw.generation_status ?? "unknown",
    fullModelEssay: raw.full_model_essay ?? null,
    fullModelWordCount: raw.full_model_word_count ?? 0,
    items: Array.isArray(raw.items)
      ? raw.items.map((it: any) => ({
          paragraphNumber: it.paragraph_number,
          role: it.role ?? "body",
          original: it.original_paragraph ?? "",
          studentRevision: it.student_revised_paragraph ?? "",
          aiModel: it.ai_model_paragraph ?? null,
          whyStronger: Array.isArray(it.why_structure_is_better) ? it.why_structure_is_better : [],
          specificExampleUsed: it.specific_example_used ?? null,
          lexicalUpgrades: Array.isArray(it.lexical_upgrades)
            ? it.lexical_upgrades
                .filter((u: any) => u && typeof u === "object")
                .map((u: any) => ({ from: u.from ?? "", to: u.to ?? "", why: u.why ?? "" }))
            : [],
        }))
      : [],
  };
}

// ---------------------------------------------------------------------------
// v20: essay revision scoped re-check — Session_Flow_and_Vocab_Expansion_Spec_v1
// §1 ("a scoped, real re-check, not a full re-band"). Today's /api/writing/
// revise/compare (runRevisionComparison above) deliberately never re-scores —
// its own comment says so. This adds a REAL check, but scoped to only the
// sentences the student actually rewrote: essay_revision_scoped_recheck_v1_0.py
// diffs original vs. revised text (paragraph-scoped difflib alignment), runs
// the real Detector (det_vip via det_vip_cli_bridge_v1_1.py) on each changed
// sentence's PARAGRAPH context, before and after, and reports a per-sentence
// before/after delta plus one honest aggregate summary string. It deliberately
// does NOT produce a holistic band, and does NOT claim Task Response or
// Coherence & Cohesion changed — see the engine's own module docstring for why
// (holistic criteria can't be judged from a handful of edited sentences, but
// local grammar/lexical accuracy can, sentence-by-sentence — exactly what the
// Detector already does per-sentence in the full pipeline's own
// 01d_detector_for_scorer.json rows).
//
// Design choice: SIBLING endpoint (/api/writing/revise/recheck), not folded
// into runRevisionComparison's request/response. Reasons:
//   1. Failure isolation — the AI-comparison call is an LLM full-paragraph
//      rewrite (up to 120s); this call is 1-6 short Detector subprocess calls
//      with a materially different cost/timeout profile (spec 1.2). Combining
//      them into one request means one slow/failing engine blocks or kills
//      the other's result.
//   2. Independently tunable safety caps — --max-detector-calls /
//      --timeout-seconds here are tuned for "a handful of sentences", not for
//      a whole-essay LLM generation.
//   3. RevisionWorkspaceClient.tsx still fires both from the SAME "Compare"
//      click via Promise.allSettled (see the component), so there's no extra
//      round trip perceived by the student — this is a backend separation of
//      concerns, not an extra UI step.
// Matches runRevisionComparison's own pattern exactly: a standalone script
// call (not the 27-stage orchestrator), request/output files written under
// the session dir for auditability, spawn + timeout + non-zero-exit rejection.
// ---------------------------------------------------------------------------

const REVISION_SCOPED_RECHECK_SCRIPT = "essay_revision_scoped_recheck_v1_0.py";
// v20: 3 minutes overall, 60s per Detector subprocess call, capped at 6 calls
// (3 changed paragraphs worth -- spec 1.2's "typically 1-5 sentences"). This
// call always passes --require-llm through (matching the existing pipeline's
// own det_vip config, which always enables it too), so a real production
// call involves real LLM latency per paragraph, not just rule/spaCy passes --
// hence a longer per-call budget than a purely rule-based check would need.
const REVISION_SCOPED_RECHECK_TIMEOUT_MS = 180 * 1000;

export interface RevisionRecheckErrorItem {
  family: string;
  rubric: string | null;
  quote: string;
  message: string;
  suggestedRevision: string | null;
  severity: string | null;
}

export interface RevisionRecheckSentence {
  originalText: string;
  revisedText: string;
  errorsBefore: RevisionRecheckErrorItem[];
  errorsAfter: RevisionRecheckErrorItem[];
  fixed: RevisionRecheckErrorItem[];
  introduced: RevisionRecheckErrorItem[];
  persisting: RevisionRecheckErrorItem[];
  status: string;
  statusLabel: string;
}

export interface RevisionScopedRecheck {
  sentencesRewritten: number;
  nowErrorFree: number;
  alreadyCleanRewrite: number;
  stillHasErrors: number;
  introducedNewErrorSentences: number;
  totalErrorsFixed: number;
  totalErrorsIntroduced: number;
  honestSummaryText: string;
  scopeDisclaimer: string;
  newSentencesAdded: number;
  sentencesRemoved: number;
  truncatedForCostCap: boolean;
  sentences: RevisionRecheckSentence[];
}

function mapRecheckError(raw: any): RevisionRecheckErrorItem {
  return {
    family: raw?.family ?? "UNKNOWN",
    rubric: raw?.rubric ?? null,
    quote: raw?.quote ?? "",
    message: raw?.message ?? "",
    suggestedRevision: raw?.suggested_revision ?? null,
    severity: raw?.severity ?? null,
  };
}

export async function runRevisionScopedRecheck(
  sessionDir: string,
  opts: { originalText: string; revisedText: string; prompt: string; taskType?: string }
): Promise<RevisionScopedRecheck> {
  const attemptsDir = path.join(sessionDir, "revision_scoped_rechecks");
  fs.mkdirSync(attemptsDir, { recursive: true });
  const stamp = Date.now();
  const reqFile = path.join(attemptsDir, `request_${stamp}.json`);
  const resultFile = path.join(attemptsDir, `recheck_${stamp}.json`);

  fs.writeFileSync(
    reqFile,
    JSON.stringify({
      original: { essay_text: opts.originalText },
      revised: { essay_text: opts.revisedText },
      prompt: { prompt_text: opts.prompt, task_type: opts.taskType ?? "WT2" },
    })
  );

  await new Promise<void>((resolve, reject) => {
    const proc = spawn(
      "python",
      [
        REVISION_SCOPED_RECHECK_SCRIPT,
        "--request",
        reqFile,
        "--output",
        resultFile,
        "--pretty",
        // Tuned for "a handful of sentences" (spec 1.2), not a whole essay —
        // see the module-level comment above for why this is a separate,
        // independently-tuned call rather than sharing runRevisionComparison's
        // 120s LLM-generation timeout.
        "--timeout-seconds",
        "60",
        "--max-detector-calls",
        "6",
      ],
      { cwd: GOLD_PIPELINE_DIR, env: process.env, windowsHide: true }
    );
    let stderr = "";
    proc.stderr.on("data", (d) => (stderr += d.toString()));
    proc.on("error", reject);
    proc.on("close", (code) =>
      code === 0 ? resolve() : reject(new Error(`revision scoped recheck exited ${code}: ${stderr.slice(-800)}`))
    );
    setTimeout(() => {
      proc.kill();
      reject(new Error("revision scoped recheck timed out after 3 minutes"));
    }, REVISION_SCOPED_RECHECK_TIMEOUT_MS);
  });

  if (!fs.existsSync(resultFile)) throw new Error("Revision scoped recheck produced no output file.");
  const raw = JSON.parse(fs.readFileSync(resultFile, "utf8"));
  const summary = raw.summary ?? {};
  const sentenceResults: any[] = Array.isArray(raw.sentence_results) ? raw.sentence_results : [];

  return {
    sentencesRewritten: summary.sentences_rewritten ?? 0,
    nowErrorFree: summary.now_error_free ?? 0,
    alreadyCleanRewrite: summary.already_clean_rewrite ?? 0,
    stillHasErrors: summary.still_has_errors ?? 0,
    introducedNewErrorSentences: summary.introduced_new_error_sentences ?? 0,
    totalErrorsFixed: summary.total_errors_fixed ?? 0,
    totalErrorsIntroduced: summary.total_errors_introduced ?? 0,
    honestSummaryText: summary.honest_summary_text ?? "",
    scopeDisclaimer: summary.scope_disclaimer ?? "",
    newSentencesAdded: summary.new_sentences_added ?? 0,
    sentencesRemoved: summary.sentences_removed ?? 0,
    truncatedForCostCap: !!summary.truncated_for_cost_cap,
    sentences: sentenceResults.map((s) => ({
      originalText: s.original_text ?? "",
      revisedText: s.revised_text ?? "",
      errorsBefore: Array.isArray(s.errors_before) ? s.errors_before.map(mapRecheckError) : [],
      errorsAfter: Array.isArray(s.errors_after) ? s.errors_after.map(mapRecheckError) : [],
      fixed: Array.isArray(s.fixed) ? s.fixed.map(mapRecheckError) : [],
      introduced: Array.isArray(s.introduced) ? s.introduced.map(mapRecheckError) : [],
      persisting: Array.isArray(s.persisting) ? s.persisting.map(mapRecheckError) : [],
      status: s.status ?? "unknown",
      statusLabel: s.status_label ?? "",
    })),
  };
}

// ---------------------------------------------------------------------------
// v13: Vocabulary Coach (LRET — the app's lexical precision engine).
//
// Confirmed by reading lret_engine_v1_12_0_meaning_sensitive_detector_families.py
// directly (not assumed): this is NOT a narrow "vocabulary error" checker.
// The Evaluator extracts every meaningful lexical unit (a word, phrase, or
// collocation) from the essay and hands them to LRET, which classifies each
// one into exactly one of four buckets — FIX (a real error), ENHANCE
// (correct, but a stronger option exists), CLARIFY (too vague — the engine
// asks the student to state what they meant), KEEP (already good). Verified
// against a real run's 07d_lret_session.json: top-level keys are
// fix_units/enhance_units/clarify_units/keep_units plus
// lexical_profile.classification_distribution with the four counts.
// ---------------------------------------------------------------------------

export interface LretUnit {
  unitText: string;
  context: string;
  confidence: number | null;
  reason: string;
  suggestions: string[];
  clarificationGuidance: string[];
  keepType: string;
}

export interface LretSession {
  counts: { fix: number; enhance: number; clarify: number; keep: number };
  fixUnits: LretUnit[];
  enhanceUnits: LretUnit[];
  clarifyUnits: LretUnit[];
  keepUnits: LretUnit[];
}

function mapLretUnit(u: any): LretUnit {
  return {
    unitText: u.unit_text ?? u.unit_norm ?? "",
    context: u.context ?? "",
    confidence: typeof u.candidate_value === "number" ? u.candidate_value : null,
    reason: u.reason ?? "",
    suggestions: Array.isArray(u.suggestions)
      ? u.suggestions
          .filter((s: any) => s && (typeof s === "string" || s.validation?.accepted !== false))
          .map((s: any) => (typeof s === "string" ? s : s.text))
          .filter(Boolean)
      : [],
    clarificationGuidance: Array.isArray(u.clarification_guidance) ? u.clarification_guidance : [],
    keepType: (u.keep_type ?? "").replace(/^keep_/, "").replace(/_/g, " "),
  };
}

export function loadLretSession(sessionDir: string): LretSession | undefined {
  const file = path.join(sessionDir, "07d_lret_session.json");
  if (!fs.existsSync(file)) return undefined;
  try {
    const raw = JSON.parse(fs.readFileSync(file, "utf8"));
    const dist = raw.lexical_profile?.classification_distribution ?? {};
    return {
      counts: {
        fix: dist.FIX ?? (raw.fix_units?.length ?? 0),
        enhance: dist.ENHANCE ?? (raw.enhance_units?.length ?? 0),
        clarify: dist.CLARIFY ?? (raw.clarify_units?.length ?? 0),
        keep: dist.KEEP ?? (raw.keep_units?.length ?? 0),
      },
      fixUnits: Array.isArray(raw.fix_units) ? raw.fix_units.map(mapLretUnit) : [],
      enhanceUnits: Array.isArray(raw.enhance_units) ? raw.enhance_units.map(mapLretUnit) : [],
      clarifyUnits: Array.isArray(raw.clarify_units) ? raw.clarify_units.map(mapLretUnit) : [],
      keepUnits: Array.isArray(raw.keep_units) ? raw.keep_units.map(mapLretUnit) : [],
    };
  } catch (e) {
    console.error(`[ST.ELLA] Could not read LRET session from ${sessionDir}:`, e);
    return undefined;
  }
}

// ---------------------------------------------------------------------------
// v21 (2026-07-23): the continuous-loop refresh path. Product-owner decision,
// verbatim: "all 3 should!!!" (Practice/Writing Coach/Vocabulary Coach/Essay
// Revision should feed LIE with real learned history, not presence checks)
// and "should be a continuous loop" (LIE/Priority Engine should refresh in
// reaction to that activity BETWEEN essays, not only when a new essay is
// submitted).
//
// Deliberately does NOT call gold_full_pipeline_orchestrator_v1_4_9.py.
// Traced its STAGE_ORDER directly: prior_context, intake, detector,
// detector_for_scorer, errormap, detector_for_evaluator, metric_profile,
// evaluator, evaluator_rubric_bridge, scorer, verifier, adjudicator,
// score_contract, progress_tracker(_persist), priority_input, priority,
// priority_normalized, directive, feedback_engine, feedback_report,
// lret_session, writing_coach(_raw), practice_session,
// mission_response_grading, learner_profile, persisted_profile,
// skills_progress, learning_roadmap, service_routing, progress_snapshot,
// revision_workspace(_launch_packet), evidence_fusion. Every stage up to and
// including lret_session/writing_coach/practice_session is essay-scoped and
// LLM-heavy (Detector/Scorer/Verifier/Adjudicator/Evaluator) -- none of
// Practice/Writing Coach/Vocabulary Coach/Essay Revision activity changes
// the essay itself, so none of that has anything new to recompute. This
// function only spawns two cheap, non-LLM standalone scripts
// (gold_engagement_history_aggregator_v1_0.py, new, and LIE itself,
// gold_lie_profile_builder_standalone_v1_4_7.py), reusing the SAME frozen
// per-essay artifacts (00_submission.json, 02d_final_score_contract.json,
// 01b_errormap_v3.json, 03b_priority_normalized_v1_4_3.json,
// 04_directive_v2.json, 06_feedback_report_v6c.json,
// 07_evaluator_output.json, 07d_lret_session.json,
// 07e_writing_coach_output.json, 07f_gold_practice_session.json) the
// original essay-submission run already produced and never rewriting them.
//
// Priority Engine is deliberately NOT re-run. Traced priority_input's own
// command (priority_input_builder_standalone_v1_4_9.py, in
// gold_engine_commands_full_v1_4_20.json): --detector (01d, essay-scoped,
// frozen), --submission (frozen), --scorer (frozen), --evaluator (frozen),
// --lret {prior_context} (the PRECEDING essay's LRET signal -- untouched by
// any of this refresh's four triggers), --vocab-ledger (read live only at
// essay-submission time, not by this refresh path). None of Practice/
// Writing Coach/Vocabulary Coach/Essay Revision activity changes any of
// those inputs, so Priority Engine has nothing new to read here --
// re-running it would be pure wasted work.
//
// gold_profile_persist_v1.py IS re-run (also cheap: pure JSON merge, no
// subprocess fan-out, no LLM -- see build_persisted_profile(), it's a
// copy.deepcopy plus a few field assignments) so the persisted continuity
// file ({learner_profiles_dir}/{studentId}_gold_profile.json, read by the
// NEXT essay's prior_context) reflects the freshest learner_profile
// snapshot too, not just this essay's own session-dir copy of it.
//
// v22 (2026-07-23): Defect 1 fix (product-owner-confirmed bug). This used to
// write its outputs back over the SAME session-dir file paths the original
// essay-submission run wrote (08_gold_learner_profile.json/08b/08c/09/08a) --
// which meant a trainer opening the QA/debug view of THAT essay days later
// would see a file mutated by whatever the student did afterward, not what
// the AI actually judged at submission time. Fixed: writes now go to
// per-STUDENT "current" files instead (currentLearnerProfilePath(studentId)
// and friends, defined above -- {learner_profiles_dir}/{studentId}_current_*,
// same convention as vocabCoachLedgerPath) and the essay's own session-dir
// artifacts are never opened for writing by this function again (confirmed:
// every fs.existsSync/path.join below that used to target sessionDir for a
// *_output write now targets a current*Path() call instead; sessionDir is
// still read from for this essay's frozen inputs, which is correct and
// unchanged). study-plan.ts's getLearningRoadmap() now reads
// currentRoadmapPath(studentId) first and only falls back to the session's
// own 08c if no refresh/reseed has ever happened for this student.
// runGoldEvaluation()'s success path reseeds the current files from each new
// essay's own fresh originals (see reseedCurrentProfileFromSession above),
// so "current" always has a correct baseline even before the first
// post-essay refresh trigger fires.
//
// No-essay-yet case: if this student has no "done" submission with a
// sessionDir (or its frozen artifacts are incomplete -- e.g. a run that
// failed partway through the orchestrator), there is nothing safe to
// refresh -- returns silently rather than throwing (and does NOT write a
// refresh-attempt row below -- that's an expected no-op, not a failed
// attempt).
//
// Error handling: this function NEVER rejects -- every failure path is
// caught and logged, never re-thrown, so the four call sites below (practice
// submit, writing-coach submit, vocabulary-coach submit, essay-revision
// compare/recheck) can call it fire-and-forget without needing their own
// try/catch. v22 (Defect 2 fix, product-owner-confirmed bug): that swallow
// used to be silent -- zero logging, zero persisted record, nothing a
// trainer or the PO could check. Now every real attempt (not the no-op
// early-returns above) logs via console.error("[ST.ELLA] ...") on failure,
// matching this codebase's established convention, AND records a row in the
// new learner_profile_refresh_attempts SQLite table (student_id, at, status,
// error_message -- see db.ts/store.ts) on both success and failure, so
// app/trainer/page.tsx can show a warning badge next to a student whose most
// recent attempt failed.
// ---------------------------------------------------------------------------

const ENGAGEMENT_HISTORY_AGGREGATOR_SCRIPT = "gold_engagement_history_aggregator_v1_0.py";
const LIE_PROFILE_BUILDER_SCRIPT = "gold_lie_profile_builder_standalone_v1_4_7.py";
const PROFILE_PERSIST_SCRIPT = "gold_profile_persist_v1.py";
// Pure Python, file-in/file-out, zero LLM calls, zero subprocess fan-out --
// this is 3 short-lived local processes reading a handful of small JSON
// files, not a re-score. 30s is a generous ceiling, not an expected runtime
// (a real run of all 3 scripts together took well under 2s in testing).
const REFRESH_TIMEOUT_MS = 30 * 1000;

const EXERCISE_BANK_PATH =
  process.env.STELLA_EXERCISE_BANK ?? path.join(GOLD_PIPELINE_DIR, "va_exercise_bank_v11d_approved.jsonl");

export async function refreshLearnerProfile(studentId: string): Promise<void> {
  try {
    const latest = submissionsFor(studentId).find((s) => s.status === "done" && s.sessionDir);
    if (!latest?.sessionDir || !fs.existsSync(latest.sessionDir)) {
      return; // no essay submitted yet (or its session dir is gone) -- nothing to attach a refresh to.
    }
    const sessionDir = latest.sessionDir;

    const requiredFrozenArtifacts = [
      "00_submission.json",
      "02d_final_score_contract.json",
      "01b_errormap_v3.json",
    ].map((f) => path.join(sessionDir, f));
    if (!requiredFrozenArtifacts.every((f) => fs.existsSync(f))) {
      return; // LIE's own required inputs are incomplete for this session -- nothing safe to refresh.
    }

    const refreshDir = path.join(sessionDir, "engagement_refresh");
    fs.mkdirSync(refreshDir, { recursive: true });
    const stamp = Date.now();
    const exportFile = path.join(refreshDir, `export_${stamp}.json`);
    const engagementFile = path.join(refreshDir, `engagement_history_${stamp}.json`);

    // JSON-export step, not direct Python sqlite3 access to stella.db --
    // see gold_engagement_history_aggregator_v1_0.py's module docstring for
    // why (Python's sqlite3 WAS confirmed able to read the WAL-mode file
    // directly, but store.ts already owns tested query logic for exactly
    // this data, and the production target is Windows, where a second OS
    // process opening the same better-sqlite3-owned file concurrently is a
    // real, avoidable risk this sidesteps entirely).
    fs.writeFileSync(
      exportFile,
      JSON.stringify({
        student_id: studentId,
        practice_results: practiceResultsFor(studentId),
        mission_results: missionResultsFor(studentId),
      })
    );

    await runPythonScript(
      ENGAGEMENT_HISTORY_AGGREGATOR_SCRIPT,
      [
        "--practice-mission-export",
        exportFile,
        "--exercise-bank",
        EXERCISE_BANK_PATH,
        "--session-dir",
        sessionDir,
        "--student-id",
        studentId,
        "--output",
        engagementFile,
      ],
      REFRESH_TIMEOUT_MS,
      "engagement history aggregation"
    );

    const vocabLedger = vocabCoachLedgerPath(studentId);
    await runPythonScript(
      LIE_PROFILE_BUILDER_SCRIPT,
      [
        "--submission",
        path.join(sessionDir, "00_submission.json"),
        "--score-contract",
        path.join(sessionDir, "02d_final_score_contract.json"),
        "--errormap",
        path.join(sessionDir, "01b_errormap_v3.json"),
        "--priority",
        path.join(sessionDir, "03b_priority_normalized_v1_4_3.json"),
        "--directive",
        path.join(sessionDir, "04_directive_v2.json"),
        "--feedback",
        path.join(sessionDir, "06_feedback_report_v6c.json"),
        "--evaluator",
        path.join(sessionDir, "07_evaluator_output.json"),
        "--lret",
        path.join(sessionDir, "07d_lret_session.json"),
        "--writing-coach",
        path.join(sessionDir, "07e_writing_coach_output.json"),
        "--practice",
        path.join(sessionDir, "07f_gold_practice_session.json"),
        ...(fs.existsSync(vocabLedger) ? ["--vocabulary-coach", vocabLedger] : []),
        "--engagement-history",
        engagementFile,
        // v22: these four used to point at the essay's own session-dir
        // paths (08_gold_learner_profile.json/08b/08c/09) -- see Defect 1
        // fix in the module comment above. Now they point at the
        // per-STUDENT "current" files instead; the session dir's own
        // originals are never opened for writing here again.
        "--output",
        currentLearnerProfilePath(studentId),
        "--skills-progress-output",
        currentSkillsProgressPath(studentId),
        "--learning-roadmap-output",
        currentRoadmapPath(studentId),
        "--progress-snapshot-output",
        currentProgressSnapshotPath(studentId),
        "--pretty",
      ],
      REFRESH_TIMEOUT_MS,
      "LIE profile refresh"
    );

    // Keep the persisted continuity file in sync too -- see module comment
    // above for why this one IS re-run (cheap, deterministic, no LLM)
    // unlike Priority Engine.
    const priorContext = path.join(sessionDir, "00a_prior_context.json");
    const directiveFile = path.join(sessionDir, "04_directive_v2.json");
    await runPythonScript(
      PROFILE_PERSIST_SCRIPT,
      [
        // v22: reads the just-refreshed per-student current learner profile
        // (written above), not the session's own frozen 08 file.
        "--learner-profile",
        currentLearnerProfilePath(studentId),
        ...(fs.existsSync(priorContext) ? ["--prior-context", priorContext] : []),
        "--score-contract",
        path.join(sessionDir, "02d_final_score_contract.json"),
        ...(fs.existsSync(directiveFile) ? ["--directive", directiveFile] : []),
        "--session-id",
        path.basename(sessionDir),
        "--learner-profiles-dir",
        learnerProfilesDir(),
        // v22: was path.join(sessionDir, "08a_gold_persisted_profile.json")
        // -- see Defect 1 fix above. This script's own --learner-profiles-dir
        // continuity file ({studentId}_gold_profile.json) is unaffected by
        // this change; only this --output target moves off the session dir.
        "--output",
        currentPersistedProfilePath(studentId),
        "--pretty",
      ],
      REFRESH_TIMEOUT_MS,
      "learner profile persist refresh"
    );

    // v22 (Defect 2 fix): record a successful attempt so a trainer/PO can
    // confirm refreshes are actually happening, not just infer it from an
    // absence of failures.
    recordLearnerProfileRefreshAttempt(studentId, "success", null);
  } catch (e) {
    // Fire-and-forget by design (see every caller) -- a refresh problem must
    // never surface as, or cause, a student-facing error on an unrelated
    // request (saving a practice result, grading a mission, etc.). v22
    // (Defect 2 fix): logging alone used to be the only trace of a failure
    // (and even that wasn't reliably present) -- now also persists a
    // "failure" row so it's visible outside the server log too (trainer
    // console badge, see app/trainer/page.tsx).
    const message = e instanceof Error ? e.message : String(e);
    console.error(`[ST.ELLA] refreshLearnerProfile failed for ${studentId}:`, e);
    try {
      recordLearnerProfileRefreshAttempt(studentId, "failure", message);
    } catch (dbErr) {
      console.error(`[ST.ELLA] Could not persist refresh-failure record for ${studentId}:`, dbErr);
    }
  }
}
