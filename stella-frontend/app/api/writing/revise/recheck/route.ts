import { NextResponse } from "next/server";
import { currentUser } from "@/lib/server/auth";
import { getSubmission, getUserById } from "@/lib/server/store";
import { runRevisionScopedRecheck, refreshLearnerProfile } from "@/lib/server/goldPipeline";
import { normalizeParagraphBreaks } from "@/lib/server/text";

// v20: the scoped re-check stage — Session_Flow_and_Vocab_Expansion_Spec_v1
// §1. Sibling to /api/writing/revise/compare (deliberately NOT folded into
// that route — see the "Design choice" comment above runRevisionScopedRecheck
// in goldPipeline.ts for why). Detector-only, scoped to the sentences the
// student actually rewrote — no holistic re-band, no Task Response /
// Coherence claim. Same non-credit gating as /compare: this doesn't touch
// evaluations_left, it's a learning artifact, not a re-evaluation.
export async function POST(req: Request) {
  const user = await currentUser();
  if (!user) return NextResponse.json({ ok: false }, { status: 401 });

  const { submissionId, revisedText } = (await req.json()) as {
    submissionId?: string;
    revisedText?: string;
  };
  if (!submissionId || !revisedText?.trim()) {
    return NextResponse.json({ ok: false, error: "Write your revision first." }, { status: 400 });
  }

  const sub = getSubmission(submissionId);
  const allowed = sub && (sub.studentId === user.id || user.role === "trainer");
  if (!sub || !allowed || !sub.sessionDir) {
    return NextResponse.json({ ok: false, error: "Essay not found." }, { status: 404 });
  }
  // v27 (2026-07-23): Essay Revision is Gold-only -- see /api/writing/revise/compare's
  // matching comment (same reasoning: gate on the submission owner's plan).
  const owner = getUserById(sub.studentId);
  if (!owner || owner.plan !== "gold") {
    return NextResponse.json({ ok: false, error: "Essay Revision is a Gold-plan feature." }, { status: 403 });
  }

  try {
    const result = await runRevisionScopedRecheck(sub.sessionDir, {
      originalText: normalizeParagraphBreaks(sub.essay),
      revisedText: normalizeParagraphBreaks(revisedText),
      prompt: sub.prompt,
    });
    // v21 (2026-07-23): continuous-loop refresh — fire-and-forget, same
    // reasoning as /api/practice. This is what makes Essay Revision a real
    // LIE input (gold_engagement_history_aggregator_v1_0.py's
    // essay_revision_history reads this exact revision_scoped_rechecks/
    // folder, including net-fixed-sentence counts).
    void refreshLearnerProfile(sub.studentId);
    return NextResponse.json({ ok: true, result });
  } catch (e) {
    console.error("[ST.ELLA] Revision scoped recheck failed:", e);
    return NextResponse.json(
      { ok: false, error: "Sentence check failed — please try again." },
      { status: 500 }
    );
  }
}
