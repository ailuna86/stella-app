// v8: currentUser() is now async because it unseals a signed iron-session
// cookie instead of reading a raw ID (see session.ts for why). Every caller
// in the app was updated to `await currentUser()`.
import { getSession } from "./session";
import { getUserById } from "./store";
import type { User } from "@/lib/types";

export async function currentUser(): Promise<User | undefined> {
  const session = await getSession();
  return session.userId ? getUserById(session.userId) : undefined;
}

export async function setSessionUser(userId: string) {
  const session = await getSession();
  session.userId = userId;
  await session.save();
}

export async function clearSession() {
  const session = await getSession();
  session.destroy();
}
