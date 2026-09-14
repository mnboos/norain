#!/bin/bash
#
# Three paths, chosen by whether /graph-cache already holds an imported graph:
#   graph present                       -> serve it (no .pbf needed)
#   graph absent, import allowed        -> download + import, then serve (or exit, IMPORT_ONLY)
#   graph absent, import not allowed    -> fail; the graph is built elsewhere and copied in
# Production sets GRAPHHOPPER_ALLOW_IMPORT=false: see docs/how-to/build-routing-graph.md.
set -euo pipefail

GRAPH_DIR=/graph-cache
GRAPHHOPPER_HEAP="${GRAPHHOPPER_HEAP:-6g}"
GRAPHHOPPER_IMPORT_HEAP="${GRAPHHOPPER_IMPORT_HEAP:-$GRAPHHOPPER_HEAP}"
GRAPHHOPPER_DATAACCESS="${GRAPHHOPPER_DATAACCESS:-RAM_STORE}"

if [ ! -f "$GRAPH_DIR/properties" ]; then
    if [ "${GRAPHHOPPER_ALLOW_IMPORT:-true}" = "false" ]; then
        echo "No imported graph in $GRAPH_DIR and GRAPHHOPPER_ALLOW_IMPORT=false."
        echo "Build the graph on another machine and copy it here: docs/how-to/build-routing-graph.md"
        exit 1
    fi

    : "${OSM_DATA_URL:?OSM_DATA_URL must be set to import a graph}"
    OSM_DATA_FILE="${OSM_DATA_DIR}/$(basename "$OSM_DATA_URL")"
    echo "Requested OSM data: ${OSM_DATA_FILE}"

    if [ ! -s "$OSM_DATA_FILE" ]; then
        echo "Downloading OSM data"
        wget \
            --no-check-certificate \
            --user-agent="norain" \
            --show-progress \
            --progress=bar:force:noscroll \
            -O "$OSM_DATA_FILE" \
            "$OSM_DATA_URL"
    fi

    echo "Importing ${OSM_DATA_FILE} with a ${GRAPHHOPPER_IMPORT_HEAP} heap"
    java -Xmx"${GRAPHHOPPER_IMPORT_HEAP}" \
        -Ddw.graphhopper.datareader.file="${OSM_DATA_FILE}" \
        -jar graphhopper.jar import /config.yaml

    if [ "${GRAPHHOPPER_IMPORT_ONLY:-false}" = "true" ]; then
        echo "Import finished; $GRAPH_DIR is ready to copy."
        exit 0
    fi
fi

# No -Xms: the heap grows to what the graph needs instead of claiming the maximum up front.
echo "Serving $GRAPH_DIR (${GRAPHHOPPER_DATAACCESS}, heap ${GRAPHHOPPER_HEAP})"
exec java -Xmx"${GRAPHHOPPER_HEAP}" \
    -Ddw.graphhopper.graph.dataaccess.default_type="${GRAPHHOPPER_DATAACCESS}" \
    -jar graphhopper.jar server /config.yaml
