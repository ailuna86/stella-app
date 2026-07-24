import Link from "next/link";

// v1: new — ST_ELLA_Student_Journey_v1.docx §4.5 ("Vocabulary Coach layout").
// The gap it identified: Vocabulary Coach, Writing Coach, Practice, and the
// feedback report each end with at most one bare "Back to dashboard" link
// (confirmed directly — none of the four had anything pointing to the other
// three), so the app reads as separate disconnected tools rather than one
// flow. One shared, same-placement component fixes that: 2-3 next-action
// links, always excluding whichever page you're already on.
type StepKey = "vocabulary_coach" | "writing_coach" | "practice" | "writing_hub";

const STEPS: Record<StepKey, { href: string; label: string; icon: string }> = {
  vocabulary_coach: { href: "/vocabulary-coach", label: "Vocabulary Coach", icon: "translate" },
  writing_coach: { href: "/writing-coach", label: "Writing Coach", icon: "edit_note" },
  practice: { href: "/practice", label: "Practice", icon: "schedule" },
  writing_hub: { href: "/writing", label: "Writing hub", icon: "menu_book" },
};

export default function NextStepsStrip({ exclude = [] }: { exclude?: StepKey[] }) {
  const keys = (Object.keys(STEPS) as StepKey[]).filter((k) => !exclude.includes(k));
  if (keys.length === 0) return null;

  return (
    <div className="mt-8 border-t border-brand-100 pt-5">
      <p className="text-xs font-medium uppercase tracking-wide text-ink-400">Keep going</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {keys.map((k) => {
          const s = STEPS[k];
          return (
            <Link key={k} href={s.href} className="btn-secondary inline-flex items-center gap-1.5">
              <span className="material-symbols-outlined text-[16px]">{s.icon}</span>
              {s.label}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
