import { NextResponse } from "next/server";
import { currentUser } from "@/lib/server/auth";
import { saveUser } from "@/lib/server/store";

// v9: extended intake — adds examType (with an early-exit path for General,
// not supported yet), residence (price differentiation), referralSource,
// purpose, priorAttempts, biggestChallenge. When examType is "general" the
// survey UI submits early with only examType set — the rest default to ""
// so app/page.tsx's `if (!user.intake) redirect("/survey")` check is
// satisfied and the student lands on the General-waitlist page instead of
// looping back into the survey.
export async function POST(req: Request) {
  const user = await currentUser();
  if (!user) return NextResponse.json({ ok: false }, { status: 401 });

  const body = (await req.json()) as Record<string, unknown>;

  user.intake = {
    examType: (body.examType as "academic" | "general") ?? "academic",
    goalBand: typeof body.goalBand === "number" ? body.goalBand : 7.0,
    examDate: (body.examDate as string) ?? "",
    residence: (body.residence as string) ?? "",
    referralSource: (body.referralSource as string) ?? "",
    purpose: (body.purpose as string) ?? "",
    selfLevel: (body.selfLevel as string) ?? "",
    priorAttempts: (body.priorAttempts as string) ?? "",
    biggestChallenge: (body.biggestChallenge as string) ?? "",
    minutesPerDay: (body.minutesPerDay as 5 | 10 | 15) ?? 10,
    completedAt: new Date().toISOString(),
  };
  saveUser(user);

  return NextResponse.json({ ok: true });
} 