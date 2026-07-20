"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

// v8: new — trainer reviews AI-drafted prompts before they can be assigned.
// Editing and approving happen in one action: edit the text if needed, then
// Approve. Nothing here can be assigned to a student until approved.
export default function PromptReviewList({
  prompts,
}: {
  prompts: { id: string; topic: string; type: string; text: string }[];
}) {
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const router = useRouter();

  async function approve(id: string, text: string) {
    setBusy(id);
    await fetch("/api/prompts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, approved: true, text }),
    });
    setBusy(null);
    router.refresh();
  }

  if (prompts.length === 0) {
    return <p className="mt-2 text-sm text-ink-400">No prompts waiting for review.</p>;
  }

  return (
    <div className="mt-3 space-y-3">
      {prompts.map((p) => (
        <div key={p.id} className="rounded-card border border-brand-100 p-4">
          <p className="text-xs font-medium text-brand-600">
            {p.topic} — {p.type.replace(/_/g, " ")}
          </p>
          <textarea
            className="mt-2 h-20 w-full rounded-card border border-brand-100 p-2 text-sm leading-relaxed"
            defaultValue={p.text}
            onChange={(e) => setEdits((cur) => ({ ...cur, [p.id]: e.target.value }))}
          />
          <button
            className="btn-primary mt-2 !py-1.5 !px-4 text-sm"
            disabled={busy === p.id}
            onClick={() => approve(p.id, edits[p.id] ?? p.text)}
          >
            {busy === p.id ? "Approving…" : "Approve"}
          </button>
        </div>
      ))}
    </div>
  );
}
