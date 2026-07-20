import { redirect } from "next/navigation";
import { currentUser } from "@/lib/server/auth";
import { getSubmission, algorithmFeedbackFor } from "@/lib/server/store";
import ReportView from "@/components/ReportView";
import AlgorithmReviewForm from "@/components/AlgorithmReviewForm";

// v8: new — dedicated QA screen: essay + AI report + structured review
// form, in one place, so a trainer doesn't have to piece it together.
export default async function AlgorithmReviewPage({ params }: { params: { id: string } }) {
  const user = await currentUser();
  if (!user) return null;
  if (user.role !== "trainer") redirect("/dashboard");

  const sub = getSubmission(params.id);
  if (!sub || !sub.report) {
    return (
      <p className="py-10 text-center text-sm text-ink-600">
        Submission not found, or not finished evaluating yet.
      </p>
    );
  }

  const criteria = Object.keys(sub.report.score_summary.criteria_bands);
  const errorIds = (sub.report.focus_area_feedback ?? [])
    .flatMap((fa) => fa.annotated_errors ?? [])
    .map((e) => ({ id: e.error_id, excerpt: e.excerpt }));
  const pastReviews = algorithmFeedbackFor(sub.id);

  return (
    <div className="mx-auto max-w-2xl py-6">
      <h1 className="text-2xl font-semibold">Algorithm review</h1>
      <ReportView report={sub.report} essayText={sub.essay} hideFeedbackWidget />
      <AlgorithmReviewForm submissionId={sub.id} criteria={criteria} errorIds={errorIds} />
      {pastReviews.length > 0 && (
        <div className="card mt-4">
          <h2 className="font-medium">Past reviews of this submission</h2>
          <div className="mt-2 space-y-2">
            {pastReviews.map((r) => (
              <div key={r.id} className="rounded-card border border-brand-100 p-3 text-sm">
                <p className="text-xs text-ink-400">
                  {new Date(r.createdAt).toLocaleString()} · {r.overallAccuracy.replace(/_/g, " ")}
                </p>
                {r.generalNotes && <p className="mt-1 text-ink-800">{r.generalNotes}</p>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
