import { NextResponse } from "next/server";
import { currentUser } from "@/lib/server/auth";
import { saveSubmission, saveUser } from "@/lib/server/store";
import { runEvaluation } from "@/lib/server/pipeline";
import { normalizeParagraphBreaks } from "@/lib/server/text";
import type { SubmissionRecord } from "@/lib/types";

// Gold-tier runs measured 480-535s end to end in real stress tests; premium
// runs are faster. 900s covers both with headroom.
export const maxDuration = 900;

export async function POST(req: Request) {
  const user = await currentUser();
  if (!user) return NextResponse.json({ ok: false, error: "Not signed in." }, { status: 401 });
  if (!user.consentAt)
    return NextResponse.json({ ok: false, error: "Please accept the AI-processing notice first." }, { status: 412 });
  if (user.pilotEndsAt && new Date(user.pilotEndsAt) < new Date()) {
    return NextResponse.json(
      { ok: false, error: "Your free pilot week has ended. Visit the upgrade page to keep submitting essays.", pilotEnded: true },
      { status: 403 }
    );
  }
  if (user.entitlements.evaluations_left <= 0) {
    return NextResponse.json(
      { ok: false, error: "No evaluations left on your plan." },
      { status: 403 }
    );
  }

  const { essay, prompt, assignmentId } = (await req.json()) as {
    essay: string;
    prompt: string;
    assignmentId: string | null;
  };
  if (!prompt?.trim())
    return NextResponse.json({ ok: false, error: "A task prompt is required." }, { status: 400 });
  if (!essay || essay.trim().split(/\s+/).length < 50)
    return NextResponse.json({ ok: false, error: "Essay too short to evaluate." }, { status: 400 });

  // Normalize once, here, so the text we store (and later reuse as
  // "original" for the revision workspace / AI comparison) is the same
  // paragraph-boundary-honoring text the pipeline actually scored — see
  // lib/server/text.ts for why this is necessary.
  const normalizedEssay = normalizeParagraphBreaks(essay);

  const id = `sub_${Date.now()}_${user.id}`;
  const record: SubmissionRecord = {
    id,
    organizationId: user.organizationId,
    studentId: user.id,
    assignmentId,
    prompt,
    essay: normalizedEssay,
    status: "evaluating",
    createdAt: new Date().toISOString(),
  };
  saveSubmission(record);

  try {
    const { report, sessionDir } = await runEvaluation({
      submissionId: id,
      studentId: user.id,
      prompt,
      essay: normalizedEssay,
      plan: user.plan,
    });
    record.status = "done";
    record.report = report;
    record.sessionDir = sessionDir;
    saveSubmission(record);

    user.entitlements.evaluations_left = Math.max(0, user.entitlements.evaluations_left - 1);
    saveUser(user);

    return NextResponse.json({ ok: true, submissionId: id });
  } catch (err) {
    // Was previously swallowed silently into the DB record only — meant
    // real pipeline errors (bad OPENAI_API_KEY, missing canonical
    // resources, a Python traceback, etc.) never showed up in the host's
    // application logs, only as a generic message in the UI. Now also
    // printed to stderr so it appears in Render/Railway logs immediately.
    console.error(`[ST.ELLA] Evaluation failed for submission ${id}:`, err);
    record.status = "failed";
    record.error = err instanceof Error ? err.message : "Evaluation failed.";
    saveSubmission(record);
    return NextResponse.json(
      { ok: false, error: "Evaluation failed. Please try again or contact your trainer." },
      { status: 500 }
    );
  }
}