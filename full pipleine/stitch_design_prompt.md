# Google Stitch prompt — ST.ELLA design

Copy everything below the line into Stitch as your project prompt. It's written as one continuous brief followed by a numbered list of screens, which is the format Stitch expects for a multi-screen app.

---

Design a clean, calm, trustworthy mobile-first web app called **ST.ELLA** — an AI-powered IELTS Writing coach used by students preparing for the exam and by their tutors. The audience includes teenagers and young adults, often studying for a high-stakes exam under time pressure, so the tone should be encouraging and low-anxiety, not corporate or exam-cold. Primary users are on phones; the app should also work cleanly on desktop for tutors.

**Design system to use throughout:**
- Primary brand color (buttons, active states, progress bars): `#534AB7` (deep violet-blue), with a soft tint `#EEEDFE` for backgrounds/highlights and `#7F77DD` for lighter accents.
- Success/positive color (submitted, correct, "on track"): `#0F6E56` (deep mint) on `#E1F5EE` background tint.
- Warning color (pilot ending soon, needs attention): amber/gold tones.
- Error color: `#993556` (muted rose) on `#FBEAF0` background tint.
- Body text: near-black ink `#2C2C2A` for headings, `#5F5E5A` for body copy, `#888780` for secondary/muted text.
- Cards: white background, 16px rounded corners, soft border, generous padding, subtle shadow — no harsh borders.
- Typography: clean modern sans-serif, confident but friendly (think Inter or similar), clear size hierarchy — large friendly headings, comfortable body text, no dense small print.
- Buttons: pill or rounded-rectangle, solid brand-violet primary button, outlined secondary button.
- Overall mood: like a supportive tutor, not a testing platform — warm whites and soft violet rather than clinical blues/grays.

Design the following screens as one connected flow:

1. **Sign-in screen.** Minimal — email input, "Send code" button, then a second state showing a 6-digit code input with "resend code" link. Reassuring microcopy that this replaces passwords.

2. **Onboarding survey (multi-step, one question per screen, ~10 steps).** A thin progress bar at the top and "Step X of 10" label. Each step is large touch-friendly choice buttons (not tiny radio buttons) or a simple input, one question per screen, auto-advances on selection where possible. Cover these steps in order: (1) which IELTS test — Academic or General Training, as two big cards; (2) target band score — grid of score buttons (5.5 through 9.0); (3) exam date — date picker plus a "not booked yet" option; (4) country of residence — searchable dropdown; (5) how did you hear about us — list of source options (Instagram, friend, trainer, Google, TikTok, other); (6) purpose — why they need IELTS (university abroad, immigration, work, personal, other); (7) current self-assessed writing level — four band-range choices; (8) prior IELTS experience — first time / retaking / studied before; (9) biggest writing challenge — task response, organization, vocabulary, grammar, not sure; (10) daily practice time preference — 5/10/15 minutes as three big buttons, with a "Finish setup" primary button.

3. **"Coming soon" screen for General Training.** Shown only to students who picked General in step 1 (not yet supported). Friendly, apologetic tone: explains Academic is the only supported test right now, confirms their email is saved and they'll be notified, no dead-end feeling — warm illustration or icon, not an error page.

4. **AI-processing consent screen.** Shown once before a student's first essay submission. Short, clear paragraph explaining their essay is sent to an AI service for scoring and their tutor can see the essay and its evaluation. Single "I understand and agree" button.

5. **Plan comparison / offer screen ("Choose your plan").** Two side-by-side cards, Premium and Gold, Gold visually emphasized (highlighted border or "recommended" badge) since it's the flagship tier. Each card: plan name, one-line tagline, a checklist of included features with checkmarks, a "Request this plan" button. Premium's features: band score across all 4 IELTS writing criteria, sentence-level error feedback, practice from your own mistakes. Gold's features: everything in Premium, plus a daily writing coach, vocabulary-building engine, essay revision with re-scoring, and a personalized multi-week study plan. Small note below both cards: pricing depends on region, shown after requesting. If the viewer is on a free pilot, show a small banner: "Your free pilot access runs until [date] — subscribing is optional."

6. **Main dashboard (student, Premium plan).** Greeting header ("Hi, [name]"). Row of 4 stat cards: current band, goal band, practice sessions completed, evaluations remaining. A "Homework" card showing the current assignment prompt and due date, or a "no assignment yet" empty state, with a "Write my essay" button. A "Daily practice" card with session length and a "Start session" button. A "Latest evaluation" card showing the most recent band score with a link to the full report.

7. **Main dashboard (student, Gold plan) — same layout as #6, plus two extra cards** that Premium doesn't have: a "Your study plan" card showing the current week number and this week's focus skill, with a "View full plan" link; and a small "Writing coach" highlight card teasing the daily guided-rewriting feature. Visually, Gold's dashboard should feel a notch richer/more personalized than Premium's — same design system, just more going on, reflecting the deeper feature set.

8. **Essay submission screen.** Shows the locked assignment prompt at the top (read-only, greyed background) if it's a trainer-assigned essay, a large text area for writing/pasting the essay below, word count indicator, and a "Submit for evaluation" button. Include a subtle note that Gold-tier evaluation can take several minutes, with a friendly loading/waiting state (progress indicator, encouraging message like "Reading your essay carefully...") rather than a blank spinner.

9. **Feedback report screen.** Header showing overall band score prominently (large number in brand violet), then a 4-criteria breakdown (Task Response, Coherence & Cohesion, Lexical Resource, Grammar) as small horizontal bars or a radar-style visual, each with its own band number. Below that, a list of "focus areas" — expandable cards, each showing a skill name, current vs target band, a short explanation, and 1-2 annotated error examples (highlighted excerpt from the essay, the issue, and a suggested correction) shown in a clear before/after style.

10. **Study plan screen (Gold only).** Timeline/roadmap layout showing multiple weeks, each week as a card with a focus criterion, difficulty label (foundational/consolidation/stretch), a short plain-language "why this week" explanation, and a row of daily practice checkmarks/status dots.

11. **Practice session screen.** Simple, game-like, low-pressure: one exercise at a time, multiple-choice style, progress dots at the top showing how many exercises are left in the session, immediate correct/incorrect feedback with a brief explanation before moving to the next.

12. **Trainer console (secondary, more utilitarian/dashboard style, desktop-first).** A denser admin-style layout: sections for "Add student," "Add trainer," a scrollable list of students each expandable to show their submission history and band trend, an "Upgrade requests" list with a "mark handled" action, and a "common mistakes across the group" visual summary (simple horizontal bar chart by error type).

Keep visual language consistent across all screens — same card style, same button shapes, same color roles — so it reads as one coherent product even though screens 6 and 7 (Premium vs Gold dashboard) should visibly communicate "more" without changing the underlying design system.
