import Link from "next/link";
import { redirect } from "next/navigation";
import { currentUser } from "@/lib/server/auth";
import { loadVocabCoachMasterySummary } from "@/lib/server/goldPipeline";
import VocabCoachPeelSession from "@/components/VocabCoachPeelSession";
import VocabCoachMasteryView from "@/components/VocabCoachMasteryView";

// v14: rewritten per Pipeline_Frontend_Spec_v2 §2. This page used to redirect
// straight into a single essay's raw LRET FIX/ENHANCE/CLARIFY/KEEP breakdown
// whenever one existed (loadLretSession lookup) — that output now lives with
// the feedback report and Essay Revision instead (see ReportView.tsx's "Your
// next step" block and app/vocabulary-coach/[id]/page.tsx, which is still the
// per-essay view, just no longer auto-redirected into from here).
//
// This page is the ongoing, cross-session destination now: mastery progress
// (Leitner box counts, due-for-review, recently mastered, a running count of
// words used correctly) plus daily PEEL practice, unchanged. Nothing here is
// gated on having submitted an essay — PEEL sessions and mastery tracking
// both work from day one.
export default async function VocabularyCoachIndexPage() {
  const user = await currentUser();
  if (!user) return null;
  if (user.role === "trainer") redirect("/trainer");

  const summary = loadVocabCoachMasterySummary(user.id);

  return (
    <div className="mx-auto max-w-xl py-10">
      <h1 className="text-center text-2xl font-semibold">Vocabulary Coach</h1>
      <p className="mt-2 text-center text-sm text-ink-600">
        Daily practice and word-mastery tracking — start today&apos;s practice below, or check
        your progress so far.
      </p>

      {summary ? (
        <div className="mt-6">
          <VocabCoachMasteryView summary={summary} />
        </div>
      ) : (
        <p className="mt-6 text-center text-xs text-ink-400">
          Complete your first practice session below to start tracking mastery.
        </p>
      )}

      <div className="mt-6">
        <VocabCoachPeelSession />
      </div>

      <div className="mt-6 flex justify-center">
        <Link href="/writing/submit" className="btn-secondary">
          Write an essay
        </Link>
      </div>
    </div>
  );
}
