#!/usr/bin/env bash
#
# Filter raw .osm.pbf files for bikes and merge them into ROUTING_OSM_FILE_FILTERED in
# ROUTING_OSM_IMPORT_DIR: `just osm-filter-many-raw-pbf-into-one FILE…` runs this. Also extracts the journey
# planner's POIs from the same files. It builds no graph; just build-graphhopper-graph-from FILE does that.
#
# Takes from just: CONTAINER (podman or docker), ROUTING_OSM_FILE_FILTERED from .env, and
# INVOCATION_DIR, which the file names are relative to.
set -euo pipefail

# On Windows this runs in Git Bash, which would rewrite every /container/path argument into a
# Windows path before podman sees it.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

# Writes /osm_data/$ROUTING_OSM_FILE_FILTERED and the POI file named after it, which
# just poi-import reads.
name="${ROUTING_OSM_FILE_FILTERED:-}"
if [[ "$name" != *.osm.pbf || "$name" == */* ]]; then
    echo "Set ROUTING_OSM_FILE_FILTERED in .env to the file name the import writes, e.g. bike-europe-cycling.osm.pbf." >&2
    exit 1
fi
pois="pois-${name#bike-}"
pois="${pois%.osm.pbf}.geojsonseq"

# File names are relative to where just was run. Each file is mounted at /import/<its name>.
# `pwd -W` is Git Bash's C:/… form, which podman on Windows understands; elsewhere it fails
# and plain `pwd` answers.
repo=$(pwd)
cd "$INVOCATION_DIR"
declare -A seen=()
mounts=() inputs=()
for file in "$@"; do
    base=$(basename "$file")
    [ -f "$file" ] || { echo "Not a file: $file" >&2; exit 1; }
    [ -z "${seen[$base]:-}" ] || { echo "Two files are named $base." >&2; exit 1; }
    seen[$base]=1
    mounts+=(-v "$(cd "$(dirname "$file")" && { pwd -W 2>/dev/null || pwd; })/$base:/import/$base:ro,z")
    inputs+=("/import/$base")
done
cd "$repo"

# COMPOSE_FILE comes from .env through just.
compose=("$CONTAINER" compose)
"${compose[@]}" build graphhopper
"${compose[@]}" run --rm --no-deps "${mounts[@]}" --entrypoint /graphhopper/filter-osm.sh graphhopper "/osm_data/$name" "${inputs[@]}"
# The journey planner's POIs come from the same files (just poi-import loads them).
"${compose[@]}" run --rm --no-deps "${mounts[@]}" --entrypoint /graphhopper/extract-pois.sh graphhopper "/osm_data/$pois" "${inputs[@]}"
echo "Wrote $name and $pois. Build the graph from them with: just build-graphhopper-graph-from $name"
