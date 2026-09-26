#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Download the OSM extracts for the routing graph (DACH + NL + BE + DK)
#
# Only downloads: `just osm-filter-many-raw-pbf-into-one` filters them for bikes and merges them in the
# GraphHopper container, so nothing but wget is needed here. The files are kept,
# and a run downloads again only what Geofabrik has updated since.
# ==============================================================================

TARGET_DIR="data/downloads/osm"

PBF_URLS=(
  "https://download.geofabrik.de/europe/germany-latest.osm.pbf"
  "https://download.geofabrik.de/europe/austria-latest.osm.pbf"
  "https://download.geofabrik.de/europe/switzerland-latest.osm.pbf"
  "https://download.geofabrik.de/europe/netherlands-latest.osm.pbf"
  "https://download.geofabrik.de/europe/belgium-latest.osm.pbf"
  "https://download.geofabrik.de/europe/denmark-latest.osm.pbf"
)

if ! command -v wget &> /dev/null; then
  echo "Error: 'wget' is not installed." >&2
  exit 1
fi

mkdir -p "$TARGET_DIR"

echo "==> Downloading OSM extracts from Geofabrik into $TARGET_DIR..."
for url in "${PBF_URLS[@]}"; do
  echo " -> $(basename "$url")"
  # -N: download only when the server's file is newer than ours.
  wget -N -q --show-progress -P "$TARGET_DIR" "$url"
done

echo "=============================================================================="
echo "Done. Extracts in: $TARGET_DIR"
echo "Build the routing graph from them (filtered for bikes and merged into one) with:"
echo "  just osm-filter-many-raw-pbf-into-one $TARGET_DIR/*.osm.pbf"
echo "It writes the file set in .env, for example:"
echo "  ROUTING_OSM_FILE_FILTERED=bike-europe-cycling.osm.pbf"
echo "This many countries need a large build heap: set GRAPHHOPPER_BUILD_HEAP and"
echo "GRAPHHOPPER_MEM_LIMIT (see docs/how-to/build-routing-graph.md)."
echo "=============================================================================="
