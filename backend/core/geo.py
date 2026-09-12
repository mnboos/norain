"""Small spherical geometry helpers; no Django or API dependencies."""

import math
from itertools import pairwise

from shapely.geometry import LineString

LENGTH_EPS = 1e-6


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000.0 * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    return (
        math.degrees(
            math.atan2(
                math.sin(dl) * math.cos(p2), math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
            )
        )
        % 360
    )


def vertex_distances(coords) -> list[float]:
    if not coords:
        return []
    result = [0.0]
    for a, b in pairwise(coords):
        result.append(result[-1] + haversine_m(*a[:2], *b[:2]))
    return result


def interpolate_coord(a, b, fraction: float) -> tuple[float, float]:
    delta = (b[0] - a[0] + 180) % 360 - 180
    return (a[0] + delta * fraction + 180) % 360 - 180, a[1] + (b[1] - a[1]) * fraction


METRES_PER_DEGREE = 111_320.0


def simplify_line(polyline, keep, tolerance_m: float) -> list[int]:
    """Douglas-Peucker on a ``[[lon, lat], ...]`` line; returns the kept vertex indices.

    The tolerance is in metres. Longitudes are scaled by the cosine of the mean latitude
    first, so it means the same distance east-west as north-south. The indices in ``keep``
    and both ends always survive, whatever the tolerance.
    """
    if len(polyline) < 3:
        return list(range(len(polyline)))

    scale = math.cos(math.radians(sum(p[1] for p in polyline) / len(polyline)))
    scaled = [(p[0] * scale, p[1]) for p in polyline]
    simplified = LineString(scaled).simplify(tolerance_m / METRES_PER_DEGREE).coords

    kept = {0, len(polyline) - 1}
    kept.update(i for i in keep if 0 <= i < len(polyline))
    # simplify() returns coordinates, not indices. Its output is an ordered subset of the
    # input, so walk both in step: a route that passes the same point twice then maps each
    # pass to its own vertex instead of both to one.
    pointer = 0
    for x, y in simplified:
        while pointer < len(scaled) and scaled[pointer] != (x, y):
            pointer += 1
        if pointer < len(scaled):
            kept.add(pointer)
    return sorted(kept)
