import { NextResponse } from "next/server";
import fs from "fs";
import path from "path";
import { currentUser } from "@/lib/server/auth";
import { submissionsFor } from "@/lib/server/store";

// Temporary diagnostic-only route (v1, remove once real QA tooling exists).
// Returns the raw pipeline artifact JSON for the signed-in user's most
// recent *successfully completed* submission (status "done" + sessionDir
// set — a failed run never gets a sessionDir written, see
// app/api/evaluate/route.ts's catch block). Self-serve, no admin flag
// needed: a student can only ever read their own most recent session,
// same data already visible to them via the report page, just in raw form
// instead of the mapped/curated FeedbackReport shape.
const ARTIFACTS: Record<string, string> = {
  submission: "00_submission.json",
  detector: "01_detector_output.json",
  detector_for_scorer: "01d_detector_for_scorer.json",
  errormap: "01b_errormap_v3.json",
  evaluator: "07_evaluator_output.json",
  evaluator_rubric_bridge: "01e_detector_for_scorer_rubric_enriched.json",
  scorer: "02a_premium_scorer_v1_4_1_output.json",
  verifier: "02b_premium_verifier_v1_4_3_output.json",
  adjudicator: "02c_final_adjudicated_v1_2.json",
  score_contract: "02d_final_score_contract.json",
  progress_tracker: "02e_gold_progress_tracker.json",
  progress_tracker_persist: "02f_gold_progress_tracker_persisted.json",
  priority_input: "03a_priority_input_v1_4_8.json",
  priority: "03_pe_output.json",
  priority_normalized: "03b_priority_normalized_v1_4_3.json",
  directive: "04_directive_v2.json",
  feedback_report: "06_feedback_report_v6c.json",
  lret_session: "07d_lret_session.json",
  writing_coach: "07e_writing_coach_output.json",
  practice_session: "07f_gold_practice_session.json",
  learner_profile: "08_gold_learner_profile.json",
  persisted_profile: "08a_gold_persisted_profile.json",
  qa_report: "QA_gold_report.json",
  manifest: "gold_run_manifest.json",
};

export async function GET() {
  const user = await currentUser();
  if (!user) return NextResponse.json({ ok: false, error: "Not signed in." }, { status: 401 });

  const submissions = submissionsFor(user.id);
  const latest = submissions.find((s) => s.status === "done" && s.sessionDir);
  if (!latest) {
    return NextResponse.json({ ok: false, error: "No completed session with a sessionDir found." }, { status: 404 });
  }

  const out: Record<string, unknown> = {
    _submission_id: latest.id,
    _session_dir: latest.sessionDir,
    _created_at: latest.createdAt,
  };
  for (const [key, filename] of Object.entries(ARTIFACTS)) {
    const p = path.join(latest.sessionDir!, filename);
    if (fs.existsSync(p)) {
      try {
        out[key] = JSON.parse(fs.readFileSync(p, "utf8"));
      } catch (e) {
        out[key] = { _read_error: e instanceof Error ? e.message : String(e) };
      }
    } else {
      out[key] = { _missing: filename };
    }
  }
  return NextResponse.json(out);
}
