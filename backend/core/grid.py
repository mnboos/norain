"""Grid-based forecast storage and retrieval.

Each ~1 km² cell (lat/lon rounded to 2 decimal places) stores the raw API response
so multiple routes passing through the same cell share the same forecast data.

Also contains the external weather API fetch functions (moved here from weather.py
to avoid circular imports between weather.py <-> grid.py).
"""

import os
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from asgiref.sync import sync_to_async
from loguru import logger

from .models import EnsembleCell, ForecastCell
from .uncertainty import ENSEMBLE_VARIABLES
from .wind import finite_number

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OWM_ONECALL_URL = "https://api.openweathermap.org/data/3.0/onecall"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"

_OM_VARS = {
    "precipitation",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "temperature_2m",
    "weather_code",
    "relative_humidity_2m",
    "dew_point_2m",
    "apparent_temperature",
    "precipitation_probability",
}

# Cells older than this are considered stale and should be refreshed.
MAX_CELL_AGE = timedelta(hours=2)

ENSEMBLE_MODELS = "icon_seamless_eps,meteoswiss_icon_ch1_ensemble,meteoswiss_icon_ch2_ensemble"
POP_MEMBER_MM = 0.1
ENSEMBLE_REQUEST_VERSION = 2


# =============================================================================
# External weather API fetch functions (moved from weather.py)
# =============================================================================


async def _fetch_ensemble(lat_r: float, lon_r: float, forecast_days: int, day_key: str) -> dict:
    """Open-Meteo ensemble: many members across several models, hourly weather variables."""
    params = {
        "latitude": lat_r,
        "longitude": lon_r,
        "hourly": ",".join(ENSEMBLE_VARIABLES),
        "wind_speed_unit": "kmh",
        "models": ENSEMBLE_MODELS,
        "timezone": "Europe/Zurich",
        "forecast_days": forecast_days,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(ENSEMBLE_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        data["_norain_request_version"] = ENSEMBLE_REQUEST_VERSION
        return data


async def _fetch_open_meteo(lat_r: float, lon_r: float, forecast_days: int, day_key: str) -> dict:
    features = ",".join(_OM_VARS)
    params = {
        "latitude": lat_r,
        "longitude": lon_r,
        "minutely_15": features,
        "hourly": features,
        "wind_speed_unit": "kmh",
        "timezone": "Europe/Zurich",
        "forecast_days": forecast_days,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(OPEN_METEO_URL, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()


async def _fetch_owm(lat_r: float, lon_r: float) -> dict | None:
    """OpenWeatherMap One Call 3.0 hourly fallback. Returns None if no key configured."""
    key = os.environ.get("OPENWEATHERMAP_API_KEY")
    if not key:
        logger.warning("No OpenWeatherMap API key configured")
        return None
    params = {
        "lat": lat_r,
        "lon": lon_r,
        "units": "metric",
        "exclude": "current,minutely,daily,alerts",
        "appid": key,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(OWM_ONECALL_URL, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()


# =============================================================================
# Forecast extraction from API responses (moved from weather.py)
# =============================================================================


def _nearest_index(times: list[str], eta: datetime) -> int:
    """Index of the time string (naive local ISO) closest to eta."""
    best_i, best_d = 0, None
    for i, ts in enumerate(times):
        d = abs((datetime.fromisoformat(ts) - eta).total_seconds())
        if best_d is None or d < best_d:
            best_i, best_d = i, d
    return best_i


def _at(block: dict, var: str, i: int) -> float:
    arr = block.get(var) or []
    return float(arr[i]) if i < len(arr) and arr[i] is not None else 0.0


def _wind_value(value, *, direction=False, factor=1.0) -> float | None:
    number = finite_number(value, nonnegative=not direction)
    return None if number is None else number % 360 if direction else number * factor


def _wind_at(block: dict, var: str, i: int, *, direction=False) -> float | None:
    values = block.get(var)
    value = values[i] if isinstance(values, list) and i < len(values) else None
    return _wind_value(value, direction=direction)


def _from_open_meteo(data: dict, eta: datetime) -> dict | None:
    """Pull the forecast nearest eta, preferring 15-min data, falling back to hourly.

    If *eta* falls outside the high-resolution ``minutely_15`` window the function
    falls through to ``hourly``.  The hourly block covers the full forecast horizon
    so out-of-range etas are *clamped* to the nearest available hour rather than
    returning ``None`` — better to show slightly-off data than no data at all.
    """
    # Provider timestamps are Swiss local wall time. Saved routes can supply aware ETAs.
    if eta.tzinfo is not None:
        eta = eta.astimezone(ZoneInfo("Europe/Zurich")).replace(tzinfo=None)
    # minutely_15 covers ~1–2 h into the future; only use when the nearest match
    # is within 2 h, otherwise fall through to hourly.
    MINUTELY_15_MAX_DELTA = timedelta(hours=2)

    for block in ("minutely_15", "hourly"):
        b = data.get(block)
        if not isinstance(b, dict) or not b.get("time"):
            continue
        times = b["time"]
        i = _nearest_index(times, eta)
        matched = datetime.fromisoformat(times[i])
        delta = abs(eta - matched)

        # Reject minutely_15 when the best match is too far — use hourly instead.
        if block == "minutely_15" and delta > MINUTELY_15_MAX_DELTA:
            continue

        if delta > timedelta(hours=1):
            logger.debug(
                "_from_open_meteo: clamping eta={} to {} ({:+d} min) in {} block",
                eta.isoformat(),
                times[i],
                round((eta - matched).total_seconds() / 60),
                block,
            )

        code = (b.get("weather_code") or [None])[i] if b.get("weather_code") else None
        rain = b.get("precipitation") or b.get("rain") or []
        return {
            "rain_mm": float(rain[i]) if i < len(rain) and rain[i] is not None else 0.0,
            "temp": _at(b, "temperature_2m", i),
            "wind_speed": _wind_at(b, "wind_speed_10m", i),
            "wind_gust": _wind_at(b, "wind_gusts_10m", i),
            "precipitation_interval_s": 900 if block == "minutely_15" else 3600,
            "wind_dir": _wind_at(b, "wind_direction_10m", i, direction=True),
            "weather_code": int(code) if code is not None else None,
            "pop": None,
            "source": "open-meteo",
        }
    return None


def _from_owm(data: dict, eta: datetime) -> dict | None:
    """Extract a forecast sample from OWM One Call 3.0 hourly data.

    OWM hourly is a list of objects, each with ``dt``, ``temp``, ``wind_speed``, etc.
    Rejects Open-Meteo dict-style hourly (parallel arrays) to avoid confusing it
    with OWM data — the two formats have fundamentally different structures.
    """
    hourly = data.get("hourly")
    if not isinstance(hourly, list) or not hourly:
        return None
    if not isinstance(hourly[0], dict):
        logger.warning(
            "OWM parser: first hourly entry is not a dict (type={}), skipping",
            type(hourly[0]).__name__,
        )
        return None
    eta_ts = eta.timestamp()
    entry = min(hourly, key=lambda h: abs(h.get("dt", 0) - eta_ts))
    # OWM One Call 3.0 returns rain as a float (precipitation volume in mm);
    # legacy OWM 2.5 and some wrappers still use {"1h": value} — accept both.
    raw_rain = entry.get("rain")
    if isinstance(raw_rain, dict):
        rain = float(raw_rain.get("1h", 0.0))
    elif isinstance(raw_rain, (int, float)):
        rain = float(raw_rain)
    else:
        rain = 0.0
    return {
        "rain_mm": rain,
        "precipitation_interval_s": 3600,
        "temp": float(entry.get("temp", 0.0)),
        "wind_speed": _wind_value(entry.get("wind_speed"), factor=3.6),
        "wind_gust": _wind_value(entry.get("wind_gust"), factor=3.6),
        "wind_dir": _wind_value(entry.get("wind_deg"), direction=True),
        "weather_code": None,
        "pop": float(entry.get("pop")) if entry.get("pop") is not None else None,
        "source": "openweathermap",
    }


def _ensemble_at(data: dict, eta: datetime) -> tuple[float, float] | None:
    """(pop, rain_if_wet) at eta's hour, or None if out of range / no data."""
    h = data.get("hourly") or {}
    times = h.get("time") or []
    if not times:
        return None
    if eta < datetime.fromisoformat(times[0]) - timedelta(hours=1) or eta > datetime.fromisoformat(
        times[-1]
    ) + timedelta(hours=1):
        return None
    i = _nearest_index(times, eta)
    total = 0
    wet_vals: list[float] = []
    for key, series in h.items():
        if not key.startswith("precipitation"):
            continue
        if i < len(series) and series[i] is not None:
            total += 1
            if series[i] >= POP_MEMBER_MM:
                wet_vals.append(series[i])
    if not total:
        return None
    pop = len(wet_vals) / total
    rain_if_wet = sum(wet_vals) / len(wet_vals) if wet_vals else 0.0
    return pop, rain_if_wet


# =============================================================================
# DB sync helpers
# =============================================================================


def _get_forecast_cell_sync(
    lat_r: float,
    lon_r: float,
    day_key: date,
    source: str,
    forecast_days: int | None = None,
) -> ForecastCell | None:
    """Return a fresh ForecastCell from the DB, or None if missing/stale/inadequate.

    When *forecast_days* is provided, rejects cells that were fetched with a
    shorter horizon — their time series won't cover an eta that far out.
    """
    try:
        cell = ForecastCell.objects.get(lat_r=lat_r, lon_r=lon_r, day_key=day_key, source=source)
    except ForecastCell.DoesNotExist:
        return None
    if datetime.now(tz=UTC) - cell.fetched_at > MAX_CELL_AGE:
        return None
    if forecast_days is not None and cell.forecast_days < forecast_days:
        return None
    return cell


def _get_ensemble_cell_sync(
    lat_r: float,
    lon_r: float,
    day_key: date,
    forecast_days: int | None = None,
) -> EnsembleCell | None:
    """Return a fresh EnsembleCell from the DB, or None if missing/stale/inadequate."""
    try:
        cell = EnsembleCell.objects.get(lat_r=lat_r, lon_r=lon_r, day_key=day_key)
    except EnsembleCell.DoesNotExist:
        return None
    if cell.data.get("_norain_request_version") != ENSEMBLE_REQUEST_VERSION:
        return None
    if datetime.now(tz=UTC) - cell.fetched_at > MAX_CELL_AGE:
        return None
    if forecast_days is not None and cell.forecast_days < forecast_days:
        return None
    return cell


def _store_forecast_cell_sync(
    lat_r: float, lon_r: float, day_key: date, forecast_days: int, data: dict, source: str
) -> ForecastCell:
    """Insert or update a ForecastCell with fresh data."""
    return ForecastCell.objects.update_or_create(
        lat_r=lat_r,
        lon_r=lon_r,
        day_key=day_key,
        source=source,
        defaults={"forecast_days": forecast_days, "data": data},
    )[0]


def _store_ensemble_cell_sync(
    lat_r: float, lon_r: float, day_key: date, forecast_days: int, data: dict
) -> EnsembleCell:
    """Insert or update an EnsembleCell with fresh data."""
    return EnsembleCell.objects.update_or_create(
        lat_r=lat_r,
        lon_r=lon_r,
        day_key=day_key,
        defaults={"forecast_days": forecast_days, "data": data},
    )[0]


# =============================================================================
# Public async API
# =============================================================================


async def get_cached_forecast_cell(
    lat_r: float, lon_r: float, day_key: str | date, forecast_days: int
) -> ForecastCell | None:
    """Return a fresh ForecastCell already in the DB, without ever fetching.

    Checks both sources, because a cell laid down by the OWM fallback is just as usable
    as an Open-Meteo one — looking up a single source would report a miss for data we hold.
    Callers that must not spend an API request (the route-list thumbnails) use this.
    """
    if isinstance(day_key, str):
        day_key = date.fromisoformat(day_key)

    for source in ("open-meteo", "openweathermap"):
        cell = await sync_to_async(_get_forecast_cell_sync)(lat_r, lon_r, day_key, source, forecast_days)
        if cell is not None:
            return cell
    return None


async def get_or_fetch_forecast_cell(
    lat_r: float, lon_r: float, day_key: str | date, forecast_days: int
) -> ForecastCell | None:
    """Return a fresh ForecastCell from the DB, or fetch + store and return.

    Tries Open-Meteo first, falls back to OWM. Returns None if both fail.
    """
    if isinstance(day_key, str):
        day_key = date.fromisoformat(day_key)

    cell = await get_cached_forecast_cell(lat_r, lon_r, day_key, forecast_days)
    if cell is not None:
        return cell

    # Fetch fresh data
    data = None
    source = "open-meteo"
    try:
        data = await _fetch_open_meteo(lat_r, lon_r, forecast_days, day_key.isoformat())
    except (httpx.HTTPError, KeyError, ValueError):
        pass

    if data is None:
        source = "openweathermap"
        try:
            data = await _fetch_owm(lat_r, lon_r)
        except (httpx.HTTPError, KeyError, ValueError):
            pass

    if data is None:
        logger.warning(
            "get_or_fetch_forecast_cell: both Open-Meteo and OWM failed for ({}, {}), day_key={}, forecast_days={}",
            lat_r,
            lon_r,
            day_key,
            forecast_days,
        )
        return None

    cell = await sync_to_async(_store_forecast_cell_sync)(lat_r, lon_r, day_key, forecast_days, data, source)
    return cell


async def get_cached_ensemble_cell(
    lat_r: float, lon_r: float, day_key: str | date, forecast_days: int
) -> EnsembleCell | None:
    """Return a fresh EnsembleCell already in the DB, without ever fetching."""
    if isinstance(day_key, str):
        day_key = date.fromisoformat(day_key)
    return await sync_to_async(_get_ensemble_cell_sync)(lat_r, lon_r, day_key, forecast_days)


async def get_or_fetch_ensemble_cell(
    lat_r: float, lon_r: float, day_key: str | date, forecast_days: int
) -> EnsembleCell | None:
    """Return a fresh EnsembleCell from the DB, or fetch + store and return."""
    if isinstance(day_key, str):
        day_key = date.fromisoformat(day_key)

    cell = await get_cached_ensemble_cell(lat_r, lon_r, day_key, forecast_days)
    if cell is not None:
        return cell

    data = None
    try:
        data = await _fetch_ensemble(lat_r, lon_r, forecast_days, day_key.isoformat())
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        # Logged, unlike before: a swallowed failure here stores no cell, so the task that
        # called it still reports success while having fetched nothing. Rate limiting is
        # the usual cause and it is invisible without this.
        logger.warning(
            "get_or_fetch_ensemble_cell: ensemble fetch failed for ({}, {}), day_key={}, forecast_days={}: {}",
            lat_r,
            lon_r,
            day_key,
            forecast_days,
            exc,
        )

    if data is None:
        return None

    cell = await sync_to_async(_store_ensemble_cell_sync)(lat_r, lon_r, day_key, forecast_days, data)
    return cell


def extract_sample(cell_data: dict, eta: datetime, source: str | None = None) -> dict | None:
    """Extract the weather sample matching `eta` from a stored cell's JSON data.

    Args:
        cell_data: Raw API response dict (Open-Meteo parallel arrays or OWM list of objects).
        eta: The datetime to look up.
        source: The forecast source (``"open-meteo"`` or ``"openweathermap"``). When provided,
            routes directly to the correct parser, avoiding the guesswork that caused the
            OWM parser to receive Open-Meteo dict-style ``hourly`` data. When ``None``,
            falls back to the heuristic (try Open-Meteo first, then OWM).
    """
    if source == "openweathermap":
        return _from_owm(cell_data, eta)
    if source == "open-meteo":
        return _from_open_meteo(cell_data, eta)
    # Fallback heuristic (backward compatible)
    result = _from_open_meteo(cell_data, eta)
    if result is not None:
        return result
    return _from_owm(cell_data, eta) if cell_data.get("hourly") else None


def extract_ensemble(cell_data: dict, eta: datetime) -> tuple[float, float] | None:
    """Extract (pop, rain_if_wet) for `eta` from a stored ensemble cell's JSON data."""
    return _ensemble_at(cell_data, eta)
