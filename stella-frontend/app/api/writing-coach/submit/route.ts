import { NextResponse } from "next/server";
import { currentUser } from "@/lib/server/auth";
import { submissionsFor } from "@/lib/server/store";
import { runMissionGrading } from "@/lib/server/goldPipeline";

// v11: grades a Writing Coach mission response. Deliberately does NOT touch
// evaluations_left or spawn the full Gold orchestrator — mission grading is
// unlimited and near-instant, unlike essay evaluation, which is capped and
// slow. See goldPipeline.ts (runMissionGrading) for why this calls
// writing_coach_v1_2_17_freeze_candidate.py directly instead.
export async function POST(req: Request) {
  const user = await currentUser();
  if (!user) return NextResponse.json({ ok: false }, { status: 401 });
  if (user.role === "trainer") return NextResponse.json({ ok: false }, { status: 403 });

  const { text } = (await req.json()) as { text?: string };
  if (!text || !text.trim()) {
    return NextResponse.json({ ok: false, error: "Write your response before submitting." }, { status: 400 });
  }

  const latest = submissionsFor(user.id).find((s) => s.status === "done" && s.sessionDir);
  if (!latest?.sessionDir) {
    return NextResponse.json({ ok: false, error: "No mission available yet." }, { status: 404 });
  }

  try {
    const result = await runMissionGrading(latest.sessionDir, text);
    return NextResponse.json({ ok: true, result });
  } catch (e) {
    console.error("[ST.ELLA] Mission grading failed:", e);
    return NextResponse.json(
      { ok: false, error: "Grading failed — please try again." },
      { status: 500 }
    );
  }
}
