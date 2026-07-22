# v9 — rewritten for the current Gold pipeline architecture. The old version
# of this file (v8) pointed at frontend_v8/ and full_premium_v1/, which no
# longer exist under those names/roles; this app is stella-frontend/ and the
# pipeline it drives by default is the Gold pipeline in "full pipleine/"
# (gold_full_pipeline_orchestrator_v1_4_9.py +
# gold_engine_commands_full_v1_4_13.json).
#
# This app needs Node (to run Next.js) and Python (to run the Gold pipeline
# as a subprocess — see lib/server/goldPipeline.ts) in the same place, plus
# persistent storage for the SQLite database and the pipeline's session
# output (both runtime state, mounted from the disk in the hosting
# provider's dashboard). LRET's canonical lexical registries are NOT
# runtime state — they're static reference data, and as of this revision
# they live committed in the repo at stella-frontend/app/resources/, so
# they get baked into the image at build time like any other app file. No
# manual upload to the disk is needed for them.
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
  # from source (node-gyp needs make/g++ for that). python3-pip added after
  # a real deploy crash revealed the pipeline is NOT 100% standard-library
  # after all — see "full pipleine/requirements.txt" for what and why.
  && apt-get install -y --no-install-recommends python3 python3-pip build-essential \
  && ln -s /usr/bin/python3 /usr/bin/python \
  && rm -rf /var/lib/apt/lists/*

# --- Python pipeline dependencies ----------------------------------------
# Debian 12 (bookworm) blocks bare `pip install` outside a virtualenv
# (PEP 668, "externally-managed-environment") -- --break-system-packages is
# required here since this container has no other Python workload to
# protect from version conflicts.
COPY ["full pipleine/requirements.txt", "/app/pipeline-requirements.txt"]
RUN pip3 install --no-cache-dir --break-system-packages -r /app/pipeline-requirements.txt

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
COPY ["full pipleine/", "/app/pipeline/"]

ENV STELLA_GOLD_PIPELINE_DIR=/app/pipeline
# Bumped from v1_4_13 -- that pin was 3 versions stale (missing the V3
# Section 3 topic-alignment-risk ceiling from v1_4_15 and the Vocabulary
# Coach wiring from v1_4_16) and was silently overriding goldPipeline.ts's
# own updated default, since this explicit env var always wins. If you add
# a newer gold_engine_commands_full_v*.json in the future, this line -- not
# just the TS default -- is what actually controls which config Render runs.
ENV STELLA_GOLD_ENGINE_CONFIG=gold_engine_commands_full_v1_4_16.json
# Baked-in path: stella-frontend/app/resources/ (repo) lands at
# /app/app/resources inside the image, because WORKDIR is /app and
# `COPY stella-frontend/ ./` preserves the app/ subfolder structure.
ENV STELLA_CANONICAL_RESOURCES_DIR=/app/app/resources

# --- Single persistent disk, split two ways via symlinks ------------------
# Most hosts (Render's Web Service tier, notably) only support ONE
# persistent disk per service, but this app needs two separate persistent
# locations (db, pipeline session output — canonical resources are now
# baked into the image, see above, so they no longer need the disk).
# docker-entrypoint.sh mounts everything under /data and symlinks
# /app/data and /app/pipeline/gold_web_sessions to subfolders inside it at
# container start -- the app's own code is untouched, it still reads/writes
# those exact paths.
COPY stella-frontend/docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Mount ONE disk here in your hosting provider's dashboard so the database,
# pipeline session output, and canonical resources all survive redeploys.
VOLUME ["/data"]

EXPOSE 3000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["npm", "start"]
