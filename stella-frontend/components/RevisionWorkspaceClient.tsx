"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

interface RevisionSpan {
  quote: string;
  explanation: string;
}
interface RevisionSentence {
  index: number;
  text: string;
  status: string;
  statusLabel: string;
  hint: string;
  spans: RevisionSpan[];
}
interface RevisionParagraph {
  index: number;
  role: string;
  status: string;
  statusLabel: string;
  hint: string;
  alerts: string[];
  sentences: RevisionSentence[];
}

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

// v20: scoped re-check types — Session_Flow_and_Vocab_Expansion_Spec_v1 §1.
// Additive, alongside the existing AI-comparison result above, not a
// replacement for it. Never a holistic band/Task-Response/Coherence claim.
interface RecheckErrorItem {
  family: string;
  rubric: string | null;
  quote: string;
  message: string;
  suggestedRevision: string | null;
  severity: string | null;
}
interface RecheckSentence {
  originalText: string;
  revisedText: string;
  errorsBefore: RecheckErrorItem[];
  errorsAfter: RecheckErrorItem[];
  fixed: RecheckErrorItem[];
  introduced: RecheckErrorItem[];
  persisting: RecheckErrorItem[];
  status: string;
  statusLabel: string;
}
interface RecheckResult {
  sentencesRewritten: number;
  nowErrorFree: number;
  alreadyCleanRewrite: number;
  stillHasErrors: number;
  introducedNewErrorSentences: number;
  totalErrorsFixed: number;
  totalErrorsIntroduced: number;
  honestSummaryText: string;
  scopeDisclaimer: string;
  newSentencesAdded: number;
  sentencesRemoved: number;
  truncatedForCostCap: boolean;
  sentences: RecheckSentence[];
}

// v20: a sentence's own card color follows the SAME status it actually ended
// up in, not a generic "changed" color — rose only for genuinely worse/still
// broken, mint only for genuinely fixed, amber for "changed but mixed" or a
// non-error edit. Matches the red/amber/mint convention already used for
// paragraph/sentence status above.
const RECHECK_CARD_STYLE: Record<string, string> = {
  now_error_free: "border-mint-400 bg-mint-50",
  already_clean_rewrite: "border-brand-100 bg-white",
  introduced_new_error: "border-rose-400 bg-rose-50",
  got_worse: "border-rose-400 bg-rose-50",
  still_has_errors: "border-amber-300 bg-amber-50",
  partially_improved: "border-amber-300 bg-amber-50",
  changed_errors: "border-amber-300 bg-amber-50",
};
const RECHECK_BADGE_STYLE: Record<string, string> = {
  now_error_free: "bg-mint-400 text-white",
  already_clean_rewrite: "bg-brand-100 text-brand-700",
  introduced_new_error: "bg-rose-400 text-white",
  got_worse: "bg-rose-400 text-white",
  still_has_errors: "bg-amber-300 text-amber-900",
  partially_improved: "bg-amber-300 text-amber-900",
  changed_errors: "bg-amber-300 text-amber-900",
};

type Filter = "all" | "red" | "redyellow" | "yellow";

const CARD_STYLE: Record<string, string> = {
  red: "border-rose-400 bg-rose-50",
  yellow: "border-amber-300 bg-amber-50",
  green: "border-mint-400 bg-mint-50",
};
const BADGE_STYLE: Record<string, string> = {
  red: "bg-rose-400 text-white",
  yellow: "bg-amber-300 text-amber-900",
  green: "bg-mint-400 text-white",
};
const BORDER_L: Record<string, string> = {
  red: "border-l-rose-400",
  yellow: "border-l-amber-300",
  green: "border-l-mint-400",
};

function matchesFilter(status: string, filter: Filter): boolean {
  if (filter === "all") return true;
  if (filter === "red") return status === "red";
  if (filter === "redyellow") return status === "red" || status === "yellow";
  if (filter === "yellow") return status === "yellow";
  return true;
}

export default function RevisionWorkspaceClient({
  paragraphs,
  cleanText,
  originalText,
  submissionId,
  prompt,
  assignmentId,
  evaluationsLeft,
}: {
  paragraphs: RevisionParagraph[];
  cleanText: string;
  originalText: string;
  submissionId: string;
  prompt: string;
  assignmentId: string | null;
  evaluationsLeft: number;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [essay, setEssay] = useState(cleanText || originalText);
  const [busy, setBusy] = useState(false);
  const [comparing, setComparing] = useState(false);
  const [err, setErr] = useState("");
  const [comparison, setComparison] = useState<ComparisonResult | null>(null);
  // v20: fired alongside the AI comparison (same click, separate request —
  // see the "Design choice" comment on runRevisionScopedRecheck in
  // goldPipeline.ts for why this is a sibling call, not folded into one).
  const [recheck, setRecheck] = useState<RecheckResult | null>(null);
  const [recheckErr, setRecheckErr] = useState("");
  const router = useRouter();

  const words = essay.trim() ? essay.trim().split(/\s+/).length : 0;
  const counts = useMemo(() => {
    let red = 0,
      yellow = 0;
    for (const p of paragraphs) for (const s of p.sentences) {
      if (s.status === "red") red++;
      else if (s.status === "yellow") yellow++;
    }
    return { red, yellow };
  }, [paragraphs]);

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
    setRecheckErr("");
    setRecheck(null);
    // v20: both requests fire from this one click and run concurrently —
    // no extra round trip is perceived by the student even though they're
    // two separate backend calls (see goldPipeline.ts's "Design choice"
    // comment for why they're kept separate server-side). Each is handled
    // independently so a failure in one doesn't blank out the other.
    const comparisonPromise = fetch("/api/writing/revise/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ submissionId, revisedText: essay }),
    })
      .then((res) => res.json())
      .then((data) => {
        if (data.ok) setComparison(data.result);
        else setErr(data.error ?? "Comparison failed.");
      })
      .catch(() => setErr("Comparison failed — please try again."));

    const recheckPromise = fetch("/api/writing/revise/recheck", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ submissionId, revisedText: essay }),
    })
      .then((res) => res.json())
      .then((data) => {
        if (data.ok) setRecheck(data.result);
        else setRecheckErr(data.error ?? "Sentence check failed.");
      })
      .catch(() => setRecheckErr("Sentence check failed — please try again."));

    await Promise.allSettled([comparisonPromise, recheckPromise]);
    setComparing(false);
  }

  // Flatten every paragraph's lexical upgrades into one top-level list, as
  // in the reference design — the student learns from these regardless of
  // which paragraph they came from.
  const allUpgrades = useMemo(
    () => (comparison?.items ?? []).flatMap((it) => it.lexicalUpgrades.map((u) => ({ ...u, paragraph: it.paragraphNumber }))),
    [comparison]
  );

  return (
    <div>
      {/* Sticky filter toolbar */}
      <div className="sticky top-0 z-10 -mx-4 mb-6 flex flex-col gap-3 border-b border-brand-100 bg-white/95 px-4 py-3 backdrop-blur sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          <span className="mr-1 text-xs text-ink-400">Filter:</span>
          {(
            [
              ["all", "Show all"],
              ["red", "Only red"],
              ["redyellow", "Yellow + red"],
              ["yellow", "Only yellow"],
            ] as [Filter, string][]
          ).map(([f, label]) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={`rounded-full px-4 py-1.5 text-xs font-medium transition ${
                filter === f
                  ? "bg-brand-600 text-white"
                  : "border border-brand-100 text-ink-600 hover:bg-brand-50"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => setEssay(cleanText || originalText)}
          className="btn-secondary shrink-0 !px-4 !py-2 text-xs"
        >
          Copy clean essay to editor
        </button>
      </div>

      <div className="grid gap-6 lg:grid-cols-12 lg:items-start">
        {/* Left: annotated paragraphs */}
        <div className="space-y-4 lg:col-span-7">
          {paragraphs.map((p) => {
            const filteredSentences = p.sentences.filter((s) => matchesFilter(s.status, filter));
            if (filter !== "all" && filteredSentences.length === 0) return null;
            const shown = filter === "all" ? p.sentences : filteredSentences;
            return (
              <div
                key={p.index}
                className={`overflow-hidden rounded-card border-2 border-l-4 ${
                  CARD_STYLE[p.status] ?? CARD_STYLE.yellow
                } ${BORDER_L[p.status] ?? BORDER_L.yellow} bg-white`}
              >
                <div className="flex items-center justify-between bg-white/60 px-4 py-2.5">
                  <span className="text-xs font-bold uppercase tracking-widest text-ink-600">
                    {p.role || `Paragraph ${p.index}`}
                  </span>
                  <span
                    className={`rounded px-2 py-0.5 text-[10px] font-bold ${BADGE_STYLE[p.status] ?? BADGE_STYLE.yellow}`}
                  >
                    {p.statusLabel}
                  </span>
                </div>
                <div className="space-y-3 p-4">
                  {p.hint && <p className="text-sm text-ink-700">{p.hint}</p>}
                  {p.alerts.map((a, i) => (
                    <p key={i} className="text-xs text-ink-600">
                      ⚠ {a}
                    </p>
                  ))}
                  {shown.map((s) => (
                    <div
                      key={s.index}
                      className={`relative rounded-card border p-3 text-sm ${CARD_STYLE[s.status] ?? CARD_STYLE.yellow}`}
                    >
                      <span
                        className={`absolute right-3 top-2.5 rounded-full px-2 py-0.5 text-[10px] font-bold uppercase shadow-sm ${
                          BADGE_STYLE[s.status] ?? BADGE_STYLE.yellow
                        }`}
                      >
                        {s.statusLabel}
                      </span>
                      <p className="pr-16 text-ink-800">{s.text}</p>
                      {s.hint && (
                        <div className="mt-2 rounded-md bg-white/80 p-2.5">
                          <p className="text-xs font-semibold text-ink-800">Hint</p>
                          <p className="mt-0.5 text-xs text-ink-600">{s.hint}</p>
                          {s.spans.length > 0 && (
                            <div className="mt-1.5 flex flex-wrap gap-1.5">
                              {s.spans.map((sp, i) => (
                                <span key={i} className="rounded bg-white px-1.5 py-0.5 text-[11px] italic text-ink-700">
                                  "{sp.quote}"
                                  {sp.explanation && <span className="not-italic text-ink-500"> — {sp.explanation}</span>}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>

        {/* Right: sticky editor */}
        <div className="lg:sticky lg:top-24 lg:col-span-5 lg:self-start">
          <div className="overflow-hidden rounded-2xl border border-brand-100 bg-white shadow-soft">
            <div className="flex items-center justify-between bg-brand-600 px-4 py-3 text-white">
              <span className="text-sm font-medium">Live revision editor</span>
              <span className="text-xs opacity-80">{words} words</span>
            </div>
            <textarea
              className="h-72 w-full resize-none p-4 text-sm leading-relaxed outline-none"
              value={essay}
              onChange={(e) => setEssay(e.target.value)}
            />
            <div className="flex items-center justify-end gap-2 border-t border-brand-100 bg-brand-50/50 px-3 py-2">
              <span className="flex items-center gap-1 text-[11px] font-bold text-rose-600">
                <span className="h-2 w-2 rounded-full bg-rose-400" /> {counts.red} red
              </span>
              <span className="flex items-center gap-1 text-[11px] font-bold text-amber-700">
                <span className="h-2 w-2 rounded-full bg-amber-400" /> {counts.yellow} yellow
              </span>
            </div>
            <div className="space-y-3 border-t border-brand-100 bg-brand-50/30 p-4">
              {err && <p className="text-sm text-rose-600">{err}</p>}
              <button className="btn-secondary w-full" onClick={compare} disabled={comparing || words < 20}>
                {comparing ? "Comparing…" : "Compare with AI model"}
              </button>
              <div className="relative">
                <button
                  className="btn-primary w-full"
                  onClick={submit}
                  disabled={busy || words < 50 || evaluationsLeft <= 0}
                >
                  {busy ? "Evaluating…" : "Resubmit for re-evaluation"}
                </button>
                <span className="absolute -top-2.5 -right-1.5 rounded-full border-2 border-white bg-amber-500 px-2 py-0.5 text-[10px] font-bold text-white shadow-sm">
                  1 credit
                </span>
              </div>
              {evaluationsLeft <= 0 && (
                <p className="text-xs text-rose-600">
                  No evaluations left on your plan — ask your trainer about upgrading.
                </p>
              )}
              {busy && (
                <p className="text-xs text-ink-400">
                  This runs the full scoring pipeline again and can take a few minutes — keep
                  this tab open.
                </p>
              )}
            </div>
          </div>

          <div className="mt-4 flex gap-3 rounded-card bg-mint-50 p-4">
            <span aria-hidden className="text-mint-600">
              ✦
            </span>
            <div>
              <p className="text-xs font-bold text-mint-700">Expert tip</p>
              <p className="mt-0.5 text-xs text-ink-700">
                Try using complex sentence structures (subordinate clauses) when fixing red
                sentences — it also helps your Grammatical Range score.
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* v20: Scoped sentence re-check — additive, shown alongside the AI
          comparison below, not a replacement for it. Deliberately no band
          score / Task Response / Coherence claim anywhere in this panel —
          see RevisionScopedRecheck's own scopeDisclaimer, rendered verbatim. */}
      {recheckErr && !recheck && (
        <p className="mt-6 text-sm text-rose-600">{recheckErr}</p>
      )}
      {recheck && (
        <div className="mt-8 space-y-4 border-t border-brand-100 pt-6">
          <div>
            <h3 className="text-xs font-bold uppercase tracking-widest text-ink-400">
              Sentence-level re-check
            </h3>
            <p className="mt-1 text-sm text-ink-800">{recheck.honestSummaryText}</p>
            <p className="mt-1 text-xs text-ink-500">{recheck.scopeDisclaimer}</p>
            {recheck.truncatedForCostCap && (
              <p className="mt-1 text-xs text-amber-700">
                You rewrote more sentences than we could re-check in one pass — showing the first
                batch below.
              </p>
            )}
          </div>

          {recheck.sentences.length > 0 && (
            <div className="space-y-3">
              {recheck.sentences.map((s, i) => (
                <div
                  key={i}
                  className={`rounded-card border p-3 text-sm ${
                    RECHECK_CARD_STYLE[s.status] ?? RECHECK_CARD_STYLE.changed_errors
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${
                        RECHECK_BADGE_STYLE[s.status] ?? RECHECK_BADGE_STYLE.changed_errors
                      }`}
                    >
                      {s.statusLabel}
                    </span>
                  </div>
                  <div className="mt-2 grid gap-2 md:grid-cols-2">
                    <div className="rounded-md bg-white/80 p-2">
                      <p className="text-[10px] font-bold uppercase tracking-wide text-ink-400">
                        Before
                      </p>
                      <p className="mt-0.5 text-ink-700">{s.originalText}</p>
                    </div>
                    <div className="rounded-md bg-white/80 p-2">
                      <p className="text-[10px] font-bold uppercase tracking-wide text-ink-400">
                        After (your rewrite)
                      </p>
                      <p className="mt-0.5 text-ink-800">{s.revisedText}</p>
                    </div>
                  </div>
                  {s.fixed.length > 0 && (
                    <p className="mt-2 text-xs text-mint-700">
                      ✓ Fixed: {s.fixed.map((e) => e.family.replace(/_/g, " ").toLowerCase()).join(", ")}
                    </p>
                  )}
                  {s.introduced.length > 0 && (
                    <p className="mt-2 text-xs text-rose-700">
                      ⚠ New: {s.introduced.map((e) => e.message || e.family.replace(/_/g, " ").toLowerCase()).join("; ")}
                    </p>
                  )}
                  {s.persisting.length > 0 && (
                    <p className="mt-2 text-xs text-amber-700">
                      Still there: {s.persisting.map((e) => e.family.replace(/_/g, " ").toLowerCase()).join(", ")}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* AI comparison */}
      {comparison && (
        <div className="mt-8 space-y-6 border-t border-brand-100 pt-6">
          <div className="rounded-card border border-brand-200 bg-brand-50 p-4 text-sm text-brand-800">
            This model rewrite is generated from your <b>original</b> essay, not your revision —
            use it to see both against a strong version. Don&apos;t copy it as your own work.
            {comparison.fullModelWordCount > 0 && (
              <span className="ml-1 text-brand-600">({comparison.fullModelWordCount} words)</span>
            )}
          </div>

          {!comparison.modelAvailable && (
            <p className="text-sm text-ink-600">
              A model rewrite couldn&apos;t be generated for this attempt — your paragraph
              comparison is still shown below.
            </p>
          )}

          <div className="space-y-6">
            {comparison.items.map((it) => (
              <div key={it.paragraphNumber} className="space-y-2">
                <h3 className="text-xs font-bold uppercase tracking-widest text-ink-400">
                  {it.role || `Paragraph ${it.paragraphNumber}`}
                </h3>
                <div className="grid gap-3 md:grid-cols-3">
                  <div className="rounded-card border border-brand-100 bg-white p-3 shadow-soft">
                    <p className="text-[10px] font-bold uppercase tracking-wide text-ink-400">Original</p>
                    <p className="mt-1 text-sm text-ink-800">{it.original}</p>
                  </div>
                  <div className="relative rounded-card border-2 border-brand-400 bg-white p-3 shadow-soft">
                    <span className="absolute -top-2.5 left-3 rounded-full bg-brand-600 px-2 py-0.5 text-[10px] font-bold uppercase text-white">
                      Your revision
                    </span>
                    <p className="mt-1.5 text-sm text-ink-800">{it.studentRevision}</p>
                  </div>
                  <div className="select-none rounded-card border border-mint-200 bg-mint-50 p-3">
                    <p className="text-[10px] font-bold uppercase tracking-wide text-mint-600">✦ AI model rewrite</p>
                    <p className="mt-1 text-sm italic text-ink-700">
                      {it.aiModel ?? "Not available for this paragraph."}
                    </p>
                  </div>
                </div>
                {it.whyStronger.length > 0 && (
                  <div className="rounded-r-xl border-l-4 border-amber-400 bg-amber-50 p-3">
                    <p className="text-xs font-bold uppercase tracking-wide text-amber-700">Why this is stronger</p>
                    <ul className="mt-1.5 space-y-1 text-sm text-ink-800">
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
              </div>
            ))}
          </div>

          {allUpgrades.length > 0 && (
            <div>
              <h3 className="font-medium text-brand-800">Useful lexical upgrades</h3>
              <div className="mt-2 space-y-2">
                {allUpgrades.map((u, i) => (
                  <details key={i} className="overflow-hidden rounded-card border border-brand-100 bg-white">
                    <summary className="flex cursor-pointer flex-wrap items-center gap-2 p-3 text-sm hover:bg-brand-50">
                      <span className="rounded bg-rose-50 px-2 py-0.5 text-[10px] font-bold text-rose-700">
                        STUDENT
                      </span>
                      <span className="italic text-ink-700">"{u.from}"</span>
                      <span className="text-ink-400">→</span>
                      <span className="rounded bg-mint-50 px-2 py-0.5 text-[10px] font-bold text-mint-700">
                        MODEL
                      </span>
                      <span className="font-medium text-brand-800">"{u.to}"</span>
                    </summary>
                    {u.why && (
                      <p className="border-t border-brand-100 bg-brand-50/40 p-3 text-xs text-ink-600">{u.why}</p>
                    )}
                  </details>
                ))}
              </div>
            </div>
          )}

          {comparison.fullModelEssay && (
            <details className="card shadow-soft">
              <summary className="cursor-pointer font-medium text-brand-800">
                Show full AI model essay ({comparison.fullModelWordCount} words)
              </summary>
              <div className="mt-3 rounded-card bg-rose-50 p-2.5 text-xs text-rose-700">
                Reminder: copying this text directly into an exam essay will lead to
                disqualification for plagiarism.
              </div>
              <p className="mt-3 select-none whitespace-pre-wrap text-sm leading-relaxed text-ink-800">
                {comparison.fullModelEssay}
              </p>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
