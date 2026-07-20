import { redirect } from "next/navigation";
import { currentUser } from "@/lib/server/auth";
import UpgradeRequestButtons from "@/components/UpgradeRequestButtons";

// v9: replaces the old marketing-only page. Real payment is deliberately
// deferred (pilot week is free) — "Request this plan" records intent via
// /api/upgrade-request for manual follow-up (see the trainer console's
// "Upgrade requests" section). Gold's feature bullets carry over from the
// previous version (Writing Coach, LRET, revision loop) — these match what
// the Gold pipeline actually does; Premium's bullets describe what
// pipeline_runner_v14j.py's report format supports. Pricing itself isn't
// set yet, so this shows region-aware "request pricing" instead of numbers.
export default async function UpgradePage() {
  const user = await currentUser();
  if (!user) return null;
  if (user.role === "trainer") redirect("/trainer");

  return (
    <div className="mx-auto max-w-4xl px-4 py-12">
      {(user.plan === "premium_pilot" || user.plan === "gold") && user.pilotEndsAt && (
        <div className="mb-8 rounded-card bg-brand-50 px-4 py-3 text-center text-sm text-brand-800">
          Your free pilot access runs until {user.pilotEndsAt} — subscribing is optional.
        </div>
      )}

      <div className="mb-10 text-center">
        <h1 className="text-3xl font-semibold text-ink-900">Invest in your future score</h1>
        <p className="mx-auto mt-3 max-w-xl text-base text-ink-600">
          Choose the plan that fits your study pace. Pricing depends on where you live —
          request a plan below and you&apos;ll hear back with pricing for your region.
        </p>
      </div>

      <div className="grid items-stretch gap-6 md:grid-cols-2">
        <PlanCard
          name="Premium"
          tagline="Essential tools for independent learners aiming for a high band score."
          features={[
            "Band score across all 4 IELTS writing criteria",
            "Sentence-level error feedback with corrections",
            "Practice exercises from your own mistakes",
          ]}
          current={user.plan === "premium" || user.plan === "premium_pilot"}
          requestPlan="premium"
        />
        <PlanCard
          name="Gold"
          tagline="The complete package — daily coaching and a personalized roadmap."
          features={[
            "Writing Coach — daily guided rewriting of your own sentences",
            "Vocabulary Coach (LRET) for lexical range and precision",
            "Full revision loop with adjudicated, higher-confidence scoring",
            "Multi-week personalized study plan that adapts as you improve",
          ]}
          current={user.plan === "gold"}
          requestPlan="gold"
          highlight
        />
      </div>

      <div className="mx-auto mt-16 max-w-xl">
        <h2 className="mb-6 text-center text-lg font-semibold text-ink-900">Common questions</h2>
        <div className="space-y-3">
          <details className="rounded-card border border-brand-100 bg-white p-4">
            <summary className="flex cursor-pointer list-none items-center justify-between text-sm font-medium text-ink-800">
              Can I switch plans later?
              <span className="text-ink-400">⌄</span>
            </summary>
            <p className="mt-3 text-sm text-ink-600">
              Yes — your trainer can move you between plans at any time.
            </p>
          </details>
          <details className="rounded-card border border-brand-100 bg-white p-4">
            <summary className="flex cursor-pointer list-none items-center justify-between text-sm font-medium text-ink-800">
              What happens after I request a plan?
              <span className="text-ink-400">⌄</span>
            </summary>
            <p className="mt-3 text-sm text-ink-600">
              Your trainer sees the request and follows up with pricing for your region — no
              payment is taken automatically.
            </p>
          </details>
        </div>
      </div>
    </div>
  );
}

function PlanCard({
  name,
  tagline,
  features,
  current,
  requestPlan,
  highlight,
}: {
  name: string;
  tagline: string;
  features: string[];
  current: boolean;
  requestPlan: "premium" | "gold";
  highlight?: boolean;
}) {
  return (
    <div
      className={`relative flex flex-col rounded-card border bg-white p-7 transition hover:scale-[1.01] ${
        highlight ? "border-2 border-brand-400 shadow-soft" : "border-brand-100"
      }`}
    >
      {highlight && (
        <span className="absolute -top-3.5 left-1/2 -translate-x-1/2 rounded-full bg-brand-600 px-4 py-1 text-xs font-bold uppercase tracking-wide text-white shadow-sm">
          Recommended
        </span>
      )}
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-ink-900">{name}</h2>
        {current && (
          <span className="rounded-full bg-mint-50 px-2 py-0.5 text-xs font-medium text-mint-600">
            Your plan
          </span>
        )}
      </div>
      <p className="mt-2 text-sm text-ink-600">{tagline}</p>
      <ul className="mt-5 flex-1 space-y-3 text-sm text-ink-800">
        {features.map((f) => (
          <li key={f} className="flex items-start gap-2.5">
            <span className="material-symbols-outlined text-[20px] text-mint-600">check_circle</span>
            {f}
          </li>
        ))}
      </ul>
      <UpgradeRequestButtons requestPlan={requestPlan} disabled={current} />
    </div>
  );
}
