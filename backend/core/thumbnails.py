"""Build the tiny route-shape + ride-quality blob shown next to each row in the route list.

The list is polled every 60 s and renders every route at once, so this must be cheap and
must never spend an API request: everything is read from already-warm grid cells, and a
sample point whose cell is cold stays ``None`` rather than being guessed at. The frontend
paints those spans neutral grey.

Scoring deliberately does *not* happen here. ``frontend/src/utils/rideQuality.ts`` owns the
rain/wind/temperature curves and the colour ramp, and the full route map already uses them;
a second implementation in Python would drift and the thumbnail would eventually disagree
with the map about the same route. So this module ships the raw numbers the scorer reads.
"""

from datetime import datetime

from loguru import logger
from shapely.geometry import LineString

from .models import RecurringRoute
from .schedule import next_departure
from .weather import compute_route_weather

# A ~40 px glyph cannot show more than this, and the blob travels with every list response.
MAX_THUMBNAIL_VERTICES = 64

# The fields rideScore() reads, and nothing else.
_SAMPLE_FIELDS = ("rain_mm", "precipitation_interval_s", "rain_rate_mm_h", "temp", "headwind")


def simplify_path(
    polyline: list[list[float]],
    sample_points: list[dict],
) -> tuple[list[list[float]], dict[int, int]]:
    """Reduce a full GraphHopper polyline to something a 40 px box can draw.

    Returns the reduced ``[[lon, lat], ...]`` plus a map from original vertex index to
    index in the reduced path.

    Every sample point's vertex is kept regardless of the tolerance, so each weather sample
    still lands on a real vertex of the drawn path. Douglas-Peucker (via shapely, already a
    dependency) keeps the corners that give a route its recognisable shape, which striding
    every Nth vertex does not.
    """
    if not polyline:
        return [], {}

    must_keep = {int(sp["idx"]) for sp in sample_points if 0 <= int(sp.get("idx", -1)) < len(polyline)}
    must_keep.update({0, len(polyline) - 1})

    keep = set(must_keep)
    if len(polyline) > MAX_THUMBNAIL_VERTICES:
        # Raise the tolerance until the simplified line fits. Starting from the bounding
        # box keeps this to a handful of iterations at any route length.
        line = LineString(polyline)
        minx, miny, maxx, maxy = line.bounds
        tolerance = max(maxx - minx, maxy - miny) / 200 or 1e-6
        simplified: list[tuple[float, float]] = []
        for _ in range(24):
            simplified = list(LineString(polyline).simplify(tolerance).coords)
            if len(simplified) + len(must_keep) <= MAX_THUMBNAIL_VERTICES:
                break
            tolerance *= 2
        # simplify() returns coordinates, not indices; map them back by exact position.
        by_coord = {(round(p[0], 7), round(p[1], 7)): i for i, p in enumerate(polyline)}
        for x, y in simplified:
            i = by_coord.get((round(x, 7), round(y, 7)))
            if i is not None:
                keep.add(i)
    else:
        keep.update(range(len(polyline)))

    kept = sorted(keep)
    path = [[polyline[i][0], polyline[i][1]] for i in kept]
    return path, {original: new for new, original in enumerate(kept)}


async def compute_route_thumbnail(route: RecurringRoute) -> dict | None:
    """Assemble the thumbnail blob for a route's next departure, from warm cells only.

    Returns ``None`` when the route has no geometry yet or has no next departure — the
    caller stores that as-is and the UI shows a placeholder.
    """
    coordinates = route.polyline_coordinates
    if not route.sample_points or not coordinates:
        return None

    departure = next_departure(route.schedule_cron)
    if departure is None:
        return None

    path, index_map = simplify_path(coordinates, route.sample_points)
    if len(path) < 2:
        return None

    forecast = await compute_route_weather(
        start_lat=route.start_lat,
        start_lon=route.start_lon,
        dest_lat=route.dest_lat,
        dest_lon=route.dest_lon,
        profile=route.profile,
        departure_time=departure.isoformat(),
        sample_points=route.sample_points,
        polyline=coordinates,
        total_seconds=route.total_seconds,
        total_distance_m=route.total_distance_m,
        cache_only=True,
        include_uncertainty=False,
    )

    # compute_route_weather drops sample points whose cell was cold, so align what came
    # back onto the route's own sample points by elapsed time; the gaps become None.
    by_elapsed = {s.elapsed_s: s for s in forecast.samples}
    samples: list[dict | None] = []
    for sp in route.sample_points:
        vertex = index_map.get(int(sp.get("idx", -1)))
        sample = by_elapsed.get(int(sp["elapsed_s"]))
        if vertex is None or sample is None:
            samples.append(None)
            continue
        entry = {"i": vertex}
        entry.update({field: getattr(sample, field) for field in _SAMPLE_FIELDS})
        samples.append(entry)

    known = sum(1 for s in samples if s is not None)
    logger.debug(
        "compute_route_thumbnail: {} — {}/{} samples from warm cells, {} vertices",
        route.name,
        known,
        len(samples),
        len(path),
    )
    return {
        "departure": departure.isoformat(),
        "path": path,
        "samples": samples,
        "computed_at": datetime.now().astimezone().isoformat(),
    }
