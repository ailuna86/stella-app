import Link from "next/link";
import { redirect } from "next/navigation";
import { currentUser } from "@/lib/server/auth";
import { submissionsFor } from "@/lib/server/store";
import { loadWritingCoach } from "@/lib/server/goldPipeline";
import MissionSubmitForm from "@/components/MissionSubmitForm";

// v10: new — the Gold pipeline generates a real, personalized daily coaching
// mission after every evaluated essay (07e_writing_coach_output.json). This
// screen was missing entirely; the dashboard used to show a static "coming
// soon" card instead of linking anywhere. See lib/server/goldPipeline.ts for
// how the mission data is read.
export default async function WritingCoachPage() {
  const user = await currentUser();
  if (!user) return null;
  if (user.role === "trainer") redirect("/trainer");

  const latest = submissionsFor(user.id).find((s) => s.status === "done" && s.sessionDir);
  const coach = latest?.sessionDir ? loadWritingCoach(latest.sessionDir) : undefined;

  if (!latest) {
    return (
      <div className="mx-auto max-w-xl py-10 text-center">
        <h1 className="text-2xl font-semibold">Writing coach</h1>
        <p className="mt-2 text-sm text-ink-600">
          Your daily coaching mission is built from your essays — submit your first one to
          unlock it.
        </p>
        <Link href="/writing/submit" className="btn-primary mt-4 inline-flex">
          Write my essay
        </Link>
      </div>
    );
  }

  if (!coach) {
    return (
      <div className="mx-auto max-w-xl py-10 text-center">
        <h1 className="text-2xl font-semibold">Writing coach</h1>
        <p className="mt-2 text-sm text-ink-600">
          No mission is available for your latest essay yet — check back after your next
          submission, or ask your trainer if this persists.
        </p>
      </div>
    );
  }

  const { homeCard: card, mission } = coach;

  return (
    <div className="mx-auto max-w-5xl py-6">
      <h1 className="text-2xl font-semibold">Writing coach</h1>
      <p className="mt-2 max-w-2xl text-sm leading-relaxed text-ink-600">
        Writing coach is a short daily exercise — usually 10 minutes — that trains one
        specific skill at a time, chosen from what your actual essays show you need most.
        It's not another practice quiz: each mission asks you to produce real sentences,
        which is closer to what the exam actually tests than picking a multiple-choice
        answer. Complete a few missions on the same skill and it moves on to the next
        thing holding your score back.
      </p>
      {card.planSummary && <p className="mt-2 text-sm text-ink-600">{card.planSummary}</p>}

      {/* Header card — matches the Stitch violet mission header */}
      <div className="mt-4 flex flex-col gap-6 rounded-card bg-brand-600 p-6 text-white shadow-soft md:flex-row md:items-center md:justify-between">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            {card.weeklyFocus && (
              <span className="rounded-full bg-white/20 px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide">
                Weekly focus
              </span>
            )}
            <span className="flex items-center gap-1 text-xs text-white/80">{card.timeboxMinutes} min</span>
          </div>
          <h2 className="text-2xl font-semibold">{mission.title || card.missionTitle}</h2>
          {card.weeklyFocus && <p className="text-sm text-white/80">Focus skill: {card.weeklyFocus}</p>}
          {card.message && <p className="text-sm text-white/80">{card.message}</p>}
        </div>
        {card.streakGoal && (
          <div className="min-w-[220px] rounded-card border border-white/20 bg-white/10 p-4">
            <p className="mb-1 text-xs text-white/80">Streak goal</p>
            <p className="font-medium">{card.streakGoal}</p>
          </div>
        )}
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-3 lg:items-start">
        {/* Left: challenge + stimulus */}
        <div className="space-y-4 lg:col-span-2">
          <div className="card shadow-soft">
            <div className="flex items-start justify-between gap-3">
              <h3 className="font-medium text-brand-800">The challenge</h3>
              <span className="shrink-0 rounded-full bg-brand-50 px-3 py-1 text-xs capitalize text-brand-800">
                {mission.difficulty}
              </span>
            </div>
            <p className="mt-2 text-sm leading-relaxed text-ink-800">{mission.studentGoal}</p>

            {mission.steps.length > 0 && (
              <div className="mt-4">
                <p className="text-xs font-medium uppercase tracking-wide text-ink-400">Mission steps</p>
                <ol className="mt-2 space-y-2">
                  {mission.steps.map((s, i) => (
                    <li key={i} className="flex items-start gap-3 text-sm text-ink-800">
                      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-50 text-xs font-bold text-brand-600">
                        {i + 1}
                      </span>
                      <span>{s}</span>
                    </li>
                  ))}
                </ol>
              </div>
            )}

            {mission.modelExample && (
              <details className="mt-4 rounded-card border border-amber-200 bg-amber-50 p-3">
                <summary className="cursor-pointer text-xs font-medium text-amber-800">
                  Stuck? See a worked example (try it yourself first — this won't fit your items
                  exactly)
                </summary>
                <p className="mt-2 text-sm italic text-ink-700">{mission.modelExample}</p>
              </details>
            )}
          </div>

          {mission.stimulusItems.length > 0 && (
            <div className="grid gap-3 sm:grid-cols-2">
              {mission.stimulusItems.map((it) => (
                <div key={it.itemNumber} className="card shadow-soft">
                  <span className="text-xs font-bold text-brand-600">Stimulus #{it.itemNumber}</span>
                  <p className="mt-1.5 text-sm italic text-ink-700">"{it.roughInput}"</p>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right: response + result */}
        <div className="lg:col-span-1">
          <MissionSubmitForm requiredItems={mission.requiredItems} successChecklist={mission.successChecklist} />
        </div>
      </div>

      <Link href="/dashboard" className="btn-secondary mt-6 inline-flex">
        Back to dashboard
      </Link>
    </div>
  );
}
