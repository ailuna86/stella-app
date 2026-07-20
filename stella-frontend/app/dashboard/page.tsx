import Link from "next/link";
import { redirect } from "next/navigation";
import { currentUser } from "@/lib/server/auth";
import { activeAssignmentFor, submissionsFor, practiceResultsFor } from "@/lib/server/store";
import { getLearningRoadmap, serviceIcon, serviceLabel } from "@/lib/server/study-plan";
import { loadWritingCoach } from "@/lib/server/goldPipeline";

export default async function Dashboard() {
  const user = await currentUser();
  if (!user) return null;
  if (user.role === "trainer") redirect("/trainer");
  if (!user.intake) redirect("/survey");
  if (user.intake.examType === "general") redirect("/general-waitlist");

  const assignment = activeAssignmentFor(user.id);
  const submissions = submissionsFor(user.id);
  const latest = submissions.find((s) => s.status === "done" && s.report);
  const practices = practiceResultsFor(user.id);
  const submittedForAssignment =
    assignment && submissions.some((s) => s.assignmentId === assignment.id);
  // v15: the "study plan" used to be a fabricated multi-week timeline read
  // from a file that never existed — see lib/server/study-plan.ts for the
  // real artifact this now reads (a 3-phase roadmap regenerated after every
  // essay, not a multi-week calendar). Gold only, same as the pipeline only
  // ever generates it for Gold sessions.
  const roadmap = latest?.sessionDir ? getLearningRoadmap(latest.sessionDir) : undefined;

  const pilotEndsAt = user.pilotEndsAt ? new Date(user.pilotEndsAt) : null;
  const pilotExpired = pilotEndsAt ? pilotEndsAt < new Date() : false;
  const pilotDaysLeft = pilotEndsAt
    ? Math.ceil((pilotEndsAt.getTime() - Date.now()) / (24 * 60 * 60 * 1000))
    : null;
  const pilotEndingSoon = pilotDaysLeft !== null && pilotDaysLeft <= 2 && !pilotExpired;

  const isGold = user.plan === "gold";
  // v10: was hardcoded to a "coming soon" stub — the Gold pipeline has
  // actually produced a real daily mission for every evaluated essay all
  // along (07e_writing_coach_output.json), it just was never read. See
  // lib/server/goldPipeline.ts for the full explanation.
  const coachMission =
    isGold && latest?.sessionDir ? loadWritingCoach(latest.sessionDir) : undefined;

  return (
    <div className="mx-auto max-w-4xl">
      <div className="flex items-center gap-3">
        <div
          className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-sm font-semibold text-white ${
            isGold ? "border-2 border-amber-400 bg-brand-600" : "bg-brand-600"
          }`}
        >
          {user.name?.[0]?.toUpperCase() ?? "S"}
        </div>
        <div>
          <h1 className="text-2xl font-semibold text-ink-900">Hi, {user.name}</h1>
          {isGold && (
            <span className="flex items-center gap-1 text-xs font-bold text-amber-600">
              <span className="material-symbols-outlined text-[14px]">stars</span>
              GOLD MEMBER
            </span>
          )}
        </div>
      </div>

      {(pilotExpired || pilotEndingSoon) && (
        <div className="card mt-4 bg-amber-50 !border-amber-200">
          <p className="text-sm text-amber-900">
            {pilotExpired
              ? "Your free pilot week has ended. You can still view past reports — subscribe to keep submitting new essays."
              : `Your free pilot week ends in ${pilotDaysLeft} day${pilotDaysLeft === 1 ? "" : "s"}. Subscribing is optional — you can keep using the free plan features that don't need new evaluations.`}
          </p>
          <Link href="/upgrade" className="btn-secondary mt-3 inline-flex">
            {pilotExpired ? "Subscribe to continue" : "See plans"}
          </Link>
        </div>
      )}

      <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat icon="trending_up" label="Current band" value={latest ? latest.report!.score_summary.holistic_band.toFixed(1) : "—"} />
        <Stat icon="flag" label="Goal band" value={user.intake.goalBand.toFixed(1)} />
        <Stat icon="edit_note" label="Practice sessions" value={String(practices.length)} />
        <Stat icon="confirmation_number" label="Evaluations left" value={String(user.entitlements.evaluations_left)} />
      </div>

      {isGold && latest?.report && (
        <div className="card relative mt-4 overflow-hidden card-gold">
          <span className="material-symbols-outlined absolute right-3 top-3 text-6xl text-amber-500/10">
            trending_up
          </span>
          <h2 className="font-medium text-ink-900">Latest assessment</h2>
          <div className="relative mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <CriterionStat
              label="Task response"
              value={latest.report.score_summary.criteria_bands.task_achievement}
            />
            <CriterionStat
              label="Coherence & cohesion"
              value={latest.report.score_summary.criteria_bands.coherence_cohesion}
            />
            <CriterionStat
              label="Lexical resource"
              value={latest.report.score_summary.criteria_bands.lexical_resource}
            />
            <CriterionStat
              label="Grammar"
              value={latest.report.score_summary.criteria_bands.grammatical_range_accuracy}
            />
          </div>
          <div className="relative mt-4 flex items-center justify-between rounded-card bg-brand-600 p-4 text-white">
            <span className="flex items-center gap-2 text-sm font-medium">
              <span className="material-symbols-outlined text-[20px]">auto_awesome</span>
              Overall band: {latest.report.score_summary.holistic_band.toFixed(1)}
            </span>
            <Link href={`/writing/report/${latest.id}`} className="text-sm font-medium underline hover:opacity-80">
              Detailed analysis
            </Link>
          </div>
        </div>
      )}

      <div className="card mt-4">
        <h2 className="flex items-center gap-2 font-medium text-ink-900">
          <span className="material-symbols-outlined text-brand-600">assignment</span>
          Homework
        </h2>
        {!assignment ? (
          <p className="mt-2 text-sm text-ink-600">
            No assignment yet — your trainer will send one. Meanwhile, daily practice is
            open below.
          </p>
        ) : submittedForAssignment ? (
          <p className="mt-2 text-sm text-mint-600">
            Submitted. {latest ? "Your report is ready below." : "Evaluation in progress…"}
          </p>
        ) : (
          <>
            <p className="mt-2 text-sm leading-relaxed text-ink-800">{assignment.prompt}</p>
            <p className="mt-1 text-xs text-ink-400">Due {assignment.dueDate}</p>
            <Link href="/writing/submit" className="btn-primary mt-3 inline-flex">
              Write my essay
            </Link>
          </>
        )}
      </div>

      <div className="card mt-4">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 font-medium text-ink-900">
            <span className="material-symbols-outlined text-brand-600">schedule</span>
            Daily practice — {user.intake.minutesPerDay} min
          </h2>
          <span className="text-xs text-ink-400">
            From your own errors · never repeats
          </span>
        </div>
        <Link href="/practice" className="btn-primary mt-3 inline-flex">
          Start session
        </Link>
      </div>

      {roadmap && roadmap.phases[0] && (
        <div className="card mt-4 shadow-soft">
          <h2 className="flex items-center gap-2 font-medium text-ink-900">
            <span className="material-symbols-outlined text-brand-600">event_note</span>
            Your next step
          </h2>
          <p className="mt-2 text-sm text-ink-600">Based on your latest essay:</p>
          <div className="mt-2 flex items-start gap-3 rounded-card bg-brand-50 p-3">
            <span className="material-symbols-outlined mt-0.5 text-brand-600">
              {serviceIcon(roadmap.phases[0].service)}
            </span>
            <div>
              <p className="text-sm font-medium text-brand-800">
                {serviceLabel(roadmap.phases[0].service)} — {roadmap.phases[0].focus}
              </p>
              <p className="mt-1 text-xs italic text-ink-600">{roadmap.phases[0].goal}</p>
            </div>
          </div>
          <Link
            href="/study-plan"
            className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-brand-600 hover:text-brand-800"
          >
            View full roadmap <span className="material-symbols-outlined text-[16px]">arrow_forward</span>
          </Link>
        </div>
      )}

      {isGold && (
        <div className="card mt-4 bg-brand-800 text-white">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-white/10">
              <span className="material-symbols-outlined text-amber-400">edit_note</span>
            </div>
            <h2 className="font-medium">{coachMission ? coachMission.homeCard.title : "Writing coach"}</h2>
          </div>
          {coachMission ? (
            <>
              <p className="mt-2 text-sm text-brand-100">{coachMission.homeCard.message}</p>
              <div className="mt-3 flex items-center gap-3 text-xs text-brand-200">
                <span>Weekly focus: {coachMission.homeCard.weeklyFocus}</span>
                <span>·</span>
                <span>{coachMission.homeCard.timeboxMinutes} min</span>
              </div>
              <Link
                href="/writing-coach"
                className="btn-secondary mt-3 inline-flex !border-white !bg-white !text-brand-800 hover:!bg-brand-50"
              >
                {coachMission.homeCard.buttonText}
              </Link>
            </>
          ) : (
            <p className="mt-2 text-sm text-brand-100">
              Your daily coaching mission appears here after your first evaluated essay.
            </p>
          )}
        </div>
      )}

      {!isGold && latest && (
        <div className="card mt-4 shadow-soft">
          <h2 className="flex items-center gap-2 font-medium text-ink-900">
            <span className="material-symbols-outlined text-brand-600">history_edu</span>
            Latest evaluation
          </h2>
          <p className="mt-1 text-sm text-ink-600">
            Band {latest.report!.score_summary.holistic_band.toFixed(1)} ·{" "}
            {new Date(latest.createdAt).toLocaleDateString()}
          </p>
          <Link
            href={`/writing/report/${latest.id}`}
            className="btn-secondary mt-3 inline-flex"
          >
            Open report
          </Link>
        </div>
      )}
    </div>
  );
}

function Stat({ icon, label, value }: { icon: string; label: string; value: string }) {
  return (
    <div className="rounded-card bg-brand-50 p-4 text-center">
      <span className="material-symbols-outlined text-[18px] text-brand-400">{icon}</span>
      <div className="text-xl font-semibold text-brand-800">{value}</div>
      <div className="text-xs text-brand-600">{label}</div>
    </div>
  );
}

function CriterionStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-card border border-brand-100 p-3 text-center">
      <div className="text-lg font-semibold text-brand-800">{value.toFixed(1)}</div>
      <div className="text-[11px] uppercase tracking-wide text-ink-400">{label}</div>
    </div>
  );
}
