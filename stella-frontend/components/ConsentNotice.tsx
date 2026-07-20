"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

// v8: new — one-time AI-processing disclosure shown before a user's first
// essay submission. Applies to every user (pilot or paid), permanently.
// v14: rebuilt to match the Stitch ai_consent screen — glass card over soft
// background blobs, icon badge, and a trust bento grid — while keeping the
// actual disclosure copy accurate to what the app really does (no
// compliance claims like "GDPR compliant" that haven't actually been
// verified — Stitch's mockup copy included one, this version doesn't).
export default function ConsentNotice() {
  const [busy, setBusy] = useState(false);
  const router = useRouter();

  async function accept() {
    setBusy(true);
    await fetch("/api/consent", { method: "POST" });
    setBusy(false);
    router.refresh();
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden px-5 py-10">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute -left-[10%] -top-[10%] h-[40%] w-[40%] rounded-full bg-brand-200/20 blur-[120px]" />
        <div className="absolute -bottom-[10%] -right-[10%] h-[50%] w-[50%] rounded-full bg-brand-100/30 blur-[150px]" />
      </div>

      <main className="relative z-10 w-full max-w-xl">
        <div className="flex flex-col items-center rounded-[32px] bg-white/80 p-8 text-center shadow-soft backdrop-blur-md md:p-12">
          <div className="relative mb-8">
            <div className="flex h-24 w-24 items-center justify-center rounded-full bg-brand-100">
              <span className="material-symbols-outlined text-[48px] text-brand-600">neurology</span>
            </div>
            <div className="absolute -right-2 -top-2 flex h-10 w-10 items-center justify-center rounded-full bg-white shadow-sm">
              <span className="material-symbols-outlined text-[20px] text-mint-600">verified_user</span>
            </div>
          </div>

          <header className="mb-8">
            <h1 className="mb-3 text-2xl font-semibold text-ink-900">
              Before you submit your first essay
            </h1>
            <p className="text-base leading-relaxed text-ink-600">
              To give you a detailed band score and feedback, your essay is sent securely to an
              external AI provider for scoring.
            </p>
          </header>

          <div className="mb-10 grid w-full grid-cols-1 gap-4 text-left">
            <div className="flex items-start gap-4 rounded-2xl border border-brand-100 bg-brand-50/40 p-5">
              <span className="material-symbols-outlined mt-1 text-brand-600">psychology</span>
              <div>
                <h3 className="text-sm font-medium text-ink-900">AI-powered scoring</h3>
                <p className="text-xs text-ink-400">
                  Your essay is analyzed across all four IELTS criteria to generate your band
                  score and feedback.
                </p>
              </div>
            </div>
            <div className="flex items-start gap-4 rounded-2xl border border-brand-100 bg-brand-50/40 p-5">
              <span className="material-symbols-outlined mt-1 text-brand-600">visibility</span>
              <div>
                <h3 className="text-sm font-medium text-ink-900">Trainer visibility</h3>
                <p className="text-xs text-ink-400">
                  Your trainer can see your essay and its evaluation, to support your learning.
                </p>
              </div>
            </div>
            <div className="flex items-start gap-4 rounded-2xl border border-brand-100 bg-brand-50/40 p-5">
              <span className="material-symbols-outlined mt-1 text-brand-600">lock</span>
              <div>
                <h3 className="text-sm font-medium text-ink-900">Sent securely</h3>
                <p className="text-xs text-ink-400">
                  Your essay text is transmitted securely to the AI provider for scoring only.
                </p>
              </div>
            </div>
          </div>

          <div className="w-full">
            <button
              className="btn-primary flex w-full items-center justify-center gap-2 !py-4"
              onClick={accept}
              disabled={busy}
            >
              {busy ? "Saving…" : "I understand and agree"}
              {!busy && <span className="material-symbols-outlined text-[20px]">arrow_forward</span>}
            </button>
            <p className="mt-4 text-xs text-ink-400">This only needs your confirmation once.</p>
          </div>
        </div>
      </main>
    </div>
  );
}
