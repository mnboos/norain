"""Public routes: the part of a saved route that may be shown to anyone.

A recurring route starts and ends where someone lives and works, and its schedule says when
they are on the road. So the public view is only the line *between* two privacy zones —
circles of ``privacy_zone_m`` around the start and the destination — and never the endpoints,
their names, the via points or the schedule. Everything public (the page, the list glyph, the
elevation profile, the forecast a visitor runs, a copy into the visitor's own routes) is built
from ``public_geometry``; nothing public reads ``polyline`` directly.

Circles, not a distance along the line: a round trip passes its start again at the end, and a
route that leaves home and doubles back would show the doorstep if only the first metres went.
Every vertex inside either circle in the first half of the ride trims the start up to it, and
every one in the second half trims the end back to it.
"""

import secrets

from .geo import haversine_m, vertex_distances
from .models import RecurringRoute
from .weather import SAMPLE_INTERVAL_DEFAULT_S, sample_line

PRIVACY_ZONE_CHOICES = (0, 250, 500, 1000, 2000)
# Less than this left between the zones is not worth publishing, and would put the visible
# stretch so close to both endpoints that it gives them away anyway.
MIN_PUBLIC_DISTANCE_M = 1000

_SLUG_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i: links get read aloud
SLUG_LENGTH = 10


def new_slug() -> str:
    return "".join(secrets.choice(_SLUG_ALPHABET) for _ in range(SLUG_LENGTH))


def _anchors(route: RecurringRoute) -> tuple[tuple[float, float], ...]:
    return ((route.start_lon, route.start_lat), (route.dest_lon, route.dest_lat))


def _inside(point, anchors, zone_m: float) -> bool:
    return any(haversine_m(point[0], point[1], lon, lat) < zone_m for lon, lat in anchors)


def visible_range(coords: list, anchors, zone_m: float) -> tuple[int, int] | None:
    """First and last vertex index shown publicly, or None when too little is left."""
    if len(coords) < 2:
        return None
    cumulative = vertex_distances(coords)
    half = cumulative[-1] / 2
    first, last = 0, len(coords) - 1
    if zone_m > 0:
        # The line's own ends count as anchors too: an imported track need not start
        # exactly on the stored start point.
        anchors = (*anchors, tuple(coords[0][:2]), tuple(coords[-1][:2]))
        for i, point in enumerate(coords):
            if cumulative[i] > half:
                break
            if _inside(point, anchors, zone_m):
                first = i + 1
        for i in range(len(coords) - 1, -1, -1):
            if cumulative[i] < half:
                break
            if _inside(coords[i], anchors, zone_m):
                last = i - 1
    if last <= first or cumulative[last] - cumulative[first] < MIN_PUBLIC_DISTANCE_M:
        return None
    return first, last


def public_geometry(route: RecurringRoute, zone_m: float | None = None) -> dict | None:
    """The route's geometry between its privacy zones, timed from the first visible vertex.

    Shaped like a job geometry (``core.tasks._job_geometry``) so a visitor's forecast runs on
    exactly this line. None while the route has no geometry, or when the zones leave too little.
    """
    coords = route.polyline_coordinates
    times = route.vertex_times
    if not coords or not times or len(times) != len(coords):
        return None
    span = visible_range(coords, _anchors(route), route.privacy_zone_m if zone_m is None else zone_m)
    if span is None:
        return None
    first, last = span
    line = coords[first : last + 1]
    start_s = times[first]
    cum_s = [t - start_s for t in times[first : last + 1]]
    elevations = (
        route.vertex_elevations if route.vertex_elevations and len(route.vertex_elevations) == len(coords) else None
    )
    return {
        "polyline": line,
        "vertex_times": cum_s,
        "vertex_elevations": elevations[first : last + 1] if elevations else None,
        "sample_points": sample_line(line, cum_s, SAMPLE_INTERVAL_DEFAULT_S),
        "total_seconds": int(cum_s[-1]),
        "total_distance_m": round(vertex_distances(line)[-1], 1),
    }


def in_privacy_zone(route: RecurringRoute, lon: float, lat: float) -> bool:
    """Whether a point (a photo's location) lies inside one of the route's privacy zones."""
    return route.privacy_zone_m > 0 and _inside((lon, lat), _anchors(route), route.privacy_zone_m)


def ascent_m(elevations: list | None) -> float | None:
    """Total climb, ignoring wobbles under 2 m that a DEM adds on flat ground."""
    heights = [h for h in elevations or [] if h is not None]
    if len(heights) < 2:
        return None
    total, base = 0.0, heights[0]
    for h in heights[1:]:
        if h - base >= 2:
            total += h - base
            base = h
        elif h < base:
            base = h
    return round(total)
