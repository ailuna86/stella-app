#!/bin/sh
# Render (and some other hosts) only support ONE persistent disk per web
# service, but this app needs two separate persistent locations (SQLite
# db, pipeline session output). Fix: mount the single disk at /data, and
# symlink the two paths the app's code actually expects (lib/server/db.ts,
# goldPipeline.ts) to subfolders inside it. No app code needed to change --
# it still reads from /app/data and /app/pipeline/gold_web_sessions exactly
# as before, those paths just resolve through a symlink onto the one real
# disk now.
#
# Canonical resources are NOT handled here anymore -- they're static
# reference data baked into the image at build time from
# stella-frontend/app/resources/ (see Dockerfile), so they don't need disk
# space or a symlink at all.
#
# Runs on every container start. The container's own filesystem layer is
# thrown away and rebuilt from the image on every restart/redeploy, so
# anything sitting at these paths from a previous run is harmless to
# replace -- only /data itself is the real, persistent volume.
set -e

mkdir -p /data/db /data/sessions

rm -rf /app/data
ln -s /data/db /app/data

mkdir -p /app/pipeline
rm -rf /app/pipeline/gold_web_sessions
ln -s /data/sessions /app/pipeline/gold_web_sessions

exec "$@"
