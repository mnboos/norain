"""django-tasks task definitions for NoRain.

Background tasks for route geometry computation and forecast grid pre-warming.
"""

from datetime import UTC, datetime, timedelta

from asgiref.sync import async_to_sync, sync_to_async
from django.db import transaction
from django.db.models import F
from loguru import logger

from core import departures, telemetry
from core.claims import claim_cell, release_cell
from core.entitlements import entitlements_for, entitlements_for_sync, strip_uncertainty
from core.forecast_schemas import RouteWeatherOut
from core.grid import (
    get_cached_ensemble_cell,
    get_cached_forecast_cell,
    get_or_fetch_ensemble_cell,
    get_or_fetch_forecast_cell,
)
from core.jobs import MAX_PLAN_ATTEMPTS, PLAN_RETRY_DELAY, get_or_start_job, publish, set_status
from core.models import ForecastJob, ProcessedStripeEvent, RecurringRoute, route_line
from core.plotting import generate_forecast_figures
from core.schedule import forecast_available_at, local_today, next_departure, upcoming_departures
from core.sections import compute_sections
from core.stations import api_key, purge_station_data, refresh_stations_for_ride, ride_in_window
from core.thumbnails import compute_route_thumbnail
from core.tracing import traced_task as task
from core.weather import (  # reuse existing functions
    ROUTING_ERRORS,
    SAMPLE_INTERVAL_DEFAULT_S,
    WeatherSnapshot,
    build_geometry,
    compute_route_weather,
)
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
        geometry = await build_geometry(
            route.profile,
            route.start_lat,
            route.start_lon,
            route.dest_lat,
            route.dest_lon,
            SAMPLE_INTERVAL_DEFAULT_S,
        )
    except ROUTING_ERRORS as e:
        # The old geometry stays in place; the next edit or backfill tries again.
        logger.error(f"Failed to fetch route geometry for {route.name}: {e}")
        return

    sample_points = geometry["sample_points"]
    route.polyline = route_line(geometry["polyline"])
    route.total_seconds = geometry["total_seconds"]
    route.total_distance_m = geometry["total_distance_m"]
    route.sample_points = sample_points
    route.vertex_times = geometry["vertex_times"]
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


@task(queue_name="cells")
def refresh_forecast_cell(
    lat_r: float, lon_r: float, day_key: str, forecast_days: int, job_id: str | None = None
) -> None:
    """Pre-fetch a single grid cell's deterministic weather and store it."""
    async_to_sync(_refresh_forecast_cell_async)(lat_r, lon_r, day_key, forecast_days, job_id)


async def _refresh_forecast_cell_async(
    lat_r: float, lon_r: float, day_key: str, forecast_days: int, job_id: str | None = None
) -> None:
    stored = False
    try:
        cell = await get_or_fetch_forecast_cell(lat_r, lon_r, day_key, forecast_days)
        stored = bool(cell)
        if stored:
            logger.debug(f"Forecast cell refreshed: ({lat_r}, {lon_r}, {day_key})")
        else:
            logger.warning(f"Forecast cell NOT stored: ({lat_r}, {lon_r}, {day_key}, days={forecast_days})")
    finally:
        # Released on failure too: a cell both providers refused must stay claimable, or
        # one bad fetch would block every retry for the whole claim TTL.
        release_cell("forecast", lat_r, lon_r, day_key, forecast_days)
        await _settle_cell(job_id, failed=not stored)


@task(queue_name="cells")
def refresh_ensemble_cell(
    lat_r: float, lon_r: float, day_key: str, forecast_days: int, job_id: str | None = None
) -> None:
    """Pre-fetch a single grid cell's ensemble data and store it."""
    async_to_sync(_refresh_ensemble_cell_async)(lat_r, lon_r, day_key, forecast_days, job_id)


async def _refresh_ensemble_cell_async(
    lat_r: float, lon_r: float, day_key: str, forecast_days: int, job_id: str | None = None
) -> None:
    stored = False
    try:
        cell = await get_or_fetch_ensemble_cell(lat_r, lon_r, day_key, forecast_days)
        stored = bool(cell)
        if stored:
            logger.debug(f"Ensemble cell refreshed: ({lat_r}, {lon_r}, {day_key})")
        else:
            logger.warning(f"Ensemble cell NOT stored: ({lat_r}, {lon_r}, {day_key}, days={forecast_days})")
    finally:
        release_cell("ensemble", lat_r, lon_r, day_key, forecast_days)
        await _settle_cell(job_id, failed=not stored)


@task(queue_name="cells")
def refresh_station_observations(job_id: str) -> None:
    """Fetch recent weather-station readings near the part of a job's ride that is close to now."""
    async_to_sync(_refresh_station_observations_async)(job_id)


async def _refresh_station_observations_async(job_id: str) -> None:
    try:
        job = await ForecastJob.objects.filter(id=job_id).afirst()
        if job is not None and job.geometry:
            now = datetime.now(tz=UTC)
            times = departures.candidate_times(job.params)
            points = job.geometry["sample_points"]
            if departures.enabled(job.params):
                from core.stations import STATION_HORIZON
                points = [{**sp, "elapsed_s": 0} for sp in points if any(
                    abs(t + timedelta(seconds=sp["elapsed_s"]) - now) < STATION_HORIZON for t in times
                )]
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
            "total_seconds": route.total_seconds,
            "total_distance_m": route.total_distance_m,
        }

    return await build_geometry(
        params["profile"],
        params["start_lat"],
        params["start_lon"],
        params["dest_lat"],
        params["dest_lon"],
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

    sample_points = geometry["sample_points"]
    windows = departures.fetch_windows(job.params, sample_points, local_today())
    cells = _cell_set(sample_points)
    with_stations = await _wants_stations(job, geometry.get("total_seconds"))

    # Both counters and the status must be committed before the first task is enqueued: a
    # `cells` worker is fast enough to settle a cell while this function is still running,
    # and a settle against cells_total=0 would hand the job to assembly with no data.
    job.geometry = geometry
    # deterministic + ensemble per cell per window, plus one unit for the station fetch.
    job.cells_total = len(cells) * len(windows) * 2 + (1 if with_stations else 0)
    job.cells_settled = 0
    job.cells_failed = 0
    job.status = ForecastJob.Status.FETCHING
    await job.asave(update_fields=["geometry", "cells_total", "cells_settled", "cells_failed", "status", "updated_at"])
    await publish(job)

    if job.cells_total == 0:
        won = await ForecastJob.objects.filter(id=job.id, status=ForecastJob.Status.FETCHING).aupdate(
            status=ForecastJob.Status.ASSEMBLING, updated_at=datetime.now(tz=UTC)
        )
        if won:
            await job.arefresh_from_db()
            await publish(job)
            await compute_route_weather_job.aenqueue(str(job.id))
        return

    settled = 0
    for day_key, days in windows:
        for lat_r, lon_r in cells:
            # Ensemble cells are fetched for every tier: `pop` and `rain_if_wet` are not gated,
            # only the spread is, and the cells are shared between accounts anyway.
            for kind, cached, cell_task in (
                ("forecast", get_cached_forecast_cell, refresh_forecast_cell),
                ("ensemble", get_cached_ensemble_cell, refresh_ensemble_cell),
            ):
                if await cached(lat_r, lon_r, day_key, days) is not None:
                    settled += 1
                    continue
                # Enqueued even when the claim is held elsewhere. The holder is usually the
                # pre-warm scan, whose task carries no job_id and so would never report back --
                # counting the cell settled here would let assembly run before the data landed
                # and store an empty forecast as a finished one. A duplicate task is cheap: by
                # the time it runs the cell is normally warm and get_or_fetch_* just reads it.
                claim_cell(kind, lat_r, lon_r, day_key, days)
                await cell_task.aenqueue(lat_r, lon_r, day_key, days, str(job.id))

    if with_stations:
        # Counted in cells_total above, so assembly waits for the readings to land.
        await refresh_station_observations.aenqueue(str(job.id))

    logger.info(f"Forecast job {job.id}: {len(cells)} cells, {job.cells_total - settled} fetches enqueued")

    for _ in range(settled):
        await _settle_cell(str(job.id))


async def _wants_stations(job: ForecastJob, total_seconds: float | None) -> bool:
    """Whether this job should spend Weather Underground calls: a key, a Pro owner, a ride near now."""
    now = datetime.now(tz=UTC)
    if not api_key() or not any(
        ride_in_window(t, total_seconds, now) for t in departures.candidate_times(job.params)
    ):
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
        won = _active_stage(job).filter(computed_weather__isnull=True).update(
            computed_weather=computed, updated_at=datetime.now(tz=UTC)
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
    params = job.params
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
                if (departure < now or not forecast_available_at(departure)
                        or not forecast_available_at(departure + timedelta(seconds=geometry["total_seconds"]))):
                    candidates.append({
                        "departure_time": departures.local_iso(departure),
                        "arrival_time": departures.local_iso(departure + timedelta(seconds=geometry["total_seconds"])),
                        "complete": False, "samples": [],
                    })
                    continue
                candidate = await compute_route_weather(**{
                    **weather_args, "departure_time": departures.local_iso(departure),
                    "strict_coverage": True, "include_segments": False,
                })
                candidates.append(departures.compact_candidate(departure, candidate, len(geometry["sample_points"])))
            comparison = {
                "requested_time": departures.local_iso(departures.instant(params["departure_time"])),
                "window_start": departures.local_iso(times[0]), "window_end": departures.local_iso(times[-1]),
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
    """Add charts and sections to a persisted computation and publish the result."""
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
        payload["figures"] = generate_forecast_figures(forecast, departure_time=params["departure_time"])
        payload["sections"] = [
            section.model_dump(mode="json") for section in compute_sections(forecast.samples, forecast.total_distance_m)
        ]
        if job.kind == ForecastJob.Kind.ROUTE:
            payload["route_id"] = params["route_id"]
        payload["departure_time"] = params["departure_time"]
        payload["entitlements"] = computed["entitlements"]
        if computed.get("departure_inputs"):
            payload["departure_inputs"] = computed["departure_inputs"]

        won = await _active_stage(job).aupdate(
            result=payload,
            status=ForecastJob.Status.DONE,
            error="",
            computed_weather=None,
            updated_at=datetime.now(tz=UTC),
        )
    except Exception:
        await _fail_forecast_stage(job, "Wetterdaten konnten nicht zusammengestellt werden.")
        raise

    if won:
        await job.arefresh_from_db()
        await publish(job)
        telemetry.completed(job, "success")
        logger.info(f"Forecast job {job.id} done ({len(forecast.samples)} samples)")


async def start_forecast_job(
    kind: str, owner, params: dict, *, min_remaining: timedelta = timedelta(0)
) -> ForecastJob:
    """Find or start the job for a request, enqueueing planning only when it is new."""
    if kind == ForecastJob.Kind.ROUTE:
        route = await RecurringRoute.objects.only("geometry_fetched_at").filter(id=params["route_id"]).afirst()
        revision = route.geometry_fetched_at.isoformat() if route and route.geometry_fetched_at else None
        params = {**params, "geometry_revision": revision}
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
    """Active routes whose owner's tier still entitles them to pre-warming.

    With ``owner_id``, only that account's routes -- the same quota applies, so a sign-in
    never pre-warms a route the hourly pass would skip.

    This is where the Open-Meteo budget is actually spent — a route here fans out one
    fetch per sample point per upcoming departure — so the quota has to be applied at
    this point and not only in the HTTP endpoints.

    An account can only exceed its quota by being downgraded after creating routes. In
    that case keep the oldest `max_routes`: those are the ones the user has relied on
    longest, and the choice is stable between runs, unlike dropping all of them. The extra
    routes stay visible and usable in the UI — they just stop being pre-warmed.
    """
    selected: list[RecurringRoute] = []
    by_owner: dict[int | None, list[RecurringRoute]] = {}
    query = (
        RecurringRoute.objects.filter(active=True)
        .select_related("owner")
        .defer("polyline", "thumbnail")  # large JSON blobs this pass never reads
        .order_by("created_at")
    )
    if owner_id is not None:
        query = query.filter(owner_id=owner_id)
    async for route in query:
        by_owner.setdefault(route.owner_id, []).append(route)

    for route_owner_id, routes in by_owner.items():
        if route_owner_id is None:
            # Ownerless legacy routes belong to nobody and are invisible in the UI; see
            # the claim_routes management command.
            continue
        limits = await sync_to_async(entitlements_for_sync)(routes[0].owner)
        selected.extend(routes if limits.max_routes is None else routes[: limits.max_routes])
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

    now = datetime.now(tz=UTC)
    today = local_today()
    cells = _cell_set(route.sample_points)

    cell_count = 0
    ensemble_count = 0
    for dep in upcoming_departures(route.schedule_cron, count=3, after=now):
        if not forecast_available_at(dep):
            continue

        params = {
            "departure_time": dep.isoformat(),
            "departure_flex_before_minutes": route.departure_flex_before_minutes,
            "departure_flex_after_minutes": route.departure_flex_after_minutes,
        }
        for day_key, days in departures.fetch_windows(params, route.sample_points, today):
            # No station readings here: they are only good for minutes and this scan runs
            # hourly, so it would spend the Weather Underground budget on data nobody reads.
            for lat_r, lon_r in cells:
                for kind, cached, cell_task in (
                    ("forecast", get_cached_forecast_cell, refresh_forecast_cell),
                    ("ensemble", get_cached_ensemble_cell, refresh_ensemble_cell),
                ):
                    if await cached(lat_r, lon_r, day_key, days) is not None:
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
