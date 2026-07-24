"use client";

import { useEffect, useState } from "react";
import NextStepsStrip from "@/components/NextStepsStrip";

interface VocabItem {
  phrase: string;
  topic?: string;
  subtopic?: string;
  // v1_2: bare academic words (source_bank "academic_word") carry an
  // optional structural_hint -- shown only if the student asks, never by
  // default (Academic_Words_Redesign_Spec_v1.docx Section 4).
  source_bank?: string;
  part_of_speech?: string;
  structural_hint?: string;
}

interface ReviewItem {
  phrase: string;
  box: string;
  note: string;
}

interface SessionState {
  status: "generated" | "not_yet_available";
  nextSessionAvailableAt: string | null;
  sessionId: string | null;
  topic: string | null;
  subtopic: string | null;
  taskType: string | null;
  angle: string | null;
  scenarioText: string | null;
  instructionFinal: string | null;
  suggestedVocabulary: VocabItem[];
  reviewItems: ReviewItem[];
  lretBiasApplied: boolean;
  lretBiasNote: string | null;
  filePath: string;
}

interface ItemVerdict {
  phrase: string;
  source: string;
  verdict: string;
  evidence: string;
}

interface SubmitResult {
  itemVerdicts: ItemVerdict[];
  paragraphNote: { oneIdeaOk: boolean | null; note: string };
  llmChecked: boolean;
}

const VERDICT_META: Record<string, { label: string; className: string }> = {
  used_correctly: { label: "Used correctly", className: "border-mint-200 bg-mint-50 text-mint-700" },
  used_but_awkward: { label: "Used, a bit awkward", className: "border-amber-200 bg-amber-50 text-amber-700" },
  attempted_incorrectly: { label: "Attempted, not quite right", className: "border-rose-200 bg-rose-50 text-rose-700" },
  not_used: { label: "Not used", className: "border-ink-200 bg-ink-50 text-ink-600" },
  needs_review: { label: "Needs a human look", className: "border-brand-200 bg-brand-50 text-brand-700" },
};

function formatCooldown(iso: string | null): string {
  if (!iso) return "soon";
  const then = new Date(iso).getTime();
  const now = Date.now();
  const hrs = Math.max(0, Math.round((then - now) / (1000 * 60 * 60)));
  if (hrs <= 0) return "any moment now";
  if (hrs === 1) return "in about 1 hour";
  if (hrs < 24) return `in about ${hrs} hours`;
  const days = Math.round(hrs / 24);
  return days === 1 ? "tomorrow" : `in about ${days} days`;
}

export default function VocabCoachPeelSession() {
  const [loading, setLoading] = useState(true);
  const [session, setSession] = useState<SessionState | null>(null);
  const [err, setErr] = useState("");
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<SubmitResult | null>(null);
  // v1_2: which academic-word chips currently have their structural_hint
  // expanded -- collapsed by default for every item, per spec Section 4.
  const [openHints, setOpenHints] = useState<Record<number, boolean>>({});

  useEffect(() => {
    let cancelled = false;
    fetch("/api/vocabulary-coach/session")
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        if (data.ok) setSession(data.session);
        else setErr(data.error ?? "Could not load a vocabulary coach session.");
      })
      .catch(() => !cancelled && setErr("Could not load a vocabulary coach session."))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSubmit() {
    if (!session || session.status !== "generated" || !text.trim()) return;
    setSubmitting(true);
    setErr("");
    try {
      const res = await fetch("/api/vocabulary-coach/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sessionFilePath: session.filePath, text }),
      });
      const data = await res.json();
      if (data.ok) setResult(data.result);
      else setErr(data.error ?? "Grading failed.");
    } catch {
      setErr("Grading failed — please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return <div className="card animate-pulse text-sm text-ink-500">Loading today's practice…</div>;
  }

  if (err && !session) {
    return <div className="card border-rose-200 text-sm text-rose-700">{err}</div>;
  }

  if (!session || session.status === "not_yet_available") {
    return (
      <div className="card shadow-soft">
        <h2 className="text-lg font-semibold">Vocabulary practice</h2>
        <p className="mt-2 text-sm text-ink-600">
          You've completed today's practice. Your next set is ready {formatCooldown(session?.nextSessionAvailableAt ?? null)}.
        </p>
        <NextStepsStrip exclude={["vocabulary_coach"]} />
      </div>
    );
  }

  if (result) {
    return (
      <div className="card shadow-soft">
        <h2 className="text-lg font-semibold">Your results</h2>
        <div className="mt-3 space-y-2">
          {result.itemVerdicts.map((v, i) => {
            const meta = VERDICT_META[v.verdict] ?? VERDICT_META.needs_review;
            return (
              <div key={i} className={`rounded-card border p-3 ${meta.className}`}>
                <div className="flex items-center justify-between gap-2">
                  <code className="text-sm font-medium">"{v.phrase}"</code>
                  <span className="shrink-0 text-xs font-medium uppercase tracking-wide">{meta.label}</span>
                </div>
                {v.evidence && <p className="mt-1 text-xs opacity-80">{v.evidence}</p>}
              </div>
            );
          })}
        </div>
        {result.paragraphNote.note && (
          <p className="mt-3 text-xs text-ink-500">{result.paragraphNote.note}</p>
        )}
        {!result.llmChecked && (
          <p className="mt-2 text-xs text-ink-400">
            Semantic check not run this time — verdicts above marked "needs a human look" are unverified, not wrong.
          </p>
        )}
        <NextStepsStrip exclude={["vocabulary_coach"]} />
      </div>
    );
  }

  return (
    <div className="card shadow-soft">
      <h2 className="text-lg font-semibold">Vocabulary practice</h2>
      <p className="mt-1 text-xs uppercase tracking-wide text-ink-400">
        {[session.topic, session.subtopic].filter(Boolean).join(" · ")}
        {session.angle ? ` · ${session.angle}` : ""}
      </p>

      {/* Universal PEEL explanation -- same structure regardless of task_type/
          angle, so it lives once here rather than repeated across all 228
          prompt-bank entries. Collapsed by default (native <details>, no JS
          state needed) so it doesn't distract returning students, but is one
          click away for anyone unsure what "one paragraph" should contain. */}
      <details className="mt-3 rounded-card border border-brand-100 bg-brand-50/40 p-3 text-sm">
        <summary className="cursor-pointer font-medium text-brand-800">
          What should my paragraph contain? (PEEL)
        </summary>
        <ul className="mt-2 space-y-1.5 text-ink-700">
          <li><span className="font-medium text-brand-800">Point</span> — state your one idea in the first sentence. Don't hedge or list multiple ideas.</li>
          <li><span className="font-medium text-brand-800">Evidence</span> — give one specific example, reason, or fact that supports it.</li>
          <li><span className="font-medium text-brand-800">Explain</span> — say why that evidence actually supports your point.</li>
          <li><span className="font-medium text-brand-800">Link</span> — close with a sentence that ties back to the question.</li>
        </ul>
        <p className="mt-2 text-xs text-ink-500">
          This is the same structure every time, whatever the task type — only the topic and target words change.
        </p>
      </details>

      {session.scenarioText && <p className="mt-3 text-sm leading-relaxed text-ink-700">{session.scenarioText}</p>}
      {session.instructionFinal && (
        <p className="mt-2 text-sm font-medium text-ink-900">{session.instructionFinal}</p>
      )}

      {session.suggestedVocabulary.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {session.suggestedVocabulary.map((v, i) => {
            const isAcademic = v.source_bank === "academic_word";
            if (!isAcademic || !v.structural_hint) {
              return (
                <span key={i} className="rounded-full border border-brand-200 bg-brand-50 px-3 py-1 text-xs font-medium text-brand-800">
                  {v.phrase}
                </span>
              );
            }
            const open = !!openHints[i];
            return (
              <span
                key={i}
                className="inline-flex items-center gap-1.5 rounded-full border border-mint-200 bg-mint-50 px-3 py-1 text-xs font-medium text-mint-800"
                title="Academic word — build your own collocation"
              >
                {v.phrase}
                <button
                  type="button"
                  onClick={() => setOpenHints((prev) => ({ ...prev, [i]: !prev[i] }))}
                  className="rounded-full border border-mint-300 px-1.5 text-[10px] font-semibold text-mint-700 hover:bg-mint-100"
                >
                  {open ? "hide" : "hint?"}
                </button>
                {open && <span className="text-[11px] font-normal text-mint-700">{v.structural_hint}</span>}
              </span>
            );
          })}
        </div>
      )}

      {session.reviewItems.length > 0 && (
        <div className="mt-2">
          <p className="text-xs text-ink-500">Also try to work in a review item if it fits naturally:</p>
          <div className="mt-1 flex flex-wrap gap-2">
            {session.reviewItems.map((r, i) => (
              <span key={i} className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-700">
                {r.phrase}
              </span>
            ))}
          </div>
        </div>
      )}

      <textarea
        className="mt-4 w-full rounded-card border border-ink-400/30 p-3 text-sm outline-none focus:border-brand-500"
        rows={5}
        placeholder="Write your one-paragraph response here…"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />

      {err && <p className="mt-2 text-xs text-rose-600">{err}</p>}

      <button
        type="button"
        className="btn-primary mt-3 disabled:opacity-40"
        disabled={!text.trim() || submitting}
        onClick={handleSubmit}
      >
        {submitting ? "Grading…" : "Submit"}
      </button>
    </div>
  );
}
