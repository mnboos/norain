from typing import Literal
from uuid import UUID

from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError

from ..departures import candidate_times, check_flexibility
from ..entitlements import entitlements_for
from ..forecast_schemas import ForecastJobOut, ForecastMapDetailOut, ForecastUncertainty
from ..jobs import job_snapshot, line_at_detail, wind_arrows_at_detail
from ..models import ForecastJob
from ..tasks import start_forecast_job

router = Router(tags=["Route weather"])

# The profiles GraphHopper is configured with (data/graphhopper/graphhopper-config.yaml). Checked on
# input so an unknown one is a 422 here rather than a failed routing call on a worker.
ROUTING_PROFILES = ("bike", "ebike", "fast_ebike")


def check_routing_profile(profile: str) -> str:
    if profile not in ROUTING_PROFILES:
        raise ValueError(f"unknown profile {profile!r}; expected one of {', '.join(ROUTING_PROFILES)}")
    return profile


def job_out(job) -> ForecastJobOut:
    return ForecastJobOut(**job_snapshot(job))


def flexibility_params(departure: str, before: int, after: int) -> dict:
    try:
        check_flexibility(before)
        check_flexibility(after)
        if not before and not after:
            return {}
        params = {"departure_flex_before_minutes": before, "departure_flex_after_minutes": after}
        candidate_times({"departure_time": departure, **params})
    except ValueError as exc:
        raise HttpError(422, str(exc)) from None
    else:
        return params


@router.get("/route_weather", response={200: ForecastJobOut, 202: ForecastJobOut})
async def route_weather(
    request: HttpRequest,
    start_lat: float,
    start_lon: float,
    dest_lat: float,
    dest_lon: float,
    profile: str,
    departure_time: str,
    interval_seconds: int = 300,
    departure_flex_before_minutes: int = 0,
    departure_flex_after_minutes: int = 0,
):
    """Start (or join) the forecast for an ad-hoc route.

    Returns 200 with the payload when an identical forecast is already computed and still
    fresh, otherwise 202 and a job to watch over `wsUrl`. The routing call and every
    provider fetch happen on workers -- this endpoint never blocks on them.
    """
    try:
        check_routing_profile(profile)
    except ValueError as exc:
        raise HttpError(422, str(exc)) from None

    if (departure_flex_before_minutes or departure_flex_after_minutes) and not (
        await entitlements_for(getattr(request, "auth", None))
    ).departure_comparison:
        raise HttpError(402, "Departure comparison requires Plus. Try Plus free for 14 days.")
    job = await start_forecast_job(
        ForecastJob.Kind.ADHOC,
        getattr(request, "auth", None) or None,
        {
            "start_lat": start_lat,
            "start_lon": start_lon,
            "dest_lat": dest_lat,
            "dest_lon": dest_lon,
            "profile": profile,
            "departure_time": departure_time,
            "interval_seconds": interval_seconds,
            **flexibility_params(departure_time, departure_flex_before_minutes, departure_flex_after_minutes),
        },
    )
    status = 200 if job.status == ForecastJob.Status.DONE else 202
    return status, job_out(job)


@router.get("/forecast_jobs/{job_id}", response=ForecastJobOut)
async def forecast_job(request: HttpRequest, job_id: UUID):
    """Poll one forecast job.

    The WebSocket is the primary channel; this exists so a client behind a proxy that
    drops upgrades still makes progress, and so tests can assert without a socket.
    """
    return job_out(await _readable_job(request, job_id))


@router.get("/forecast_jobs/{job_id}/map_detail", response=ForecastMapDetailOut)
async def forecast_job_map_detail(request: HttpRequest, job_id: UUID, detail: Literal["medium", "full"]):
    """The route line and wind arrows at more detail than the job result carries.

    The map asks for this only once it is zoomed in far enough to show the difference.
    """
    job = await _readable_job(request, job_id, finished=True)
    return {"line": line_at_detail(job.result, detail), "wind_arrows": wind_arrows_at_detail(job.result, detail)}


@router.get("/forecast_jobs/{job_id}/samples/{index}/uncertainty", response=ForecastUncertainty | None)
async def forecast_job_sample_uncertainty(request: HttpRequest, job_id: UUID, index: int):
    """One sample's full ensemble spread, including the per-model breakdown.

    ``null`` when the sample has none -- which includes every sample of a free account,
    since the stored result is stripped before storage.
    """
    job = await _readable_job(request, job_id, finished=True)
    samples = job.result.get("samples") or []
    if not 0 <= index < len(samples):
        raise HttpError(404, "Sample not found.")
    if not (await entitlements_for(getattr(request, "auth", None))).ensemble_uncertainty:
        return None
    return samples[index].get("uncertainty")


async def _readable_job(request: HttpRequest, job_id: UUID, *, finished: bool = False):
    """Fetch a job the caller may read, or 404.

    With ``finished``, a job that has no result yet is a 404 as well: its parts do not
    exist until assembly is done.
    """
    job = await ForecastJob.objects.filter(id=job_id).afirst()
    if job is None:
        raise HttpError(404, "Forecast job not found.")

    # Ad-hoc jobs are guarded by the unguessable id alone; a saved route's forecast is
    # private to its owner.
    if job.owner_id is not None:
        user = getattr(request, "auth", None)
        if not user or not user.is_authenticated or user.id != job.owner_id:
            raise HttpError(404, "Forecast job not found.")

    if finished and (job.status != ForecastJob.Status.DONE or not job.result):
        raise HttpError(404, "Forecast job is not finished.")
    from ..jobs import restrict_job_result

    restrict_job_result(job, await entitlements_for(getattr(request, "auth", None)))
    return job
