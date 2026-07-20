// v8: simple fixed-window rate limiter backed by the same SQLite database —
// no separate service needed at this scale. Used to stop someone from
// spamming a student's inbox with code requests, or hammering the login
// endpoint to brute-force a 6-digit code.
import { db } from "./db";

const WINDOW_MS = 15 * 60 * 1000; // 15 minutes

export function checkRateLimit(key: string, max: number): boolean {
  const now = Date.now();
  const row = db().prepare("SELECT * FROM rate_limits WHERE key = ?").get(key) as any;
  if (!row || now - row.window_start > WINDOW_MS) {
    db()
      .prepare("INSERT OR REPLACE INTO rate_limits (key, count, window_start) VALUES (?, ?, ?)")
      .run(key, 1, now);
    return true;
  }
  if (row.count >= max) return false;
  db().prepare("UPDATE rate_limits SET count = count + 1 WHERE key = ?").run(key);
  return true;
}
