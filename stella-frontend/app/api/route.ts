import { NextResponse } from "next/server";
import { currentUser } from "@/lib/server/auth";
import { runVocabCoachSession } from "@/lib/server/goldPipeline";

// Vocabulary Coach (PEEL half) — generates or returns the next available
// session for this student. Cooldown-gated (see
// vocab_coach_selection_engine_v1_1.py): if the student's last session was
// within the cooldown window, this returns { ok: true, session: { status:
// "not_yet_available", ... } } rather than an error — that's an expected,
// normal state, not a failure.
export async function GET() {
  const user = await currentUser();
  if (!user) return NextResponse.json({ ok: false }, { status: 401 });
  if (user.role === "trainer") return NextResponse.json({ ok: false }, { status: 403 });

  try {
    const session = await runVocabCoachSession(user.id);
    return NextResponse.json({ ok: true, session });
  } catch (e) {
    console.error("[ST.ELLA] Vocabulary Coach session generation failed:", e);
    return NextResponse.json(
      { ok: false, error: "Could not generate a vocabulary coach session — please try again." },
      { status: 500 }
    );
  }
}
