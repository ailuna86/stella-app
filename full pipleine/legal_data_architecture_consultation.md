# ST.ELLA — Legal Structure, Data Protection, Architecture & Deployment

I'm not a lawyer or accountant, and none of this is legal or tax advice — treat it as a grounded starting point for a conversation with a Kyrgyz lawyer/accountant (бухгалтер), not a substitute for one. Where I've cited a source, that's what I found current as of today; verify anything money- or compliance-related before you rely on it.

## 1. Business structure (Kyrgyzstan)

Yes — deploying under individual entrepreneur (IE / индивидуальный предприниматель) status is a normal, lightweight route for exactly this kind of business, and the current tax framework is unusually favorable for software/IT specifically:

- IEs in the IT sector on the single-tax system pay **2% on revenue** (not profit) — one of the more advantageous regimes in the region for exactly the kind of SaaS/EdTech business this is. [Solar Staff](https://help.solarstaff.com/en/articles/9245252-freelance-and-taxes-kyrgyzstan), [kgaccount](https://kgaccount.com/en/sole-proprietorship/)
- There's also a separate **patent-based** system (fixed fee, no reporting, exempt from profit/sales/VAT tax) — but that's built for periodic/fixed activities, not an ongoing subscription business with recurring revenue and growth. The 2% single-tax IT regime is the better fit for what you're building.
- If most of your revenue ends up coming from outside Kyrgyzstan (international IELTS candidates), it's worth asking your accountant specifically about **High Technology Park (HTP) residency**: IEs can apply, the tax rate drops to **1%** (or effectively 0% income/sales/VAT on foreign-sourced income) once approved, but it requires ≥80% export revenue (18 months to hit that), a mandatory annual audit (~$1,000), and a 1% revenue contribution back to HTP. [kgaccount — HTP](https://kgaccount.com/en/high-technology-park/), [HTP KG official](https://htp.kg/en/) — this is worth evaluating once you have real pilot-week revenue data, not before.
- IE registration can be done fully online now (state registration certificate + tax office registration at your place of residence). [Deel — sole proprietorship guide](https://www.deel.com/blog/sole-proprietorship-kyrgyzstan/), [myreloc](https://myreloc.com/en/register-an-individual-entrepreneur-ie-and-llc-in-kyrgyzstan-remotely/)
- None of this requires you to have real payment integration live for the pilot — the pilot week is free, and the plan-request flow I built defers real payment until after. Register/formalize once you're actually about to invoice paying subscribers, not before.

**Ask your accountant specifically:** (a) whether informal/pilot tutoring-adjacent software falls under any separate education-sector licensing in Kyrgyzstan (my read is no — this is software, not a school — but confirm), (b) whether receiving foreign-currency card payments (future Stripe/payment processor) triggers any National Bank currency-control reporting for an IE.

## 2. Data protection

Kyrgyzstan has an active personal data protection law and regulator — this isn't a legal vacuum:

- **Law "On Personal Information"** (No. 58, 2008, most recently amended 2021) is the primary law, enforced by the **State Agency for Protection of Personal Data**. [dpa.gov.kg — the law itself](https://dpa.gov.kg/en/npa/4), [DLA Piper overview](https://www.dlapiperdataprotection.com/?t=law&c=KG)
- Data subjects have rights that map closely to GDPR-style rights: access, correction, erasure, portability, objection to processing, restriction. Build for these regardless of exact current enforcement posture — they're the right default anyway.
- There was a **mandatory "personal data array holder" registration** requirement, with a transition period running Nov 2025–Feb 2026 during which the DPA could still fine unregistered processing even as the registration requirement itself was being phased out. [dresilience.org](https://dresilience.org/en/2025/09/12/mandatory-registration-of-personal-data-array-holders-what-organizations-in-kyrgyzstan-need-to-know/) — since that transition window has just closed as of this month, **confirm current registration status with your lawyer before wider launch**; I can't be fully certain from here whether registration is now required, optional, or abolished.
- Large-scale/sensitive processing can trigger a **Data Protection Officer** requirement and mandatory impact assessments for high-risk processing (profiling, automated decision-making) — an AI system that scores students and drives study recommendations plausibly counts as "automated decision-making" in spirit, even at pilot scale. Worth a direct question to your lawyer: does a 2-tutor pilot trigger this, or only at larger scale?

**What's already built that helps here:** the AI-processing consent notice (shown before first submission), scoped data storage (everything tied to `organizationId`), and no third-party analytics/tracking beyond OpenAI (for evaluation) and Resend (for login emails).

**What's a genuine open decision, not yet resolved:** the current architecture retains all student data indefinitely, even if a student stops using the app — this was an earlier deliberate choice (so a returning student's profile/study plan isn't lost), but "indefinite retention with no deletion path" sits awkwardly next to data-subject erasure rights. I'd recommend: keep indefinite retention as the *default*, but add an honored, even if manual for now, "delete my data on request" path (an email to you, followed by you running a deletion query) — that's proportionate for pilot scale and defensible under an erasure-rights framework, without needing to build a self-serve deletion feature this week.

## 3. Should students (or parents) sign anything for the pilot?

Two separate things, and I'd keep them separate:

1. **AI-processing consent** — already built (`ConsentNotice.tsx`), shown before first submission, one-time. This covers "your essay goes to an AI provider, your trainer can see it too." Keep as-is.
2. **A short pilot-specific notice** — not yet built, and worth adding before real students start. It should say, in plain language: this is an early pilot/beta (scoring and features may change), the free week is optional-subscribe afterward, what data is collected (the survey answers, essays, AI reports) and why, and how to request deletion. This doesn't need to be a formal contract — a one-page notice with a checkbox, or even a single email you send before the pilot starts, is proportionate at this scale.
3. **Parental consent, if any pilot student is under 18.** IELTS test-takers are frequently 16–17 (applying to universities abroad), so this is a real, not hypothetical, question for your specific cohort. For a small pilot, a simple parent-facing version of the same notice — even just a reply-confirmation email from the parent — is reasonable; it doesn't need notarization. I'd treat "is anyone in this pilot under 18" as the first thing to check this week, since it changes what you need before they submit their first essay.

I did **not** build either of these documents yet — they're a business/legal-tone decision I'd rather you (or a lawyer) word precisely than have me draft blind. Say the word and I'll draft a first version of both for your review.

## 4. Data storage & architecture

What's already in place is appropriate for pilot scale and doesn't need to change this week:

- **SQLite on a Render persistent disk**, WAL mode (safe concurrent writes), everything scoped by `organizationId`. For 2 tutors and a handful of students, this comfortably handles the load.
- **No separate database server to run or pay for** — one less moving part during a week where you want to be watching the pipeline, not administering infrastructure.

What I'd flag for *after* the pilot, not before:

- **Backups.** SQLite-on-disk has no automatic backup story by default. A daily copy of the `.db` file to somewhere else (even just downloading it manually once a day during pilot week) is cheap insurance against a Render disk issue wiping a week of real pilot data.
- **Migration path.** Once you're past a handful of concurrent users — rough personal guideline, not a hard number — a single-process SQLite file starts to strain. Render Postgres or Supabase are natural next steps; the current schema (typed rows, `organizationId` scoping everywhere) should port over without a redesign. Not urgent for a 2-tutor pilot.
- **Essay text and AI reports are sensitive personal data**, stored in the same file as everything else. Nothing to change architecturally, but confirm the Render disk isn't publicly exposed (should be true by default — worth a five-minute check, not an assumption) and that you're on HTTPS end to end once a real domain is attached.

## 5. Deployment & domain

- **Render deployment is already fully documented** (`DEPLOY_v8.md`) — Dockerfile-based, persistent disk, roughly $7–10/month, auto-redeploy on push to `main`.
- **Domain**: the default `onrender.com` subdomain works fine for the pilot itself — I wouldn't block next week's launch on buying a domain. But register a proper one (Namecheap, Cloudflare Registrar, etc.) before a wider B2C launch, for two concrete reasons: parents trust a real domain more than a platform subdomain when a minor's data is involved, and Resend needs a **verified sending domain** for reliable email deliverability — right now, without one, OTP codes fall back to on-screen display, which won't scale past a small trusted pilot.

## 6. What I'd do this week, in order

1. Confirm whether any pilot student is under 18 — determines whether you need parental consent before day one.
2. Ask your accountant the two open questions in section 1 (education licensing, currency-control reporting) — doesn't block the pilot, but you'll want answers before you invoice anyone for real.
3. Confirm current DPA registration status for Kyrgyzstan (transition period just closed) — again, doesn't block a free pilot week, but worth knowing before wider launch.
4. Let me draft the pilot notice + parental consent text if you want it — I held off so a legal/business voice reviews the wording, not because it's hard to write.
