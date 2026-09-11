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

    wind_speed: float  # km/h
    wind_gust: float | None = None  # km/h
    wind_dir: float  # degrees, the direction the wind blows *from* (meteorological)
    headwind: float  # km/h, positive = headwind, negative = tailwind
    crosswind: float  # km/h, absolute cross component

    weather_code: int | None = None  # WMO weather code (Open-Meteo)
    weather_desc: str


class RouteWeatherSummary(CamelSchema):
    will_rain: bool
    first_rain_eta: str | None = None
    first_rain_place: str | None = None  # "lat,lon" of the first wet point
    max_rain_mm: float
    rain_probability: float | None = None  # 0..1 peak probability
    rain_amount: float  # "if it rains" mm at the peak-risk point
    max_headwind: float
    source: str  # "open-meteo" or "openweathermap"


class RouteWeatherOut(CamelSchema):
    line: list[list[float]]  # full route polyline as [[lon, lat], ...]
    total_seconds: int
    total_distance_m: float
    samples: list[WeatherSample]
    summary: RouteWeatherSummary
