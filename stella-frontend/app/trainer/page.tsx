import { redirect } from "next/navigation";
import Link from "next/link";
import { currentUser } from "@/lib/server/auth";
import {
  getUsers,
  getAssignments,
  submissionsFor,
  practiceResultsFor,
  allPlatformFeedback,
  getPrompts,
  pendingUpgradeRequests,
  getUserById,
} from "@/lib/server/store";
import { getLearningRoadmap, serviceLabel } from "@/lib/server/study-plan";
import { CRITERION_LABELS } from "@/lib/types";
import AssignmentForm from "@/components/AssignmentForm";
import AddStudentForm from "@/components/AddStudentForm";
import AddTrainerForm from "@/components/AddTrainerForm";
import PromptReviewList from "@/components/PromptReviewList";
import UpgradeRequestsList from "@/components/UpgradeRequestsList";

// v5 trainer console: full history per student (expandable), group-wide
// common mistakes, and collected platform feedback.
// v6: adds a second "Add trainer" form + trainers list.
// v8: adds a prompt-review queue (drafted prompts awaiting approval before
// they can be assigned), a study-plan summary + "due for essay" flag per
// student, submissions the pipeline flagged as needing human review sorted
// first, and a link into the new algorithm-review tool per submission.
// v9: study-plan lookup now passes s.plan through (Gold and Premium students
// read from different pipeline output directories — see study-plan.ts).
export default async function TrainerConsole() {
  const user = await currentUser();
  if (!user) return null;
  if (user.role !== "trainer") redirect("/dashboard");

  const students = getUsers().filter((u) => u.role === "student");
  const trainers = getUsers().filter((u) => u.role === "trainer");
  const assignments = getAssignments().sort((a, b) =>
    b.createdAt.localeCompare(a.createdAt)
  );
  const feedback = allPlatformFeedback().slice(-20).reverse();
  const approvedPrompts = getPrompts({ approvedOnly: true });
  const pendingPrompts = getPrompts().filter((p) => !p.approved);
  const upgradeRequests = pendingUpgradeRequests().map((r) => {
    const student = getUserById(r.userId);
    return {
      id: r.id,
      studentName: student?.name ?? "Unknown",
      studentEmail: student?.email ?? "",
      requestedPlan: r.requestedPlan,
      createdAt: r.createdAt,
    };
  });

  // Aggregate common mistakes across all evaluated submissions
  const familyCounts = new Map<string, number>();
  for (const s of students) {
    for (const sub of submissionsFor(s.id)) {
      if (sub.status !== "done" || !sub.report) continue;
      for (const fa of sub.report.focus_area_feedback ?? []) {
        for (const err of fa.annotated_errors ?? []) {
          familyCounts.set(err.error_type, (familyCounts.get(err.error_type) ?? 0) + 1);
        }
      }
    }
  }
  const commonMistakes = [...familyCounts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8);

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="flex items-center gap-2 text-2xl font-semibold text-ink-900">
        <span className="material-symbols-outlined text-brand-600">school</span>
        Trainer console
      </h1>

      <div className="card mt-4">
        <h2 className="flex items-center gap-2 font-medium text-ink-900">
          <span className="material-symbols-outlined text-brand-600">person_add</span>
          Add student
        </h2>
        <p className="mt-1 text-xs text-ink-400">
          The student then signs in with this email and confirms it with a code.
        </p>
        <AddStudentForm />
      </div>

      <div className="card mt-4">
        <h2 className="flex items-center gap-2 font-medium text-ink-900">
          <span className="material-symbols-outlined text-brand-600">group_add</span>
          Add trainer
        </h2>
        <p className="mt-1 text-xs text-ink-400">
          Gives full trainer access (all students, all consoles). They sign in
          the same way — email, then a confirmation code.
        </p>
        <AddTrainerForm />
        {trainers.length > 0 && (
          <div className="mt-3 space-y-1 border-t border-brand-100 pt-3">
            {trainers.map((t) => (
              <p key={t.id} className="text-sm text-ink-600">
                {t.name} · {t.email} · {t.verifiedAt ? "verified" : "not signed in yet"}
              </p>
            ))}
          </div>
        )}
      </div>

      <div className="card mt-4">
        <h2 className="flex items-center gap-2 font-medium text-ink-900">
          <span className="material-symbols-outlined text-brand-600">upgrade</span>
          Upgrade requests
        </h2>
        <p className="mt-1 text-xs text-ink-400">
          Students who requested Premium or Gold from the plan page. No payment
          processor yet — follow up and activate manually, then mark handled.
        </p>
        <UpgradeRequestsList requests={upgradeRequests} />
      </div>

      <div className="card mt-4">
        <h2 className="flex items-center gap-2 font-medium text-ink-900">
          <span className="material-symbols-outlined text-brand-600">fact_check</span>
          Prompts awaiting review
        </h2>
        <p className="mt-1 text-xs text-ink-400">
          Original, IELTS-style, drafted for you to check and approve — not shown to
          students or assignable until approved here.
        </p>
        <PromptReviewList prompts={pendingPrompts} />
      </div>

      <div className="card mt-4">
        <h2 className="flex items-center gap-2 font-medium text-ink-900">
          <span className="material-symbols-outlined text-brand-600">assignment_add</span>
          New assignment
        </h2>
        <AssignmentForm
          students={students.map((s) => ({ id: s.id, name: s.name }))}
          prompts={approvedPrompts}
        />
      </div>

      <div className="card mt-4">
        <h2 className="flex items-center gap-2 font-medium text-ink-900">
          <span className="material-symbols-outlined text-brand-600">bar_chart</span>
          Common mistakes across the group
        </h2>
        {commonMistakes.length === 0 ? (
          <p className="mt-2 text-sm text-ink-400">No evaluated essays yet.</p>
        ) : (
          <div className="mt-3 space-y-1.5">
            {commonMistakes.map(([family, count]) => (
              <div key={family} className="flex items-center gap-2">
                <div
                  className="h-5 rounded bg-brand-200"
                  style={{ width: `${Math.min(100, count * 14)}px` }}
                />
                <span className="text-sm text-ink-800">
                  {family.replace(/_/g, " ").toLowerCase()}
                </span>
                <span className="text-xs text-ink-400">×{count}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card mt-4">
        <h2 className="flex items-center gap-2 font-medium text-ink-900">
          <span className="material-symbols-outlined text-brand-600">groups</span>
          Students
        </h2>
        <div className="mt-3 space-y-3">
          {students.length === 0 && (
            <p className="text-sm text-ink-400">No students yet — add them above.</p>
          )}
          {students.map((s) => {
            const subs = submissionsFor(s.id);
            const done = subs
              .filter((x) => x.status === "done" && x.report)
              // v8: submissions the pipeline flagged as ambiguous surface first
              .sort((a, b) => Number(!!b.report?.escalate_to_human_review) - Number(!!a.report?.escalate_to_human_review));
            const practices = practiceResultsFor(s.id);
            const roadmap =
              s.plan === "gold" && done[0]?.sessionDir ? getLearningRoadmap(done[0].sessionDir) : undefined;
            const avgAcc = practices.length
              ? Math.round(
                  (practices.reduce(
                    (acc: number, p: any) => acc + (p.total ? p.correct / p.total : 0),
                    0
                  ) /
                    practices.length) *
                    100
                )
              : null;
            return (
              <details key={s.id} className="rounded-card border border-brand-100 p-4">
                <summary className="flex cursor-pointer items-center justify-between">
                  <span>
                    <span className="text-sm font-medium">{s.name}</span>
                    <span className="ml-2 text-xs text-ink-400">
                      {s.email} · {s.verifiedAt ? "verified" : "not signed in yet"} ·{" "}
                      {s.plan === "gold" ? "Gold" : "Premium"}
                      {s.pilotEndsAt && new Date(s.pilotEndsAt) < new Date() ? " (pilot ended)" : ""} ·{" "}
                      {s.entitlements.evaluations_left} evals left
                      {avgAcc !== null && ` · practice ${avgAcc}% (${practices.length} sessions)`}
                    </span>
                  </span>
                  <span className="text-sm font-semibold text-brand-800">
                    {done[0] ? `Band ${done[0].report!.score_summary.holistic_band.toFixed(1)}` : "—"}
                  </span>
                </summary>

                <div className="mt-3 space-y-2 border-t border-brand-100 pt-3">
                  {roadmap?.phases[0] && (
                    <p className="text-xs text-ink-400">
                      Next step: {serviceLabel(roadmap.phases[0].service)}
                      {roadmap.phases[0].focus && ` — ${roadmap.phases[0].focus}`}
                    </p>
                  )}
                  {done.length === 0 && (
                    <p className="text-sm text-ink-400">
                      {subs.length ? "Evaluation in progress…" : "No submissions yet."}
                    </p>
                  )}
                  {done.map((sub) => (
                    <div
                      key={sub.id}
                      className="flex items-center justify-between rounded-card border border-brand-100 p-3 text-sm hover:border-brand-400"
                    >
                      <Link href={`/writing/report/${sub.id}`} className="flex-1 text-ink-600">
                        {sub.report?.escalate_to_human_review && (
                          <span className="mr-2 rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-800">
                            needs review
                          </span>
                        )}
                        {new Date(sub.createdAt).toLocaleDateString()} ·{" "}
                        {sub.prompt.slice(0, 60)}…
                      </Link>
                      <span className="flex shrink-0 items-center gap-2 pl-3">
                        {Object.entries(sub.report!.score_summary.criteria_bands).map(
                          ([k, v]) => (
                            <span key={k} className="text-xs text-ink-400">
                              {(CRITERION_LABELS[k] ?? k).split(" ")[0].slice(0, 4)} {v}
                            </span>
                          )
                        )}
                        <span className="font-semibold text-brand-800">
                          {sub.report!.score_summary.holistic_band.toFixed(1)}
                        </span>
                        <Link
                          href={`/trainer/review/${sub.id}`}
                          className="text-xs text-brand-600 hover:text-brand-800"
                        >
                          Review AI
                        </Link>
                      </span>
                    </div>
                  ))}
                </div>
              </details>
            );
          })}
        </div>
      </div>

      <div className="card mt-4">
        <h2 className="flex items-center gap-2 font-medium text-ink-900">
          <span className="material-symbols-outlined text-brand-600">forum</span>
          Platform feedback from students
        </h2>
        {feedback.length === 0 ? (
          <p className="mt-2 text-sm text-ink-400">
            Collected automatically after each report and practice session.
          </p>
        ) : (
          <div className="mt-3 space-y-2">
            {feedback.map((f, i) => {
              const who = students.find((s) => s.id === f.studentId)?.name ?? f.studentId;
              return (
                <div key={i} className="rounded-card border border-brand-100 p-3 text-sm">
                  <p className="text-xs text-ink-400">
                    {who} · {f.context} · {new Date(f.at).toLocaleDateString()} · clarity{" "}
                    {f.clarity}/5 · usefulness {f.usefulness}/5
                  </p>
                  {f.comment && <p className="mt-1 text-ink-800">{f.comment}</p>}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="card mt-4">
        <h2 className="flex items-center gap-2 font-medium text-ink-900">
          <span className="material-symbols-outlined text-brand-600">event_note</span>
          Assignments
        </h2>
        <div className="mt-3 space-y-2">
          {assignments.length === 0 && <p className="text-sm text-ink-400">None yet.</p>}
          {assignments.map((a) => (
            <div key={a.id} className="rounded-card border border-brand-100 p-4">
              <p className="text-sm text-ink-800">{a.prompt}</p>
              <p className="mt-1 text-xs text-ink-400">
                Due {a.dueDate} · {a.studentIds.length} student(s)
              </p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
