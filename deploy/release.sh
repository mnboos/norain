#!/bin/sh
set -eu

: "${BACKEND_IMAGE:?BACKEND_IMAGE must be set}"
: "${FRONTEND_IMAGE:?FRONTEND_IMAGE must be set}"
: "${GRAPHHOPPER_IMAGE:?GRAPHHOPPER_IMAGE must be set}"
: "${PHOTON_IMAGE:?PHOTON_IMAGE must be set}"

# COMPOSE_FILE in the production .env selects the compose file (docker-compose.prod.yml).
compose="docker compose --env-file .env"

export BACKEND_IMAGE FRONTEND_IMAGE GRAPHHOPPER_IMAGE PHOTON_IMAGE
$compose pull
# Never replace a working routing service with an image that cannot load its graph.
# Prepare, validate and activate a matching graph first; see build-routing-graph.md.
$compose run --rm --no-deps --entrypoint python graphhopper /graphhopper/artifact.py ready current
$compose run --rm backend python manage.py migrate --noinput
$compose run --rm --user root backend python manage.py collectstatic --noinput
# daphne runs as `app` and writes uploaded route photos here.
$compose run --rm --no-deps --user root --entrypoint chown backend app:app /app/backend/media
$compose up -d --remove-orphans
