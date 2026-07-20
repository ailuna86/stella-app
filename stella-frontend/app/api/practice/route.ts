import { NextResponse } from "next/server";
import { currentUser } from "@/lib/server/auth";
import { addSeen, getSeen, savePracticeResult, submissionsFor } from "@/lib/server/store";
import { buildPracticeSession } from "@/lib/server/pipeline";
import { exercisesForMinutes } from "@/lib/types";

export async function GET() {
  const user = await currentUser();
  if (!user) return NextResponse.json({ ok: false }, { status: 401 });
  if (!user.entitlements.can_practice)
    return NextResponse.json({ ok: false, error: "Practice not on your plan." }, { status: 403 });

  const latest = submissionsFor(user.id).find((s) => s.status === "done" && s.report);
  const weakFamilies =
    latest?.report?.focus_area_feedback?.map((fa) => fa.skill_tag) ?? [];

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
  return NextResponse.json({ ok: true });
}
