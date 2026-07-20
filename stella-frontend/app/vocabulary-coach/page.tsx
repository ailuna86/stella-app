import Link from "next/link";
import { redirect } from "next/navigation";
import { currentUser } from "@/lib/server/auth";
import { submissionsFor } from "@/lib/server/store";
import { loadLretSession } from "@/lib/server/goldPipeline";

// v13: index route for Vocabulary Coach (LRET) — mirrors the /writing-coach
// pattern: find the latest evaluated essay that actually has LRET output and
// send the student straight to its essay-specific view, since vocabulary
// classification is generated per essay (07d_lret_session.json lives inside
// that essay's session folder), not as one rolling account-level feed.
export default async function VocabularyCoachIndexPage() {
  const user = await currentUser();
  if (!user) return null;
  if (user.role === "trainer") redirect("/trainer");

  const subs = submissionsFor(user.id).filter((s) => s.status === "done" && s.sessionDir);
  const withLret = subs.find((s) => s.sessionDir && loadLretSession(s.sessionDir));

  if (withLret) redirect(`/vocabulary-coach/${withLret.id}`);

  return (
    <div className="mx-auto max-w-xl py-10 text-center">
      <h1 className="text-2xl font-semibold">Vocabulary coach</h1>
      <p className="mt-2 text-sm text-ink-600">
        Vocabulary coach is built from your evaluated essays — submit your first one to unlock
        it.
      </p>
      <Link href="/writing/submit" className="btn-primary mt-4 inline-flex">
        Write my essay
      </Link>
    </div>
  );
}
