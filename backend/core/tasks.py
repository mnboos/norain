"""django-tasks task definitions for NoRain.

Background tasks for route geometry computation and forecast grid pre-warming.
"""

import asyncio
import random
from datetime import UTC, date, datetime, timedelta
from time import monotonic

from asgiref.sync import async_to_sync, sync_to_async
from django.db import transaction
from django.db.models import F
from loguru import logger

from core import departures, telemetry
from core.claims import claim_cell, release_cell
from core.entitlements import (
    allowed_route_ids,
    briefing_route_ids,
    briefing_route_ids_by_owner,
    entitlements_for,
    forecast_params_for,
    strip_uncertainty,
)
from core.forecast_schemas import RouteWeatherOut
from core.gpx import exact_geometry
from core.grid import (
    get_cached_cell_keys,
    get_or_fetch_ensemble_cell,
    get_or_fetch_forecast_cell,
)
from core.jobs import MAX_PLAN_ATTEMPTS, PLAN_RETRY_DELAY, get_or_start_job, publish, set_status
from core.journey_geometry import Limits, LineMeasure
from core.journey_planner import JourneyPlanner, RoutingBudget
from core.journeys import LAST_DAY_SLACK, LODGING_CORRIDOR_M, LODGING_WINDOW, lodging_candidates
from core.models import (
    ForecastJob,
    Journey,
    JourneyDay,
    JourneyStage,
    ProcessedStripeEvent,
    RecurringRoute,
    route_line,
)
from core.pois import pois_along_sync
from core.ratelimit import ProviderThrottled
from core.road_prefs import RoadPrefs, merge_models, road_prefs_model
from core.schedule import LOCAL_TZ, forecast_available_at, local_today, next_departure, upcoming_departures
from core.sections import compute_sections
from core.stations import api_key, purge_station_data, refresh_stations_for_ride, ride_in_window
from core.thumbnails import compute_route_thumbnail
from core.tracing import traced_task as task
from core.weather import (  # reuse existing functions
    ROUTING_ERRORS,
    SAMPLE_INTERVAL_DEFAULT_S,
    WeatherSnapshot,
    build_geometries,
    build_geometry,
    compute_route_weather,
    forecast_days_for,
    route_legs,
    routing_points,
)
from core.weather_routing import cell_weather, corridor_cells, zone_model
from core.wind import valid_vertex_times


@task()
def refresh_route_geometry(route_id: str, *, backfill_only: bool = False) -> None:
    """Fetch the GraphHopper route for a RecurringRoute and store polyline + sample points."""
    async_to_sync(_refresh_route_geometry_async)(route_id, backfill_only=backfill_only)


async def _refresh_route_geometry_async(route_id: str, *, backfill_only: bool = False) -> None:
    try:
        route = await RecurringRoute.objects.aget(id=route_id)
    except RecurringRoute.DoesNotExist:
        logger.warning(f"Route {route_id} not found for geometry refresh")
        return

    if backfill_only and valid_vertex_times(route.polyline_coordinates, route.vertex_times):
        return
    try:
        geometry = (
            exact_geometry(route.imported_coordinates, route.duration_seconds, SAMPLE_INTERVAL_DEFAULT_S)
            if route.geometry_source == "imported"
            else await build_geometry(route.profile, route.routing_points, SAMPLE_INTERVAL_DEFAULT_S)
        )
    except ROUTING_ERRORS as e:
        # The old geometry stays in place; the next edit or backfill tries again.
        logger.error(f"Failed to fetch route geometry for {route.name}: {e}")
        return

    sample_points = geometry["sample_points"]
    now = datetime.now(tz=UTC)
    stored = await RecurringRoute.objects.filter(id=route.id, updated_at=route.updated_at).aupdate(
        polyline=route_line(geometry["polyline"]),
        total_seconds=geometry["total_seconds"],
        total_distance_m=geometry["total_distance_m"],
        sample_points=sample_points,
        vertex_times=geometry["vertex_times"],
        vertex_elevations=geometry.get("vertex_elevations"),
        geometry_fetched_at=now,
        updated_at=now,
    )
    if not stored:
        # An edit won while routing was in flight. Never write the stale model back.
        if await RecurringRoute.objects.filter(id=route.id).aexists():
            await refresh_route_geometry.aenqueue(str(route.id))
        return

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

    # Don't regress a good glyph to grey. Cells expire after MAX_CELL_AGE (2 h) while
    # the scheduler runs less often than that, so a pass routinely finds every cell
    # cold; overwriting each time would leave thumbnails grey almost always.
    # A *different* departure still overwrites — that is a different ride.
    same_departure = new is not None and new.get("departure") == (route.thumbnail or {}).get("departure")
    if same_departure and _known_samples(new) == 0 and _known_samples(route.thumbnail) > 0:
        logger.debug(f"Keeping existing thumbnail for {route.name}: no warm cells this pass")
        return

    route.thumbnail = new
    route.thumbnail_computed_at = datetime.now(tz=UTC)
    await route.asave(update_fields=["thumbnail", "thumbnail_computed_at"])


# --------------------------------------------------------------------------- grid cells
# One task per ~1 km² cell: the unit the providers are actually billed in, and the unit
# that is worth retrying on its own. These run on the `cells` queue, several workers wide.
#
# A rate-limited Open-Meteo (core.ratelimit) does not fail the cell: the task re-enqueues
# itself a little later, without settling, so the job waits for the data instead of being
# assembled without it. The deferrals are bounded well inside JOB_STALL_TIMEOUT, and each
# one touches the job, or the stall check would restart it and fan every cell out again.
# A longer wait (the hourly or daily limit) skips straight to the last attempt.

MAX_CELL_DEFERS = 3
MAX_DEFER_WAIT = 90  # seconds; beyond that the provider is out for the hour, not the minute
SHORT_WAIT = 1.0  # waited out in the worker; anything longer would block the replica


async def _cell_fetch(fetch, attempt: int):
    """``fetch(last)``, once more after a wait of up to SHORT_WAIT. Raises ``ProviderThrottled`` to defer."""
    last = attempt >= MAX_CELL_DEFERS
    try:
        return await fetch(last)
    except ProviderThrottled as throttled:
        if throttled.retry_after > MAX_DEFER_WAIT:
            return await fetch(True)
        if throttled.retry_after > SHORT_WAIT:
            raise
        await asyncio.sleep(throttled.retry_after)
    return await fetch(last)


async def _defer_cell(task, kind: str, args: tuple, job_id: str | None, attempt: int, wait: float) -> None:
    delay = timedelta(seconds=wait + random.uniform(1, 10))  # noqa: S311 -- jitter, not a secret
    logger.info(f"{kind} cell {args[:3]}: provider throttled, retrying in {delay.total_seconds():.0f} s")
    await task.using(run_after=datetime.now(tz=UTC) + delay).aenqueue(*args, job_id, attempt=attempt + 1)
    if job_id:
        await ForecastJob.objects.filter(id=job_id, status=ForecastJob.Status.FETCHING).aupdate(
            updated_at=datetime.now(tz=UTC)
        )


@task(queue_name="cells")
def refresh_forecast_cell(
    lat_r: float, lon_r: float, day_key: str, forecast_days: int, job_id: str | None = None, attempt: int = 0
) -> None:
    """Pre-fetch a single grid cell's deterministic weather and store it."""
    async_to_sync(_refresh_forecast_cell_async)(lat_r, lon_r, day_key, forecast_days, job_id, attempt)


async def _refresh_forecast_cell_async(
    lat_r: float, lon_r: float, day_key: str, forecast_days: int, job_id: str | None = None, attempt: int = 0
) -> None:
    stored = deferred = False
    try:
        cell = await _cell_fetch(
            lambda last: get_or_fetch_forecast_cell(lat_r, lon_r, day_key, forecast_days, allow_fallback=last),
            attempt,
        )
        stored = bool(cell)
        if stored:
            logger.debug(f"Forecast cell refreshed: ({lat_r}, {lon_r}, {day_key})")
        else:
            logger.warning(f"Forecast cell NOT stored: ({lat_r}, {lon_r}, {day_key}, days={forecast_days})")
    except ProviderThrottled as throttled:
        await _defer_cell(
            refresh_forecast_cell,
            "Forecast",
            (lat_r, lon_r, day_key, forecast_days),
            job_id,
            attempt,
            throttled.retry_after,
        )
        deferred = True
    finally:
        # Released on failure too: a cell both providers refused must stay claimable, or
        # one bad fetch would block every retry for the whole claim TTL.
        release_cell("forecast", lat_r, lon_r, day_key, forecast_days)
        if not deferred:  # a deferred cell settles when its retry does
            await _settle_cell(job_id, failed=not stored)


@task(queue_name="cells")
def refresh_ensemble_cell(
    lat_r: float, lon_r: float, day_key: str, forecast_days: int, job_id: str | None = None, attempt: int = 0
) -> None:
    """Pre-fetch a single grid cell's ensemble data and store it."""
    async_to_sync(_refresh_ensemble_cell_async)(lat_r, lon_r, day_key, forecast_days, job_id, attempt)


async def _refresh_ensemble_cell_async(
    lat_r: float, lon_r: float, day_key: str, forecast_days: int, job_id: str | None = None, attempt: int = 0
) -> None:
    stored = deferred = False
    try:
        cell = await _cell_fetch(
            lambda last: get_or_fetch_ensemble_cell(lat_r, lon_r, day_key, forecast_days, raise_throttled=not last),
            attempt,
        )
        stored = bool(cell)
        if stored:
            logger.debug(f"Ensemble cell refreshed: ({lat_r}, {lon_r}, {day_key})")
        else:
            logger.warning(f"Ensemble cell NOT stored: ({lat_r}, {lon_r}, {day_key}, days={forecast_days})")
    except ProviderThrottled as throttled:
        await _defer_cell(
            refresh_ensemble_cell,
            "Ensemble",
            (lat_r, lon_r, day_key, forecast_days),
            job_id,
            attempt,
            throttled.retry_after,
        )
        deferred = True
    finally:
        release_cell("ensemble", lat_r, lon_r, day_key, forecast_days)
        if not deferred:
            await _settle_cell(job_id, failed=not stored)


@task(queue_name="cells")
def refresh_station_observations(job_id: str) -> None:
    """Fetch recent weather-station readings near the part of a job's ride that is close to now."""
    async_to_sync(_refresh_station_observations_async)(job_id)


async def _refresh_station_observations_async(job_id: str) -> None:
    try:
        job = await ForecastJob.objects.select_related("owner").filter(id=job_id).afirst()
        if job is not None and job.geometry and (await entitlements_for(job.owner)).station_correction:
            now = datetime.now(tz=UTC)
            times = departures.candidate_times(job.params)
            points = job.geometry["sample_points"]
            if departures.enabled(job.params):
                from core.stations import STATION_HORIZON

                points = [
                    {**sp, "elapsed_s": 0}
                    for sp in points
                    if any(abs(t + timedelta(seconds=sp["elapsed_s"]) - now) < STATION_HORIZON for t in times)
                ]
                departure = now
            else:
                departure = datetime.fromisoformat(job.params["departure_time"])
            fresh = await refresh_stations_for_ride(points, departure, now)
            logger.debug(f"Station readings for job {job_id}: {fresh} fresh")
    finally:
        # Settled even when the task fails: the correction is optional, the forecast is not.
        # Never counted as failed either: no stations nearby is a normal answer, and
        # cells_failed would cut the job's lifetime as if forecast data were missing.
        await _settle_cell(job_id)


def _settle_cell_sync(job_id: str, failed: bool = False) -> tuple[ForecastJob | None, bool]:
    """Count one finished cell and, if it was the last, hand the job to computation.

    ``failed`` also counts the cell in ``cells_failed``: it settled, but stored nothing.

    Returns ``(job, won_computation)``. The increment is atomic but the read that follows is
    not, so several `cells` workers finishing at once could each see a complete job and
    enqueue computation. The guarded UPDATE lets the database pick exactly one winner: only
    the row still in `fetching` flips to `assembling`, and only that caller enqueues.

    The increment is itself guarded on the job still fetching, so a late duplicate settle
    cannot push the counter past `cells_total` and show the browser "3/2".
    """
    jobs = ForecastJob.objects.filter(id=job_id)
    increments = {"cells_settled": F("cells_settled") + 1}
    if failed:
        increments["cells_failed"] = F("cells_failed") + 1
    if not jobs.filter(status=ForecastJob.Status.FETCHING).update(**increments):
        return None, False

    won = bool(
        jobs.filter(
            status=ForecastJob.Status.FETCHING,
            cells_settled__gte=F("cells_total"),
        ).update(status=ForecastJob.Status.ASSEMBLING, updated_at=datetime.now(tz=UTC))
    )
    return jobs.first(), won


async def _settle_cell(job_id: str | None, failed: bool = False) -> None:
    if not job_id:
        return
    job, won = await sync_to_async(_settle_cell_sync)(job_id, failed)
    if job is None:
        return
    await publish(job)
    if won:
        await compute_route_weather_job.aenqueue(str(job.id))


# --------------------------------------------------------------------------- forecast jobs
# The HTTP endpoints only create a job and enqueue `plan_forecast_job`. Planning resolves
# geometry and fans out one cell task per distinct grid cell; the last cell to settle hands
# over to `compute_route_weather_job`, then assembly adds charts and sections.


def _cell_set(sample_points: list[dict]) -> list[tuple[float, float]]:
    """The distinct grid cells a route passes through, in order of first visit.

    A five-minute sampling interval puts several consecutive samples inside the same ~1 km²
    cell, and every one of them would otherwise become its own provider request.
    """
    seen: dict[tuple[float, float], None] = {}
    for sp in sample_points:
        seen.setdefault((sp["lat_r"], sp["lon_r"]), None)
    return list(seen)


async def _job_geometry(job: ForecastJob) -> dict | None:
    """Resolve the job's route geometry, or None while a saved route is still waiting.

    Computed once here and stored on the job, so assembly -- usually a different worker
    process, where the routing LRU in weather.py is cold -- never calls GraphHopper again.
    """
    if job.geometry:
        return job.geometry

    params = job.params
    if job.kind == ForecastJob.Kind.ROUTE:
        route = await RecurringRoute.objects.filter(id=params["route_id"]).afirst()
        if route is None:
            raise ValueError(f"Route {params['route_id']} no longer exists")
        if not route.sample_points:
            await refresh_route_geometry.aenqueue(str(route.id))
            return None
        return {
            "polyline": route.polyline_coordinates,
            "sample_points": route.sample_points,
            "vertex_times": route.vertex_times,
            "vertex_elevations": route.vertex_elevations,
            "total_seconds": route.total_seconds,
            "total_distance_m": route.total_distance_m,
        }

    if job.kind == ForecastJob.Kind.JOURNEY_STAGE:
        # Written whole by plan_journey, never without geometry: nothing to wait for.
        stage = await JourneyStage.objects.filter(id=params["journey_stage_id"]).afirst()
        if stage is None:
            raise ValueError(f"Journey stage {params['journey_stage_id']} no longer exists")
        return {
            "polyline": stage.polyline_coordinates,
            "sample_points": stage.sample_points,
            "vertex_times": stage.vertex_times,
            "vertex_elevations": stage.vertex_elevations,
            "total_seconds": stage.total_seconds,
            "total_distance_m": stage.total_distance_m,
        }

    if params.get("geometry_source") == "imported":
        return exact_geometry(
            params["coordinates"], params["duration_seconds"], params.get("interval_seconds", SAMPLE_INTERVAL_DEFAULT_S)
        )

    return await build_geometry(
        params["profile"],
        routing_points(
            params["start_lat"],
            params["start_lon"],
            params["dest_lat"],
            params["dest_lon"],
            params.get("via_points", []),
        ),
        params.get("interval_seconds", SAMPLE_INTERVAL_DEFAULT_S),
    )


@task(queue_name="forecasts")
def plan_forecast_job(job_id: str) -> None:
    """Resolve a job's geometry and fan out one task per grid cell it needs."""
    async_to_sync(_plan_forecast_job_async)(job_id)


@telemetry.stage("planning")
async def _plan_forecast_job_async(job_id: str) -> None:
    job = await ForecastJob.objects.select_related("owner").filter(id=job_id).afirst()
    if job is None:
        logger.warning(f"Forecast job {job_id} not found for planning")
        return
    if job.status == ForecastJob.Status.DONE:
        return

    if job.kind == ForecastJob.Kind.ROUTE and str(job.params.get("route_id")) not in {
        str(i) for i in await sync_to_async(allowed_route_ids)(job.owner)
    }:
        await _fail_not_allowed(job, "Diese Route ist durch deinen Tarif pausiert.")
        return
    if (
        job.kind == ForecastJob.Kind.JOURNEY_STAGE
        and not await JourneyStage.objects.filter(
            id=job.params.get("journey_stage_id"), day__journey__owner_id=job.owner_id
        ).aexists()
    ):
        await _fail_not_allowed(job, "Diese Etappe gibt es nicht mehr.")
        return
    await telemetry.bind_job(job)
    await set_status(job, ForecastJob.Status.PLANNING)

    try:
        geometry = await _job_geometry(job)
    except ROUTING_ERRORS as exc:  # ValueError also covers a saved route that no longer exists
        logger.error(f"Forecast job {job.id} could not resolve geometry: {exc}")
        await set_status(job, ForecastJob.Status.FAILED, error="Route konnte nicht berechnet werden.")
        return

    if geometry is None:
        # A saved route whose geometry task has not landed yet. refresh_route_geometry logs
        # and returns on failure, so without this ceiling a route GraphHopper cannot solve
        # would have its plan task re-deferring itself forever.
        job.attempts += 1
        if job.attempts >= MAX_PLAN_ATTEMPTS:
            await set_status(job, ForecastJob.Status.FAILED, error="Routen-Geometrie konnte nicht berechnet werden.")
            return
        await job.asave(update_fields=["attempts", "updated_at"])
        await plan_forecast_job.using(run_after=datetime.now(tz=UTC) + PLAN_RETRY_DELAY).aenqueue(str(job.id))
        return

    job.params = await forecast_params_for(job.owner, job.params)
    sample_points = geometry["sample_points"]
    windows = departures.fetch_windows(job.params, sample_points, local_today())
    cells = _cell_set(sample_points)
    with_stations = await _wants_stations(job, geometry.get("total_seconds"))
    warm_forecasts, warm_ensembles = await get_cached_cell_keys(cells, windows)

    # Both counters and the status must be committed before the first task is enqueued: a
    # `cells` worker is fast enough to settle a cell while this function is still running,
    # and a settle against cells_total=0 would hand the job to assembly with no data.
    job.geometry = geometry
    # deterministic + ensemble per cell per window, plus one unit for the station fetch.
    job.cells_total = len(cells) * len(windows) * 2 + (1 if with_stations else 0)
    job.cells_settled = len(warm_forecasts) + len(warm_ensembles)
    job.cells_failed = 0
    job.status = ForecastJob.Status.FETCHING
    await job.asave(update_fields=["geometry", "cells_total", "cells_settled", "cells_failed", "status", "updated_at"])
    await publish(job)

    if job.cells_settled == job.cells_total:
        won = await ForecastJob.objects.filter(id=job.id, status=ForecastJob.Status.FETCHING).aupdate(
            status=ForecastJob.Status.ASSEMBLING, updated_at=datetime.now(tz=UTC)
        )
        if won:
            await job.arefresh_from_db()
            await publish(job)
            await compute_route_weather_job.aenqueue(str(job.id))
        return

    for day_key, days in windows:
        day = date.fromisoformat(day_key)
        for lat_r, lon_r in cells:
            # Ensemble cells are fetched for every tier: `pop` and `rain_if_wet` are not gated,
            # only the spread is, and the cells are shared between accounts anyway.
            for kind, warm_keys, cell_task in (
                ("forecast", warm_forecasts, refresh_forecast_cell),
                ("ensemble", warm_ensembles, refresh_ensemble_cell),
            ):
                if (lat_r, lon_r, day) in warm_keys:
                    continue
                # Enqueued even when the claim is held elsewhere. The holder is usually the
                # pre-warm scan, whose task carries no job_id and so would never report back --
                # counting the cell settled here would let assembly run before the data landed
                # and store an empty forecast as a finished one. A duplicate task never fetches
                # twice: get_or_fetch_* takes the cell's fetch lease (core.cell_lease), so it
                # either reads the warm cell or waits for the holder and reads what it stored.
                claim_cell(kind, lat_r, lon_r, day_key, days)
                await cell_task.aenqueue(lat_r, lon_r, day_key, days, str(job.id))

    if with_stations:
        # Counted in cells_total above, so assembly waits for the readings to land.
        await refresh_station_observations.aenqueue(str(job.id))

    logger.info(f"Forecast job {job.id}: {len(cells)} cells, {job.cells_total - job.cells_settled} fetches enqueued")


async def _fail_not_allowed(job: ForecastJob, error: str) -> None:
    """Fail a job its owner may no longer read, dropping the result kept to show meanwhile."""
    job.stale_result = None
    await ForecastJob.objects.filter(pk=job.pk).aupdate(stale_result=None)
    await set_status(job, ForecastJob.Status.FAILED, error=error)


async def _wants_stations(job: ForecastJob, total_seconds: float | None) -> bool:
    """Whether this job should spend Weather Underground calls: a key, a Pro owner, a ride near now.

    Never for a journey stage: journeys are planned ahead, and one plan makes a job per day
    and alternative, which would burn the fail-closed budget (30 calls a minute).
    """
    if job.kind == ForecastJob.Kind.JOURNEY_STAGE:
        return False
    now = datetime.now(tz=UTC)
    if not api_key() or not any(ride_in_window(t, total_seconds, now) for t in departures.candidate_times(job.params)):
        return False
    return (await entitlements_for(job.owner)).station_correction


def _active_stage(job: ForecastJob):
    """Only write back if this stage has not completed, failed, or been restarted."""
    return ForecastJob.objects.filter(id=job.id, status=ForecastJob.Status.ASSEMBLING, updated_at=job.updated_at)


async def _fail_forecast_stage(job: ForecastJob, error: str) -> None:
    if await _active_stage(job).aupdate(
        status=ForecastJob.Status.FAILED, error=error, computed_weather=None, updated_at=datetime.now(tz=UTC)
    ):
        await job.arefresh_from_db()
        telemetry.completed(job, "failed")
        await publish(job)


def _store_computed_weather(job: ForecastJob, computed: dict) -> None:
    # This backend stores queued tasks in the same database: commit the intermediate
    # result and its continuation together, or roll both back if enqueue fails.
    with transaction.atomic():
        won = (
            _active_stage(job)
            .filter(computed_weather__isnull=True)
            .update(computed_weather=computed, updated_at=datetime.now(tz=UTC))
        )
        if won:
            assemble_forecast_job.enqueue(str(job.id))


@task(queue_name="compute")
def compute_route_weather_job(job_id: str) -> None:
    """Compute a forecast from warm cells, then queue payload assembly."""
    async_to_sync(_compute_route_weather_job_async)(job_id)


@telemetry.stage("computation")
async def _compute_route_weather_job_async(job_id: str) -> None:
    job = await ForecastJob.objects.select_related("owner").filter(id=job_id).afirst()
    if job is None or job.status != ForecastJob.Status.ASSEMBLING or job.computed_weather is not None:
        return

    await telemetry.bind_job(job)
    params = await forecast_params_for(job.owner, job.params)
    geometry = job.geometry or {}
    try:
        if geometry.get("sample_points") is None or geometry.get("polyline") is None:
            raise ValueError("Forecast computation requires stored route geometry")
        limits = await entitlements_for(job.owner)
        weather_args = {
            "start_lat": params.get("start_lat", 0.0),
            "start_lon": params.get("start_lon", 0.0),
            "dest_lat": params.get("dest_lat", 0.0),
            "dest_lon": params.get("dest_lon", 0.0),
            "profile": params.get("profile", "bike"),
            "departure_time": params["departure_time"],
            "sample_points": geometry.get("sample_points"),
            "polyline": geometry.get("polyline"),
            "total_seconds": geometry.get("total_seconds"),
            "total_distance_m": geometry.get("total_distance_m"),
            "vertex_times": geometry.get("vertex_times"),
            "interval_seconds": params.get("interval_seconds", SAMPLE_INTERVAL_DEFAULT_S),
            # Every cell this job needs has already been fetched by a `cells` task. Reading
            # through the fetching accessors here would put provider calls back on the path
            # this whole design exists to keep them off.
            "cache_only": True,
            # Computed, not stripped afterwards: a corrected number cannot be un-corrected.
            # The owner is part of the job key, so free and Pro never share a result, and the
            # tier is recorded in it below, so a tier change is never served a stale one.
            "station_correction_enabled": limits.station_correction,
        }

        comparison = None
        times: list[datetime] = []
        if departures.enabled(params):
            times = departures.candidate_times(params)
            windows = departures.fetch_windows(params, geometry["sample_points"], local_today())
            weather_args["snapshot"] = WeatherSnapshot(max(days for _, days in windows))
            # Give the baseline the same elapsed-time semantics as the alternatives.
            weather_args["departure_time"] = departures.local_iso(departures.instant(params["departure_time"]))
        forecast = await compute_route_weather(**weather_args)
        if departures.enabled(params):
            candidates = []
            now = weather_args["snapshot"].now
            for departure in times:
                if (
                    departure < now
                    or not forecast_available_at(departure)
                    or not forecast_available_at(departure + timedelta(seconds=geometry["total_seconds"]))
                ):
                    candidates.append(
                        {
                            "departure_time": departures.local_iso(departure),
                            "arrival_time": departures.local_iso(
                                departure + timedelta(seconds=geometry["total_seconds"])
                            ),
                            "complete": False,
                            "samples": [],
                        }
                    )
                    continue
                candidate = await compute_route_weather(
                    **{
                        **weather_args,
                        "departure_time": departures.local_iso(departure),
                        "strict_coverage": True,
                        "include_segments": False,
                    }
                )
                candidates.append(departures.compact_candidate(departure, candidate, len(geometry["sample_points"])))
            comparison = {
                "requested_time": departures.local_iso(departures.instant(params["departure_time"])),
                "window_start": departures.local_iso(times[0]),
                "window_end": departures.local_iso(times[-1]),
                "candidates": candidates,
            }

        # Stripped before storage, not on read: the stored result is what the WebSocket
        # pushes and what the job endpoint returns, so a free account must never have Pro
        # data sitting in its row.
        if not limits.ensemble_uncertainty:
            strip_uncertainty(forecast.samples)

        if not forecast.samples and job.cells_total:
            await _fail_forecast_stage(job, "Noch keine Wetterdaten verfügbar.")
            return
        await sync_to_async(_store_computed_weather)(
            job,
            {
                "forecast": forecast.model_dump(mode="json"),
                "entitlements": limits.result_marker(),
                "departure_inputs": comparison,
            },
        )
    except Exception:
        await _fail_forecast_stage(job, "Wetterdaten konnten nicht berechnet werden.")
        raise


@task(queue_name="forecasts")
def assemble_forecast_job(job_id: str) -> None:
    """Add sections to a persisted computation and publish the result."""
    async_to_sync(_assemble_forecast_job_async)(job_id)


@telemetry.stage("assembly")
async def _assemble_forecast_job_async(job_id: str) -> None:
    job = await ForecastJob.objects.select_related("owner").filter(id=job_id).afirst()
    if job is None or job.status != ForecastJob.Status.ASSEMBLING:
        return

    await telemetry.bind_job(job)
    params = job.params
    try:
        computed = job.computed_weather
        if not isinstance(computed, dict):
            raise TypeError("Forecast assembly requires computed weather")
        limits = await entitlements_for(job.owner)
        if computed["entitlements"] != limits.result_marker():
            await _fail_forecast_stage(job, "Berechtigungen geändert. Bitte Wetter erneut laden.")
            return
        forecast = RouteWeatherOut.model_validate(computed["forecast"])
        payload = forecast.model_dump(mode="json")
        payload["sections"] = [
            section.model_dump(mode="json") for section in compute_sections(forecast.samples, forecast.total_distance_m)
        ]
        if job.kind == ForecastJob.Kind.ROUTE:
            payload["route_id"] = params["route_id"]
        if job.kind == ForecastJob.Kind.JOURNEY_STAGE:
            payload["journey_stage_id"] = params["journey_stage_id"]
        payload["elevation_geometry"] = {
            key: (job.geometry or {}).get(key) for key in ("vertex_times", "vertex_elevations")
        }
        payload["departure_time"] = params["departure_time"]
        payload["entitlements"] = computed["entitlements"]
        if computed.get("departure_inputs"):
            payload["departure_inputs"] = computed["departure_inputs"]
        now = datetime.now(tz=UTC)
        # What the stale-result age cap and the page's "Stand" read: updated_at moves on restart.
        payload["computed_at"] = now.isoformat()

        won = await _active_stage(job).aupdate(
            result=payload,
            stale_result=None,
            status=ForecastJob.Status.DONE,
            error="",
            computed_weather=None,
            updated_at=now,
        )
    except Exception:
        await _fail_forecast_stage(job, "Wetterdaten konnten nicht zusammengestellt werden.")
        raise

    if won:
        await job.arefresh_from_db()
        await publish(job)
        telemetry.completed(job, "success")
        logger.info(f"Forecast job {job.id} done ({len(forecast.samples)} samples)")


async def start_forecast_job(kind: str, owner, params: dict, *, min_remaining: timedelta = timedelta(0)) -> ForecastJob:
    """Find or start the job for a request, enqueueing planning only when it is new."""
    if kind == ForecastJob.Kind.ROUTE:
        route = await RecurringRoute.objects.only("geometry_fetched_at").filter(id=params["route_id"]).afirst()
        revision = route.geometry_fetched_at.isoformat() if route and route.geometry_fetched_at else None
        params = {**params, "geometry_revision": revision}
    if kind == ForecastJob.Kind.JOURNEY_STAGE:
        stage = await JourneyStage.objects.only("geometry_fetched_at").filter(id=params["journey_stage_id"]).afirst()
        params = {**params, "geometry_revision": stage.geometry_fetched_at.isoformat() if stage else None}
    job, needs_planning = await get_or_start_job(kind, owner, params, min_remaining=min_remaining)
    if needs_planning:
        await plan_forecast_job.aenqueue(str(job.id))
    return job


STRIPE_EVENT_RETENTION = timedelta(days=30)

# A finished job is only reusable while its cells are (MAX_CELL_AGE, 2 h); a day of slack
# keeps recent ones around for debugging without letting the table grow without bound.
FORECAST_JOB_RETENTION = timedelta(days=1)

# How long after a route scan its thumbnail is rebuilt, leaving the cell fetches it just
# enqueued time to land.
THUMBNAIL_DELAY = timedelta(minutes=2)

# The hourly pass also builds the finished forecast for a route's next departure, so opening
# the route returns it at once. Only for departures this close...
PREBUILD_HORIZON = timedelta(hours=48)
# ...on routes the owner opened this recently (RecurringRoute.last_viewed_at)...
PREBUILD_VIEWED_WITHIN = timedelta(days=14)
# ...and a job is rebuilt once less than this is left of its lifetime: one pass interval
# (run_forecast_scheduler runs hourly) plus slack, so it cannot expire between passes.
PREBUILD_MIN_REMAINING = timedelta(minutes=75)
# Gap between pre-builds. `compute` and `forecasts` each run one worker process, so a burst
# of builds would queue a user's own forecast behind all of them. Past ~120 routes the tail
# runs into the next pass, which enqueues those again; the job lookup makes that a no-op.
PREBUILD_STAGGER = timedelta(seconds=30)


def _purge_processed_events() -> int:
    """Drop webhook ids older than Stripe could possibly retry."""
    cutoff = datetime.now(tz=UTC) - STRIPE_EVENT_RETENTION
    return ProcessedStripeEvent.objects.filter(received_at__lt=cutoff).delete()[0]


def _purge_expired_jobs() -> int:
    """Drop forecast jobs nobody can still be waiting on."""
    cutoff = datetime.now(tz=UTC) - FORECAST_JOB_RETENTION
    return ForecastJob.objects.filter(updated_at__lt=cutoff).delete()[0]


@task()
def refresh_upcoming_forecasts() -> dict:
    """Fan the pre-warm pass out to one scan task per eligible route, then run maintenance.

    Returns counts of enqueued scans and purged rows.
    """
    return async_to_sync(_refresh_upcoming_forecasts_async)()


async def _prewarm_routes(owner_id: int | None = None) -> list[RecurringRoute]:
    """Only entitled briefing routes departing within four hours receive background work."""
    selected: list[RecurringRoute] = []
    query = (
        RecurringRoute.objects.filter(active=True, owner__isnull=False)
        .exclude(briefing_channel="")
        .select_related("owner")
    )
    if owner_id is not None:
        query = query.filter(owner_id=owner_id)
    routes = [route async for route in query]
    eligible_by_owner = await sync_to_async(briefing_route_ids_by_owner)([route.owner_id for route in routes])
    now = datetime.now(tz=UTC)
    for route in routes:
        departure = next_departure(route.schedule_cron)
        if route.id in eligible_by_owner[route.owner_id] and departure and departure - now <= timedelta(hours=4):
            selected.append(route)
    return selected


async def _enqueue_scans(routes: list[RecurringRoute]) -> int:
    """One scan task per route that has geometry to scan; returns how many were enqueued."""
    scanned = 0
    for route in routes:
        if not route.sample_points:
            continue
        await scan_route_forecasts.aenqueue(str(route.id))
        scanned += 1
    return scanned


async def _enqueue_prebuilds(routes: list[RecurringRoute]) -> int:
    """One pre-build task per route the owner opened recently, spaced PREBUILD_STAGGER apart.

    ``routes`` comes from `_prewarm_routes`, so the tier quota already applies. Whether the
    next departure is close enough is decided in the task, when it runs.
    """
    now = datetime.now(tz=UTC)
    enqueued = 0
    for route in routes:
        if (
            not route.sample_points
            or route.last_viewed_at is None
            or now - route.last_viewed_at > PREBUILD_VIEWED_WITHIN
        ):
            continue
        await prebuild_route_forecast.using(run_after=now + enqueued * PREBUILD_STAGGER).aenqueue(str(route.id))
        enqueued += 1
    return enqueued


@task()
def prebuild_route_forecast(route_id: str) -> dict:
    """Build the finished forecast for a route's next departure before anyone asks for it."""
    return async_to_sync(_prebuild_route_forecast_async)(route_id)


async def _prebuild_route_forecast_async(route_id: str) -> dict:
    route = await (
        RecurringRoute.objects.select_related("owner").defer("polyline", "thumbnail").filter(id=route_id).afirst()
    )
    if route is None or not route.active or not route.sample_points or route.owner is None:
        return {"built": False, "reason": "not eligible"}

    if route.id not in await sync_to_async(briefing_route_ids)(route.owner):
        return {"built": False, "reason": "not entitled"}
    now = datetime.now(tz=UTC)
    departure = next_departure(route.schedule_cron)
    if departure is None or departure - now > PREBUILD_HORIZON or not forecast_available_at(departure):
        return {"built": False, "reason": "no departure soon"}

    # isoformat() of the same next_departure the route API serves as `nextDeparture`: the
    # page splits that string into date and time, and the endpoint joins them back, so both
    # sides hash the same departure_time and land on the same job.
    params = departures.route_job_params(
        route.id, departure.isoformat(), route.departure_flex_before_minutes, route.departure_flex_after_minutes
    )

    # Planning would spend Weather Underground calls on a ride this close to now, for a job
    # that only lives STATION_JOB_LIFETIME. The background pass never fetches stations.
    if (
        api_key()
        and any(ride_in_window(t, route.total_seconds, now) for t in departures.candidate_times(params))
        and (await entitlements_for(route.owner)).station_correction
    ):
        return {"built": False, "reason": "station window"}

    job = await start_forecast_job(ForecastJob.Kind.ROUTE, route.owner, params, min_remaining=PREBUILD_MIN_REMAINING)
    logger.debug(f"prebuild_route_forecast({route.name}): job {job.id} {job.status}")
    return {"built": True, "job_id": str(job.id)}


@task()
def refresh_user_forecasts(user_id: int) -> dict:
    """Check one account's routes for forecasts to refresh, right after it signs in.

    The hourly pass can leave a route's cells up to an hour past MAX_CELL_AGE; scanning at
    sign-in means the list and the first forecast the user opens are usually warm already.
    Cheap to repeat: a scan skips warm cells and the claims deduplicate the rest.
    """
    return async_to_sync(_refresh_user_forecasts_async)(user_id)


async def _refresh_user_forecasts_async(user_id: int) -> dict:
    routes = await _prewarm_routes(owner_id=user_id)
    scanned = await _enqueue_scans(routes)
    prebuilds = await _enqueue_prebuilds(routes)
    logger.debug(f"refresh_user_forecasts({user_id}): {scanned} route scans, {prebuilds} pre-builds enqueued")
    return {"routes": scanned, "prebuilds": prebuilds}


async def _refresh_upcoming_forecasts_async() -> dict:
    """Hand each eligible route its own scan task, then run maintenance.

    The scan itself used to walk every route's sample points inline, so one slow route held
    up the whole pass and none of it ever reached the queue.
    """
    routes = await _prewarm_routes()
    scanned = await _enqueue_scans(routes)
    prebuilds = await _enqueue_prebuilds(routes)

    # Both ledgers only exist to reject repeats and to answer polls; without a purge they
    # grow forever.
    purged = await sync_to_async(_purge_processed_events)()
    jobs_purged = await sync_to_async(_purge_expired_jobs)()
    stations_purged = await sync_to_async(purge_station_data)()

    logger.info(
        f"refresh_upcoming_forecasts: {scanned} route scans enqueued, {prebuilds} pre-builds enqueued, "
        f"{purged} stripe events purged, {jobs_purged} forecast jobs purged, "
        f"{stations_purged} station rows purged"
    )
    return {
        "routes": scanned,
        "prebuilds": prebuilds,
        "stripe_events_purged": purged,
        "forecast_jobs_purged": jobs_purged,
        "station_rows_purged": stations_purged,
    }


@task()
def scan_route_forecasts(route_id: str) -> dict:
    """Enqueue the grid cells one route's upcoming departures will need."""
    return async_to_sync(_scan_route_forecasts_async)(route_id)


async def _scan_route_forecasts_async(route_id: str) -> dict:
    route = await RecurringRoute.objects.filter(id=route_id).afirst()
    if route is None or not route.sample_points:
        return {"cells_enqueued": 0, "ensembles_enqueued": 0}
    owner = await sync_to_async(lambda: route.owner)()
    if owner is None or route.id not in await sync_to_async(briefing_route_ids)(owner):
        return {"cells_enqueued": 0, "ensembles_enqueued": 0}

    now = datetime.now(tz=UTC)
    today = local_today()
    cells = _cell_set(route.sample_points)

    cell_count = 0
    ensemble_count = 0
    for dep in upcoming_departures(route.schedule_cron, count=1, after=now):
        if dep - now > timedelta(hours=4) or not forecast_available_at(dep):
            continue

        params = {
            "departure_time": dep.isoformat(),
            "departure_flex_before_minutes": route.departure_flex_before_minutes,
            "departure_flex_after_minutes": route.departure_flex_after_minutes,
        }
        windows = departures.fetch_windows(params, route.sample_points, today)
        warm_forecasts, warm_ensembles = await get_cached_cell_keys(cells, windows)
        for day_key, days in windows:
            day = date.fromisoformat(day_key)
            # No station readings here: they are only good for minutes and this scan runs
            # hourly, so it would spend the Weather Underground budget on data nobody reads.
            for lat_r, lon_r in cells:
                for kind, warm_keys, cell_task in (
                    ("forecast", warm_forecasts, refresh_forecast_cell),
                    ("ensemble", warm_ensembles, refresh_ensemble_cell),
                ):
                    if (lat_r, lon_r, day) in warm_keys:
                        continue
                    # The claim is what stops the same cell being enqueued once per sample
                    # point, once per route sharing it, and again by a live request.
                    if not claim_cell(kind, lat_r, lon_r, day_key, days):
                        continue
                    await cell_task.aenqueue(lat_r, lon_r, day_key, days)
                    if kind == "forecast":
                        cell_count += 1
                    else:
                        ensemble_count += 1

    # Deferred rather than simply enqueued last: several `cells` workers run in parallel, so
    # queue position no longer implies completion order. The "don't regress a good glyph to
    # grey" guard in _refresh_route_thumbnail_async covers a pass that still runs early.
    await refresh_route_thumbnail.using(run_after=now + THUMBNAIL_DELAY).aenqueue(str(route.id))

    logger.debug(f"scan_route_forecasts({route.name}): {cell_count} cells, {ensemble_count} ensembles enqueued")
    return {"cells_enqueued": cell_count, "ensembles_enqueued": ensemble_count}


# --------------------------------------------------------------------------- journeys
# plan_journey cuts the journey into days; plan_journey_routes routes each day (around the
# weather when it is close enough), fills POI gaps and places breaks. Between them the
# corridor cells for weather-routed days are fetched by ordinary cell tasks; the second task
# re-defers itself until they are warm or its attempts run out, the pattern plan_forecast_job
# uses for missing geometry. On `default`: a plan makes tens of GraphHopper calls and must
# not hold up the `forecasts` queue, where someone is waiting on a single forecast.

# Days closer than this are routed around rain and headwind; further out, a shower's
# position is not worth steering by.
WEATHER_ROUTING_DAYS = 3
CORRIDOR_RETRY_DELAY = timedelta(seconds=20)
MAX_CORRIDOR_ATTEMPTS = 6
# Rounds of "route, read the weather at the new etas, route again".
WEATHER_ROUTING_ROUNDS = 2
MAX_JOURNEY_DAYS = 14


def _journey_is_current(journey_id, revision: int):
    return Journey.objects.filter(id=journey_id, plan_revision=revision)


async def _set_plan_status(journey_id, revision: int, status: str, error: str = "", **fields) -> bool:
    return bool(await _journey_is_current(journey_id, revision).aupdate(plan_status=status, plan_error=error, **fields))


def _day_seconds(journey: Journey) -> float:
    """The riding a day allows: the time limit, but never past the day's window."""
    window = (
        datetime.combine(date.min, journey.latest_arrival) - datetime.combine(date.min, journey.earliest_start)
    ).total_seconds()
    limits = [s for s in (journey.max_day_seconds, window if window > 0 else None) if s]
    return min(limits) if limits else 0


def _day_departure(journey: Journey, day: date) -> datetime:
    return datetime.combine(day, journey.earliest_start, tzinfo=LOCAL_TZ)


def _wants_weather_routing(journey: Journey, limits, day: date) -> bool:
    prefs = journey.weather_prefs or {}
    if not limits.weather_routing or not (prefs.get("avoid_rain", True) or prefs.get("avoid_headwind", True)):
        return False
    return 0 <= (day - local_today()).days < WEATHER_ROUTING_DAYS


async def _pois_along(coordinates, categories, corridor_m):
    return await sync_to_async(pois_along_sync)(coordinates, list(categories), corridor_m)


@task()
def plan_journey(journey_id: str, revision: int) -> None:
    """Cut a journey into days, then hand over to routing each day."""
    async_to_sync(_plan_journey_async)(journey_id, revision)


async def _plan_journey_async(journey_id: str, revision: int) -> None:

    journey = await Journey.objects.select_related("owner").filter(id=journey_id, plan_revision=revision).afirst()
    if journey is None:
        return
    if not await _set_plan_status(journey_id, revision, Journey.PlanStatus.ROUTING):
        return
    limits = await entitlements_for(journey.owner)
    road_model = road_prefs_model(RoadPrefs.from_json(journey.road_prefs)) or None
    budget = RoutingBudget()
    began = monotonic()
    try:
        days = await _plan_day_ends(journey, limits, road_model, budget)
    except JourneyPlanningError as exc:
        await _set_plan_status(journey_id, revision, Journey.PlanStatus.FAILED, str(exc))
        return
    except ROUTING_ERRORS as exc:
        logger.info(f"Journey {journey_id}: no route: {exc}")
        await _set_plan_status(
            journey_id, revision, Journey.PlanStatus.FAILED, "Für diese Reise wurde keine Route gefunden."
        )
        return
    except Exception:
        await _set_plan_status(
            journey_id, revision, Journey.PlanStatus.FAILED, "Die Reise konnte nicht geplant werden."
        )
        raise
    finally:
        _log_journey_routing(journey_id, budget, began)
    if days is None:
        return
    state = {"days": days, "route_requests": budget.used}
    if not await _journey_is_current(journey_id, revision).aupdate(plan_state=state, plan_attempts=0):
        return

    enqueued = 0
    for day in days:
        if not await _journey_is_current(journey_id, revision).aexists():
            return
        if day["weather"]:
            enqueued += await _enqueue_corridor_cells(journey, day, road_model)
    if not await _journey_is_current(journey_id, revision).aexists():
        return
    if enqueued:
        if not await _set_plan_status(journey_id, revision, Journey.PlanStatus.WEATHER):
            return
        await plan_journey_routes.using(run_after=datetime.now(tz=UTC) + CORRIDOR_RETRY_DELAY).aenqueue(
            journey_id, revision
        )
    else:
        await plan_journey_routes.aenqueue(journey_id, revision)


class JourneyPlanningError(Exception):
    """A valid request for which no progressing day plan can be made."""


def _planner(journey, model, budget):
    return JourneyPlanner(journey.profile, model, budget, build_geometry, route_legs, _pois_along)


def _day_points(day):
    return (tuple(day["start"]), *(tuple(p) for p in day.get("vias", [])), tuple(day["end"]))


def _log_journey_routing(journey_id, budget, began):
    logger.info(
        "Journey {}: optional_requests={} essential_requests={} failures={} exhausted={} duration_s={:.2f}",
        journey_id,
        budget.used,
        budget.essential,
        budget.failures,
        budget.exhausted,
        monotonic() - began,
    )


async def _plan_day_ends(journey, entitlements, model, budget):
    planner = _planner(journey, model, budget)
    pending = list(journey.via_points or [])
    remainder, indices = await planner.route(journey.routing_points)
    start = list(journey.routing_points[0])
    destination = list(journey.routing_points[-1])
    day_limits = Limits(_day_seconds(journey), journey.max_day_distance_m)
    days = []
    while True:
        if not await _journey_is_current(journey.id, journey.plan_revision).aexists():
            return None
        if len(days) >= MAX_JOURNEY_DAYS:
            raise JourneyPlanningError(f"Mehr als {MAX_JOURNEY_DAYS} Tage: bitte längere Tagesetappen wählen.")
        measure = LineMeasure(remainder)
        last = day_limits.scaled(1 + LAST_DAY_SLACK).allows(*measure.between(0, -1))
        lodging, lodging_detour = None, None
        if last:
            end, day_vias = destination, pending
        else:
            boundary = measure.boundary(0, day_limits)
            if measure.meters[boundary] < 1:
                raise JourneyPlanningError("Das Tageslimit erlaubt keine Fahrt.")
            events = [
                {"index": idx, "point": point, "mandatory": True}
                for point, idx in zip(pending, indices[1:-1], strict=True)
            ]
            hits = lodging_candidates(
                await planner.window_hits(
                    remainder,
                    measure.index(measure.meters[boundary] * (1 - LODGING_WINDOW)),
                    boundary,
                    ["lodging"],
                    LODGING_CORRIDOR_M,
                ),
                journey.lodging_kinds or [],
            )
            hits = [h for h in hits if h.along_m >= measure.meters[boundary] * (1 - LODGING_WINDOW)]
            choices = await planner.candidates(remainder, events, hits, 0, boundary, day_limits)
            accepted = None
            for choice in [*choices, None]:
                if choice is not None:
                    inserted = await planner.insert(remainder, events, choice, 0, day_limits)
                    if not inserted:
                        continue
                    proposal, mapped, stop = inserted
                    endpoint, at = stop["point"], stop["index"]
                else:
                    proposal, mapped, at = remainder, events, boundary
                    endpoint = proposal["polyline"][at][:2]
                # Partition from the actual tentative visit order; mutate nothing yet.
                consumed = [e["point"] for e in mapped if e.get("mandatory") and e["index"] <= at]
                still_pending = [e["point"] for e in mapped if e.get("mandatory") and e["index"] > at]
                try:
                    next_geometry, next_indices = await planner.route([endpoint, *still_pending, destination])
                except ROUTING_ERRORS:
                    budget.failures += 1
                    if choice is None:
                        raise
                    continue
                progress = LineMeasure(proposal).meters[at]
                if progress < 1 or LineMeasure(next_geometry).meters[-1] > measure.meters[-1] - 1:
                    continue
                accepted = endpoint, consumed, still_pending, next_geometry, next_indices
                if choice is not None:
                    lodging = stop["pois"][0]
                    lodging_detour = {"s": lodging["detour_s"], "m": lodging["detour_m"]}
                break
            if accepted is None:
                raise JourneyPlanningError("Das Tageslimit erlaubt keine Fahrt.")
            end, day_vias, next_pending, next_geometry, next_indices = accepted
        day_date = journey.start_date + timedelta(days=len(days))
        days.append(
            {
                "index": len(days),
                "date": day_date.isoformat(),
                "start": start,
                "end": end,
                "vias": day_vias,
                "lodging": lodging,
                "lodging_detour": lodging_detour,
                "lodging_missing": not last and lodging is None,
                "last_day": last,
                "weather": _wants_weather_routing(journey, entitlements, day_date),
            }
        )
        if last:
            return days
        start, pending, remainder, indices = end, next_pending, next_geometry, next_indices


async def _day_corridor(journey: Journey, day: dict, model: dict | None):
    """The day routed as it stands, and the corridor cells around it with their window."""

    points = _day_points(day)
    geometry = await build_geometry(journey.profile, points, SAMPLE_INTERVAL_DEFAULT_S, model)
    departure = _day_departure(journey, date.fromisoformat(day["date"]))
    days = forecast_days_for(departure, geometry["sample_points"], local_today())
    return geometry, corridor_cells(geometry["sample_points"]), departure, day["date"], days


async def _enqueue_corridor_cells(journey: Journey, day: dict, model: dict | None) -> int:
    """Fetch the day's corridor cells; returns how many are still cold."""
    try:
        _, cells, _, day_key, days = await _day_corridor(journey, day, model)
    except ROUTING_ERRORS:
        return 0
    warm, _ = await get_cached_cell_keys(list(cells), [(day_key, days)])
    cold = [key for key in cells if (key[0], key[1], date.fromisoformat(day_key)) not in warm]
    for lat_r, lon_r in cold:
        # No job id: nobody counts these. The routing task checks the cache instead.
        if claim_cell("forecast", lat_r, lon_r, day_key, days):
            await refresh_forecast_cell.aenqueue(lat_r, lon_r, day_key, days)
    return len(cold)


@task()
def plan_journey_routes(journey_id: str, revision: int) -> None:
    """Route each day of a cut journey into its stages."""
    async_to_sync(_plan_journey_routes_async)(journey_id, revision)


async def _plan_journey_routes_async(journey_id: str, revision: int) -> None:

    journey = await Journey.objects.select_related("owner").filter(id=journey_id, plan_revision=revision).afirst()
    if journey is None or not journey.plan_state:
        return
    limits = await entitlements_for(journey.owner)
    road_model = road_prefs_model(RoadPrefs.from_json(journey.road_prefs)) or None
    days = journey.plan_state["days"]

    # Wait for the corridor cells, within bounds: a cell that never arrives only costs its
    # zone, never the plan.
    if journey.plan_attempts < MAX_CORRIDOR_ATTEMPTS:
        cold = 0
        for day in days:
            if day["weather"]:
                try:
                    _, cells, _, day_key, forecast_days = await _day_corridor(journey, day, road_model)
                except ROUTING_ERRORS:
                    continue
                warm, _ = await get_cached_cell_keys(list(cells), [(day_key, forecast_days)])
                cold += sum((k[0], k[1], date.fromisoformat(day_key)) not in warm for k in cells)
        if cold:
            if await _journey_is_current(journey_id, revision).aupdate(plan_attempts=F("plan_attempts") + 1):
                await plan_journey_routes.using(run_after=datetime.now(tz=UTC) + CORRIDOR_RETRY_DELAY).aenqueue(
                    journey_id, revision
                )
            return

    if not await _set_plan_status(journey_id, revision, Journey.PlanStatus.ROUTING):
        return
    budget = RoutingBudget(used=journey.plan_state.get("route_requests", 0))
    began = monotonic()
    try:
        planned = []
        for index, day in enumerate(days):
            if not await _journey_is_current(journey_id, revision).aexists():
                return
            planned.append(
                await _plan_day(journey, {**day, "last_day": index == len(days) - 1}, limits, road_model, budget)
            )
    except ROUTING_ERRORS as exc:
        logger.info(f"Journey {journey_id}: a day could not be routed: {exc}")
        await _set_plan_status(
            journey_id, revision, Journey.PlanStatus.FAILED, "Eine Tagesetappe konnte nicht berechnet werden."
        )
        return
    except Exception:
        await _set_plan_status(
            journey_id, revision, Journey.PlanStatus.FAILED, "Die Reise konnte nicht geplant werden."
        )
        raise
    finally:
        _log_journey_routing(journey_id, budget, began)
    await sync_to_async(_store_journey_plan)(journey_id, revision, planned)


async def _weather_model(journey: Journey, day: dict, road_model: dict | None) -> dict | None:
    """Route the day, read the weather at its etas, route around it; repeat once."""

    prefs = journey.weather_prefs or {}
    geometry, cells, departure, day_key, forecast_days = await _day_corridor(journey, day, road_model)
    model = road_model
    for _ in range(WEATHER_ROUTING_ROUNDS):
        weather = await cell_weather(cells, departure, day_key, forecast_days)
        zones = zone_model(
            weather, avoid_rain=prefs.get("avoid_rain", True), avoid_headwind=prefs.get("avoid_headwind", True)
        )
        model = merge_models(road_model or {}, zones) or None
        if not zones:
            break
        rerouted = await build_geometry(journey.profile, _day_points(day), SAMPLE_INTERVAL_DEFAULT_S, model)
        if abs(rerouted["total_distance_m"] - geometry["total_distance_m"]) < 0.01 * geometry["total_distance_m"]:
            break
        geometry, cells = rerouted, corridor_cells(rerouted["sample_points"])
    return model


async def _plan_day(journey: Journey, day: dict, limits, road_model: dict | None, budget=None) -> dict:
    model = await _weather_model(journey, day, road_model) if day["weather"] else road_model
    planner = _planner(journey, model, budget if budget is not None else RoutingBudget())
    points = _day_points(day)
    events = []
    if day.get("vias"):
        path, indices = await planner.route(points)
        events = [{"index": i, "point": p, "mandatory": True} for p, i in zip(day["vias"], indices[1:-1], strict=True)]
        paths = [path]
    else:
        try:
            paths = await build_geometries(
                journey.profile, points, limits.max_journey_alternatives, SAMPLE_INTERVAL_DEFAULT_S, model
            )
        except ROUTING_ERRORS:
            paths = [await build_geometry(journey.profile, points, SAMPLE_INTERVAL_DEFAULT_S, model)]
    stages = []
    seen = set()
    for rank, path in enumerate(paths):
        if not await _journey_is_current(journey.id, journey.plan_revision).aexists():
            break
        stage = await planner.stage(
            path,
            [dict(e) for e in events],
            list(journey.poi_categories or []),
            Limits(journey.max_leg_seconds, journey.max_leg_distance_m),
            Limits(_day_seconds(journey), journey.max_day_distance_m),
            last_day=day.get("last_day", False),
        )
        signature = tuple(tuple(p) for p in stage["geometry"]["polyline"])
        if signature not in seen:
            seen.add(signature)
            stages.append({**stage, "rank": rank})
    compliant = [s for s in stages if not s["limit_overruns"].get("day")]
    return {**day, "stages": compliant or stages}


def _store_journey_plan(journey_id: str, revision: int, planned: list[dict]) -> None:
    """Replace the journey's days in one transaction, unless the plan is no longer current."""
    now = datetime.now(tz=UTC)
    with transaction.atomic():
        journey = Journey.objects.select_for_update().filter(id=journey_id, plan_revision=revision).first()
        if journey is None:
            return
        journey.days.all().delete()
        for day in planned:
            row = JourneyDay.objects.create(
                journey=journey,
                index=day["index"],
                date=date.fromisoformat(day["date"]),
                start=day["start"],
                end=day["end"],
                lodging=day["lodging"],
                lodging_detour=day.get("lodging_detour"),
                lodging_missing=day["lodging_missing"],
                weather_routed=day["weather"],
            )
            for stage in day["stages"]:
                geometry = stage["geometry"]
                JourneyStage.objects.create(
                    day=row,
                    rank=stage["rank"],
                    via_points=stage["via_points"],
                    polyline=route_line(geometry["polyline"]),
                    total_seconds=geometry["total_seconds"],
                    total_distance_m=geometry["total_distance_m"],
                    sample_points=geometry["sample_points"],
                    vertex_times=geometry["vertex_times"],
                    vertex_elevations=geometry.get("vertex_elevations"),
                    geometry_fetched_at=now,
                    breaks=stage["breaks"],
                    gaps=stage["gaps"],
                    detours=stage["detours"],
                    detour_m=stage["detour_m"],
                    leg_m=stage["leg_m"],
                    leg_seconds=stage.get("leg_seconds", 0),
                    limit_overruns=stage.get("limit_overruns", {}),
                )
        journey.plan_status = Journey.PlanStatus.DONE
        journey.plan_error = ""
        journey.planned_at = now
        journey.save(update_fields=["plan_status", "plan_error", "planned_at", "updated_at"])
