"""Forecast jobs: identity, lifecycle and progress publishing.

A forecast is never computed inside an HTTP request. The endpoint creates or finds a
``ForecastJob``, enqueues ``plan_forecast_job`` and returns immediately; the browser then
watches the job over the WebSocket in ``core/consumers.py`` (or polls the job endpoint if
the socket cannot be opened).
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta

from channels.exceptions import ChannelFull
from channels.layers import get_channel_layer
from loguru import logger
from redis.exceptions import RedisError

from .geo import simplify_line
from .models import ForecastJob

# A job that has not been touched for this long is assumed dead -- its worker was killed
# mid-flight -- and the next request restarts it rather than waiting forever on it.
JOB_STALL_TIMEOUT = timedelta(minutes=5)
FORECAST_ALGORITHM_VERSION = 3

# How many times plan_forecast_job may wait for route geometry before giving up. Geometry
# failures are logged and swallowed by refresh_route_geometry, so without a ceiling a job
# for a route GraphHopper cannot solve would re-defer itself forever.
MAX_PLAN_ATTEMPTS = 3

# How long to wait between those attempts.
PLAN_RETRY_DELAY = timedelta(seconds=20)

# How long a finished job is reused when some of its cells failed to fetch. Provider limits
# (Open-Meteo answers 429 when a burst of cells goes out) usually clear within minutes;
# reusing such a job for the full MAX_CELL_AGE would hide the missing data for two hours.
INCOMPLETE_JOB_LIFETIME = timedelta(minutes=5)

# How long a finished job is reused when its ride is close enough to now for weather-station
# readings to matter. Readings are kept for 10 minutes; a job reused for the full
# MAX_CELL_AGE would serve old readings, or none at all if it was planned before the ride
# came into range.
STATION_JOB_LIFETIME = timedelta(minutes=10)


def job_key(kind: str, owner_id: int | None, params: dict) -> str:
    """Stable identity for a forecast request.

    Identical requests collapse onto one job, so a page that mounts twice, or two people
    asking for the same public ride, cost one fan-out. ``owner_id`` is part of the identity
    because ``result`` is stored already entitlement-stripped.
    """
    payload = json.dumps({"version": FORECAST_ALGORITHM_VERSION, "kind": kind, "owner": owner_id, "params": params},
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


# Route-line detail levels, as Douglas-Peucker tolerances in metres. The job result carries
# the coarse line; the map asks for a finer one only once the user zooms in far enough to
# see the difference. ``None`` is the unsimplified GraphHopper polyline.
LINE_DETAIL: dict[str, float | None] = {"coarse": 50.0, "medium": 10.0, "full": None}

# Felt-wind arrow spacing along the route in metres, per detail level. The map keeps arrows
# 80 px apart on screen, which at Swiss latitudes is ~4 km at zoom 11 and ~500 m at zoom 14,
# so denser arrows than this would only be thinned away again in the browser.
WIND_ARROW_SPACING: dict[str, float | None] = {"coarse": 2000.0, "medium": 500.0, "full": None}


def group_name(job_id) -> str:
    return f"forecast.{job_id}"


def job_snapshot(job: ForecastJob, *, include_result: bool = True) -> dict:
    """The job state as the browser sees it.

    Deliberately the same snake_case shape the job endpoint returns, so the frontend can
    parse a WebSocket frame and an HTTP response with one function -- the generated
    client's ``ForecastJobOutFromJSON`` -- instead of two.
    """
    snapshot = {
        "job_id": str(job.id),
        "status": job.status,
        "cells_settled": job.cells_settled,
        "cells_total": job.cells_total,
        "error": job.error,
        "ws_url": f"/ws/forecast/{job.id}/",
    }
    if include_result and job.status == ForecastJob.Status.DONE:
        snapshot["result"] = forecast_view(job)
    return snapshot


def sample_vertices(line: list[list[float]], samples: list[dict]) -> set[int]:
    """Index of the line vertex each sample sits on.

    Samples are placed on real polyline vertices and arrive in route order, so an exact
    match scanning forward finds each one. The frontend colours the line by locating
    samples the same way, so these vertices must survive any simplification.
    """
    found: set[int] = set()
    pointer = 0
    for sample in samples:
        for i in range(pointer, len(line)):
            if line[i][0] == sample.get("lon") and line[i][1] == sample.get("lat"):
                found.add(i)
                pointer = i
                break
    return found


def line_at_detail(result: dict, detail: str) -> list[list[float]]:
    """The stored route line, simplified to one of the ``LINE_DETAIL`` levels."""
    line = result.get("line") or []
    tolerance = LINE_DETAIL[detail]
    if tolerance is None:
        return line
    keep = sample_vertices(line, result.get("samples") or [])
    return [line[i] for i in simplify_line(line, keep, tolerance)]


def _complete_felt_wind(segment: dict) -> bool:
    """Whether a wind segment can be drawn as an arrow: fully covered, speed and angle known."""
    return (
        segment.get("wind_coverage", 0) >= 1 - 1e-9
        and segment.get("felt_coverage", 0) >= 1 - 1e-9
        and segment.get("elapsed_s") is not None
        and segment.get("felt_speed") is not None
        and segment.get("felt_angle") is not None
        and segment.get("bearing") is not None
    )


def wind_arrows_at_detail(result: dict, detail: str) -> list[dict]:
    """The stored wind segments as map arrows, at most one per ``WIND_ARROW_SPACING``.

    Only the fields the map draws, rounded to what it can show: ~1 m for position and a
    tenth of a unit for the rest. A segment with partial data is never an arrow; showing it
    would claim a felt wind the forecast does not have for that stretch.
    """
    spacing = WIND_ARROW_SPACING[detail]
    arrows: list[dict] = []
    next_start = float("-inf")
    for segment in result.get("wind_segments") or []:
        if not _complete_felt_wind(segment) or segment.get("start_m", 0) < next_start:
            continue
        arrows.append({
            "lat": round(segment["lat"], 5),
            "lon": round(segment["lon"], 5),
            "bearing": round(segment["bearing"], 1),
            "felt_speed": round(segment["felt_speed"], 1),
            "felt_angle": round(segment["felt_angle"], 1),
        })
        if spacing is not None:
            next_start = segment.get("start_m", 0) + spacing
    return arrows


def uncertainty_partial(samples: list[dict]) -> bool:
    """Whether some sample lacks ensemble data: no spread, a missing model or metric."""
    # Imported here: core.uncertainty reaches core.api, and core.tasks imports this module.
    from .uncertainty import METRICS

    for sample in samples:
        uncertainty = sample.get("uncertainty")
        if not uncertainty:
            return True
        present = {model.get("model") for model in uncertainty.get("models") or []}
        if any(name not in present for name in uncertainty.get("requested_models") or []):
            return True
        metrics = uncertainty.get("metrics") or {}
        if any((metrics.get(key) or {}).get("median") is None for key in METRICS):
            return True
    return False


def forecast_view(job: ForecastJob) -> dict:
    """The finished forecast as the job endpoint and the WebSocket serve it.

    The stored ``result`` stays complete; this only trims what goes over the wire. Parts
    that only some pages show are fetched on demand from ``/forecast_jobs/{id}/...``:
    the chart figures, the finer route line and wind arrows, and each sample's per-model
    breakdown.
    Shaping happens here, on read, and never enters the job key -- otherwise the map and
    the route page would each compute their own job for the same forecast.
    """
    result = job.result or {}
    view = {key: value for key, value in result.items() if key not in ("figures", "wind_segments")}
    view["line"] = line_at_detail(result, "coarse")
    view["wind_arrows"] = wind_arrows_at_detail(result, "coarse")
    samples = result.get("samples") or []
    view["samples"] = [
        {
            **sample,
            "uncertainty": (
                {k: v for k, v in sample["uncertainty"].items() if k not in ("models", "requested_models")}
                if sample.get("uncertainty") else None
            ),
        }
        for sample in samples
    ]
    view["uncertainty_partial"] = uncertainty_partial(samples)
    view["job_id"] = str(job.id)
    view["version"] = job.updated_at.isoformat() if job.updated_at else ""
    return view


async def publish(job: ForecastJob) -> None:
    """Push the job's current state to every socket watching it.

    Never raises: a forecast that completed but could not be announced is still a
    completed forecast, and the frontend falls back to polling the job endpoint.
    """
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        await layer.group_send(group_name(job.id), {"type": "forecast.event", "payload": job_snapshot(job)})
    except (RedisError, OSError, ChannelFull) as exc:  # announcing progress must never fail the work
        logger.warning(f"Could not publish forecast job {job.id}: {exc}")


async def set_status(job: ForecastJob, status: str, *, error: str = "") -> None:
    """Move a job to a new state, persist it and tell the watchers."""
    job.status = status
    job.error = error
    await job.asave(update_fields=["status", "error", "updated_at"])
    await publish(job)


async def _uses_stations(job: ForecastJob, owner, now: datetime) -> bool:
    """Whether this job's result would be corrected with station readings if planned now."""
    from .entitlements import entitlements_for
    from .stations import api_key, ride_in_window

    departure = job.params.get("departure_time")
    if not api_key() or not departure:
        return False
    total_seconds = (job.geometry or {}).get("total_seconds")
    if not ride_in_window(datetime.fromisoformat(departure), total_seconds, now):
        return False
    return (await entitlements_for(owner)).station_correction


async def get_or_start_job(kind: str, owner, params: dict) -> tuple[ForecastJob, bool]:
    """Find or create the job for this request.

    Returns ``(job, needs_planning)``. ``needs_planning`` is False when a fresh result is
    already available or an identical job is still in flight -- in both cases the caller
    just subscribes instead of starting a second fan-out.
    """
    # Imported here, not at module scope: core.grid reaches core.api (via core.uncertainty),
    # which imports core.tasks, which imports this module -- a cycle that only bites when
    # the ASGI app loads consumers before the API package.
    from .grid import MAX_CELL_AGE

    key = job_key(kind, owner.id if owner is not None else None, params)
    now = datetime.now(tz=UTC)

    job, created = await ForecastJob.objects.aget_or_create(
        key=key,
        defaults={"kind": kind, "owner": owner, "params": params},
    )
    if created:
        return job, True

    # A finished forecast stays valid exactly as long as the cells behind it would have --
    # unless some of them failed to fetch, then only until a retry has a fair chance.
    lifetime = INCOMPLETE_JOB_LIFETIME if job.cells_failed else MAX_CELL_AGE
    done = job.status == ForecastJob.Status.DONE
    if done and now - job.updated_at > STATION_JOB_LIFETIME and await _uses_stations(job, owner, now):
        lifetime = STATION_JOB_LIFETIME
    if job.status == ForecastJob.Status.DONE and now - job.updated_at <= lifetime:
        return job, False

    # Still working, and recently enough that its worker is plausibly alive.
    if not job.is_terminal and now - job.updated_at <= JOB_STALL_TIMEOUT:
        return job, False

    # Stale result, previous failure, or a job whose worker died: start over.
    job.status = ForecastJob.Status.PENDING
    job.cells_total = 0
    job.cells_settled = 0
    job.cells_failed = 0
    job.attempts = 0
    job.error = ""
    job.result = None
    job.geometry = None
    job.params = params
    await job.asave(
        update_fields=[
            "status", "cells_total", "cells_settled", "cells_failed", "attempts",
            "error", "result", "geometry", "params", "updated_at",
        ]
    )
    return job, True
