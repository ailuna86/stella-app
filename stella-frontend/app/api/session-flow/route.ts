import { NextResponse } from "next/server";
import { currentUser } from "@/lib/server/auth";
import { submissionsFor } from "@/lib/server/store";
import { getSessionFlowStatus, loadDailyDigest } from "@/lib/server/goldPipeline";

// v19: Practice is the one engine page that's a client component with no
// server-fetched props (see app/practice/page.tsx) — every other engine page
// computes getSessionFlowStatus server-side directly. This route exists only
// so Practice's client component can get the same real status the others do,
// instead of duplicating the "latest submission" lookup as a second, parallel
// API surface.
export async function GET() {
  const user = await currentUser();
  if (!user) return NextResponse.json({ ok: false }, { status: 401 });

  const latest = submissionsFor(user.id).find((s) => s.status === "done" && s.sessionDir);
  const status = getSessionFlowStatus(user.id, { sessionDir: latest?.sessionDir, submissionId: latest?.id });
  const digest = loadDailyDigest(user.id);

  return NextResponse.json({ ok: true, status, digest });
}
