#!/bin/bash
#
# The container serves /graph-cache/current. Terrain preparation, candidate builds and
# activation are explicit; a failed import never replaces the active graph.
set -euo pipefail

GRAPH_DIR=/graph-cache
GRAPHHOPPER_HEAP="${GRAPHHOPPER_HEAP:-6g}"
GRAPHHOPPER_BUILD_HEAP="${GRAPHHOPPER_BUILD_HEAP:-$GRAPHHOPPER_HEAP}"
GRAPHHOPPER_BUILD_THREADS="${GRAPHHOPPER_BUILD_THREADS:-3}"
GRAPHHOPPER_DATAACCESS="${GRAPHHOPPER_DATAACCESS:-MMAP}"
# RAM_STORE builds in the heap. MMAP keeps the graph in files on /graph-cache, so a large area
# builds with a much smaller heap (and more slowly). Both write the same graph.
GRAPHHOPPER_BUILD_DATAACCESS="${GRAPHHOPPER_BUILD_DATAACCESS:-RAM_STORE}"

prepare_osm() {
    : "${ROUTING_OSM_FILE_FILTERED:?Name the bike-filtered file to build from: just build-graphhopper-graph-from FILE}"
    BIKE_DATA_FILE="${OSM_DATA_DIR}/${ROUTING_OSM_FILE_FILTERED}"
    echo "Importing from ${OSM_DATA_DIR} (ROUTING_OSM_IMPORT_DIR on the host: ${ROUTING_OSM_IMPORT_DIR:-unknown})"

    # bike-<OSM_DATA_URL's file name> is made from that download, and made again when the
    # extract is newer. Any other name must already exist (just osm-filter-many-raw-pbf-into-one).
    OSM_DATA_URL="${OSM_DATA_URL:-}"
    if [[ "$OSM_DATA_URL" == *://* ]] && [ "$ROUTING_OSM_FILE_FILTERED" = "bike-$(basename "$OSM_DATA_URL")" ]; then
        OSM_DATA_FILE="${OSM_DATA_DIR}/$(basename "$OSM_DATA_URL")"
        if [ ! -s "$BIKE_DATA_FILE" ] || [ "$OSM_DATA_FILE" -nt "$BIKE_DATA_FILE" ]; then
            if [ ! -s "$OSM_DATA_FILE" ]; then
                echo "Downloading ${OSM_DATA_URL}"
                wget \
                    --no-check-certificate \
                    --user-agent="norain" \
                    --show-progress \
                    --progress=bar:force:noscroll \
                    -O "$OSM_DATA_FILE" \
                    "$OSM_DATA_URL"
            fi
            echo "Filtering ${OSM_DATA_FILE} for bikes into ${BIKE_DATA_FILE}"
            /graphhopper/filter-osm.sh "$BIKE_DATA_FILE" "$OSM_DATA_FILE"
        fi
    elif [ ! -s "$BIKE_DATA_FILE" ]; then
        echo "${BIKE_DATA_FILE} does not exist."
        echo "Put it in ROUTING_OSM_IMPORT_DIR on the host (${ROUTING_OSM_IMPORT_DIR:-unknown}), or make it with just osm-filter-many-raw-pbf-into-one."
        exit 1
    fi

}

# The zoom-12 archive that fills gaps in the zoom-15 one (FallbackElevationProvider), when the
# terrain has one. Terrain prepared before the fallback existed has none and still works.
fallback_args() {
    fallback_opts=()
    if [ -f "$1/fallback.pmtiles" ]; then
        fallback_opts=(-Ddw.graphhopper.graph.elevation.pmtiles.fallback.location="$1/fallback.pmtiles")
        if [ -n "${2:-}" ]; then
            fallback_opts+=(-Ddw.graphhopper.graph.elevation.pmtiles.fallback.cache_dir="$1/cache-fallback")
        fi
    fi
}

# Preparation and imports are explicit. Startup does not download terrain.
terrain() {
    prepare_osm
    python /graphhopper/terrain.py prepare "$BIKE_DATA_FILE" "$@"
}

terrain_cleanup() {
    python /graphhopper/terrain.py cleanup
}

# Tree heights for "avoid shade" (canopy.py), beside the current terrain, which ships them with
# it (just routing-ship-candidate). GraphHopper finds it beside terrain.pmtiles while serving,
# never at import: no graph rebuild. Without it trees cast no shadow; terrain and clouds still do.
canopy() {
    [ $# -gt 0 ] || { echo "Usage: entrypoint.sh canopy RAW.osm.pbf [RAW.osm.pbf ...]" >&2; exit 2; }
    if [ ! -d /osm_data/elevation/current ]; then
        echo "Prepare the terrain first (just download-elevation-for FILE): the canopy goes beside it." >&2
        exit 1
    fi
    python /graphhopper/canopy.py "$(readlink -f /osm_data/elevation/current)/canopy.pmtiles" "$@"
}

# Backfills for the system dashboard's coverage map (GET /coverage), for terrain and graphs
# made before the build wrote these files. Neither changes the graph or the terrain key.
# The served graph's terrain, which need not be elevation/current. The release's own road cells
# come first; without them the terrain's cells, or for terrain prepared for a bounding box,
# every cell in it.
terrain_coverage() {
    artifact=$(python /graphhopper/artifact.py check "${1:-current}")
    terrain_dir=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["terrain"])' "$artifact/artifact.json")
    cells=()
    [ ! -f "$artifact/cells.json" ] || cells=(--cells "$artifact/cells.json")
    python /graphhopper/terrain.py coverage --terrain "$terrain_dir" "${cells[@]}"
}

graph_cells() {
    : "${ROUTING_OSM_FILE_FILTERED:?Name the bike-filtered file the graph was built from}"
    artifact=$(python /graphhopper/artifact.py check "${1:-current}")
    python /graphhopper/terrain.py cells "${OSM_DATA_DIR}/${ROUTING_OSM_FILE_FILTERED}" \
        --cells-out "$artifact/cells.json"
}

build() {
    python /graphhopper/memory.py "$GRAPHHOPPER_BUILD_HEAP"
    if ! [[ "$GRAPHHOPPER_BUILD_THREADS" =~ ^[1-9][0-9]*$ ]]; then
        echo "GRAPHHOPPER_BUILD_THREADS must be a positive integer, got: $GRAPHHOPPER_BUILD_THREADS" >&2
        exit 1
    fi
    exec 9>/graph-cache/.build.lock
    flock -n 9 || { echo "Another graph build is running." >&2; exit 1; }
    prepare_osm
    if [ ! -d /osm_data/elevation/current ]; then
        echo "Elevation data has not been prepared in ROUTING_OSM_IMPORT_DIR (${ROUTING_OSM_IMPORT_DIR:-unknown})." >&2
        echo "Run: just download-elevation-for $ROUTING_OSM_FILE_FILTERED" >&2
        echo "Then retry: just build-graphhopper-graph-from $ROUTING_OSM_FILE_FILTERED" >&2
        exit 1
    fi
    # Hold the terrain selection stable until the import has captured its immutable path.
    exec 8>/osm_data/elevation/.prepare.lock
    flock -s 8
    # The check counts the file's road cells anyway; the release keeps them for GET /coverage.
    road_cells=$(mktemp /tmp/graphhopper-cells-XXXXXX.json)
    if ! python /graphhopper/terrain.py check "$BIKE_DATA_FILE" --cells-out "$road_cells"; then
        echo "The prepared elevation data in ROUTING_OSM_IMPORT_DIR (${ROUTING_OSM_IMPORT_DIR:-unknown}) is for another OSM file." >&2
        echo "Run: just download-elevation-for $ROUTING_OSM_FILE_FILTERED" >&2
        echo "Then retry: just build-graphhopper-graph-from $ROUTING_OSM_FILE_FILTERED" >&2
        exit 1
    fi
    terrain_dir=$(readlink -f /osm_data/elevation/current)
    artifact=$(python /graphhopper/artifact.py begin --terrain "$terrain_dir" \
        --cells "$road_cells" --osm "$BIKE_DATA_FILE")
    rm -f "$road_cells"
    flock -u 8
    fallback_args "$terrain_dir" cache
    # Thread counts go into a copy of the config, never through -Ddw.: Dropwizard passes those as
    # strings and GraphHopper's PMap.getInt ignores a string, so every count fell back to its
    # default (0 for urban density, which fails the import after hours). The candidate keeps its
    # fingerprinted config; thread counts do not change the graph.
    build_config=$(mktemp /tmp/graphhopper-build-XXXXXX.yaml)
    sed -E "s/^([[:space:]]*(prepare\.ch|prepare\.lm|graph\.urban_density|prepare\.subnetworks)\.threads:)[[:space:]]*[0-9]+/\1 ${GRAPHHOPPER_BUILD_THREADS}/" \
        "$artifact/config.yaml" > "$build_config"
    if [ "$(grep -cE '^[[:space:]]*(prepare\.ch|prepare\.lm|graph\.urban_density|prepare\.subnetworks)\.threads: '"${GRAPHHOPPER_BUILD_THREADS}"'$' "$build_config")" -ne 4 ]; then
        echo "Could not set the four build thread counts in $artifact/config.yaml." >&2
        exit 1
    fi
    echo "Building candidate $artifact; the active graph remains available."
    java --enable-native-access=ALL-UNNAMED -Xmx"${GRAPHHOPPER_BUILD_HEAP}" \
        -Ddw.graphhopper.graph.location="$artifact/graph" \
        -Ddw.graphhopper.custom_models.directory="$artifact/models" \
        -Ddw.graphhopper.graph.elevation.pmtiles.location="$terrain_dir/terrain.pmtiles" \
        -Ddw.graphhopper.graph.elevation.cache_dir="$terrain_dir/cache" \
        "${fallback_opts[@]}" \
        -Ddw.graphhopper.graph.dataaccess.default_type="${GRAPHHOPPER_BUILD_DATAACCESS}" \
        -Ddw.graphhopper.datareader.file="${BIKE_DATA_FILE}" \
        -jar graphhopper.jar import "$build_config"
    python /graphhopper/artifact.py finish "${artifact#/graph-cache/}"
    echo "Candidate ready. Validate it before activation (see docs/how-to/build-routing-graph.md)."
}

serve() {
    python /graphhopper/memory.py "$GRAPHHOPPER_HEAP"
    artifact=$(python /graphhopper/artifact.py check "${1:-current}")
    terrain_dir=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["terrain"])' "$artifact/artifact.json")
    fallback_args "$terrain_dir"
    exec java --enable-native-access=ALL-UNNAMED -Xmx"${GRAPHHOPPER_HEAP}" \
        -Ddw.graphhopper.graph.location="$artifact/graph" \
        -Ddw.graphhopper.custom_models.directory="$artifact/models" \
        -Ddw.graphhopper.graph.elevation.pmtiles.location="$terrain_dir/terrain.pmtiles" \
        "${fallback_opts[@]}" \
        -Ddw.graphhopper.graph.dataaccess.default_type="${GRAPHHOPPER_DATAACCESS}" \
        -jar graphhopper.jar server "$artifact/config.yaml"
}

case "${1:-serve}" in
    terrain) shift; terrain "$@" ;;
    terrain-cleanup) terrain_cleanup ;;
    canopy) shift; canopy "$@" ;;
    terrain-coverage) terrain_coverage "${2:-current}" ;;
    graph-cells) graph_cells "${2:-current}" ;;
    build) build ;;
    serve) serve "${2:-current}" ;;
    activate) python /graphhopper/artifact.py activate "${2:-candidate}" ;;
    rollback) python /graphhopper/artifact.py rollback previous ;;
    *) echo "Unknown command: $1 (expected terrain, terrain-cleanup, canopy, terrain-coverage, graph-cells, build, serve, activate or rollback)" >&2; exit 2 ;;
esac
