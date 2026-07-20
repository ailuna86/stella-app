"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function AddStudentForm() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [plan, setPlan] = useState<"gold" | "premium_pilot">("gold");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const router = useRouter();

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    const res = await fetch("/api/students", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, email, plan }),
    });
    setBusy(false);
    if (res.ok) {
      setName("");
      setEmail("");
      router.refresh();
    } else {
      const data = await res.json().catch(() => ({}));
      setErr(data.error ?? "Could not add student.");
    }
  }

  return (
    <form onSubmit={add} className="mt-3 flex flex-wrap gap-2">
      <input
        required
        placeholder="Name"
        className="flex-1 rounded-card border border-brand-100 p-3 text-sm"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <input
        required
        type="email"
        placeholder="Email"
        className="flex-1 rounded-card border border-brand-100 p-3 text-sm"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
      />
      <select
        className="rounded-card border border-brand-100 p-3 text-sm"
        value={plan}
        onChange={(e) => setPlan(e.target.value as "gold" | "premium_pilot")}
      >
        <option value="gold">Gold pilot</option>
        <option value="premium_pilot">Premium pilot</option>
      </select>
      <button className="btn-primary" disabled={busy}>
        {busy ? "Adding..." : "Add student"}
      </button>
      {err && <p className="w-full text-sm text-rose-600">{err}</p>}
    </form>
  );
}