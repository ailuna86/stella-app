"use client";

import { useEffect, useState } from "react";

// v18: first-visit engine description pop-up — ST_ELLA_Student_Journey_v1.docx
// §4.4 / Pipeline_Frontend_Spec_v2 §1. 2-3 sentences, then 3 plain-language
// bullets, one "Got it" button. Auto-opens once per student per engine
// (server-side flag via /api/engine-intro, not localStorage — a student
// switching from phone to laptop should not see it re-triggered). The small
// "?" button next to the page title reopens the same content on demand,
// every time, regardless of seen-state.
interface EngineIntroContent {
  title: string;
  summary: string;
  bullets: string[];
}

const CONTENT: Record<string, EngineIntroContent> = {
  vocabulary_coach: {
    title: "Vocabulary Coach",
    summary:
      "Vocabulary Coach tracks specific words and collocations over time using spaced repetition — a word only counts as “mastered” once it survives a later retest, not the first time you use it correctly.",
    bullets: [
      "A short daily practice paragraph (PEEL) using target words at your level.",
      "Each word moves through review stages — new, review 1–3, mastered — based on real, repeated use.",
      "When you've submitted a recent essay, practice leans toward the topic and words that essay's feedback flagged.",
    ],
  },
  writing_coach: {
    title: "Writing Coach",
    summary:
      "Writing Coach is a short daily exercise — usually about 10 minutes — that trains one specific sentence-level skill at a time, chosen from what your actual essays show you need most.",
    bullets: [
      "Each mission asks you to write real sentences, not pick a multiple-choice answer.",
      "Feedback is Pass / Partial / Not yet, with what went well and what to fix first.",
      "Once you're consistent on one skill, it moves on to the next thing holding your score back.",
    ],
  },
  practice: {
    title: "Practice",
    summary:
      "Practice is a quick, timed set of exercises drawn from your recent mistakes — grammar and structure drills, not a random general quiz.",
    bullets: [
      "Each session is short and can be repeated as often as you like.",
      "Skill families you've already mastered rotate out over time.",
      "A session recap at the end groups results into what went well vs. what to work on next.",
    ],
  },
  essay_revision: {
    title: "Essay Revision",
    summary:
      "Essay Revision lets you rewrite specific parts of an already-evaluated essay and compare your revision against an AI model answer for the same prompt — without spending another evaluation credit.",
    bullets: [
      "Guided by paragraph-level status cards and sentence-level hints drawn from your original feedback.",
      "A three-way comparison (your original / your revision / an AI model) shows concretely what changed.",
      "This is a learning exercise, not a new official score — it does not re-band your essay.",
    ],
  },
};

export default function EngineIntroModal({ engineKey }: { engineKey: keyof typeof CONTENT }) {
  const [open, setOpen] = useState(false);
  const content = CONTENT[engineKey];

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/engine-intro?engine=${engineKey}`)
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        const seen = data.ok ? !!data.seen : true; // fail-safe: never force-show on a fetch error
        if (!seen) setOpen(true);
      })
      .catch(() => {
        // fetch failed — fail safe, don't force-show
      });
    return () => {
      cancelled = true;
    };
  }, [engineKey]);

  async function gotIt() {
    setOpen(false);
    try {
      await fetch("/api/engine-intro", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ engine: engineKey }),
      });
    } catch {
      // non-critical — worst case the modal auto-opens once more next visit
    }
  }

  if (!content) return null;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={`What is ${content.title}?`}
        className="ml-1.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-brand-200 text-[11px] font-bold text-brand-600 hover:bg-brand-50 align-middle"
      >
        ?
      </button>

      {open && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-card bg-white p-6 shadow-soft">
            <h2 className="text-lg font-semibold text-ink-900">{content.title}</h2>
            <p className="mt-2 text-sm leading-relaxed text-ink-700">{content.summary}</p>
            <ul className="mt-3 space-y-1.5 text-sm text-ink-700">
              {content.bullets.map((b, i) => (
                <li key={i} className="flex gap-2">
                  <span className="text-brand-500">&bull;</span> <span>{b}</span>
                </li>
              ))}
            </ul>
            <button className="btn-primary mt-5 w-full" onClick={gotIt}>
              Got it
            </button>
          </div>
        </div>
      )}
    </>
  );
}
