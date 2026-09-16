#!/bin/bash
set -euo pipefail

PHOTON_DATA_DIR=/photon_data
INDEX_URL="${PHOTON_INDEX_URL:-}"
INDEX_FILE="${PHOTON_INDEX_FILE:-}"
IMPORT_HEAP="${PHOTON_IMPORT_HEAP:-4g}"

# Build in a temporary directory: failed imports must never look like ready indexes.
# Existing indexes (including manually copied ones) are always reused.
if [ ! -d "${PHOTON_DATA_DIR}/photon_data" ]; then
    if [ -z "${INDEX_FILE}" ] && [ "${PHOTON_ALLOW_DOWNLOAD:-true}" != true ]; then
        echo "Photon index missing. Import a local dump with PHOTON_INDEX_FILE and PHOTON_IMPORT_ONLY=true; see docs/how-to/deploy-vps.md." >&2
        exit 1
    fi
    if [ -n "${INDEX_FILE}" ] && [ ! -r "${INDEX_FILE}" ]; then
        echo "Cannot read PHOTON_INDEX_FILE: ${INDEX_FILE}" >&2
        exit 1
    fi
    artifact="${INDEX_FILE:-${INDEX_URL}}"
    case "${artifact}" in
        *.jsonl.zst | *.jsonl | *.tar.bz2) ;;
        *)
            echo "Photon artifact must end in .jsonl.zst, .jsonl or .tar.bz2 (Photon 1.0 format)." >&2
            exit 1
            ;;
    esac
    staging=$(mktemp -d "${PHOTON_DATA_DIR}/.import-XXXXXX")
    trap 'rm -rf -- "${staging}"' EXIT
    if [ -z "${INDEX_FILE}" ]; then
        echo "Downloading Photon artifact: ${INDEX_URL}"
        INDEX_FILE="${staging}/download"
        wget --user-agent="norain" --show-progress --progress=bar:force:noscroll \
             -O "${INDEX_FILE}" "${INDEX_URL}"
    fi
    case "${artifact}" in
        *.jsonl.zst)
            echo "Importing Photon JSONL dump..."
            zstd --stdout -d "${INDEX_FILE}" | java "-Xmx${IMPORT_HEAP}" -jar /photon.jar import -import-file - -data-dir "${staging}"
            ;;
        *.jsonl)
            java "-Xmx${IMPORT_HEAP}" -jar /photon.jar import -import-file "${INDEX_FILE}" -data-dir "${staging}"
            ;;
        *.tar.bz2)
            echo "Extracting prebuilt Photon index..."
            pbzip2 -cd "${INDEX_FILE}" | tar -x -C "${staging}"
            ;;
    esac
    if [ ! -d "${staging}/photon_data" ]; then
        echo "Photon artifact did not produce a photon_data directory." >&2
        exit 1
    fi
    mv "${staging}/photon_data" "${PHOTON_DATA_DIR}/photon_data"
    rm -rf -- "${staging}"
    trap - EXIT
fi

if [ "${PHOTON_IMPORT_ONLY:-false}" = true ]; then
    echo "Photon index ready."
    exit 0
fi

echo "Starting photon"
exec java -jar /photon.jar serve -data-dir "${PHOTON_DATA_DIR}" -listen-ip 0.0.0.0 "$@"
