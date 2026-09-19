set dotenv-load
set windows-shell := ["pwsh.exe", "/c"]
set shell := ["bash", "-c"]

localappdata := env("LOCALAPPDATA", "")
gdal_path := localappdata + "\\Programs\\OSGeo4W"

# Export these so that uv and the compiler can see them

export GDAL_HOME := if os_family() == "windows" { gdal_path } else { env("GDAL_HOME", "") }
export INCLUDE := if os_family() == "windows" { gdal_path + "\\include" } else { env("INCLUDE", "") }
export LIB := if os_family() == "windows" { gdal_path + "\\lib" } else { env("LIB", "") }
export GDAL_VERSION := "3.13.1"

image_prefix := "ghcr.io/mnboos/norain"

# podman on Windows, docker elsewhere, podman when there is no docker binary (a shell alias
# `docker=podman` doesn't count: recipes run in a non-interactive shell); override with CONTAINER_ENGINE.
container := env("CONTAINER_ENGINE", if os_family() == "windows" { "podman" } else { `command -v docker >/dev/null && echo docker || echo podman` })

# The extract every graph build uses; same default as docker-compose.base.yml.
osm_data_url := env("OSM_DATA_URL", "https://download.geofabrik.de/europe/switzerland-latest.osm.pbf")

# List the recipes by group.
default:
    @just --list

[group('setup')]
[windows]
[working-directory("backend")]
setup:
    @echo LIB=%LIB%
    uv add gdal==%GDAL_VERSION%
    uv venv --clear
    uv sync

[group('setup')]
[unix]
[working-directory("backend")]
setup:
    if [ "{{ os() }}" = "macos" ]; then brew list gdal >/dev/null 2>&1 || brew install gdal; fi
    uv venv --clear
    uv sync

[doc("Start PostGIS, Redis, GraphHopper and Photon on the host ports set in .env.")]
[group('dev')]
services:
    {{ container }} compose -f docker-compose.dev.yml up -d db redis graphhopper photon

[doc("Run Django on BACKEND_PORT from .env (default 8000).")]
[group('dev')]
[working-directory("backend")]
backend:
    uv run python manage.py runserver

[doc("Run Vite on FRONTEND_PORT from .env (default 3000).")]
[group('dev')]
[working-directory("frontend")]
frontend:
    npm run dev

[doc("Run any Django management command locally, e.g. just manage showmigrations.")]
[group('tasks')]
[working-directory("backend")]
manage +args:
    uv run python manage.py {{ args }}

[doc("Process background tasks locally; defaults to all queues. Optionally pass default, cells, compute or forecasts.")]
[group('tasks')]
[working-directory("backend")]
worker queue='*':
    uv run python manage.py db_worker --queue-name '{{ queue }}'

[doc("Queue forecast pre-warming for all eligible routes. Requires default and cells workers.")]
[group('tasks')]
[working-directory("backend")]
forecast-refresh-all:
    uv run python manage.py refresh_forecasts

[doc("Queue missing/stale forecast cells for a route's next three departures. Requires geometry and default/cells workers; reuses fresh cells.")]
[group('tasks')]
[working-directory("backend")]
forecast-refresh $route_id:
    uv run python manage.py shell -c "import os; from core.models import RecurringRoute; from core.tasks import scan_route_forecasts; route = RecurringRoute.objects.get(id=os.environ['route_id']); print('Queued route scan:', scan_route_forecasts.enqueue(str(route.id)).id)"

[doc("Queue a geometry refresh for one route against the current routing graph. Requires a default worker.")]
[group('tasks')]
[working-directory("backend")]
routing-refresh-route $route_id:
    uv run python manage.py shell -c "import os; from core.models import RecurringRoute; from core.tasks import refresh_route_geometry; route = RecurringRoute.objects.get(id=os.environ['route_id']); print('Queued geometry refresh:', refresh_route_geometry.enqueue(str(route.id)).id)"

[doc("Queue a route thumbnail rebuild using cached weather. Requires a default worker.")]
[group('tasks')]
[working-directory("backend")]
thumbnail-refresh $route_id:
    uv run python manage.py shell -c "import os; from core.models import RecurringRoute; from core.tasks import refresh_route_thumbnail; route = RecurringRoute.objects.get(id=os.environ['route_id']); print('Queued thumbnail refresh:', refresh_route_thumbnail.enqueue(str(route.id)).id)"

[doc("Preview missing route timings; pass --enqueue to queue repairs, optionally --route-id UUID or --limit N.")]
[group('tasks')]
[working-directory("backend")]
routing-backfill *args:
    uv run python manage.py backfill_route_vertex_times {{ args }}

[doc("Rebuild the local routing graph after editing graphhopper-config.yaml or data/graphhopper/models/ (ride speeds live there). Deletes data/graphhopper/cache and builds it again from the bike-filtered extract; the OSM extract and elevation tiles are kept. Takes minutes.")]
[group('geodata')]
[confirm("This deletes the local routing graph and builds it again. Continue?")]
routing-build:
    {{ container }} compose -f docker-compose.dev.yml stop graphhopper
    {{ container }} compose -f docker-compose.dev.yml run --rm --entrypoint bash graphhopper -c 'rm -rf /graph-cache/..?* /graph-cache/.[!.]* /graph-cache/*'
    {{ container }} compose -f docker-compose.dev.yml run --rm -e GRAPHHOPPER_BUILD_ONLY=true graphhopper
    {{ container }} compose -f docker-compose.dev.yml up -d graphhopper

[doc("Print each bike profile's average speed on a few reference routes. Run it after routing-build to see what a speed change did.")]
[group('geodata')]
routing-speeds *args:
    uv run --project backend python scripts/routing_speeds.py {{ args }}

[doc("Re-route every saved route against the current graph. Needs a worker on the default queue; run it after a new graph, or stored arrival times stay old.")]
[group('geodata')]
[working-directory("backend")]
routing-refresh-routes:
    uv run python manage.py shell -c "from core.models import RecurringRoute; from core.tasks import refresh_route_geometry; print(sum(refresh_route_geometry.enqueue(str(i)) is not None for i in RecurringRoute.objects.values_list('id', flat=True)), 'routes queued')"

[doc("Build the routing graph from local .osm.pbf files instead of downloading OSM_DATA_URL, e.g. just osm-import ~/osm/germany-latest.osm.pbf ~/osm/austria-latest.osm.pbf. Filters them for bikes like every build and merges several into one. One file must have OSM_DATA_URL's file name; for several, set OSM_DATA_URL to a plain file name for the merged set, e.g. dach.osm.pbf. Deletes the current graph first; the elevation tiles are kept. Takes minutes.")]
[group('geodata')]
[confirm("This deletes the local routing graph and builds a new one from the files. Continue?")]
[positional-arguments]
osm-import +files:
    #!/usr/bin/env bash
    set -euo pipefail
    # Every build (routing-build, a fresh start) reads /osm_data/bike-<OSM_DATA_URL's file name>,
    # so the import writes that file, or the next rebuild would use other data.
    name="{{ file_name(osm_data_url) }}"
    if [ $# -eq 1 ] && [ "$(basename "$1")" != "$name" ]; then
        echo "OSM_DATA_URL in .env ends in $name. Set it to a URL, or just the file name, ending in $(basename "$1")." >&2
        exit 1
    fi
    if [ $# -gt 1 ] && [[ "{{ osm_data_url }}" == *://* ]]; then
        echo "Several files are merged into one, and no download matches that. Set OSM_DATA_URL in .env to a plain file name for the merged set, e.g. dach.osm.pbf." >&2
        exit 1
    fi
    # File names are relative to where just was run. Each file is mounted at /import/<its name>.
    cd "{{ invocation_directory() }}"
    declare -A seen=()
    mounts=() inputs=()
    for file in "$@"; do
        base=$(basename "$file")
        [ -f "$file" ] || { echo "Not a file: $file" >&2; exit 1; }
        [ -z "${seen[$base]:-}" ] || { echo "Two files are named $base." >&2; exit 1; }
        seen[$base]=1
        mounts+=(-v "$(cd "$(dirname "$file")" && pwd)/$base:/import/$base:ro,z")
        inputs+=("/import/$base")
    done
    cd "{{ justfile_directory() }}"
    compose=({{ container }} compose -f docker-compose.dev.yml)
    "${compose[@]}" build graphhopper
    "${compose[@]}" stop graphhopper
    "${compose[@]}" run --rm --no-deps "${mounts[@]}" --entrypoint /graphhopper/filter-osm.sh graphhopper "/osm_data/bike-$name" "${inputs[@]}"
    "${compose[@]}" run --rm --no-deps --entrypoint bash graphhopper -c 'rm -rf /graph-cache/..?* /graph-cache/.[!.]* /graph-cache/*'
    # The filtered file is now newer than any extract in /osm_data, so the entrypoint builds from it without a download.
    "${compose[@]}" run --rm --no-deps -e GRAPHHOPPER_BUILD_ONLY=true graphhopper
    "${compose[@]}" up -d graphhopper

[doc("Build the geocoder index from local Photon 1.0 dumps (.jsonl.zst or .jsonl; several become one index) or one prebuilt index (.tar.bz2), e.g. just photon-import photon_dumps/*.jsonl. The current index is replaced only once the new one is ready.")]
[group('geodata')]
[confirm("This replaces the local geocoder index with one built from the files. Continue?")]
[positional-arguments]
photon-import +files:
    #!/usr/bin/env bash
    set -euo pipefail
    # File names are relative to where just was run. Each file is mounted at /import/<its name>;
    # PHOTON_INDEX_FILE separates them with spaces, so a name must not have one.
    cd "{{ invocation_directory() }}"
    declare -A seen=()
    mounts=() inputs=()
    for file in "$@"; do
        base=$(basename "$file")
        [ -f "$file" ] || { echo "Not a file: $file" >&2; exit 1; }
        [[ "$base" != *" "* ]] || { echo "File names with spaces don't work here: $base" >&2; exit 1; }
        [ -z "${seen[$base]:-}" ] || { echo "Two files are named $base." >&2; exit 1; }
        seen[$base]=1
        mounts+=(-v "$(cd "$(dirname "$file")" && pwd)/$base:/import/$base:ro,z")
        inputs+=("/import/$base")
    done
    cd "{{ justfile_directory() }}"
    compose=({{ container }} compose -f docker-compose.dev.yml)
    "${compose[@]}" build photon
    "${compose[@]}" stop photon
    "${compose[@]}" run --rm --no-deps "${mounts[@]}" \
        -e PHOTON_INDEX_FILE="${inputs[*]}" -e PHOTON_REPLACE_INDEX=true \
        -e PHOTON_IMPORT_ONLY=true -e PHOTON_ALLOW_DOWNLOAD=false photon
    "${compose[@]}" up -d photon

[group('api')]
[working-directory("backend")]
export-openapi-schema:
    uv run python manage.py export_openapi_schema --api core.api.api --indent 4 --output openapi.json

# (Re)generate the API client for the frontend from the schema.

[group('api')]
[unix]
[working-directory("packages/api")]
delete-api:
    rm -rf apis/
    rm -rf models/

[group('api')]
[windows]
[working-directory("packages/api")]
delete-api:
    if (Test-Path "apis/") { Remove-Item "apis/" -Recurse -Force }
    if (Test-Path "models/") { Remove-Item "models/" -Recurse -Force }

[group('api')]
[working-directory("backend")]
update-api--build-only: delete-api
    uv run openapi-generator-cli generate \
        --global-property "supportingFiles,apis,apiTests,models,apiDocs=false,modelDocs=false" \
        -i openapi.json \
        -g typescript-fetch \
        -o ../packages/api \
        --enable-post-process-file \
        -c api-generator.typescript-fetch.additionalProperties.json
    uv run python add_ts_nocheck.py

[group('api')]
[working-directory("frontend")]
update-api: export-openapi-schema update-api--build-only
    npm run lint
    npm run build

[doc("Build and deploy the checked-out source on the current VPS. Requires production .env settings and local image tags.")]
[group('deploy')]
deploy-local:
    SENTRY_RELEASE="$(git rev-parse HEAD)" {{ container }} compose --env-file .env build
    {{ container }} compose --env-file .env pull db redis
    {{ container }} compose --env-file .env run --rm --pull never backend python manage.py migrate --noinput
    {{ container }} compose --env-file .env run --rm --pull never --user root backend python manage.py collectstatic --noinput
    {{ container }} compose --env-file .env up -d --pull never --remove-orphans

# Mirrors the deploy job in .github/workflows/release.yml; the images must already be published.
[doc("Release a commit's published images to the VPS over SSH, then check health. Needs VPS_USER, VPS_HOST, VPS_PUBLIC_HEALTH_URL. Roll back with an older sha.")]
[group('deploy')]
deploy sha=`git rev-parse HEAD`:
    ssh -o StrictHostKeyChecking=yes {{ env("VPS_USER") }}@{{ env("VPS_HOST") }} "cd /srv/norain && BACKEND_IMAGE={{ image_prefix }}-backend:{{ sha }} FRONTEND_IMAGE={{ image_prefix }}-frontend:{{ sha }} GRAPHHOPPER_IMAGE={{ image_prefix }}-graphhopper:{{ sha }} PHOTON_IMAGE={{ image_prefix }}-photon:{{ sha }} ./deploy/release.sh"
    curl --fail --retry 12 --retry-delay 5 --retry-connrefused {{ env("VPS_PUBLIC_HEALTH_URL") }}

[env("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")]
[env("ANTHROPIC_DEFAULT_HAIKU_MODEL", "deepseek-v4-flash")]
[env("ANTHROPIC_DEFAULT_OPUS_MODEL", "deepseek-v4-pro[1m]")]
[env("ANTHROPIC_DEFAULT_SONNET_MODEL", "deepseek-v4-pro[1m]")]
[env("ANTHROPIC_MODEL", "deepseek-v4-pro[1m]")]
[env("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")]
[env("CLAUDE_CODE_EFFORT_LEVEL", "max")]
[env("CLAUDE_CODE_SUBAGENT_MODEL", "deepseek-v4-flash")]
[group('tools')]
claude-deepseek:
    claude --model opus --effort max

alias claude := claude-deepseek

[doc("Download the OSM extracts into data/downloads/osm (only what changed since); build the graph from them with just osm-import.")]
[windows]
download-pbf:
    wsl bash -c "chmod +x scripts/download-pbf.sh && ./scripts/download-pbf.sh"

[doc("Download the Photon dumps into data/downloads/photon (only what changed since); build the index from them with just photon-import.")]
[windows]
download-photon-dumps:
    wsl bash -c "chmod +x scripts/download-photon-dumps.sh && ./scripts/download-photon-dumps.sh"

[doc("Download the OSM extracts into data/downloads/osm (only what changed since); build the graph from them with just osm-import.")]
[linux]
download-pbf:
    chmod +x scripts/download-pbf.sh
    ./scripts/download-pbf.sh

[doc("Download the Photon dumps into data/downloads/photon (only what changed since); build the index from them with just photon-import.")]
[linux]
download-photon-dumps:
    chmod +x scripts/download-photon-dumps.sh
    ./scripts/download-photon-dumps.sh