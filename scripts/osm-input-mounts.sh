# Sourced by the scripts that read raw .osm.pbf files in the graphhopper container. Fills
# `mounts` (compose run -v options) and `inputs` (the container paths) for the files named:
# each is mounted read-only at /import/<its name>. File names are relative to INVOCATION_DIR,
# where just was run. `pwd -W` is Git Bash's C:/… form, which podman on Windows understands;
# elsewhere it fails and plain `pwd` answers.
#
# On Windows this runs in Git Bash, which would rewrite every /container/path argument into a
# Windows path before podman sees it.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

osm_input_mounts() {
    local repo file base previous
    mounts=() inputs=()
    repo=$(pwd)
    cd "$INVOCATION_DIR"
    for file in "$@"; do
        base=$(basename "$file")
        [ -f "$file" ] || { echo "Not a file: $file" >&2; exit 1; }
        # macOS ships Bash 3.2: use the indexed paths instead of an associative array.
        # The default expansion also handles an empty array under `set -u` there.
        for previous in "${inputs[@]-}"; do
            [ "$previous" != "/import/$base" ] || { echo "Two files are named $base." >&2; exit 1; }
        done
        mounts+=(-v "$(cd "$(dirname "$file")" && { pwd -W 2>/dev/null || pwd; })/$base:/import/$base:ro,z")
        inputs+=("/import/$base")
    done
    cd "$repo"
}
