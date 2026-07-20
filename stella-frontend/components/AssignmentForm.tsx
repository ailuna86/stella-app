"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

// v8: prompts are now passed in as a prop (fetched from the database, only
// trainer-approved ones) instead of importing a static file — this is what
// lets the trainer approve/edit prompts in the console and have it take
// effect immediately.
export default function AssignmentForm({
  students,
  prompts,
}: {
  students: { id: string; name: string }[];
  prompts: { id: string; topic: string; type: string; text: string }[];
}) {
  const [prompt, setPrompt] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [selected, setSelected] = useState<string[]>(students.map((s) => s.id));
  const [busy, setBusy] = useState(false);
  const router = useRouter();

  async function create() {
    setBusy(true);
    await fetch("/api/assignments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, dueDate, studentIds: selected }),
    });
    setBusy(false);
    setPrompt("");
    router.refresh();
  }

  function toggle(id: string) {
    setSelected((cur) =>
      cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]
    );
  }

  return (
    <div className="mt-3 space-y-3">
      <select
        className="w-full rounded-card border border-brand-100 p-3 text-sm"
        value=""
        onChange={(e) => e.target.value && setPrompt(e.target.value)}
      >
        <option value="">Insert from prompt bank (optional)…</option>
        {prompts.map((p) => (
          <option key={p.id} value={p.text}>
            {p.topic} — {p.type.replace(/_/g, " ")}
          </option>
        ))}
      </select>
      <textarea
        className="h-24 w-full rounded-card border border-brand-100 p-3 text-sm leading-relaxed"
        placeholder="Task 2 prompt for the assignment…"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
      />
      <div className="flex flex-wrap items-center gap-3">
        <input
          type="date"
          className="rounded-card border border-brand-100 p-2.5 text-sm"
          value={dueDate}
          onChange={(e) => setDueDate(e.target.value)}
        />
        {students.map((s) => (
          <label key={s.id} className="flex items-center gap-1.5 text-sm text-ink-600">
            <input
              type="checkbox"
              checked={selected.includes(s.id)}
              onChange={() => toggle(s.id)}
            />
            {s.name}
          </label>
        ))}
      </div>
      <button
        className="btn-primary"
        onClick={create}
        disabled={busy || !prompt.trim() || !dueDate || selected.length === 0}
      >
        {busy ? "Creating…" : "Send assignment"}
      </button>
    </div>
  );
}
