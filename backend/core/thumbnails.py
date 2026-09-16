"""Build the tiny route-shape + ride-quality blob shown next to each row in the route list.

The list is polled every 60 s and renders every route at once, so this must be cheap and
must never spend an API request: everything is read from already-warm grid cells, and a
sample point whose cell is cold stays ``None`` rather than being guessed at. The frontend
paints those spans neutral grey.

Scoring deliberately does *not* happen here. The blob stores the raw numbers
``core.ride_quality`` reads, and the route list scores them when it is served - so a change
to the scoring config shows at once instead of after every thumbnail has been rebuilt.
"""

from datetime import datetime

from asgiref.sync import sync_to_async
from loguru import logger
from shapely.geometry import LineString

from .entitlements import entitlements_for_sync
from .geo import METRES_PER_DEGREE, simplify_line
from .models import RecurringRoute
from .schedule import next_departure
from .weather import compute_route_weather

# A ~40 px glyph cannot show more than this, and the blob travels with every list response.
MAX_THUMBNAIL_VERTICES = 64

# The fields core.ride_quality.ride_score() reads, and nothing else.
_SAMPLE_FIELDS = (
    "rain_mm", "precipitation_interval_s", "rain_rate_mm_h", "pop", "rain_if_wet", "temp", "headwind",
    "wind_power_w", "weather_code",
)


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

    if len(polyline) > MAX_THUMBNAIL_VERTICES:
        # Raise the tolerance until the simplified line fits. Starting from the bounding
        # box keeps this to a handful of iterations at any route length.
        line = LineString(polyline)
        minx, miny, maxx, maxy = line.bounds
        tolerance_m = max(maxx - minx, maxy - miny) / 200 * METRES_PER_DEGREE or 0.1
        keep: set[int] = set()
        for _ in range(24):
            keep = set(simplify_line(polyline, must_keep, tolerance_m))
            if len(keep) <= MAX_THUMBNAIL_VERTICES:
                break
            tolerance_m *= 2
    else:
        keep = set(range(len(polyline)))

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

    # The owner's tier decides whether the map shows station-corrected temperatures, so the
    # list reads the same way or the two would disagree about one ride. Readings are only
    # read here, never fetched.
    limits = await sync_to_async(lambda: entitlements_for_sync(route.owner if route.owner_id else None))()

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
        # The rain score combines the ensemble's chance and amount (`pop`, `rain_if_wet`), so
        # the list reads warm ensemble cells too - cache-only, like the forecast cells - or it
        # would score the main run alone and disagree with the map.
        include_uncertainty=True,
        vertex_times=route.vertex_times,
        include_segments=False,
        station_correction_enabled=limits.station_correction,
    )

    # compute_route_weather drops sample points whose cell was cold, so align what came
    # back onto the route's own sample points by elapsed time; the gaps become None.
    by_index = {s.sample_index: s for s in forecast.samples}
    samples: list[dict | None] = []
    for index, sp in enumerate(route.sample_points):
        vertex = index_map.get(int(sp.get("idx", -1)))
        sample = by_index.get(index)
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
