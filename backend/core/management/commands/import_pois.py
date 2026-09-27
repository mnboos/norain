"""Replace the POI table with the features of a GeoJSON-sequence file.

Usage: python manage.py import_pois /osm_data/pois-switzerland-latest.geojsonseq

The file comes from docker/osm-extract-pois.sh (`just poi-extract-from-unfiltered-osm-pbf`). The whole replacement
runs in one transaction, so a failed import leaves the old POIs in place.

It is a bulk load: the secondary indexes are dropped, the table truncated, the rows copied in with
COPY and the indexes built once at the end, which is far faster than maintaining them row by row
for millions of POIs. The price is an exclusive lock on the table until the import commits:
anything reading POIs (journey planning, the POI endpoints) waits for it instead of seeing the
old set.
"""

import json
from collections.abc import Callable, Iterator
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from shapely.geometry import shape

from core.models import Poi
from core.pois import categories, kept_tags

PROGRESS_EVERY = 100_000

COLUMNS = ("osm_ref", "category", "name", "tags", "location")


def read_pois(path: Path) -> Iterator[tuple[str, str, str, str, str]]:
    """One row (in ``COLUMNS`` order) per feature and category, areas reduced to a point on their surface."""
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip().lstrip("\x1e")
            if not line:
                continue
            feature = json.loads(line)
            tags = {k: str(v) for k, v in (feature.get("properties") or {}).items()}
            wanted = categories(tags)
            ref = str(feature.get("id") or "")
            if not wanted or not ref or ref in seen or not feature.get("geometry"):
                continue
            geometry = shape(feature["geometry"])
            if geometry.is_empty:
                continue
            # A point on the surface, not the centroid: an L-shaped campsite's centroid can lie
            # outside it, on the other side of a river.
            point = geometry if geometry.geom_type == "Point" else geometry.representative_point()
            seen.add(ref)
            name = tags.get("name", "")[:300]
            kept = json.dumps(kept_tags(tags))
            location = f"SRID=4326;POINT({point.x!r} {point.y!r})"
            for category in wanted:
                yield ref, category, name, kept, location


def _secondary_indexes(cursor, table: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """The table's unique/exclusion constraints and its plain indexes, as (name, definition).

    The primary key stays. Recreating from the catalog's own definitions keeps the names
    Django's migrations know, so no index name is hardcoded here.
    """
    cursor.execute(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = %s::regclass AND contype IN ('u', 'x')",
        [table],
    )
    constraints = cursor.fetchall()
    cursor.execute(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = current_schema() AND tablename = %s "
        "AND indexname NOT IN (SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass)",
        [table, table],
    )
    return constraints, cursor.fetchall()


def import_pois(path: Path, log: Callable[[str], None] = lambda _: None) -> int:
    table = Poi._meta.db_table
    quoted = connection.ops.quote_name(table)
    count = 0
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL maintenance_work_mem = '1GB'")
        constraints, indexes = _secondary_indexes(cursor, table)
        for name, _ in constraints:
            cursor.execute(f"ALTER TABLE {quoted} DROP CONSTRAINT {connection.ops.quote_name(name)}")
        for name, _ in indexes:
            cursor.execute(f"DROP INDEX {connection.ops.quote_name(name)}")
        cursor.execute(f"TRUNCATE {quoted} RESTART IDENTITY")

        log("Loading POIs…")
        with cursor.copy(f"COPY {quoted} ({', '.join(COLUMNS)}) FROM STDIN") as copy:
            for row in read_pois(path):
                copy.write_row(row)
                count += 1
                if count % PROGRESS_EVERY == 0:
                    log(f"  {count} rows")

        log(f"Building indexes over {count} rows…")
        for name, definition in constraints:
            cursor.execute(f"ALTER TABLE {quoted} ADD CONSTRAINT {connection.ops.quote_name(name)} {definition}")
        for _, definition in indexes:
            cursor.execute(definition)
        cursor.execute(f"ANALYZE {quoted}")
    return count


class Command(BaseCommand):
    help = "Replace the POI table with a GeoJSON-sequence file from osm-extract-pois.sh"

    def add_arguments(self, parser):
        parser.add_argument("file", help="pois-<extract>.geojsonseq")

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.is_file():
            raise CommandError(f"Not a file: {path}")
        count = import_pois(path, log=self.stdout.write)
        self.stdout.write(self.style.SUCCESS(f"Imported {count} POIs from {path}"))
