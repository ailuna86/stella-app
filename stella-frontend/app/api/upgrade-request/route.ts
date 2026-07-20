import { NextResponse } from "next/server";
import { currentUser } from "@/lib/server/auth";
import { addUpgradeRequest, markUpgradeRequestDone } from "@/lib/server/store";

// v9: no real payment processor yet (deliberately deferred until after the
// pilot). This just records intent so it can be fulfilled manually — see
// the "Upgrade requests" section in the trainer console.
export async function POST(req: Request) {
  const user = await currentUser();
  if (!user) return NextResponse.json({ ok: false }, { status: 401 });

  const { requestedPlan } = (await req.json()) as { requestedPlan: "premium" | "gold" };
  if (requestedPlan !== "premium" && requestedPlan !== "gold")
    return NextResponse.json({ ok: false, error: "Invalid plan." }, { status: 400 });

  addUpgradeRequest({
    organizationId: user.organizationId,
    userId: user.id,
    requestedPlan,
  });
  return NextResponse.json({ ok: true });
}

// Trainer marks a request as handled once they've followed up/invoiced manually.
export async function PATCH(req: Request) {
  const user = await currentUser();
  if (!user || user.role !== "trainer")
    return NextResponse.json({ ok: false }, { status: 403 });

  const { id } = (await req.json()) as { id: string };
  if (!id) return NextResponse.json({ ok: false, error: "Missing id." }, { status: 400 });
  markUpgradeRequestDone(id);
  return NextResponse.json({ ok: true });
}
