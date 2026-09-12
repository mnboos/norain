"""django-tasks task definitions for NoRain.

Background tasks for route geometry computation and forecast grid pre-warming.
"""

from datetime import UTC, date, datetime, timedelta

from asgiref.sync import async_to_sync, sync_to_async
from django.tasks import task
from loguru import logger

from .models import ProcessedStripeEvent, RecurringRoute, route_line
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

    route.polyline = route_line(coords)
    route.total_seconds = int(cum_s[-1]) if cum_s else 0
    route.total_distance_m = round(path.get("distance", 0.0), 1)
    route.sample_points = sample_points
    route.geometry_fetched_at = datetime.now(tz=UTC)
    await route.asave()

    logger.info(f"Route geometry stored for {route.name} ({len(sample_points)} sample points)")

    # Give the new shape a thumbnail straight away. Scores stay grey until the cells warm.
    await refresh_route_thumbnail.aenqueue(str(route.id))


@task()
def refresh_route_thumbnail(route_id: str) -> None:
    """Rebuild a route's list glyph from whatever forecast cells are already warm."""
    async_to_sync(_refresh_route_thumbnail_async)(route_id)


def _known_samples(thumbnail: dict | None) -> int:
    """How many of a thumbnail's sample points actually carry weather."""
    if not thumbnail:
        return 0
    return sum(1 for sample in thumbnail.get("samples") or [] if sample)


async def _refresh_route_thumbnail_async(route_id: str) -> None:
    from .thumbnails import compute_route_thumbnail

    try:
        route = await RecurringRoute.objects.aget(id=route_id)
    except RecurringRoute.DoesNotExist:
        logger.warning(f"Route {route_id} not found for thumbnail refresh")
        return

    new = await compute_route_thumbnail(route)

    if new is None and route.sample_points:
        # Nothing to forecast right now (no upcoming departure), but the stored glyph
        # still describes this route's shape — leave it rather than blanking the row.
        return

    if new is not None and new.get("departure") == (route.thumbnail or {}).get("departure"):
        # Don't regress a good glyph to grey. Cells expire after MAX_CELL_AGE (2 h) while
        # the scheduler runs less often than that, so a pass routinely finds every cell
        # cold; overwriting each time would leave thumbnails grey almost always.
        # A *different* departure still overwrites — that is a different ride.
        if _known_samples(new) == 0 and _known_samples(route.thumbnail) > 0:
            logger.debug(f"Keeping existing thumbnail for {route.name}: no warm cells this pass")
            return

    route.thumbnail = new
    route.thumbnail_computed_at = datetime.now(tz=UTC)
    await route.asave(update_fields=["thumbnail", "thumbnail_computed_at"])


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


STRIPE_EVENT_RETENTION = timedelta(days=30)


def _purge_processed_events() -> int:
    """Drop webhook ids older than Stripe could possibly retry."""
    cutoff = datetime.now(tz=UTC) - STRIPE_EVENT_RETENTION
    return ProcessedStripeEvent.objects.filter(received_at__lt=cutoff).delete()[0]


@task()
def refresh_upcoming_forecasts() -> dict:
    """Find all routes with upcoming departures, enqueue cell refreshes for missing/stale cells.

    Returns counts of processed routes and enqueued cell refreshes.
    """
    return async_to_sync(_refresh_upcoming_forecasts_async)()


async def _prewarm_routes() -> list[RecurringRoute]:
    """Active routes whose owner's tier still entitles them to pre-warming.

    This is where the Open-Meteo budget is actually spent — a route here fans out one
    fetch per sample point per upcoming departure — so the quota has to be applied at
    this point and not only in the HTTP endpoints.

    An account can only exceed its quota by being downgraded after creating routes. In
    that case keep the oldest `max_routes`: those are the ones the user has relied on
    longest, and the choice is stable between runs, unlike dropping all of them. The extra
    routes stay visible and usable in the UI — they just stop being pre-warmed.
    """
    from .entitlements import entitlements_for_sync

    selected: list[RecurringRoute] = []
    by_owner: dict[int | None, list[RecurringRoute]] = {}
    query = (
        RecurringRoute.objects.filter(active=True)
        .select_related("owner")
        .defer("polyline", "thumbnail")  # large JSON blobs this pass never reads
        .order_by("created_at")
    )
    async for route in query:
        by_owner.setdefault(route.owner_id, []).append(route)

    for owner_id, routes in by_owner.items():
        if owner_id is None:
            # Ownerless legacy routes belong to nobody and are invisible in the UI; see
            # the claim_routes management command.
            continue
        limits = await sync_to_async(entitlements_for_sync)(routes[0].owner)
        selected.extend(routes if limits.max_routes is None else routes[: limits.max_routes])
    return selected


async def _refresh_upcoming_forecasts_async() -> dict:
    from .grid import _get_ensemble_cell_sync, _get_forecast_cell_sync

    # These two are plain ORM calls; reached directly from this async function they raise
    # SynchronousOnlyOperation, which is what stopped the whole pre-warm pass.
    get_forecast_cell = sync_to_async(_get_forecast_cell_sync)
    get_ensemble_cell = sync_to_async(_get_ensemble_cell_sync)

    today = date.today()
    now = datetime.now(tz=UTC)

    routes = await _prewarm_routes()
    route_count = 0
    cell_count = 0
    ensemble_count = 0

    for route in routes:
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
                fc = await get_forecast_cell(lat_r, lon_r, dep_day_key, "open-meteo", days)
                if fc is None:
                    await refresh_forecast_cell.aenqueue(lat_r, lon_r, dep_day_key.isoformat(), days)
                    cell_count += 1

                # Check if ensemble cell is missing/stale
                ec = await get_ensemble_cell(lat_r, lon_r, dep_day_key, days)
                if ec is None:
                    await refresh_ensemble_cell.aenqueue(lat_r, lon_r, dep_day_key.isoformat(), days)
                    ensemble_count += 1

    # Enqueued last so these land behind the cell refreshes above in the FIFO queue and
    # see warm data. A pass that still runs early just writes grey; the next tick fills in.
    thumbnail_count = 0
    for route in routes:
        if not route.sample_points:
            continue
        await refresh_route_thumbnail.aenqueue(str(route.id))
        thumbnail_count += 1

    # The webhook ledger only exists to reject Stripe's redeliveries, which stop within
    # a few days; without a purge it grows forever.
    purged = await sync_to_async(_purge_processed_events)()

    logger.info(
        f"refresh_upcoming_forecasts: {route_count} routes, "
        f"{cell_count} cell refreshes, {ensemble_count} ensemble refreshes, "
        f"{thumbnail_count} thumbnails enqueued, {purged} stripe events purged"
    )
    return {
        "routes": route_count,
        "cells_enqueued": cell_count,
        "ensembles_enqueued": ensemble_count,
        "thumbnails_enqueued": thumbnail_count,
        "stripe_events_purged": purged,
    }
