import { currentUser } from "@/lib/server/auth";
import { getSubmission } from "@/lib/server/store";
import { loadRevisionWorkspace, loadLretSession, getSessionFlowStatus, loadDailyDigest } from "@/lib/server/goldPipeline";
import ReportView from "@/components/ReportView";

// v16: the LRET (vocabulary/word-choice) summary used to be reduced to a
// single "This essay's vocabulary" link (see the removed comment below and
// Pipeline_Frontend_Spec_v2 §1) -- reported directly by the user as feeling
// like LRET feedback was simply missing, since general feedback ends and
// nothing lexical-specific is visible without an extra click. Now passing
// the full LretSession through so ReportView can render an inline summary
// right below the general feedback, with the dedicated page kept as "see
// everything" rather than the only way to see anything.

export default async function ReportPage({ params }: { params: { id: string } }) {
  const user = await currentUser();
  if (!user) return null;

  const sub = getSubmission(params.id);
  const allowed = sub && (sub.studentId === user.id || user.role === "trainer");

  if (!sub || !allowed) {
    return <p className="py-10 text-center text-sm text-ink-600">Report not found.</p>;
  }
  if (sub.status === "failed") {
    return (
      <div className="mx-auto max-w-xl py-10 text-center">
        <h1 className="text-xl font-semibold text-rose-800">Evaluation failed</h1>
        <p className="mt-2 text-sm text-ink-600">{sub.error}</p>
      </div>
    );
  }
  if (!sub.report) {
    return (
      <p className="py-10 text-center text-sm text-ink-600">
        Evaluation in progress — refresh in a minute.
      </p>
    );
  }

  // v10: pass through whether a revision workspace exists so ReportView can
  // link to it (see app/writing/revise/[id]/page.tsx) — the pipeline
  // generates this for every Gold submission, it just wasn't linked anywhere.
  const hasRevision = sub.sessionDir ? !!loadRevisionWorkspace(sub.sessionDir) : false;
  // v13: whether this essay has LRET (Vocabulary Coach) output — see
  // app/vocabulary-coach/[id]/page.tsx.
  // v16: now passing the full session (not just a boolean) so ReportView can
  // render an inline summary of it, not just gate a link.
  const lretSession = sub.sessionDir ? loadLretSession(sub.sessionDir) : undefined;
  const hasVocabulary = !!lretSession;

  // v19: session-flow stepper — Session_Flow_and_Vocab_Expansion_Spec_v1 §0.
  // Scoped to sub.studentId (not the viewer's id), same reasoning as
  // hasRevision/hasVocabulary above: a trainer viewing a student's report
  // should see that student's flow, not their own (empty) one.
  const flowStatus = getSessionFlowStatus(sub.studentId, { sessionDir: sub.sessionDir, submissionId: sub.id });
  const flowDigest = loadDailyDigest(sub.studentId);

  // v8: trainer viewing a student's report also sees the essay text itself
  // (previously never shown anywhere) — students see their own view as
  // before, essay omitted since they already have it.
  return (
    <ReportView
      report={sub.report}
      goal={user.intake?.goalBand}
      essayText={user.role === "trainer" ? sub.essay : undefined}
      hideFeedbackWidget={user.role === "trainer"}
      submissionId={sub.id}
      hasRevision={hasRevision}
      hasVocabulary={hasVocabulary}
      lretSession={lretSession}
      sessionFlow={flowStatus}
      dailyDigest={flowDigest}
    />
  );
}
