"""Bike-route weather forecast.

Given a start, a destination, a routing profile and a departure time, this:
  1. routes start -> destination with GraphHopper, asking for geometry + per-segment time,
  2. samples the route at fixed *time* intervals (so each sample has a place AND a clock time),
  3. looks up the forecast for each (place, clock-time) from the grid layer
     (ForecastCell + EnsembleCell), falling back to live API fetch + store if needed,
  4. derives head/tail/cross-wind relative to the direction of travel.

The route sampling is decoupled from the forecast resolution: GraphHopper gives the true
per-road travel time, so we know exactly when you reach each point of the polyline.
"""
import json
import math
import os
from datetime import date, datetime, timedelta

import httpx
from async_lru import alru_cache
from loguru import logger

from .api.route_weather import RouteWeatherOut, RouteWeatherSummary, WeatherSample
from .grid import (
    ENSEMBLE_MODELS,
    extract_sample,
    get_cached_ensemble_cell,
    get_cached_forecast_cell,
    get_or_fetch_ensemble_cell,
    get_or_fetch_forecast_cell,
)
from .uncertainty import extract_uncertainty

GRAPHHOPPER_URL = os.environ.get("GRAPHHOPPER_API_URL", "http://localhost:8989").rstrip("/")

SAMPLE_INTERVAL_DEFAULT_S = 300  # one weather sample per 5 minutes of riding
RAIN_THRESHOLD_MM = 0.1  # precipitation (mm) within a 15-min step that counts as "rain"
COORD_ROUND = 2  # ~1 km grid: nearby samples share one forecast call

# Ensemble (probability of precipitation): each member counts as "rain this hour" at >= this much,
# and we call it rain at a point when at least this fraction of members agree. Tuned against a week
# of live data so showery hours light up while clear hours stay quiet.
POP_VERDICT = 0.25  # fraction of wet members at a point that flips will_rain to true

# WMO weather codes -> short German description (Open-Meteo `weather_code`).
WMO_DE = {
    0: "Klar",
    1: "Meist klar",
    2: "Teils bewölkt",
    3: "Bewölkt",
    45: "Nebel",
    48: "Reifnebel",
    51: "Leichter Niesel",
    53: "Niesel",
    55: "Starker Niesel",
    56: "Gefrierender Niesel",
    57: "Starker gefrierender Niesel",
    61: "Leichter Regen",
    63: "Regen",
    65: "Starker Regen",
    66: "Gefrierender Regen",
    67: "Starker gefrierender Regen",
    71: "Leichter Schneefall",
    73: "Schneefall",
    75: "Starker Schneefall",
    77: "Schneegriesel",
    80: "Leichte Regenschauer",
    81: "Regenschauer",
    82: "Heftige Regenschauer",
    85: "Leichte Schneeschauer",
    86: "Starke Schneeschauer",
    95: "Gewitter",
    96: "Gewitter mit Hagel",
    99: "Schweres Gewitter mit Hagel",
}


# --------------------------------------------------------------------------- geometry helpers
def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _bearing_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Initial compass bearing (deg, 0=N, clockwise) from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _wind_components(wind_speed: float, wind_dir: float, travel_bearing: float) -> tuple[float, float]:
    """Resolve wind into head/cross components relative to the direction of travel.

    `wind_dir` is the compass direction the wind blows *from*. Returns (headwind, crosswind):
    headwind > 0 means wind against you, < 0 means tailwind; crosswind is the absolute side component.
    """
    rel = math.radians(wind_dir - travel_bearing)
    return wind_speed * math.cos(rel), abs(wind_speed * math.sin(rel))


def _cumulative_times_s(coords: list[list[float]], time_details: list[list]) -> list[float]:
    """Elapsed seconds at every polyline vertex.

    GraphHopper `details.time` is a list of [from_idx, to_idx, time_ms] intervals over the
    points array. We spread each interval's time across its sub-segments weighted by geodesic
    length, so a long edge gets proportionally more time than a short one.
    """
    n = len(coords)
    seg_s = [0.0] * n  # seconds to travel from vertex i-1 to vertex i (indexed by i)
    for frm, to, time_ms in time_details:
        frm, to = int(frm), int(to)
        if to <= frm:
            continue
        lengths = [_haversine_m(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1]) for i in range(frm, to)]
        total = sum(lengths)
        secs = time_ms / 1000.0
        for k, i in enumerate(range(frm + 1, to + 1)):
            seg_s[i] = secs * (lengths[k] / total) if total > 0 else secs / (to - frm)

    cum = [0.0] * n
    for i in range(1, n):
        cum[i] = cum[i - 1] + seg_s[i]
    return cum


def _sample_indices(cum_s: list[float], interval_s: int) -> list[int]:
    """Vertex indices nearest to elapsed times 0, interval, 2*interval, ... and the final point."""
    total = cum_s[-1] if cum_s else 0.0
    targets = []
    t = 0.0
    while t < total:
        targets.append(t)
        t += interval_s
    targets.append(total)

    indices: list[int] = []
    j = 0
    for target in targets:
        while j + 1 < len(cum_s) and cum_s[j + 1] <= target:
            j += 1
        # pick nearer of j and j+1
        idx = j
        if j + 1 < len(cum_s) and abs(cum_s[j + 1] - target) < abs(cum_s[j] - target):
            idx = j + 1
        if not indices or indices[-1] != idx:
            indices.append(idx)
    return indices


# --------------------------------------------------------------------------- routing
@alru_cache(maxsize=64)
async def _fetch_route(profile: str, start_lat: float, start_lon: float, dest_lat: float, dest_lon: float) -> dict:
    body = {
        "profile": profile,
        "points": [[start_lon, start_lat], [dest_lon, dest_lat]],
        "points_encoded": False,
        "calc_points": True,
        "instructions": False,
        "details": ["time"],
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{GRAPHHOPPER_URL}/route", json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()


# --------------------------------------------------------------------------- forecast window
def _forecast_days(eta: datetime, today: date) -> int:
    """Open-Meteo forecast_days needed to cover eta. `today` is the window origin (00:00 local)."""
    return max(1, min(16, (eta.date() - today).days + 2))


# --------------------------------------------------------------------------- summarization
def _summarize(samples: list[WeatherSample], source: str) -> RouteWeatherSummary:
    """Route-level verdict.

    Two coherent modes:
    - probabilistic (some point has a `pop`): verdict from the ensemble (>= POP_VERDICT)
    - deterministic fallback (no point has a pop): verdict from the rain amount, with
      rain_probability = None so the UI shows mm instead of "0%".
    """
    if any(s.pop is not None for s in samples):
        wet = [s for s in samples if (s.pop or 0.0) >= POP_VERDICT]
        peak = max(samples, key=lambda s: (s.pop or 0.0))
        amount = peak.rain_if_wet if peak.rain_if_wet is not None else peak.rain_mm
        rain_probability = round(max(s.pop or 0.0 for s in samples), 2)
        rain_amount = round(amount, 1)
    else:
        wet = [s for s in samples if s.rain_mm >= RAIN_THRESHOLD_MM]
        rain_probability = None
        rain_amount = round(max((s.rain_mm for s in samples), default=0.0), 1)
    return RouteWeatherSummary(
        will_rain=bool(wet),
        first_rain_eta=wet[0].eta if wet else None,
        first_rain_place=f"{wet[0].lat:.4f},{wet[0].lon:.4f}" if wet else None,
        max_rain_mm=round(max((s.rain_mm for s in samples), default=0.0), 2),
        rain_probability=rain_probability,
        rain_amount=rain_amount,
        max_headwind=round(max((s.headwind for s in samples), default=0.0), 1),
        source=source,
    )


# --------------------------------------------------------------------------- core logic
async def compute_route_weather(
    start_lat: float,
    start_lon: float,
    dest_lat: float,
    dest_lon: float,
    profile: str,
    departure_time: str,
    sample_points: list[dict] | None = None,
    polyline: list[list[float]] | None = None,
    total_seconds: int | None = None,
    total_distance_m: float | None = None,
    interval_seconds: int = SAMPLE_INTERVAL_DEFAULT_S,
    cache_only: bool = False,
    include_uncertainty: bool = True,
) -> RouteWeatherOut:
    """Compute weather along a route.

    If pre-computed sample_points + polyline are provided (from a saved RecurringRoute),
    skips the GraphHopper routing step. Otherwise fetches the route and samples it.
    Weather data is always looked up via the grid layer (ForecastCell / EnsembleCell),
    which caches raw API responses keyed by ~1 km² cells.

    Args:
        cache_only: read only cells already in the DB, never spend an API request. A sample
            point whose cell is cold is simply left out. Callers that run per route on a
            polling endpoint (the list thumbnails) must set this — the fetching path would
            otherwise hit Open-Meteo once per cold cell on every poll.
        include_uncertainty: when false, skip the ensemble lookup entirely. Drops `pop`,
            `rain_if_wet` and the spread from every sample, so only use it where those are
            not rendered.
    """
    departure = datetime.fromisoformat(departure_time)
    today = date.today()

    if sample_points is not None and polyline is not None:
        # Pre-computed route: skip GraphHopper
        coords = polyline
        total_s = total_seconds or 0
        total_dist = total_distance_m or 0.0
    else:
        # Ad-hoc: fetch route from GraphHopper and compute sample points
        route = await _fetch_route(profile, start_lat, start_lon, dest_lat, dest_lon)
        path = route["paths"][0]
        coords: list[list[float]] = path["points"]["coordinates"]
        time_details = path.get("details", {}).get("time", [[0, len(coords) - 1, path.get("time", 0)]])
        cum_s = _cumulative_times_s(coords, time_details)
        sample_idx = _sample_indices(cum_s, max(60, interval_seconds))
        total_s = int(cum_s[-1]) if cum_s else 0
        total_dist = round(path.get("distance", 0.0), 1)

        # Build sample_points on the fly
        sample_points = []
        for idx in sample_idx:
            lon, lat = coords[idx][0], coords[idx][1]
            sample_points.append({
                "lat": lat,
                "lon": lon,
                "lat_r": round(lat, COORD_ROUND),
                "lon_r": round(lon, COORD_ROUND),
                "elapsed_s": int(cum_s[idx]),
                "idx": idx,
            })

    # For each sample point, look up weather from the grid
    samples: list[WeatherSample] = []
    forecast_source = "open-meteo"

    for i, sp in enumerate(sample_points):
        logger.debug("compute_route_weather: processing sample point {}: {}", i, sp)
        lat = sp["lat"]
        lon = sp["lon"]
        lat_r = sp["lat_r"]
        lon_r = sp["lon_r"]
        elapsed = sp["elapsed_s"]
        eta = departure + timedelta(seconds=elapsed)
        idx = sp.get("idx", i)  # vertex index for bearing calculation

        days = _forecast_days(eta, today)
        day_key_str = departure.date().isoformat()

        # Look up deterministic weather from grid (fetches + stores if missing)
        if cache_only:
            cell = await get_cached_forecast_cell(lat_r, lon_r, day_key_str, days)
        else:
            cell = await get_or_fetch_forecast_cell(lat_r, lon_r, day_key_str, days)
        if cell is None:
            logger.warning(f"No forecast data for ({lat_r}, {lon_r}) at {eta}")
            continue
        logger.debug("compute_route_weather: got forecast cell for {}: {}\n{}", eta, cell, json.dumps(cell.data, indent=2))
        forecast = extract_sample(cell.data, eta, cell.source)
        if forecast is None:
            logger.warning(f"Could not extract sample at {eta} from cell data")
            continue
        forecast_source = forecast["source"]

        # Wind bearing: use vertex index if available, else neighboring sample points
        if "idx" in sp and sp["idx"] > 0 and sp["idx"] < len(coords) - 1:
            a_idx = max(0, idx - 1)
            b_idx = min(len(coords) - 1, idx + 1)
            bearing = _bearing_deg(coords[a_idx][0], coords[a_idx][1], coords[b_idx][0], coords[b_idx][1])
        else:
            # Fallback to neighboring sample points
            a_i = max(0, i - 1)
            b_i = min(len(sample_points) - 1, i + 1)
            a = sample_points[a_i]
            b = sample_points[b_i]
            bearing = _bearing_deg(a["lon"], a["lat"], b["lon"], b["lat"]) if a_i != b_i else 0.0

        uncertainty = None
        if not include_uncertainty:
            ens_cell = None
        elif cache_only:
            ens_cell = await get_cached_ensemble_cell(lat_r, lon_r, day_key_str, days)
        else:
            ens_cell = await get_or_fetch_ensemble_cell(lat_r, lon_r, day_key_str, days)
        if ens_cell is not None:
            uncertainty = extract_uncertainty(
                ens_cell.data, eta, bearing, ens_cell.fetched_at, ENSEMBLE_MODELS.split(","),
            )
        pop = uncertainty.pop if uncertainty is not None else None
        rain_if_wet = uncertainty.rain_if_wet if uncertainty is not None else None
        probability_source = "open-meteo-ensemble" if pop is not None else None
        if pop is None:
            pop = forecast.get("pop")
            probability_source = forecast_source if pop is not None else None
        interval = forecast.get("precipitation_interval_s", 3600)

        headwind, crosswind = _wind_components(forecast["wind_speed"], forecast["wind_dir"], bearing)

        samples.append(
            WeatherSample(
                lat=lat,
                lon=lon,
                elapsed_s=int(elapsed),
                eta=eta.isoformat(),
                rain_mm=round(forecast["rain_mm"], 2),
                precipitation_interval_s=interval,
                rain_rate_mm_h=round(forecast["rain_mm"] * 3600 / interval, 3),
                uncertainty=uncertainty,
                probability_source=probability_source,
                pop=round(pop, 2) if pop is not None else None,
                rain_if_wet=round(rain_if_wet, 2) if rain_if_wet is not None else None,
                temp=round(forecast["temp"], 1),
                wind_speed=round(forecast["wind_speed"], 1),
                wind_gust=round(forecast["wind_gust"], 1) if forecast["wind_gust"] is not None else None,
                wind_dir=round(forecast["wind_dir"], 0),
                headwind=round(headwind, 1),
                crosswind=round(crosswind, 1),
                weather_code=forecast["weather_code"],
                weather_desc=WMO_DE.get(forecast["weather_code"], "") if forecast["weather_code"] is not None else "",
            )
        )

    if not samples:
        logger.warning(
            "compute_route_weather: all {} sample points produced empty forecast data",
            len(sample_points),
        )

    summary = _summarize(samples, forecast_source)

    return RouteWeatherOut(
        line=coords,
        total_seconds=total_s,
        total_distance_m=round(total_dist, 1),
        samples=samples,
        summary=summary,
    )
