import { currentUser } from "@/lib/server/auth";
import { getSubmission } from "@/lib/server/store";
import { loadRevisionWorkspace, loadLretSession } from "@/lib/server/goldPipeline";
import ReportView from "@/components/ReportView";

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
  const hasVocabulary = sub.sessionDir ? !!loadLretSession(sub.sessionDir) : false;

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
    />
  );
}
