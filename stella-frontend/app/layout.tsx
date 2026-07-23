import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Link from "next/link";
import { currentUser } from "@/lib/server/auth";
import LoginGate from "@/components/LoginGate";
import "./globals.css";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: {
    default: "ST.ELLA — English Language Learning Assistant",
    template: "%s | ST.ELLA",
  },
  description:
    "ST.ELLA evaluates IELTS essays on all four official criteria and trains exactly what holds you back.",
  robots: { index: false },
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const user = await currentUser();

  return (
    <html lang="en">
      <head>
        <link
          href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className={inter.className}>
        {!user ? (
          <main className="mx-auto flex min-h-screen max-w-md items-center px-4">
            <LoginGate />
          </main>
        ) : (
          <>
            <header className="border-b border-brand-100">
              <nav className="mx-auto flex max-w-5xl items-center justify-between px-4 py-4">
                <Link href="/" className="text-lg font-semibold text-brand-800">
                  ST.ELLA
                </Link>
                <div className="flex items-center gap-4 text-sm text-ink-600">
                  {user.role === "trainer" ? (
                    <Link href="/trainer" className="hover:text-brand-800">
                      Trainer console
                    </Link>
                  ) : (
                    // v14: nav restructure per Pipeline_Frontend_Spec_v2 §1 — "Your plan",
                    // "Coach", and "Vocabulary" no longer stand on their own in the top bar.
                    // They're full-named cards inside the new /writing hub instead, which
                    // resolves the earlier complaint that abbreviated nav labels ("Coach",
                    // "Vocabulary") weren't clear about what they actually were.
                    <>
                      <Link href="/dashboard" className="hover:text-brand-800">
                        Dashboard
                      </Link>
                      <Link href="/writing" className="hover:text-brand-800">
                        Writing
                      </Link>
                      <Link href="/progress" className="hover:text-brand-800">
                        Progress
                      </Link>
                    </>
                  )}
                  {user.plan !== "gold" && (
                    <Link
                      href="/upgrade"
                      className="rounded-full border border-brand-200 px-4 py-1.5 text-brand-800 hover:bg-brand-50"
                    >
                      Upgrade to Gold
                    </Link>
                  )}
                  <form action="/api/auth/logout" method="post">
                    <button className="text-ink-400 hover:text-ink-800">Log out</button>
                  </form>
                </div>
              </nav>
            </header>
            <main className="mx-auto max-w-5xl px-4 py-8">{children}</main>
          </>
        )}
        <footer className="border-t border-brand-100 py-6 text-center text-xs text-ink-400">
          ST.ELLA — English Language Learning Assistant. Band estimates are advisory and
          not official IELTS results.
        </footer>
      </body>
    </html>
  );
}
