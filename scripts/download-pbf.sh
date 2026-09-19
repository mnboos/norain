#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# OSM Data Pipeline for Bicycle Routing (DACH + NL + BE + DK)
# Optimized for GraphHopper instances running on constrained memory (e.g., 24GB RAM)
# ==============================================================================

WORK_DIR="./osm_workspace"
OUTPUT_PBF="europe_cycling_combined.osm.pbf"

# List of Geofabrik PBF files to download
PBF_URLS=(
  "https://download.geofabrik.de/europe/germany-latest.osm.pbf"
  "https://download.geofabrik.de/europe/austria-latest.osm.pbf"
  "https://download.geofabrik.de/europe/switzerland-latest.osm.pbf"
  "https://download.geofabrik.de/europe/netherlands-latest.osm.pbf"
  "https://download.geofabrik.de/europe/belgium-latest.osm.pbf"
  "https://download.geofabrik.de/europe/denmark-latest.osm.pbf"
)

# ------------------------------------------------------------------------------
# 1. Dependency Check
# ------------------------------------------------------------------------------
echo "==> Checking required tools..."
for tool in osmium wget; do
  if ! command -v "$tool" &> /dev/null; then
    echo "Error: '$tool' is not installed."
    echo "Install dependencies on Ubuntu/Debian via: sudo apt update && sudo apt install -y osmium-tool wget"
    exit 1
  fi
done

mkdir -p "$WORK_DIR"
cd "$WORK_DIR"

# ------------------------------------------------------------------------------
# 2. Download PBF Files
# ------------------------------------------------------------------------------
echo "==> Downloading regional OSM extracts from Geofabrik..."
DOWNLOADED_FILES=()

for url in "${PBF_URLS[@]}"; do
  filename=$(basename "$url")
  DOWNLOADED_FILES+=("$filename")

  if [ -f "$filename" ]; then
    echo " -> $filename already exists, skipping download."
  else
    echo " -> Downloading $filename..."
    wget -q --show-progress "$url" -O "$filename"
  fi
done

# ------------------------------------------------------------------------------
# 3. Merge PBF Files
# ------------------------------------------------------------------------------
echo "==> Merging PBF extracts into a single file..."
RAW_MERGED="merged_raw.pbf"

osmium merge "${DOWNLOADED_FILES[@]}" -o "$RAW_MERGED" --overwrite

# ------------------------------------------------------------------------------
# 4. Filter Non-Cycling Infrastructure
# ------------------------------------------------------------------------------
echo "==> Filtering out motorways and non-cyclable highways..."
# Removes highways tagged as motorway or motorway_link to reduce graph size by ~15-20%
osmium tags-filter "$RAW_MERGED" \
  w/highway=motorway,motorway_link \
  --invert-match \
  -o "../$OUTPUT_PBF" \
  --overwrite

# ------------------------------------------------------------------------------
# 5. Cleanup
# ------------------------------------------------------------------------------
echo "==> Cleaning up temporary raw PBF files..."
cd ..
rm -rf "$WORK_DIR"

echo "=============================================================================="
echo "Success! Combined and filtered dataset ready:"
echo " Output File : $(pwd)/$OUTPUT_PBF"
echo " Size        : $(du -h "$OUTPUT_PBF" | cut -f1)"
echo "=============================================================================="