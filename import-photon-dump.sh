#!/bin/bash
# Run inside the Photon image with the persistent data directory mounted.
set -euo pipefail
# Several dumps become one index; see PHOTON_INDEX_FILE in docker/photon-entrypoint.sh.
: "${1:?Usage: bash /import-photon-dump.sh /photon_data/dump.jsonl.zst [more dumps ...]}"
export PHOTON_INDEX_FILE="$*"
export PHOTON_IMPORT_ONLY=true
export PHOTON_ALLOW_DOWNLOAD=false
exec /entrypoint.sh
