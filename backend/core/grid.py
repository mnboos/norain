"""Grid-based forecast storage and retrieval.

Each ~1 km² cell (lat/lon rounded to 2 decimal places) stores the raw API response
so multiple routes passing through the same cell share the same forecast data.

Also contains the external weather API fetch functions (moved here from weather.py
to avoid circular imports between weather.py <-> grid.py).
"""

import os
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from itertools import batched
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from asgiref.sync import sync_to_async
from django.db.models import Q
from loguru import logger

from .cell_lease import fetch_lease
from .models import EnsembleCell, ForecastCell
from .ratelimit import Limit, ProviderThrottled, acquire, cooldown_left, record_throttle
from .ratelimit import describe_failure as _failure
from .telemetry import emit, provider
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
# minutely_15 covers ~1-2 h into the future; only use it when the nearest match is
# within 2 h, otherwise fall through to hourly.
MINUTELY_15_MAX_DELTA = timedelta(hours=2)

ENSEMBLE_MODELS = "icon_seamless_eps,meteoswiss_icon_ch1_ensemble,meteoswiss_icon_ch2_ensemble"
POP_MEMBER_MM = 0.1
ENSEMBLE_REQUEST_VERSION = 2
CELL_LOOKUP_BATCH_SIZE = 500

# Open-Meteo's free limits (open-meteo.com/en/pricing), in its own weighted calls. The free
# API is for non-commercial use; with OPEN_METEO_API_KEY the requests go to the customer
# hosts instead, and the OPEN_METEO_LIMIT_* variables should then carry the plan's limits.
OPEN_METEO_LIMITS = {"MINUTE": (60, 600), "HOUR": (3600, 5000), "DAY": (86400, 10000), "MONTH": (30 * 86400, 300000)}
# Headroom for requests we weigh too low and for the windows not lining up with Open-Meteo's.
OPEN_METEO_MARGIN = 0.8
# One Call 3.0 is free for 1000 calls a day and billed beyond that.
OWM_DAILY_CAP = 900

type CellKey = tuple[float, float, date]


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        logger.warning(f"{name} is not a number, using {default}")
        return default


def open_meteo_limit() -> Limit:
    """One budget for the forecast and the ensemble API: Open-Meteo counts per client."""
    return Limit(
        "open-meteo",
        tuple(
            (seconds, _env_number(f"OPEN_METEO_LIMIT_{name}", calls) * OPEN_METEO_MARGIN)
            for name, (seconds, calls) in OPEN_METEO_LIMITS.items()
        ),
        fail_open=True,
    )


def owm_limit() -> Limit:
    return Limit("openweathermap", ((86400, _env_number("OPENWEATHERMAP_DAILY_CAP", OWM_DAILY_CAP)),), fail_open=False)


def open_meteo_weight(variables: int, forecast_days: int, models: int = 1) -> float:
    """How many calls Open-Meteo counts one request as: more than 10 variables or 14 days is more than one.

    The ensemble API may count its members too; that is not documented. A weight that is too
    low shows up as 429s, and ``core.ratelimit`` then scales the budget down by itself.
    """
    return max(1.0, variables * models / 10) * max(1.0, forecast_days / 14)


FORECAST_VARIABLES = len(_OM_VARS) * 2  # minutely_15 and hourly both count
ENSEMBLE_MODEL_COUNT = len(ENSEMBLE_MODELS.split(","))


def _open_meteo_request(url: str, params: dict) -> tuple[str, dict]:
    """The customer host and key when a commercial key is configured, else the free host."""
    if key := os.environ.get("OPEN_METEO_API_KEY"):
        return url.replace("://", "://customer-", 1), {**params, "apikey": key}
    return url, params


def _throttle_wait(limit: Limit, exc: Exception) -> float:
    """The cooldown a failed call starts: only a 429 starts one."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        return record_throttle(limit, exc.response)
    return 0.0


# =============================================================================
# External weather API fetch functions (moved from weather.py)
# =============================================================================


@provider("open-meteo-ensemble")
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
    url, params = _open_meteo_request(ENSEMBLE_URL, params)
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        data["_norain_request_version"] = ENSEMBLE_REQUEST_VERSION
        return data


@provider("open-meteo")
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
    url, params = _open_meteo_request(OPEN_METEO_URL, params)
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()


@provider("openweathermap", optional_key="OPENWEATHERMAP_API_KEY")
async def _fetch_owm(lat_r: float, lon_r: float) -> dict | None:
    """OpenWeatherMap One Call 3.0 hourly fallback. Returns None if no key configured."""
    key = os.environ.get("OPENWEATHERMAP_API_KEY")
    if not key:
        emit("count", "weather.fallback", outcome="unconfigured")
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

        code: Any = (b.get("weather_code") or [None])[i] if b.get("weather_code") else None
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


def _find_forecast_cell_sync(
    lat_r: float, lon_r: float, day_key: date, forecast_days: int | None = None
) -> ForecastCell | None:
    """A fresh cell from either source: an OWM fallback cell is as usable as an Open-Meteo one."""
    for source in ("open-meteo", "openweathermap"):
        cell = _get_forecast_cell_sync(lat_r, lon_r, day_key, source, forecast_days)
        if cell is not None:
            return cell
    return None


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


def _cached_cell_keys_sync(
    cells: list[tuple[float, float]], windows: list[tuple[str | date, int]]
) -> tuple[set[CellKey], set[CellKey]]:
    """Read availability metadata only, with two queries per batch of exact cell keys."""
    requirements: dict[CellKey, int] = {}
    for day_key, days in windows:
        day = date.fromisoformat(day_key) if isinstance(day_key, str) else day_key
        for lat_r, lon_r in cells:
            key = (lat_r, lon_r, day)
            requirements[key] = max(requirements.get(key, days), days)

    cutoff = datetime.now(tz=UTC) - MAX_CELL_AGE
    forecasts: set[CellKey] = set()
    ensembles: set[CellKey] = set()
    for batch in batched(requirements.items(), CELL_LOOKUP_BATCH_SIZE, strict=False):
        requested = Q(
            *(Q(lat_r=lat, lon_r=lon, day_key=day, forecast_days__gte=days) for (lat, lon, day), days in batch),
            _connector=Q.OR,
        )
        sources: dict[CellKey, str] = {}
        for lat, lon, day, source in ForecastCell.objects.filter(
            requested, fetched_at__gte=cutoff, source__in=("open-meteo", "openweathermap")
        ).values_list("lat_r", "lon_r", "day_key", "source"):
            key = (lat, lon, day)
            if key not in sources or source == "open-meteo":
                sources[key] = source
        forecasts.update(sources)
        warm_ensembles = set(
            EnsembleCell.objects.filter(
                requested, fetched_at__gte=cutoff, data___norain_request_version=ENSEMBLE_REQUEST_VERSION
            ).values_list("lat_r", "lon_r", "day_key")
        )
        ensembles.update(warm_ensembles)
        for key, _ in batch:
            if source := sources.get(key):
                emit("count", "cache.lookup", kind="forecast", outcome="hit", source=source)
            else:
                emit("count", "cache.lookup", kind="forecast", outcome="miss")
            emit("count", "cache.lookup", kind="ensemble", outcome="hit" if key in warm_ensembles else "miss")
    return forecasts, ensembles


async def get_cached_cell_keys(
    cells: list[tuple[float, float]], windows: list[tuple[str | date, int]]
) -> tuple[set[CellKey], set[CellKey]]:
    """Return fresh deterministic/ensemble keys without fetching or loading weather JSON."""
    return await sync_to_async(_cached_cell_keys_sync)(cells, windows)


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

    cell = await sync_to_async(_find_forecast_cell_sync)(lat_r, lon_r, day_key, forecast_days)
    if cell is not None:
        emit("count", "cache.lookup", kind="forecast", outcome="hit", source=cell.source)
        return cell
    emit("count", "cache.lookup", kind="forecast", outcome="miss")
    return None


async def _leased_fetch(
    kind: str,
    lat_r: float,
    lon_r: float,
    day_key: date,
    forecast_days: int,
    find_sync: Callable[[float, float, date, int | None], Any],
    fetch_and_store: Callable[[], Awaitable[Any]],
    raise_throttled: bool = False,
) -> Any:
    """Run *fetch_and_store* only as the holder of this cell's fetch lease.

    However many callers miss the cache for one cell at once, one fetches; the rest wait
    for it and return what it stored (see ``core.cell_lease``). A waiter whose holder was
    throttled is throttled too, so its task defers alongside instead of failing the cell.
    """
    while True:
        async with fetch_lease(kind, lat_r, lon_r, day_key) as holder:
            # Looked up again under the lease: another holder may have stored the cell
            # between this caller's cache miss and taking the lease.
            cell = await sync_to_async(find_sync)(lat_r, lon_r, day_key, forecast_days)
            if cell is not None:
                return cell
            if holder:
                return await fetch_and_store()
            if await sync_to_async(find_sync)(lat_r, lon_r, day_key, None) is None:
                # The holder failed or died. Fetching here would be exactly the duplicate
                # request the lease exists to prevent; the next scan or job retries.
                logger.info(f"{kind} cell ({lat_r}, {lon_r}, {day_key}): concurrent fetch stored nothing")
                if raise_throttled and (wait := cooldown_left(open_meteo_limit())):
                    raise ProviderThrottled("open-meteo", wait)
                return None
        # A fresh cell exists but covers fewer days than this caller needs. That is a
        # different request, so compete for the lease again and fetch the longer horizon.


async def get_or_fetch_forecast_cell(
    lat_r: float, lon_r: float, day_key: str | date, forecast_days: int, *, allow_fallback: bool = True
) -> ForecastCell | None:
    """Return a fresh ForecastCell from the DB, or fetch + store and return.

    Tries Open-Meteo first, falls back to OWM. Returns None if both fail. At most one
    caller per cell fetches at a time; concurrent callers get what it stored.

    When Open-Meteo is rate-limited (our budget or its 429) and *allow_fallback* is False,
    raises ``ProviderThrottled`` instead of spending an OWM call: the cell task would rather
    wait. Other Open-Meteo failures fall back either way.
    """
    if isinstance(day_key, str):
        day_key = date.fromisoformat(day_key)

    cell = await get_cached_forecast_cell(lat_r, lon_r, day_key, forecast_days)
    if cell is not None:
        return cell
    return await _leased_fetch(
        "forecast",
        lat_r,
        lon_r,
        day_key,
        forecast_days,
        _find_forecast_cell_sync,
        lambda: _fetch_and_store_forecast(lat_r, lon_r, day_key, forecast_days, allow_fallback),
        raise_throttled=not allow_fallback,
    )


async def _fetch_and_store_forecast(
    lat_r: float, lon_r: float, day_key: date, forecast_days: int, allow_fallback: bool = True
) -> ForecastCell | None:
    data = None
    source = "open-meteo"
    limit = open_meteo_limit()
    wait = acquire(limit, open_meteo_weight(FORECAST_VARIABLES, forecast_days))
    if not wait:
        try:
            data = await _fetch_open_meteo(lat_r, lon_r, forecast_days, day_key.isoformat())
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            # Logged: the calling task still reports success, so a TLS, DNS or rate-limit
            # failure is otherwise visible only as a failed job.
            wait = _throttle_wait(limit, exc)
            logger.warning(
                "get_or_fetch_forecast_cell: Open-Meteo fetch failed for ({}, {}): {}", lat_r, lon_r, _failure(exc)
            )
    if data is None and wait and not allow_fallback:
        raise ProviderThrottled(limit.provider, wait)

    if data is None:
        emit("count", "weather.fallback", outcome="needed")
        source = "openweathermap"
        # Without a key _fetch_owm makes no call, so it spends no budget either.
        if not os.environ.get("OPENWEATHERMAP_API_KEY") or not acquire(owm_limit()):
            try:
                data = await _fetch_owm(lat_r, lon_r)
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                _throttle_wait(owm_limit(), exc)
                logger.warning(
                    "get_or_fetch_forecast_cell: OWM fetch failed for ({}, {}): {}", lat_r, lon_r, _failure(exc)
                )
        else:
            logger.warning("get_or_fetch_forecast_cell: OpenWeatherMap budget reached, no fallback call")

    if data is None:
        logger.warning(
            "get_or_fetch_forecast_cell: both Open-Meteo and OWM failed for ({}, {}), day_key={}, forecast_days={}",
            lat_r,
            lon_r,
            day_key,
            forecast_days,
        )
        return None

    if source == "openweathermap":
        emit("count", "weather.fallback", outcome="recovered")
    return await sync_to_async(_store_forecast_cell_sync)(lat_r, lon_r, day_key, forecast_days, data, source)


async def get_cached_ensemble_cell(
    lat_r: float, lon_r: float, day_key: str | date, forecast_days: int
) -> EnsembleCell | None:
    """Return a fresh EnsembleCell already in the DB, without ever fetching."""
    if isinstance(day_key, str):
        day_key = date.fromisoformat(day_key)
    cell = await sync_to_async(_get_ensemble_cell_sync)(lat_r, lon_r, day_key, forecast_days)
    emit("count", "cache.lookup", kind="ensemble", outcome="hit" if cell is not None else "miss")
    return cell


async def get_or_fetch_ensemble_cell(
    lat_r: float, lon_r: float, day_key: str | date, forecast_days: int, *, raise_throttled: bool = False
) -> EnsembleCell | None:
    """Return a fresh EnsembleCell from the DB, or fetch + store and return.

    At most one caller per cell fetches at a time; concurrent callers get what it stored.
    A rate-limited Open-Meteo returns None, or raises ``ProviderThrottled`` if asked to.
    """
    if isinstance(day_key, str):
        day_key = date.fromisoformat(day_key)

    cell = await get_cached_ensemble_cell(lat_r, lon_r, day_key, forecast_days)
    if cell is not None:
        return cell
    return await _leased_fetch(
        "ensemble",
        lat_r,
        lon_r,
        day_key,
        forecast_days,
        _get_ensemble_cell_sync,
        lambda: _fetch_and_store_ensemble(lat_r, lon_r, day_key, forecast_days, raise_throttled),
        raise_throttled=raise_throttled,
    )


async def _fetch_and_store_ensemble(
    lat_r: float, lon_r: float, day_key: date, forecast_days: int, raise_throttled: bool = False
) -> EnsembleCell | None:
    data = None
    limit = open_meteo_limit()
    wait = acquire(limit, open_meteo_weight(len(ENSEMBLE_VARIABLES), forecast_days, ENSEMBLE_MODEL_COUNT))
    if not wait:
        try:
            data = await _fetch_ensemble(lat_r, lon_r, forecast_days, day_key.isoformat())
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            # Logged, unlike before: a swallowed failure here stores no cell, so the task that
            # called it still reports success while having fetched nothing.
            wait = _throttle_wait(limit, exc)
            logger.warning(
                "get_or_fetch_ensemble_cell: ensemble fetch failed for ({}, {}), day_key={}, forecast_days={}: {}",
                lat_r,
                lon_r,
                day_key,
                forecast_days,
                _failure(exc),
            )

    if data is None:
        if wait and raise_throttled:
            raise ProviderThrottled(limit.provider, wait)
        return None

    return await sync_to_async(_store_ensemble_cell_sync)(lat_r, lon_r, day_key, forecast_days, data)


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
