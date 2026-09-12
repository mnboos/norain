"""Local wind integration. Numerical pieces never become weather-fetch locations.

Project wind-from vectors into the rider's frame before taking absolute values, norms
or distance buckets. Display resolution is independent of the integration grid.
"""

import math
from bisect import bisect_right
from dataclasses import dataclass, field
from itertools import pairwise

from .geo import LENGTH_EPS, bearing_deg, interpolate_coord, vertex_distances

INTEGRATION_STEP_M = 25.0
SEGMENT_STEP_M = 100.0
MAX_SEGMENTS = 500
TIME_EPS = 1e-6
SPEED_EPS = 1e-6
CALM_SPEED = 0.1


def finite_number(value, *, nonnegative=False) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return None
    return float(value) if not nonnegative or value >= 0 else None


@dataclass(frozen=True)
class WindVector:
    east: float
    north: float


@dataclass(frozen=True)
class WeightedDirection:
    east: float
    north: float
    length_m: float


@dataclass(frozen=True)
class ResolvedTimes:
    values: list[float | None]
    source: str


@dataclass
class SampleWind:
    headwind: float | None = None
    cross_abs_mean: float | None = None
    coverage: float = 0.0
    support: list[WeightedDirection] = field(default_factory=list)


@dataclass
class WindProfile:
    samples: list[SampleWind]
    total_distance_m: float
    segments: list[dict] = field(default_factory=list)
    distribution: dict | None = None


def normalize_wind(speed, direction) -> WindVector | None:
    speed = finite_number(speed, nonnegative=True)
    direction = finite_number(direction)
    if speed == 0:
        return WindVector(0.0, 0.0)
    if speed is None or direction is None:
        return None
    angle = math.radians(direction % 360)
    return WindVector(speed * math.sin(angle), speed * math.cos(angle))


def project_wind(wind: WindVector, bearing: float) -> tuple[float, float]:
    angle = math.radians(bearing)
    east, north = math.sin(angle), math.cos(angle)
    return wind.east * east + wind.north * north, wind.east * north - wind.north * east


def wind_components(speed: float, direction: float, bearing: float) -> tuple[float, float]:
    """Compatibility projection: signed headwind, absolute crosswind."""
    head, cross = project_wind(normalize_wind(speed, direction), bearing)
    return head, abs(cross)


def project_support(wind: WindVector, directions: list[WeightedDirection]) -> tuple[float | None, float | None]:
    length = sum(d.length_m for d in directions)
    if length <= 0:
        return None, None
    return (
        sum((wind.east * d.east + wind.north * d.north) * d.length_m for d in directions) / length,
        sum(abs(wind.east * d.north - wind.north * d.east) * d.length_m for d in directions) / length,
    )


def valid_vertex_times(coords, times) -> bool:
    if not isinstance(times, list) or len(times) != len(coords) or not times:
        return False
    if any(finite_number(t, nonnegative=True) is None for t in times) or abs(times[0]) > TIME_EPS:
        return False
    distances = vertex_distances(coords)
    return all(
        b >= a and (distances[i + 1] - distances[i] <= LENGTH_EPS or b - a > TIME_EPS)
        for i, (a, b) in enumerate(pairwise(times))
    )


def anchor_indices(coords, sample_points) -> list[int | None]:
    """Keep identity/order; malformed indices create gaps, never coordinate-based joins."""
    result = []
    previous = -1
    for sp in sample_points:
        idx = sp.get("idx")
        if isinstance(idx, int) and not isinstance(idx, bool) and previous < idx < len(coords):
            result.append(idx)
            previous = idx
        else:
            result.append(None)
    return result


def resolve_vertex_times(coords, sample_points, vertex_times=None) -> ResolvedTimes:
    if valid_vertex_times(coords, vertex_times):
        return ResolvedTimes([float(t) for t in vertex_times], "routing")
    distances = vertex_distances(coords)
    values = [None] * len(coords)
    indices = anchor_indices(coords, sample_points)
    for n in range(len(indices) - 1):
        a, b = indices[n : n + 2]
        ta = finite_number(sample_points[n].get("elapsed_s"), nonnegative=True)
        tb = finite_number(sample_points[n + 1].get("elapsed_s"), nonnegative=True)
        if a is None or b is None or ta is None or tb is None or tb - ta <= TIME_EPS:
            continue
        length = distances[b] - distances[a]
        if length <= LENGTH_EPS:
            continue
        for i in range(a, b + 1):
            values[i] = ta + (tb - ta) * (distances[i] - distances[a]) / length
    usable = any(
        a is not None and b is not None and b - a > TIME_EPS and distances[i + 1] > distances[i]
        for i, (a, b) in enumerate(pairwise(values))
    )
    return ResolvedTimes(values, "sample-interpolation" if usable else "unavailable")


def ground_bucket(head: float, cross: float) -> str:
    if math.hypot(head, cross) < CALM_SPEED:
        return "calm_m"
    angle = abs(math.degrees(math.atan2(cross, head)))
    # Round only the classification angle to remove sin/cos noise at exact boundaries.
    angle = round(angle, 10)
    return "headwind_m" if angle < 45 else "tailwind_m" if angle > 135 else "crosswind_m"


def compute_wind_profile(
    coords,
    sample_points,
    aligned_wind,
    resolved_times,
    total_distance_m,
    *,
    include_segments=True,
    integration_step_m=INTEGRATION_STEP_M,
    max_segments=MAX_SEGMENTS,
) -> WindProfile:
    """Integrate once, with binary lookups for edge/anchor/support positions.

    Coverage is accumulated over local intervals; midpoint glyphs never supply totals.
    Lengths are scaled only for reporting, not for speed or integration.
    """
    distances = vertex_distances(coords)
    length = distances[-1] if distances else 0.0
    reported = finite_number(total_distance_m, nonnegative=True)
    total = reported if reported and reported > 0 else length
    result = WindProfile([SampleWind() for _ in sample_points], total)
    categories = dict.fromkeys(("headwind_m", "crosswind_m", "tailwind_m", "calm_m", "unknown_m"), 0.0)
    if include_segments:
        result.distribution = {
            **categories,
            "mean_felt_speed": None,
            "max_felt_speed": None,
            "felt_covered_m": 0.0,
            "timing_source": resolved_times.source,
        }
    if length <= LENGTH_EPS:
        if result.distribution is not None:
            result.distribution["unknown_m"] = total
        return result
    scale = total / length
    indices = anchor_indices(coords, sample_points)
    # Missing indices invalidate both neighbour intervals. Sorted valid positions are
    # used only to locate candidates; original-index adjacency is checked below.
    positions = [(distances[idx], n) for n, idx in enumerate(indices) if idx is not None]
    anchor_d = [p[0] for p in positions]
    edges = [i for i in range(len(coords) - 1) if distances[i + 1] - distances[i] > LENGTH_EPS]
    edge_starts = [distances[i] for i in edges]
    support_bounds = [0.0] + [(a + b) / 2 for a, b in pairwise(anchor_d)] + [length]

    def evaluate(distance):
        edge = edges[min(max(bisect_right(edge_starts, distance) - 1, 0), len(edges) - 1)]
        edge_len = distances[edge + 1] - distances[edge]
        fraction = min(1.0, max(0.0, (distance - distances[edge]) / edge_len))
        lon, lat = interpolate_coord(coords[edge], coords[edge + 1], fraction)
        bearing = bearing_deg(*coords[edge][:2], *coords[edge + 1][:2])
        a, b = resolved_times.values[edge : edge + 2]
        elapsed = a + (b - a) * fraction if a is not None and b is not None else None
        speed = edge_len * 3.6 / (b - a) if a is not None and b is not None and b - a > TIME_EPS else None
        wind = None
        bracket = min(bisect_right(anchor_d, distance) - 1, len(positions) - 2)
        if bracket >= 0:
            da, ia = positions[bracket]
            db, ib = positions[bracket + 1]
            wa, wb = aligned_wind[ia], aligned_wind[ib]
            if ib == ia + 1 and da <= distance <= db and db - da > LENGTH_EPS and wa is not None and wb is not None:
                ta = finite_number(sample_points[ia].get("elapsed_s"), nonnegative=True)
                tb = finite_number(sample_points[ib].get("elapsed_s"), nonnegative=True)
                f = (
                    (elapsed - ta) / (tb - ta)
                    if elapsed is not None and ta is not None and tb is not None and tb - ta > TIME_EPS
                    else (distance - da) / (db - da)
                )
                f = min(1.0, max(0.0, f))
                wind = WindVector(wa.east + (wb.east - wa.east) * f, wa.north + (wb.north - wa.north) * f)
        values = {
            "lat": lat,
            "lon": lon,
            "elapsed_s": elapsed,
            "bearing": bearing,
            "rider_speed": speed,
            "wind_speed": None,
            "wind_dir": None,
            "headwind": None,
            "crosswind": None,
            "felt_speed": None,
            "felt_angle": None,
        }
        if wind is not None:
            head, cross = project_wind(wind, bearing)
            w = math.hypot(wind.east, wind.north)
            values.update(
                wind_speed=w,
                wind_dir=math.degrees(math.atan2(wind.east, wind.north)) % 360 if w > SPEED_EPS else None,
                headwind=head,
                crosswind=cross,
            )
            if include_segments and speed is not None:
                felt = math.hypot(speed + head, cross)
                values.update(
                    felt_speed=felt,
                    felt_angle=math.degrees(math.atan2(cross, speed + head)) if felt > SPEED_EPS else None,
                )
        return values

    # Original edges + regular subdivisions + support boundaries. No display boundaries.
    boundaries = set(anchor_d + support_bounds)
    for edge in edges:
        start, end = distances[edge : edge + 2]
        count = max(1, math.ceil((end - start) / integration_step_m))
        boundaries.update(start + (end - start) * j / count for j in range(count + 1))
    boundaries = sorted(boundaries)
    sums = [[0.0, 0.0, 0.0] for _ in sample_points]
    covered_intervals = []
    felt_weighted = felt_length = 0.0
    max_felt = None
    for start, end in pairwise(boundaries):
        piece_length = end - start
        if piece_length <= 0:
            continue
        midpoint = (start + end) / 2
        values = evaluate(midpoint)
        head, cross, felt = values["headwind"], values["crosswind"], values["felt_speed"]
        categories["unknown_m" if head is None else ground_bucket(head, cross)] += piece_length * scale
        covered_intervals.append((start, end, head is not None, felt is not None))
        if head is not None and positions:
            support_i = min(bisect_right(support_bounds, midpoint) - 1, len(positions) - 1)
            n = positions[support_i][1]
            sums[n][0] += head * piece_length
            sums[n][1] += abs(cross) * piece_length
            sums[n][2] += piece_length
            rad = math.radians(values["bearing"])
            support = result.samples[n].support
            direction = WeightedDirection(math.sin(rad), math.cos(rad), piece_length)
            if (
                support
                and abs(support[-1].east - direction.east) < 1e-12
                and abs(support[-1].north - direction.north) < 1e-12
            ):
                old = support[-1]
                support[-1] = WeightedDirection(old.east, old.north, old.length_m + piece_length)
            else:
                support.append(direction)
        if felt is not None:
            felt_length += piece_length
            felt_weighted += felt * piece_length
            max_felt = felt if max_felt is None else max(max_felt, felt)

    for p, (distance, n) in enumerate(positions):
        sample = result.samples[n]
        h, c, covered = sums[n]
        if covered > 0 and aligned_wind[n] is not None:
            sample.headwind, sample.cross_abs_mean = h / covered, c / covered
            support_length = support_bounds[p + 1] - support_bounds[p]
            sample.coverage = min(1.0, covered / support_length) if support_length > 0 else 0.0
        elif aligned_wind[n] is not None:
            bearing = evaluate(distance)["bearing"]
            h, c = project_wind(aligned_wind[n], bearing)
            sample.headwind, sample.cross_abs_mean = h, abs(c)
            rad = math.radians(bearing)
            sample.support = [WeightedDirection(math.sin(rad), math.cos(rad), 1.0)]

    if not include_segments:
        return result
    result.distribution.update(
        categories,
        mean_felt_speed=felt_weighted / felt_length if felt_length else None,
        max_felt_speed=max_felt,
        felt_covered_m=felt_length * scale,
    )
    count = min(max_segments, math.ceil(total / SEGMENT_STEP_M))
    step = max(SEGMENT_STEP_M, total / max_segments)
    cursor = 0
    for j in range(count):
        start, end = min(j * step, total), min((j + 1) * step, total)
        a, b = start / scale, end / scale
        values = evaluate((a + b) / 2)
        ground_length = apparent_length = 0.0
        while cursor < len(covered_intervals) and covered_intervals[cursor][1] <= a:
            cursor += 1
        k = cursor
        while k < len(covered_intervals) and covered_intervals[k][0] < b:
            lo, hi, ground, apparent = covered_intervals[k]
            overlap = max(0.0, min(hi, b) - max(lo, a))
            ground_length += overlap if ground else 0
            apparent_length += overlap if apparent else 0
            k += 1
        result.segments.append(
            dict(
                start_m=start,
                end_m=end,
                **values,
                wind_coverage=min(1.0, ground_length / (b - a)),
                felt_coverage=min(1.0, apparent_length / (b - a)),
            )
        )
    return result
