#!/bin/bash
#
# Three paths, chosen by whether /graph-cache already holds a built graph:
#   graph present                          -> serve it (no .pbf needed)
#   graph missing, GRAPHHOPPER_BUILD_GRAPH=true (default)
#                                          -> download OSM_DATA_URL, filter it for bikes
#                                             (filter-osm.sh), build the graph, then serve it
#                                             (or exit, with GRAPHHOPPER_BUILD_ONLY=true)
#   graph missing, GRAPHHOPPER_BUILD_GRAPH=false
#                                          -> fail; for a graph built on another machine and
#                                             copied in (docs/how-to/build-routing-graph.md)
# To rebuild: stop the service, empty /graph-cache, start it again.
set -euo pipefail

GRAPH_DIR=/graph-cache
GRAPHHOPPER_HEAP="${GRAPHHOPPER_HEAP:-6g}"
GRAPHHOPPER_BUILD_HEAP="${GRAPHHOPPER_BUILD_HEAP:-$GRAPHHOPPER_HEAP}"
GRAPHHOPPER_DATAACCESS="${GRAPHHOPPER_DATAACCESS:-MMAP}"

if [ ! -f "$GRAPH_DIR/properties" ]; then
    if [ "${GRAPHHOPPER_BUILD_GRAPH:-true}" = "false" ]; then
        echo "No graph in $GRAPH_DIR and GRAPHHOPPER_BUILD_GRAPH=false, so none is built here."
        echo "Build the graph on another machine and copy it here: docs/how-to/build-routing-graph.md"
        exit 1
    fi

    : "${OSM_DATA_URL:?OSM_DATA_URL must be set to build a graph}"
    OSM_DATA_FILE="${OSM_DATA_DIR}/$(basename "$OSM_DATA_URL")"
    BIKE_DATA_FILE="${OSM_DATA_DIR}/bike-$(basename "$OSM_DATA_URL")"
    echo "Requested OSM data: ${OSM_DATA_FILE}"

    # The filtered copy is reused until the extract is newer than it. With only the filtered
    # copy present (just osm-import), nothing is downloaded.
    if [ ! -s "$BIKE_DATA_FILE" ] || [ "$OSM_DATA_FILE" -nt "$BIKE_DATA_FILE" ]; then
        if [ ! -s "$OSM_DATA_FILE" ]; then
            if [[ "$OSM_DATA_URL" != *://* ]]; then
                echo "OSM_DATA_URL is a file name, but neither ${OSM_DATA_FILE} nor ${BIKE_DATA_FILE} exists."
                echo "Put the file there, or run just osm-import with the extracts it was made from."
                exit 1
            fi
            echo "Downloading OSM data"
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

    # GraphHopper calls building the graph "import".
    echo "Building the graph from ${BIKE_DATA_FILE} with a ${GRAPHHOPPER_BUILD_HEAP} heap"
    java -Xmx"${GRAPHHOPPER_BUILD_HEAP}" \
        -Ddw.graphhopper.datareader.file="${BIKE_DATA_FILE}" \
        -jar graphhopper.jar import /config.yaml

    if [ "${GRAPHHOPPER_BUILD_ONLY:-false}" = "true" ]; then
        echo "Build finished; $GRAPH_DIR is ready."
        exit 0
    fi
fi

# No -Xms: the heap grows to what the graph needs instead of claiming the maximum up front.
echo "Serving $GRAPH_DIR (${GRAPHHOPPER_DATAACCESS}, heap ${GRAPHHOPPER_HEAP})"
exec java -Xmx"${GRAPHHOPPER_HEAP}" \
    -Ddw.graphhopper.graph.dataaccess.default_type="${GRAPHHOPPER_DATAACCESS}" \
    -jar graphhopper.jar server /config.yaml
