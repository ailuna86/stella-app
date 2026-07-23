import Link from "next/link";
import { redirect } from "next/navigation";
import { currentUser } from "@/lib/server/auth";

// v1: new — the "Writing" hub, per Pipeline_Frontend_Spec_v2 §1. Top nav
// collapses to Dashboard · Writing · Progress; this page is what "Writing"
// opens. It replaces three separate, unclearly-named top-nav links ("Your
// plan", "Coach", "Vocabulary") with one hub of full-named cards, so the
// nav bar has room to grow into Speaking/Reading/Critical Thinking Coach
// later as siblings of Writing rather than more items crammed into one bar.
//
// Deliberately NOT listed here: Essay Revision (§1 — it's anchored directly
// below the feedback report once an essay is evaluated, not a standing
// destination a student browses to) and the LRET-derived "Word Choice
// Report" (same reasoning — tied to one essay, lives with that essay's
// report, not in this hub).
interface Card {
  href: string;
  title: string;
  description: string;
  goldOnly?: boolean;
}

const CARDS: Card[] = [
  {
    href: "/writing/submit",
    title: "Submit an essay",
    description: "Write or paste a Task 2 response and get it evaluated on all four IELTS criteria.",
  },
  {
    href: "/vocabulary-coach",
    title: "Vocabulary Coach",
    description: "Daily retrieval practice and word-mastery tracking — new words, words due for review, and words you've already mastered.",
    goldOnly: true,
  },
  {
    href: "/writing-coach",
    title: "Writing Coach",
    description: "A short, personalized writing mission generated after every evaluated essay.",
    goldOnly: true,
  },
  {
    href: "/practice",
    title: "Practice",
    description: "Quick timed exercises targeting whatever's holding your score back right now.",
  },
  {
    href: "/study-plan",
    title: "Study plan",
    description: "A simple, focused sequence of what to do next, based on your latest essay.",
    goldOnly: true,
  },
];

export default async function WritingHubPage() {
  const user = await currentUser();
  if (!user) return null;
  if (user.role === "trainer") redirect("/trainer");

  const isGold = user.plan === "gold";

  return (
    <div className="mx-auto max-w-2xl py-6">
      <h1 className="text-2xl font-semibold">Writing</h1>
      <p className="mt-2 text-sm text-ink-600">
        Everything for Writing Task 2 in one place — submit an essay, practice daily, and act on
        your feedback.
      </p>

      <div className="mt-6 space-y-3">
        {CARDS.map((c) => {
          const locked = c.goldOnly && !isGold;
          if (locked) {
            return (
              <div key={c.href} className="card flex items-center justify-between gap-4 opacity-60">
                <div>
                  <h2 className="font-semibold">{c.title}</h2>
                  <p className="mt-1 text-sm text-ink-600">{c.description}</p>
                </div>
                <Link href="/upgrade" className="btn-secondary shrink-0 whitespace-nowrap">
                  Gold only
                </Link>
              </div>
            );
          }
          return (
            <Link key={c.href} href={c.href} className="card shadow-soft flex items-center justify-between gap-4 hover:border-brand-300">
              <div>
                <h2 className="font-semibold">{c.title}</h2>
                <p className="mt-1 text-sm text-ink-600">{c.description}</p>
              </div>
              <span className="material-symbols-outlined shrink-0 text-brand-400">chevron_right</span>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
