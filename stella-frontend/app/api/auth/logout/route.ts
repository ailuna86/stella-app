import { NextResponse } from "next/server";
import { clearSession } from "@/lib/server/auth";

export async function POST(req: Request) {
  await clearSession();
  return NextResponse.redirect(new URL("/", req.url), 303);
}
