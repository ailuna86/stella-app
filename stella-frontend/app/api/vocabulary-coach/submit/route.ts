import { NextResponse } from "next/server";
import { currentUser } from "@/lib/server/auth";
import { submitVocabCoachResponse } from "@/lib/server/goldPipeline";

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
    return NextResponse.json({ ok: true, result });
  } catch (e) {
    console.error("[ST.ELLA] Vocabulary Coach grading failed:", e);
    return NextResponse.json(
      { ok: false, error: "Grading failed — please try again." },
      { status: 500 }
    );
  }
}
