import Link from "next/link";
import { redirect } from "next/navigation";
import { currentUser } from "@/lib/server/auth";
import { loadVocabCoachMasterySummary, getSessionFlowStatus, loadDailyDigest } from "@/lib/server/goldPipeline";
import { submissionsFor } from "@/lib/server/store";
import VocabCoachPeelSession from "@/components/VocabCoachPeelSession";
import VocabCoachMasteryView from "@/components/VocabCoachMasteryView";
import EngineIntroModal from "@/components/EngineIntroModal";
import SessionFlowStepper from "@/components/SessionFlowStepper";

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
  // v27 (2026-07-23): Vocabulary Coach is part of Gold's coaching/learning
  // layer, deliberately excluded from the new scored-only Premium tier (see
  // PREMIUM_PIPELINE_SPEC_V1.docx) -- this page previously had ZERO
  // server-side plan check (only the /writing hub's goldOnly card was
  // cosmetic), so a premium student who navigated here directly, or
  // bookmarked the URL, got a fully working Vocabulary Coach anyway. Redirect
  // to the hub, which already shows the real upgrade-to-Gold messaging for
  // this card.
  if (user.plan !== "gold") redirect("/writing");

  const summary = loadVocabCoachMasterySummary(user.id);
  // v19: session-flow stepper — Session_Flow_and_Vocab_Expansion_Spec_v1 §0.
  const latest = submissionsFor(user.id).find((s) => s.status === "done" && s.sessionDir);
  const flowStatus = getSessionFlowStatus(user.id, { sessionDir: latest?.sessionDir, submissionId: latest?.id });
  const flowDigest = loadDailyDigest(user.id);

  return (
    <div className="mx-auto max-w-xl py-10">
      <h1 className="flex items-center justify-center text-center text-2xl font-semibold">
        Vocabulary Coach
        <EngineIntroModal engineKey="vocabulary_coach" />
      </h1>
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

      <SessionFlowStepper status={flowStatus} digest={flowDigest} currentKey="vocabulary_coach" />

      <div className="mt-6 flex justify-center">
        <Link href="/writing/submit" className="btn-secondary">
          Write an essay
        </Link>
      </div>
    </div>
  );
}
