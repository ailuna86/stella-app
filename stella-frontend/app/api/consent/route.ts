import { NextResponse } from "next/server";
import { currentUser } from "@/lib/server/auth";
import { saveUser } from "@/lib/server/store";

// v8: new — one-time AI-processing disclosure. Applies to every user
// (pilot student or paying subscriber), not just this pilot.
// v10: dropped the recordConsent() call — that function was never defined
// in store.ts (a leftover from an earlier draft), which made every click
// of "I understand and agree" 500. saveUser() below already persists
// consentAt to the same column, so nothing else needs to change.
export async function POST() {
  const user = await currentUser();
  if (!user) return NextResponse.json({ ok: false }, { status: 401 });

  user.consentAt = new Date().toISOString();
  saveUser(user);
  return NextResponse.json({ ok: true });
}
