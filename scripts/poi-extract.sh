#!/usr/bin/env bash
#
# Extract the journey planner's POIs from raw .osm.pbf files into POIS_FILE in
# ROUTING_OSM_IMPORT_DIR, which just poi-import reads: `just poi-extract FILE…` runs this.
# The files can be anywhere; several are merged into one POI file.
#
# Takes from just: CONTAINER (podman or docker), POIS_FILE (named after
# ROUTING_OSM_FILE_FILTERED) and INVOCATION_DIR, which the file names are relative to.
set -euo pipefail

. "$(dirname "$0")/osm-input-mounts.sh"
osm_input_mounts "$@"

# COMPOSE_FILE comes from .env through just.
compose=("$CONTAINER" compose)
"${compose[@]}" build graphhopper
"${compose[@]}" run --rm --no-deps "${mounts[@]}" --entrypoint /graphhopper/extract-pois.sh graphhopper "/osm_data/$POIS_FILE" "${inputs[@]}"
echo "Wrote $POIS_FILE. Load it with: just poi-import (just poi-import-prod on the VPS)"
