"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export interface UpgradeRequestRow {
  id: string;
  studentName: string;
  studentEmail: string;
  requestedPlan: "premium" | "gold";
  createdAt: string;
}

export default function UpgradeRequestsList({ requests }: { requests: UpgradeRequestRow[] }) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const router = useRouter();

  async function markDone(id: string) {
    setBusyId(id);
    await fetch("/api/upgrade-request", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    setBusyId(null);
    router.refresh();
  }

  if (requests.length === 0)
    return <p className="mt-2 text-sm text-ink-400">No pending requests.</p>;

  return (
    <div className="mt-3 space-y-2">
      {requests.map((r) => (
        <div
          key={r.id}
          className="flex items-center justify-between rounded-card border border-brand-100 p-3 text-sm"
        >
          <span>
            <span className="font-medium">{r.studentName}</span> ({r.studentEmail}) wants{" "}
            <span className="font-medium capitalize">{r.requestedPlan}</span> ·{" "}
            {new Date(r.createdAt).toLocaleDateString()}
          </span>
          <button
            className="btn-secondary"
            onClick={() => markDone(r.id)}
            disabled={busyId === r.id}
          >
            {busyId === r.id ? "…" : "Mark handled"}
          </button>
        </div>
      ))}
    </div>
  );
}
