"use client";

import { useState } from "react";

interface MissionItemFeedback {
  itemNumber: number;
  roughInput: string | null;
  studentSentence: string | null;
  status: string;
  issues: string[];
  suggestedRevision: string | null;
  explanation: string | null;
  howToImprove: string | null;
}

interface MissionGradeResult {
  outcome: string;
  missionScore: number;
  completionMessage: string;
  overallComment: string;
  whatWentWell: string[];
  whatToFixFirst: string[];
  tryAgainInstruction: string | null;
  items: MissionItemFeedback[];
}

const OUTCOME_STYLE: Record<string, { badge: string; label: string }> = {
  pass: { badge: "bg-mint-400 text-white", label: "Pass" },
  partial_pass: { badge: "bg-amber-300 text-amber-900", label: "Partial pass" },
  fail: { badge: "bg-rose-400 text-white", label: "Not yet" },
  invalid_empty_response: { badge: "bg-ink-200 text-ink-700", label: "No response" },
  invalid_incomplete_output: { badge: "bg-ink-200 text-ink-700", label: "Incomplete" },
};

export default function MissionSubmitForm({
  requiredItems,
  successChecklist = [],
}: {
  requiredItems: number;
  successChecklist?: string[];
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [result, setResult] = useState<MissionGradeResult | null>(null);

  async function submit() {
    setBusy(true);
    setErr("");
    try {
      const res = await fetch("/api/writing-coach/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const data = await res.json();
      if (data.ok) {
        setResult(data.result);
      } else {
        setErr(data.error ?? "Grading failed.");
      }
    } catch {
      setErr("Grading failed — please try again.");
    } finally {
      setBusy(false);
    }
  }

  if (result) {
    const style = OUTCOME_STYLE[result.outcome] ?? OUTCOME_STYLE.fail;
    return (
      <div className="space-y-6">
        <div className="card shadow-soft">
          <div className="flex items-center justify-between">
            <h2 className="font-medium text-ink-800">Your result</h2>
            <span className={`rounded-full px-3 py-1 text-xs font-medium ${style.badge}`}>{style.label}</span>
          </div>
          <p className="mt-2 text-sm text-ink-800">{result.overallComment}</p>
          {result.completionMessage && (
            <p className="mt-1 text-xs text-ink-400">{result.completionMessage}</p>
          )}

          {result.whatWentWell.length > 0 && (
            <div className="mt-3 rounded-card border border-mint-200 bg-mint-50 p-3">
              <p className="text-xs font-medium uppercase tracking-wide text-mint-600">What went well</p>
              <ul className="mt-1.5 space-y-1 text-sm text-ink-800">
                {result.whatWentWell.map((w, i) => (
                  <li key={i} className="flex gap-2">
                    <span aria-hidden className="text-mint-600">✓</span> {w}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {result.whatToFixFirst.length > 0 && (
            <div className="mt-3 rounded-card border border-rose-200 bg-rose-50 p-3">
              <p className="text-xs font-medium uppercase tracking-wide text-rose-600">Fix first</p>
              <ul className="mt-1.5 space-y-1 text-sm text-ink-800">
                {result.whatToFixFirst.map((w, i) => (
                  <li key={i} className="flex gap-2">
                    <span aria-hidden className="text-rose-600">•</span> {w}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {result.tryAgainInstruction && (
            <p className="mt-4 rounded-card bg-brand-50 p-3 text-sm text-brand-800">
              {result.tryAgainInstruction}
            </p>
          )}

          <button
            className="btn-secondary mt-4"
            onClick={() => {
              setResult(null);
              setText("");
            }}
          >
            Try again
          </button>
        </div>

        {result.items.length > 0 && (
          <div className="space-y-3">
            <p className="text-xs font-medium uppercase tracking-widest text-ink-400">Detailed breakdown</p>
            {result.items.map((it) => {
              const passed = it.status?.toLowerCase().includes("pass");
              return (
                <div
                  key={it.itemNumber}
                  className={`grid gap-4 rounded-card border p-4 shadow-soft md:grid-cols-2 ${
                    passed ? "border-mint-200" : "border-rose-200"
                  }`}
                >
                  <div className="space-y-2">
                    <span
                      className={`text-[10px] font-bold uppercase tracking-widest ${
                        passed ? "text-mint-600" : "text-rose-600"
                      }`}
                    >
                      Item {it.itemNumber} · {passed ? "Successful" : "Needs revision"}
                    </span>
                    {it.studentSentence && (
                      <p className="text-sm font-medium text-ink-800">{it.studentSentence}</p>
                    )}
                    {it.issues.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {it.issues.map((iss, i) => (
                          <span key={i} className="rounded-full bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-700">
                            {iss}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="space-y-2">
                    {it.explanation && (
                      <div className={`rounded-card p-3 ${passed ? "bg-mint-50" : "bg-rose-50"}`}>
                        <p className={`text-xs font-medium ${passed ? "text-mint-700" : "text-rose-700"}`}>
                          {passed ? "Excellent!" : "Explanation"}
                        </p>
                        <p className="mt-1 text-xs text-ink-700">{it.explanation}</p>
                      </div>
                    )}
                    {it.suggestedRevision && (
                      <div className="rounded-card border-2 border-mint-200 bg-white p-3">
                        <p className="text-xs font-medium text-mint-700">Suggested revision</p>
                        <p className="mt-1 text-xs italic text-ink-800">{it.suggestedRevision}</p>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="card shadow-soft">
      <h2 className="font-medium text-brand-800">Your response</h2>
      <p className="mt-1 text-xs text-ink-400">
        {requiredItems} item{requiredItems === 1 ? "" : "s"} — one sentence per line, numbered 1–
        {requiredItems}.
      </p>
      <textarea
        className="mt-3 h-56 w-full rounded-card border border-brand-100 p-4 text-sm leading-relaxed outline-none focus:border-brand-400"
        placeholder={`1. ...\n2. ...`}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />

      {successChecklist.length > 0 && (
        <div className="mt-4 rounded-card border-l-4 border-brand-400 bg-brand-50 p-3">
          <p className="text-xs font-medium text-brand-800">Before you submit:</p>
          <ul className="mt-1.5 space-y-1 text-xs text-ink-700">
            {successChecklist.map((c, i) => (
              <li key={i} className="flex items-center gap-1.5">
                <span aria-hidden className="text-brand-500">✓</span> {c}
              </li>
            ))}
          </ul>
        </div>
      )}

      {err && <p className="mt-2 text-sm text-rose-600">{err}</p>}
      <button className="btn-primary mt-4 w-full" onClick={submit} disabled={busy || !text.trim()}>
        {busy ? "Grading…" : "Submit for grading"}
        <span className="rounded-full bg-white/20 px-2 py-0.5 text-[10px]">0 credits</span>
      </button>
      {busy && (
        <p className="mt-2 text-center text-xs text-ink-400">
          Grading each sentence in detail can take a couple of minutes — please keep this tab
          open.
        </p>
      )}
    </div>
  );
}
