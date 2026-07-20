"use client";

import { useState } from "react";

export default function UpgradeRequestButtons({
  requestPlan,
  disabled,
}: {
  requestPlan: "premium" | "gold";
  disabled?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);

  async function request() {
    setBusy(true);
    await fetch("/api/upgrade-request", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ requestedPlan: requestPlan }),
    });
    setBusy(false);
    setSent(true);
  }

  if (disabled) return null;
  if (sent)
    return (
      <p className="btn-secondary mt-4 inline-flex cursor-default">
        Request sent — we'll follow up
      </p>
    );

  return (
    <button className="btn-primary mt-4 w-full" onClick={request} disabled={busy}>
      {busy ? "Sending…" : "Request this plan"}
    </button>
  );
}
