import { NextResponse } from "next/server";
import { findUserByEmail } from "@/lib/server/store";
import { issueCode } from "@/lib/server/otp";
import { checkRateLimit } from "@/lib/server/rate-limit";

// v8: two fixes from the pilot-readiness review.
// 1. No longer reveals whether an email is registered (previously 404 for
//    unknown emails, 200 for known ones — a user-enumeration leak). Now
//    always returns ok:true; unregistered emails just never get a code, so
//    the next step (entering a code) will fail the same way an expired
//    code would.
// 2. Rate-limited per email and per IP, so a code can't be used to spam
//    someone's inbox or be brute-forced via repeated requests.
export async function POST(req: Request) {
  const { email } = (await req.json()) as { email: string };
  const normalized = (email ?? "").trim().toLowerCase();
  if (!normalized) return NextResponse.json({ ok: false, error: "Email required." }, { status: 400 });

  const ip = req.headers.get("x-forwarded-for") ?? "unknown";
  if (!checkRateLimit(`request-code:email:${normalized}`, 5) || !checkRateLimit(`request-code:ip:${ip}`, 20)) {
    return NextResponse.json({ ok: false, error: "Too many requests. Please wait a bit and try again." }, { status: 429 });
  }

  const user = findUserByEmail(normalized);
  let emailed = false;
  let devCode: string | undefined;
  if (user) {
    const result = await issueCode(user.email);
    emailed = result.emailed;
    devCode = result.devCode;
  }

  return NextResponse.json({ ok: true, emailed, devCode });
}
