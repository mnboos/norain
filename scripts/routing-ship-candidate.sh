#!/usr/bin/env bash
#
# Copy the local candidate graph, the terrain it was built with and the POI file to the VPS,
# then point the VPS's `candidate` (and `elevation/current`) at them:
# `just routing-ship-candidate` runs this. It neither validates nor activates anything there.
#
# Takes from just: VPS_USER, VPS_HOST, ROUTING_OSM_IMPORT_DIR (local), POIS_FILE, and
# optionally VPS_NORAIN_DIR (the checkout with the production .env, default /srv/norain).
# The VPS paths come from that .env: APP_STORAGE_PATH and ROUTING_OSM_IMPORT_DIR.
set -euo pipefail

: "${VPS_USER:?Set VPS_USER in .env}"
: "${VPS_HOST:?Set VPS_HOST in .env}"
: "${ROUTING_OSM_IMPORT_DIR:?Set ROUTING_OSM_IMPORT_DIR in .env}"
remote="$VPS_USER@$VPS_HOST"
norain_dir="${VPS_NORAIN_DIR:-/srv/norain}"
command -v rsync >/dev/null || { echo "rsync is required (on Windows, run this from WSL)." >&2; exit 1; }

# The graph cache mount in docker-compose.base.yml.
cache=data/graphhopper/cache
if [ ! -L "$cache/candidate" ]; then
    echo "No local candidate. Run: just build-graphhopper-graph-from FILE" >&2
    exit 1
fi
release=$(readlink "$cache/candidate")          # releases/<id>
case "$release" in
    releases/*) ;;
    *) echo "Unexpected candidate link: $release" >&2; exit 1 ;;
esac
manifest="$cache/$release/artifact.json"
if ! grep -Eq '"status": "(built|validated)"' "$manifest"; then
    echo "$release is not a finished import (see $manifest)." >&2
    exit 1
fi
# The terrain the graph was built with, as the container saw it: /osm_data/elevation/<hash>.
terrain=$(sed -n 's|.*"terrain": "/osm_data/\(elevation/[^"/]*\)".*|\1|p' "$manifest")
if [ -z "$terrain" ] || [ ! -f "$ROUTING_OSM_IMPORT_DIR/$terrain/manifest.json" ]; then
    echo "The terrain recorded in $manifest is not in $ROUTING_OSM_IMPORT_DIR." >&2
    exit 1
fi

# Read the two paths from the production .env without sourcing it.
read_remote_env() {
    ssh -o StrictHostKeyChecking=yes "$remote" \
        "sed -n 's/^$1=//p' $norain_dir/.env | tail -n 1 | tr -d \"\\\"'\r\""
}
remote_storage=$(read_remote_env APP_STORAGE_PATH)
remote_osm=$(read_remote_env ROUTING_OSM_IMPORT_DIR)
if [[ "$remote_storage" != /* || "$remote_osm" != /* ]]; then
    echo "Set absolute APP_STORAGE_PATH and ROUTING_OSM_IMPORT_DIR in $remote:$norain_dir/.env." >&2
    exit 1
fi
remote_cache="$remote_storage/graphhopper/cache"

echo "Shipping $release and $terrain to $remote"
ssh "$remote" "mkdir -p '$remote_cache/releases' '$remote_osm/elevation'"
# rsync resumes an interrupted copy. Nothing on the VPS points at the copies until both are
# complete, so a partial copy is harmless.
rsync -aP "$cache/$release/" "$remote:$remote_cache/$release/"
# terrain.pmtiles and fallback.pmtiles. The decoded tile caches are only written and read
# by an import; serving (and /elevation) reads the archives.
rsync -aP --exclude /cache/ --exclude /cache-fallback/ \
    "$ROUTING_OSM_IMPORT_DIR/$terrain/" "$remote:$remote_osm/$terrain/"
if [ -n "${POIS_FILE:-}" ] && [ -f "$ROUTING_OSM_IMPORT_DIR/$POIS_FILE" ]; then
    rsync -aP "$ROUTING_OSM_IMPORT_DIR/$POIS_FILE" "$remote:$remote_osm/"
else
    echo "No POI file ${POIS_FILE:-} in $ROUTING_OSM_IMPORT_DIR; journeys keep the VPS's POIs."
fi

# `elevation/current` too, so a later build of the same file on the VPS passes the check.
ssh "$remote" "ln -sfn '$release' '$remote_cache/candidate' && ln -sfn '${terrain#elevation/}' '$remote_osm/elevation/current'"

cat <<EOF
Candidate $release is on $VPS_HOST. There, in $norain_dir:
  just routing-validate-candidate '[[lon,lat],[lon,lat]]'
  just routing-activate
  just poi-import-into-db${POIS_FILE:+ $POIS_FILE}
EOF
