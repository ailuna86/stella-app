import Link from "next/link";
import { redirect } from "next/navigation";
import { currentUser } from "@/lib/server/auth";
import { activeAssignmentFor } from "@/lib/server/store";
import SubmitForm from "@/components/SubmitForm";
import ConsentNotice from "@/components/ConsentNotice";

export default async function SubmitPage() {
  const user = await currentUser();
  if (!user) return null;
  if (!user.intake && user.role === "student") redirect("/survey");
  if (!user.consentAt) return <ConsentNotice />;

  if (user.pilotEndsAt && new Date(user.pilotEndsAt) < new Date()) {
    return (
      <div className="mx-auto max-w-xl py-10 text-center">
        <h1 className="text-2xl font-semibold">Your free pilot week has ended</h1>
        <p className="mt-2 text-sm text-ink-600">
          Your past reports and practice history are still available. Subscribe to keep
          submitting new essays.
        </p>
        <Link href="/upgrade" className="btn-primary mt-4 inline-flex">
          See plans
        </Link>
      </div>
    );
  }

  const assignment = user.entitlements.can_self_submit
    ? null
    : activeAssignmentFor(user.id);

  if (!user.entitlements.can_self_submit && !assignment) {
    return (
      <div className="mx-auto max-w-xl py-10 text-center">
        <h1 className="text-2xl font-semibold">No assignment yet</h1>
        <p className="mt-2 text-sm text-ink-600">
          Essay evaluation opens when your trainer sends an assignment. Daily practice
          is always available from your dashboard.
        </p>
      </div>
    );
  }

  return (
    <SubmitForm
      lockedPrompt={assignment?.prompt ?? null}
      assignmentId={assignment?.id ?? null}
      evaluationsLeft={user.entitlements.evaluations_left}
      isGold={user.plan === "gold"}
    />
  );
}
