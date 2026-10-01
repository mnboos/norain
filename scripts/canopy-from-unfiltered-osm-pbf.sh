#!/usr/bin/env bash
#
# Rasterise the woods of raw .osm.pbf files into canopy.pmtiles beside the current terrain in
# ROUTING_OSM_IMPORT_DIR, the tree heights GraphHopper's "avoid shade" reads while serving:
# `just canopy-from-unfiltered-osm-pbf FILE…` runs this. The files can be anywhere; several
# are merged. GraphHopper picks it up when it next starts; the graph is not rebuilt.
#
# Takes from just: CONTAINER (podman or docker) and INVOCATION_DIR, which the file names are
# relative to.
set -euo pipefail

. "$(dirname "$0")/osm-input-mounts.sh"
osm_input_mounts "$@"

# COMPOSE_FILE comes from .env through just.
compose=("$CONTAINER" compose)
"${compose[@]}" build graphhopper
"${compose[@]}" run --rm --no-deps "${mounts[@]}" graphhopper canopy "${inputs[@]}"
echo "Restart GraphHopper to read it; just routing-ship-candidate ships it with the terrain."
