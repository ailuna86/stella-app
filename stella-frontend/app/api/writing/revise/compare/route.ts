import { NextResponse } from "next/server";
import { currentUser } from "@/lib/server/auth";
import { getSubmission } from "@/lib/server/store";
import { runRevisionComparison } from "@/lib/server/goldPipeline";
import { normalizeParagraphBreaks } from "@/lib/server/text";

// v12: the AI comparison stage — generates a model rewrite of the student's
// ORIGINAL essay (never their revision, per the ER spec) and returns a
// paragraph-by-paragraph Original / Your revision / AI model comparison.
// Deliberately separate from /api/evaluate: this doesn't cost an evaluation
// credit and doesn't re-score anything, it's a comparison/learning artifact.
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

  try {
    const result = await runRevisionComparison(sub.sessionDir, {
      originalText: normalizeParagraphBreaks(sub.essay),
      revisedText: normalizeParagraphBreaks(revisedText),
      prompt: sub.prompt,
    });
    return NextResponse.json({ ok: true, result });
  } catch (e) {
    console.error("[ST.ELLA] Revision comparison failed:", e);
    return NextResponse.json(
      { ok: false, error: "Comparison failed — please try again." },
      { status: 500 }
    );
  }
}
