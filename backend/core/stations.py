"""Weather Underground personal weather stations, used to correct near-term forecasts.

Stations only measure the present, so they can only improve the first hours of a ride:
the difference between what a station reads now and what the model says for now is added
to the model at each sample's eta, with a weight that fades to zero as the eta moves away
from the observation time. A ride tomorrow gets no station data and costs no calls.

What is corrected, and why only that:
  * temperature -- stations measure it well and a model bias persists for hours.
  * rain *presence* -- "it is raining at the stations now" moves the probability for the
    next hour. The model's rain amount is left alone.
  * wind is not touched: PWS anemometers sit low and sheltered, far from the 10 m the
    model reports, so their offsets would only drag the headwind down.

The free PWS-owner key allows 1500 calls a day and 30 a minute, and a key that keeps going
over can be switched off. So every call goes through ``_spend_call``: station lookups are
cached for weeks, observations for minutes, a job reads at most ``MAX_STATIONS_PER_JOB``
stations, and only tasks fetch. ``get_cached_readings`` is the only accessor the forecast
assembly and the thumbnails may use.
"""

import os
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any

import httpx
from asgiref.sync import sync_to_async
from loguru import logger

from . import telemetry
from .geo import haversine_m
from .models import StationLookup, StationObservation
from .ratelimit import Limit, acquire, describe_failure, record_throttle
from .schedule import LOCAL_TZ

WU_NEAR_URL = "https://api.weather.com/v3/location/near"
WU_CURRENT_URL = "https://api.weather.com/v2/pws/observations/current"

# How far from the observation time a correction still has any weight.
STATION_HORIZON = timedelta(hours=2)
RAIN_HORIZON = timedelta(hours=1)

STATION_RADIUS_M = 5000.0
MIN_STATIONS = 2  # one station alone is too easily a badly placed one
MAX_STATIONS_PER_JOB = 6
MAX_LOOKUPS_PER_JOB = 12
LOOKUP_ROUND = 20  # lookup cells of 1/20 degree (~4-5 km), so the 10 nearest stations cover them

OBSERVATION_TTL = timedelta(minutes=10)
LOOKUP_TTL = timedelta(days=14)
MAX_OBS_AGE = timedelta(minutes=30)  # older readings are not "now" any more
STALE_STATION = timedelta(days=1)  # the lookup lists stations that stopped reporting years ago

TEMP_OUTLIER_C = 3.0  # a sensor in the sun reads several degrees high
MAX_TEMP_OFFSET_C = 5.0
WET_RATE_MM_H = 0.1

# The key allows 1500 a day and 30 a minute; stay under both with some margin.
DAILY_CAP = 1400
MINUTE_CAP = 25
BLOCKED_TTL = 900  # the first pause after a 429; ``core.ratelimit`` doubles it on the next


def api_key() -> str | None:
    return os.environ.get("WEATHERUNDERGROUND_API_KEY") or None


def as_aware(value: datetime) -> datetime:
    """Departure times without a zone are Swiss local wall time, like the provider data."""
    return value if value.tzinfo is not None else value.replace(tzinfo=LOCAL_TZ)


# =============================================================================
# Call budget
# =============================================================================


def _limit() -> Limit:
    # Built per call so the caps stay patchable in tests.
    return Limit(
        "weather-underground", ((60, MINUTE_CAP), (86400, DAILY_CAP)), fail_open=False, base_cooldown=BLOCKED_TTL
    )


def _spend_call() -> bool:
    """Count one Weather Underground call, or refuse it when a cap is reached or a 429 is recent.

    Fails *closed*, unlike ``claims.py``: without the cache there is no counter, and going
    over the key's limit can get it switched off. Skipping a call only costs the
    correction, never the forecast.
    """
    return not acquire(_limit())


# =============================================================================
# API fetch + parsing
# =============================================================================


async def _get_json(url: str, params: dict) -> dict | None:
    """One budgeted GET. ``{}`` for 204 (nothing there), ``None`` when no call was made or it failed."""
    key = api_key()
    if not key:
        return None
    if not await sync_to_async(_spend_call)():
        logger.info("Weather Underground call skipped: budget reached")
        return None
    started, outcome = perf_counter(), "success"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params={**params, "format": "json", "apiKey": key}, timeout=15)
        if resp.status_code == 204:
            return {}
        if resp.status_code == 429:
            outcome = "rate_limited"
            await sync_to_async(record_throttle)(_limit(), resp)
            return None
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            outcome = "invalid_response"
    except (httpx.HTTPError, ValueError) as exc:
        outcome = telemetry.error_outcome(exc)
        logger.warning(f"Weather Underground request failed: {describe_failure(exc)}")
        return None
    finally:
        telemetry.emit("count", "provider.request", provider="weather-underground", outcome=outcome)
        telemetry.emit(
            "distribution",
            "provider.duration",
            perf_counter() - started,
            unit="second",
            provider="weather-underground",
            outcome=outcome,
        )
    return data if isinstance(data, dict) else None


async def _fetch_nearby(lat: float, lon: float) -> list[dict] | None:
    """The up to 10 stations nearest a point, or None if no answer was had."""
    data = await _get_json(WU_NEAR_URL, {"geocode": f"{lat},{lon}", "product": "pws"})
    return None if data is None else parse_nearby(data)


async def _fetch_observation(station_id: str) -> dict | None:
    data = await _get_json(
        WU_CURRENT_URL,
        {"stationId": station_id, "units": "m", "numericPrecision": "decimal"},
    )
    return None if data is None else parse_observation(data)


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def parse_nearby(data: dict) -> list[dict]:
    """Turn the parallel arrays of ``/v3/location/near`` into station dicts.

    A station missing an id or coordinates is dropped, never filled in.
    """
    location = data.get("location") if isinstance(data, dict) else None
    if not isinstance(location, dict):
        return []
    ids = location.get("stationId") or []
    columns = {name: location.get(name) or [] for name in ("latitude", "longitude", "qcStatus", "updateTimeUtc")}

    def column(name: str, i: int):
        values = columns[name]
        return values[i] if i < len(values) else None

    stations = []
    for i, station_id in enumerate(ids):
        lat, lon = _number(column("latitude", i)), _number(column("longitude", i))
        if not isinstance(station_id, str) or not station_id or lat is None or lon is None:
            continue
        qc = column("qcStatus", i)
        updated = _number(column("updateTimeUtc", i))
        stations.append(
            {
                "id": station_id,
                "lat": lat,
                "lon": lon,
                "qc": int(qc) if isinstance(qc, int) else None,
                "updated": int(updated) if updated is not None else None,
            }
        )
    return stations


def parse_observation(data: dict) -> dict | None:
    """The one observation of ``/v2/pws/observations/current``, or None when it is unusable.

    Temperature and rain rate stay None when missing -- a missing reading must not turn
    into 0 °C or "dry".
    """
    observations = data.get("observations") if isinstance(data, dict) else None
    if not isinstance(observations, list) or not observations or not isinstance(observations[0], dict):
        return None
    obs = observations[0]
    epoch, lat, lon = _number(obs.get("epoch")), _number(obs.get("lat")), _number(obs.get("lon"))
    station_id = obs.get("stationID")
    metric = obs.get("metric") if isinstance(obs.get("metric"), dict) else {}
    if epoch is None or lat is None or lon is None or not isinstance(station_id, str):
        return None
    temp, precip_rate = _number(metric.get("temp")), _number(metric.get("precipRate"))
    if temp is None and precip_rate is None:
        return None
    qc = obs.get("qcStatus")
    return {
        "station_id": station_id,
        "lat": lat,
        "lon": lon,
        "observed_at": datetime.fromtimestamp(epoch, tz=UTC),
        "temp": temp,
        "precip_rate": precip_rate,
        "qc_status": int(qc) if isinstance(qc, int) else None,
    }


# =============================================================================
# Which samples and stations a ride needs
# =============================================================================


def ride_in_window(departure: datetime, total_seconds: float | None, now: datetime) -> bool:
    """True when some part of the ride lies within ``STATION_HORIZON`` of now."""
    start = as_aware(departure)
    end = start + timedelta(seconds=total_seconds or 0)
    return start <= now + STATION_HORIZON and end >= now - STATION_HORIZON


def _samples_in_window(sample_points: list[dict], departure: datetime, now: datetime) -> list[tuple[int, dict]]:
    start = as_aware(departure)
    return [
        (i, sp)
        for i, sp in enumerate(sample_points)
        if abs(start + timedelta(seconds=sp["elapsed_s"]) - now) < STATION_HORIZON
    ]


def _lookup_cell(lat: float, lon: float) -> tuple[float, float]:
    return round(lat * LOOKUP_ROUND) / LOOKUP_ROUND, round(lon * LOOKUP_ROUND) / LOOKUP_ROUND


def _usable_station(station: dict, now: datetime) -> bool:
    if station.get("qc") == 0:
        return False
    updated: Any = station.get("updated")
    if updated is None:
        return True  # No timestamp at all is not a reason to discard the station.
    return now - datetime.fromtimestamp(updated, tz=UTC) <= STALE_STATION


def pick_stations(
    points: list[tuple[float, float]], candidates: list[dict], limit: int = MAX_STATIONS_PER_JOB
) -> list[str]:
    """Choose up to ``limit`` stations so that as many points as possible get ``MIN_STATIONS``.

    Points are ride samples in eta order, so the earliest part of the ride -- where the
    correction weighs most -- is served first. Each round gives every point still short
    of stations its nearest unpicked one in range; a station near several points counts
    for all of them.
    """
    by_id = {c["id"]: c for c in candidates}
    picked: list[str] = []

    def distance(point, station_id):
        station = by_id[station_id]
        return haversine_m(point[1], point[0], station["lon"], station["lat"])

    while len(picked) < limit:
        progress = False
        for point in points:
            if len(picked) >= limit:
                break
            if sum(1 for s in picked if distance(point, s) <= STATION_RADIUS_M) >= MIN_STATIONS:
                continue
            in_range = [s for s in by_id if s not in picked and distance(point, s) <= STATION_RADIUS_M]
            if in_range:
                picked.append(min(in_range, key=lambda s: distance(point, s)))
                progress = True
        if not progress:
            break
    return picked


# =============================================================================
# DB helpers
# =============================================================================


def _fresh_lookup_sync(lat_c: float, lon_c: float) -> list[dict] | None:
    lookup = StationLookup.objects.filter(lat_c=lat_c, lon_c=lon_c).first()
    if lookup is None or datetime.now(tz=UTC) - lookup.fetched_at > LOOKUP_TTL:
        return None
    return lookup.stations


def _store_lookup_sync(lat_c: float, lon_c: float, stations: list[dict]) -> None:
    StationLookup.objects.update_or_create(lat_c=lat_c, lon_c=lon_c, defaults={"stations": stations})


def _fresh_observation_ids_sync(station_ids: list[str]) -> set[str]:
    cutoff = datetime.now(tz=UTC) - OBSERVATION_TTL
    return set(
        StationObservation.objects.filter(station_id__in=station_ids, fetched_at__gte=cutoff).values_list(
            "station_id", flat=True
        )
    )


def _store_observation_sync(obs: dict) -> None:
    StationObservation.objects.update_or_create(
        station_id=obs["station_id"],
        defaults={key: obs[key] for key in ("lat", "lon", "observed_at", "temp", "precip_rate", "qc_status")},
    )


def purge_station_data() -> int:
    """Drop readings and lookups nobody will read again."""
    now = datetime.now(tz=UTC)
    observations = StationObservation.objects.filter(observed_at__lt=now - timedelta(days=1)).delete()[0]
    lookups = StationLookup.objects.filter(fetched_at__lt=now - 2 * LOOKUP_TTL).delete()[0]
    return observations + lookups


def _recent_observations_sync(points: list[tuple[float, float]], now: datetime) -> list[StationObservation]:
    margin = STATION_RADIUS_M / 111_000 * 2  # degrees; generous so the longitude span is covered too
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return list(
        StationObservation.objects.filter(
            observed_at__gte=now - MAX_OBS_AGE,
            lat__gte=min(lats) - margin,
            lat__lte=max(lats) + margin,
            lon__gte=min(lons) - margin,
            lon__lte=max(lons) + margin,
        ).exclude(qc_status=0)
    )


# =============================================================================
# Fetching (tasks only)
# =============================================================================


async def refresh_stations_for_ride(sample_points: list[dict], departure: datetime, now: datetime) -> int:
    """Make sure the stations near the in-window part of a ride have a recent reading.

    Spends Weather Underground calls, so only the ``refresh_station_observations`` task may
    call it. Returns how many stations now have a fresh observation.
    """
    window = _samples_in_window(sample_points, departure, now)
    if not window or not api_key():
        return 0
    points = [(sp["lat"], sp["lon"]) for _, sp in window]

    cells = list(dict.fromkeys(_lookup_cell(lat, lon) for lat, lon in points))[:MAX_LOOKUPS_PER_JOB]
    candidates: dict[str, dict] = {}
    for lat_c, lon_c in cells:
        stations = await sync_to_async(_fresh_lookup_sync)(lat_c, lon_c)
        if stations is None:
            stations = await _fetch_nearby(lat_c, lon_c)
            if stations is None:
                continue  # no call made or it failed; try again next time
            await sync_to_async(_store_lookup_sync)(lat_c, lon_c, stations)
        for station in stations:
            if _usable_station(station, now):
                candidates.setdefault(station["id"], station)

    picked = pick_stations(points, list(candidates.values()))
    fresh = await sync_to_async(_fresh_observation_ids_sync)(picked)
    for station_id in picked:
        if station_id in fresh:
            continue
        obs = await _fetch_observation(station_id)
        if obs is None:
            continue
        await sync_to_async(_store_observation_sync)(obs)
        fresh.add(station_id)
    logger.debug(f"refresh_stations_for_ride: {len(cells)} lookups, {len(picked)} stations, {len(fresh)} fresh")
    return len(fresh)


# =============================================================================
# Reading (assembly and thumbnails) -- never fetches
# =============================================================================


@dataclass(frozen=True)
class Reading:
    station_id: str
    observed_at: datetime
    temp: float | None
    precip_rate: float | None


async def get_cached_readings(sample_points: list[dict], departure: datetime, now: datetime) -> list[list[Reading]]:
    """For each sample point, the recent readings of stations within range. Cache only.

    Samples outside the correction window get an empty list.
    """
    readings: list[list[Reading]] = [[] for _ in sample_points]
    window = _samples_in_window(sample_points, departure, now)
    if not window:
        return readings
    observations = await sync_to_async(_recent_observations_sync)([(sp["lat"], sp["lon"]) for _, sp in window], now)
    for i, sp in window:
        readings[i] = [
            Reading(obs.station_id, obs.observed_at, obs.temp, obs.precip_rate)
            for obs in observations
            if haversine_m(sp["lon"], sp["lat"], obs.lon, obs.lat) <= STATION_RADIUS_M
        ]
    return readings


# =============================================================================
# The correction itself (pure)
# =============================================================================


@dataclass(frozen=True)
class StationCorrection:
    observed_at: datetime
    station_count: int
    temp_offset: float | None  # station median minus model, at observed_at
    wet_share: float | None  # share of stations measuring rain now
    wet_rate_mm_h: float | None  # median rate of the wet stations


def lead_weight(eta: datetime, observed_at: datetime, horizon: timedelta) -> float:
    """1 at the observation time, falling linearly to 0 at ``horizon`` either side."""
    lead = abs(as_aware(eta) - observed_at)
    return max(0.0, 1.0 - lead / horizon)


def station_correction(readings: list[Reading], model_temp_now: float | None) -> StationCorrection | None:
    """Summarise one sample's readings, or None when there are too few to trust."""
    if not readings:
        return None
    observed_at = min(r.observed_at for r in readings)

    temp_offset = None
    temps = [r.temp for r in readings if r.temp is not None]
    if len(temps) >= MIN_STATIONS and model_temp_now is not None:
        median = statistics.median(temps)
        kept = [t for t in temps if abs(t - median) <= TEMP_OUTLIER_C]
        if len(kept) >= MIN_STATIONS:
            offset = statistics.median(kept) - model_temp_now
            temp_offset = max(-MAX_TEMP_OFFSET_C, min(MAX_TEMP_OFFSET_C, offset))

    wet_share = wet_rate = None
    rates = [r.precip_rate for r in readings if r.precip_rate is not None]
    if len(rates) >= MIN_STATIONS:
        wet = [rate for rate in rates if rate >= WET_RATE_MM_H]
        wet_share = len(wet) / len(rates)
        wet_rate = statistics.median(wet) if wet else None

    if temp_offset is None and wet_share is None:
        return None
    return StationCorrection(observed_at, len(readings), temp_offset, wet_share, wet_rate)
