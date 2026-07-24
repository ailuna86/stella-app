import { redirect } from "next/navigation";
import Link from "next/link";
import { currentUser } from "@/lib/server/auth";
import {
  getUsers,
  getAssignments,
  submissionsFor,
  practiceResultsFor,
  missionResultsFor,
  allPlatformFeedback,
  getPrompts,
  pendingUpgradeRequests,
  getUserById,
  latestLearnerProfileRefreshAttempt,
} from "@/lib/server/store";
import { getLearningRoadmap, serviceLabel } from "@/lib/server/study-plan";
import { loadVocabCoachMasterySummary } from "@/lib/server/goldPipeline";
import { CRITERION_LABELS } from "@/lib/types";
import AssignmentForm from "@/components/AssignmentForm";
import AddStudentForm from "@/components/AddStudentForm";
import AddTrainerForm from "@/components/AddTrainerForm";
import PromptReviewList from "@/components/PromptReviewList";
import UpgradeRequestsList from "@/components/UpgradeRequestsList";

// v20: essay-submission timer — trainer-facing "how much time did this
// student actually spend" readout next to each submission row (e.g.
// "Exam · 38m"). Rounds to the nearest minute since second-level precision
// isn't useful here; always shows at least "0m" rather than blank so a
// very fast (or auto-submitted-at-zero) attempt is still visible as a
// real, if tiny, number rather than looking like missing data.
function formatMinutes(totalSeconds: number): string {
  const minutes = Math.max(0, Math.round(totalSeconds / 60));
  return `${minutes}m`;
}

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

  // v19: group-wide report + lesson-plan suggestions — requested directly:
  // the console previously only showed raw error-family counts, nothing
  // synthesized across practice, writing coach, or vocabulary coach, and no
  // "what to focus on next lesson" summary. Every number below is a real
  // aggregate of what students' own sessions already recorded (practice_results,
  // mission_results, each student's vocab ledger via loadVocabCoachMasterySummary)
  // — nothing here is inferred or LLM-generated, so it stays traceable back to
  // real activity rather than reading as a black-box recommendation.
  const oneWeekAgoMs = Date.now() - 7 * 24 * 60 * 60 * 1000;
  let weeklyPracticeCorrect = 0;
  let weeklyPracticeTotal = 0;
  let weeklySessionCount = 0;
  let weeklyPass = 0;
  let weeklyPartial = 0;
  let weeklyFail = 0;
  const failedMissionTitles = new Map<string, number>();
  let groupDueForReview = 0;
  let groupMastered = 0;
  const overdueWordCounts = new Map<string, number>();

  for (const s of students) {
    for (const r of practiceResultsFor(s.id)) {
      if (new Date(r.at).getTime() < oneWeekAgoMs) continue;
      weeklySessionCount += 1;
      weeklyPracticeCorrect += r.correct ?? 0;
      weeklyPracticeTotal += r.total ?? 0;
    }
    for (const m of missionResultsFor(s.id)) {
      if (new Date(m.at).getTime() < oneWeekAgoMs) continue;
      if (m.outcome === "pass") weeklyPass += 1;
      else if (m.outcome === "partial_pass") weeklyPartial += 1;
      else if (m.outcome === "fail") {
        weeklyFail += 1;
        if (m.missionTitle) failedMissionTitles.set(m.missionTitle, (failedMissionTitles.get(m.missionTitle) ?? 0) + 1);
      }
    }
    const vocab = loadVocabCoachMasterySummary(s.id);
    if (vocab) {
      groupDueForReview += vocab.dueForReview.length;
      groupMastered += vocab.boxCounts.mastered;
      for (const d of vocab.dueForReview) {
        overdueWordCounts.set(d.phrase, (overdueWordCounts.get(d.phrase) ?? 0) + 1);
      }
    }
  }

  const weeklyMissionsTotal = weeklyPass + weeklyPartial + weeklyFail;
  const weeklyPracticeAccuracy = weeklyPracticeTotal > 0 ? Math.round((weeklyPracticeCorrect / weeklyPracticeTotal) * 100) : null;
  const topFailedMission = [...failedMissionTitles.entries()].sort((a, b) => b[1] - a[1])[0];
  const topOverdueWords = [...overdueWordCounts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3);

  // Deterministic, rule-based suggestions — each one only fires when its own
  // real number crosses a threshold, and states that number so a trainer can
  // check it rather than take it on faith.
  const focusSuggestions: string[] = [];
  if (commonMistakes[0] && commonMistakes[0][1] >= 3) {
    focusSuggestions.push(
      `${commonMistakes[0][0].replace(/_/g, " ").toLowerCase()} is the most common error across the group (${commonMistakes[0][1]} occurrences) — worth a short group mini-lesson.`
    );
  }
  if (weeklyMissionsTotal >= 3 && weeklyFail / weeklyMissionsTotal > 0.4) {
    focusSuggestions.push(
      `${Math.round((weeklyFail / weeklyMissionsTotal) * 100)}% of Writing Coach missions this week came back "not yet"${
        topFailedMission ? ` — most often on "${topFailedMission[0]}" (${topFailedMission[1]} students)` : ""
      }.`
    );
  }
  if (groupDueForReview >= 5) {
    focusSuggestions.push(
      `${groupDueForReview} vocabulary items are due for review across the group${
        topOverdueWords.length ? ` — most shared: ${topOverdueWords.map(([w, c]) => `"${w}" (${c})`).join(", ")}` : ""
      }.`
    );
  }
  if (weeklyPracticeAccuracy !== null && weeklyPracticeAccuracy < 70 && weeklySessionCount >= 3) {
    focusSuggestions.push(
      `Group practice accuracy this week is ${weeklyPracticeAccuracy}% across ${weeklySessionCount} session${weeklySessionCount === 1 ? "" : "s"} — below the 70% mark, worth extra repetition time.`
    );
  }

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

      {/* v19: group-wide report + lesson-plan suggestions, requested directly
          ("not only errors, but report on the whole group, suggestions for
          the lesson plan, what to focus on"). Common mistakes above already
          covered essay errors; this adds practice, Writing Coach, and
          Vocabulary Coach trends across the whole cohort, plus a short,
          rule-based "what to focus on" list derived from those same numbers. */}
      <div className="card mt-4">
        <h2 className="flex items-center gap-2 font-medium text-ink-900">
          <span className="material-symbols-outlined text-brand-600">groups</span>
          Group focus this week
        </h2>
        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-card bg-brand-50 p-3 text-center">
            <div className="text-lg font-semibold text-brand-800">
              {weeklyPracticeAccuracy !== null ? `${weeklyPracticeAccuracy}%` : "—"}
            </div>
            <div className="text-xs text-brand-600">Practice accuracy ({weeklySessionCount} sessions)</div>
          </div>
          <div className="rounded-card bg-brand-50 p-3 text-center">
            <div className="text-lg font-semibold text-brand-800">
              {weeklyMissionsTotal > 0 ? `${weeklyPass + weeklyPartial}/${weeklyMissionsTotal}` : "—"}
            </div>
            <div className="text-xs text-brand-600">Writing Coach pass/partial</div>
          </div>
          <div className="rounded-card bg-brand-50 p-3 text-center">
            <div className="text-lg font-semibold text-brand-800">{groupDueForReview}</div>
            <div className="text-xs text-brand-600">Vocab items due for review</div>
          </div>
          <div className="rounded-card bg-brand-50 p-3 text-center">
            <div className="text-lg font-semibold text-brand-800">{groupMastered}</div>
            <div className="text-xs text-brand-600">Words mastered so far</div>
          </div>
        </div>

        {focusSuggestions.length > 0 ? (
          <div className="mt-4 space-y-2">
            <p className="text-xs font-medium uppercase tracking-wide text-ink-400">Suggested focus</p>
            {focusSuggestions.map((s, i) => (
              <div key={i} className="flex gap-2 rounded-card border border-brand-100 p-3 text-sm text-ink-800">
                <span className="text-brand-500">→</span> <span>{s}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="mt-3 text-sm text-ink-400">
            No strong group-wide pattern yet this week — check back after more sessions.
          </p>
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
            // v22: getLearningRoadmap now also takes studentId -- see
            // study-plan.ts's comment (Defect 1 fix).
            const roadmap =
              s.plan === "gold" && done[0]?.sessionDir ? getLearningRoadmap(s.id, done[0].sessionDir) : undefined;
            const refreshAttempt = latestLearnerProfileRefreshAttempt(s.id);
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
                    {/* v22: refresh-failure badge -- Defect 2 fix. refreshLearnerProfile()
                        never rejects by design (fire-and-forget), so this badge is what
                        actually makes a failing continuous-loop refresh visible to a
                        trainer instead of it being silently swallowed forever. Same small-
                        badge style as the existing "needs review" / "Exam · 38m" badges
                        below. Title carries the real error message for a quick hover-check. */}
                    {refreshAttempt?.status === "failure" && (
                      <span
                        className="ml-2 rounded-full bg-red-100 px-2 py-0.5 text-xs text-red-800"
                        title={refreshAttempt.errorMessage ?? undefined}
                      >
                        profile refresh failing
                      </span>
                    )}
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
                        {sub.mode && (
                          <span
                            className={`mr-2 rounded-full px-2 py-0.5 text-xs ${
                              sub.mode === "exam"
                                ? "bg-brand-100 text-brand-800"
                                : "bg-brand-50 text-ink-600"
                            }`}
                          >
                            {sub.mode === "exam" ? "Exam" : "Practice"}
                            {typeof sub.timeSpentSeconds === "number" &&
                              ` · ${formatMinutes(sub.timeSpentSeconds)}`}
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
