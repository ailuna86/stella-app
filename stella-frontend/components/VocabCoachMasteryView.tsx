import type { VocabCoachMasterySummary } from "@/lib/server/goldPipeline";

// v1: Vocabulary Coach mastery view — Pipeline_Frontend_Spec_v2 §2. Replaces
// the raw per-essay LRET dump this page used to redirect into; that stays
// with the feedback report + Essay Revision instead (see ReportView.tsx's
// "Your next step" block). This page is now the ongoing, cross-session
// picture: what's due, what's been mastered, and a running count of words
// genuinely used correctly — not what was wrong with one specific essay.
const BOX_LABELS: Record<string, string> = {
  new: "New",
  box_1: "Box 1",
  box_2: "Box 2",
  box_3: "Box 3",
  mastered: "Mastered",
};

export default function VocabCoachMasteryView({ summary }: { summary: VocabCoachMasterySummary }) {
  const { boxCounts, dueForReview, recentlyMastered, vocabularyBankCount, sessionsCompleted } = summary;

  return (
    <div className="card shadow-soft">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">Your vocabulary progress</h2>
        <span className="text-xs text-ink-400">
          {sessionsCompleted} session{sessionsCompleted === 1 ? "" : "s"} completed
        </span>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-5">
        {(["new", "box_1", "box_2", "box_3", "mastered"] as const).map((box) => (
          <div
            key={box}
            className={`rounded-card border p-3 text-center ${
              box === "mastered" ? "border-mint-200 bg-mint-50" : "border-brand-100 bg-brand-50/40"
            }`}
          >
            <p className={`text-xl font-semibold ${box === "mastered" ? "text-mint-700" : "text-brand-800"}`}>
              {boxCounts[box]}
            </p>
            <p className="text-[11px] uppercase tracking-wide text-ink-500">{BOX_LABELS[box]}</p>
          </div>
        ))}
      </div>

      <div className="mt-4 rounded-card border border-brand-100 bg-brand-50/40 p-3">
        <p className="text-sm text-ink-700">
          <span className="font-semibold text-brand-800">{vocabularyBankCount}</span> words and
          collocations you&apos;ve used correctly in practice so far.
        </p>
      </div>

      {dueForReview.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-medium uppercase tracking-wide text-amber-700">
            Due for review ({dueForReview.length})
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {dueForReview.map((item) => (
              <span
                key={item.phrase}
                className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-700"
              >
                {item.phrase}
              </span>
            ))}
          </div>
        </div>
      )}

      {recentlyMastered.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-medium uppercase tracking-wide text-mint-700">Recently mastered</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {recentlyMastered.map((item) => (
              <span
                key={item.phrase}
                className="rounded-full border border-mint-200 bg-mint-50 px-3 py-1 text-xs font-medium text-mint-700"
              >
                {item.phrase}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
