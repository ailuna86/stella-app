"use client";

import { useState } from "react";

// Short pilot questions shown after a report or practice session (v5).
// Two 1–5 ratings + optional comment; one submission per view.
// v8: rating ends are now labeled ("Not at all" / "Completely") — a bare
// 1–5 scale didn't say which direction was good. The comment box is also
// now explicitly framed as an open question ("what was useful, what
// wasn't") rather than a generic "anything to add" prompt.
export default function PlatformFeedbackWidget({
  context,
}: {
  context: "report" | "practice";
}) {
  const [clarity, setClarity] = useState(0);
  const [usefulness, setUsefulness] = useState(0);
  const [comment, setComment] = useState("");
  const [sent, setSent] = useState(false);

  const q1 =
    context === "report" ? "Was this feedback clear?" : "Were these exercises useful?";
  const q2 =
    context === "report"
      ? "Will it help you improve your writing?"
      : "Did they match your weaknesses?";

  async function send() {
    setSent(true);
    await fetch("/api/platform-feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ context, clarity, usefulness, comment }),
    });
  }

  if (sent)
    return (
      <p className="mt-6 rounded-card bg-mint-50 p-4 text-center text-sm text-mint-800">
        Thanks — this helps us improve ST.ELLA.
      </p>
    );

  return (
    <div className="card mt-6 !border-brand-100">
      <p className="text-xs font-medium tracking-wide text-brand-600">
        30 seconds for the pilot
      </p>
      <Rating label={q1} value={clarity} onChange={setClarity} />
      <Rating label={q2} value={usefulness} onChange={setUsefulness} />
      <label className="mt-3 block text-sm text-ink-800">
        What was useful? What wasn&apos;t?
      </label>
      <textarea
        className="mt-1 h-16 w-full rounded-card border border-brand-100 p-3 text-sm"
        placeholder="Optional, but specifics help us improve this fast."
        value={comment}
        onChange={(e) => setComment(e.target.value)}
      />
      <button
        className="btn-secondary mt-2 !py-2"
        onClick={send}
        disabled={!clarity || !usefulness}
      >
        Send
      </button>
    </div>
  );
}

function Rating({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="mt-3">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm text-ink-800">{label}</span>
        <div className="flex gap-1">
          {[1, 2, 3, 4, 5].map((n) => (
            <button
              key={n}
              onClick={() => onChange(n)}
              aria-label={`${n} of 5`}
              className={`h-8 w-8 rounded-full border text-sm font-medium transition ${
                value >= n
                  ? "border-brand-600 bg-brand-600 text-brand-50"
                  : "border-brand-200 text-brand-800 hover:bg-brand-50"
              }`}
            >
              {n}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-ink-400">
        <span>Not at all</span>
        <span>Completely</span>
      </div>
    </div>
  );
}
