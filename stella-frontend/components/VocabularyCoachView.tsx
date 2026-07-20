"use client";

import { useMemo, useState } from "react";

interface LretUnit {
  unitText: string;
  context: string;
  confidence: number | null;
  reason: string;
  suggestions: string[];
  clarificationGuidance: string[];
  keepType: string;
}

type Filter = "fix" | "enhance" | "clarify" | "keep";

const FILTER_META: Record<
  Filter,
  { label: string; chipBg: string; chipText: string; ring: string; border: string; iconBg: string }
> = {
  fix: {
    label: "Fix",
    chipBg: "bg-rose-50",
    chipText: "text-rose-600",
    ring: "ring-rose-400",
    border: "border-rose-400",
    iconBg: "bg-rose-50 text-rose-600",
  },
  enhance: {
    label: "Enhance",
    chipBg: "bg-brand-50",
    chipText: "text-brand-800",
    ring: "ring-brand-600",
    border: "border-brand-400",
    iconBg: "bg-brand-50 text-brand-600",
  },
  clarify: {
    label: "Clarify",
    chipBg: "bg-amber-50",
    chipText: "text-amber-700",
    ring: "ring-amber-500",
    border: "border-amber-400",
    iconBg: "bg-amber-50 text-amber-700",
  },
  keep: {
    label: "Keep",
    chipBg: "bg-mint-50",
    chipText: "text-mint-600",
    ring: "ring-mint-400",
    border: "border-mint-400",
    iconBg: "bg-mint-50 text-mint-600",
  },
};

// Renders the sentence context with the flagged phrase underlined/colored in
// place, matching the Stitch design's inline-highlight style rather than
// showing the phrase floating separately from its sentence.
function ContextLine({ context, unitText, colorClass }: { context: string; unitText: string; colorClass: string }) {
  if (!context || !unitText) return context ? <>{context}</> : null;
  const idx = context.toLowerCase().indexOf(unitText.toLowerCase());
  if (idx === -1) return <>{context}</>;
  const before = context.slice(0, idx);
  const match = context.slice(idx, idx + unitText.length);
  const after = context.slice(idx + unitText.length);
  return (
    <>
      {before}
      <span className={`font-semibold underline decoration-2 underline-offset-4 ${colorClass}`}>{match}</span>
      {after}
    </>
  );
}

function ClarifyInput({ guidance }: { guidance: string[] }) {
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState(false);
  return (
    <div className="mt-3 space-y-2">
      {guidance.length > 0 && (
        <p className="text-sm text-ink-600">
          {guidance[0]}
          {guidance.length > 1 && (
            <span className="text-ink-400"> ({guidance.slice(1).join(" · ")})</span>
          )}
        </p>
      )}
      {saved ? (
        <p className="flex items-center gap-1.5 text-xs text-mint-600">
          <span aria-hidden>✓</span> Saved — keep this in mind when you revise this sentence.
        </p>
      ) : (
        <div className="flex gap-2">
          <input
            className="flex-1 rounded-full border border-ink-400/40 px-4 py-1.5 text-sm outline-none focus:border-amber-500"
            placeholder="Type your clarification…"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          <button
            type="button"
            className="shrink-0 rounded-full bg-amber-100 px-3 py-1.5 text-xs font-medium text-amber-800 disabled:opacity-40"
            disabled={!value.trim()}
            onClick={() => setSaved(true)}
          >
            Save
          </button>
        </div>
      )}
    </div>
  );
}

export default function VocabularyCoachView({
  counts,
  units,
  reviseHref,
}: {
  counts: { fix: number; enhance: number; clarify: number; keep: number };
  units: { fix: LretUnit[]; enhance: LretUnit[]; clarify: LretUnit[]; keep: LretUnit[] };
  reviseHref: string | null;
}) {
  const defaultFilter: Filter =
    counts.fix > 0 ? "fix" : counts.clarify > 0 ? "clarify" : counts.enhance > 0 ? "enhance" : "keep";
  const [filter, setFilter] = useState<Filter>(defaultFilter);
  const [showAllKeep, setShowAllKeep] = useState(false);

  const active = units[filter];
  const meta = FILTER_META[filter];

  const visibleKeep = useMemo(
    () => (showAllKeep ? active : active.slice(0, 6)),
    [active, showAllKeep, filter]
  );

  return (
    <div>
      <div className="mt-4 flex flex-wrap gap-3">
        {(Object.keys(FILTER_META) as Filter[]).map((f) => {
          const m = FILTER_META[f];
          const isActive = filter === f;
          return (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={`flex min-w-[80px] flex-1 flex-col items-center justify-center rounded-card p-3 transition ${m.chipBg} ${m.chipText} ${
                isActive ? `ring-2 ring-offset-2 ${m.ring}` : ""
              }`}
            >
              <span className="text-xl font-semibold">{counts[f]}</span>
              <span className="text-xs font-medium uppercase tracking-wide">{m.label}</span>
            </button>
          );
        })}
      </div>

      <div className="mt-5 space-y-3">
        {active.length === 0 && (
          <p className="rounded-card border border-brand-100 p-4 text-sm text-ink-600">
            Nothing in this category for this essay.
          </p>
        )}

        {filter !== "keep" &&
          active.map((u, i) => (
            <div key={i} className={`card border-l-4 ${meta.border} shadow-soft`}>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <code className={`rounded px-2 py-0.5 text-sm font-medium ${meta.chipBg} ${meta.chipText}`}>
                    "{u.unitText}"
                  </code>
                  {u.context && (
                    <p className="mt-1.5 text-sm italic text-ink-600">
                      <ContextLine context={u.context} unitText={u.unitText} colorClass={meta.chipText} />
                    </p>
                  )}
                </div>
                <span className={`shrink-0 rounded-full p-1.5 ${meta.iconBg}`} aria-hidden>
                  {filter === "fix" ? "!" : filter === "enhance" ? "✦" : "?"}
                </span>
              </div>

              {filter === "clarify" ? (
                <ClarifyInput guidance={u.clarificationGuidance} />
              ) : (
                (u.suggestions.length > 0 || u.reason) && (
                  <div className="mt-3 rounded-card bg-brand-50/40 p-3">
                    {u.reason && <p className="text-sm text-ink-700">{u.reason}</p>}
                    {u.suggestions.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-2">
                        {u.suggestions.map((s, si) => (
                          <span
                            key={si}
                            className={`rounded-full border px-3 py-1 text-xs font-medium ${
                              filter === "fix"
                                ? "border-rose-200 bg-white text-rose-700"
                                : "border-brand-200 bg-white text-brand-800"
                            }`}
                          >
                            {s}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                )
              )}
            </div>
          ))}

        {filter === "keep" &&
          visibleKeep.map((u, i) => (
            <div key={i} className="flex items-center justify-between rounded-card border border-mint-200 bg-white p-3">
              <div className="flex items-center gap-3">
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-mint-50 text-mint-600">
                  ✓
                </span>
                <div>
                  <code className="text-sm font-medium text-mint-700">"{u.unitText}"</code>
                  {u.keepType && (
                    <p className="text-[11px] uppercase tracking-wide text-ink-400">{u.keepType}</p>
                  )}
                </div>
              </div>
            </div>
          ))}
        {filter === "keep" && !showAllKeep && active.length > visibleKeep.length && (
          <button
            type="button"
            onClick={() => setShowAllKeep(true)}
            className="text-sm font-medium text-brand-600 hover:text-brand-800"
          >
            Show all {active.length} kept phrases
          </button>
        )}
      </div>

      {reviseHref && (
        <div className="card-gold mt-6 flex items-center justify-between gap-4 shadow-soft">
          <div>
            <p className="text-sm text-brand-900">Working on your revision?</p>
            <p className="text-xs text-brand-600">These same phrases are highlighted there too.</p>
          </div>
          <a href={reviseHref} className="btn-primary shrink-0">
            Open workspace
          </a>
        </div>
      )}
    </div>
  );
}
