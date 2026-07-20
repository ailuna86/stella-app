import { NextResponse } from "next/server";
import { randomUUID } from "crypto";
import { currentUser } from "@/lib/server/auth";
import { addUser, findUserByEmail } from "@/lib/server/store";

const PILOT_DAYS = 7;

export async function POST(req: Request) {
  const user = await currentUser();
  if (!user || user.role !== "trainer")
    return NextResponse.json({ ok: false }, { status: 403 });

  const { name, email, plan } = (await req.json()) as {
    name: string;
    email: string;
    plan?: "gold" | "premium_pilot";
  };
  if (!name?.trim() || !email?.trim())
    return NextResponse.json({ ok: false, error: "Name and email required." }, { status: 400 });
  if (findUserByEmail(email))
    return NextResponse.json({ ok: false, error: "This email already exists." }, { status: 409 });

  // v9 (pilot): trainer picks the tier per student — the existing tutor's
  // cohort gets Gold, the 2nd/new tutor's cohort gets Premium. Defaults to
  // gold if omitted so older clients/forms don't break.
  const resolvedPlan: "gold" | "premium_pilot" = plan ?? "gold";

  const pilotEndsAt = new Date(Date.now() + PILOT_DAYS * 24 * 60 * 60 * 1000).toISOString();

  const newUser = {
    id: `student_${randomUUID()}`,
    organizationId: user.organizationId,
    name: name.trim(),
    email: email.trim().toLowerCase(),
    role: "student" as const,
    plan: resolvedPlan,
    pilotEndsAt,
    entitlements: {
      // v10: was true — meant every student could type any prompt they
      // wanted into the free-text box on /writing/submit, bypassing the
      // trainer entirely. The locked-prompt UI and "No assignment yet"
      // empty state already exist for exactly this case (see
      // app/writing/submit/page.tsx); students should only submit against
      // a prompt the trainer assigned via "New assignment" on /trainer.
      can_self_submit: false,
      can_practice: true,
      // Pilot default — a full free week of coaching without leaving cost
      // exposure unbounded. Adjust here if you want a different cap.
      evaluations_left: 10,
    },
  };
  addUser(newUser);

  return NextResponse.json({ ok: true, studentId: newUser.id });
}