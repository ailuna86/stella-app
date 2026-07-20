# Deploying stella-frontend (Gold pipeline) off local PowerShell

This replaces the "keep a PowerShell window open forever" setup. The
Dockerfile in this folder bundles Node (runs Next.js) and Python (runs the
Gold pipeline as a subprocess) into one image that a hosting provider keeps
running for you, auto-restarts if it crashes, and doesn't depend on your
laptop staying awake.

Recommended host: **Railway** (or Render as a close second — same
Dockerfile works on both). Both give you: build-from-Dockerfile, a real
persistent filesystem (unlike serverless platforms), no execution-time
limits (needed — a full essay evaluation can take several minutes), and
env vars set through a dashboard instead of PowerShell.

## 1. Folder layout this expects

The Dockerfile must be built from the **parent folder** that contains both
of these as siblings:

```
<parent folder>/
  stella-frontend/       <- this repo
  full pipleine/         <- the Gold Python pipeline
```

If your parent folder currently has other names or nesting, either
reorganize to match this, or edit the `COPY` lines in `Dockerfile`
accordingly.

## 2. Create a `.dockerignore` at the parent-folder level

I can't create this file myself — it needs to live one level above
`stella-frontend/`, which isn't a folder I have access to. Create a file
named `.dockerignore` right next to the two folders above (same level you'll
run `docker build` from), with this content:

```
stella-frontend/node_modules
stella-frontend/.next
stella-frontend/data
full pipleine/gold_web_sessions
full pipleine/**/*.zip
```

This keeps old test-session data, local build output, and your local
SQLite file out of the image — the volumes set up in step 5 replace all of
that at runtime.

## 3. Build the image

From the parent folder (not from inside `stella-frontend/`):

```bash
docker build -f stella-frontend/Dockerfile -t stella-app .
```

This will take a few minutes the first time (npm install + Next.js build).
If it fails on `npm ci`, delete `stella-frontend/package-lock.json` and use
`npm install` in the Dockerfile instead — but try `npm ci` first, it's more
reproducible.

## 4. Push to Railway (or Render)

One persistent disk is all you need — the app splits it into three logical
locations itself (see `docker-entrypoint.sh`), because most hosts, Render's
Web Service tier included, only support one disk per service anyway.

**Render:**
1. "New Web Service" → connect the GitHub repo → set **Dockerfile Path** to
   `stella-frontend/Dockerfile`.
2. Under Settings → Disks, add **one** disk, mount path `/data`. Render's
   free tier does NOT include persistent disks — you'll need a paid
   instance for this to actually work across restarts.
3. Never mount a disk at `/app` itself — that's where the built app lives;
   a disk mounted there would overlay your build output and either break
   the first deploy or silently freeze the app on old code on every deploy
   after that. `/data` is a path outside the build entirely, which is the
   point.

**Railway** — same idea: Settings → Volumes → one volume, mount path
`/data`.

## 5. Upload the canonical resources folder

This is the one thing that has to be moved manually — it's large,
external data that shouldn't live in the Docker image or git.

Your canonical resources folder should contain (confirmed against a real
run this session):
- `enhance_thesaurus.json`
- `discourse_registry.json`
- `positive_collocations_registry.tsv`
- `lexical_registry.json`

Copy that whole folder onto the disk at **`/data/resources`** (not
`/app/resources` — that path is a symlink `docker-entrypoint.sh` creates
pointing at `/data/resources`; either path gets you to the same place once
the container's running, but `/data/resources` is the real location on the
disk itself, which is what matters if you're copying files in directly).
The exact mechanism depends on your host:
- Railway: `railway run bash` gives you a shell inside the running
  container with the volume mounted — `scp`/`rsync` the files in, or use
  Railway's volume browser if your plan has one.
- Render: SSH into the instance (paid plans support this) and copy the
  files onto the mounted disk directly.

`STELLA_CANONICAL_RESOURCES_DIR` is already set to `/app/resources` inside
the image (see the Dockerfile) — you don't need to set it again in the
dashboard. Until you upload the files, LRET runs without its canonical
registries — it still works, just with weaker positive-collocation and
academic-vocabulary matching (confirmed: canonical loading is optional,
not a hard requirement, but you'll want it for real students).

## 6. Environment variables

Set these in your hosting provider's dashboard (never commit them):

| Variable | Required | Notes |
|---|---|---|
| `SESSION_SECRET` | yes | 32+ random chars — generate with `node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"` |
| `OPENAI_API_KEY` | yes | Powers Detector/Evaluator/LRET/Writing Coach LLM calls |
| `RESEND_API_KEY` | for real pilot | Without it, confirmation codes show on-screen instead of emailing |
| `RESEND_FROM` | for real pilot | e.g. `"ST.ELLA <login@yourdomain.com>"` |
| `STELLA_GOLD_ENGINE_CONFIG` | no | Defaults correctly to `gold_engine_commands_full_v1_4_13.json`, already baked into the image |
| `VIP_CHEAP_MODEL` / `VIP_STRONG_MODEL` | optional | Detector's two-tier model switch — see the model recommendation in `GOLD_PIPELINE_SPEC_V2.md` |
| `LRET_SUGGESTION_MODEL` | optional | Leave unset to keep classify/generate on the same (cheap) model; set to a stronger tier to test the split |

`STELLA_GOLD_PIPELINE_DIR` and `STELLA_CANONICAL_RESOURCES_DIR` don't need
to be set in the dashboard — both are baked into the image via the
Dockerfile.

## 7. Verify

After deploy, submit one real essay through the live URL and confirm:
- The submission completes (check the container logs for the 27-stage
  orchestrator run finishing without a Python traceback).
- `10_revision_workspace.json` and friends appear under `/data/sessions`
  on the disk (confirms the volume mount is actually being written to, not
  silently falling back to in-container-only storage that vanishes on
  restart).
- Restart the service from the dashboard and confirm existing user
  accounts / prior submissions are still there (confirms `/data` is a real
  persistent volume, not just container-local disk).

## What this doesn't cover

- TLS/custom domain — both Railway and Render provide this out of the box
  on their dashboards, no extra config needed here.
- Scaling beyond one instance — the SQLite database and the pipeline's
  file-based session storage are both single-writer designs; don't run
  multiple replicas against the same volumes without revisiting that.
- The Premium pipeline (`STELLA_PIPELINE_DIR`) — not wired into this
  Dockerfile; add a second `COPY` line if you need it live for the pilot.
