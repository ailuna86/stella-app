# frontend_v8 — ST.ELLA (pilot-ready: security fix, real database, study plan, algorithm QA)

NEW FOLDER — supersedes frontend_v7/ (kept unchanged, per project rule).
Built against `SPEC_frontend_v8_rev3.md` (see full_premium root).

## Why this version exists

This is the build described in the approved spec: the two critical
pilot-readiness fixes (forged-login bug, real hosting support) plus every
feature agreed on in the spec conversation — algorithm-review tool, study
plan, consent notice, prompt batch, and the multi-school foundation.

## Critical fixes

- **Session forgery, fixed.** The old cookie (`stella_uid`) stored a raw,
  unsigned user ID — anyone could set it manually and be logged in as
  anyone else, including the trainer. Replaced with `iron-session`
  (`lib/server/session.ts`): the cookie is now cryptographically sealed
  with `SESSION_SECRET`, so it can't be forged or edited from outside the
  server. `currentUser()` is now `async` — every caller was updated to
  `await` it.
- **Real database instead of loose JSON files.** `lib/server/store.ts` is
  rewritten on top of SQLite (`lib/server/db.ts`, via `better-sqlite3`).
  Every write is now atomic — no more risk of two simultaneous requests
  corrupting each other's data. Same deployment model as before (still
  "just a file on disk"), no separate database server to run.
- **Multi-school foundation.** Every user, assignment, and submission is
  now scoped to an `organization_id` (`lib/types.ts`, `Organization`). Only
  one organization exists for this pilot, but a second school can be added
  later without a schema rewrite — done now because it was essentially free
  while the data layer was already being rebuilt.

## New features (per the approved spec)

- **Algorithm-review tool** (`app/trainer/review/[id]/page.tsx`,
  `components/AlgorithmReviewForm.tsx`) — trainer sees a student's full
  essay text next to the AI's report (previously never shown — the data
  existed, it just wasn't rendered) and submits structured feedback on the
  evaluation's accuracy, wrong error flags, missed issues, and feedback
  quality. Trainer-only, permanent (confirmed B2B tool, not pilot-only).
  Submissions the pipeline flags as ambiguous (`escalate_to_human_review`)
  now surface first in the trainer console with a "needs review" badge.
- **Study plan, surfaced** (`app/study-plan/page.tsx`,
  `lib/server/study-plan.ts`) — the pipeline's existing multi-week study
  plan (built by `study_plan_engine_v1.py`, previously computed but never
  shown) now has a student-facing page. Every recommendation includes a
  plain-language reason (explicit requirement — "what to do and why"), plus
  a compact summary in the trainer console per student, and a "due for next
  essay" indicator driven by the plan's `essay_trigger`.
- **AI-processing consent notice** (`components/ConsentNotice.tsx`) — shown
  once before a user's first essay submission, for every user (pilot or
  paid), gating `/api/evaluate` until accepted.
- **Practice-session feedback gets an open question** — "What was useful?
  What wasn't?" alongside the existing 1–5 ratings
  (`components/PlatformFeedbackWidget.tsx`).
- **Rating scales are labeled** — every 1–5 rating now shows "Not at all"
  / "Completely" at the ends instead of a bare, ambiguous scale.
- **Prompt bank moved into the database**
  (`lib/prompt-bank.ts` seeds it, `lib/server/store.ts` / `app/api/prompts`
  manage it) — the original 10 prompts are pre-approved; 15 new original
  IELTS-style prompts across new topics are seeded **unapproved**, shown in
  a "Prompts awaiting review" section in the trainer console
  (`components/PromptReviewList.tsx`) — nothing in that batch is assignable
  until you approve it there.
- **Login no longer reveals whether an email is registered** — previously
  a 404-vs-200 difference leaked which emails had accounts. Now the
  request-code endpoint always "succeeds"; an unregistered email just never
  receives a real code.
- **Rate limiting** on code requests and verification attempts
  (`lib/server/rate-limit.ts`) — per email and per IP.
- **Email sending moved to Resend** — personal Gmail SMTP hits sending
  limits and spam filters past a handful of recipients. Falls back to the
  same on-screen code display when not configured or a send fails
  (unchanged behavior from v7's fix).
- **Data retention**: nothing is deleted when a student leaves — kept
  intentionally, per your decision, so it can seed their profile/study plan
  if they return as a subscriber.

## Deployment

- `Dockerfile` — bundles Node (Next.js) + Python (the evaluation pipeline)
  + a persistent-disk mount point, built from the `full_premium/` root so
  it can also include `full_premium_v1/` and the exercise bank file.
- `DEPLOY_v8.md` — plain-language, click-by-click guide to deploying this
  on Render, including the persistent disk and environment variables.
- `.env.local.example` — updated for `SESSION_SECRET` (required),
  `RESEND_API_KEY`/`RESEND_FROM` (replacing `SMTP_USER`/`SMTP_PASS`), and
  `OPENAI_API_KEY` (unchanged).

## Run locally

```powershell
cd "frontend_v8"
npm install
copy .env.local.example .env.local
# edit .env.local: set SESSION_SECRET (see the file for how to generate one);
# leave RESEND_API_KEY/RESEND_FROM blank to use the on-screen code fallback
npm run dev
```

## Verification notes

Every file in this version was written directly (not copied from v7) after
an earlier version's bulk-copy step silently truncated a couple of files —
this version avoids that failure mode entirely. Full `npm install` and
`next build` were not run in the sandbox used to build this (no internet
access to install `better-sqlite3`'s native binary in that environment);
please run `npm install && npm run build` locally before deploying, and
let me know if anything doesn't compile — happy to fix immediately.
