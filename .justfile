set dotenv-load
set windows-shell := ["pwsh.exe", "/c"]
#set shell := ["bash", "-c"]

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

# The bike-filtered file every graph build reads; same default as the GraphHopper entrypoint.
# just osm-filter-many-raw-pbf-into-one writes it, so for such files it must be set in .env.
routing_osm_file_filtered := env("ROUTING_OSM_FILE_FILTERED", "bike-" + file_name(osm_data_url))

# The POIs that belong to that file: pois-<its name without bike- and .osm.pbf>, next to it in
# ROUTING_OSM_IMPORT_DIR. just poi-extract-from-unfiltered-osm-pbf and just osm-filter-many-raw-pbf-into-one write it, just poi-import-into-db reads it.
pois_file := "pois-" + trim_start_match(without_extension(without_extension(routing_osm_file_filtered)), "bike-") + ".geojsonseq"

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

[doc("Start PostGIS, Redis, GraphHopper and Photon on the host ports set in .env. GraphHopper needs an activated graph first; see docs/how-to/build-routing-graph.md.")]
[group('dev')]
services:
    {{ container }} compose up -d db redis graphhopper photon

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

[group("geodata")]
download-elevation-for:
    echo NotImplemented

[doc("Estimate the zoom-15 Mapterhorn download for a filtered OSM file. The file must already exist in ROUTING_OSM_IMPORT_DIR.")]
[group('geodata')]
routing-terrain-estimate filtered_pbf:
    {{ container }} compose run --rm --no-deps -e ROUTING_OSM_FILE_FILTERED={{ quote(file_name(filtered_pbf)) }} graphhopper terrain --dry-run

[doc("Prepare and verify zoom-15 Mapterhorn terrain for a filtered OSM file; keeps the active graph running.")]
[group('geodata')]
routing-terrain-from filtered_pbf:
    {{ container }} compose run --rm --no-deps -e ROUTING_OSM_FILE_FILTERED={{ quote(file_name(filtered_pbf)) }} graphhopper terrain

[doc("Import a candidate graph using prepared Mapterhorn terrain. Does not stop, delete or activate the current graph.")]
[group('geodata')]
build-graphhopper-graph-from filtered_pbf:
    {{ container }} compose run --rm --no-deps -e ROUTING_OSM_FILE_FILTERED={{ quote(file_name(filtered_pbf)) }} graphhopper build

[doc('Start an isolated candidate and test all profiles. Points: JSON [[lon,lat],[lon,lat]] within the graph. Marks a passing candidate ready for activation.')]
[group('geodata')]
routing-validate-candidate points:
    CONTAINER={{ quote(container) }} uv run --no-project python scripts/routing-candidate.py {{ quote(points) }}

[doc("Activate the validated candidate with the currently configured GraphHopper image. Preserve the old image/config for rollback before the first 10.2 migration.")]
[group('geodata')]
routing-activate:
    CONTAINER={{ quote(container) }} uv run --no-project python scripts/routing-switch.py activate

[doc("Restore the previous managed graph with its matching image (pass the saved immutable image ID/tag). For a legacy 10.2 graph see the migration guide.")]
[group('geodata')]
routing-rollback image:
    CONTAINER={{ quote(container) }} uv run --no-project python scripts/routing-switch.py rollback {{ quote(image) }}

[doc("Print each bike profile's average speed on a few reference routes. Run it after routing-activate to see what a speed change did.")]
[group('geodata')]
routing-speeds *args:
    uv run --project backend python scripts/routing_speeds.py {{ args }}

[doc("Re-route every saved route against the current graph. Needs a worker on the default queue; run it after a new graph, or stored arrival times stay old.")]
[group('geodata')]
[working-directory("backend")]
routing-refresh-routes:
    uv run python manage.py shell -c "from core.models import RecurringRoute; from core.tasks import refresh_route_geometry; print(sum(refresh_route_geometry.enqueue(str(i)) is not None for i in RecurringRoute.objects.values_list('id', flat=True)), 'routes queued')"

[doc("Filter raw .osm.pbf files for bikes and merge them into ROUTING_OSM_FILE_FILTERED (e.g. bike-europe-cycling.osm.pbf) in ROUTING_OSM_IMPORT_DIR, plus the matching POI file, e.g. just osm-filter-many-raw-pbf-into-one ~/osm/germany-latest.osm.pbf ~/osm/austria-latest.osm.pbf. Builds no graph: prepare terrain with routing-terrain-from, then import, validate and activate the candidate.")]
[group('geodata')]
[confirm("This overwrites ROUTING_OSM_FILE_FILTERED (" + env("ROUTING_OSM_IMPORT_DIR") +"/"+ env("ROUTING_OSM_FILE_FILTERED") + ") and its POI file. Continue?")]
[positional-arguments]
[unix]
osm-filter-many-raw-pbf-into-one +files:
    CONTAINER={{ quote(container) }} ROUTING_OSM_FILE_FILTERED={{ quote(env("ROUTING_OSM_FILE_FILTERED", "")) }} INVOCATION_DIR={{ quote(invocation_directory_native()) }} bash scripts/osm-filter-many-raw-pbf-into-one.sh "$@"

# Git Bash: just runs a shebang recipe through cygpath, which Git does not put on PATH, and
# the bash on PATH is WSL's. [script] needs neither.
[doc("Filter raw .osm.pbf files for bikes and merge them into ROUTING_OSM_FILE_FILTERED (e.g. bike-europe-cycling.osm.pbf) in ROUTING_OSM_IMPORT_DIR, plus the matching POI file, e.g. just osm-filter-many-raw-pbf-into-one ~/osm/germany-latest.osm.pbf ~/osm/austria-latest.osm.pbf. Builds no graph: prepare terrain with routing-terrain-from, then import, validate and activate the candidate.")]
[group('geodata')]
[confirm("This overwrites ROUTING_OSM_FILE_FILTERED and its POI file. Continue?")]
[positional-arguments]
[windows]
[script("C:/Program Files/Git/bin/bash.exe", "-eu")]
osm-filter-many-raw-pbf-into-one +files:
    CONTAINER={{ quote(container) }} ROUTING_OSM_FILE_FILTERED={{ quote(env("ROUTING_OSM_FILE_FILTERED", "")) }} INVOCATION_DIR={{ quote(invocation_directory_native()) }} "$BASH" scripts/osm-filter-many-raw-pbf-into-one.sh "$@"

[doc("Extract the journey planner's POIs (water, toilets, shelters, lodging, ...) from raw .osm.pbf files, anywhere (several are merged), into the POI file named after ROUTING_OSM_FILE_FILTERED in ROUTING_OSM_IMPORT_DIR, e.g. just poi-extract-from-unfiltered-osm-pbf data/downloads/osm/*.osm.pbf. Use the same raw files the graph was filtered from, never a bike-*.osm.pbf: the bike filter dropped almost every POI. just poi-import-into-db loads it.")]
[group('geodata')]
[positional-arguments]
[unix]
poi-extract-from-unfiltered-osm-pbf +files:
    CONTAINER={{ quote(container) }} POIS_FILE={{ quote(pois_file) }} INVOCATION_DIR={{ quote(invocation_directory_native()) }} bash scripts/poi-extract-from-unfiltered-osm-pbf.sh "$@"

# Git Bash: see osm-filter-many-raw-pbf-into-one.
[doc("Extract the journey planner's POIs (water, toilets, shelters, lodging, ...) from raw .osm.pbf files, anywhere (several are merged), into the POI file named after ROUTING_OSM_FILE_FILTERED in ROUTING_OSM_IMPORT_DIR, e.g. just poi-extract-from-unfiltered-osm-pbf data/downloads/osm/*.osm.pbf. Use the same raw files the graph was filtered from, never a bike-*.osm.pbf: the bike filter dropped almost every POI. just poi-import-into-db loads it.")]
[group('geodata')]
[positional-arguments]
[windows]
[script("C:/Program Files/Git/bin/bash.exe", "-eu")]
poi-extract-from-unfiltered-osm-pbf +files:
    CONTAINER={{ quote(container) }} POIS_FILE={{ quote(pois_file) }} INVOCATION_DIR={{ quote(invocation_directory_native()) }} "$BASH" scripts/poi-extract-from-unfiltered-osm-pbf.sh "$@"

[doc("Replace the POI table with a POI file in ROUTING_OSM_IMPORT_DIR, by default the one just poi-extract-from-unfiltered-osm-pbf writes (named after ROUTING_OSM_FILE_FILTERED). Readers keep the old POIs until the new set is in. With a prod COMPOSE_FILE it runs in worker-default (the backend image has GDAL, the VPS host none), else in the host's virtualenv.")]
[group('geodata')]
poi-import-into-db file=pois_file:
    {{ if env("COMPOSE_FILE", "") =~ 'prod' { container + " compose run --rm --pull never --no-deps worker-default python manage.py import_pois " + quote("/osm_data/" + file_name(file)) } else { "cd backend && uv run python manage.py import_pois " + quote(join(justfile_directory(), env("ROUTING_OSM_IMPORT_DIR"), file_name(file))) } }}

[doc("Build the geocoder index from local Photon 1.0 dumps (.jsonl.zst or .jsonl; several become one index) or one prebuilt index (.tar.bz2), e.g. just photon-import photon_dumps/*.jsonl. The current index is replaced only once the new one is ready.")]
[group('geodata')]
[confirm("This replaces the local geocoder index with one built from the files. Continue?")]
[positional-arguments]
[unix]
photon-import +files:
    CONTAINER={{ quote(container) }} INVOCATION_DIR={{ quote(invocation_directory_native()) }} bash scripts/photon-import.sh "$@"

# Git Bash: just runs a shebang recipe through cygpath, which Git does not put on PATH, and
# the bash on PATH is WSL's. [script] needs neither.
[doc("Build the geocoder index from local Photon 1.0 dumps (.jsonl.zst or .jsonl; several become one index) or one prebuilt index (.tar.bz2), e.g. just photon-import photon_dumps/*.jsonl. The current index is replaced only once the new one is ready.")]
[group('geodata')]
[confirm("This replaces the local geocoder index with one built from the files. Continue?")]
[positional-arguments]
[windows]
[script("C:/Program Files/Git/bin/bash.exe", "-eu")]
photon-import +files:
    CONTAINER={{ quote(container) }} INVOCATION_DIR={{ quote(invocation_directory_native()) }} "$BASH" scripts/photon-import.sh "$@"

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

[doc("Download the OSM extracts into data/downloads/osm (only what changed since); filter them with just osm-filter-many-raw-pbf-into-one, then just build-graphhopper-graph-from <that file>.")]
[windows]
download-pbf:
    wsl bash -c "chmod +x scripts/download-pbf.sh && ./scripts/download-pbf.sh"

[doc("Download the Photon dumps into data/downloads/photon (only what changed since); build the index from them with just photon-import.")]
[windows]
download-photon-dumps:
    wsl bash -c "chmod +x scripts/download-photon-dumps.sh && ./scripts/download-photon-dumps.sh"

[doc("Download the OSM extracts into data/downloads/osm (only what changed since); filter them with just osm-filter-many-raw-pbf-into-one, then just build-graphhopper-graph-from <that file>.")]
[linux]
download-pbf:
    chmod +x scripts/download-pbf.sh
    ./scripts/download-pbf.sh

[doc("Download the Photon dumps into data/downloads/photon (only what changed since); build the index from them with just photon-import.")]
[linux]
download-photon-dumps:
    chmod +x scripts/download-photon-dumps.sh
    ./scripts/download-photon-dumps.sh

[working-directory("backend")]
lint-backend:
    ruff format
    ruff check --fix

[working-directory("frontend")]
lint-frontend:
    npm run lint

lint: lint-backend lint-frontend