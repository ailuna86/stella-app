"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { CRITERION_LABELS } from "@/lib/types";

// v8: new — trainer's structured QA review of the AI's evaluation quality.
// Permanent B2B tool (confirmed, not pilot-only): this is what lets a
// trainer flag bad calls, catch false-positive errors, and note where the
// written feedback was too generic to be useful, feeding future engine
// improvements. Never shown to students.
export default function AlgorithmReviewForm({
  submissionId,
  criteria,
  errorIds,
}: {
  submissionId: string;
  criteria: string[];
  errorIds: { id: string; excerpt: string }[];
}) {
  const [overallAccuracy, setOverallAccuracy] = useState<"accurate" | "too_generous" | "too_harsh">("accurate");
  const [criteriaFeedback, setCriteriaFeedback] = useState<Record<string, "correct" | "off">>(
    Object.fromEntries(criteria.map((c) => [c, "correct"]))
  );
  const [wrongErrorIds, setWrongErrorIds] = useState<string[]>([]);
  const [missedErrors, setMissedErrors] = useState("");
  const [feedbackQualityNotes, setFeedbackQualityNotes] = useState("");
  const [generalNotes, setGeneralNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const router = useRouter();

  function toggleWrong(id: string) {
    setWrongErrorIds((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));
  }

  async function submit() {
    setBusy(true);
    await fetch("/api/algorithm-feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        submissionId,
        overallAccuracy,
        criteriaFeedback,
        wrongErrorIds,
        missedErrors,
        feedbackQualityNotes,
        generalNotes,
      }),
    });
    setBusy(false);
    setSent(true);
    router.refresh();
  }

  if (sent) {
    return (
      <p className="card mt-4 bg-mint-50 !border-mint-200 text-sm text-mint-800">
        Review saved — thank you.
      </p>
    );
  }

  return (
    <div className="card mt-4 space-y-4">
      <h2 className="font-medium">Review this evaluation</h2>

      <div>
        <p className="text-sm text-ink-800">Overall band estimate</p>
        <div className="mt-1 flex gap-2">
          {(["accurate", "too_generous", "too_harsh"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setOverallAccuracy(v)}
              className={`rounded-full border px-3 py-1.5 text-sm ${
                overallAccuracy === v
                  ? "border-brand-600 bg-brand-50 text-brand-800"
                  : "border-brand-100 text-ink-600"
              }`}
            >
              {v === "accurate" ? "Accurate" : v === "too_generous" ? "Too generous" : "Too harsh"}
            </button>
          ))}
        </div>
      </div>

      <div>
        <p className="text-sm text-ink-800">Per-criterion accuracy</p>
        <div className="mt-1.5 space-y-1.5">
          {criteria.map((c) => (
            <div key={c} className="flex items-center justify-between">
              <span className="text-sm text-ink-600">{CRITERION_LABELS[c] ?? c}</span>
              <div className="flex gap-2">
                {(["correct", "off"] as const).map((v) => (
                  <button
                    key={v}
                    onClick={() => setCriteriaFeedback((cur) => ({ ...cur, [c]: v }))}
                    className={`rounded-full border px-3 py-1 text-xs ${
                      criteriaFeedback[c] === v
                        ? "border-brand-600 bg-brand-50 text-brand-800"
                        : "border-brand-100 text-ink-600"
                    }`}
                  >
                    {v === "correct" ? "Correct" : "Off"}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {errorIds.length > 0 && (
        <div>
          <p className="text-sm text-ink-800">Mark any flagged errors that were wrong (false catches)</p>
          <div className="mt-1.5 space-y-1">
            {errorIds.map((e) => (
              <label key={e.id} className="flex items-start gap-2 text-sm text-ink-600">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={wrongErrorIds.includes(e.id)}
                  onChange={() => toggleWrong(e.id)}
                />
                <span>&ldquo;{e.excerpt}&rdquo;</span>
              </label>
            ))}
          </div>
        </div>
      )}

      <div>
        <label className="text-sm text-ink-800">Anything important the AI missed?</label>
        <textarea
          className="mt-1 h-16 w-full rounded-card border border-brand-100 p-2 text-sm"
          value={missedErrors}
          onChange={(e) => setMissedErrors(e.target.value)}
        />
      </div>

      <div>
        <label className="text-sm text-ink-800">Was the written feedback useful, or too generic?</label>
        <textarea
          className="mt-1 h-16 w-full rounded-card border border-brand-100 p-2 text-sm"
          value={feedbackQualityNotes}
          onChange={(e) => setFeedbackQualityNotes(e.target.value)}
        />
      </div>

      <div>
        <label className="text-sm text-ink-800">General notes</label>
        <textarea
          className="mt-1 h-16 w-full rounded-card border border-brand-100 p-2 text-sm"
          value={generalNotes}
          onChange={(e) => setGeneralNotes(e.target.value)}
        />
      </div>

      <button className="btn-primary" onClick={submit} disabled={busy}>
        {busy ? "Saving…" : "Save review"}
      </button>
    </div>
  );
}
