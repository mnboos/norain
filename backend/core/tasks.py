"""django-tasks task definitions for NoRain.

Background tasks for route geometry computation and forecast grid pre-warming.
"""

from datetime import UTC, datetime, timedelta

from asgiref.sync import async_to_sync, sync_to_async
from django.db.models import F
from django.tasks import task
from loguru import logger

from core.claims import claim_cell, release_cell
from core.entitlements import entitlements_for, entitlements_for_sync, strip_uncertainty
from core.grid import (
    get_cached_ensemble_cell,
    get_cached_forecast_cell,
    get_or_fetch_ensemble_cell,
    get_or_fetch_forecast_cell,
)
from core.jobs import MAX_PLAN_ATTEMPTS, PLAN_RETRY_DELAY, get_or_start_job, publish, set_status
from core.models import ForecastJob, ProcessedStripeEvent, RecurringRoute, route_line
from core.plotting import generate_forecast_figures
from core.schedule import forecast_available_at, local_today, upcoming_departures
from core.sections import compute_sections
from core.stations import api_key, purge_station_data, refresh_stations_for_ride, ride_in_window
from core.thumbnails import compute_route_thumbnail
from core.weather import (  # reuse existing functions
    ROUTING_ERRORS,
    SAMPLE_INTERVAL_DEFAULT_S,
    build_geometry,
    compute_route_weather,
    forecast_days_for,
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
            fresh = await refresh_stations_for_ride(
                job.geometry["sample_points"],
                datetime.fromisoformat(job.params["departure_time"]),
                datetime.now(tz=UTC),
            )
            logger.debug(f"Station readings for job {job_id}: {fresh} fresh")
    finally:
        # Settled even when the task fails: the correction is optional, the forecast is not.
        # Never counted as failed either: no stations nearby is a normal answer, and
        # cells_failed would cut the job's lifetime as if forecast data were missing.
        await _settle_cell(job_id)


def _settle_cell_sync(job_id: str, failed: bool = False) -> tuple[ForecastJob | None, bool]:
    """Count one finished cell and, if it was the last, hand the job to assembly.

    ``failed`` also counts the cell in ``cells_failed``: it settled, but stored nothing.

    Returns ``(job, won_assembly)``. The increment is atomic but the read that follows is
    not, so several `cells` workers finishing at once could each see a complete job and
    enqueue assembly. The guarded UPDATE lets the database pick exactly one winner: only
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
        ).update(status=ForecastJob.Status.ASSEMBLING)
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
        await assemble_forecast_job.aenqueue(str(job.id))


# --------------------------------------------------------------------------- forecast jobs
# The HTTP endpoints only create a job and enqueue `plan_forecast_job`. Planning resolves
# geometry and fans out one cell task per distinct grid cell; the last cell to settle hands
# over to `assemble_forecast_job`, which builds the payload from cells that are by then warm.


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


async def _plan_forecast_job_async(job_id: str) -> None:
    job = await ForecastJob.objects.select_related("owner").filter(id=job_id).afirst()
    if job is None:
        logger.warning(f"Forecast job {job_id} not found for planning")
        return
    if job.status == ForecastJob.Status.DONE:
        return

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
    departure = datetime.fromisoformat(job.params["departure_time"])
    day_key = departure.date().isoformat()
    # Local date on purpose: the Open-Meteo window origin is local midnight, not UTC.
    days = forecast_days_for(departure, sample_points, local_today())
    cells = _cell_set(sample_points)
    with_stations = await _wants_stations(job, departure, geometry.get("total_seconds"))

    # Both counters and the status must be committed before the first task is enqueued: a
    # `cells` worker is fast enough to settle a cell while this function is still running,
    # and a settle against cells_total=0 would hand the job to assembly with no data.
    job.geometry = geometry
    job.cells_total = len(cells) * 2 + (1 if with_stations else 0)  # deterministic + ensemble (+ stations)
    job.cells_settled = 0
    job.cells_failed = 0
    job.status = ForecastJob.Status.FETCHING
    await job.asave(update_fields=["geometry", "cells_total", "cells_settled", "cells_failed", "status", "updated_at"])
    await publish(job)

    settled = 0
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


async def _wants_stations(job: ForecastJob, departure: datetime, total_seconds: float | None) -> bool:
    """Whether this job should spend Weather Underground calls: a key, a Pro owner, a ride near now."""
    if not api_key() or not ride_in_window(departure, total_seconds, datetime.now(tz=UTC)):
        return False
    return (await entitlements_for(job.owner)).station_correction


@task(queue_name="forecasts")
def assemble_forecast_job(job_id: str) -> None:
    """Build the finished forecast payload from cells that are warm by now."""
    async_to_sync(_assemble_forecast_job_async)(job_id)


async def _assemble_forecast_job_async(job_id: str) -> None:
    job = await ForecastJob.objects.select_related("owner").filter(id=job_id).afirst()
    if job is None:
        logger.warning(f"Forecast job {job_id} not found for assembly")
        return

    params = job.params
    geometry = job.geometry or {}
    assembled = False
    try:
        limits = await entitlements_for(job.owner)
        forecast = await compute_route_weather(
            start_lat=params.get("start_lat", 0.0),
            start_lon=params.get("start_lon", 0.0),
            dest_lat=params.get("dest_lat", 0.0),
            dest_lon=params.get("dest_lon", 0.0),
            profile=params.get("profile", "bike"),
            departure_time=params["departure_time"],
            sample_points=geometry.get("sample_points"),
            polyline=geometry.get("polyline"),
            total_seconds=geometry.get("total_seconds"),
            total_distance_m=geometry.get("total_distance_m"),
            vertex_times=geometry.get("vertex_times"),
            interval_seconds=params.get("interval_seconds", SAMPLE_INTERVAL_DEFAULT_S),
            # Every cell this job needs has already been fetched by a `cells` task. Reading
            # through the fetching accessors here would put provider calls back on the path
            # this whole design exists to keep them off.
            cache_only=True,
            # Computed, not stripped afterwards: a corrected number cannot be un-corrected.
            # The owner is part of the job key, so free and Pro never share a result, and the
            # tier is recorded in it below, so a tier change is never served a stale one.
            station_correction_enabled=limits.station_correction,
        )

        # Stripped before storage, not on read: the stored result is what the WebSocket
        # pushes and what the job endpoint returns, so a free account must never have Pro
        # data sitting in its row.
        if not limits.ensemble_uncertainty:
            strip_uncertainty(forecast.samples)

        # Stored in the same snake_case shape the API returns, so the job endpoint and the
        # WebSocket hand the frontend one shape.
        payload = forecast.model_dump(mode="json")
        payload["figures"] = generate_forecast_figures(forecast, departure_time=params["departure_time"])
        payload["sections"] = [
            section.model_dump(mode="json") for section in compute_sections(forecast.samples, forecast.total_distance_m)
        ]
        if job.kind == ForecastJob.Kind.ROUTE:
            payload["route_id"] = params["route_id"]
        payload["departure_time"] = params["departure_time"]
        # The tier this result was shaped for. The job key does not carry it, so without this
        # an upgrade or downgrade would keep being served the old tier's forecast.
        payload["entitlements"] = limits.result_marker()
        assembled = True
    finally:
        if not assembled:
            # Tell the watcher. The exception itself carries on to the worker, which records
            # the task as failed with its traceback and moves on to the next one.
            await set_status(job, ForecastJob.Status.FAILED, error="Wetterdaten konnten nicht zusammengestellt werden.")

    if not forecast.samples and job.cells_total:
        # Every cell was still cold at assembly time. Reporting this as a finished forecast
        # would put an empty chart in front of the user as though it were the weather; fail
        # it instead so the UI says so and the next request starts a fresh fan-out.
        logger.warning(f"Forecast job {job.id} assembled no samples from {job.cells_total} cells")
        await set_status(job, ForecastJob.Status.FAILED, error="Noch keine Wetterdaten verfügbar.")
        return

    job.result = payload
    job.status = ForecastJob.Status.DONE
    job.error = ""
    await job.asave(update_fields=["result", "status", "error", "updated_at"])
    await publish(job)
    logger.info(f"Forecast job {job.id} done ({len(forecast.samples)} samples)")


async def start_forecast_job(kind: str, owner, params: dict) -> ForecastJob:
    """Find or start the job for a request, enqueueing planning only when it is new."""
    if kind == ForecastJob.Kind.ROUTE:
        route = await RecurringRoute.objects.only("geometry_fetched_at").filter(id=params["route_id"]).afirst()
        revision = route.geometry_fetched_at.isoformat() if route and route.geometry_fetched_at else None
        params = {**params, "geometry_revision": revision}
    job, needs_planning = await get_or_start_job(kind, owner, params)
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

    for owner_id, routes in by_owner.items():
        if owner_id is None:
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


@task()
def refresh_user_forecasts(user_id: int) -> dict:
    """Check one account's routes for forecasts to refresh, right after it signs in.

    The hourly pass can leave a route's cells up to an hour past MAX_CELL_AGE; scanning at
    sign-in means the list and the first forecast the user opens are usually warm already.
    Cheap to repeat: a scan skips warm cells and the claims deduplicate the rest.
    """
    return async_to_sync(_refresh_user_forecasts_async)(user_id)


async def _refresh_user_forecasts_async(user_id: int) -> dict:
    scanned = await _enqueue_scans(await _prewarm_routes(owner_id=user_id))
    logger.debug(f"refresh_user_forecasts({user_id}): {scanned} route scans enqueued")
    return {"routes": scanned}


async def _refresh_upcoming_forecasts_async() -> dict:
    """Hand each eligible route its own scan task, then run maintenance.

    The scan itself used to walk every route's sample points inline, so one slow route held
    up the whole pass and none of it ever reached the queue.
    """
    scanned = await _enqueue_scans(await _prewarm_routes())

    # Both ledgers only exist to reject repeats and to answer polls; without a purge they
    # grow forever.
    purged = await sync_to_async(_purge_processed_events)()
    jobs_purged = await sync_to_async(_purge_expired_jobs)()
    stations_purged = await sync_to_async(purge_station_data)()

    logger.info(
        f"refresh_upcoming_forecasts: {scanned} route scans enqueued, "
        f"{purged} stripe events purged, {jobs_purged} forecast jobs purged, "
        f"{stations_purged} station rows purged"
    )
    return {
        "routes": scanned,
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

        day_key = dep.date().isoformat()
        days = forecast_days_for(dep, route.sample_points, today)

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
