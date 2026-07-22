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
// engines (vocab_coach_selection_engine_v1_1.py,
// vocab_coach_response_grader_v1_1.py, vocab_coach_ledger_update_v1_1.py) are
// registered in this config for documentation/consistency but are NOT part
// of the orchestrator's automatic per-essay STAGE_ORDER — they run on their
// own cooldown-gated cadence via the standalone runVocabCoach* functions
// below, not through a full 27-stage orchestrator invocation.
const ENGINE_CONFIG =
  process.env.STELLA_GOLD_ENGINE_CONFIG ?? "gold_engine_commands_full_v1_4_16.json";
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

const VOCAB_COACH_SELECTION_SCRIPT = "vocab_coach_selection_engine_v1_1.py";
const VOCAB_COACH_GRADER_SCRIPT = "vocab_coach_response_grader_v1_1.py";
const VOCAB_COACH_LEDGER_SCRIPT = "vocab_coach_ledger_update_v1_1.py";
const VOCAB_COACH_TOPIC_BANK = "vocab_coach_topic_bank_v1_3_0.json";
const VOCAB_COACH_TASK_TYPE_BANK = "vocab_coach_task_type_bank_v1_2_0.json";
const VOCAB_COACH_PROMPT_BANK = "vocab_coach_prompt_bank_v1_0_0.json";
const VOCAB_COACH_TIMEOUT_MS = 60 * 1000;
const VOCAB_COACH_MAX_LRET_SESSIONS = 5;

function learnerProfilesDir(): string {
  const dir = path.join(GOLD_PIPELINE_DIR, OUTPUT_ROOT, "learner_profiles");
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function vocabCoachLedgerPath(studentId: string): string {
  return path.join(learnerProfilesDir(), `${studentId}_vocab_coach_ledger.json`);
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
  suggestedVocabulary: Array<{ phrase: string; topic?: string; subtopic?: string }>;
  reviewItems: Array<{ phrase: string; box: string; note: string }>;
  lretBiasApplied: boolean;
  lretBiasNote: string | null;
  filePath: string;
}

export async function runVocabCoachSession(studentId: string): Promise<VocabCoachSession> {
  const sessionsDir = path.join(GOLD_PIPELINE_DIR, OUTPUT_ROOT, "vocab_coach_sessions", studentId);
  fs.mkdirSync(sessionsDir, { recursive: true });
  const outFile = path.join(sessionsDir, `session_${Date.now()}.json`);
  const ledgerPath = vocabCoachLedgerPath(studentId);
  const lretSessions = findRecentLretSessionPaths(studentId);
  const scoreContract = findLatestScoreContractPath(studentId);

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
      filePath: outFile,
    };
  }

  const rotation = raw.rotation ?? {};
  const prompt = raw.prompt ?? {};
  const bias = raw.lret_family_bias ?? {};
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
