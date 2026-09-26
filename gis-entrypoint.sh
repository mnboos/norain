#!/bin/bash

set -e

# --- Configuration ---
# PGHOST line is now REMOVED.
PBF_FILE="/data/import/switzerland-latest.osm.pbf"
METADATA_TABLE="import_metadata"
OSM_TABLES=(
    "planet_osm_point"
    "planet_osm_line"
    "planet_osm_polygon"
    "planet_osm_roads"
)

# --- Helper Functions ---
# (No changes to helper functions)
get_current_checksum() {
    md5sum "$PBF_FILE" | awk '{ print $1 }'
}

get_stored_checksum() {
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -c \
        "SELECT md5_hash FROM $METADATA_TABLE WHERE filename = '$(basename "$PBF_FILE")' LIMIT 1;"
}

update_stored_checksum() {
    local new_md5="$1"
    echo "GIS ENTRYPOINT: Storing new checksum in database: $new_md5"
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<-EOSQL
        CREATE TABLE IF NOT EXISTS $METADATA_TABLE (
            filename TEXT PRIMARY KEY,
            md5_hash TEXT NOT NULL,
            last_imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        INSERT INTO $METADATA_TABLE (filename, md5_hash, last_imported_at)
        VALUES ('$(basename "$PBF_FILE")', '$new_md5', NOW())
        ON CONFLICT (filename) DO UPDATE
        SET md5_hash = EXCLUDED.md5_hash,
            last_imported_at = EXCLUDED.last_imported_at;
EOSQL
}

drop_osm_tables() {
    echo "GIS ENTRYPOINT: Dropping existing OSM tables..."
    for table in "${OSM_TABLES[@]}"; do
        psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
             -c "DROP TABLE IF EXISTS ${table} CASCADE;"
    done
}

# --- Main Script Logic ---
# (No changes to the main logic)
if [ ! -f "$PBF_FILE" ]; then
    echo "GIS ENTRYPOINT: ERROR - PBF file not found at $PBF_FILE. Cannot proceed."
    exit 1
fi
echo "GIS ENTRYPOINT: Checking data freshness..."
current_md5=$(get_current_checksum)
stored_md5=$(get_stored_checksum || true)
echo "GIS ENTRYPOINT: Current PBF file checksum is $current_md5"
echo "GIS ENTRYPOINT: Stored checksum in database is $stored_md5"
if [ "$current_md5" == "$stored_md5" ]; then
    echo "GIS ENTRYPOINT: Checksums match. Data is up-to-date. No import needed."
    exit 0
else
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
             -c "create extension hstore;"


    echo "GIS ENTRYPOINT: Checksum mismatch or no previous import. Data needs to be imported."
    drop_osm_tables
    echo "GIS ENTRYPOINT: Importing data from $PBF_FILE..."
    osm2pgsql --create --slim --drop --database $POSTGRES_DB --user $POSTGRES_USER --cache 12000 --hstore $PBF_FILE
    echo "GIS ENTRYPOINT: Import complete. Updating metadata..."
    update_stored_checksum "$current_md5"
    echo "GIS ENTRYPOINT: Process finished successfully."
fi
exit 0