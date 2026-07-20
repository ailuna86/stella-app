import { NextResponse } from "next/server";
import { randomUUID } from "crypto";
import { currentUser } from "@/lib/server/auth";
import { addUser, findUserByEmail } from "@/lib/server/store";

export async function POST(req: Request) {
  const user = await currentUser();
  if (!user || user.role !== "trainer")
    return NextResponse.json({ ok: false }, { status: 403 });

  const { name, email } = (await req.json()) as { name: string; email: string };
  if (!name?.trim() || !email?.trim())
    return NextResponse.json({ ok: false, error: "Name and email required." }, { status: 400 });
  if (findUserByEmail(email))
    return NextResponse.json({ ok: false, error: "This email already exists." }, { status: 409 });

  addUser({
    id: `trainer_${randomUUID()}`,
    organizationId: user.organizationId,
    name: name.trim(),
    email: email.trim().toLowerCase(),
    role: "trainer",
    plan: "gold",
    entitlements: { can_self_submit: true, can_practice: true, evaluations_left: 999 },
  });
  return NextResponse.json({ ok: true });
}
