// Bridges the web app to pipeline_runner_v14j.py (input-driven, ARCH-V14-1).
// Set STELLA_PIPELINE_DIR to override the default location of full_premium_v1.
// v8: unchanged logic. Confirmed during the pilot-readiness review that
// every practice exercise is graded instantly by answer-matching (no AI
// call), so practice has no per-use cost — only runEvaluation() below calls
// the paid model, and that's already capped via evaluations_left.
//
// v9 (pilot): runEvaluation() is now a tier router. Gold-plan users are sent
// to the hardened Gold orchestrator (goldPipeline.ts); premium/premium_pilot
// keep using this file's original pipeline_runner_v14j.py path — nothing
// about the premium path changed below.
// v27 (2026-07-23): premium/premium_pilot now route to the new scored-only
// Premium orchestrator (runPremiumScoredEvaluation() in goldPipeline.ts) —
// same real Detector/Evaluator/Scorer/Verifier/Adjudicator/Progress-Tracker/
// Priority-Engine/Directive/Feedback-Engine/LIE engines as Gold, no coaching/
// learning layer, still LLM-backed (Detector+Evaluator). Replaces
// pipeline_runner_v14j.py/full_premium entirely for these two plans — see
// PREMIUM_PIPELINE_SPEC_V1.docx for the full rationale. The old
// runPremiumEvaluation() function below is left in place, unused, per this
// project's convention (never delete/repurpose an old code path — just stop
// calling it) in case it's ever needed as a reference or rollback.
import { spawn } from "child_process";
import fs from "fs";
import path from "path";
import type { BankExercise, FeedbackReport } from "@/lib/types";
import { runGoldEvaluation, runPremiumScoredEvaluation } from "./goldPipeline";
import { normalizeParagraphBreaks } from "./text";

export const PIPELINE_DIR =
  process.env.STELLA_PIPELINE_DIR ?? path.resolve(process.cwd(), "..", "full_premium_v1");

const RUNNER = "pipeline_runner_v14j.py";
// v10: BANK_PATH used to assume frontend_v8 sat next to full_premium_v1
// (PIPELINE_DIR/../bank.jsonl). That broke once frontend_v8 moved out of
// OneDrive to C:\dev — PIPELINE_DIR itself no longer resolves anywhere
// real. The exercise bank actually lives in the "full pipleine" folder,
// which STELLA_GOLD_PIPELINE_DIR already points to, so reuse that instead
// of requiring yet another env var. STELLA_EXERCISE_BANK is available as
// an explicit override if the bank file ever moves somewhere else.
const BANK_PATH =
  process.env.STELLA_EXERCISE_BANK ??
  (process.env.STELLA_GOLD_PIPELINE_DIR
    ? path.join(process.env.STELLA_GOLD_PIPELINE_DIR, "va_exercise_bank_v11d_approved.jsonl")
    : path.resolve(PIPELINE_DIR, "..", "va_exercise_bank_v11d_approved.jsonl"));

// v16: normalized once already by the /api/evaluate route (see
// lib/server/text.ts for the full paragraph-boundary explanation) before
// this is called — normalizing again here too since idempotent (\n\n stays
// \n\n) and this guards any other future caller of runEvaluation directly.
export async function runEvaluation(input: {
  submissionId: string;
  studentId: string;
  prompt: string;
  essay: string;
  plan: string;
}): Promise<{ report: FeedbackReport; sessionDir: string }> {
  const normalized = { ...input, essay: normalizeParagraphBreaks(input.essay) };
  if (normalized.plan === "gold") {
    return runGoldEvaluation(normalized);
  }
  // v27: was runPremiumEvaluation(normalized) (pipeline_runner_v14j.py) --
  // see module comment above.
  return runPremiumScoredEvaluation(normalized);
}

// v27: unused now (see module comment above) -- kept, not deleted, per
// project convention. Was the premium/premium_pilot path before
// runPremiumScoredEvaluation() replaced it.
async function runPremiumEvaluation(input: {
  submissionId: string;
  studentId: string;
  prompt: string;
  essay: string;
}): Promise<{ report: FeedbackReport; sessionDir: string }> {
  const subDir = path.join(PIPELINE_DIR, "web_submissions");
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

  const before = listSessions();

  await new Promise<void>((resolve, reject) => {
    const proc = spawn("python", [RUNNER, "--input", subFile], {
      cwd: PIPELINE_DIR,
      env: process.env,
      windowsHide: true,
    });
    let stderr = "";
    proc.stderr.on("data", (d) => (stderr += d.toString()));
    proc.on("error", reject);
    proc.on("close", (code) =>
      code === 0
        ? resolve()
        : reject(new Error(`pipeline exited ${code}: ${stderr.slice(-800)}`))
    );
    setTimeout(() => {
      proc.kill();
      reject(new Error("pipeline timeout after 8 minutes"));
    }, 8 * 60 * 1000);
  });

  const after = listSessions();
  const newest = after.filter((s) => !before.includes(s)).sort().pop() ?? after.sort().pop();
  if (!newest) throw new Error("no session folder produced");

  const reportFile = path.join(PIPELINE_DIR, "sessions", newest, "06_feedback_report_v6c.json");
  if (!fs.existsSync(reportFile)) throw new Error(`report missing in ${newest}`);
  const report = JSON.parse(fs.readFileSync(reportFile, "utf8")) as FeedbackReport;
  return { report, sessionDir: newest };
}

function listSessions(): string[] {
  const dir = path.join(PIPELINE_DIR, "sessions");
  return fs.existsSync(dir) ? fs.readdirSync(dir).filter((d) => d.startsWith("session_")) : [];
}

// v24 (2026-07-23): CAPACITY_DOMAIN_TO_BANK_FAMILIES + expandWeakFamilies().
// Real bug found while wiring LRET/Vocab-Coach signals into Practice: weak-
// family targeting has likely NEVER actually worked. buildPracticeSession()
// does `unseen.filter(e => weak.has(e.family))`, where e.family is one of the
// exercise bank's ~60 UPPER_SNAKE_CASE names (SUBJECT_VERB_AGREEMENT,
// COLLOCATION, REGISTER_CONTROL, ...) confirmed directly against
// va_exercise_bank_v11d_approved.jsonl. `weak` was built from
// focus_area_feedback[].skill_tag, but skill_tag's real vocabulary (confirmed
// against priority_output_normalizer_standalone_v1_4_4.py) is one of THREE
// incompatible namespaces depending on which engine produced the focus area:
//   - ErrorMap-derived: 6 coarse lower_snake buckets (sentence_control,
//     lexical_precision, academic_style, argument_development,
//     cohesion_control, task_response_control) -- capacity_domain === skill_tag
//   - Evaluator-derived: an individual lower_snake skill_id (e.g.
//     "arg_claim_precision") -- capacity_domain is "evaluator_<domain>"
//   - LRET/Vocab-Coach lexical-derived: one of 5 tokens (LEXICAL_FORM_CONTROL,
//     COLLOCATION_CONTROL, LEXICAL_CONTROL, SEMANTIC_PHRASE_CONTROL) --
//     capacity_domain is "lexical_family_<fam>"
// None of these string-match the bank's family vocabulary except one lucky
// exact hit (LEXICAL_CONTROL). So `weak.has(e.family)` was very likely always
// false in production, and Practice has been silently serving the unweighted
// `rest` fallback the entire time -- not something this session introduced,
// but only now surfaced because the task-8 fix made Evaluator/lexical focus
// areas exist to test this against at all.
//
// Fix: bridge via capacity_domain (now plumbed through in goldPipeline.ts's
// mapGoldReportToFeedbackReport -- see lib/types.ts's FocusArea.capacity_domain
// doc comment), which is the one namespace that's coarse and enumerable
// across all three sources (6 + 13 + 5 = 24 known values). This mapping is a
// best-effort human judgment call, NOT validated against real routing
// outcomes -- there is no ground truth for "which bank family corresponds to
// which Evaluator domain." Revisit once real practice-accuracy data exists
// per family. evaluator_revision_and_self_editing is deliberately left
// unmapped (no bank family plausibly corresponds to Evaluator's own revision-
// skill judgment) rather than guessed.
export const CAPACITY_DOMAIN_TO_BANK_FAMILIES: Record<string, string[]> = {
  // ErrorMap-derived (build_focus_from_errormap's CAPACITY_TO_SKILL buckets)
  sentence_control: [
    "SUBJECT_VERB_AGREEMENT", "VERB_TENSE", "VERB_FORM", "ARTICLE_DETERMINER",
    "NOUN_NUMBER_COUNTABILITY", "COUNTABLE_UNCOUNTABLE", "PREPOSITION_PATTERN",
    "PREPOSITIONS", "CLAUSE_STRUCTURE", "FRAGMENTS_RUNONS", "RELATIVE_CLAUSES",
    "CONDITIONALS", "PASSIVE_VOICE", "PRONOUN_REFERENCE", "WORD_ORDER",
    "PUNCTUATION", "GRAMMAR_CONTROL", "MODALS", "COMPARATIVE_FORM",
    "COMPARATIVES", "VERB_NOUN_COMBINATION", "SENTENCE_VARIETY",
  ],
  lexical_precision: [
    "LEXICAL_PRECISION", "COLLOCATION", "WORD_CHOICE", "WORD_FORM",
    "WORD_FORMATION", "PRECISION", "SEMANTIC_COMBINATION", "IDIOMATIC_CONTROL",
    "ACADEMIC_VOCABULARY", "LEXICAL_CONTROL", "SLAVIC_TRANSFER_AWKWARD_PHRASE",
    "REPETITION",
  ],
  academic_style: ["REGISTER_CONTROL", "FORMALITY", "HEDGING", "PARAPHRASE"],
  argument_development: [
    "ARGUMENT_STRUCTURE", "CLAIM_SUPPORT", "COUNTERARGUMENT",
    "BALANCED_DISCUSSION", "POSITION_CLARITY", "OVERGENERALIZATION",
    "CAUSAL_REASONING", "CAUSE_EFFECT_REASONING", "EXAMPLE_QUALITY",
  ],
  cohesion_control: [
    "TRANSITIONS", "REFERENCE_COHESION", "DISCOURSE_LINKING",
    "PARAGRAPH_PROGRESS", "PARAGRAPH_STRUCTURE",
  ],
  task_response_control: [
    "TASK_RESPONSE", "TASK_RESPONSE_COVERAGE", "TASK_COMPLETENESS",
    "DATA_DESCRIPTION", "INTRODUCTION_CONCLUSION", "TOPIC_SENTENCE",
    "SUPPORTING_SENTENCE", "CONCLUSION_LOGIC",
  ],
  // Evaluator-derived (build_focus_from_evaluator's "evaluator_" + domain_slug)
  evaluator_task_understanding: ["TASK_RESPONSE", "TASK_RESPONSE_COVERAGE", "POSITION_CLARITY"],
  evaluator_content_development: ["SUPPORTING_SENTENCE", "EXAMPLE_QUALITY", "DATA_DESCRIPTION", "CLAIM_SUPPORT"],
  evaluator_reasoning_competence: ["CAUSAL_REASONING", "CAUSE_EFFECT_REASONING", "ARGUMENT_STRUCTURE"],
  evaluator_information_processing: ["DATA_DESCRIPTION", "TASK_RESPONSE_COVERAGE"],
  evaluator_thinking_competence: ["BALANCED_DISCUSSION", "OVERGENERALIZATION", "COUNTERARGUMENT"],
  evaluator_argumentation: ["ARGUMENT_STRUCTURE", "CLAIM_SUPPORT", "COUNTERARGUMENT", "BALANCED_DISCUSSION", "POSITION_CLARITY"],
  evaluator_organization: ["PARAGRAPH_STRUCTURE", "PARAGRAPH_PROGRESS", "INTRODUCTION_CONCLUSION", "TOPIC_SENTENCE", "CONCLUSION_LOGIC"],
  evaluator_cohesion: ["TRANSITIONS", "REFERENCE_COHESION", "DISCOURSE_LINKING"],
  evaluator_lexical_control: ["LEXICAL_CONTROL", "LEXICAL_PRECISION", "WORD_CHOICE"],
  evaluator_advanced_lexical_competence: ["ACADEMIC_VOCABULARY", "IDIOMATIC_CONTROL", "COLLOCATION"],
  evaluator_style_and_reader_impact: ["REGISTER_CONTROL", "FORMALITY", "HEDGING", "PRECISION"],
  evaluator_grammar_production: ["GRAMMAR_CONTROL", "SUBJECT_VERB_AGREEMENT", "VERB_TENSE", "VERB_FORM", "ARTICLE_DETERMINER", "CLAUSE_STRUCTURE"],
  // evaluator_revision_and_self_editing: intentionally omitted, see comment above.
  // LRET/Vocab-Coach lexical-derived (build_focus_from_lexical_signal's "lexical_family_" + fam)
  lexical_family_single_word: ["WORD_FORM", "WORD_FORMATION", "WORD_CHOICE"],
  lexical_family_collocation_phrase: ["COLLOCATION", "VERB_NOUN_COMBINATION"],
  lexical_family_overall_lexical_control: ["LEXICAL_CONTROL", "LEXICAL_PRECISION"],
  lexical_family_meaning_clarity: ["SEMANTIC_COMBINATION", "PRECISION", "PARAPHRASE"],
  lexical_family_other_lexical: ["LEXICAL_CONTROL"],
};

// v24: replaces raw `focus_area_feedback[].skill_tag` identity matching (see
// the module comment above CAPACITY_DOMAIN_TO_BANK_FAMILIES for why that was
// a no-op). Falls back to treating skill_tag itself as a bank family name for
// any capacity_domain not in the table above (harmless: either it happens to
// match a real bank family, as LEXICAL_CONTROL already did by coincidence, or
// it matches nothing and contributes no exercises, same as today's behavior).
export function expandWeakFamilies(
  focusAreas: Array<{ skill_tag?: string; capacity_domain?: string }>
): string[] {
  const out = new Set<string>();
  for (const fa of focusAreas) {
    const mapped = fa.capacity_domain ? CAPACITY_DOMAIN_TO_BANK_FAMILIES[fa.capacity_domain] : undefined;
    if (mapped) {
      for (const fam of mapped) out.add(fam);
    } else if (fa.skill_tag) {
      out.add(fa.skill_tag);
    }
  }
  return [...out];
}

let bankCache: BankExercise[] | null = null;

export function loadBank(): BankExercise[] {
  if (bankCache) return bankCache;
  const raw = fs.readFileSync(BANK_PATH, "utf8");
  bankCache = raw
    .split("\n")
    .filter(Boolean)
    .map((l) => JSON.parse(l) as BankExercise)
    .filter((e) => Array.isArray(e.choices) && e.choices.length >= 2 && !!e.answer);
  return bankCache;
}

export function buildPracticeSession(opts: {
  weakFamilies: string[];
  seenIds: string[];
  count: number;
}): BankExercise[] {
  const bank = loadBank();
  const seen = new Set(opts.seenIds);
  const weak = new Set(opts.weakFamilies);

  const unseen = bank.filter((e) => !seen.has(e.exercise_id));

  // Prioritize exercises from the student's actual weak skill families
  // first, then fill remaining slots from the rest of the still-unseen
  // bank. Deliberately does NOT fall back to repeating seen exercises —
  // an empty result (bank exhausted) is a real, valid state the UI already
  // handles ("No new exercises available — impressive.").
  const priority = shuffle(unseen.filter((e) => weak.has(e.family)));
  const rest = shuffle(unseen.filter((e) => !weak.has(e.family)));

  return [...priority, ...rest].slice(0, opts.count);
}

function shuffle<T>(arr: T[]): T[] {
  const copy = [...arr];
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}