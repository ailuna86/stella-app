import { NextResponse } from "next/server";
import { currentUser } from "@/lib/server/auth";
import { markEngineIntroSeen } from "@/lib/server/store";

// v18: the "first-visit engine description" pop-up — ST_ELLA_Student_Journey_v1.docx
// §4.4 / Pipeline_Frontend_Spec_v2 §1. The server-side persistence half of this
// (User.seenEngineIntros, markEngineIntroSeen()) already existed — confirmed
// directly, it just had no API route or UI ever built on top of it, so the
// pop-up itself never appeared anywhere. This route is that missing piece:
// GET checks whether THIS student has seen a given engine's intro before
// (server-side, per student, not localStorage — the whole point per §4.4 is
// that a phone-only "seen it" flag shouldn't re-show the modal on a laptop);
// POST marks it seen when the student clicks "Got it".
export async function GET(req: Request) {
  const user = await currentUser();
  if (!user) return NextResponse.json({ ok: false }, { status: 401 });

  const engine = new URL(req.url).searchParams.get("engine");
  if (!engine) return NextResponse.json({ ok: false, error: "Missing engine key" }, { status: 400 });

  const seen = (user.seenEngineIntros ?? []).includes(engine);
  return NextResponse.json({ ok: true, seen });
}

export async function POST(req: Request) {
  const user = await currentUser();
  if (!user) return NextResponse.json({ ok: false }, { status: 401 });

  const body = await req.json().catch(() => ({}));
  const engine = body?.engine;
  if (!engine || typeof engine !== "string") {
    return NextResponse.json({ ok: false, error: "Missing engine key" }, { status: 400 });
  }

  markEngineIntroSeen(user.id, engine);
  return NextResponse.json({ ok: true });
}
