// v8: fixes the critical bug found before pilot launch — the old cookie
// (`stella_uid`) stored a raw, unsigned user ID. Anyone could set that
// cookie to any value (e.g. the trainer's static "trainer_1" ID) in their
// browser and be logged in as that person with zero verification. iron-
// session seals the cookie's contents with a secret key known only to the
// server, so it can't be forged, read, or edited by the browser — the
// cookie is useless to anyone without SESSION_SECRET.
import { getIronSession, IronSession } from "iron-session";
import { cookies } from "next/headers";

export interface SessionData {
  userId?: string;
}

const FALLBACK_DEV_SECRET = "dev-only-insecure-secret-change-me-before-deploying-0000";

function sessionPassword(): string {
  const secret = process.env.SESSION_SECRET;
  if (secret && secret.length >= 32) return secret;
  if (process.env.NODE_ENV === "production") {
    console.error(
      "[ST.ELLA] SESSION_SECRET is missing or too short (needs 32+ characters) — " +
        "falling back to an insecure default. Set a real SESSION_SECRET in production."
    );
  }
  return FALLBACK_DEV_SECRET;
}

export function getSession(): Promise<IronSession<SessionData>> {
  return getIronSession<SessionData>(cookies(), {
    password: sessionPassword(),
    cookieName: "stella_session",
    cookieOptions: {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      maxAge: 60 * 60 * 24 * 30,
      path: "/",
    },
  });
}
