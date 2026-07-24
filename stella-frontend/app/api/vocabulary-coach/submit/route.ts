import { NextResponse } from "next/server";
import { currentUser } from "@/lib/server/auth";
import { submitVocabCoachResponse, refreshLearnerProfile } from "@/lib/server/goldPipeline";

// Grades a Vocabulary Coach PEEL submission and updates the student's
// Leitner ledger in one call (see submitVocabCoachResponse in
// goldPipeline.ts). sessionFilePath is the file path returned alongside the
// session by GET /api/vocabulary-coach/session — the client round-trips it
// back rather than the server trying to re-derive "the current session" from
// scratch, since sessions are per-request artifacts, not standing state.
export async function POST(req: Request) {
  const user = await currentUser();
  if (!user) return NextResponse.json({ ok: false }, { status: 401 });
  if (user.role === "trainer") return NextResponse.json({ ok: false }, { status: 403 });
  // v27 (2026-07-23): Vocabulary Coach is Gold-only (see
  // PREMIUM_PIPELINE_SPEC_V1.docx) -- matches the GET session route's gate.
  if (user.plan !== "gold") {
    return NextResponse.json({ ok: false, error: "Vocabulary Coach is a Gold-plan feature." }, { status: 403 });
  }

  const { sessionFilePath, text } = (await req.json()) as {
    sessionFilePath?: string;
    text?: string;
  };
  if (!sessionFilePath) {
    return NextResponse.json({ ok: false, error: "Missing session reference." }, { status: 400 });
  }
  if (!text || !text.trim()) {
    return NextResponse.json({ ok: false, error: "Write your paragraph before submitting." }, { status: 400 });
  }

  try {
    const result = await submitVocabCoachResponse(user.id, sessionFilePath, text);
    // v21 (2026-07-23): continuous-loop refresh — fire-and-forget, same
    // reasoning as /api/practice. Note: the Vocabulary Coach ledger itself
    // is already real, live history (LIE's --vocabulary-coach argument reads
    // it directly and always has — this refresh call is about giving
    // Practice/Writing Coach/Essay Revision activity the same "something
    // just happened, refresh the profile" trigger the vocab ledger's own
    // writes already benefited from implicitly on the next essay submission).
    void refreshLearnerProfile(user.id);
    return NextResponse.json({ ok: true, result });
  } catch (e) {
    console.error("[ST.ELLA] Vocabulary Coach grading failed:", e);
    return NextResponse.json(
      { ok: false, error: "Grading failed — please try again." },
      { status: 500 }
    );
  }
}
