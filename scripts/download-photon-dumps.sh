#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# GraphHopper Photon JSONL.ZST Downloader
# Downloads raw Photon JSONL dumps for DACH + NL + BE + DK
# ==============================================================================

TARGET_DIR="./photon_dumps"

URLS=(
  "https://download1.graphhopper.com/public/europe/germany/photon-dump-germany-1.0-latest.jsonl.zst"
  "https://download1.graphhopper.com/public/europe/austria/photon-dump-austria-1.0-latest.jsonl.zst"
  "https://download1.graphhopper.com/public/europe/switzerland-liechtenstein/photon-dump-switzerland-liechtenstein-1.0-latest.jsonl.zst"
  "https://download1.graphhopper.com/public/europe/netherlands/photon-dump-netherlands-1.0-latest.jsonl.zst"
  "https://download1.graphhopper.com/public/europe/belgium/photon-dump-belgium-1.0-latest.jsonl.zst"
  "https://download1.graphhopper.com/public/europe/denmark/photon-dump-denmark-1.0-latest.jsonl.zst"
)

# 1. Check Dependencies
for tool in wget zstd; do
  if ! command -v "$tool" &> /dev/null; then
    echo "Error: '$tool' is required. Install via: sudo apt install -y wget zstd"
    exit 1
  fi
done

mkdir -p "$TARGET_DIR"
cd "$TARGET_DIR"

# 2. Download Files
echo "==> Downloading GraphHopper Photon JSONL dumps..."
for url in "${URLS[@]}"; do
  file=$(basename "$url")
  if [ -f "$file" ] || [ -f "${file%.zst}" ]; then
    echo " -> $file (or decompressed version) already exists, skipping download."
  else
    echo " -> Downloading $file..."
    wget -q --show-progress "$url"
  fi
done

# 3. Decompress ZST files
echo "==> Decompressing .zst archives..."
for file in *.jsonl.zst; do
  [ -f "$file" ] || continue
  echo " -> Decompressing $file..."
  zstd -d --rm "$file"
done

echo "=============================================================================="
echo "Done! Decompressed JSONL files ready in: $(pwd)"
echo "Import into Photon using:"
echo "  java -jar photon.jar -importer -json-dump <file1.jsonl> <file2.jsonl> ..."
echo "=============================================================================="