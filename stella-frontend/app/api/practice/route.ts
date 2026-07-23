import { NextResponse } from "next/server";
import { currentUser } from "@/lib/server/auth";
import { addSeen, getSeen, savePracticeResult, submissionsFor } from "@/lib/server/store";
import { buildPracticeSession, expandWeakFamilies } from "@/lib/server/pipeline";
import { exercisesForMinutes } from "@/lib/types";
import { refreshLearnerProfile } from "@/lib/server/goldPipeline";

export async function GET() {
  const user = await currentUser();
  if (!user) return NextResponse.json({ ok: false }, { status: 401 });
  if (!user.entitlements.can_practice)
    return NextResponse.json({ ok: false, error: "Practice not on your plan." }, { status: 403 });

  const latest = submissionsFor(user.id).find((s) => s.status === "done" && s.report);
  // v24 (2026-07-23): was `.map(fa => fa.skill_tag)` -- see
  // expandWeakFamilies()'s doc comment in pipeline.ts for why that was a real,
  // pre-existing no-op bug (skill_tag's vocabulary never matches the bank's
  // family names). This also means Evaluator's and LRET/Vocab-Coach's focus
  // areas (task #8) now actually reach exercise selection, not just
  // ErrorMap's, for the first time.
  const weakFamilies = expandWeakFamilies(latest?.report?.focus_area_feedback ?? []);

  const count = exercisesForMinutes(user.intake?.minutesPerDay ?? 10);
  const exercises = buildPracticeSession({
    weakFamilies,
    seenIds: getSeen(user.id),
    count,
  }).map((e) => ({
    exercise_id: e.exercise_id,
    family_label: e.family_label,
    prompt: e.prompt,
    choices: e.choices,
    answer: e.answer,
    explanation: e.explanation,
  }));

  return NextResponse.json({ ok: true, exercises, minutes: user.intake?.minutesPerDay ?? 10 });
}

export async function POST(req: Request) {
  const user = await currentUser();
  if (!user) return NextResponse.json({ ok: false }, { status: 401 });

  const { exerciseIds, correct, total } = (await req.json()) as {
    exerciseIds: string[];
    correct: number;
    total: number;
  };
  addSeen(user.id, exerciseIds ?? []);
  savePracticeResult(user.id, {
    at: new Date().toISOString(),
    correct,
    total,
    exerciseIds,
  });
  // v21 (2026-07-23): continuous-loop refresh -- fire-and-forget. This is a
  // background enrichment step (refreshing the LIE learner profile/roadmap
  // with this student's real practice history) the student isn't waiting to
  // see; refreshLearnerProfile() itself never rejects (every failure path is
  // caught and logged internally), so this can't crash or delay this
  // response even if the refresh fails.
  void refreshLearnerProfile(user.id);
  return NextResponse.json({ ok: true });
}
