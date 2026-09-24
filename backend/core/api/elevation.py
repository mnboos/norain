"""Elevation parts load independently of forecasts and never change route geometry."""

from uuid import UUID

import httpx
from ninja import Router
from ninja.errors import HttpError

from ..auth.backend import optional_session_auth
from ..elevation import ElevationIn, ElevationOut, elevation_profile, with_heights
from ..models import JourneyStage, RecurringRoute
from .gpx import limit_request
from .route_weather import _readable_job, _shown_result

router = Router(auth=optional_session_auth, tags=["Elevation"])


async def profile(coordinates, seconds, times=None):
    try:
        return await elevation_profile(coordinates or [], seconds, times)
    except (httpx.HTTPError, ValueError, OSError) as exc:
        raise HttpError(503, "Höhendaten konnten nicht geladen werden. Bitte erneut versuchen.") from exc


@router.post("/elevation", response=ElevationOut)
async def preview_elevation(request, data: ElevationIn):
    limit_request(request, "elevation", 60)
    return await profile([p[:2] for p in data.coordinates], data.total_seconds, data.vertex_times)


@router.get("/routes/{route_id}/elevation", response=ElevationOut)
async def route_elevation(request, route_id: UUID):
    user = request.auth
    if not user or not user.is_authenticated:
        raise HttpError(404, "Route not found.")
    route = await RecurringRoute.objects.filter(id=route_id, owner=user).afirst()
    if route is None:
        raise HttpError(404, "Route not found.")
    return await profile(
        with_heights(route.polyline_coordinates or [], route.vertex_elevations),
        route.total_seconds,
        None if route.geometry_source == "imported" else route.vertex_times,
    )


@router.get("/journey_stages/{stage_id}/elevation", response=ElevationOut)
async def stage_elevation(request, stage_id: UUID):
    user = request.auth
    if not user or not user.is_authenticated:
        raise HttpError(404, "Stage not found.")
    stage = await JourneyStage.objects.filter(id=stage_id, day__journey__owner=user).afirst()
    if stage is None:
        raise HttpError(404, "Stage not found.")
    return await profile(
        with_heights(stage.polyline_coordinates, stage.vertex_elevations), stage.total_seconds, stage.vertex_times
    )


@router.get("/forecast_jobs/{job_id}/elevation", response=ElevationOut)
async def forecast_elevation(request, job_id: UUID):
    job = await _readable_job(request, job_id, finished=True)
    result = _shown_result(job)
    line = result.get("line") or []
    snapshot = result.get("elevation_geometry") or {}
    # Old results did not snapshot elevations/times. Use geometry only if its path
    # exactly matches the shown result, including when a stale forecast is visible.
    if not snapshot and (job.geometry or {}).get("polyline") == line:
        snapshot = job.geometry
    return await profile(
        with_heights(line, snapshot.get("vertex_elevations")), result.get("total_seconds"), snapshot.get("vertex_times")
    )
