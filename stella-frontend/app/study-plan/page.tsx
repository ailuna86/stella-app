import Link from "next/link";
import { redirect } from "next/navigation";
import { currentUser } from "@/lib/server/auth";
import { submissionsFor } from "@/lib/server/store";
import { getLearningRoadmap, serviceIcon, serviceLabel } from "@/lib/server/study-plan";

// v8: new — surfaces the pipeline's roadmap output, computed after every
// essay evaluation but never shown anywhere before.
// v15: complete rewrite. This used to render a fabricated multi-week
// calendar (weeks[], day-by-day dots, a goal-band "trophy" at the end) built
// against a file — `{studentId}_study_plan.json` — that never actually
// existed anywhere in the real pipeline, on either tier. That's why this
// page always showed the "appears after your first evaluated essay" empty
// state even with essays evaluated: it was looking for the wrong file.
//
// The real artifact (08c_gold_learning_roadmap.json, Gold only, written
// fresh after every essay by gold_lie_profile_builder_standalone_v1_4_3.py)
// is a much simpler 3-phase "what to do next" sequence: phase 1's focus is
// the actual highest-priority weakness from the latest essay, phases 2-3
// are a fixed practice → essay-revision follow-through. This page now
// renders that real shape instead of an invented calendar.
export default async function StudyPlanPage() {
  const user = await currentUser();
  if (!user) return null;
  if (user.role === "trainer") redirect("/trainer");

  const latest = submissionsFor(user.id).find((s) => s.status === "done" && s.sessionDir);
  const roadmap = latest?.sessionDir ? getLearningRoadmap(latest.sessionDir) : undefined;
  const isGold = user.plan === "gold";

  return (
    <div className="mx-auto max-w-2xl py-6">
      <h1 className="text-2xl font-semibold text-ink-900">Your roadmap</h1>
      <p className="mt-2 text-sm leading-relaxed text-ink-600">
        After every evaluated essay, we recompute a short sequence of what to do next —
        starting with your single highest-priority weakness, then a controlled practice pass,
        then applying it in a real revision. It updates each time you submit a new essay,
        rather than following a fixed weekly calendar.
      </p>

      {!isGold ? (
        <p className="mt-4 rounded-card border border-brand-100 bg-brand-50 p-4 text-sm text-brand-800">
          The roadmap is a Gold-tier feature.{" "}
          <Link href="/upgrade" className="font-medium underline">
            See plans
          </Link>
          .
        </p>
      ) : !roadmap ? (
        <p className="mt-4 text-sm text-ink-600">
          Your roadmap appears here after your first evaluated essay.
        </p>
      ) : (
        <div className="relative mt-8">
          <div className="absolute bottom-6 left-5 top-2 hidden w-px bg-brand-100 sm:block" />
          <div className="space-y-6">
            {roadmap.phases.map((p, i) => {
              const isCurrent = i === 0;
              const href =
                p.service === "writing_coach"
                  ? "/writing-coach"
                  : p.service === "practice"
                  ? "/practice"
                  : p.service === "essay_revision" && latest
                  ? `/writing/revise/${latest.id}`
                  : "/dashboard";
              return (
                <div key={p.phase} className="relative flex gap-4 sm:pl-0">
                  <div
                    className={`z-10 hidden h-10 w-10 shrink-0 items-center justify-center rounded-full sm:flex ${
                      isCurrent
                        ? "bg-brand-600 text-white"
                        : "border-2 border-brand-100 bg-white text-ink-400"
                    }`}
                  >
                    <span className="material-symbols-outlined text-[20px]">
                      {serviceIcon(p.service)}
                    </span>
                  </div>
                  <div
                    className={`card flex-1 ${isCurrent ? "border-2 !border-brand-400 shadow-soft" : "opacity-70"}`}
                  >
                    <span
                      className={`inline-block rounded-full px-2.5 py-0.5 text-[11px] font-medium uppercase tracking-wide ${
                        isCurrent ? "bg-brand-600 text-white" : "bg-brand-50 text-ink-400"
                      }`}
                    >
                      {isCurrent ? "Do this next" : `Phase ${p.phase}`}
                    </span>
                    <h2 className="mt-2 font-medium text-ink-800">
                      {serviceLabel(p.service)}
                      {p.focus && ` — ${p.focus}`}
                    </h2>
                    <p className="mt-1 text-sm text-ink-600">{p.goal}</p>
                    {isCurrent && (
                      <Link href={href} className="btn-primary mt-3 inline-flex">
                        {p.service === "writing_coach"
                          ? "Go to Writing Coach"
                          : p.service === "practice"
                          ? "Start practice"
                          : "Revise your essay"}
                      </Link>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {user.intake?.goalBand && (
        <div className="mt-8 flex items-center gap-3 rounded-card bg-brand-50 p-4">
          <span className="material-symbols-outlined text-amber-500">emoji_events</span>
          <div>
            <p className="font-medium text-brand-800">Target: Band {user.intake.goalBand.toFixed(1)}</p>
            {user.intake.examDate && (
              <p className="text-sm text-ink-400">Exam date: {user.intake.examDate}</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
