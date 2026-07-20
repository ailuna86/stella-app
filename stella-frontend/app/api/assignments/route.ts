import { NextResponse } from "next/server";
import { randomUUID } from "crypto";
import { currentUser } from "@/lib/server/auth";
import { addAssignment } from "@/lib/server/store";

export async function POST(req: Request) {
  const user = await currentUser();
  if (!user || user.role !== "trainer")
    return NextResponse.json({ ok: false }, { status: 403 });

  const { prompt, dueDate, studentIds } = await req.json();
  if (!prompt?.trim() || !dueDate || !studentIds?.length)
    return NextResponse.json({ ok: false, error: "Missing fields." }, { status: 400 });

  addAssignment({
    id: `asg_${randomUUID()}`,
    organizationId: user.organizationId,
    trainerId: user.id,
    prompt: prompt.trim(),
    dueDate,
    studentIds,
    createdAt: new Date().toISOString(),
  });
  return NextResponse.json({ ok: true });
}
