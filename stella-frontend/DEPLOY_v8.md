# Deploying frontend_v8 to Render (click-by-click)

This turns "running on my laptop" into a real, always-on website with a
secure address, for about $7–10/month. Follow these once; after that,
pushing updates is a couple of clicks.

## Before you start

You'll need:
- A GitHub account (free) — Render deploys from a GitHub repository.
- The values for `SESSION_SECRET`, `RESEND_API_KEY`, `RESEND_FROM`, and
  `OPENAI_API_KEY` (see `.env.local.example` in this folder for how to get
  each one).

## 1. Put the project on GitHub

1. Create a new (private) repository on github.com, e.g. `stella-app`.
2. From your computer, inside the `full_premium` folder (the one containing
   `frontend_v8/` and `full_premium_v1/`), run:
   ```powershell
   git init
   git add frontend_v8 full_premium_v1 va_exercise_bank_v11d_approved.jsonl
   git commit -m "Initial deploy"
   git branch -M main
   git remote add origin https://github.com/<your-username>/stella-app.git
   git push -u origin main
   ```
   (If `git` isn't installed, download it from git-scm.com first.)

## 2. Create the Render service

1. Go to render.com, sign up/log in, click **New +** → **Web Service**.
2. Connect your GitHub account and select the repository you just pushed.
3. Render will detect a `Dockerfile` — set:
   - **Dockerfile Path**: `frontend_v8/Dockerfile`
   - **Docker Build Context Directory**: `.` (the repo root)
4. Choose the **Starter** plan (cheapest tier with a persistent disk —
   required for the database and pipeline files to survive restarts).

## 3. Add the persistent disk

1. In the service settings, find **Disks** → **Add Disk**.
2. Mount path: `/app/data`
3. Size: 1 GB is plenty for a 10-person pilot.

## 4. Add environment variables

In **Environment**, add each of these (values from your `.env.local` /
the setup steps in `.env.local.example`):
- `SESSION_SECRET`
- `RESEND_API_KEY`
- `RESEND_FROM`
- `OPENAI_API_KEY`
- `NODE_ENV` = `production`

## 5. Deploy

Click **Create Web Service**. The first build takes a few minutes (it's
installing Node and Python packages). When it finishes, Render gives you a
web address like `stella-app.onrender.com` — that's your live site.

## 6. Custom domain (optional, any time)

In **Settings** → **Custom Domains**, add your domain and follow the DNS
instructions Render shows you. Takes about 10 minutes plus DNS propagation
time (up to a few hours). Not required to launch the pilot.

## Updating the app later

Push new commits to the `main` branch on GitHub — Render redeploys
automatically. No manual server work needed.

## If something doesn't work on first deploy

The most likely snag is the Python pipeline's own dependencies — if
`full_premium_v1/` has a `requirements.txt` file, the Dockerfile has a
commented-out line to install it; uncomment it and redeploy. Send me the
build log if you get stuck and I'll help debug it.
