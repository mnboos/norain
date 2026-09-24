"""Measured journey paths. Events identify visits, not just geographic coordinates."""

from bisect import bisect_right
from dataclasses import dataclass
from itertools import pairwise

from shapely.geometry import LineString, Point

from .geo import haversine_m, vertex_distances
from .journeys import LAST_DAY_SLACK
from .weather import COORD_ROUND, SAMPLE_INTERVAL_DEFAULT_S, _sample_indices


@dataclass(frozen=True)
class Limits:
    seconds: float | None = None
    meters: float | None = None

    def excess(self, seconds, meters):
        return {
            "over_s": max(0, seconds - self.seconds) if self.seconds else 0,
            "over_m": max(0, meters - self.meters) if self.meters else 0,
        }

    def allows(self, seconds, meters):
        return not any(self.excess(seconds, meters).values())

    def scaled(self, factor):
        return Limits(self.seconds * factor if self.seconds else None, self.meters * factor if self.meters else None)


class LineMeasure:
    def __init__(self, geometry):
        self.seconds = geometry["vertex_times"]
        self.meters = vertex_distances(geometry["polyline"])

    def between(self, a, b):
        return self.seconds[b] - self.seconds[a], self.meters[b] - self.meters[a]

    def boundary(self, start, limits):
        by_time = (
            bisect_right(self.seconds, self.seconds[start] + limits.seconds) - 1
            if limits.seconds
            else len(self.seconds) - 1
        )
        by_distance = (
            bisect_right(self.meters, self.meters[start] + limits.meters) - 1 if limits.meters else len(self.meters) - 1
        )
        return min(by_time, by_distance)

    def index(self, meters):
        return max(0, min(len(self.meters) - 1, bisect_right(self.meters, meters + 1e-6) - 1))


def project_hits(coords, hits):
    """Convert planar segment projections to our cumulative geodesic metres.

    PostGIS's global planar fraction times geodesic length does not locate vertices
    consistently when a route changes direction. Preserve the actual segment instead.
    The caller restricts coords to the current search window to distinguish return visits.
    """
    line = LineString([p[:2] for p in coords])
    planar = [0.0]
    for a, b in pairwise(coords):
        planar.append(planar[-1] + ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5)
    meters = vertex_distances(coords)
    for hit in hits:
        distance = line.project(Point(hit.lon, hit.lat))
        i = min(len(coords) - 2, max(0, bisect_right(planar, distance) - 1))
        length = planar[i + 1] - planar[i]
        fraction = (distance - planar[i]) / length if length else 0
        yield hit, meters[i] + fraction * (meters[i + 1] - meters[i])


def measured_geometry(coords, times, elevations=None):
    samples = []
    for index in _sample_indices(times, SAMPLE_INTERVAL_DEFAULT_S):
        lon, lat = coords[index][:2]
        samples.append(
            {
                "lat": lat,
                "lon": lon,
                "lat_r": round(lat, COORD_ROUND),
                "lon_r": round(lon, COORD_ROUND),
                "elapsed_s": int(times[index]),
                "idx": index,
            }
        )
    return {
        "polyline": coords,
        "vertex_times": times,
        "vertex_elevations": elevations,
        "total_seconds": int(times[-1]),
        "total_distance_m": vertex_distances(coords)[-1],
        "sample_points": samples,
    }


def slice_geometry(geometry, start, end):
    times = geometry["vertex_times"]
    elevations = geometry.get("vertex_elevations")
    return measured_geometry(
        geometry["polyline"][start : end + 1],
        [t - times[start] for t in times[start : end + 1]],
        elevations[start : end + 1] if elevations else None,
    )


def join_geometries(parts):
    """Join only connected paths; never invent an untimed connector across a snapping gap."""
    coords, times, heights, boundaries = [], [], [], [0]
    for part in parts:
        line = part["polyline"]
        if not line:
            raise ValueError("Empty routing geometry")
        skip = 0
        if coords:
            if haversine_m(*coords[-1][:2], *line[0][:2]) > 1:
                raise ValueError("Routing segments do not meet")
            skip = 1
        offset = times[-1] if times else 0
        coords.extend(line[skip:])
        times.extend(offset + t for t in part["vertex_times"][skip:])
        heights.extend((part.get("vertex_elevations") or [None] * len(line))[skip:])
        boundaries.append(len(coords) - 1)
    return measured_geometry(coords, times, heights if all(h is not None for h in heights) else None), boundaries


def visited_gaps(geometry, events, wanted):
    measure = LineMeasure(geometry)
    result = {}
    for category in wanted:
        marks = sorted(
            {
                0,
                len(measure.meters) - 1,
                *(e["index"] for e in events if any(p["category"] == category for p in e.get("pois", []))),
            }
        )
        result[category] = [(a, b, *measure.between(a, b)) for a, b in pairwise(marks)]
    return result


def longest_visited_gaps(geometry, events, wanted):
    return {
        category: {
            "s": round(max((s for _, _, s, _ in gaps), default=0)),
            "m": round(max((m for _, _, _, m in gaps), default=0), 1),
        }
        for category, gaps in visited_gaps(geometry, events, wanted).items()
    }


def repair_breaks(geometry, events, limits):
    """Boundary pauses are final-line events; physical POI breaks are immutable."""
    measure = LineMeasure(geometry)
    end = len(measure.meters) - 1
    stops = sorted((e for e in events if e.get("break")), key=lambda e: e["index"])
    result, start = [], 0
    for stop in [*stops, {"index": end, "pois": []}]:
        target = stop["index"]
        while not limits.allows(*measure.between(start, target)):
            boundary = measure.boundary(start, limits)
            if boundary <= start:
                # The next graph edge itself exceeds the limit. Keep and report it.
                boundary = start + 1
            if boundary >= target:
                break
            result.append({"index": boundary, "pois": []})
            start = boundary
        if target < end and target > start:
            result.append(stop)
        start = target
    return [
        {
            "index": e["index"],
            "along_m": round(measure.meters[e["index"]], 1),
            "elapsed_s": int(measure.seconds[e["index"]]),
            "lon": geometry["polyline"][e["index"]][0],
            "lat": geometry["polyline"][e["index"]][1],
            "pois": e.get("pois", []),
        }
        for e in result
    ]


def check_limits(geometry, breaks, day_limits, leg_limits, *, last_day=False):
    measure = LineMeasure(geometry)
    day = (day_limits.scaled(1 + LAST_DAY_SLACK) if last_day else day_limits).excess(*measure.between(0, -1))
    result = {"legs": []}
    if any(day.values()):
        result["day"] = day
    marks = [0, *(b["index"] for b in breaks), len(measure.meters) - 1]
    for number, (a, b) in enumerate(pairwise(marks), 1):
        over = leg_limits.excess(*measure.between(a, b))
        if any(over.values()):
            result["legs"].append({"leg": number, **over})
    return result
