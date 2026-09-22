"""Points of interest along a route: water, toilets, shelters, lodging and the rest.

The routing graph never sees these. ``docker/graphhopper-filter-osm.sh`` keeps only the ways
the bike profiles use, and a toilet or a water tap is a standalone node (or a small area), so
POIs are extracted from the *raw* extract by ``docker/osm-extract-pois.sh`` and loaded into
``core.models.Poi`` by ``manage.py import_pois``. The journey planner then asks PostGIS which
of them lie near a candidate route (``pois_along``).

``POI_RULES`` is the one tag -> category map. The shell script filters with the same tags;
a test checks that every tag here appears there, so a category added here cannot silently
come back empty.
"""

import json
from dataclasses import dataclass, field

from django.db import connection

# A rule is (key, values, extra conditions). An object belongs to the first category whose
# rule matches: the key has one of the values, and every extra tag has one of its values.
Rule = tuple[str, tuple[str, ...], dict[str, tuple[str, ...]]]

POI_RULES: dict[str, tuple[Rule, ...]] = {
    "toilets": (("amenity", ("toilets",), {}),),
    "bbq": (("amenity", ("bbq",), {}),),
    "drinking_water": (
        ("amenity", ("drinking_water",), {}),
        ("man_made", ("water_tap",), {"drinking_water": ("yes",)}),
        ("natural", ("spring",), {"drinking_water": ("yes",)}),
        # A fountain is only a water source when it says so.
        ("amenity", ("fountain", "water_point"), {"drinking_water": ("yes",)}),
    ),
    "vending_machine": (("amenity", ("vending_machine",), {}),),
    "shelter": (("amenity", ("shelter",), {}),),
    "bike_repair": (
        ("amenity", ("bicycle_repair_station",), {}),
        ("shop", ("bicycle",), {}),
    ),
    "food": (("amenity", ("cafe", "restaurant", "fast_food", "biergarten"), {}),),
    "groceries": (("shop", ("supermarket", "convenience", "bakery"), {}),),
    "ebike_charging": (("amenity", ("charging_station",), {"bicycle": ("yes", "designated")}),),
    "train_station": (("railway", ("station", "halt"), {}),),
    "lodging": (("tourism", ("hotel", "hostel", "guest_house", "camp_site", "alpine_hut", "wilderness_hut"), {}),),
}

POI_CATEGORIES: tuple[str, ...] = tuple(POI_RULES)

# Where a day may end. A subset of the lodging tag values, chosen per journey.
LODGING_KINDS: tuple[str, ...] = POI_RULES["lodging"][0][1]

# Tags kept on the row. Everything else in OSM is noise for the UI and costs space.
KEPT_TAGS = (
    "opening_hours",
    "fee",
    "access",
    "shelter_type",
    "vending",
    "capacity",
    "website",
    "amenity",
    "shop",
    "tourism",
    "railway",
    "man_made",
    "natural",
)

# access=* values that make a POI useless to a passing rider.
CLOSED_ACCESS = frozenset({"private", "no"})


def classify(tags: dict[str, str]) -> str | None:
    """The category of an OSM object, or None when it is not a POI we keep."""
    if tags.get("access") in CLOSED_ACCESS:
        return None
    for category, rules in POI_RULES.items():
        for key, values, extra in rules:
            if tags.get(key) in values and all(tags.get(k) in v for k, v in extra.items()):
                return category
    return None


def filter_tags() -> set[tuple[str, str]]:
    """Every (key, value) the extraction must keep. The test compares it with the script."""
    return {(key, value) for rules in POI_RULES.values() for key, values, _ in rules for value in values}


def kept_tags(tags: dict[str, str]) -> dict[str, str]:
    return {k: tags[k] for k in KEPT_TAGS if k in tags}


@dataclass(frozen=True)
class PoiHit:
    """One POI near a route: where it is, and where along the route it can be reached."""

    osm_ref: str
    category: str
    name: str
    lon: float
    lat: float
    along_m: float  # distance from the route start to the nearest point on the route
    offset_m: float  # straight-line distance from the route; about half the detour
    tags: dict = field(default_factory=dict)

    def as_json(self) -> dict:
        """The denormalised shape stored on journey stages: no FK, so a re-import never
        touches a stored journey."""
        return {
            "osm_ref": self.osm_ref,
            "category": self.category,
            "name": self.name,
            "lon": self.lon,
            "lat": self.lat,
            "along_m": round(self.along_m, 1),
            "offset_m": round(self.offset_m, 1),
        }


def _line_wkt(coordinates: list[list[float]]) -> str:
    return "SRID=4326;LINESTRING(" + ",".join(f"{lon} {lat}" for lon, lat, *_ in coordinates) + ")"


def pois_along_sync(
    coordinates: list[list[float]], categories: list[str] | tuple[str, ...], corridor_m: float
) -> list[PoiHit]:
    """POIs of the given categories within ``corridor_m`` of the line, in riding order.

    ``along_m`` comes from ``ST_LineLocatePoint`` on the planar line scaled by the geodesic
    length. That is exact enough for placing breaks a few km apart and keeps it one query.
    """
    if len(coordinates) < 2 or not categories:
        return []
    sql = """
        WITH route AS (SELECT ST_GeomFromEWKT(%s) AS geom)
        SELECT p.osm_ref, p.category, p.name, ST_X(p.location::geometry), ST_Y(p.location::geometry),
               ST_LineLocatePoint(route.geom, p.location::geometry) * ST_Length(route.geom::geography),
               ST_Distance(p.location, route.geom::geography),
               p.tags
        FROM core_poi p, route
        WHERE p.category = ANY(%s) AND ST_DWithin(p.location, route.geom::geography, %s)
        ORDER BY 6
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, [_line_wkt(coordinates), list(categories), corridor_m])
        rows = cursor.fetchall()
    return [
        PoiHit(
            osm_ref=ref,
            category=category,
            name=name,
            lon=lon,
            lat=lat,
            along_m=float(along),
            offset_m=float(offset),
            # Django registers a text loader for jsonb, so a raw cursor returns it unparsed.
            tags=json.loads(tags) if isinstance(tags, str) else tags or {},
        )
        for ref, category, name, lon, lat, along, offset, tags in rows
    ]
