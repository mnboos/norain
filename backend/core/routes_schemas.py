"""Schemas for the recurring routes API."""

from datetime import datetime
from uuid import UUID

from .schemas import CamelSchema
from .weather_schemas import RouteWeatherOut


class RecurringRouteIn(CamelSchema):
    """Input for creating or updating a recurring route."""

    name: str
    description: str = ""
    start_lat: float
    start_lon: float
    start_name: str
    dest_lat: float
    dest_lon: float
    dest_name: str
    profile: str = "bike"
    schedule_cron: str
    schedule_description: str
    active: bool = True


class RecurringRouteOut(CamelSchema):
    """A recurring route with computed schedule fields."""

    id: UUID
    name: str
    description: str
    start_lat: float
    start_lon: float
    start_name: str
    dest_lat: float
    dest_lon: float
    dest_name: str
    profile: str
    schedule_cron: str
    schedule_description: str
    active: bool
    total_seconds: int | None = None
    total_distance_m: float | None = None
    has_geometry: bool = False
    next_departure: str | None = None  # ISO datetime
    forecast_available: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RouteSection(CamelSchema):
    """A contiguous segment of the route with uniform weather conditions."""

    start_km: float
    end_km: float
    start_time: str  # HH:MM
    end_time: str  # HH:MM
    condition: str  # "dry", "rain", "heavy_rain"
    max_rain_mm: float
    max_headwind: float
    temp_min: float
    temp_max: float


class RouteForecastOut(RouteWeatherOut):
    """Weather forecast for a specific departure of a saved route, with Plotly figures."""

    route_id: UUID
    departure_time: str
    figures: list[dict] = []  # Plotly figure JSONs
    sections: list[RouteSection] = []  # weather-grouped sections
