#!/usr/bin/env bash
#
# Build the geocoder index from local Photon dumps or one prebuilt index: `just photon-import
# FILE…` runs this. The current index is replaced only once the new one is ready.
#
# Takes from just: CONTAINER (podman or docker) and INVOCATION_DIR, which the file names are
# relative to.
set -euo pipefail

# On Windows this runs in Git Bash, which would rewrite every /container/path argument into a
# Windows path before podman sees it.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

# File names are relative to where just was run. Each file is mounted at /import/<its name>;
# PHOTON_INDEX_FILE separates them with spaces, so a name must not have one.
# `pwd -W` is Git Bash's C:/… form, which podman on Windows understands; elsewhere it fails
# and plain `pwd` answers.
repo=$(pwd)
cd "$INVOCATION_DIR"
declare -A seen=()
mounts=() inputs=()
for file in "$@"; do
    base=$(basename "$file")
    [ -f "$file" ] || { echo "Not a file: $file" >&2; exit 1; }
    [[ "$base" != *" "* ]] || { echo "File names with spaces don't work here: $base" >&2; exit 1; }
    [ -z "${seen[$base]:-}" ] || { echo "Two files are named $base." >&2; exit 1; }
    seen[$base]=1
    mounts+=(-v "$(cd "$(dirname "$file")" && { pwd -W 2>/dev/null || pwd; })/$base:/import/$base:ro,z")
    inputs+=("/import/$base")
done
cd "$repo"

compose=("$CONTAINER" compose -f docker-compose.dev.yml)
"${compose[@]}" build photon
"${compose[@]}" stop photon
"${compose[@]}" run --rm --no-deps "${mounts[@]}" \
    -e PHOTON_INDEX_FILE="${inputs[*]}" -e PHOTON_REPLACE_INDEX=true \
    -e PHOTON_IMPORT_ONLY=true -e PHOTON_ALLOW_DOWNLOAD=false photon
"${compose[@]}" up -d photon
