#!/bin/sh
# Render (and some other hosts) only support ONE persistent disk per web
# service, but this app needs three separate persistent locations (SQLite
# db, pipeline session output, canonical resources). Fix: mount the single
# disk at /data, and symlink the three paths the app's code actually
# expects (lib/server/db.ts, goldPipeline.ts, STELLA_CANONICAL_RESOURCES_DIR)
# to subfolders inside it. No app code needed to change -- it still reads
# from /app/data, /app/pipeline/gold_web_sessions, /app/resources exactly
# as before, those paths just resolve through a symlink onto the one real
# disk now.
#
# Runs on every container start. The container's own filesystem layer is
# thrown away and rebuilt from the image on every restart/redeploy, so
# anything sitting at these paths from a previous run is harmless to
# replace -- only /data itself is the real, persistent volume.
set -e

mkdir -p /data/db /data/sessions /data/resources

rm -rf /app/data
ln -s /data/db /app/data

mkdir -p /app/pipeline
rm -rf /app/pipeline/gold_web_sessions
ln -s /data/sessions /app/pipeline/gold_web_sessions

rm -rf /app/resources
ln -s /data/resources /app/resources

exec "$@"
