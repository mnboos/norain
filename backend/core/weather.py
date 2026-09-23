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
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import httpx
from async_lru import alru_cache
from loguru import logger

from .forecast_schemas import RouteWeatherOut, RouteWeatherSummary, WeatherSample, WindDistribution, WindSegment
from .geo import haversine_m as _haversine_m
from .grid import (
    ENSEMBLE_MODELS,
    extract_sample,
    get_cached_ensemble_cell,
    get_cached_forecast_cell,
    get_or_fetch_ensemble_cell,
    get_or_fetch_forecast_cell,
)
from .models import EnsembleCell
from .road_prefs import model_key
from .schedule import local_today
from .stations import (
    RAIN_HORIZON,
    STATION_HORIZON,
    Reading,
    StationCorrection,
    get_cached_readings,
    lead_weight,
    station_correction,
)
from .telemetry import provider
from .uncertainty import EnsembleCentral, ensemble_central, ensemble_weight, extract_uncertainty
from .wind import compute_wind_profile, felt_temperature, normalize_wind, resolve_vertex_times, sample_airspeed

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
def _cumulative_times_s(coords: list[list[float]], time_details: list[list]) -> list[float]:
    """Elapsed seconds at every polyline vertex.

    GraphHopper `details.time` is a list of [from_idx, to_idx, time_ms] intervals over the
    points array. We spread each interval's time across its sub-segments weighted by geodesic
    length, so a long edge gets proportionally more time than a short one.
    """
    n = len(coords)
    seg_s = [0.0] * n  # seconds to travel from vertex i-1 to vertex i (indexed by i)
    for raw_from, raw_to, time_ms in time_details:
        frm, to = int(raw_from), int(raw_to)
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
    if not cum_s:
        return []
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
# How a routing call fails: GraphHopper unreachable or refusing (HTTPError), a body that is
# not JSON (ValueError), or a reply without a path (KeyError, IndexError).
ROUTING_ERRORS = (httpx.HTTPError, ValueError, KeyError, IndexError)

# (lon, lat) pairs in riding order: start, any via points, destination. A tuple of tuples,
# because alru_cache keys on the arguments.
RoutingPoints = tuple[tuple[float, float], ...]


def routing_points(start_lat: float, start_lon: float, dest_lat: float, dest_lon: float, via=()) -> RoutingPoints:
    """Start, via points ([lon, lat] each) and destination as ``_fetch_route`` takes them."""
    return ((start_lon, start_lat), *((float(lon), float(lat)) for lon, lat in via), (dest_lon, dest_lat))


def _route_body(profile: str, points: RoutingPoints, custom_model: dict | None = None, alternatives: int = 0) -> dict:
    """The one GraphHopper request, so the editor's preview and the saved geometry agree.

    ``custom_model`` is a request model on top of the profile's (journey road preferences and
    weather zones, see ``core.road_prefs``); it must only add penalties, or LM gives wrong
    routes. ``alternatives`` > 1 asks for that many paths, which GraphHopper only does between
    two points and, on this graph, for up to about a day's ride (the node cap).
    """
    body = {
        "profile": profile,
        "points": [list(p) for p in points],
        "points_encoded": False,
        "calc_points": True,
        "instructions": False,
        "details": ["time"],
    }
    if custom_model:
        body["custom_model"] = custom_model
    if alternatives > 1:
        body["algorithm"] = "alternative_route"
        body["alternative_route.max_paths"] = alternatives
    return body


@alru_cache(maxsize=64)
@provider("graphhopper")
async def _fetch_route(profile: str, points: RoutingPoints, custom_model: str = "", alternatives: int = 0) -> dict:
    """``custom_model`` arrives as canonical JSON (``road_prefs.model_key``): the LRU keys on it."""
    body = _route_body(profile, points, json.loads(custom_model) if custom_model else None, alternatives)
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{GRAPHHOPPER_URL}/route", json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()


async def _route(profile: str, points: RoutingPoints, custom_model: dict | None = None, alternatives: int = 0) -> dict:
    """``_fetch_route`` with the request model in its hashable form; a plain request stays
    ``(profile, points)``, so it shares LRU entries with every earlier caller."""
    if not custom_model and alternatives < 2:
        return await _fetch_route(profile, points)
    return await _fetch_route(profile, points, model_key(custom_model), alternatives if alternatives > 1 else 0)


async def preview_route(profile: str, points: RoutingPoints, custom_model: dict | None = None) -> dict:
    """The line alone, for the route editor: no sampling, no weather."""
    path = (await _route(profile, points, custom_model))["paths"][0]
    return {
        "coordinates": path["points"]["coordinates"],
        "distance_m": round(path.get("distance", 0.0), 1),
        "time_s": int(path.get("time", 0) / 1000),
    }


# --------------------------------------------------------------------------- geometry
def _path_geometry(path: dict, interval_seconds: int) -> dict:
    """Sample one GraphHopper path at fixed *time* intervals."""
    coords: list[list[float]] = path["points"]["coordinates"]
    time_details = path.get("details", {}).get("time", [[0, len(coords) - 1, path.get("time", 0)]])
    cum_s = _cumulative_times_s(coords, time_details)

    sample_points = []
    for idx in _sample_indices(cum_s, max(60, interval_seconds)):
        lon, lat = coords[idx][0], coords[idx][1]
        sample_points.append(
            {
                "lat": lat,
                "lon": lon,
                "lat_r": round(lat, COORD_ROUND),
                "lon_r": round(lon, COORD_ROUND),
                "elapsed_s": int(cum_s[idx]),
                "idx": idx,
            }
        )

    return {
        "polyline": coords,
        "vertex_times": cum_s,
        "sample_points": sample_points,
        "total_seconds": int(cum_s[-1]) if cum_s else 0,
        "total_distance_m": round(path.get("distance", 0.0), 1),
    }


async def build_geometry(
    profile: str,
    points: RoutingPoints,
    interval_seconds: int = SAMPLE_INTERVAL_DEFAULT_S,
    custom_model: dict | None = None,
) -> dict:
    """Route with GraphHopper and sample it at fixed *time* intervals.

    Returns the polyline plus the sample points every later stage keys off, each carrying
    its rounded grid coordinates. Shared by the ad-hoc forecast path, the saved-route
    geometry task, forecast-job planning and journey planning so all sample a route identically.
    """
    route = await _route(profile, points, custom_model)
    return _path_geometry(route["paths"][0], interval_seconds)


async def build_geometries(
    profile: str,
    points: RoutingPoints,
    alternatives: int,
    interval_seconds: int = SAMPLE_INTERVAL_DEFAULT_S,
    custom_model: dict | None = None,
) -> list[dict]:
    """Up to ``alternatives`` sampled paths between two points, best first.

    Raises the routing errors like ``build_geometry``; a day too long for alternatives
    (GraphHopper's node cap) is the caller's to retry with one path.
    """
    route = await _route(profile, points, custom_model, alternatives)
    return [_path_geometry(path, interval_seconds) for path in route["paths"]]


# --------------------------------------------------------------------------- forecast window
def _forecast_days(eta: datetime, today: date) -> int:
    """Open-Meteo forecast_days needed to cover eta. `today` is the window origin (00:00 local)."""
    return max(1, min(16, (eta.date() - today).days + 2))


def forecast_days_for(departure: datetime, sample_points: list[dict], today: date) -> int:
    """The single forecast horizon covering every sample point of one departure.

    Deriving this per sample instead would break two things at once. A route crossing
    midnight gives a later sample a larger horizon, so ``_get_forecast_cell_sync`` rejects
    the cell an earlier sample just stored and refetches it inside the same request; and
    two samples in one cell would claim under different keys, defeating deduplication.
    """
    last = max((sp["elapsed_s"] for sp in sample_points), default=0)
    return _forecast_days(departure + timedelta(seconds=last), today)


# --------------------------------------------------------------------------- summarization
def mean_felt_temp(samples: list[dict]) -> float | None:
    """Time-weighted mean felt temperature of a ride, from stored sample dicts.

    Each sample stands for half the riding time to each neighbour. Samples without
    ``felt_temp`` (no timing, or stored before it existed) count with their air temperature.
    """
    points = [
        (s["elapsed_s"], s["felt_temp"] if s.get("felt_temp") is not None else s["temp"])
        for s in samples
        if s.get("elapsed_s") is not None and s.get("temp") is not None
    ]
    if not points:
        return None
    if len(points) == 1:
        return points[0][1]
    weighted = total = 0.0
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        gap = max(0.0, t1 - t0)
        weighted += gap * (v0 + v1) / 2
        total += gap
    return weighted / total if total > 0 else sum(v for _, v in points) / len(points)


def _blend(value: float | None, central: float | None, weight: float) -> float | None:
    """``value`` moved ``weight`` of the way to ``central``; the ensemble alone only at weight 1."""
    if central is None:
        return value
    if value is None:
        return central if weight >= 1 else None
    return (1 - weight) * value + weight * central


def _blend_with_ensemble(forecast: dict, central: EnsembleCentral, weight: float) -> None:
    """Move the single run's temperature and wind toward the ensemble's central estimate.

    Done in place, before the wind profile and the samples are built, so every later reader of
    ``forecast`` (headwind, wind power, felt temperature, frost, arrows) sees the same number.
    Rain and the weather code stay the single run's: the ride score already weighs the
    ensemble's rain through ``pop`` and ``rain_if_wet``.
    """
    forecast["temp"] = _blend(forecast["temp"], central.temp, weight)
    forecast["wind_gust"] = _blend(forecast.get("wind_gust"), central.wind_gust, weight)
    if central.wind is None:
        return
    own = normalize_wind(forecast.get("wind_speed"), forecast.get("wind_dir"))
    east = _blend(own.east if own else None, central.wind.east, weight)
    north = _blend(own.north if own else None, central.wind.north, weight)
    if east is None or north is None:
        return
    forecast["wind_speed"] = math.hypot(east, north)
    forecast["wind_dir"] = math.degrees(math.atan2(east, north)) % 360


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
        max_headwind=max((s.headwind for s in samples if s.headwind is not None), default=None),
        max_wind_power_w=max((s.wind_power_w for s in samples if s.wind_power_w is not None), default=None),
        source=source,
    )


# --------------------------------------------------------------------------- core logic
@dataclass
class WeatherSnapshot:
    """One job's cache reads, shared by all candidate departures."""

    forecast_days: int
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    cells: dict = field(default_factory=dict)
    readings: list[list[Reading]] | None = None

    async def cell(self, kind, lat, lon, day):
        key = (kind, lat, lon, day)
        if key not in self.cells:
            getter = get_cached_forecast_cell if kind == "forecast" else get_cached_ensemble_cell
            self.cells[key] = await getter(lat, lon, day, self.forecast_days)
        return self.cells[key]


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
    vertex_times: list[float] | None = None,
    include_segments: bool = True,
    station_correction_enabled: bool = False,
    snapshot: WeatherSnapshot | None = None,
    strict_coverage: bool = False,
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
        station_correction_enabled: nudge the temperature and rain probability of samples
            near now toward what weather stations measure. Reads stored readings only; the
            `refresh_station_observations` task is what fetches them.
    """
    departure = datetime.fromisoformat(departure_time)
    # Aware departures cross DST transitions in elapsed time, not wall-clock time.
    local_departure = departure
    if departure.tzinfo is not None:
        from .schedule import LOCAL_TZ
        local_departure = departure.astimezone(LOCAL_TZ)
        departure = departure.astimezone(UTC)
    today = local_today()

    if sample_points is not None and polyline is not None:
        # Pre-computed route: skip GraphHopper
        coords = polyline
        total_s = total_seconds or 0
        total_dist = total_distance_m or 0.0
    else:
        # Ad-hoc: fetch route from GraphHopper and compute sample points
        geometry = await build_geometry(
            profile, routing_points(start_lat, start_lon, dest_lat, dest_lon), interval_seconds
        )
        coords = geometry["polyline"]
        sample_points = geometry["sample_points"]
        total_s = geometry["total_seconds"]
        total_dist = geometry["total_distance_m"]
        vertex_times = geometry["vertex_times"]

    # For each sample point, look up weather from the grid. One horizon covers them all,
    # so every cell of this departure shares a cache key and a claim key.
    days = snapshot.forecast_days if snapshot is not None else forecast_days_for(local_departure, sample_points, today)
    day_key_str = local_departure.date().isoformat()
    samples: list[WeatherSample] = []
    forecast_source = "open-meteo"
    forecasts: list[dict | None] = [None] * len(sample_points)
    ensemble_cells: list[EnsembleCell | None] = [None] * len(sample_points)
    ensemble_weights: list[float | None] = [None] * len(sample_points)
    corrections: list[StationCorrection | None] = [None] * len(sample_points)
    readings: list[list[Reading]] = [[] for _ in sample_points]
    if station_correction_enabled:
        if snapshot is not None:
            if snapshot.readings is None:
                snapshot.readings = await get_cached_readings(
                    [{**sp, "elapsed_s": 0} for sp in sample_points], snapshot.now, snapshot.now,
                )
            readings = snapshot.readings or []
        else:
            readings = await get_cached_readings(sample_points, departure, datetime.now(tz=UTC))

    for i, sp in enumerate(sample_points):
        logger.debug("compute_route_weather: processing sample point {}: {}", i, sp)
        lat = sp["lat"]
        lon = sp["lon"]
        lat_r = sp["lat_r"]
        lon_r = sp["lon_r"]
        elapsed = sp["elapsed_s"]
        eta = departure + timedelta(seconds=elapsed)

        # Look up deterministic weather from grid (fetches + stores if missing)
        if snapshot is not None:
            cell = await snapshot.cell("forecast", lat_r, lon_r, day_key_str)
        elif cache_only:
            cell = await get_cached_forecast_cell(lat_r, lon_r, day_key_str, days)
        else:
            cell = await get_or_fetch_forecast_cell(lat_r, lon_r, day_key_str, days)
        if cell is None:
            logger.warning(f"No forecast data for ({lat_r}, {lon_r}) at {eta}")
            continue
        if strict_coverage:
            from .departures import cell_covers
            if not cell_covers(cell.data, eta, cell.source):
                continue
        logger.debug("compute_route_weather: got forecast cell for {}: {}", eta, cell)
        forecast = extract_sample(cell.data, eta, cell.source)
        if forecast is None:
            logger.warning(f"Could not extract sample at {eta} from cell data")
            continue
        forecast_source = forecast["source"]
        if readings[i]:
            # Temperature is corrected here, before the wind profile and the samples are
            # built, so every later reader of `forecast` sees the same number.
            observed_at = min(r.observed_at for r in readings[i])
            model_now = extract_sample(cell.data, observed_at, cell.source)
            correction = station_correction(readings[i], model_now["temp"] if model_now else None)
            corrections[i] = correction
            if correction is not None and correction.temp_offset is not None:
                forecast["temp"] += lead_weight(eta, observed_at, STATION_HORIZON) * correction.temp_offset
        forecasts[i] = forecast
        if not include_uncertainty:
            ens_cell = None
        elif snapshot is not None:
            ens_cell = await snapshot.cell("ensemble", lat_r, lon_r, day_key_str)
        elif cache_only:
            ens_cell = await get_cached_ensemble_cell(lat_r, lon_r, day_key_str, days)
        else:
            ens_cell = await get_or_fetch_ensemble_cell(lat_r, lon_r, day_key_str, days)
        ensemble_cells[i] = ens_cell
        if ens_cell is not None and (weight := ensemble_weight(eta, ens_cell.fetched_at)) > 0:
            central = ensemble_central(ens_cell.data, eta, ENSEMBLE_MODELS.split(","))
            if central is not None:
                _blend_with_ensemble(forecast, central, weight)
                ensemble_weights[i] = round(weight, 2)

    # Future anchors are now available. Integrate unrounded vectors over geometry once;
    # gaps retain their original indices rather than becoming adjacent valid samples.
    times = resolve_vertex_times(coords, sample_points, vertex_times)
    wind = compute_wind_profile(
        coords, sample_points,
        [normalize_wind(f.get("wind_speed"), f.get("wind_dir")) if f is not None else None for f in forecasts],
        times, total_dist, include_segments=include_segments,
    )
    for i, sp in enumerate(sample_points):
        forecast = forecasts[i]
        if forecast is None:
            continue
        lat, lon, elapsed = sp["lat"], sp["lon"], sp["elapsed_s"]
        eta = departure + timedelta(seconds=elapsed)
        forecast_source = forecast["source"]
        ens_cell = ensemble_cells[i]
        uncertainty = None
        if ens_cell is not None:
            uncertainty = extract_uncertainty(
                ens_cell.data, eta, None, ens_cell.fetched_at, ENSEMBLE_MODELS.split(","),
                wind_support=wind.samples[i].support,
            )
        pop = uncertainty.pop if uncertainty is not None else None
        rain_if_wet = uncertainty.rain_if_wet if uncertainty is not None else None
        probability_source = "open-meteo-ensemble" if pop is not None else None
        if pop is None:
            pop = forecast.get("pop")
            probability_source = forecast_source if pop is not None else None
        interval = forecast.get("precipitation_interval_s", 3600)

        station_count = None
        correction = corrections[i]
        if correction is not None:
            if correction.temp_offset is not None and lead_weight(eta, correction.observed_at, STATION_HORIZON) > 0:
                station_count = correction.station_count
            rain_weight = lead_weight(eta, correction.observed_at, RAIN_HORIZON)
            if pop is not None and correction.wet_share is not None and rain_weight > 0:
                pop = (1 - rain_weight) * pop + rain_weight * correction.wet_share
                if correction.wet_rate_mm_h is not None and not rain_if_wet:
                    # The ensemble has no wet member, but the stations measure rain: the
                    # "if it rains" amount is their measured rate, not a made-up number.
                    rain_if_wet = correction.wet_rate_mm_h
                station_count = correction.station_count

        headwind, crosswind = wind.samples[i].headwind, wind.samples[i].cross_abs_mean
        wind_power_w = wind.samples[i].wind_power_w
        airspeed = sample_airspeed(wind.samples[i])
        felt_temp = felt_temperature(forecast["temp"], airspeed) if airspeed is not None else None

        samples.append(
            WeatherSample(
                lat=lat,
                lon=lon,
                elapsed_s=int(elapsed),
                sample_index=i,
                wind_coverage=wind.samples[i].coverage,
                eta=(eta.astimezone(local_departure.tzinfo) if local_departure.tzinfo else eta).isoformat(),
                rain_mm=round(forecast["rain_mm"], 2),
                precipitation_interval_s=interval,
                rain_rate_mm_h=round(forecast["rain_mm"] * 3600 / interval, 3),
                uncertainty=uncertainty,
                probability_source=probability_source,
                pop=round(pop, 2) if pop is not None else None,
                rain_if_wet=round(rain_if_wet, 2) if rain_if_wet is not None else None,
                temp=round(forecast["temp"], 1),
                felt_temp=round(felt_temp, 1) if felt_temp is not None else None,
                wind_speed=round(forecast["wind_speed"], 1) if forecast["wind_speed"] is not None else None,
                wind_gust=round(forecast["wind_gust"], 1) if forecast["wind_gust"] is not None else None,
                wind_dir=round(forecast["wind_dir"], 0) % 360 if forecast["wind_dir"] is not None else None,
                headwind=round(headwind, 1) if headwind is not None else None,
                crosswind=round(crosswind, 1) if crosswind is not None else None,
                wind_power_w=round(wind_power_w) if wind_power_w is not None else None,
                weather_code=forecast["weather_code"],
                weather_desc=WMO_DE.get(forecast["weather_code"], "") if forecast["weather_code"] is not None else "",
                station_count=station_count,
                ensemble_weight=ensemble_weights[i],
            )
        )

    if not samples:
        logger.warning(
            "compute_route_weather: all {} sample points produced empty forecast data",
            len(sample_points),
        )

    summary = _summarize(samples, forecast_source)
    summary.station_corrected = any(s.station_count for s in samples)
    if wind.distribution is not None:
        summary.wind_distribution = WindDistribution(**wind.distribution)

    return RouteWeatherOut(
        line=coords,
        total_seconds=total_s,
        total_distance_m=wind.total_distance_m,
        samples=samples,
        summary=summary,
        wind_segments=[WindSegment(**segment) for segment in wind.segments],
    )
