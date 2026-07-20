"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";

// v4 auth: email → 6-digit confirmation code → in. First-time students are
// then routed to the intake survey by the server (nav hidden until done).
// v6: on-screen devCode fallback when SMTP/email isn't configured.
// v7: SMTP send failures no longer crash the request.
// v8: request-code no longer reveals whether an email is registered (fixes
// a user-enumeration leak) — it always "succeeds" and moves to the code
// step. An email that was never registered will just never get a real code
// and will fail at the verify step instead, same as an expired code would.
// v14: rebuilt to match the Stitch sign_in screen exactly — glass card over
// soft background blobs, individual 6-box OTP input with auto-advance,
// reassuring icon callout, and footer links, instead of the plainer single
// -input version this used to be.
export default function LoginGate() {
  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [digits, setDigits] = useState<string[]>(Array(6).fill(""));
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [devCode, setDevCode] = useState("");
  const router = useRouter();
  const inputRefs = useRef<Array<HTMLInputElement | null>>([]);
  const code = digits.join("");

  async function requestCode(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    setDevCode("");
    const res = await fetch("/api/auth/request-code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    setBusy(false);
    if (res.ok) {
      const data = await res.json().catch(() => ({}));
      if (!data.emailed && data.devCode) setDevCode(data.devCode);
      setStep("code");
      setTimeout(() => inputRefs.current[0]?.focus(), 50);
    } else {
      const data = await res.json().catch(() => ({}));
      setErr(data.error ?? "Something went wrong. Please try again.");
    }
  }

  async function verify(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    const res = await fetch("/api/auth/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, code }),
    });
    setBusy(false);
    if (res.ok) router.refresh();
    else
      setErr(
        "Code doesn't match, has expired, or this email isn't registered for the pilot. Ask your trainer if you're not sure."
      );
  }

  function setDigit(i: number, value: string) {
    const v = value.replace(/\D/g, "").slice(-1);
    setDigits((d) => {
      const next = [...d];
      next[i] = v;
      return next;
    });
    if (v && i < 5) inputRefs.current[i + 1]?.focus();
  }

  function onKeyDown(i: number, e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Backspace" && !digits[i] && i > 0) inputRefs.current[i - 1]?.focus();
  }

  return (
    <div className="relative flex min-h-screen w-full items-center justify-center overflow-hidden p-5">
      {/* Atmospheric background blobs, matching Stitch */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -left-[5%] -top-[10%] h-[40vw] w-[40vw] rounded-full bg-brand-600/5 blur-3xl" />
        <div className="absolute -right-[10%] top-[40%] h-[50vw] w-[50vw] rounded-full bg-brand-200/10 blur-3xl" />
        <div className="absolute bottom-[-10%] left-[20%] h-[35vw] w-[35vw] rounded-full bg-brand-100/20 blur-3xl" />
      </div>

      <main className="z-10 w-full max-w-[480px]">
        <div className="mb-10 text-center">
          <h1 className="mb-2 text-3xl font-bold tracking-tight text-brand-800">ST.ELLA</h1>
          <p className="text-sm text-ink-400">Your supportive path to IELTS excellence.</p>
        </div>

        <div className="rounded-2xl bg-white/90 p-8 shadow-soft backdrop-blur-md md:p-10">
          {step === "email" ? (
            <form onSubmit={requestCode} className="space-y-6">
              <div className="space-y-2 text-center">
                <h2 className="text-xl font-semibold text-ink-900">Welcome back</h2>
                <p className="text-sm text-ink-400">
                  Enter your student email to receive a secure sign-in code.
                </p>
              </div>
              <div className="space-y-4">
                <div>
                  <label htmlFor="email" className="mb-1 ml-1 block text-xs font-medium text-ink-600">
                    Email address
                  </label>
                  <input
                    id="email"
                    type="email"
                    required
                    placeholder="student@university.edu"
                    className="w-full rounded-lg border border-brand-100 bg-brand-50/40 px-4 py-3 text-sm outline-none focus:border-brand-600 focus:ring-4 focus:ring-brand-600/5"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                  />
                </div>
                {err && <p className="text-sm text-rose-600">{err}</p>}
                <button className="btn-primary flex w-full items-center justify-center gap-2 !py-4" disabled={busy}>
                  {busy ? "Sending code…" : "Send code"}
                  <span className="material-symbols-outlined text-[20px]">arrow_forward</span>
                </button>
              </div>
              <div className="flex items-start gap-3 rounded-xl border border-brand-100 bg-brand-50/50 p-4">
                <span className="material-symbols-outlined mt-0.5 text-[20px] text-brand-600">verified_user</span>
                <p className="text-xs leading-relaxed text-ink-600">
                  We use one-time codes instead of passwords. It&apos;s more secure, easier to
                  remember, and keeps your progress safe without the stress of forgotten
                  credentials.
                </p>
              </div>
            </form>
          ) : (
            <form onSubmit={verify} className="space-y-8">
              <div className="space-y-2 text-center">
                <button
                  type="button"
                  className="mx-auto mb-4 flex items-center justify-center gap-1 text-xs font-medium text-brand-600 transition-colors hover:text-brand-800"
                  onClick={() => {
                    setStep("email");
                    setDigits(Array(6).fill(""));
                    setErr("");
                  }}
                >
                  <span className="material-symbols-outlined text-[16px]">arrow_back</span>
                  Back to email
                </button>
                <h2 className="text-xl font-semibold text-ink-900">Verify your identity</h2>
                <p className="text-sm text-ink-400">
                  If <span className="font-medium text-ink-800">{email}</span> is registered, a
                  6-digit code was just sent.
                </p>
              </div>

              {devCode && (
                <p className="rounded-card border border-amber-300 bg-amber-50 p-3 text-center text-sm text-amber-800">
                  Email sending isn&apos;t configured on this server yet, so here&apos;s your
                  code directly:{" "}
                  <span className="font-mono text-base font-semibold">{devCode}</span>
                </p>
              )}

              <div className="space-y-6">
                <div className="flex justify-between gap-2 md:gap-4">
                  {digits.map((d, i) => (
                    <input
                      key={i}
                      ref={(el) => {
                        inputRefs.current[i] = el;
                      }}
                      type="text"
                      inputMode="numeric"
                      maxLength={1}
                      value={d}
                      onChange={(e) => setDigit(i, e.target.value)}
                      onKeyDown={(e) => onKeyDown(i, e)}
                      className="aspect-square w-full rounded-xl border border-brand-100 bg-brand-50/40 text-center text-xl font-semibold outline-none focus:border-brand-600 focus:ring-4 focus:ring-brand-600/5"
                    />
                  ))}
                </div>
                {err && <p className="text-center text-sm text-rose-600">{err}</p>}
                <div className="space-y-4">
                  <button className="btn-primary w-full !py-4" disabled={busy || code.length !== 6}>
                    {busy ? "Checking…" : "Sign in to ST.ELLA"}
                  </button>
                  <p className="text-center text-sm text-ink-600">
                    Didn&apos;t receive it?{" "}
                    <button
                      type="button"
                      className="font-medium text-brand-600 underline decoration-brand-600/30 underline-offset-4 hover:text-brand-800"
                      onClick={() => {
                        setStep("email");
                        setDigits(Array(6).fill(""));
                        setErr("");
                      }}
                    >
                      Resend code
                    </button>
                  </p>
                </div>
              </div>

              <div className="flex items-center justify-center gap-2 text-ink-400">
                <span className="material-symbols-outlined text-[18px]">lock</span>
                <span className="text-xs">End-to-end encrypted session</span>
              </div>
            </form>
          )}
        </div>

        <p className="mt-4 text-center text-xs text-ink-400">
          Private pilot. Accounts are created by your trainer.
        </p>
        <footer className="mt-6 space-x-6 text-center">
          <span className="text-xs text-ink-400">Help Center</span>
          <span className="text-xs text-ink-400">Privacy Policy</span>
          <span className="text-xs text-ink-400">Terms of Use</span>
        </footer>
      </main>
    </div>
  );
}
