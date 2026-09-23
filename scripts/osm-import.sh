#!/usr/bin/env bash
#
# Build the routing graph from local .osm.pbf files: `just osm-import FILE…` runs this.
# Filters them for bikes like every build, merges several into one, extracts the journey
# planner's POIs from the same files, then rebuilds the graph.
#
# Takes from just: CONTAINER (podman or docker), OSM_DATA_URL, and INVOCATION_DIR, which the
# file names are relative to.
set -euo pipefail

# On Windows this runs in Git Bash, which would rewrite every /container/path argument into a
# Windows path before podman sees it.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

# Every build (routing-build, a fresh start) reads /osm_data/bike-<OSM_DATA_URL's file name>,
# so the import writes that file, or the next rebuild would use other data.
name=$(basename "$OSM_DATA_URL")
if [ $# -eq 1 ] && [ "$(basename "$1")" != "$name" ]; then
    echo "OSM_DATA_URL in .env ends in $name. Set it to a URL, or just the file name, ending in $(basename "$1")." >&2
    exit 1
fi
if [ $# -gt 1 ] && [[ "$OSM_DATA_URL" == *://* ]]; then
    echo "Several files are merged into one, and no download matches that. Set OSM_DATA_URL in .env to a plain file name for the merged set, e.g. dach.osm.pbf." >&2
    exit 1
fi

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

compose=("$CONTAINER" compose -f docker-compose.dev.yml)
"${compose[@]}" build graphhopper
"${compose[@]}" stop graphhopper
"${compose[@]}" run --rm --no-deps "${mounts[@]}" --entrypoint /graphhopper/filter-osm.sh graphhopper "/osm_data/bike-$name" "${inputs[@]}"
# The journey planner's POIs come from the same files (just poi-import loads them).
"${compose[@]}" run --rm --no-deps "${mounts[@]}" --entrypoint /graphhopper/extract-pois.sh graphhopper "/osm_data/pois-${name%.osm.pbf}.geojsonseq" "${inputs[@]}"
"${compose[@]}" run --rm --no-deps --entrypoint bash graphhopper -c 'rm -rf /graph-cache/..?* /graph-cache/.[!.]* /graph-cache/*'
# The filtered file is now newer than any extract in /osm_data, so the entrypoint builds from it without a download.
"${compose[@]}" run --rm --no-deps -e GRAPHHOPPER_BUILD_ONLY=true graphhopper
"${compose[@]}" up -d graphhopper
