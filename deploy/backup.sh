#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
set -a
. ./.env
set +a

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY must be set}"
: "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE must be set}"

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
docker compose --env-file .env -f docker-compose.prod.yml exec -T db \
    pg_dump --username="$DB_USER" --format=custom "$DB_NAME" |
    restic backup --stdin --stdin-filename "norain-postgres-${timestamp}.dump"
# Route photos exist nowhere else. restic stores only what changed since the last run.
restic backup --tag photos "${APP_STORAGE_PATH:?APP_STORAGE_PATH must be set}/django/media"
restic forget --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune
