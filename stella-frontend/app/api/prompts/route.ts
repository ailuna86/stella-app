import { NextResponse } from "next/server";
import { currentUser } from "@/lib/server/auth";
import { setPromptApproval } from "@/lib/server/store";

// v8: new — lets a trainer approve/edit a drafted prompt (or un-approve one
// already live) before it's offered in the assignment picker.
export async function POST(req: Request) {
  const user = await currentUser();
  if (!user || user.role !== "trainer") return NextResponse.json({ ok: false }, { status: 403 });

  const { id, approved, text } = await req.json();
  if (!id) return NextResponse.json({ ok: false, error: "Missing id." }, { status: 400 });
  setPromptApproval(id, !!approved, text);
  return NextResponse.json({ ok: true });
}
