#!/usr/bin/env bash
#
# Build a candidate graph natively on this machine, without a container: the same steps as the
# image's `build` (docker/graphhopper-entrypoint.sh), with the jar copied out of the image that
# will serve the graph; it asks whether to rebuild that image first or use the jar copied before.
# Worth it on macOS, where Docker's VM caps memory and bind mounts are slow; on Linux the
# container costs next to nothing. Validation and activation stay in the container
# (just routing-validate-candidate, routing-activate), so the serving jar checks the result.
#
# Needs: Java 25 (the image's), osmium-tool, python3 >= 3.11 (just install-graphhopper-host-tools).
# A container engine only to rebuild GraphHopper or copy its jar out of the image
# (GRAPHHOPPER_IMAGE, default norain-graphhopper:local); a jar copied before needs none.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO=$(pwd -P)

: "${ROUTING_OSM_FILE_FILTERED:?Name the bike-filtered file to build from: just build-graphhopper-graph-host FILE}"
: "${ROUTING_OSM_IMPORT_DIR:?ROUTING_OSM_IMPORT_DIR must be set}"
OSM_DIR=$(cd "$ROUTING_OSM_IMPORT_DIR" && pwd -P)
GRAPH_ROOT="$REPO/data/graphhopper/cache"
HOST_DIR="$REPO/data/graphhopper/host"
BUILD_HEAP="${GRAPHHOPPER_BUILD_HEAP:-${GRAPHHOPPER_HEAP:-6g}}"
BUILD_THREADS="${GRAPHHOPPER_BUILD_THREADS:-3}"
BUILD_DATAACCESS="${GRAPHHOPPER_BUILD_DATAACCESS:-RAM_STORE}"
CONTAINER="${CONTAINER:-docker}"
IMAGE="${GRAPHHOPPER_IMAGE:-norain-graphhopper:local}"

fail() { echo "$*" >&2; exit 1; }

# A file lock for the whole command, like flock(1), which macOS lacks. The lock is on the open
# file, so it survives the exec and holds until the command exits.
locked() {
    local mode=$1 file=$2
    shift 2
    python3 -c '
import fcntl, os, sys
mode, path, *command = sys.argv[1:]
fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB if mode == "ex" else fcntl.LOCK_SH)
except BlockingIOError:
    sys.exit("Another graph build is running.")
os.set_inheritable(fd, True)
os.execvp(command[0], command)
' "$mode" "$file" "$@"
}

if [ "${1:-}" != "--locked" ]; then
    mkdir -p "$GRAPH_ROOT"
    locked ex "$GRAPH_ROOT/.build.lock" bash "$REPO/scripts/graphhopper-host-build.sh" --locked
    exit
fi

# --- Tools -----------------------------------------------------------------------------------
command -v osmium >/dev/null || fail "osmium is missing (the terrain check reads the file's nodes): just install-graphhopper-host-tools"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || fail "python3 >= 3.11 is needed (hashlib.file_digest): just install-graphhopper-host-tools"

if [ -z "${JAVA_HOME:-}" ] && [ -x /usr/libexec/java_home ]; then
    JAVA_HOME=$(/usr/libexec/java_home -v 25 2>/dev/null || true)
fi
JAVA="${JAVA_HOME:+$JAVA_HOME/bin/}java"
java_major=$("$JAVA" -XshowSettings:properties -version 2>&1 | sed -n 's/^ *java\.specification\.version = //p')
# The image runs Java 25; a newer JDK tightens the Unsafe and native access the graph and the
# WebP decoder use. GRAPHHOPPER_ANY_JAVA=1 tries another one anyway.
if [ "$java_major" != 25 ] && [ "${GRAPHHOPPER_ANY_JAVA:-}" != 1 ]; then
    fail "Java 25 is needed, found ${java_major:-none} ($JAVA). Install it (just install-graphhopper-host-tools) or set JAVA_HOME."
fi

# --- The jar --------------------------------------------------------------------------------
# artifact.json records the jar's checksum and GraphHopper revision; serving checks the revision.
# The jar is the image's (the Dockerfile patches GraphHopper), copied into HOST_DIR. The script asks
# whether to rebuild the image first or use the copy as it is; using it needs no container engine.
# CONTAINER is only for the rebuild and the copy. GRAPHHOPPER_JAR_REBUILD=yes|no answers in advance
# (without a terminal and without it: use the copy).
copy_jar_from_image() {
    local image_id cid
    image_id=$("$CONTAINER" image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null) \
        || fail "Image $IMAGE not found. Build it (docker compose build graphhopper) or pull GRAPHHOPPER_IMAGE."
    echo "Copying the jar out of $IMAGE"
    mkdir -p "$HOST_DIR"
    cid=$("$CONTAINER" create "$IMAGE")
    trap '"$CONTAINER" rm "$cid" >/dev/null' EXIT
    "$CONTAINER" cp "$cid:/graphhopper/graphhopper.jar" "$HOST_DIR/graphhopper.jar"
    "$CONTAINER" cp "$cid:/graphhopper/revision" "$HOST_DIR/revision"
    "$CONTAINER" rm "$cid" >/dev/null
    trap - EXIT
    printf '%s\n' "$image_id" > "$HOST_DIR/image-id"
}

rebuild_jar() {
    echo "Rebuilding GraphHopper: $CONTAINER compose build graphhopper"
    "$CONTAINER" compose build graphhopper
    copy_jar_from_image
}

if [ -z "${GRAPHHOPPER_JAR:-}" ]; then
    answer=""
    case "${GRAPHHOPPER_JAR_REBUILD:-}" in
        yes) answer=r ;;
        no) answer=u ;;
        "") ;;
        *) fail "GRAPHHOPPER_JAR_REBUILD must be yes or no, got: $GRAPHHOPPER_JAR_REBUILD" ;;
    esac
    if [ -s "$HOST_DIR/graphhopper.jar" ] && [ -s "$HOST_DIR/revision" ]; then
        if [ -z "$answer" ] && [ -t 0 ]; then
            echo "GraphHopper jar: $HOST_DIR/graphhopper.jar"
            # docker cp keeps the jar's time from the image build; image-id is written by the copy.
            stamp="$HOST_DIR/image-id"
            [ -f "$stamp" ] || stamp="$HOST_DIR/graphhopper.jar"
            echo "  revision $(cut -c1-12 "$HOST_DIR/revision"), copied $(date -r "$stamp" '+%Y-%m-%d %H:%M')"
            read -r -p "[u]se it as is, or [r]ebuild GraphHopper with $CONTAINER compose build first? [U/r] " answer || answer=""
        fi
        case "${answer:-u}" in
            u|U) ;;
            r|R) rebuild_jar ;;
            *) fail "Unknown answer: $answer" ;;
        esac
    else
        [ "$answer" != u ] || fail "No jar in $HOST_DIR to use. Run with GRAPHHOPPER_JAR_REBUILD=yes, or set GRAPHHOPPER_JAR."
        if [ -z "$answer" ]; then
            [ -t 0 ] || fail "No jar in $HOST_DIR. Run with GRAPHHOPPER_JAR_REBUILD=yes, or set GRAPHHOPPER_JAR."
            echo "There is no GraphHopper jar in $HOST_DIR yet."
            if "$CONTAINER" image inspect "$IMAGE" >/dev/null 2>&1; then
                read -r -p "[r]ebuild GraphHopper ($CONTAINER compose build), [c]opy it from the existing image $IMAGE, or [a]bort? [r/c/A] " answer || answer=""
            else
                read -r -p "[r]ebuild GraphHopper ($CONTAINER compose build) or [a]bort? [r/A] " answer || answer=""
            fi
        fi
        case "${answer:-a}" in
            r|R) rebuild_jar ;;
            c|C) copy_jar_from_image ;;
            *) fail "Aborted." ;;
        esac
    fi
    GRAPHHOPPER_JAR="$HOST_DIR/graphhopper.jar"
    GRAPHHOPPER_REVISION_FILE="$HOST_DIR/revision"
fi
: "${GRAPHHOPPER_REVISION_FILE:?Set GRAPHHOPPER_REVISION_FILE with GRAPHHOPPER_JAR}"
export GRAPH_ROOT GRAPHHOPPER_JAR GRAPHHOPPER_REVISION_FILE
export GRAPHHOPPER_CONFIG="$REPO/data/graphhopper/graphhopper-config.yaml"
export GRAPHHOPPER_MODELS="$REPO/data/graphhopper/models"

# --- Inputs ----------------------------------------------------------------------------------
[[ "$BUILD_THREADS" =~ ^[1-9][0-9]*$ ]] || fail "GRAPHHOPPER_BUILD_THREADS must be a positive integer, got: $BUILD_THREADS"
BIKE_DATA_FILE="$OSM_DIR/$ROUTING_OSM_FILE_FILTERED"
# No download-and-filter here, unlike the container: make the file first
# (just osm-filter-many-raw-pbf-into-one, or one container build of bike-<OSM_DATA_URL's name>).
[ -s "$BIKE_DATA_FILE" ] || fail "$BIKE_DATA_FILE does not exist. Make it with just osm-filter-many-raw-pbf-into-one."
[ -d "$OSM_DIR/elevation/current" ] || fail "Elevation data has not been prepared. Run: just download-elevation-for $ROUTING_OSM_FILE_FILTERED"

# --- Check the terrain and open the release, holding the terrain selection stable --------------
terrain_dir=$(cd "$OSM_DIR/elevation/current" && pwd -P)
road_cells=$(mktemp "${TMPDIR:-/tmp}/graphhopper-cells.XXXXXX")
release_file=$(mktemp "${TMPDIR:-/tmp}/graphhopper-release.XXXXXX")
build_config=$(mktemp "${TMPDIR:-/tmp}/graphhopper-build.XXXXXX")
trap 'rm -f "$road_cells" "$release_file" "$build_config"' EXIT
# The serving container mounts ROUTING_OSM_IMPORT_DIR at /osm_data: record the terrain there.
locked sh "$OSM_DIR/elevation/.prepare.lock" bash -c '
    set -euo pipefail
    if [ "$(cd "$1/elevation/current" && pwd -P)" != "$2" ]; then
        echo "The terrain selection changed; run the build again." >&2; exit 1
    fi
    python3 docker/graphhopper-terrain.py check "$3" --root "$1/elevation" --cells-out "$4" || {
        echo "The prepared elevation data is for another OSM file. Run: just download-elevation-for $(basename "$3")" >&2
        exit 1
    }
    python3 docker/graphhopper-artifact.py begin --terrain "$2" \
        --terrain-as "/osm_data/elevation/$(basename "$2")" --cells "$4" --osm "$3" > "$5"
' _ "$OSM_DIR" "$terrain_dir" "$BIKE_DATA_FILE" "$road_cells" "$release_file"
artifact=$(cat "$release_file")

# --- Import ----------------------------------------------------------------------------------
fallback_opts=()
if [ -f "$terrain_dir/fallback.pmtiles" ]; then
    fallback_opts=(
        -Ddw.graphhopper.graph.elevation.pmtiles.fallback.location="$terrain_dir/fallback.pmtiles"
        -Ddw.graphhopper.graph.elevation.pmtiles.fallback.cache_dir="$terrain_dir/cache-fallback"
    )
fi
# From here on this repeats the entrypoint's build(): change both.
# Thread counts go into a copy of the config, never through -Ddw. (see the entrypoint's build).
sed -E "s/^([[:space:]]*(prepare\.ch|prepare\.lm|graph\.urban_density|prepare\.subnetworks)\.threads:)[[:space:]]*[0-9]+/\1 ${BUILD_THREADS}/" \
    "$artifact/config.yaml" > "$build_config"
if [ "$(grep -cE '^[[:space:]]*(prepare\.ch|prepare\.lm|graph\.urban_density|prepare\.subnetworks)\.threads: '"${BUILD_THREADS}"'$' "$build_config")" -ne 4 ]; then
    fail "Could not set the four build thread counts in $artifact/config.yaml."
fi
echo "Building candidate $artifact on the host (heap $BUILD_HEAP, $BUILD_THREADS threads, $BUILD_DATAACCESS)."
"$JAVA" --enable-native-access=ALL-UNNAMED -Xmx"$BUILD_HEAP" \
    -Ddw.graphhopper.graph.location="$artifact/graph" \
    -Ddw.graphhopper.custom_models.directory="$artifact/models" \
    -Ddw.graphhopper.graph.elevation.pmtiles.location="$terrain_dir/terrain.pmtiles" \
    -Ddw.graphhopper.graph.elevation.cache_dir="$terrain_dir/cache" \
    ${fallback_opts[@]+"${fallback_opts[@]}"} \
    -Ddw.graphhopper.graph.dataaccess.default_type="$BUILD_DATAACCESS" \
    -Ddw.graphhopper.datareader.file="$BIKE_DATA_FILE" \
    -jar "$GRAPHHOPPER_JAR" import "$build_config"
python3 docker/graphhopper-artifact.py finish "${artifact#"$GRAPH_ROOT"/}"
echo "Candidate ready. Validate it in the container: just routing-validate-candidate '[[lon,lat],[lon,lat]]'"
