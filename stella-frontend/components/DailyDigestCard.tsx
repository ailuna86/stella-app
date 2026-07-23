import type { DailyDigest } from "@/lib/server/goldPipeline";

// v1: daily cross-engine digest — Pipeline_Frontend_Spec_v2 §4. Only rendered
// by the dashboard when digest.hasActivity is true, so a student who hasn't
// done anything today doesn't get a deflating "0 exercises, 0 missions" card.
function pluralize(n: number, word: string) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

export default function DailyDigestCard({ digest }: { digest: DailyDigest }) {
  const parts: string[] = [];
  if (digest.exercisesCompleted > 0) parts.push(pluralize(digest.exercisesCompleted, "exercise"));
  if (digest.missionsCompleted > 0) parts.push(pluralize(digest.missionsCompleted, "writing mission"));
  if (digest.newWordsLearned > 0) parts.push(pluralize(digest.newWordsLearned, "new word or collocation"));

  const summary =
    parts.length === 0
      ? "You checked in today."
      : parts.length === 1
      ? `You completed ${parts[0]}.`
      : `You completed ${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}.`;

  return (
    <div className="card-gold">
      <h2 className="font-semibold text-ink-800">Today was a good day!</h2>
      <p className="mt-1 text-sm text-ink-600">{summary}</p>
      {digest.workOnNext && (
        <p className="mt-1 text-sm text-ink-600">
          Work on <span className="font-medium text-brand-800">{digest.workOnNext}</span> next time.
        </p>
      )}
      <p className="mt-2 text-sm text-ink-600">See you tomorrow!</p>
    </div>
  );
}
