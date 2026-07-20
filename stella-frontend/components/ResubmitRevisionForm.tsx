"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

interface ComparisonItem {
  paragraphNumber: number;
  role: string;
  original: string;
  studentRevision: string;
  aiModel: string | null;
  whyStronger: string[];
  specificExampleUsed: string | null;
  lexicalUpgrades: Array<{ from: string; to: string; why: string }>;
}

interface ComparisonResult {
  modelAvailable: boolean;
  generationStatus: string;
  fullModelEssay: string | null;
  fullModelWordCount: number;
  items: ComparisonItem[];
}

// v11: the Stitch brief lists "essay revision with re-scoring" as a named
// Gold feature — re-scoring means running the revised text back through the
// same evaluation pipeline as any other essay, so this reuses /api/evaluate
// rather than inventing a separate lightweight path. That also means it
// correctly costs one evaluation credit, same as the original submission.
// v12: added "Compare with AI model" — a second, free action on the same
// textarea that calls the AI-comparison engine instead of re-evaluating.
// Per the ER spec, the model rewrite always comes from the ORIGINAL essay,
// never the student's revision — the revision is shown for comparison only.
export default function ResubmitRevisionForm({
  submissionId,
  prompt,
  initialText,
  assignmentId,
  evaluationsLeft,
}: {
  submissionId: string;
  prompt: string;
  initialText: string;
  assignmentId: string | null;
  evaluationsLeft: number;
}) {
  const [essay, setEssay] = useState(initialText);
  const [busy, setBusy] = useState(false);
  const [comparing, setComparing] = useState(false);
  const [err, setErr] = useState("");
  const [comparison, setComparison] = useState<ComparisonResult | null>(null);
  const router = useRouter();
  const words = essay.trim() ? essay.trim().split(/\s+/).length : 0;

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
        router.push(`/writing/report/${data.submissionId}`);
        return;
      }
      setErr(data.error ?? "Re-evaluation failed.");
    } catch {
      setErr("Re-evaluation failed — please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function compare() {
    setComparing(true);
    setErr("");
    setComparison(null);
    try {
      const res = await fetch("/api/writing/revise/compare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ submissionId, revisedText: essay }),
      });
      const data = await res.json();
      if (data.ok) {
        setComparison(data.result);
      } else {
        setErr(data.error ?? "Comparison failed.");
      }
    } catch {
      setErr("Comparison failed — please try again.");
    } finally {
      setComparing(false);
    }
  }

  return (
    <div className="card mt-4 shadow-soft">
      <div className="flex items-baseline justify-between">
        <h2 className="font-medium text-ink-800">Your revision</h2>
        <span className="text-xs text-ink-400">{evaluationsLeft} evaluations left</span>
      </div>
      <p className="mt-1 text-xs text-ink-400">
        Pre-filled with your original text — edit it above using the hints. Then either
        compare it against an AI model rewrite (free, instant), or resubmit for a full new
        band score (uses one evaluation credit).
      </p>
      <div className="mt-3 overflow-hidden rounded-card border border-brand-100 focus-within:border-brand-400">
        <div className="flex items-center justify-between border-b border-brand-100 bg-brand-50/50 px-4 py-2">
          <span className="text-xs font-medium text-ink-600">Your revised essay</span>
          <span className="text-xs text-ink-400">{words} words</span>
        </div>
        <textarea
          className="h-64 w-full p-4 text-[15px] leading-relaxed outline-none"
          value={essay}
          onChange={(e) => setEssay(e.target.value)}
        />
      </div>
      {err && <p className="mt-2 text-sm text-rose-600">{err}</p>}

      <div className="mt-3 flex flex-wrap gap-3">
        <button className="btn-secondary" onClick={compare} disabled={comparing || words < 20}>
          {comparing ? "Comparing…" : "Compare with AI model"}
        </button>
        <button
          className="btn-primary"
          onClick={submit}
          disabled={busy || words < 50 || evaluationsLeft <= 0}
        >
          {busy ? "Evaluating…" : "Resubmit for re-evaluation"}
        </button>
      </div>
      {evaluationsLeft <= 0 && (
        <p className="mt-2 text-xs text-rose-600">
          No evaluations left on your plan — ask your trainer about upgrading.
        </p>
      )}
      {busy && (
        <p className="mt-2 text-xs text-ink-400">
          This runs the full scoring pipeline again and can take a few minutes — keep
          this tab open.
        </p>
      )}

      {comparison && (
        <div className="mt-5 border-t border-brand-100 pt-5">
          <div className="rounded-card bg-brand-50 p-3 text-xs text-brand-800">
            This model rewrite is generated from your <b>original</b> essay, not your
            revision — use it to see both against a strong version. Don&apos;t copy it as
            your own work.
          </div>

          {!comparison.modelAvailable && (
            <p className="mt-3 text-sm text-ink-600">
              A model rewrite couldn&apos;t be generated for this attempt — your paragraph
              comparison is still shown below.
            </p>
          )}

          <div className="mt-4 space-y-4">
            {comparison.items.map((it) => (
              <div key={it.paragraphNumber} className="rounded-card border border-brand-100 p-4">
                <h3 className="text-sm font-medium capitalize text-ink-800">
                  Paragraph {it.paragraphNumber} — {it.role}
                </h3>
                <div className="mt-3 grid gap-3 md:grid-cols-3">
                  <div className="rounded-card border border-brand-100 bg-brand-50/30 p-3">
                    <p className="text-[10px] font-bold uppercase tracking-wide text-ink-400">
                      Original
                    </p>
                    <p className="mt-1 text-sm text-ink-800">{it.original}</p>
                  </div>
                  <div className="rounded-card border border-brand-100 bg-brand-50/30 p-3">
                    <p className="text-[10px] font-bold uppercase tracking-wide text-ink-400">
                      Your revision
                    </p>
                    <p className="mt-1 text-sm text-ink-800">{it.studentRevision}</p>
                  </div>
                  <div className="rounded-card border border-mint-200 bg-mint-50 p-3">
                    <p className="text-[10px] font-bold uppercase tracking-wide text-mint-600">
                      AI model rewrite
                    </p>
                    <p className="mt-1 text-sm text-ink-800">
                      {it.aiModel ?? "Not available for this paragraph."}
                    </p>
                  </div>
                </div>
                {it.whyStronger.length > 0 && (
                  <div className="mt-3 rounded-card bg-amber-50 p-3">
                    <p className="text-xs font-medium uppercase tracking-wide text-amber-700">
                      Why this is stronger
                    </p>
                    <ul className="mt-1 space-y-1 text-sm text-ink-800">
                      {it.whyStronger.map((w, i) => (
                        <li key={i} className="flex gap-2">
                          <span aria-hidden>•</span> {w}
                        </li>
                      ))}
                    </ul>
                    {it.specificExampleUsed && (
                      <p className="mt-1.5 text-xs text-ink-600">
                        <b>Example design:</b> {it.specificExampleUsed}
                      </p>
                    )}
                  </div>
                )}
                {it.lexicalUpgrades.length > 0 && (
                  <details className="mt-3">
                    <summary className="cursor-pointer text-xs font-medium text-brand-600">
                      Useful lexical upgrades
                    </summary>
                    <ul className="mt-2 space-y-1.5 text-sm text-ink-800">
                      {it.lexicalUpgrades.map((u, i) => (
                        <li key={i}>
                          <code className="rounded bg-rose-50 px-1 text-rose-800">{u.from}</code>{" "}
                          → <code className="rounded bg-mint-50 px-1 text-mint-800">{u.to}</code>
                          {u.why && <span className="text-ink-600"> — {u.why}</span>}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            ))}
          </div>

          {comparison.fullModelEssay && (
            <details className="card mt-4">
              <summary className="cursor-pointer font-medium text-ink-800">
                Show full AI model essay ({comparison.fullModelWordCount} words)
              </summary>
              <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-ink-800">
                {comparison.fullModelEssay}
              </p>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
