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
import { spawn } from "child_process";
import fs from "fs";
import path from "path";
import type { BankExercise, FeedbackReport } from "@/lib/types";
import { runGoldEvaluation } from "./goldPipeline";
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
  return runPremiumEvaluation(normalized);
}

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