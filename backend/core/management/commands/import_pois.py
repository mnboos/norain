"""Replace the POI table with the features of a GeoJSON-sequence file.

Usage: python manage.py import_pois /osm_data/pois-switzerland-latest.geojsonseq

The file comes from docker/osm-extract-pois.sh (`just poi-extract`). The whole replacement
runs in one transaction, so a failed import leaves the old POIs in place, and readers keep
seeing them until the new set commits.
"""

import json
from collections.abc import Iterator
from pathlib import Path

from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from shapely.geometry import shape

from core.models import Poi
from core.pois import categories, kept_tags

BATCH_SIZE = 5000


def read_pois(path: Path) -> Iterator[Poi]:
    """One Poi per feature and category, areas reduced to a point on their surface."""
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
            for category in wanted:
                yield Poi(
                    osm_ref=ref,
                    category=category,
                    name=tags.get("name", "")[:300],
                    tags=kept_tags(tags),
                    location=Point(point.x, point.y, srid=4326),
                )


def import_pois(path: Path) -> int:
    count = 0
    with transaction.atomic():
        Poi.objects.all().delete()
        batch: list[Poi] = []
        for poi in read_pois(path):
            batch.append(poi)
            if len(batch) >= BATCH_SIZE:
                Poi.objects.bulk_create(batch)
                count += len(batch)
                batch = []
        if batch:
            Poi.objects.bulk_create(batch)
            count += len(batch)
    return count


class Command(BaseCommand):
    help = "Replace the POI table with a GeoJSON-sequence file from osm-extract-pois.sh"

    def add_arguments(self, parser):
        parser.add_argument("file", help="pois-<extract>.geojsonseq")

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.is_file():
            raise CommandError(f"Not a file: {path}")
        count = import_pois(path)
        self.stdout.write(self.style.SUCCESS(f"Imported {count} POIs from {path}"))
