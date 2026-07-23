import { NextResponse } from "next/server";
import { currentUser } from "@/lib/server/auth";
import { submissionsFor, saveMissionResult } from "@/lib/server/store";
import { runMissionGrading, loadWritingCoach, refreshLearnerProfile } from "@/lib/server/goldPipeline";

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
    // v17: index this attempt for the daily digest (Pipeline_Frontend_Spec_v2 §4).
    // Only real graded attempts count — invalid/empty responses aren't a completed
    // mission, so they're excluded rather than inflating "missions completed today".
    if (result.outcome === "pass" || result.outcome === "partial_pass" || result.outcome === "fail") {
      // v21 (2026-07-23): missionTitle was never actually passed here before
      // (saveMissionResult's 3rd field was always omitted), so every
      // mission_results row's mission_title has been null since the column
      // was introduced (v17) — confirmed directly against a real attempt
      // file/mission_results row before writing this. The mission's own
      // title lives in THIS essay's 07e_writing_coach_output.json (the
      // mission itself, unaffected by grading), read via loadWritingCoach —
      // read here, before the title is needed for the new engagement-history
      // aggregation's "most commonly failed mission" field (see
      // gold_engagement_history_aggregator_v1_0.py) to actually have
      // anything to report.
      const missionTitle = loadWritingCoach(latest.sessionDir)?.mission.title || null;
      saveMissionResult(user.id, { at: new Date().toISOString(), outcome: result.outcome, missionTitle });
    }
    // v21: continuous-loop refresh — fire-and-forget, same reasoning as
    // /api/practice (see that route's comment). refreshLearnerProfile()
    // never rejects, so this can't affect the response below even on failure.
    void refreshLearnerProfile(user.id);
    return NextResponse.json({ ok: true, result });
  } catch (e) {
    console.error("[ST.ELLA] Mission grading failed:", e);
    return NextResponse.json(
      { ok: false, error: "Grading failed — please try again." },
      { status: 500 }
    );
  }
}
