#!/bin/bash
#
# The container never builds a graph by itself. Two modes:
#   (no argument)  serve the graph in /graph-cache; without one, fail and say how to build it
#   build          build the graph from ROUTING_OSM_FILE_FILTERED into /graph-cache, then exit.
#                  just build-graphhopper-graph-from FILE runs this after emptying the cache.
set -euo pipefail

GRAPH_DIR=/graph-cache
GRAPHHOPPER_HEAP="${GRAPHHOPPER_HEAP:-6g}"
GRAPHHOPPER_BUILD_HEAP="${GRAPHHOPPER_BUILD_HEAP:-$GRAPHHOPPER_HEAP}"
GRAPHHOPPER_DATAACCESS="${GRAPHHOPPER_DATAACCESS:-MMAP}"
# RAM_STORE builds in the heap. MMAP keeps the graph in files on /graph-cache, so a large area
# builds with a much smaller heap (and more slowly). Both write the same graph.
GRAPHHOPPER_BUILD_DATAACCESS="${GRAPHHOPPER_BUILD_DATAACCESS:-RAM_STORE}"

build() {
    : "${ROUTING_OSM_FILE_FILTERED:?Name the bike-filtered file to build from: just build-graphhopper-graph-from FILE}"
    if [ -f "$GRAPH_DIR/properties" ]; then
        echo "$GRAPH_DIR already holds a graph. Empty it first (just build-graphhopper-graph-from does)."
        exit 1
    fi
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

    # GraphHopper calls building the graph "import".
    echo "Building the graph from ${BIKE_DATA_FILE} with a ${GRAPHHOPPER_BUILD_HEAP} heap (${GRAPHHOPPER_BUILD_DATAACCESS})"
    java -Xmx"${GRAPHHOPPER_BUILD_HEAP}" \
        -Ddw.graphhopper.graph.dataaccess.default_type="${GRAPHHOPPER_BUILD_DATAACCESS}" \
        -Ddw.graphhopper.datareader.file="${BIKE_DATA_FILE}" \
        -jar graphhopper.jar import /config.yaml
    echo "Build finished; $GRAPH_DIR is ready."
}

case "${1:-serve}" in
    build)
        build
        ;;
    serve)
        if [ ! -f "$GRAPH_DIR/properties" ]; then
            echo "No graph in $GRAPH_DIR. The container never builds one by itself. Build it with:"
            echo "  just build-graphhopper-graph-from <bike-filtered .osm.pbf in ROUTING_OSM_IMPORT_DIR>"
            echo "or copy one built elsewhere (docs/how-to/build-routing-graph.md)."
            exit 1
        fi
        # No -Xms: the heap grows to what the graph needs instead of claiming the maximum up front.
        echo "Serving $GRAPH_DIR (${GRAPHHOPPER_DATAACCESS}, heap ${GRAPHHOPPER_HEAP})"
        exec java -Xmx"${GRAPHHOPPER_HEAP}" \
            -Ddw.graphhopper.graph.dataaccess.default_type="${GRAPHHOPPER_DATAACCESS}" \
            -jar graphhopper.jar server /config.yaml
        ;;
    *)
        echo "Unknown command: $1 (expected build, or none to serve)" >&2
        exit 2
        ;;
esac
