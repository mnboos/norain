from typing import Literal
from uuid import UUID

from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError
from pydantic import Field

from ..schemas import CamelSchema

router = Router(tags=["Route weather"])


class EnsembleRange(CamelSchema):
    member_count: int
    p10: float | None = None
    median: float | None = None
    p90: float | None = None


class EnsembleStatistics(CamelSchema):
    metrics: dict[str, EnsembleRange]
    pop: float | None = None
    rain_if_wet: float | None = None  # mm in preceding forecast hour


class EnsembleModelStatistics(EnsembleStatistics):
    model: str


class ForecastUncertainty(EnsembleStatistics):
    models: list[EnsembleModelStatistics]
    requested_models: list[str]
    forecast_time: str
    fetched_at: str
    source: str = "open-meteo-ensemble"
    precipitation_interval_s: int = 3600


class ForecastUncertaintySummary(EnsembleStatistics):
    """A sample's ensemble spread without the per-model breakdown.

    The breakdown is most of the bytes and only the details panel shows it, for one point
    at a time, so it comes from ``/forecast_jobs/{id}/samples/{index}/uncertainty``.
    """

    forecast_time: str
    fetched_at: str
    source: str = "open-meteo-ensemble"
    precipitation_interval_s: int = 3600


class WeatherSample(CamelSchema):
    """Weather at one point along the route, at the clock time you'll be there."""

    lat: float
    lon: float
    elapsed_s: int  # seconds of riding from the start until this point
    eta: str  # ISO 8601 local clock time you arrive at this point

    rain_mm: float  # provider precipitation accumulation; interval specified below
    precipitation_interval_s: int | None = None
    rain_rate_mm_h: float | None = None
    probability_source: str | None = None
    uncertainty: ForecastUncertainty | None = None
    pop: float | None = None  # probability of precipitation 0..1 (ensemble members; OWM as fallback)
    rain_if_wet: float | None = None  # mean precip (mm) of just the ensemble members forecasting rain
    temp: float  # °C

    wind_speed: float | None = None  # km/h
    wind_gust: float | None = None  # km/h
    wind_dir: float | None = None  # meteorological wind-from direction
    headwind: float | None = None  # distance-weighted support mean, positive against rider
    crosswind: float | None = None  # support mean of absolute local crosswind
    sample_index: int | None = None  # original sample_points index, stable across missing cells
    wind_coverage: float | None = Field(default=None, ge=0, le=1)

    weather_code: int | None = None  # WMO weather code (Open-Meteo)
    weather_desc: str
    # How many weather stations nudged this sample's temperature or rain probability.
    # None when the sample is the plain model forecast.
    station_count: int | None = None


class WindSegment(CamelSchema):
    start_m: float = Field(ge=0, allow_inf_nan=False)
    end_m: float = Field(ge=0, allow_inf_nan=False)
    lat: float = Field(allow_inf_nan=False)
    lon: float = Field(allow_inf_nan=False)
    elapsed_s: float | None = None
    bearing: float | None = None
    rider_speed: float | None = None
    wind_speed: float | None = None
    wind_dir: float | None = None
    headwind: float | None = None
    crosswind: float | None = None  # signed midpoint value: positive from rider's right
    felt_speed: float | None = None
    felt_angle: float | None = None
    wind_coverage: float = Field(ge=0, le=1)
    felt_coverage: float = Field(ge=0, le=1)


class WindDistribution(CamelSchema):
    headwind_m: float = Field(ge=0)
    crosswind_m: float = Field(ge=0)
    tailwind_m: float = Field(ge=0)
    calm_m: float = Field(ge=0)
    unknown_m: float = Field(ge=0)
    mean_felt_speed: float | None = None
    max_felt_speed: float | None = None
    felt_covered_m: float = Field(ge=0)
    timing_source: Literal["routing", "sample-interpolation", "unavailable"]


class RouteWeatherSummary(CamelSchema):
    will_rain: bool
    first_rain_eta: str | None = None
    first_rain_place: str | None = None  # "lat,lon" of the first wet point
    max_rain_mm: float
    rain_probability: float | None = None  # 0..1 peak probability
    rain_amount: float  # "if it rains" mm at the peak-risk point
    max_headwind: float | None = None
    wind_distribution: WindDistribution | None = None
    source: str  # "open-meteo" or "openweathermap"
    station_corrected: bool = False  # some samples were corrected with station readings


class RouteWeatherOut(CamelSchema):
    line: list[list[float]]  # full route polyline as [[lon, lat], ...]
    total_seconds: int
    total_distance_m: float
    samples: list[WeatherSample]
    summary: RouteWeatherSummary
    wind_segments: list[WindSegment] = Field(default_factory=list)


class RouteSection(CamelSchema):
    start_km: float
    end_km: float
    start_time: str
    end_time: str
    condition: str
    max_rain_mm: float
    max_headwind: float | None = None
    temp_min: float
    temp_max: float


class ForecastSampleOut(WeatherSample):
    uncertainty: ForecastUncertaintySummary | None = None


class WindArrow(CamelSchema):
    """One felt-wind arrow for the map: only segments with complete felt-wind data."""

    lat: float
    lon: float
    bearing: float  # direction of travel, degrees clockwise from north
    felt_speed: float  # km/h
    felt_angle: float  # relative to the rider, degrees; positive from the right


class RouteForecastOut(CamelSchema):
    """A finished forecast as the job endpoint and the WebSocket serve it.

    Slimmer than what the job stores (see ``core.jobs.forecast_view``): ``line`` is the
    coarse route line, ``wind_arrows`` are about 2 km apart and the samples carry no
    per-model breakdown. The chart figures, finer map detail and one sample's breakdown
    each come from their own endpoint, keyed by ``job_id``; ``version`` changes whenever
    the job is recomputed under the same id.
    """

    job_id: UUID
    version: str
    route_id: UUID | None = None
    departure_time: str
    line: list[list[float]]  # coarse route line as [[lon, lat], ...]
    total_seconds: int
    total_distance_m: float
    samples: list[ForecastSampleOut]
    summary: RouteWeatherSummary
    wind_arrows: list[WindArrow] = Field(default_factory=list)
    sections: list[RouteSection] = Field(default_factory=list)
    uncertainty_partial: bool = False


class ForecastMapDetailOut(CamelSchema):
    """The route line and felt-wind arrows at one detail level."""

    line: list[list[float]]  # [[lon, lat], ...]
    wind_arrows: list[WindArrow]


class ForecastJobOut(CamelSchema):
    """A forecast being computed in the background, and its progress."""

    job_id: UUID
    status: str
    cells_settled: int = 0
    cells_total: int = 0
    error: str = ""
    # Present once `status` is "done". Stored already serialised and entitlement-stripped.
    result: RouteForecastOut | None = None
    ws_url: str = Field(default="", description="WebSocket path that streams this job's progress")


def job_out(job) -> ForecastJobOut:
    from ..jobs import job_snapshot

    return ForecastJobOut(**job_snapshot(job))


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
):
    """Start (or join) the forecast for an ad-hoc route.

    Returns 200 with the payload when an identical forecast is already computed and still
    fresh, otherwise 202 and a job to watch over `wsUrl`. The routing call and every
    provider fetch happen on workers -- this endpoint never blocks on them.
    """
    from ..models import ForecastJob
    from ..tasks import start_forecast_job

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


@router.get("/forecast_jobs/{job_id}/figures", response=list[dict])
async def forecast_job_figures(request: HttpRequest, job_id: UUID):
    """The Plotly chart figures of a finished job, for the pages that draw charts."""
    job = await _readable_job(request, job_id, finished=True)
    return job.result.get("figures") or []


@router.get("/forecast_jobs/{job_id}/map_detail", response=ForecastMapDetailOut)
async def forecast_job_map_detail(request: HttpRequest, job_id: UUID, detail: Literal["medium", "full"]):
    """The route line and felt-wind arrows at more detail than the job result carries.

    The map asks for this only once it is zoomed in far enough to show the difference.
    """
    from ..jobs import line_at_detail, wind_arrows_at_detail

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
    return samples[index].get("uncertainty")


async def _readable_job(request: HttpRequest, job_id: UUID, *, finished: bool = False):
    """Fetch a job the caller may read, or 404.

    With ``finished``, a job that has no result yet is a 404 as well: its parts do not
    exist until assembly is done.
    """
    from ..models import ForecastJob

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
    return job
