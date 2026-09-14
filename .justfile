set dotenv-load
set windows-shell := ["pwsh.exe", "/c"]
set shell := ["bash", "-c"]

localappdata := env("LOCALAPPDATA", "")
gdal_path := localappdata + "\\Programs\\OSGeo4W"

# Export these so that uv and the compiler can see them

export GDAL_HOME := gdal_path
export INCLUDE := gdal_path + "\\include"
export LIB := gdal_path + "\\lib"
export GDAL_VERSION := "3.13.1"

recipe-name:
    echo 'This is a recipe!'

# this is a comment
another-recipe:
    @echo 'This is another recipe.'

build:
    docker compose build

# Print a stats report for a GTFS ZIP (routes per type, trips, stops, stop_times, transfers).
gtfs-stats gtfs-input:
    uv run --project backend gtfs-stats.py {{ gtfs-input }}

# Filter the Swiss GTFS to rail/tram/ferry only (drops buses to reduce OTP memory ~80%).
# Run once after downloading the GTFS, before build-otp-graph.

# Example: just filter-gtfs data/otp/gtfs_fp2026_20260408.zip
filter-gtfs gtfs-input output="data/otp/gtfs-rail.zip":
    uv run --project backend filter-gtfs.py {{ gtfs-input }} {{ output }}

# Filter the Switzerland OSM PBF to transport-relevant tags only (highways, transit stops,
# turn restrictions). Reduces PBF size ~50-70% and OTP runtime memory ~15-30%.

# Run once after downloading the OSM PBF, before build-otp-graph.
[unix]
filter-osm:
    docker build -q -t job-graph/osmium-tool -f docker/osmium.Dockerfile .
    docker run --rm \
        -v "{{ justfile_directory() }}/data:/data" \
        job-graph/osmium-tool \
        osmium tags-filter \
            /data/switzerland-latest.osm.pbf \
            w/highway \
            wa/public_transport=platform \
            wa/railway=platform \
            w/park_ride=yes \
            r/type=restriction \
            r/type=route \
            -o /data/otp/switzerland-transport.osm.pbf \
            --overwrite

[windows]
filter-osm:
    docker build -q -t job-graph/osmium-tool -f docker/osmium.Dockerfile .
    docker run --rm -v ./data:/data job-graph/osmium-tool osmium tags-filter /data/switzerland-latest.osm.pbf w/highway wa/public_transport=platform wa/railway=platform w/park_ride=yes r/type=restriction r/type=route -o /data/otp/switzerland-transport.osm.pbf --overwrite

# Build the OTP routing graph from OSM + filtered GTFS data.

# Run once during initial setup, and again after OSM or GTFS data updates.
[unix]
build-otp-graph:
    docker run --rm \
        -e JAVA_TOOL_OPTIONS='-XX:MaxRAMPercentage=80' \
        -v "{{ justfile_directory() }}/data/otp:/var/opentripplanner" \
        -v "{{ justfile_directory() }}/data/otp/switzerland-transport.osm.pbf:/var/opentripplanner/switzerland-transport.osm.pbf" \
        opentripplanner/opentripplanner:2.9.0 --build --save

[windows]
build-otp-graph:
    docker run --rm -v ./data/otp:/var/opentripplanner -v ./data/otp/switzerland-transport.osm.pbf:/var/opentripplanner/switzerland-transport.osm.pbf opentripplanner/opentripplanner:2.9.0 --build --save

# Import OSM + GTFS data into MOTIS (run once, or after data updates).
[unix]
build-motis:
    mkdir -p "{{ justfile_directory() }}/data/motis/data"
    docker run --rm \
        -v "{{ justfile_directory() }}/data/otp/switzerland-transport.osm.pbf:/input/switzerland-transport.osm.pbf:ro" \
        -v "{{ justfile_directory() }}/data/otp/gtfs-rail.zip:/input/gtfs-rail.zip:ro" \
        -v "{{ justfile_directory() }}/data/motis/config.yml:/config.yml:ro" \
        -v "{{ justfile_directory() }}/data/motis/data:/data" \
        ghcr.io/motis-project/motis:master \
        ./motis import

[windows]
build-motis:
    docker run --rm -v ./data/otp/switzerland-transport.osm.pbf:/input/switzerland-transport.osm.pbf:ro -v ./data/otp/gtfs-rail.zip:/input/gtfs-rail.zip:ro -v ./data/motis/config.yml:/config.yml:ro -v ./data/motis/data:/data ghcr.io/motis-project/motis:master ./motis import

print-justfile-dir:
    echo justfile dir: '{{ justfile_directory() }}'

[linux]
printenv: print-justfile-dir
    echo foobar $GDAL_LIBRARY_PATH

[windows]
printenv: print-justfile-dir
    @echo bla %GDAL_LIBRARY_PATH%

[windows]
[working-directory("backend")]
setup:
    @echo LIB=%LIB%
    uv add gdal==%GDAL_VERSION%
    uv venv --clear
    uv sync

[working-directory("backend")]
scrape:
    python manage.py run_scraper

[working-directory("backend")]
geocode:
    python manage.py geocode

[doc("Import a ready-to-use index that you can download from the Graphhopper page.")]
setup-geocoder:
    # wget https://download1.graphhopper.com/public/europe/switzerland-liechtenstein/photon-dump-switzerland-liechtenstein-1.0-latest.jsonl.zst
    podman compose run --entrypoint bash -v ./photon-dump-switzerland-liechtenstein-1.0-latest.jsonl.zst:/photon-dump.jsonl.zst photon -c /import-photon-dump.sh
    #podman compose run --entrypoint bash -v ./photon-dump-austria-1.0-latest.jsonl.zst:/photon-dump.jsonl.zst photon -c /import-photon-dump.sh

[doc("Import the Switzerland OSM PBF into Photon for geocoding. This is a one-time setup step. Use this only if you want to re-import the PBF into Photon, e.g. after an OSM update. If possible, use the exported index from the Graphhopper page.")]
photon-import-pbf:
    podman compose run --entrypoint bash -v ./data/switzerland-latest.osm.pbf:/switzerland-latest.osm.pbf photon -osm-pbf=switzerland-latest.osm.pbf -country-codes="CH" -languages=de,fr,it,en

prepare input="C:\\Users\\mboos\\Downloads\\gtfs_fp2026_20260408.zip":
    just filter-gtfs {{ input }}
    just filter-osm
    just build-motis

[working-directory("backend")]
export-openapi-schema:
    uv run python manage.py export_openapi_schema --api core.api.api --indent 4 --output openapi.json

# (Re)generate the API client for the frontend from the schema.

[linux]
[working-directory("packages/api")]
delete-api:
    rm -rf apis/
    rm -rf models/

[windows]
[working-directory("packages/api")]
delete-api:
    if (Test-Path "apis/") { Remove-Item "apis/" -Recurse -Force }
    if (Test-Path "models/") { Remove-Item "models/" -Recurse -Force }

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

[working-directory("frontend")]
update-api: export-openapi-schema update-api--build-only
    npm run lint
    npm run build

[env("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")]
[env("ANTHROPIC_DEFAULT_HAIKU_MODEL", "deepseek-v4-flash")]
[env("ANTHROPIC_DEFAULT_OPUS_MODEL", "deepseek-v4-pro[1m]")]
[env("ANTHROPIC_DEFAULT_SONNET_MODEL", "deepseek-v4-pro[1m]")]
[env("ANTHROPIC_MODEL", "deepseek-v4-pro[1m]")]
[env("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")]
[env("CLAUDE_CODE_EFFORT_LEVEL", "max")]
[env("CLAUDE_CODE_SUBAGENT_MODEL", "deepseek-v4-flash")]
claude-deepseek:
    claude --model opus --effort max

alias claude := claude-deepseek
