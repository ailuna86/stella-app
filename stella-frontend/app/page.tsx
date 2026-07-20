import { redirect } from "next/navigation";
import { currentUser } from "@/lib/server/auth";

export default async function Home() {
  const user = await currentUser();
  if (!user) return null; // layout renders the login gate
  if (user.role === "trainer") redirect("/trainer");
  if (!user.intake) redirect("/survey");
  if (user.intake.examType === "general") redirect("/general-waitlist");
  redirect("/dashboard");
}
