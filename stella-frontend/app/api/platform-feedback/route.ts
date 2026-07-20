import { NextResponse } from "next/server";
import { currentUser } from "@/lib/server/auth";
import { savePlatformFeedback } from "@/lib/server/store";

export async function POST(req: Request) {
  const user = await currentUser();
  if (!user) return NextResponse.json({ ok: false }, { status: 401 });

  const { context, clarity, usefulness, comment } = await req.json();
  if (!["report", "practice"].includes(context))
    return NextResponse.json({ ok: false }, { status: 400 });

  savePlatformFeedback({
    studentId: user.id,
    context,
    clarity: Number(clarity) || 0,
    usefulness: Number(usefulness) || 0,
    comment: String(comment ?? "").slice(0, 1000),
    at: new Date().toISOString(),
  });
  return NextResponse.json({ ok: true });
}
