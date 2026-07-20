"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function AddTrainerForm() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState(false);
  const router = useRouter();

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    setOk(false);
    const res = await fetch("/api/trainers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, email }),
    });
    setBusy(false);
    if (res.ok) {
      setName("");
      setEmail("");
      setOk(true);
      router.refresh();
    } else {
      const data = await res.json().catch(() => ({}));
      setErr(data.error ?? "Could not add trainer.");
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
      <button className="btn-primary" disabled={busy}>
        {busy ? "Adding…" : "Add trainer"}
      </button>
      {err && <p className="w-full text-sm text-rose-600">{err}</p>}
      {ok && (
        <p className="w-full text-sm text-emerald-600">
          Trainer added — they sign in the same way, with their email and a confirmation code.
        </p>
      )}
    </form>
  );
}
