#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Download the Photon 1.0 dumps for the geocoder (DACH + NL + BE + DK)
#
# The dumps stay compressed: `just photon-import` reads .jsonl.zst directly and
# imports all of them into one index. The files are kept, and a run downloads
# again only what GraphHopper has updated since.
# ==============================================================================

TARGET_DIR="data/downloads/photon"

URLS=(
  "https://download1.graphhopper.com/public/europe/germany/photon-dump-germany-1.0-latest.jsonl.zst"
  "https://download1.graphhopper.com/public/europe/austria/photon-dump-austria-1.0-latest.jsonl.zst"
  "https://download1.graphhopper.com/public/europe/switzerland-liechtenstein/photon-dump-switzerland-liechtenstein-1.0-latest.jsonl.zst"
  "https://download1.graphhopper.com/public/europe/netherlands/photon-dump-netherlands-1.0-latest.jsonl.zst"
  "https://download1.graphhopper.com/public/europe/belgium/photon-dump-belgium-1.0-latest.jsonl.zst"
  "https://download1.graphhopper.com/public/europe/denmark/photon-dump-denmark-1.0-latest.jsonl.zst"
)

if ! command -v wget &> /dev/null; then
  echo "Error: 'wget' is not installed." >&2
  exit 1
fi

mkdir -p "$TARGET_DIR"

echo "==> Downloading Photon dumps into $TARGET_DIR..."
for url in "${URLS[@]}"; do
  echo " -> $(basename "$url")"
  # -N: download only when the server's file is newer than ours.
  wget -N -q --show-progress -P "$TARGET_DIR" "$url"
done

echo "=============================================================================="
echo "Done. Dumps in: $TARGET_DIR"
echo "Build the geocoder index from all of them (one index) with:"
echo "  just photon-import $TARGET_DIR/*.jsonl.zst"
echo "This many countries may need a larger PHOTON_IMPORT_HEAP in .env."
echo "=============================================================================="
