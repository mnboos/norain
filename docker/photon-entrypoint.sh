#!/bin/bash
set -euo pipefail

PHOTON_DATA_DIR=/photon_data
INDEX_URL="${PHOTON_INDEX_URL:-}"
IMPORT_HEAP="${PHOTON_IMPORT_HEAP:-4g}"
# PHOTON_INDEX_FILE: one local artifact, or several .jsonl.zst / .jsonl dumps separated by
# spaces, which become one index (e.g. one dump per country).
read -r -a INDEX_FILES <<< "${PHOTON_INDEX_FILE:-}"

# Photon's import takes one file and drops whatever the index already holds, so several dumps
# can't be imported one after another. They go in as one stream instead: every dump starts
# with the same header and country list, so those lines are kept from the first file only.
dump_stream() {
    local first=true file
    for file in "$@"; do
        case "${file}" in
            *.zst) zstd --stdout -d "${file}" ;;
            *) cat "${file}" ;;
        esac | if ${first}; then
            cat
        else
            awk 'NR <= 2 && /^ ?\{"type":"(NominatimDumpFile|CountryInfo)"/ { next } { print }'
        fi
        first=false
    done
}

# Build in a temporary directory: failed imports must never look like ready indexes.
# Existing indexes (including manually copied ones) are reused, unless PHOTON_REPLACE_INDEX=true;
# then the old one is swapped out only once the new one is ready.
if [ ! -d "${PHOTON_DATA_DIR}/photon_data" ] || [ "${PHOTON_REPLACE_INDEX:-false}" = true ]; then
    if [ ${#INDEX_FILES[@]} -eq 0 ] && [ "${PHOTON_ALLOW_DOWNLOAD:-true}" != true ]; then
        echo "Photon index missing. Import a local dump with PHOTON_INDEX_FILE and PHOTON_IMPORT_ONLY=true; see docs/how-to/deploy-vps.md." >&2
        exit 1
    fi
    for file in "${INDEX_FILES[@]}"; do
        if [ ! -r "${file}" ]; then
            echo "Cannot read PHOTON_INDEX_FILE: ${file}" >&2
            exit 1
        fi
        case "${file}" in
            *.jsonl.zst | *.jsonl) ;;
            *.tar.bz2)
                if [ ${#INDEX_FILES[@]} -gt 1 ]; then
                    echo "A prebuilt .tar.bz2 index can't be combined with other files; only dumps can." >&2
                    exit 1
                fi
                ;;
        esac
    done
    artifact="${INDEX_FILES[0]:-${INDEX_URL}}"
    case "${artifact}" in
        *.jsonl.zst | *.jsonl | *.tar.bz2) ;;
        *)
            echo "Photon artifact must end in .jsonl.zst, .jsonl or .tar.bz2 (Photon 1.0 format)." >&2
            exit 1
            ;;
    esac
    staging=$(mktemp -d "${PHOTON_DATA_DIR}/.import-XXXXXX")
    trap 'rm -rf -- "${staging}"' EXIT
    if [ ${#INDEX_FILES[@]} -eq 0 ]; then
        echo "Downloading Photon artifact: ${INDEX_URL}"
        wget --user-agent="norain" --show-progress --progress=bar:force:noscroll \
             -O "${staging}/download" "${INDEX_URL}"
        INDEX_FILES=("${staging}/download")
    fi
    case "${artifact}" in
        *.jsonl.zst | *.jsonl)
            # The download has no extension, so name the format for dump_stream.
            if [ "${INDEX_FILES[0]}" = "${staging}/download" ] && [[ "${artifact}" == *.zst ]]; then
                mv "${staging}/download" "${staging}/download.zst"
                INDEX_FILES=("${staging}/download.zst")
            fi
            echo "Importing Photon JSONL dump(s): ${INDEX_FILES[*]}"
            dump_stream "${INDEX_FILES[@]}" | java "-Xmx${IMPORT_HEAP}" -jar /photon.jar import -import-file - -data-dir "${staging}"
            ;;
        *.tar.bz2)
            echo "Extracting prebuilt Photon index..."
            pbzip2 -cd "${INDEX_FILES[0]}" | tar -x -C "${staging}"
            ;;
    esac
    if [ ! -d "${staging}/photon_data" ]; then
        echo "Photon artifact did not produce a photon_data directory." >&2
        exit 1
    fi
    if [ -d "${PHOTON_DATA_DIR}/photon_data" ]; then
        echo "Replacing the old Photon index."
        mv "${PHOTON_DATA_DIR}/photon_data" "${staging}/old"
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
