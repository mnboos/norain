#!/bin/bash
set -euo pipefail

PHOTON_DATA_DIR=/photon_data
INDEX_URL="${PHOTON_INDEX_URL:?PHOTON_INDEX_URL must be set (a Photon dump/index matching your OSM extract)}"
IMPORT_HEAP="${PHOTON_IMPORT_HEAP:-4g}"

# Photon stores its index at <data-dir>/photon_data. We only fetch/build it when missing, so a
# manually-prepared index in the mounted volume is left untouched.
#
# Two artifact types are supported (browse https://download1.graphhopper.com/public/ — the version
# token must be 1.0 to match the photon-1.0.1.jar in the Dockerfile's photon stage):
#   * country/region:   photon-dump-<region>-1.0-latest.jsonl.zst  -> imported here (builds the index)
#   * continent/planet: photon-db-<region>-1.0-latest.tar.bz2      -> extracted here (prebuilt index)
if [ ! -d "${PHOTON_DATA_DIR}/photon_data" ]; then
    cd "${PHOTON_DATA_DIR}"
    case "${INDEX_URL}" in
        *.jsonl.zst | *.jsonl)
            echo "Downloading Photon JSONL dump: ${INDEX_URL}"
            wget --no-check-certificate --user-agent="norain" \
                 --show-progress --progress=bar:force:noscroll \
                 -O dump.jsonl.zst "${INDEX_URL}"
            echo "Importing dump (this builds the search index, may take a few minutes)..."
            zstd --stdout -d dump.jsonl.zst | java "-Xmx${IMPORT_HEAP}" -jar /photon.jar import -import-file - -data-dir "${PHOTON_DATA_DIR}"
            rm -f dump.jsonl.zst
            ;;
        *.tar.bz2)
            echo "Downloading prebuilt Photon index: ${INDEX_URL}"
            wget --no-check-certificate --user-agent="norain" \
                 --show-progress --progress=bar:force:noscroll \
                 -O index.tar.bz2 "${INDEX_URL}"
            echo "Extracting search index..."
            pbzip2 -cd index.tar.bz2 | tar x
            rm -f index.tar.bz2
            ;;
        *)
            echo "Unsupported PHOTON_INDEX_URL: expected *.jsonl.zst (dump) or *.tar.bz2 (prebuilt index)." >&2
            exit 1
            ;;
    esac
fi

if [ -d "${PHOTON_DATA_DIR}/photon_data" ]; then
    echo "Starting photon"
    # Forward any extra args from the container `command:` (e.g. -cors-any) to `serve`.
    exec java -jar /photon.jar serve -data-dir "${PHOTON_DATA_DIR}" -listen-ip 0.0.0.0 "$@"
else
    echo "Could not start photon: search index not found after download." >&2
    exit 1
fi
