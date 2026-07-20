import Link from "next/link";
import { redirect } from "next/navigation";
import { currentUser } from "@/lib/server/auth";

// v9: landing spot for students who picked "General Training" in the
// survey — the product only supports Academic right now. We already have
// their verified email (they signed in before the survey), so "notify me"
// is implicit: nothing further to collect here.
// v14: rebuilt to match the Stitch coming_soon_general screen — icon badge
// with a "still building" accent, warmer copy, and a confirmation card
// instead of a plain paragraph.
export default async function GeneralWaitlist() {
  const user = await currentUser();
  if (!user) return null;
  if (user.intake?.examType !== "general") redirect("/dashboard");

  return (
    <div className="relative flex min-h-[70vh] items-center justify-center overflow-hidden px-5 py-16">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute left-1/4 top-1/4 h-64 w-64 rounded-full bg-brand-100 opacity-40 blur-[80px]" />
        <div className="absolute bottom-1/4 right-1/4 h-80 w-80 rounded-full bg-brand-50 opacity-60 blur-[100px]" />
      </div>

      <div className="relative z-10 w-full max-w-lg text-center">
        <div className="relative mx-auto mb-8 inline-block">
          <div className="mx-auto flex h-40 w-40 items-center justify-center rounded-full bg-brand-100 shadow-sm">
            <span className="material-symbols-outlined text-[64px] text-brand-600">school</span>
          </div>
          <div className="absolute -bottom-2 -right-2 rounded-2xl border-4 border-white bg-amber-500 p-3 shadow-lg">
            <span className="material-symbols-outlined text-white">construction</span>
          </div>
        </div>

        <h1 className="text-2xl font-semibold text-ink-900">We&apos;re still crafting this!</h1>
        <p className="mx-auto mt-3 max-w-md text-base leading-relaxed text-ink-600">
          General Training isn&apos;t quite ready yet — we&apos;re putting all our energy into
          the Academic experience first.
        </p>

        <div className="mx-auto mt-8 inline-flex items-center gap-4 rounded-xl border border-brand-100 bg-white/80 p-5 text-left shadow-soft">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-mint-50">
            <span className="material-symbols-outlined text-mint-600">mark_email_read</span>
          </div>
          <div>
            <p className="text-sm font-medium text-ink-900">Don&apos;t worry!</p>
            <p className="text-xs text-ink-400">
              We&apos;ve saved your details and will email {user.email} the moment General
              Training is ready.
            </p>
          </div>
        </div>

        <div className="mt-8">
          <Link href="/dashboard" className="btn-primary inline-flex items-center gap-2">
            <span className="material-symbols-outlined text-[20px]">arrow_back</span>
            Back to dashboard
          </Link>
        </div>
      </div>
    </div>
  );
}
