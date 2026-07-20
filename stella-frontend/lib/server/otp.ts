// One-time email confirmation codes.
// v7: SMTP send wrapped in try/catch so a bad config falls back to showing
// the code on screen instead of crashing the login flow.
// v8: switched from personal Gmail SMTP (nodemailer) to Resend, a
// transactional email API — Gmail's sending limits and spam-flagging make
// it unreliable past a handful of recipients. Falls back to the same
// on-screen devCode when RESEND_API_KEY/RESEND_FROM aren't set or a send
// fails, so local testing still works with zero email setup.
import { db } from "./db";
import crypto from "crypto";
import { Resend } from "resend";

const TTL_MS = 10 * 60 * 1000;

export async function issueCode(email: string): Promise<{ emailed: boolean; devCode?: string }> {
  const code = String(crypto.randomInt(100000, 999999));
  const key = email.toLowerCase();
  db()
    .prepare("INSERT OR REPLACE INTO otp_codes (email, code, expires, attempts) VALUES (?, ?, ?, 0)")
    .run(key, code, Date.now() + TTL_MS);

  const apiKey = process.env.RESEND_API_KEY;
  const from = process.env.RESEND_FROM; // e.g. "ST.ELLA <login@yourdomain.com>"
  if (apiKey && from) {
    try {
      const resend = new Resend(apiKey);
      const { error } = await resend.emails.send({
        from,
        to: email,
        subject: `${code} is your ST.ELLA confirmation code`,
        text: `Your ST.ELLA confirmation code is ${code}. It expires in 10 minutes.`,
      });
      if (error) throw new Error(error.message);
      return { emailed: true };
    } catch (e) {
      console.error(
        `[ST.ELLA OTP] Failed to email code to ${email} — check RESEND_API_KEY/RESEND_FROM in .env.local:`,
        e instanceof Error ? e.message : e
      );
      console.log(`\n[ST.ELLA OTP] Confirmation code for ${email}: ${code}\n`);
      return { emailed: false, devCode: code };
    }
  } else {
    console.log(`\n[ST.ELLA OTP] Confirmation code for ${email}: ${code}\n`);
    return { emailed: false, devCode: code };
  }
}

export function verifyCode(email: string, code: string): boolean {
  const key = email.toLowerCase();
  const row = db().prepare("SELECT * FROM otp_codes WHERE email = ?").get(key) as any;
  if (!row) return false;
  if (row.attempts + 1 > 5 || Date.now() > row.expires) {
    db().prepare("DELETE FROM otp_codes WHERE email = ?").run(key);
    return false;
  }
  db().prepare("UPDATE otp_codes SET attempts = attempts + 1 WHERE email = ?").run(key);
  const ok = row.code === code.trim();
  if (ok) db().prepare("DELETE FROM otp_codes WHERE email = ?").run(key);
  return ok;
}
