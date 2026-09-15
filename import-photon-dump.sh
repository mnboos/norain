#!/bin/bash
# Run inside the Photon image with the persistent data directory mounted.
set -euo pipefail
export PHOTON_INDEX_FILE="${1:?Usage: bash /import-photon-dump.sh /photon_data/dump.jsonl.zst}"
export PHOTON_IMPORT_ONLY=true
export PHOTON_ALLOW_DOWNLOAD=false
exec /entrypoint.sh
