"""Public GPX interchange; saved route downloads retain owner authorization."""

from uuid import UUID

from django.core.cache import cache
from django.http import HttpResponse
from django.utils.text import slugify
from ninja import File, Router, UploadedFile
from ninja.errors import HttpError
from pydantic import Field

from ..auth.backend import optional_session_auth
from ..entitlements import entitlements_for
from ..forecast_schemas import ForecastJobOut
from ..gpx import MAX_GPX_BYTES, exact_geometry, parse_gpx, serialize_gpx
from ..models import ForecastJob
from ..route_input import RoutePlanIn
from ..schemas import CamelSchema
from ..tasks import start_forecast_job
from ..weather import ROUTING_ERRORS, build_geometry
from .recurring_route import _owned_route
from .route_weather import _readable_job, flexibility_params, job_out
from .route_weather import router as weather_router

router = Router(auth=optional_session_auth, tags=["GPX"])


def limit_request(request, action, limit=30):
    import hashlib
    import time

    user = request.auth
    identity = str(user.pk) if user.is_authenticated else request.META.get("REMOTE_ADDR", "unknown")
    key = f"gpx:{action}:{hashlib.sha256(identity.encode()).hexdigest()}:{int(time.time() // 60)}"
    cache.add(key, 0, 90)
    if cache.incr(key) > limit:
        raise HttpError(429, "Zu viele Anfragen. Bitte kurz warten.")


class GpxPathOut(CamelSchema):
    name: str
    coordinates: list[list[float]]
    distance_m: float
    routing_points: list[list[float]]


@router.post("/gpx/import", response=list[GpxPathOut])
async def import_gpx(request, file: File[UploadedFile]):
    limit_request(request, "import")
    if file.size > MAX_GPX_BYTES:
        raise HttpError(413, "Die GPX-Datei darf höchstens 10 MiB gross sein.")
    try:
        return parse_gpx(file.read(MAX_GPX_BYTES + 1))
    except ValueError as exc:
        raise HttpError(422, str(exc)) from None


class GpxExportIn(CamelSchema):
    name: str = Field(default="NoRain", max_length=200)
    coordinates: list[list[float]] = Field(min_length=2, max_length=100000)


def gpx_response(name, points):
    try:
        content = serialize_gpx(name, points)
    except ValueError as exc:
        raise HttpError(422, str(exc)) from None
    response = HttpResponse(content, content_type="application/gpx+xml")
    response["Content-Disposition"] = f'attachment; filename="{slugify(name)[:100] or "route"}.gpx"'
    response["Cache-Control"] = "private, no-store"
    return response


@router.post("/gpx/export")
async def export_gpx(request, data: GpxExportIn):
    limit_request(request, "export")
    return gpx_response(data.name, data.coordinates)


@router.get("/routes/{route_id}/gpx")
async def export_saved_gpx(request, route_id: UUID):
    route = await _owned_route(request, route_id)
    points = route.imported_coordinates if route.geometry_source == "imported" else route.polyline_coordinates
    if not points:
        raise HttpError(409, "Die Strecke wird noch berechnet.")
    return gpx_response(route.name, points)


@router.get("/forecast_jobs/{job_id}/gpx")
async def export_job_gpx(request, job_id: UUID):
    job = await _readable_job(request, job_id)
    points = (
        job.params.get("coordinates")
        if job.params.get("geometry_source") == "imported"
        else (job.geometry or {}).get("polyline")
    )
    if not points:
        raise HttpError(409, "Die Strecke wird noch berechnet.")
    return gpx_response(job.params.get("name", "NoRain"), points)


class RoutePlanOut(CamelSchema):
    coordinates: list[list[float]]
    distance_m: float
    time_s: int


@router.post("/gpx/preview", response=RoutePlanOut)
async def preview_gpx(request, data: RoutePlanIn):
    limit_request(request, "preview", 60)
    try:
        geometry = (
            exact_geometry(data.coordinates, data.duration_seconds)
            if data.geometry_source == "imported"
            else await build_geometry(data.profile, tuple(tuple(p[:2]) for p in data.coordinates))
        )
    except (ValueError, *ROUTING_ERRORS) as exc:
        raise HttpError(422, "Für diese Punkte wurde keine Route gefunden.") from exc
    return {
        "coordinates": data.coordinates if data.geometry_source == "imported" else geometry["polyline"],
        "distance_m": geometry["total_distance_m"],
        "time_s": geometry["total_seconds"],
    }


class RoutePlanForecastIn(RoutePlanIn):
    departure_time: str
    interval_seconds: int = Field(default=300, ge=60, le=3600)
    departure_flex_before_minutes: int = Field(default=0, ge=0, le=120, multiple_of=15)
    departure_flex_after_minutes: int = Field(default=0, ge=0, le=120, multiple_of=15)


@weather_router.post("/route_weather", response={200: ForecastJobOut, 202: ForecastJobOut}, tags=["GPX"])
async def forecast_route_plan(request, data: RoutePlanForecastIn):
    limit_request(request, "forecast")
    user = request.auth if request.auth.is_authenticated else None
    if (data.departure_flex_before_minutes or data.departure_flex_after_minutes) and not (
        await entitlements_for(user)
    ).departure_comparison:
        raise HttpError(402, "Departure comparison requires Plus.")
    params = data.model_dump(exclude={"departure_flex_before_minutes", "departure_flex_after_minutes"})
    params.update(
        flexibility_params(data.departure_time, data.departure_flex_before_minutes, data.departure_flex_after_minutes)
    )
    first, last = data.coordinates[0], data.coordinates[-1]
    params.update(start_lon=first[0], start_lat=first[1], dest_lon=last[0], dest_lat=last[1])
    if data.geometry_source == "graphhopper":
        params["via_points"] = data.coordinates[1:-1]
    job = await start_forecast_job(ForecastJob.Kind.ADHOC, user, params)
    return (200 if job.status == ForecastJob.Status.DONE else 202), job_out(job)
