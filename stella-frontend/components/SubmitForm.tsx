"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

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
    try {
      const res = await fetch("/api/evaluate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ essay, prompt, assignmentId }),
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

  const canSubmit = !busy && words >= 50 && !!prompt.trim() && evaluationsLeft > 0;

  return (
    <div className="mx-auto max-w-2xl py-6">
      {busy && <LoadingOverlay />}

      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold">Write your essay</h1>
        <span className="text-xs text-ink-400">{evaluationsLeft} evaluations left</span>
      </div>

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
    </div>
  );
}
