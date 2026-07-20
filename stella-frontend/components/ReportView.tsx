"use client";

import { useState } from "react";
import Link from "next/link";
import PlatformFeedbackWidget from "@/components/PlatformFeedbackWidget";
import {
  CRITERION_LABELS,
  overallTarget,
  rubricTarget,
  type FeedbackReport,
} from "@/lib/types";

// v8: added optional essayText — when provided (trainer view only; see
// app/writing/report/[id]/page.tsx), shows the student's actual essay in a
// collapsible section. Previously the data existed but was never rendered
// anywhere, so trainers had no way to see what the student actually wrote.
// v9: rebuilt visuals to match the Stitch feedback_report screen — a circular
// band gauge, per-criterion progress bars, and expandable focus-area cards
// with a before/after annotation drawn straight from the real
// annotated_errors data (no invented copy).
export default function ReportView({
  report,
  goal,
  essayText,
  hideFeedbackWidget,
  submissionId,
  hasRevision,
  hasVocabulary,
}: {
  report: FeedbackReport;
  goal?: number;
  essayText?: string;
  hideFeedbackWidget?: boolean;
  submissionId?: string;
  hasRevision?: boolean;
  hasVocabulary?: boolean;
}) {
  const s = report.score_summary;
  const target = overallTarget(s.holistic_band, goal);
  const weakest = Object.entries(s.criteria_bands)
    .filter(([, v]) => v != null)
    .sort((a, b) => (a[1] ?? 9) - (b[1] ?? 9))[0];

  const errorsByFamily = new Map<string, FeedbackReport["focus_area_feedback"][0]["annotated_errors"]>();
  for (const fa of report.focus_area_feedback ?? []) {
    for (const err of fa.annotated_errors ?? []) {
      const list = errorsByFamily.get(err.error_type) ?? [];
      list.push(err);
      errorsByFamily.set(err.error_type, list);
    }
  }

  const [openFocus, setOpenFocus] = useState<number>(0);

  // Circular gauge geometry — band is out of 9.
  const radius = 70;
  const circumference = 2 * Math.PI * radius;
  const bandFraction = Math.min(1, Math.max(0, (s.holistic_band ?? 0) / 9));
  const dashOffset = circumference * (1 - bandFraction);

  return (
    <div className="mx-auto max-w-3xl py-6">
      {report.escalate_to_human_review && (
        <div className="mb-4 rounded-card border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
          The pipeline flagged this submission as ambiguous — worth a closer look before
          fully trusting the automated evaluation.
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-12">
        <div className="card flex flex-col items-center justify-center text-center shadow-soft lg:col-span-5">
          <p className="text-xs font-medium text-ink-400">Overall band score</p>
          <div className="relative mt-3 flex h-40 w-40 items-center justify-center">
            <svg viewBox="0 0 160 160" className="h-full w-full -rotate-90">
              <circle cx="80" cy="80" r={radius} fill="none" stroke="#EEEDFE" strokeWidth="10" />
              <circle
                cx="80"
                cy="80"
                r={radius}
                fill="none"
                stroke="#534AB7"
                strokeWidth="10"
                strokeLinecap="round"
                strokeDasharray={circumference}
                strokeDashoffset={dashOffset}
              />
            </svg>
            <div className="absolute flex flex-col items-center">
              <span className="text-3xl font-semibold text-brand-800">
                {s.holistic_band?.toFixed(1)}
              </span>
              <span className="mt-0.5 text-xs text-mint-600">Target {target.toFixed(1)}</span>
            </div>
          </div>
          <p className="mt-4 max-w-xs text-sm text-ink-600">{s.headline_message}</p>
        </div>

        <div className="card shadow-soft lg:col-span-7">
          <h2 className="font-medium text-ink-800">Criteria breakdown</h2>
          <div className="mt-4 space-y-4">
            {Object.entries(s.criteria_bands).map(([key, band]) => {
              const b = band ?? 0;
              const weak = key === weakest?.[0];
              return (
                <div key={key}>
                  <div className="flex items-end justify-between">
                    <span className="text-sm text-ink-800">{CRITERION_LABELS[key] ?? key}</span>
                    <span className={`font-medium ${weak ? "text-rose-600" : "text-brand-800"}`}>
                      {b.toFixed(1)}
                    </span>
                  </div>
                  <div className="mt-1.5 h-2.5 w-full overflow-hidden rounded-full bg-brand-50">
                    <div
                      className={`h-full rounded-full ${weak ? "bg-rose-400" : "bg-brand-400"}`}
                      style={{ width: `${Math.min(100, (b / 9) * 100)}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {essayText && (
        <details className="card mt-4">
          <summary className="cursor-pointer font-medium text-ink-800">
            Student's essay
          </summary>
          <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-ink-800">
            {essayText}
          </p>
        </details>
      )}

      {(report.top_strengths?.length || report.top_weaknesses?.length) && (
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <div className="card !border-mint-200">
            <h2 className="font-medium text-mint-800">Your strengths</h2>
            <ul className="mt-2 space-y-2 text-sm text-ink-600">
              {(report.top_strengths ?? []).slice(0, 3).map((t) => (
                <li key={t} className="flex gap-2">
                  <span className="text-mint-400">✓</span> {t}
                </li>
              ))}
            </ul>
          </div>
          <div className="card !border-rose-100">
            <h2 className="font-medium text-rose-800">Work on these</h2>
            <ul className="mt-2 space-y-2 text-sm text-ink-600">
              {(report.top_weaknesses ?? []).slice(0, 3).map((t) => (
                <li key={t} className="flex gap-2">
                  <span className="text-rose-400">•</span> {t}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {report.focus_area_feedback?.length > 0 && (
        <div className="mt-6">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="font-medium text-ink-800">Focus areas</h2>
            <div className="flex items-center gap-2">
              {hasVocabulary && submissionId && (
                <Link
                  href={`/vocabulary-coach/${submissionId}`}
                  className="rounded-full border border-brand-200 px-3 py-1 text-xs font-medium text-brand-800 hover:bg-brand-50"
                >
                  Vocabulary breakdown
                </Link>
              )}
              <span className="rounded-full bg-brand-50 px-3 py-1 text-xs uppercase tracking-wide text-brand-600">
                {report.focus_area_feedback.length} suggested improvement
                {report.focus_area_feedback.length === 1 ? "" : "s"}
              </span>
            </div>
          </div>

          <div className="mt-3 space-y-3">
            {report.focus_area_feedback.map((fa, i) => {
              const open = openFocus === i;
              const example = fa.annotated_errors?.[0];
              return (
                <div key={fa.rank ?? i} className="overflow-hidden rounded-card border border-brand-100 shadow-soft">
                  <button
                    type="button"
                    onClick={() => setOpenFocus(open ? -1 : i)}
                    className="flex w-full items-start justify-between gap-4 p-4 text-left hover:bg-brand-50/50"
                  >
                    <div>
                      <h3 className="font-medium text-ink-800">
                        {CRITERION_LABELS[fa.criterion] ?? fa.skill_tag?.replace(/_/g, " ") ?? fa.criterion}
                      </h3>
                      <p className="mt-1 text-xs text-ink-400">
                        Current {fa.current_band?.toFixed(1)} → target{" "}
                        <span className="font-medium text-brand-600">{fa.target_band?.toFixed(1)}</span>
                      </p>
                    </div>
                    <span className={`shrink-0 text-ink-400 transition-transform ${open ? "rotate-180" : ""}`}>
                      ⌄
                    </span>
                  </button>

                  {open && (
                    <div className="space-y-4 border-t border-brand-100 px-4 pb-5 pt-4">
                      <p className="rounded-card bg-brand-50 p-3 text-sm text-ink-600">{fa.summary}</p>

                      {example && (
                        <div className="grid gap-3 sm:grid-cols-2">
                          <div className="rounded-card border-l-4 border-rose-400 bg-rose-50 p-3">
                            <p className="text-[10px] font-bold uppercase tracking-wide text-rose-600">
                              Original
                            </p>
                            <p className="mt-1 text-sm text-ink-800">
                              “…<span className="rounded bg-rose-100 px-1">{example.excerpt}</span>…”
                            </p>
                          </div>
                          <div className="rounded-card border-l-4 border-mint-400 bg-mint-50 p-3">
                            <p className="text-[10px] font-bold uppercase tracking-wide text-mint-600">
                              Improved
                            </p>
                            <p className="mt-1 text-sm text-ink-800">{example.correction}</p>
                          </div>
                        </div>
                      )}
                      {example?.issue && <p className="text-sm text-ink-600">{example.issue}</p>}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      <details className="card mt-4">
        <summary className="cursor-pointer font-medium text-ink-800">
          Full report — all errors by family
        </summary>
        {[...errorsByFamily.entries()].map(([family, errors]) => (
          <div key={family} className="mt-4">
            <h3 className="text-sm font-medium text-ink-800">
              {family.replace(/_/g, " ")} — {errors.length} error{errors.length > 1 ? "s" : ""}
            </h3>
            <div className="mt-2 space-y-3">
              {errors.map((err, i) => (
                <div key={err.error_id ?? i} className="rounded-card border border-brand-100 p-4">
                  <p className="text-sm text-ink-800">
                    “…
                    <span className="rounded bg-rose-50 px-1 text-rose-800">{err.excerpt}</span>
                    …”
                  </p>
                  <p className="mt-2 text-sm text-ink-600">{err.issue}</p>
                  <p className="mt-1 text-sm text-mint-600">{err.correction}</p>
                </div>
              ))}
            </div>
          </div>
        ))}
      </details>

      {report.closing_message && (
        <p className="mt-4 rounded-card bg-brand-50 p-4 text-sm text-brand-900">
          {report.closing_message}
        </p>
      )}

      <div className="mt-6 flex flex-wrap gap-3">
        {hasRevision && submissionId && (
          <Link href={`/writing/revise/${submissionId}`} className="btn-primary">
            Revise this essay
          </Link>
        )}
        <Link href="/practice" className={hasRevision && submissionId ? "btn-secondary" : "btn-primary"}>
          Start today&apos;s practice
        </Link>
        <Link href="/dashboard" className="btn-secondary">
          Back to dashboard
        </Link>
      </div>

      {!hideFeedbackWidget && <PlatformFeedbackWidget context="report" />}
    </div>
  );
}
