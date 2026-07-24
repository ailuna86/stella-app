import Link from "next/link";
import type { SessionFlowStatus, DailyDigest } from "@/lib/server/goldPipeline";

// v19: guided session-flow stepper — Session_Flow_and_Vocab_Expansion_Spec_v1
// §0. Shows the student where they are in the recommended sequence (practice
// → writing coach → vocabulary coach → essay revision if this cycle started
// from an essay) and links straight to whichever step isn't done yet. When
// every step is done, shows a wrap-up using the same real, already-computed
// DailyDigest data (exercises/missions/words) rather than inventing new
// "did this help" copy the pipeline can't actually back up yet — a scoped
// before/after re-check (fewer errors, stronger essay) needs the essay-
// revision re-check engine, which doesn't exist yet (see
// Session_Flow_and_Vocab_Expansion_Spec_v1 §1); this wrap-up doesn't claim it.
export default function SessionFlowStepper({
  status,
  digest,
  currentKey,
}: {
  status: SessionFlowStatus;
  digest?: DailyDigest;
  currentKey?: string;
}) {
  const { steps, currentIndex, cameFromEssay } = status;
  const allDone = currentIndex >= steps.length;

  return (
    <div className="mt-6 rounded-card border border-brand-100 bg-brand-50/40 p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-ink-400">
        {cameFromEssay ? "Today's plan for this essay" : "Today's plan"}
      </p>

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {steps.map((s, i) => {
          const isCurrent = i === currentIndex && s.key !== currentKey;
          const isHere = s.key === currentKey;
          return (
            <div key={s.key} className="flex items-center gap-1.5">
              {i > 0 && <span className="text-ink-300">→</span>}
              <span
                className={`inline-flex items-center gap-1 rounded-full px-3 py-1 text-xs font-medium ${
                  s.done
                    ? "bg-mint-100 text-mint-800"
                    : isHere
                    ? "border border-brand-400 bg-white text-brand-800"
                    : isCurrent
                    ? "bg-brand-600 text-white"
                    : "bg-white text-ink-400"
                }`}
              >
                {s.done && <span aria-hidden>✓</span>}
                {s.label}
              </span>
            </div>
          );
        })}
      </div>

      {!allDone ? (
        <div className="mt-3 flex items-center justify-between gap-3">
          <p className="text-sm text-ink-600">
            Next up: <span className="font-medium text-brand-800">{steps[currentIndex].label}</span>
          </p>
          {steps[currentIndex].key !== currentKey && (
            <Link href={steps[currentIndex].href} className="btn-primary">
              Continue
            </Link>
          )}
        </div>
      ) : (
        <div className="mt-3">
          <p className="text-sm font-medium text-mint-700">
            You've completed today's plan{cameFromEssay ? " for this essay" : ""}.
          </p>
          {digest && digest.hasActivity && (
            <p className="mt-1 text-sm text-ink-600">
              {[
                digest.exercisesCompleted > 0 && `${digest.exercisesCompleted} practice exercise${digest.exercisesCompleted === 1 ? "" : "s"}`,
                digest.missionsCompleted > 0 && `${digest.missionsCompleted} writing mission${digest.missionsCompleted === 1 ? "" : "s"}`,
                digest.newWordsLearned > 0 && `${digest.newWordsLearned} new word${digest.newWordsLearned === 1 ? "" : "s"} learned`,
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          )}
          {cameFromEssay && (
            <p className="mt-1 text-xs text-ink-400">
              Resubmit a revised essay whenever you're ready for a fresh band score.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
