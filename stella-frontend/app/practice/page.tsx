"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import PlatformFeedbackWidget from "@/components/PlatformFeedbackWidget";

interface Ex {
  exercise_id: string;
  family_label: string;
  prompt: string;
  choices: string[];
  answer: string;
  explanation: string;
}

const LETTERS = ["A", "B", "C", "D", "E", "F"];

// v9: per-item results accumulated through the session, used to build the
// session recap below (Pipeline_Frontend_Spec_v2 §3) — grouped by
// family_label into "went well" vs "to work on" instead of just a bare
// score. Pure aggregation of what the session already produces item by
// item; no new grading, and the existing /api/practice POST (per-item
// exerciseIds/correct/total) is untouched.
interface ItemResult {
  exerciseId: string;
  familyLabel: string;
  correct: boolean;
  explanation: string;
}

export default function PracticeSession() {
  const [exercises, setExercises] = useState<Ex[] | null>(null);
  const [minutes, setMinutes] = useState(10);
  const [idx, setIdx] = useState(0);
  const [picked, setPicked] = useState<string | null>(null);
  const [score, setScore] = useState(0);
  const [results, setResults] = useState<ItemResult[]>([]);
  const [finished, setFinished] = useState(false);
  const [err, setErr] = useState("");

  async function load() {
    setExercises(null);
    setIdx(0);
    setPicked(null);
    setScore(0);
    setResults([]);
    setFinished(false);
    const res = await fetch("/api/practice");
    const data = await res.json();
    if (data.ok) {
      setExercises(data.exercises);
      setMinutes(data.minutes);
    } else {
      setErr(data.error ?? "Could not load practice session.");
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function finish(finalScore: number, exs: Ex[]) {
    setFinished(true);
    await fetch("/api/practice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        exerciseIds: exs.map((e) => e.exercise_id),
        correct: finalScore,
        total: exs.length,
      }),
    });
  }

  if (err) return <p className="py-10 text-center text-sm text-rose-600">{err}</p>;
  if (!exercises)
    return <p className="py-10 text-center text-sm text-ink-400">Building your session…</p>;
  if (exercises.length === 0)
    return (
      <p className="py-10 text-center text-sm text-ink-600">
        No new exercises available — impressive. Ask your trainer to extend the bank.
      </p>
    );

  const ex = exercises[idx];
  const correct = picked === ex.answer;

  if (finished) {
    // v9: session recap grouped by family_label — Pipeline_Frontend_Spec_v2 §3.
    // Pure aggregation of `results`, which is just every item's own outcome
    // recorded as it happened; no new grading here.
    const byFamily = new Map<string, { correct: number; total: number; missedExplanation?: string }>();
    for (const r of results) {
      const entry = byFamily.get(r.familyLabel) ?? { correct: 0, total: 0 };
      entry.total += 1;
      if (r.correct) entry.correct += 1;
      else entry.missedExplanation = entry.missedExplanation ?? r.explanation;
      byFamily.set(r.familyLabel, entry);
    }
    const wentWell = [...byFamily.entries()].filter(([, v]) => v.correct === v.total);
    const toWorkOn = [...byFamily.entries()].filter(([, v]) => v.correct < v.total);

    return (
      <div className="mx-auto max-w-md py-10 text-center">
        <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-card bg-mint-50">
          <span className="text-2xl font-semibold text-mint-800">
            {score}/{exercises.length}
          </span>
        </div>
        <h1 className="mt-4 text-2xl font-semibold">Session complete</h1>
        <p className="mt-2 text-sm text-ink-600">
          {score === exercises.length
            ? "Perfect — the next session will move you to new skill families."
            : "Good work. Missed patterns will come back in future sessions."}
        </p>

        {(wentWell.length > 0 || toWorkOn.length > 0) && (
          <div className="mt-6 space-y-3 text-left">
            {wentWell.length > 0 && (
              <div className="rounded-card border border-mint-200 bg-mint-50 p-4">
                <p className="text-xs font-semibold uppercase tracking-wide text-mint-700">Went well</p>
                <ul className="mt-1.5 space-y-1 text-sm text-mint-800">
                  {wentWell.map(([family, v]) => (
                    <li key={family}>
                      {family.replace(/_/g, " ")} — {v.correct}/{v.total}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {toWorkOn.length > 0 && (
              <div className="rounded-card border border-amber-200 bg-amber-50 p-4">
                <p className="text-xs font-semibold uppercase tracking-wide text-amber-700">To work on</p>
                <ul className="mt-1.5 space-y-2 text-sm text-amber-800">
                  {toWorkOn.map(([family, v]) => (
                    <li key={family}>
                      <span className="font-medium">
                        {family.replace(/_/g, " ")} — {v.correct}/{v.total}
                      </span>
                      {v.missedExplanation && <p className="mt-0.5 text-xs text-amber-700">{v.missedExplanation}</p>}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        <div className="mt-6 flex justify-center gap-3">
          <button className="btn-primary" onClick={load}>
            Practice more
          </button>
          <Link href="/dashboard" className="btn-secondary">
            Back to dashboard
          </Link>
        </div>
        <div className="text-left">
          <PlatformFeedbackWidget context="practice" />
        </div>
      </div>
    );
  }

  function answer(choice: string) {
    if (picked !== null) return;
    setPicked(choice);
    const isCorrect = choice === ex.answer;
    if (isCorrect) setScore((v) => v + 1);
    setResults((prev) => [
      ...prev,
      { exerciseId: ex.exercise_id, familyLabel: ex.family_label, correct: isCorrect, explanation: ex.explanation },
    ]);
  }

  function nextExercise() {
    if (idx + 1 >= exercises!.length) {
      finish(score, exercises!);
    } else {
      setIdx(idx + 1);
      setPicked(null);
    }
  }

  return (
    <div className="mx-auto max-w-2xl py-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-brand-800">Practice</h1>
          <p className="text-xs text-ink-400">
            {ex.family_label} · Exercise {idx + 1} of {exercises.length}
          </p>
        </div>
        <span className="rounded-full bg-brand-50 px-3 py-1 text-xs text-brand-600">
          {minutes}-min session
        </span>
      </div>

      <div className="mt-3 flex justify-center gap-1.5">
        {exercises.map((e, i) => (
          <div
            key={e.exercise_id}
            className={`h-2 rounded-full transition-all ${
              i === idx
                ? "w-6 bg-brand-600"
                : i < idx
                ? "w-2 bg-brand-800"
                : "w-2 bg-brand-100"
            }`}
          />
        ))}
      </div>

      <div className="card mt-6 shadow-soft">
        <span className="inline-block rounded-full bg-brand-50 px-3 py-1 text-xs font-medium text-brand-600">
          {ex.family_label}
        </span>
        <h2 className="mt-4 text-lg font-semibold leading-relaxed text-ink-800">{ex.prompt}</h2>

        <div className="mt-5 space-y-2.5">
          {ex.choices.map((opt, i) => {
            let cls = "border-brand-100 hover:border-brand-400";
            let badgeCls = "bg-brand-50 text-brand-600";
            if (picked !== null) {
              if (opt === ex.answer) {
                cls = "border-mint-400 bg-mint-50";
                badgeCls = "bg-mint-400 text-white";
              } else if (opt === picked) {
                cls = "border-rose-400 bg-rose-50";
                badgeCls = "bg-rose-400 text-white";
              } else {
                cls = "border-brand-100 opacity-50";
              }
            }
            return (
              <button
                key={opt}
                onClick={() => answer(opt)}
                disabled={picked !== null}
                className={`flex w-full items-center gap-3 rounded-card border p-4 text-left text-sm transition ${cls}`}
              >
                <span
                  className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${badgeCls}`}
                >
                  {LETTERS[i] ?? i + 1}
                </span>
                <span className="text-ink-800">{opt}</span>
              </button>
            );
          })}
        </div>

        {picked !== null && (
          <div
            className={`mt-5 flex items-start gap-3 rounded-card p-4 ${
              correct ? "bg-mint-50" : "bg-rose-50"
            }`}
          >
            <span
              className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sm font-bold text-white ${
                correct ? "bg-mint-400" : "bg-rose-400"
              }`}
            >
              <span className="material-symbols-outlined text-[18px]">{correct ? "check" : "close"}</span>
            </span>
            <div className="flex-1">
              <p className={`font-medium ${correct ? "text-mint-800" : "text-rose-800"}`}>
                {correct ? "Correct!" : "Not quite"}
              </p>
              <p className="mt-1 text-sm text-ink-600">{ex.explanation}</p>
              <button className="btn-primary mt-3" onClick={nextExercise}>
                {idx + 1 >= exercises.length ? "Finish session" : "Next exercise"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
