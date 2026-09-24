#!/usr/bin/env bash
#
# Filter raw .osm.pbf files for bikes and merge them into ROUTING_OSM_FILE_FILTERED in
# ROUTING_OSM_IMPORT_DIR: `just osm-filter-many-raw-pbf-into-one FILE…` runs this. Also extracts the journey
# planner's POIs from the same files. It builds no graph; just build-graphhopper-graph-from FILE does that.
#
# Takes from just: CONTAINER (podman or docker), ROUTING_OSM_FILE_FILTERED from .env, and
# INVOCATION_DIR, which the file names are relative to.
set -euo pipefail

. "$(dirname "$0")/osm-input-mounts.sh"

# Writes /osm_data/$ROUTING_OSM_FILE_FILTERED and the POI file named after it, which
# just poi-import-into-db reads.
name="${ROUTING_OSM_FILE_FILTERED:-}"
if [[ "$name" != *.osm.pbf || "$name" == */* ]]; then
    echo "Set ROUTING_OSM_FILE_FILTERED in .env to the file name the import writes, e.g. bike-europe-cycling.osm.pbf." >&2
    exit 1
fi
pois="pois-${name#bike-}"
pois="${pois%.osm.pbf}.geojsonseq"

# Each file is mounted at /import/<its name>.
osm_input_mounts "$@"

# COMPOSE_FILE comes from .env through just.
compose=("$CONTAINER" compose)
"${compose[@]}" build graphhopper
"${compose[@]}" run --rm --no-deps "${mounts[@]}" --entrypoint /graphhopper/filter-osm.sh graphhopper "/osm_data/$name" "${inputs[@]}"
# The journey planner's POIs come from the same files (just poi-import-into-db loads them).
"${compose[@]}" run --rm --no-deps "${mounts[@]}" --entrypoint /graphhopper/extract-pois.sh graphhopper "/osm_data/$pois" "${inputs[@]}"
echo "Wrote $name and $pois. Build the graph from them with: just build-graphhopper-graph-from $name"
