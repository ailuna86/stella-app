"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

// v20: essay-submission timer (PO request: "Timer for essay submission - 2
// modes - exam mode - 40 min for WT2 only, and practice mode - no time
// restrictions - student chooses, not trainer, but trainer should see how
// much time each student spent").
//
// Design decisions (documented here since the spec didn't go into this much
// detail):
// - There was no existing Task 1 / Task 2 selector anywhere in this flow —
//   every essay silently went through the pipeline as WT2 (see
//   lib/server/goldPipeline.ts's `opts.taskType ?? "WT2"` default). Adding
//   one here is scoped strictly to gating exam mode ("WT2 only") — it does
//   NOT change what the grading pipeline does with the essay, that's a
//   separate, pre-existing limitation outside this feature's scope.
// - Elapsed-time tracking uses simple wall-clock start-to-submit time (not
//   focus/blur-aware pausing) — there is no existing tab-visibility
//   tracking anywhere in this codebase, and a real exam proctor doesn't
//   pause the clock when a candidate looks away either, so this matches
//   real exam conditions and avoids a lot of edge-case complexity for a
//   pilot-stage feature.
// - Timer-start trigger: this flow has no "begin session" moment today (the
//   page simply loads editable). Rather than start the 40-minute countdown
//   silently on page load — which would unfairly burn exam time while the
//   student is still reading the prompt — exam mode starts on a deliberate
//   "Start exam" choice, mirroring a real exam-room check-in. Practice mode
//   (and Task 1, which has no mode choice at all) starts elapsed tracking at
//   the same "choose and begin writing" moment, just without a visible
//   clock, so a practice student never feels watched.
// - 0:00 behavior: auto-submit whatever is currently in the textarea,
//   exactly as a real exam hall would, with a clear on-screen warning
//   inside the last 5 minutes so this is never a surprise. The full-screen
//   LoadingOverlay that submit() already triggers doubles as the "no more
//   editing" lock the instant time runs out.
const EXAM_SECONDS = 40 * 60;

function formatClock(totalSeconds: number): string {
  const m = Math.floor(Math.max(0, totalSeconds) / 60);
  const s = Math.max(0, totalSeconds) % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

const LOADING_PHRASES = [
  "Reading your essay carefully…",
  "Analyzing grammatical range…",
  "Evaluating lexical resource…",
  "Checking task response and coherence…",
  "Finalizing your band score…",
];

// v14: added a real loading overlay (rotating phrases + animated progress +
// the 4 criteria icons) instead of just a text note next to the button,
// matching the Stitch essay_submission screen's "friendly loading state"
// brief. The bar itself is indeterminate — we don't track real pipeline
// progress — but rotating through actual pipeline stages keeps a multi-
// minute wait from feeling like a stalled blank spinner.
function LoadingOverlay() {
  const [phraseIndex, setPhraseIndex] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setPhraseIndex((i) => (i + 1) % LOADING_PHRASES.length), 3500);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="fixed inset-0 z-[100] flex flex-col items-center justify-center bg-white/95 p-6 text-center backdrop-blur-md">
      <div className="relative mb-10 h-40 w-40">
        <div className="absolute inset-0 animate-ping rounded-full border-4 border-brand-100" />
        <div className="absolute inset-4 animate-pulse rounded-full border-2 border-brand-400" />
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="flex h-20 w-20 rotate-12 items-center justify-center rounded-3xl bg-brand-600 shadow-lg transition-transform hover:rotate-0">
            <span className="material-symbols-outlined text-4xl text-white">auto_awesome</span>
          </div>
        </div>
      </div>
      <h3 className="mb-3 text-xl font-semibold text-brand-800">{LOADING_PHRASES[phraseIndex]}</h3>
      <p className="mb-8 max-w-sm text-sm text-ink-600">
        Our scoring engine is analyzing your grammar, vocabulary, coherence, and task response
        against official IELTS criteria — this takes a few minutes, please keep this tab open.
      </p>
      <div className="mb-6 h-2 w-full max-w-md overflow-hidden rounded-full bg-brand-50">
        <div className="h-full w-2/3 animate-pulse rounded-full bg-brand-600" />
      </div>
      <div className="flex items-center gap-6 text-ink-400">
        <div className="flex flex-col items-center gap-1.5">
          <span className="material-symbols-outlined text-brand-600">assignment_turned_in</span>
          <span className="text-[10px] uppercase tracking-wide">Task response</span>
        </div>
        <div className="flex flex-col items-center gap-1.5">
          <span className="material-symbols-outlined text-brand-600">account_tree</span>
          <span className="text-[10px] uppercase tracking-wide">Coherence</span>
        </div>
        <div className="flex flex-col items-center gap-1.5">
          <span className="material-symbols-outlined text-brand-600">font_download</span>
          <span className="text-[10px] uppercase tracking-wide">Lexical resource</span>
        </div>
        <div className="flex flex-col items-center gap-1.5">
          <span className="material-symbols-outlined text-brand-600">spellcheck</span>
          <span className="text-[10px] uppercase tracking-wide">Grammar</span>
        </div>
      </div>
    </div>
  );
}

export default function SubmitForm({
  lockedPrompt,
  assignmentId,
  evaluationsLeft,
  isGold,
}: {
  lockedPrompt: string | null;
  assignmentId: string | null;
  evaluationsLeft: number;
  isGold?: boolean;
}) {
  const draftKey = `stella_draft_${assignmentId ?? "unassigned"}`;
  const [prompt, setPrompt] = useState(lockedPrompt ?? "");
  const [essay, setEssay] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [draftSavedAt, setDraftSavedAt] = useState<Date | null>(null);
  const router = useRouter();
  const words = essay.trim() ? essay.trim().split(/\s+/).length : 0;

  // -- essay-submission timer state ------------------------------------
  // taskType is a student self-declaration used only to gate exam mode
  // ("WT2 only" per the spec) — it isn't sent to the pipeline (see the
  // design-decision comment above the imports).
  const [taskType, setTaskType] = useState<"task1" | "task2">("task2");
  // null = not chosen yet (only possible for task2, which requires a
  // deliberate choice before the essay box unlocks). Task 1 has no choice —
  // it's always "practice", chosen automatically below.
  const [mode, setMode] = useState<"exam" | "practice" | null>(null);
  const [remainingSeconds, setRemainingSeconds] = useState<number | null>(null);
  const startedAtRef = useRef<number | null>(null);
  const autoSubmittedRef = useRef(false);

  function beginAttempt(chosen: "exam" | "practice") {
    setMode(chosen);
    startedAtRef.current = Date.now();
    autoSubmittedRef.current = false;
    setRemainingSeconds(chosen === "exam" ? EXAM_SECONDS : null);
  }

  function selectTaskType(next: "task1" | "task2") {
    setTaskType(next);
    if (next === "task1") {
      // Task 1: no mode question, always untimed — but still silently
      // tracked so the trainer console has a real elapsed-time number.
      beginAttempt("practice");
    } else {
      // Switching back to Task 2 requires a fresh, deliberate mode choice
      // before writing resumes — this also resets any in-progress timer.
      setMode(null);
      startedAtRef.current = null;
      autoSubmittedRef.current = false;
      setRemainingSeconds(null);
    }
  }

  // Exam-mode countdown: ticks every second while mode === "exam"; at 0:00
  // auto-submits whatever's currently in the textarea (see design-decision
  // comment above). Re-running every second keeps the closure inside
  // submit() fresh with the latest `essay`/`prompt` state.
  useEffect(() => {
    if (mode !== "exam" || remainingSeconds === null) return;
    if (remainingSeconds <= 0) {
      if (!autoSubmittedRef.current) {
        autoSubmittedRef.current = true;
        submit();
      }
      return;
    }
    const id = setTimeout(() => setRemainingSeconds((s) => (s !== null ? s - 1 : s)), 1000);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, remainingSeconds]);

  // Local draft recovery — nothing server-side, just so a closed tab or
  // refresh doesn't lose an in-progress essay.
  useEffect(() => {
    try {
      const saved = localStorage.getItem(draftKey);
      if (saved) {
        const parsed = JSON.parse(saved);
        if (parsed.essay) setEssay(parsed.essay);
        if (!lockedPrompt && parsed.prompt) setPrompt(parsed.prompt);
      }
    } catch {
      // ignore — draft recovery is a convenience, not critical
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function saveDraft() {
    try {
      localStorage.setItem(draftKey, JSON.stringify({ essay, prompt, savedAt: Date.now() }));
      setDraftSavedAt(new Date());
    } catch {
      setErr("Couldn't save draft on this device — copy your essay somewhere safe.");
    }
  }

  async function submit() {
    setBusy(true);
    setErr("");
    // Wall-clock elapsed time from attempt-start (mode chosen) to submit —
    // see design-decision comment above the imports for why this is simple
    // start-to-submit time rather than focus/blur-aware pausing.
    const timeSpentSeconds = startedAtRef.current
      ? Math.max(0, Math.round((Date.now() - startedAtRef.current) / 1000))
      : 0;
    try {
      const res = await fetch("/api/evaluate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          essay,
          prompt,
          assignmentId,
          mode: mode ?? "practice",
          timeSpentSeconds,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        try {
          localStorage.removeItem(draftKey);
        } catch {
          // non-critical
        }
        router.push(`/writing/report/${data.submissionId}`);
        return;
      }
      setErr(data.error ?? "Evaluation failed.");
    } catch {
      setErr("Evaluation failed — is the pipeline available? Ask your trainer.");
    } finally {
      setBusy(false);
    }
  }

  const canSubmit = !busy && words >= 50 && !!prompt.trim() && evaluationsLeft > 0 && !!mode;
  const examWarning = mode === "exam" && remainingSeconds !== null && remainingSeconds <= 300;

  return (
    <div className="mx-auto max-w-2xl py-6">
      {busy && <LoadingOverlay />}

      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold">Write your essay</h1>
        <span className="text-xs text-ink-400">{evaluationsLeft} evaluations left</span>
      </div>

      {/* v20: task type — student self-declaration, used only to gate exam
          mode (spec: "exam mode - 40 min for WT2 only"). Doesn't change how
          the essay is graded — see the design-decision comment near the top
          of this file. */}
      <div className="mt-4 flex items-center gap-2">
        <span className="text-xs font-medium uppercase tracking-wide text-ink-400">Task</span>
        <div className="flex overflow-hidden rounded-full border border-brand-100">
          <button
            type="button"
            onClick={() => selectTaskType("task1")}
            className={`px-3 py-1 text-xs font-medium ${
              taskType === "task1" ? "bg-brand-600 text-white" : "bg-white text-ink-600"
            }`}
          >
            Task 1
          </button>
          <button
            type="button"
            onClick={() => selectTaskType("task2")}
            className={`px-3 py-1 text-xs font-medium ${
              taskType === "task2" ? "bg-brand-600 text-white" : "bg-white text-ink-600"
            }`}
          >
            Task 2
          </button>
        </div>
        {taskType === "task1" && (
          <span className="text-xs text-ink-400">Untimed practice — exam mode is Task 2 only.</span>
        )}
      </div>

      {/* Mode choice — only Task 2 asks; Task 1 is always practice (set
          automatically by selectTaskType above). Writing is gated behind
          this choice for Task 2 so the exam countdown only ever starts on a
          deliberate "Start exam" click, never silently on page load. */}
      {taskType === "task2" && mode === null && (
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <button
            type="button"
            onClick={() => beginAttempt("exam")}
            className="card text-left transition hover:border-brand-400"
          >
            <p className="flex items-center gap-1.5 text-sm font-semibold text-ink-900">
              <span className="material-symbols-outlined text-[18px] text-brand-600">timer</span>
              Exam mode
            </p>
            <p className="mt-1 text-xs text-ink-600">
              Strict 40-minute countdown, just like the real test. Auto-submits at 0:00.
            </p>
          </button>
          <button
            type="button"
            onClick={() => beginAttempt("practice")}
            className="card text-left transition hover:border-brand-400"
          >
            <p className="flex items-center gap-1.5 text-sm font-semibold text-ink-900">
              <span className="material-symbols-outlined text-[18px] text-brand-600">self_improvement</span>
              Practice mode
            </p>
            <p className="mt-1 text-xs text-ink-600">
              No time limit — write at your own pace.
            </p>
          </button>
        </div>
      )}

      {/* Exam countdown — only ever shown in exam mode; practice-mode time
          is still tracked (see submit()) but deliberately never surfaced to
          the student so practice never feels watched. */}
      {mode === "exam" && remainingSeconds !== null && (
        <div
          className={`mt-4 flex items-center justify-between rounded-card border px-4 py-2 text-sm ${
            examWarning
              ? "border-rose-300 bg-rose-50 text-rose-700"
              : "border-brand-100 bg-brand-50/50 text-brand-800"
          }`}
        >
          <span className="flex items-center gap-1.5 font-medium">
            <span className="material-symbols-outlined text-[18px]">timer</span>
            {formatClock(remainingSeconds)} remaining
          </span>
          {examWarning && (
            <span className="text-xs">
              Under 5 minutes left — your essay auto-submits at 0:00.
            </span>
          )}
        </div>
      )}

      {lockedPrompt ? (
        <div className="card mt-4 bg-brand-50 !border-brand-100">
          <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-brand-600">
            <span className="material-symbols-outlined text-[16px]">lock</span> Assignment prompt
          </p>
          <p className="mt-2 text-sm leading-relaxed text-brand-900">{lockedPrompt}</p>
        </div>
      ) : (
        <textarea
          className="mt-4 h-24 w-full rounded-card border border-brand-100 p-4 text-sm leading-relaxed outline-none focus:border-brand-400"
          placeholder="Paste the task prompt your essay answers…"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
        />
      )}

      {mode === null ? (
        <p className="mt-4 text-sm text-ink-400">Choose a mode above to start writing.</p>
      ) : (
        <>
          <div className="mt-4 overflow-hidden rounded-card border border-brand-100 shadow-soft focus-within:border-brand-400">
            <div className="flex items-center justify-between border-b border-brand-100 bg-brand-50/50 px-4 py-2">
              <span className="text-xs font-medium text-ink-600">Your essay response</span>
              <span className={`text-xs ${words < 250 ? "text-rose-600" : "text-mint-600"}`}>
                {words} word{words === 1 ? "" : "s"}
                {words < 250 ? " — aim for at least 250" : ""}
              </span>
            </div>
            <textarea
              className="h-80 w-full p-4 text-[15px] leading-relaxed outline-none"
              placeholder="Start typing your essay here…"
              value={essay}
              onChange={(e) => setEssay(e.target.value)}
            />
          </div>

          <div className="mt-4 flex flex-col items-center justify-between gap-3 sm:flex-row">
            {isGold ? (
              <div className="flex items-center gap-2 rounded-full bg-brand-50 px-4 py-2 text-xs text-brand-800">
                <span className="material-symbols-outlined text-[16px] text-amber-500">stars</span>
                <span>
                  <span className="font-medium">Gold tier:</span> detailed AI feedback with a
                  full study plan, typically ready in under 10 minutes.
                </span>
              </div>
            ) : (
              <span className="text-xs text-ink-400">
                {draftSavedAt ? `Draft saved ${draftSavedAt.toLocaleTimeString()}` : " "}
              </span>
            )}
            <div className="flex w-full items-center gap-3 sm:w-auto">
              <button className="btn-secondary flex-1 sm:flex-none" onClick={saveDraft} disabled={!essay.trim()}>
                Save draft
              </button>
              <button className="btn-primary flex-1 sm:flex-none" onClick={submit} disabled={!canSubmit}>
                {busy ? "Evaluating…" : "Submit for evaluation"}
              </button>
            </div>
          </div>
          {isGold && draftSavedAt && (
            <p className="mt-2 text-right text-xs text-ink-400">
              Draft saved {draftSavedAt.toLocaleTimeString()}
            </p>
          )}
          {err && <p className="mt-2 text-right text-sm text-rose-600">{err}</p>}
        </>
      )}
    </div>
  );
}
