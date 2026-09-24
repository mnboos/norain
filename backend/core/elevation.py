"""Route elevation independent of weather; terrain enrichment never re-routes a path."""

import hashlib
import json
import math
from bisect import bisect_right
from itertools import pairwise

import httpx
from pydantic import Field, field_validator, model_validator

from .gpx import distances, validate_track
from .models import ElevationProfile
from .schemas import CamelSchema

MAX_POINTS = 2000


class ElevationPoint(CamelSchema):
    distance_m: float
    elapsed_s: float
    elevation_m: float | None


class ElevationOut(CamelSchema):
    points: list[ElevationPoint]
    source: str
    approximate_timing: bool = False


class ElevationIn(CamelSchema):
    coordinates: list[list[float]] = Field(min_length=2, max_length=100000)
    total_seconds: float = Field(gt=0, le=1382400, allow_inf_nan=False)
    vertex_times: list[float] | None = Field(default=None, max_length=100000)

    _coordinates = field_validator("coordinates")(validate_track)

    @model_validator(mode="after")
    def check_times(self):
        times = self.vertex_times
        if times is not None and (
            len(times) != len(self.coordinates)
            or not all(math.isfinite(t) for t in times)
            or times[0] != 0
            or any(a > b for a, b in pairwise(times))
            or times[-1] > self.total_seconds + 1
        ):
            raise ValueError("Ungültige Fahrzeiten entlang der Strecke.")
        return self


def profile_samples(coordinates, times, total_seconds):
    """Sample on the original line, preserving endpoints and route order even on loops."""
    cumulative = distances(coordinates)
    total = cumulative[-1]
    if total <= 0:
        return []
    times = times or [d / total * total_seconds for d in cumulative]
    # Include original vertices when small; long routes use bounded, evenly spaced positions.
    targets = (
        cumulative if len(coordinates) <= MAX_POINTS else [total * i / (MAX_POINTS - 1) for i in range(MAX_POINTS)]
    )
    # Densify sparse imported tracks for terrain lookup, at most every 30 m (bounded above).
    if any(len(p) < 3 for p in coordinates):
        count = min(MAX_POINTS, max(2, math.ceil(total / 30) + 1))
        targets = sorted({0.0, total, *(total * i / (count - 1) for i in range(count))})
    result = []
    for distance in targets:
        i = min(max(0, bisect_right(cumulative, distance) - 1), len(coordinates) - 2)
        span = cumulative[i + 1] - cumulative[i]
        fraction = (distance - cumulative[i]) / span if span else 0
        a, b = coordinates[i : i + 2]
        delta_lon = (b[0] - a[0] + 180) % 360 - 180
        lon = (a[0] + fraction * delta_lon + 180) % 360 - 180
        elevation = a[2] + fraction * (b[2] - a[2]) if len(a) == len(b) == 3 else None
        result.append(
            {
                "lon": lon,
                "lat": a[1] + fraction * (b[1] - a[1]),
                "distance_m": distance,
                "elapsed_s": times[i] + fraction * (times[i + 1] - times[i]),
                "elevation_m": elevation,
            }
        )
    return result


async def terrain_heights(samples):
    from .weather import GRAPHHOPPER_URL

    missing = [p for p in samples if p["elevation_m"] is None]
    if not missing:
        return
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{GRAPHHOPPER_URL}/elevation", json={"points": [[p["lon"], p["lat"]] for p in missing]}
        )
        response.raise_for_status()
    heights = response.json().get("elevation")
    if not isinstance(heights, list) or len(heights) != len(missing):
        raise ValueError("Ungültige Höhenantwort von GraphHopper.")
    for point, height in zip(missing, heights, strict=True):
        point["elevation_m"] = height if isinstance(height, (int, float)) and math.isfinite(height) else None


async def elevation_profile(coordinates, total_seconds, vertex_times=None, source="GraphHopper / Mapterhorn"):
    if len(coordinates) < 2 or not total_seconds:
        return {"points": [], "source": source, "approximate_timing": True}
    # Old cached jobs may lack vertex times. Use distance-based timing rather than inventing precision.
    if vertex_times is not None and (
        len(vertex_times) != len(coordinates)
        or not vertex_times
        or any(not math.isfinite(t) for t in vertex_times)
        or vertex_times[0] != 0
        or any(a > b for a, b in pairwise(vertex_times))
    ):
        vertex_times = None
    key = hashlib.sha256(
        json.dumps(["v1-z15", coordinates, total_seconds, vertex_times, source], separators=(",", ":")).encode()
    ).hexdigest()
    cached = await ElevationProfile.objects.filter(key=key).afirst()
    if cached:
        return cached.data
    samples = profile_samples(coordinates, vertex_times, total_seconds)
    missing = any(p["elevation_m"] is None for p in samples)
    if missing:
        await terrain_heights(samples)
        source = "GraphHopper / Mapterhorn"
    result = {
        "points": [{key: sample[key] for key in ("distance_m", "elapsed_s", "elevation_m")} for sample in samples],
        "source": source,
        "approximate_timing": vertex_times is None,
    }
    # Retry gaps on a later request: missing remote tiles must not become permanent empty profiles.
    if samples and all(p["elevation_m"] is not None for p in samples):
        await ElevationProfile.objects.aget_or_create(key=key, defaults={"data": result})
    return result


def with_heights(coordinates, heights):
    if not heights or len(heights) != len(coordinates):
        return coordinates
    return [
        list(p[:2]) + ([h] if h is not None and math.isfinite(h) else [])
        for p, h in zip(coordinates, heights, strict=True)
    ]
