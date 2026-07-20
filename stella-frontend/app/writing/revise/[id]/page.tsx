import Link from "next/link";
import { currentUser } from "@/lib/server/auth";
import { getSubmission } from "@/lib/server/store";
import { loadRevisionWorkspace, loadLretSession } from "@/lib/server/goldPipeline";
import RevisionWorkspaceClient from "@/components/RevisionWorkspaceClient";

// v10: new — the Gold pipeline generates a full sentence-by-sentence revision
// workspace for every evaluated essay (10_revision_workspace.json: each
// sentence colored green/yellow/red with a student-safe hint, sourced from
// the real Evaluator + detector + errormap output). There was previously no
// screen for this at all. See lib/server/goldPipeline.ts for how it's read.
// v13: rebuilt to match the Stitch essay_revision_workspace design exactly —
// functional filter toolbar, working "copy clean essay to editor", and the
// AI comparison + editor merged into one client component so state (the
// student's in-progress revision text) is shared between them. See
// components/RevisionWorkspaceClient.tsx.
const WAVE_DOT: Record<string, string> = { red: "bg-rose-400", yellow: "bg-amber-400", green: "bg-mint-400" };

export default async function RevisePage({ params }: { params: { id: string } }) {
  const user = await currentUser();
  if (!user) return null;

  const sub = getSubmission(params.id);
  const allowed = sub && (sub.studentId === user.id || user.role === "trainer");
  if (!sub || !allowed) {
    return <p className="py-10 text-center text-sm text-ink-600">Not found.</p>;
  }

  const workspace = sub.sessionDir ? loadRevisionWorkspace(sub.sessionDir) : undefined;
  if (!workspace) {
    return (
      <div className="mx-auto max-w-xl py-10 text-center">
        <h1 className="text-xl font-semibold">Revision workspace not available</h1>
        <p className="mt-2 text-sm text-ink-600">
          This essay doesn't have a revision workspace attached — it's only produced for
          Gold-tier evaluations.
        </p>
        <Link href={`/writing/report/${sub.id}`} className="btn-secondary mt-4 inline-flex">
          Back to report
        </Link>
      </div>
    );
  }

  const hasVocabulary = sub.sessionDir ? !!loadLretSession(sub.sessionDir) : false;
  const wordPct = workspace.prewriting?.minimumWords
    ? Math.min(100, Math.round((workspace.wordCount / workspace.prewriting.minimumWords) * 100))
    : null;

  return (
    <div className="mx-auto max-w-6xl py-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Revise your essay</h1>
          <p className="mt-1 text-xs uppercase tracking-wide text-ink-400">
            Hints only mode · Gold tier
          </p>
        </div>
        {hasVocabulary && (
          <Link
            href={`/vocabulary-coach/${sub.id}`}
            className="rounded-full border border-brand-200 px-4 py-1.5 text-xs font-medium text-brand-800 hover:bg-brand-50"
          >
            See vocabulary breakdown
          </Link>
        )}
      </div>
      <p className="mt-2 max-w-3xl text-sm leading-relaxed text-ink-600">
        Essay revision breaks your submission into paragraphs and individual sentences and
        marks each one green, yellow, or red based on what the scoring engines actually
        found — so instead of re-reading your whole essay hunting for problems, you can see
        exactly which sentences are fine, which need a check, and which need rewriting, with
        a short hint on each one. Work through the red sentences first, then the yellow ones,
        and resubmit once you're done for a fresh band score.
      </p>
      <p className="mt-2 text-sm text-ink-600">
        {workspace.wordCount} words · {workspace.sentenceCounts.red} sentence
        {workspace.sentenceCounts.red === 1 ? "" : "s"} to rewrite ·{" "}
        {workspace.sentenceCounts.yellow} to check
      </p>

      {workspace.prewriting && (
        <details className="card mt-4 shadow-soft" open>
          <summary className="cursor-pointer font-medium text-ink-800">
            Before you write or revise
          </summary>
          <div className="mt-3 grid gap-6 border-t border-brand-100 pt-4 md:grid-cols-2">
            <div className="space-y-3">
              <div>
                <div className="mb-1 flex items-center justify-between text-xs">
                  <span className="text-ink-600">Target word count</span>
                  <span className="font-medium text-mint-600">
                    {workspace.prewriting.minimumWords}+ words
                    {workspace.prewriting.recommendedRange && ` (${workspace.prewriting.recommendedRange} ideal)`}
                  </span>
                </div>
                {wordPct !== null && (
                  <div className="h-2 w-full overflow-hidden rounded-full bg-brand-50">
                    <div className="h-full rounded-full bg-brand-600" style={{ width: `${wordPct}%` }} />
                  </div>
                )}
              </div>
              {Object.keys(workspace.prewriting.paragraphTargets).length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {Object.entries(workspace.prewriting.paragraphTargets).map(([k, v]) => (
                    <span key={k} className="rounded-full bg-brand-50 px-3 py-1 text-xs text-brand-800">
                      {k.replace(/_/g, " ")}: {v}
                    </span>
                  ))}
                </div>
              )}
              {workspace.prewriting.strongExampleRule && (
                <p className="text-sm text-ink-700">
                  <b className="text-mint-700">Strong examples:</b> {workspace.prewriting.strongExampleRule}
                </p>
              )}
              {workspace.prewriting.weakExampleRule && (
                <p className="text-sm text-ink-700">
                  <b className="text-rose-700">Avoid:</b> {workspace.prewriting.weakExampleRule}
                </p>
              )}
            </div>
            {workspace.prewriting.bodyParagraphFormula.length > 0 && (
              <div className="rounded-card border border-brand-100 bg-brand-50/30 p-4">
                <p className="mb-2 text-xs font-bold uppercase tracking-wide text-brand-600">
                  Body paragraph formula
                </p>
                <div className="flex flex-wrap items-center gap-1.5">
                  {workspace.prewriting.bodyParagraphFormula.map((f, i) => (
                    <span key={i} className="flex items-center gap-1.5">
                      <span className="rounded border border-brand-200 bg-white px-2 py-0.5 text-[11px] font-medium text-ink-800">
                        {f}
                      </span>
                      {i < workspace.prewriting!.bodyParagraphFormula.length - 1 && (
                        <span className="text-brand-400">→</span>
                      )}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </details>
      )}

      {(workspace.overallHints.languageRepair.length > 0 || workspace.overallHints.paragraphFunction.length > 0) && (
        <div className="card mt-4 shadow-soft">
          <h3 className="font-medium text-ink-800">Whole-essay priorities</h3>
          <p className="mt-0.5 text-xs text-ink-400">What to fix first, across the whole essay -- before you dig into individual sentences.</p>
          <div className="mt-3 space-y-2">
            {[...workspace.overallHints.languageRepair, ...workspace.overallHints.paragraphFunction].map((h, i) => (
              <div
                key={i}
                className={`rounded-lg border-l-4 p-2.5 text-sm ${
                  h.level === "red"
                    ? "border-rose-400 bg-rose-50/60 text-rose-800"
                    : h.level === "green"
                    ? "border-mint-400 bg-mint-50/60 text-mint-800"
                    : "border-amber-400 bg-amber-50/60 text-amber-800"
                }`}
              >
                {h.text}
              </div>
            ))}
          </div>
        </div>
      )}

      {workspace.waves.length > 0 && (
        <div className="card mt-4 shadow-soft">
          <h3 className="font-medium text-ink-800">Revision plan</h3>
          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {workspace.waves.map((w, i) => (
              <div key={i} className="rounded-lg border-b-2 border-brand-400 bg-brand-50/40 p-3">
                <p className="text-[11px] text-ink-400">Wave {i + 1}</p>
                <p className="mt-0.5 flex items-center gap-1.5 text-sm font-bold text-ink-800">
                  {w.level && <span className={`h-2 w-2 rounded-full ${WAVE_DOT[w.level] ?? WAVE_DOT.yellow}`} />}
                  {w.title}
                </p>
                {w.text && <p className="mt-0.5 text-xs text-ink-600">{w.text}</p>}
              </div>
            ))}
          </div>
        </div>
      )}

      {workspace.checklist.length > 0 && (
        <details className="card mt-4 shadow-soft">
          <summary className="cursor-pointer font-medium text-ink-800">Before you resubmit</summary>
          <ul className="mt-3 space-y-1 border-t border-brand-100 pt-3 text-sm text-ink-600">
            {workspace.checklist.map((c, i) => (
              <li key={i} className="flex gap-2">
                <span aria-hidden>•</span> {c}
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className="mt-6">
        <RevisionWorkspaceClient
          paragraphs={workspace.paragraphs}
          cleanText={workspace.cleanText}
          originalText={sub.essay}
          submissionId={sub.id}
          prompt={sub.prompt}
          assignmentId={sub.assignmentId}
          evaluationsLeft={user.entitlements.evaluations_left}
        />
      </div>

      <div className="mt-4 flex gap-3">
        <Link href={`/writing/report/${sub.id}`} className="btn-secondary">
          Back to report
        </Link>
      </div>
    </div>
  );
}
