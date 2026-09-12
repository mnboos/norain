"""RecurringRoute API: schemas, CRUD, and per-departure forecasts."""

from datetime import datetime
from uuid import UUID

from django.db.models import Q
from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError
from pydantic import Field

from ..auth.backend import session_auth
from ..entitlements import entitlements_for
from ..models import ForecastJob, RecurringRoute, route_point
from ..schedule import forecast_available_at, next_departure
from ..schemas import CamelSchema
from .route_weather import ForecastJobOut, job_out

router = Router(auth=session_auth, tags=["Recurring routes"])


class RecurringRouteIn(CamelSchema):
    name: str
    description: str = ""
    start_lat: float = Field(ge=-90, le=90)
    start_lon: float = Field(ge=-180, le=180)
    start_name: str
    dest_lat: float = Field(ge=-90, le=90)
    dest_lon: float = Field(ge=-180, le=180)
    dest_name: str
    profile: str = "bike"
    schedule_cron: str
    schedule_description: str
    active: bool = True


class RouteThumbnailSample(CamelSchema):
    """Weather inputs for one point in a route-list thumbnail."""

    i: int
    rain_mm: float
    temp: float
    headwind: float | None = None
    precipitation_interval_s: int | None = None
    rain_rate_mm_h: float | None = None


class RouteThumbnail(CamelSchema):
    departure: str | None = None
    path: list[list[float]] = Field(default_factory=list)
    samples: list[RouteThumbnailSample | None] = Field(default_factory=list)
    computed_at: str | None = None


class RecurringRouteOut(CamelSchema):
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
    next_departure: str | None = None
    forecast_available: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    thumbnail: RouteThumbnail | None = None


async def _current_user(request: HttpRequest):
    """Return the authenticated session user established by the router."""
    user = getattr(request, "auth", None)
    if not user or not user.is_authenticated:
        raise HttpError(401, "Authentication required.")
    return user


async def _owned_route(request: HttpRequest, route_id: UUID) -> RecurringRoute:
    """Fetch a route owned by the current user, hiding other accounts' routes."""
    user = await _current_user(request)
    try:
        return await RecurringRoute.objects.aget(id=route_id, owner=user)
    except RecurringRoute.DoesNotExist:
        raise HttpError(404, "Route not found.") from None


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
        # hasattr, not getattr-with-default: the default expression would be evaluated
        # eagerly and load the very field list_routes defers.
        has_geometry=(
            route.geometry_ready if hasattr(route, "geometry_ready") else route.sample_points is not None
        ),
        next_departure=nd.isoformat() if nd else None,
        forecast_available=forecast_available_at(nd) if nd else False,
        created_at=route.created_at,
        updated_at=route.updated_at,
        # Read straight from the stored blob: the list endpoint must not parse forecast
        # cells. refresh_route_thumbnail keeps it current.
        thumbnail=route.thumbnail,
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("/routes", response=list[RecurringRouteOut])
async def list_routes(request: HttpRequest):
    """List the current account's active recurring routes by next departure."""
    user = await _current_user(request)
    # `polyline` and `sample_points` are large blobs that _route_to_out never reads, and
    # this endpoint is polled every 60 s. `has_geometry` is annotated so deferring
    # sample_points does not trigger a per-row query to test it.
    query = (
        RecurringRoute.objects.filter(active=True, owner=user)
        .defer("polyline", "sample_points", "vertex_times")
        .annotate(geometry_ready=Q(sample_points__isnull=False))
    )
    routes = [r async for r in query]
    result = [_route_to_out(r) for r in routes]
    # Sort: next_first (None sorts last)
    result.sort(key=lambda r: (r.next_departure is None, r.next_departure or ""))
    return result


async def _assert_route_quota(user, *, exclude_id: UUID | None = None) -> None:
    """Refuse a new active route once the account's tier is full."""
    limits = await entitlements_for(user)
    if limits.max_routes is None:
        return
    existing = RecurringRoute.objects.filter(owner=user, active=True)
    if exclude_id is not None:
        existing = existing.exclude(id=exclude_id)
    if await existing.acount() >= limits.max_routes:
        raise HttpError(
            402,
            f"Der {limits.plan}-Tarif erlaubt {limits.max_routes} aktive Routen. "
            "Upgrade auf Pro für unbegrenzte Routen.",
        )


@router.post("/routes", response=RecurringRouteOut)
async def create_route(request: HttpRequest, data: RecurringRouteIn):
    """Create a new recurring route. Enqueues a background task to fetch route geometry."""
    from ..tasks import refresh_route_geometry

    user = await _current_user(request)
    await _assert_route_quota(user)
    values = data.model_dump(exclude={"start_lat", "start_lon", "dest_lat", "dest_lon"})
    route = await RecurringRoute.objects.acreate(
        owner=user,
        start_point=route_point(data.start_lat, data.start_lon),
        destination_point=route_point(data.dest_lat, data.dest_lon),
        **values,
    )
    await refresh_route_geometry.aenqueue(str(route.id))
    return _route_to_out(route)


@router.get("/routes/{route_id}", response=RecurringRouteOut)
async def get_route(request: HttpRequest, route_id: UUID):
    """Get a single recurring route by ID."""
    route = await _owned_route(request, route_id)
    return _route_to_out(route)


@router.put("/routes/{route_id}", response=RecurringRouteOut)
async def update_route(request: HttpRequest, route_id: UUID, data: RecurringRouteIn):
    """Update a recurring route. Re-fetches geometry if start, destination, or profile changed."""
    from ..tasks import refresh_route_geometry

    route = await _owned_route(request, route_id)
    # Reactivating a route consumes a slot just as creating one does.
    if data.active and not route.active:
        await _assert_route_quota(await _current_user(request), exclude_id=route_id)
    needs_geometry = (
        route.start_lat != data.start_lat
        or route.start_lon != data.start_lon
        or route.dest_lat != data.dest_lat
        or route.dest_lon != data.dest_lon
        or route.profile != data.profile
    )

    values = data.model_dump(exclude={"start_lat", "start_lon", "dest_lat", "dest_lon"})
    for field, value in values.items():
        setattr(route, field, value)
    route.start_point = route_point(data.start_lat, data.start_lon)
    route.destination_point = route_point(data.dest_lat, data.dest_lon)
    await route.asave()

    if needs_geometry:
        route.sample_points = None
        route.polyline = None
        route.vertex_times = None
        route.geometry_fetched_at = None
        await route.asave()
        await refresh_route_geometry.aenqueue(str(route.id))

    return _route_to_out(route)


@router.delete("/routes/{route_id}", response={204: None})
async def delete_route(request: HttpRequest, route_id: UUID):
    """Delete a recurring route."""
    route = await _owned_route(request, route_id)
    await route.adelete()
    return 204, None


# ---------------------------------------------------------------------------
# Forecast for a specific departure
# ---------------------------------------------------------------------------


@router.get("/routes/{route_id}/forecast", response={200: ForecastJobOut, 202: ForecastJobOut})
async def route_forecast(
    request: HttpRequest,
    route_id: UUID,
    date: str,  # YYYY-MM-DD
    time: str,  # HH:MM
):
    """Start (or join) the forecast for one departure of a saved route.

    Returns 200 with the finished payload -- weather, Plotly figures and sections -- when
    an identical forecast is already computed and still fresh, otherwise 202 and a job to
    watch over `wsUrl`. Cell fetching and figure rendering both happen on workers; neither
    is allowed on this path.
    """
    from ..tasks import start_forecast_job

    route = await _owned_route(request, route_id)

    if not route.sample_points:
        raise HttpError(409, "Route geometry not yet computed. Try again in a few seconds.")

    job = await start_forecast_job(
        ForecastJob.Kind.ROUTE,
        await _current_user(request),
        {"route_id": str(route.id), "departure_time": f"{date}T{time}"},
    )
    status = 200 if job.status == ForecastJob.Status.DONE else 202
    return status, job_out(job)
