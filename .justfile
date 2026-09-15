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

# podman on Windows, docker elsewhere; override with CONTAINER_ENGINE.
container := env("CONTAINER_ENGINE", if os_family() == "windows" { "podman" } else { "docker" })

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

[doc("Import a ready-to-use index that you can download from the Graphhopper page.")]
[group('geodata')]
setup-geocoder:
    # wget https://download1.graphhopper.com/public/europe/switzerland-liechtenstein/photon-dump-switzerland-liechtenstein-1.0-latest.jsonl.zst
    {{ container }} compose run --entrypoint bash -v ./photon-dump-switzerland-liechtenstein-1.0-latest.jsonl.zst:/photon-dump.jsonl.zst photon -c /import-photon-dump.sh
    #podman compose run --entrypoint bash -v ./photon-dump-austria-1.0-latest.jsonl.zst:/photon-dump.jsonl.zst photon -c /import-photon-dump.sh

[doc("Import the Switzerland OSM PBF into Photon for geocoding. This is a one-time setup step. Use this only if you want to re-import the PBF into Photon, e.g. after an OSM update. If possible, use the exported index from the Graphhopper page.")]
[group('geodata')]
photon-import-pbf:
    {{ container }} compose run --entrypoint bash -v ./data/switzerland-latest.osm.pbf:/switzerland-latest.osm.pbf photon -osm-pbf=switzerland-latest.osm.pbf -country-codes="CH" -languages=de,fr,it,en

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
    {{ container }} compose --env-file .env -f docker-compose.prod.yml build
    {{ container }} compose --env-file .env -f docker-compose.prod.yml pull db redis
    {{ container }} compose --env-file .env -f docker-compose.prod.yml run --rm --pull never backend python manage.py migrate --noinput
    {{ container }} compose --env-file .env -f docker-compose.prod.yml run --rm --pull never --user root backend python manage.py collectstatic --noinput
    {{ container }} compose --env-file .env -f docker-compose.prod.yml up -d --pull never --remove-orphans

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
