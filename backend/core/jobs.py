"""Forecast jobs: identity, lifecycle and progress publishing.

A forecast is never computed inside an HTTP request. The endpoint creates or finds a
``ForecastJob``, enqueues ``plan_forecast_job`` and returns immediately; the browser then
watches the job over the WebSocket in ``core/consumers.py`` (or polls the job endpoint if
the socket cannot be opened).
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from channels.exceptions import ChannelFull
from channels.layers import get_channel_layer
from loguru import logger
from redis.exceptions import RedisError

from . import telemetry
from .departures import candidate_times, comparison_view
from .entitlements import entitlements_for, forecast_params_for
from .geo import simplify_line
from .grid import MAX_CELL_AGE
from .models import ForecastJob
from .ride_quality import score_sample, wind_effort, wind_effort_level, worst_frost_level
from .stations import api_key, ride_in_window
from .uncertainty import METRICS

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

# How old a previous result may be and still be shown, flagged stale, while its job refreshes.
# Measured from the result's own ``computed_at``: every restart bumps ``updated_at``.
STALE_RESULT_MAX_AGE = timedelta(hours=24)


def job_key(kind: str, owner_id: int | None, params: dict) -> str:
    """Stable identity for a forecast request.

    Identical requests collapse onto one job, so a page that mounts twice, or two people
    asking for the same public ride, cost one fan-out. ``owner_id`` is part of the identity
    because ``result`` is stored already entitlement-stripped. The owner's *tier* is not:
    ``get_or_start_job`` compares it against the marker the result was assembled with.
    """
    payload = json.dumps(
        {"version": FORECAST_ALGORITHM_VERSION, "kind": kind, "owner": owner_id, "params": params},
        sort_keys=True,
        separators=(",", ":"),
    )
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


def job_snapshot(job: ForecastJob, *, include_result: bool = True, include_stale: bool = False) -> dict:
    """The job state as the browser sees it.

    Deliberately the same snake_case shape the job endpoint returns, so the frontend can
    parse a WebSocket frame and an HTTP response with one function -- the generated
    client's ``ForecastJobOutFromJSON`` -- instead of two.

    ``include_stale`` adds the previous result, flagged ``stale``, while the job refreshes.
    Only the HTTP envelope asks for it: the page needs it once, and the progress frames go
    out on every settled cell.
    """
    snapshot: dict[str, Any] = {
        "job_id": str(job.id),
        "status": job.status,
        "cells_settled": job.cells_settled,
        "cells_total": job.cells_total,
        "error": job.error,
        "ws_url": f"/ws/forecast/{job.id}/",
    }
    if include_result and job.status == ForecastJob.Status.DONE:
        snapshot["result"] = forecast_view(job)
    elif include_stale and job.status != ForecastJob.Status.DONE and job.stale_result:
        snapshot["result"] = forecast_view(job, job.stale_result)
        snapshot["stale"] = True
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


def _complete_ground_wind(segment: dict) -> bool:
    """Whether a wind segment can be drawn as an arrow: fully covered, speed and direction known.

    Calm air has no direction (``wind_dir`` is None) and gets no arrow.
    """
    return (
        segment.get("wind_coverage", 0) >= 1 - 1e-9
        and segment.get("wind_speed") is not None
        and segment.get("wind_dir") is not None
        and segment.get("bearing") is not None
    )


def wind_arrows_at_detail(result: dict, detail: str) -> list[dict]:
    """The stored wind segments as real-wind map arrows, at most one per ``WIND_ARROW_SPACING``.

    Only the fields the map draws, rounded to what it can show: ~1 m for position, a tenth
    of a unit for speed and angles, whole watts. A segment with partial data is never an
    arrow; showing it would claim a wind the forecast does not have for that stretch. The
    wind effort needs timing and stays None without it -- the arrow is still drawn.
    """
    spacing = WIND_ARROW_SPACING[detail]
    arrows: list[dict] = []
    next_start = float("-inf")
    for segment in result.get("wind_segments") or []:
        if not _complete_ground_wind(segment) or segment.get("start_m", 0) < next_start:
            continue
        power = segment.get("wind_power_w")
        arrows.append(
            {
                "lat": round(segment["lat"], 5),
                "lon": round(segment["lon"], 5),
                "bearing": round(segment["bearing"], 1),
                "wind_speed": round(segment["wind_speed"], 1),
                "wind_dir": round(segment["wind_dir"], 1),
                "wind_power_w": round(power) if power is not None else None,
                "wind_effort_level": wind_effort_level(power),
                "wind_effort": round(wind_effort(power), 3),
            }
        )
        if spacing is not None:
            next_start = segment.get("start_m", 0) + spacing
    return arrows


def uncertainty_partial(samples: list[dict]) -> bool:
    """Whether some sample lacks ensemble data: no spread, a missing model or metric."""
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


def sections_with_frost(sections: list[dict], samples: list[dict]) -> list[dict]:
    """The stored sections with each one's frost level scored from the samples it covers.

    Scored here rather than in ``compute_sections`` so a change to ``RIDE_QUALITY`` shows on
    the next request, like every other ride-quality verdict. Sections stored before they
    carried their sample range keep ``frost_level`` at None, and the map simply draws no
    marker for them - they are gone within the job's lifetime anyway.
    """
    out: list[dict] = []
    for section in sections:
        start, end = section.get("start_index"), section.get("end_index")
        covered = samples[start : end + 1] if isinstance(start, int) and isinstance(end, int) else []
        out.append({**section, "frost_level": worst_frost_level(covered)})
    return out


def forecast_view(job: ForecastJob, result: dict | None = None) -> dict:
    """The finished forecast as the job endpoint and the WebSocket serve it.

    The stored ``result`` stays complete; this only trims what goes over the wire. Parts
    that only some pages show are fetched on demand from ``/forecast_jobs/{id}/...``:
    the finer route line and wind arrows, and each sample's per-model breakdown. The
    charts need no part: the frontend draws them from the samples.
    Shaping happens here, on read, and never enters the job key -- otherwise the map and
    the route page would each compute their own job for the same forecast.

    ``result`` is the payload to render, ``job.result`` unless given (the stale one).
    """
    result = (job.result if result is None else result) or {}
    # "figures": results stored before the frontend drew the charts itself still carry them.
    dropped = ("figures", "wind_segments", "entitlements", "departure_inputs")
    view = {key: value for key, value in result.items() if key not in dropped}
    if result.get("departure_inputs"):
        view["departure_comparison"] = comparison_view(result["departure_inputs"])
    view["line"] = line_at_detail(result, "coarse")
    view["wind_arrows"] = wind_arrows_at_detail(result, "coarse")
    samples = result.get("samples") or []
    # Ride quality is scored here, on read, so a change to RIDE_QUALITY shows on the next
    # request rather than after every stored job has been rebuilt.
    view["samples"] = [
        {
            **score_sample(sample),
            "uncertainty": (
                {k: v for k, v in sample["uncertainty"].items() if k not in ("models", "requested_models")}
                if sample.get("uncertainty")
                else None
            ),
        }
        for sample in samples
    ]
    if isinstance(result.get("summary"), dict):
        view["summary"] = {
            **result["summary"],
            "max_wind_effort_level": wind_effort_level(result["summary"].get("max_wind_power_w")),
            "max_frost_level": worst_frost_level(samples),
        }
    if result.get("sections"):
        # Only when the stored result has them: this trims the payload, it never adds a key
        # the job did not carry.
        view["sections"] = sections_with_frost(result["sections"], samples)
    view["uncertainty_partial"] = uncertainty_partial(samples)
    view["job_id"] = str(job.id)
    # The compute time, so the charts re-key when a fresh result replaces a stale one.
    view["version"] = result.get("computed_at") or (job.updated_at.isoformat() if job.updated_at else "")
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
    if status == ForecastJob.Status.FAILED:
        won = await ForecastJob.objects.filter(pk=job.pk, status=job.status, updated_at=job.updated_at).exclude(
            status__in=ForecastJob.TERMINAL_STATUSES,
        ).aupdate(status=status, error=error, updated_at=datetime.now(tz=UTC))
        if not won:
            return
        await job.arefresh_from_db()
        telemetry.completed(job, "failed")
    else:
        job.status = status
        job.error = error
        await job.asave(update_fields=["status", "error", "updated_at"])
    await publish(job)


async def _uses_stations(job: ForecastJob, owner, now: datetime) -> bool:
    """Whether this job's result would be corrected with station readings if planned now."""

    departure = job.params.get("departure_time")
    if job.kind == ForecastJob.Kind.JOURNEY_STAGE or not api_key() or not departure:
        return False
    total_seconds = (job.geometry or {}).get("total_seconds")
    if not any(ride_in_window(t, total_seconds, now) for t in candidate_times(job.params)):
        return False
    return (await entitlements_for(owner)).station_correction


async def _built_for_current_tier(job: ForecastJob, owner) -> bool:
    """Whether a finished result was assembled for the owner's tier as it is now.

    The result is shaped by the tier -- spread stripped, stations applied or not -- so after
    an upgrade it would keep hiding what was paid for, and after a downgrade keep serving Pro
    data. A result without the marker predates it and is rebuilt as well.
    """

    return (job.result or {}).get("entitlements") == (await entitlements_for(owner)).result_marker()


def carry_stale(job: ForecastJob, marker, now: datetime) -> dict | None:
    """The result a restarting job may keep showing, flagged stale, while it refreshes.

    The last finished result, or else the one an earlier restart already kept: a refresh
    that fails or stalls (a throttled provider) restarts again, and must not lose it. Only a
    result built for the owner's current tier (``marker``) and computed within
    ``STALE_RESULT_MAX_AGE``. A finished result from before ``computed_at`` existed is dated by
    ``updated_at``: nothing but assembly writes a finished row. Any other undated one is dropped.
    """
    candidate = job.result or job.stale_result
    if not candidate or candidate.get("entitlements") != marker:
        return None
    if job.result and job.status == ForecastJob.Status.DONE and not candidate.get("computed_at"):
        candidate = {**candidate, "computed_at": job.updated_at.isoformat()}
    try:
        computed_at = datetime.fromisoformat(candidate.get("computed_at") or "")
    except ValueError:
        return None
    return candidate if now - computed_at <= STALE_RESULT_MAX_AGE else None


async def get_or_start_job(
    kind: str, owner, params: dict, *, min_remaining: timedelta = timedelta(0)
) -> tuple[ForecastJob, bool]:
    """Find or create the job for this request.

    Returns ``(job, needs_planning)``. ``needs_planning`` is False when a fresh result is
    already available or an identical job is still in flight -- in both cases the caller
    just subscribes instead of starting a second fan-out.

    ``min_remaining`` treats a finished result as stale once less than that much of its
    lifetime is left. The hourly pre-build passes it so a job it leaves in place cannot
    expire before the next pass.
    """
    params = await forecast_params_for(owner, params)
    key = job_key(kind, owner.id if owner is not None else None, params)
    now = datetime.now(tz=UTC)

    job, created = await ForecastJob.objects.aget_or_create(
        key=key,
        defaults={"kind": kind, "owner": owner, "params": params},
    )
    context = await telemetry.job_context(job)
    context["trigger"] = "prewarm" if min_remaining > timedelta(0) else "request"
    if created:
        telemetry.event("forecast.request", outcome="new", **context)
        return job, True

    # A finished forecast stays valid exactly as long as the cells behind it would have --
    # unless some of them failed to fetch, then only until a retry has a fair chance.
    lifetime = INCOMPLETE_JOB_LIFETIME if job.cells_failed else MAX_CELL_AGE
    done = job.status == ForecastJob.Status.DONE
    if done and now - job.updated_at > STATION_JOB_LIFETIME and await _uses_stations(job, owner, now):
        lifetime = STATION_JOB_LIFETIME
    if done and now - job.updated_at <= lifetime - min_remaining and await _built_for_current_tier(job, owner):
        telemetry.event("forecast.request", outcome="cached" if done else "joined", **context)
        return job, False

    marker = (await entitlements_for(owner)).result_marker()

    # Still working, and recently enough that its worker is plausibly alive.
    if not job.is_terminal and now - job.updated_at <= JOB_STALL_TIMEOUT:
        if job.stale_result and job.stale_result.get("entitlements") != marker:
            # The tier changed mid-refresh: the kept result would show what it no longer allows.
            job.stale_result = None
            await ForecastJob.objects.filter(pk=job.pk).aupdate(stale_result=None)
        telemetry.event("forecast.request", outcome="cached" if done else "joined", **context)
        return job, False

    outcome = "restart_expired" if done else "restart_failed" if job.is_terminal else "restart_stalled"
    telemetry.event("forecast.request", outcome=outcome, **context)

    # Stale result, previous failure, or a job whose worker died: start over, keeping the
    # last result to show while the new one is computed.
    job.stale_result = carry_stale(job, marker, now)
    job.status = ForecastJob.Status.PENDING
    job.cells_total = 0
    job.cells_settled = 0
    job.cells_failed = 0
    job.attempts = 0
    job.error = ""
    job.result = None
    job.computed_weather = None
    job.geometry = None
    job.params = params
    await job.asave(
        update_fields=[
            "status",
            "cells_total",
            "cells_settled",
            "cells_failed",
            "attempts",
            "error",
            "result",
            "stale_result",
            "computed_weather",
            "geometry",
            "params",
            "updated_at",
        ]
    )
    return job, True


def restrict_job_result(job, limits):
    """Read-time expiry guard for polling and WebSocket delivery."""
    from copy import deepcopy
    for field in ("result", "stale_result"):
        payload = getattr(job, field, None)
        if not payload:
            continue
        payload = deepcopy(payload)
        if not limits.departure_comparison:
            payload.pop("departure_inputs", None)
            payload.pop("departure_comparison", None)
        if not limits.ensemble_uncertainty:
            for sample in payload.get("samples", []):
                sample["uncertainty"] = None
        setattr(job, field, payload)
