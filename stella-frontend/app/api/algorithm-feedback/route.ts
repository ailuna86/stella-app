import { NextResponse } from "next/server";
import { currentUser } from "@/lib/server/auth";
import { saveAlgorithmFeedback } from "@/lib/server/store";

// v8: new — trainer's structured QA review of the AI's evaluation on a
// given submission. Trainer/admin-only; never exposed to students.
export async function POST(req: Request) {
  const user = await currentUser();
  if (!user || user.role !== "trainer") return NextResponse.json({ ok: false }, { status: 403 });

  const body = await req.json();
  const {
    submissionId,
    overallAccuracy,
    criteriaFeedback,
    wrongErrorIds,
    missedErrors,
    feedbackQualityNotes,
    generalNotes,
  } = body;
  if (!submissionId || !overallAccuracy)
    return NextResponse.json({ ok: false, error: "Missing fields." }, { status: 400 });

  const saved = saveAlgorithmFeedback({
    organizationId: user.organizationId,
    submissionId,
    trainerId: user.id,
    overallAccuracy,
    criteriaFeedback: criteriaFeedback ?? {},
    wrongErrorIds: wrongErrorIds ?? [],
    missedErrors: missedErrors ?? "",
    feedbackQualityNotes: feedbackQualityNotes ?? "",
    generalNotes: generalNotes ?? "",
  });
  return NextResponse.json({ ok: true, id: saved.id });
}
