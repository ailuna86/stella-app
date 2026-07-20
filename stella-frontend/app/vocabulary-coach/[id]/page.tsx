import Link from "next/link";
import { currentUser } from "@/lib/server/auth";
import { getSubmission } from "@/lib/server/store";
import { loadLretSession, loadRevisionWorkspace } from "@/lib/server/goldPipeline";
import VocabularyCoachView from "@/components/VocabularyCoachView";

// v13: Vocabulary Coach — surfaces the LRET engine's real per-essay output.
// Previously this engine's work only ever showed up folded into the
// feedback report's "Focus areas" and, since v12, the AI-comparison lexical
// upgrades list — neither of those show the full FIX/ENHANCE/CLARIFY/KEEP
// picture the engine actually produces. See lib/server/goldPipeline.ts
// (loadLretSession) for the real 07d_lret_session.json schema this reads.
export default async function VocabularyCoachPage({ params }: { params: { id: string } }) {
  const user = await currentUser();
  if (!user) return null;

  const sub = getSubmission(params.id);
  const allowed = sub && (sub.studentId === user.id || user.role === "trainer");
  if (!sub || !allowed) {
    return <p className="py-10 text-center text-sm text-ink-600">Not found.</p>;
  }

  const lret = sub.sessionDir ? loadLretSession(sub.sessionDir) : undefined;
  if (!lret) {
    return (
      <div className="mx-auto max-w-xl py-10 text-center">
        <h1 className="text-xl font-semibold">Vocabulary coach not available</h1>
        <p className="mt-2 text-sm text-ink-600">
          This essay doesn't have vocabulary analysis attached — it's only produced for
          Gold-tier evaluations.
        </p>
        <Link href={`/writing/report/${sub.id}`} className="btn-secondary mt-4 inline-flex">
          Back to report
        </Link>
      </div>
    );
  }

  const hasWorkspace = sub.sessionDir ? !!loadRevisionWorkspace(sub.sessionDir) : false;

  return (
    <div className="mx-auto max-w-2xl py-6">
      <h1 className="text-2xl font-semibold">Vocabulary coach</h1>
      <p className="mt-2 text-sm leading-relaxed text-ink-600">
        Every key word and phrase in this essay, sorted into what needs fixing, what could be
        stronger, what's unclear, and what's already working. This is different from the error
        list on your report — instead of only flagging mistakes, it also offers upgrades for
        vocabulary that's correct but generic, and asks you to clarify anything too vague to
        score well, so you can actually improve your word choice, not just fix errors.
      </p>

      <VocabularyCoachView
        counts={lret.counts}
        units={{ fix: lret.fixUnits, enhance: lret.enhanceUnits, clarify: lret.clarifyUnits, keep: lret.keepUnits }}
        reviseHref={hasWorkspace ? `/writing/revise/${sub.id}` : null}
      />

      <div className="mt-4 flex gap-3">
        <Link href={`/writing/report/${sub.id}`} className="btn-secondary">
          Back to report
        </Link>
      </div>
    </div>
  );
}
