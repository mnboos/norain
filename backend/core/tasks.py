"""django-tasks task definitions for NoRain.

Background tasks for route geometry computation and forecast grid pre-warming.
"""

from datetime import UTC, date, datetime

from asgiref.sync import async_to_sync
from django.tasks import task
from loguru import logger

from .models import RecurringRoute
from .schedule import forecast_available_at, upcoming_departures
from .weather import (  # reuse existing functions
    COORD_ROUND,
    SAMPLE_INTERVAL_DEFAULT_S,
    _cumulative_times_s,
    _fetch_route,
    _sample_indices,
)


@task()
def refresh_route_geometry(route_id: str) -> None:
    """Fetch the GraphHopper route for a RecurringRoute and store polyline + sample points."""
    async_to_sync(_refresh_route_geometry_async)(route_id)


async def _refresh_route_geometry_async(route_id: str) -> None:
    try:
        route = await RecurringRoute.objects.aget(id=route_id)
    except RecurringRoute.DoesNotExist:
        logger.warning(f"Route {route_id} not found for geometry refresh")
        return

    try:
        gh = await _fetch_route(route.profile, route.start_lat, route.start_lon, route.dest_lat, route.dest_lon)
    except Exception as e:
        logger.error(f"Failed to fetch route geometry for {route.name}: {e}")
        return

    path = gh["paths"][0]
    coords: list[list[float]] = path["points"]["coordinates"]
    time_details = path.get("details", {}).get("time", [[0, len(coords) - 1, path.get("time", 0)]])
    cum_s = _cumulative_times_s(coords, time_details)
    sample_idx = _sample_indices(cum_s, max(60, SAMPLE_INTERVAL_DEFAULT_S))

    # Pre-compute sample points with rounded coordinates for grid lookups
    sample_points = []
    for idx in sample_idx:
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

    route.polyline = coords
    route.total_seconds = int(cum_s[-1]) if cum_s else 0
    route.total_distance_m = round(path.get("distance", 0.0), 1)
    route.sample_points = sample_points
    route.geometry_fetched_at = datetime.now(tz=UTC)
    await route.asave()

    logger.info(f"Route geometry stored for {route.name} ({len(sample_points)} sample points)")


@task()
def refresh_forecast_cell(lat_r: float, lon_r: float, day_key: str, forecast_days: int) -> None:
    """Pre-fetch a single grid cell's deterministic weather and store it."""
    async_to_sync(_refresh_forecast_cell_async)(lat_r, lon_r, day_key, forecast_days)


async def _refresh_forecast_cell_async(lat_r: float, lon_r: float, day_key: str, forecast_days: int) -> None:
    from .grid import get_or_fetch_forecast_cell

    cell = await get_or_fetch_forecast_cell(lat_r, lon_r, day_key, forecast_days)
    if cell:
        logger.debug(f"Forecast cell refreshed: ({lat_r}, {lon_r}, {day_key})")


@task()
def refresh_ensemble_cell(lat_r: float, lon_r: float, day_key: str, forecast_days: int) -> None:
    """Pre-fetch a single grid cell's ensemble data and store it."""
    async_to_sync(_refresh_ensemble_cell_async)(lat_r, lon_r, day_key, forecast_days)


async def _refresh_ensemble_cell_async(lat_r: float, lon_r: float, day_key: str, forecast_days: int) -> None:
    from .grid import get_or_fetch_ensemble_cell

    cell = await get_or_fetch_ensemble_cell(lat_r, lon_r, day_key, forecast_days)
    if cell:
        logger.debug(f"Ensemble cell refreshed: ({lat_r}, {lon_r}, {day_key})")


@task()
def refresh_upcoming_forecasts() -> dict:
    """Find all routes with upcoming departures, enqueue cell refreshes for missing/stale cells.

    Returns counts of processed routes and enqueued cell refreshes.
    """
    return async_to_sync(_refresh_upcoming_forecasts_async)()


async def _refresh_upcoming_forecasts_async() -> dict:
    from .grid import _get_ensemble_cell_sync, _get_forecast_cell_sync

    today = date.today()
    now = datetime.now(tz=UTC)

    routes = RecurringRoute.objects.filter(active=True)
    route_count = 0
    cell_count = 0
    ensemble_count = 0

    async for route in routes:
        if not route.sample_points:
            continue

        route_count += 1
        departures = upcoming_departures(route.schedule_cron, count=3, after=now)

        for dep in departures:
            if not forecast_available_at(dep):
                continue

            dep_day_key = dep.date()
            days = max(1, min(16, (dep_day_key - today).days + 2))

            for sp in route.sample_points:
                lat_r = sp["lat_r"]
                lon_r = sp["lon_r"]

                # Check if deterministic cell is missing/stale
                fc = _get_forecast_cell_sync(lat_r, lon_r, dep_day_key, "open-meteo", days)
                if fc is None:
                    await refresh_forecast_cell.aenqueue(lat_r, lon_r, dep_day_key.isoformat(), days)
                    cell_count += 1

                # Check if ensemble cell is missing/stale
                ec = _get_ensemble_cell_sync(lat_r, lon_r, dep_day_key, days)
                if ec is None:
                    await refresh_ensemble_cell.aenqueue(lat_r, lon_r, dep_day_key.isoformat(), days)
                    ensemble_count += 1

    logger.info(
        f"refresh_upcoming_forecasts: {route_count} routes, "
        f"{cell_count} cell refreshes, {ensemble_count} ensemble refreshes enqueued"
    )
    return {"routes": route_count, "cells_enqueued": cell_count, "ensembles_enqueued": ensemble_count}
