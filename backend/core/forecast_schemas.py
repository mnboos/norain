"""The forecast payload: what compute_route_weather builds, what a job stores and what the API serves.

Kept out of the ``core.api`` package so the domain modules (weather, uncertainty, plotting,
sections) can import it without loading the API routers, which import ``core.tasks``.
"""

from typing import Literal
from uuid import UUID

from pydantic import Field

from .schemas import CamelSchema


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
    # Extra watts to hold the planned speed against the wind vs. calm air; negative = helps.
    wind_power_w: float | None = None
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
    wind_power_w: float | None = None
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
    mean_wind_power_w: float | None = None  # distance-weighted, tailwind counted as 0
    max_wind_power_w: float | None = None
    timing_source: Literal["routing", "sample-interpolation", "unavailable"]


class RouteWeatherSummary(CamelSchema):
    will_rain: bool
    first_rain_eta: str | None = None
    first_rain_place: str | None = None  # "lat,lon" of the first wet point
    max_rain_mm: float
    rain_probability: float | None = None  # 0..1 peak probability
    rain_amount: float  # "if it rains" mm at the peak-risk point
    max_headwind: float | None = None
    max_wind_power_w: float | None = None  # largest sample wind effort, W
    # "niedrig" … "sehr hoch" for max_wind_power_w. Filled when served (core.jobs.forecast_view),
    # never stored, like every field derived from the ride-quality curves.
    max_wind_effort_level: str | None = None
    # The iciest point of the ride as a word, filled when served. None means no frost worth
    # naming - a ride with no samples at all says so through `samples` being empty.
    max_frost_level: str | None = None
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
    # Which samples this section covers. Plain data, not a verdict - it is what lets
    # ``core.jobs.forecast_view`` score the section's frost on read. Optional because sections
    # are stored in ``job.result``: every job finished before these existed has section dicts
    # without them, and a required field would fail validation on the way back out.
    start_index: int | None = None
    end_index: int | None = None
    # Filled on read by ``forecast_view``; never stored. See ``core.ride_quality``.
    frost_level: str | None = None


class ForecastSampleOut(WeatherSample):
    uncertainty: ForecastUncertaintySummary | None = None
    # Served from core.ride_quality when the forecast is read; None where it cannot be scored.
    ride_score: float | None = Field(default=None, ge=0, le=1)  # 0 = best ride, 1 = worst
    ride_label: str | None = None  # e.g. "mässig · v. a. Regen"
    wind_effort_level: str | None = None  # "Wind hilft", "keiner", "niedrig" … "sehr hoch"
    frost_level: str | None = None  # "leicht" … "stark"; None means no frost worth naming


class WindArrow(CamelSchema):
    """One real-wind arrow for the map: only segments with complete ground-wind data."""

    lat: float
    lon: float
    bearing: float  # direction of travel, degrees clockwise from north
    wind_speed: float  # km/h over ground
    wind_dir: float  # degrees, direction the wind comes FROM
    wind_power_w: float | None = None  # extra watts at the planned speed; negative = helps
    wind_effort_level: str | None = None  # the effort as a word; see core.ride_quality
    wind_effort: float = Field(default=0, ge=0, le=1)  # 0..1, sizes the arrow


class DepartureCandidate(CamelSchema):
    departure_time: str
    arrival_time: str
    available: bool
    ride_score: float | None = None
    ride_label: str


class DepartureComparison(CamelSchema):
    requested_time: str
    window_start: str
    window_end: str
    candidates: list[DepartureCandidate]
    recommended_time: str | None = None
    explanation: str


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
    departure_comparison: DepartureComparison | None = None


class ForecastMapDetailOut(CamelSchema):
    """The route line and wind arrows at one detail level."""

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
