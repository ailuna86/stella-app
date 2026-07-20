# v9 — rewritten for the current Gold pipeline architecture. The old version
# of this file (v8) pointed at frontend_v8/ and full_premium_v1/, which no
# longer exist under those names/roles; this app is stella-frontend/ and the
# pipeline it drives by default is the Gold pipeline in "full pipleine/"
# (gold_full_pipeline_orchestrator_v1_4_9.py +
# gold_engine_commands_full_v1_4_13.json).
#
# This app needs Node (to run Next.js) and Python (to run the Gold pipeline
# as a subprocess — see lib/server/goldPipeline.ts) in the same place, plus
# persistent storage for the SQLite database, the pipeline's session output,
# and LRET's canonical lexical registries. All three are mounted as volumes
# in your hosting provider's dashboard rather than baked into the image,
# since they're either runtime state (db, sessions) or large external data
# that shouldn't live in version control or the image layer cache
# (canonical resources — confirmed real size: tens of thousands of
# collocation/lexical entries).
#
# The Gold pipeline's Python code is entirely standard-library (confirmed by
# checking every import across every engine file — no `openai` package, no
# spaCy, nothing pip-installable) — no `pip install` step is needed here. If
# that ever changes, add a requirements.txt to "full pipleine/" and a
# `RUN pip3 install --no-cache-dir -r ...` line below.
#
# IMPORTANT — build context: this Dockerfile must be built from the parent
# folder that contains BOTH stella-frontend/ and "full pipleine/" as sibling
# directories, not from inside stella-frontend/ itself, so it can copy the
# Python pipeline in. Example (run from the parent folder):
#   docker build -f stella-frontend/Dockerfile -t stella-app .
#
# The "full pipleine" folder name has a space in it — every reference to it
# below is quoted for that reason. Don't remove the quotes.

FROM node:20-bookworm

RUN apt-get update \
  # build-essential is a fallback in case npm can't fetch a prebuilt
  # better-sqlite3 binary for this platform/Node ABI and has to compile it
  # from source (node-gyp needs make/g++ for that).
  && apt-get install -y --no-install-recommends python3 build-essential \
  && ln -s /usr/bin/python3 /usr/bin/python \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Node app -----------------------------------------------------------
COPY stella-frontend/package.json stella-frontend/package-lock.json* ./
RUN npm ci
COPY stella-frontend/ ./
RUN npm run build

# --- Python pipeline (sibling directory in the real project) ------------
# Copied wholesale: engine scripts, gold_engine_commands_full_v1_4_13.json,
# the writing-coach move bank, and every other file the command config's
# relative paths expect to find alongside it.
COPY "full pipleine/" "/app/pipeline/"

ENV STELLA_GOLD_PIPELINE_DIR=/app/pipeline
ENV STELLA_GOLD_ENGINE_CONFIG=gold_engine_commands_full_v1_4_13.json
# Deliberately NOT set here — point this at your mounted canonical-resources
# volume in your hosting provider's dashboard (see the deployment guide).
# Left unset, the orchestrator falls back to a path that only exists on the
# original dev machine and LRET's canonical registries silently won't load.
# ENV STELLA_CANONICAL_RESOURCES_DIR=/app/resources

# Persistent volumes — mount these in your hosting provider's dashboard so
# they survive redeploys and restarts:
#   /app/data                    SQLite database (users, submissions, etc.)
#   /app/pipeline/gold_web_sessions   every essay's full session output
#   /app/resources                LRET's canonical lexical registries
#     (upload once; see the deployment guide for what goes in it)
VOLUME ["/app/data", "/app/pipeline/gold_web_sessions", "/app/resources"]

EXPOSE 3000
CMD ["npm", "start"]
