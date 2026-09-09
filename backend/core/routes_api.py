"""API endpoints for recurring route CRUD and per-route weather forecasts."""

from uuid import UUID

from django.http import HttpRequest
from ninja import Router

from .models import RecurringRoute
from .routes_schemas import RecurringRouteIn, RecurringRouteOut, RouteForecastOut
from .schedule import forecast_available_at, next_departure
from .tasks import refresh_route_geometry
from .weather import compute_route_weather
from .plotting import generate_forecast_figures
from .sections import compute_sections

router = Router()


def _route_to_out(route: RecurringRoute) -> RecurringRouteOut:
    """Build a RecurringRouteOut from a model instance, computing schedule fields."""
    nd = next_departure(route.schedule_cron)
    return RecurringRouteOut(
        id=route.id,
        name=route.name,
        description=route.description,
        start_lat=route.start_lat,
        start_lon=route.start_lon,
        start_name=route.start_name,
        dest_lat=route.dest_lat,
        dest_lon=route.dest_lon,
        dest_name=route.dest_name,
        profile=route.profile,
        schedule_cron=route.schedule_cron,
        schedule_description=route.schedule_description,
        active=route.active,
        total_seconds=route.total_seconds,
        total_distance_m=route.total_distance_m,
        has_geometry=route.sample_points is not None,
        next_departure=nd.isoformat() if nd else None,
        forecast_available=forecast_available_at(nd) if nd else False,
        created_at=route.created_at,
        updated_at=route.updated_at,
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("/routes", response=list[RecurringRouteOut])
async def list_routes(request: HttpRequest):
    """List all active recurring routes, sorted by next departure (soonest first)."""
    routes = [r async for r in RecurringRoute.objects.filter(active=True)]
    result = [_route_to_out(r) for r in routes]
    # Sort: next_first (None sorts last)
    result.sort(key=lambda r: (r.next_departure is None, r.next_departure or ""))
    return result


@router.post("/routes", response=RecurringRouteOut)
async def create_route(request: HttpRequest, data: RecurringRouteIn):
    """Create a new recurring route. Enqueues a background task to fetch route geometry."""
    route = await RecurringRoute.objects.acreate(**data.model_dump())
    await refresh_route_geometry.aenqueue(str(route.id))
    return _route_to_out(route)


@router.get("/routes/{route_id}", response=RecurringRouteOut)
async def get_route(request: HttpRequest, route_id: UUID):
    """Get a single recurring route by ID."""
    route = await RecurringRoute.objects.aget(id=route_id)
    return _route_to_out(route)


@router.put("/routes/{route_id}", response=RecurringRouteOut)
async def update_route(request: HttpRequest, route_id: UUID, data: RecurringRouteIn):
    """Update a recurring route. Re-fetches geometry if start, destination, or profile changed."""
    route = await RecurringRoute.objects.aget(id=route_id)
    needs_geometry = (
        route.start_lat != data.start_lat
        or route.start_lon != data.start_lon
        or route.dest_lat != data.dest_lat
        or route.dest_lon != data.dest_lon
        or route.profile != data.profile
    )

    for field, value in data.model_dump().items():
        setattr(route, field, value)
    await route.asave()

    if needs_geometry:
        route.sample_points = None
        route.polyline = None
        route.geometry_fetched_at = None
        await route.asave()
        await refresh_route_geometry.aenqueue(str(route.id))

    return _route_to_out(route)


@router.delete("/routes/{route_id}", response={204: None})
async def delete_route(request: HttpRequest, route_id: UUID):
    """Delete a recurring route."""
    await RecurringRoute.objects.filter(id=route_id).adelete()
    return 204, None


# ---------------------------------------------------------------------------
# Forecast for a specific departure
# ---------------------------------------------------------------------------


@router.get("/routes/{route_id}/forecast", response=RouteForecastOut)
async def route_forecast(
    request: HttpRequest,
    route_id: UUID,
    date: str,  # YYYY-MM-DD
    time: str,  # HH:MM
):
    """Get weather forecast + Plotly figures for a specific departure of a saved route.

    Assembles the forecast from cached grid cells (ForecastCell + EnsembleCell).
    If cells are missing, they are fetched on-the-fly and stored.
    """
    route = await RecurringRoute.objects.aget(id=route_id)

    if not route.sample_points:
        from ninja.errors import HttpError

        raise HttpError(409, "Route geometry not yet computed. Try again in a few seconds.")

    departure_time = f"{date}T{time}"

    forecast = await compute_route_weather(
        start_lat=route.start_lat,
        start_lon=route.start_lon,
        dest_lat=route.dest_lat,
        dest_lon=route.dest_lon,
        profile=route.profile,
        departure_time=departure_time,
        sample_points=route.sample_points,
        polyline=route.polyline,
        total_seconds=route.total_seconds,
        total_distance_m=route.total_distance_m,
    )

    # Generate Plotly figures

    figures = generate_forecast_figures(forecast)

    # Compute route sections

    sections = compute_sections(forecast.samples, forecast.total_distance_m)

    return RouteForecastOut(
        route_id=route.id,
        departure_time=departure_time,
        line=forecast.line,
        total_seconds=forecast.total_seconds,
        total_distance_m=forecast.total_distance_m,
        samples=forecast.samples,
        summary=forecast.summary,
        figures=figures,
        sections=sections,
    )
