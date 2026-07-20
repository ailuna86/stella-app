import { NextResponse } from "next/server";
import { findUserByEmail, saveUser } from "@/lib/server/store";
import { verifyCode } from "@/lib/server/otp";
import { setSessionUser } from "@/lib/server/auth";
import { checkRateLimit } from "@/lib/server/rate-limit";

// v8: sets a signed iron-session cookie (setSessionUser) instead of writing
// the raw user ID directly — see lib/server/session.ts. Also rate-limited
// per IP to slow down brute-forcing the 6-digit code.
export async function POST(req: Request) {
  const { email, code } = (await req.json()) as { email: string; code: string };
  const normalized = (email ?? "").trim().toLowerCase();

  const ip = req.headers.get("x-forwarded-for") ?? "unknown";
  if (!checkRateLimit(`verify:ip:${ip}`, 30)) {
    return NextResponse.json({ ok: false }, { status: 429 });
  }

  const user = findUserByEmail(normalized);
  if (!user || !verifyCode(normalized, code ?? ""))
    return NextResponse.json({ ok: false }, { status: 401 });

  if (!user.verifiedAt) {
    user.verifiedAt = new Date().toISOString();
    saveUser(user);
  }

  await setSessionUser(user.id);
  return NextResponse.json({ ok: true, firstTime: !user.intake });
}
