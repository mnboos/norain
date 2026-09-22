"""RecurringRoute API: schemas, CRUD, and per-departure forecasts."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from asgiref.sync import sync_to_async
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest
from loguru import logger
from ninja import Router
from ninja.errors import HttpError
from pydantic import Field, field_validator
from redis.exceptions import RedisError

from .. import telemetry
from ..auth.backend import session_auth
from ..departures import route_job_params
from ..entitlements import allowed_route_ids, entitlements_for, entitlements_for_sync
from ..forecast_schemas import ForecastJobOut
from ..models import ForecastJob, RecurringRoute, RideBriefing, User, route_point
from ..ride_quality import worst_frost_level, worst_rain_level, worst_ride_score
from ..road_prefs import RoadPrefs, road_prefs_model
from ..schedule import check_schedule_cron, forecast_available_at, next_departure
from ..schemas import CamelSchema
from ..tasks import refresh_route_geometry, start_forecast_job
from ..weather import ROUTING_ERRORS, preview_route
from .route_weather import check_routing_profile, flexibility_params, job_out

router = Router(auth=session_auth, tags=["Recurring routes"])

# Enough to bend a commute round a few spots, few enough to keep a GraphHopper call cheap.
MAX_VIA_POINTS = 15


def check_coordinates(points: list[list[float]]) -> list[list[float]]:
    """[[lon, lat], ...], as lists: the stored JSON compares equal to it only in that shape."""
    for point in points:
        if len(point) != 2 or not (-180 <= point[0] <= 180 and -90 <= point[1] <= 90):
            raise ValueError("each point is [lon, lat]")
    return [[float(lon), float(lat)] for lon, lat in points]


def check_via_points(points: list[list[float]]) -> list[list[float]]:
    if len(points) > MAX_VIA_POINTS:
        raise ValueError(f"at most {MAX_VIA_POINTS} via points")
    return check_coordinates(points)


class RecurringRouteIn(CamelSchema):
    name: str
    description: str = ""
    start_lat: float = Field(ge=-90, le=90)
    start_lon: float = Field(ge=-180, le=180)
    start_name: str
    dest_lat: float = Field(ge=-90, le=90)
    dest_lon: float = Field(ge=-180, le=180)
    dest_name: str
    via_points: list[list[float]] = Field(default_factory=list)
    profile: str = "bike"
    schedule_cron: str
    schedule_description: str
    departure_flex_before_minutes: int = Field(default=0, ge=0, le=120, multiple_of=15)
    departure_flex_after_minutes: int = Field(default=0, ge=0, le=120, multiple_of=15)
    active: bool = True
    return_schedule_cron: str | None = None
    return_schedule_description: str = ""

    @field_validator("return_schedule_cron")
    @classmethod
    def check_return_schedule(cls, value):
        return check_schedule_cron(value) if value else value

    _profile = field_validator("profile")(check_routing_profile)
    _via_points = field_validator("via_points")(check_via_points)
    _schedule_cron = field_validator("schedule_cron")(check_schedule_cron)


class RouteThumbnail(CamelSchema):
    """The route-list glyph: the simplified path, the ride quality of its worst sample, and
    the rain and frost the row shows beside it.

    The stored blob keeps the raw sample weather; only verdicts and two aggregates leave the
    server, scored when the list is served (see ``core.ride_quality``). The rain and frost
    levels are the *worst point of the ride*, not the worst-scoring sample: "will it rain on
    my ride" is a different question from "what spoils it".
    """

    departure: str | None = None
    path: list[list[float]] = Field(default_factory=list)
    computed_at: str | None = None
    ride_score: float | None = Field(default=None, ge=0, le=1)  # None: no sample could be scored
    ride_label: str | None = None
    # None means "no rain / no frost worth naming". Nothing to show at all is a missing
    # thumbnail or one whose `departure` no longer matches, which the list already handles.
    rain_level: str | None = None
    frost_level: str | None = None
    rain_probability: float | None = Field(default=None, ge=0, le=1)  # peak chance of rain
    max_rain_rate_mm_h: float | None = None  # shown instead when there is no probability
    temp_min: float | None = None  # coldest point of the ride, °C


def _thumbnail_out(blob: dict | None) -> RouteThumbnail | None:
    if not blob:
        return None
    samples = [s for s in (blob.get("samples") or []) if s is not None]
    worst = worst_ride_score(samples)
    pops = [s["pop"] for s in samples if s.get("pop") is not None]
    rates = [s["rain_rate_mm_h"] for s in samples if s.get("rain_rate_mm_h") is not None]
    temps = [s["temp"] for s in samples if s.get("temp") is not None]
    return RouteThumbnail(
        departure=blob.get("departure"),
        path=blob.get("path") or [],
        computed_at=blob.get("computed_at"),
        ride_score=round(worst.score, 4) if worst else None,
        ride_label=worst.label if worst else None,
        rain_level=worst_rain_level(samples),
        frost_level=worst_frost_level(samples),
        rain_probability=round(max(pops), 2) if pops else None,
        max_rain_rate_mm_h=round(max(rates), 1) if rates else None,
        temp_min=round(min(temps), 1) if temps else None,
    )


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
    via_points: list[list[float]] = Field(default_factory=list)
    profile: str
    schedule_cron: str
    schedule_description: str
    departure_flex_before_minutes: int = 0
    departure_flex_after_minutes: int = 0
    active: bool
    total_seconds: int | None = None
    total_distance_m: float | None = None
    has_geometry: bool = False
    next_departure: str | None = None
    forecast_available: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    thumbnail: RouteThumbnail | None = None
    return_route_id: UUID | None = None
    parent_route_id: UUID | None = None
    return_schedule_cron: str | None = None
    return_schedule_description: str = ""
    return_next_departure: str | None = None


async def _current_user(request: HttpRequest) -> User:
    """Return the authenticated session user established by the router."""
    user = getattr(request, "auth", None)
    if not isinstance(user, User) or not user.is_authenticated:
        raise HttpError(401, "Authentication required.")
    return user


async def _owned_route(request: HttpRequest, route_id: UUID) -> RecurringRoute:
    """Fetch a route owned by the current user, hiding other accounts' routes."""
    user = await _current_user(request)
    try:
        return await RecurringRoute.objects.select_related("return_journey").aget(id=route_id, owner=user)
    except RecurringRoute.DoesNotExist:
        raise HttpError(404, "Route not found.") from None


def _route_to_out(route: RecurringRoute) -> RecurringRouteOut:
    """Build a RecurringRouteOut from a model instance, computing schedule fields."""
    nd = next_departure(route.schedule_cron)
    returning = route._state.fields_cache.get("return_journey")
    return_departure = next_departure(returning.schedule_cron) if returning else None
    return RecurringRouteOut(
        id=route.id,
        return_route_id=returning.id if returning else None,
        parent_route_id=route.return_of_id,
        return_schedule_cron=returning.schedule_cron if returning else None,
        return_schedule_description=returning.schedule_description if returning else "",
        return_next_departure=return_departure.isoformat() if return_departure else None,
        name=route.name,
        description=route.description,
        start_lat=route.start_lat,
        start_lon=route.start_lon,
        start_name=route.start_name,
        dest_lat=route.dest_lat,
        dest_lon=route.dest_lon,
        dest_name=route.dest_name,
        via_points=route.via_points or [],
        profile=route.profile,
        schedule_cron=route.schedule_cron,
        schedule_description=route.schedule_description,
        departure_flex_before_minutes=route.departure_flex_before_minutes,
        departure_flex_after_minutes=route.departure_flex_after_minutes,
        active=route.active,
        total_seconds=route.total_seconds,
        total_distance_m=route.total_distance_m,
        # hasattr, not getattr-with-default: the default expression would be evaluated
        # eagerly and load the very field list_routes defers.
        has_geometry=(route.geometry_ready if hasattr(route, "geometry_ready") else route.sample_points is not None),
        next_departure=nd.isoformat() if nd else None,
        forecast_available=forecast_available_at(nd) if nd else False,
        created_at=route.created_at,
        updated_at=route.updated_at,
        # Read straight from the stored blob: the list endpoint must not parse forecast
        # cells. refresh_route_thumbnail keeps it current; scoring it is cheap arithmetic.
        thumbnail=_thumbnail_out(route.thumbnail),
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
        RecurringRoute.objects.filter(active=True, owner=user, return_of__isnull=True)
        .select_related("return_journey")
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
    existing = RecurringRoute.objects.filter(owner=user, active=True, return_of__isnull=True)
    if exclude_id is not None:
        existing = existing.exclude(id=exclude_id)
    if await existing.acount() >= limits.max_routes:
        telemetry.event(
            "route.action",
            action="quota",
            outcome="rejected",
            **{"user.id": str(user.pk), "plan": str(limits.plan)},
        )
        raise HttpError(
            402,
            f"Der {limits.plan}-Tarif erlaubt {limits.max_routes} aktive Routen. "
            "Upgrade auf Pro für unbegrenzte Routen.",
        )


@router.post("/routes", response=RecurringRouteOut)
async def create_route(request: HttpRequest, data: RecurringRouteIn):
    """Create a new recurring route. Enqueues a background task to fetch route geometry."""
    user = await _current_user(request)
    if (data.departure_flex_before_minutes or data.departure_flex_after_minutes) and not (
        await entitlements_for(user)
    ).departure_comparison:
        raise HttpError(402, "Departure comparison requires Plus.")
    values = data.model_dump(
        exclude={
            "start_lat",
            "start_lon",
            "dest_lat",
            "dest_lon",
            "return_schedule_cron",
            "return_schedule_description",
        }
    )
    route = await sync_to_async(_create_with_quota)(
        user,
        return_schedule_cron=data.return_schedule_cron,
        return_schedule_description=data.return_schedule_description,
        start_point=route_point(data.start_lat, data.start_lon),
        destination_point=route_point(data.dest_lat, data.dest_lon),
        **values,
    )
    telemetry.event(
        "route.action",
        action="created",
        outcome="success",
        **telemetry.route_context(route),
        **await sync_to_async(telemetry.user_context)(user),
    )
    await refresh_route_geometry.aenqueue(str(route.id))
    returning = route._state.fields_cache.get("return_journey")
    if returning:
        await refresh_route_geometry.aenqueue(str(returning.id))
    return _route_to_out(route)


class RoadPrefsIn(CamelSchema):
    """Journey road preferences (``core.road_prefs.RoadPrefs``); every one is a penalty."""

    surface: Literal["any", "avoid_unpaved", "paved_only"] = "any"
    climbing: Literal["neutral", "avoid"] = "neutral"
    traffic: Literal["neutral", "avoid_main", "avoid_off_network"] = "neutral"
    towns: Literal["neutral", "avoid"] = "neutral"

    def prefs(self) -> RoadPrefs:
        return RoadPrefs(surface=self.surface, climbing=self.climbing, traffic=self.traffic, towns=self.towns)


class RoutePreviewIn(CamelSchema):
    profile: str = "bike"
    # [[lon, lat], ...]: start, via points, destination.
    points: list[list[float]] = Field(min_length=2, max_length=MAX_VIA_POINTS + 2)
    # A journey's editor draws the line its road preferences give.
    road_prefs: RoadPrefsIn | None = None

    _profile = field_validator("profile")(check_routing_profile)
    _points = field_validator("points")(check_coordinates)


class RoutePreviewOut(CamelSchema):
    coordinates: list[list[float]]
    distance_m: float
    time_s: int


# The editor asks once per drag, so a minute's worth of real editing stays far below this.
PREVIEW_LIMIT_PER_MINUTE = 60


def _preview_allowed(user_id: int) -> bool:
    """Per-account limit on preview calls. Fails open, like the cell claims: the cache
    guards GraphHopper's load, it never decides whether a user may edit."""
    key = f"routepreview:{user_id}:{int(datetime.now(tz=UTC).timestamp() // 60)}"
    try:
        cache.add(key, 0, 90)
        return cache.incr(key) <= PREVIEW_LIMIT_PER_MINUTE
    except (RedisError, OSError, ValueError) as exc:
        logger.warning(f"Route preview limit unavailable: {exc}")
        return True


@router.post("/routes/preview", response=RoutePreviewOut)
async def route_preview(request: HttpRequest, data: RoutePreviewIn):
    """The line through the given points, for the route editor.

    The one request that calls GraphHopper directly: an editor cannot wait on a queue. It
    returns only the line — no sampling, no weather — and a saved route's geometry still
    comes from ``refresh_route_geometry``.
    """
    user = await _current_user(request)
    if not _preview_allowed(user.pk):
        raise HttpError(429, "Zu viele Routenberechnungen. Bitte kurz warten.")
    try:
        model = road_prefs_model(data.road_prefs.prefs()) if data.road_prefs else None
        return await preview_route(data.profile, tuple((lon, lat) for lon, lat in data.points), model)
    except ROUTING_ERRORS as exc:
        logger.info(f"Route preview failed: {exc}")
        raise HttpError(422, "Für diese Punkte wurde keine Route gefunden.") from None


@router.get("/routes/{route_id}", response=RecurringRouteOut)
async def get_route(request: HttpRequest, route_id: UUID):
    """Get a single recurring route by ID."""
    route = await _owned_route(request, route_id)
    return _route_to_out(route)


@router.put("/routes/{route_id}", response=RecurringRouteOut)
async def update_route(request: HttpRequest, route_id: UUID, data: RecurringRouteIn):
    """Update a recurring route. Re-fetches geometry if start, destination, or profile changed."""
    route = await _owned_route(request, route_id)
    user = await _current_user(request)
    if (
        (data.departure_flex_before_minutes or data.departure_flex_after_minutes)
        and (
            data.departure_flex_before_minutes != route.departure_flex_before_minutes
            or data.departure_flex_after_minutes != route.departure_flex_after_minutes
        )
        and not (await entitlements_for(user)).departure_comparison
    ):
        raise HttpError(402, "Departure comparison requires Plus.")
    # Reactivating a route consumes a slot just as creating one does.
    if data.active and not route.active:
        await _assert_route_quota(await _current_user(request), exclude_id=route_id)
    needs_geometry = (
        route.start_lat != data.start_lat
        or route.start_lon != data.start_lon
        or route.dest_lat != data.dest_lat
        or route.dest_lon != data.dest_lon
        or route.profile != data.profile
        or route.via_points != data.via_points
    )

    values = data.model_dump(
        exclude={
            "start_lat",
            "start_lon",
            "dest_lat",
            "dest_lon",
            "return_schedule_cron",
            "return_schedule_description",
        }
    )
    for field, value in values.items():
        setattr(route, field, value)
    route.start_point = route_point(data.start_lat, data.start_lon)
    route.destination_point = route_point(data.dest_lat, data.dest_lon)
    await sync_to_async(_save_with_quota)(user, route, data.return_schedule_cron, data.return_schedule_description)
    await RideBriefing.objects.filter(route=route, status="pending").aupdate(status="canceled")

    telemetry.event(
        "route.action",
        action="updated",
        outcome="success",
        **telemetry.route_context(route),
        **await sync_to_async(telemetry.user_context)(await _current_user(request)),
    )

    if needs_geometry:
        route.sample_points = None
        route.polyline = None
        route.vertex_times = None
        route.geometry_fetched_at = None
        await route.asave()
        await refresh_route_geometry.aenqueue(str(route.id))

    returning = route._state.fields_cache.get("return_journey")
    if returning and not returning.sample_points:
        await refresh_route_geometry.aenqueue(str(returning.id))
    return _route_to_out(route)


@router.delete("/routes/{route_id}", response={204: None})
async def delete_route(request: HttpRequest, route_id: UUID):
    """Delete a recurring route."""
    route = await _owned_route(request, route_id)
    context = {
        **telemetry.route_context(route),
        **await sync_to_async(telemetry.user_context)(await _current_user(request)),
    }
    await route.adelete()
    telemetry.event("route.action", action="deleted", outcome="success", **context)
    return 204, None


# ---------------------------------------------------------------------------
# Forecast for a specific departure
# ---------------------------------------------------------------------------

# Sent by the dashboard when it loads a forecast ahead of a click. Such a request is not the
# user opening the route, so it must not mark the route as viewed.
PREFETCH_HEADER = "X-NoRain-Prefetch"

# The comparison query refetches every 60 s; without this a viewed route is written each time.
VIEW_RECORD_INTERVAL = timedelta(hours=1)


async def _record_view(route: RecurringRoute) -> None:
    """Remember that the owner opened this route, so the hourly pass pre-builds its forecast."""
    now = datetime.now(tz=UTC)
    await (
        RecurringRoute.objects.filter(id=route.id)
        .filter(Q(last_viewed_at__isnull=True) | Q(last_viewed_at__lt=now - VIEW_RECORD_INTERVAL))
        .aupdate(last_viewed_at=now)
    )


@router.get("/routes/{route_id}/forecast", response={200: ForecastJobOut, 202: ForecastJobOut})
async def route_forecast(
    request: HttpRequest,
    route_id: UUID,
    date: str,  # YYYY-MM-DD
    time: str,  # HH:MM
    departure_flex_before_minutes: int | None = None,
    departure_flex_after_minutes: int | None = None,
):
    """Start (or join) the forecast for one departure of a saved route.

    Returns 200 with the finished payload -- weather and sections -- when an identical
    forecast is already computed and still fresh, otherwise 202 and a job to watch over
    `wsUrl`. Cell fetching and assembly both happen on workers; neither is allowed on this
    path.
    """
    route = await _owned_route(request, route_id)

    user = await _current_user(request)
    if route.id not in await sync_to_async(allowed_route_ids)(user):
        raise HttpError(402, "This route is paused. Choose your active routes in your account or try Plus.")
    limits = await entitlements_for(user)
    if not limits.departure_comparison and (departure_flex_before_minutes or departure_flex_after_minutes):
        raise HttpError(402, "Departure comparison requires Plus.")
    if not route.sample_points:
        raise HttpError(409, "Route geometry not yet computed. Try again in a few seconds.")

    if request.headers.get(PREFETCH_HEADER) != "1":
        await _record_view(route)

    departure = f"{date}T{time}"
    before = (
        route.departure_flex_before_minutes if departure_flex_before_minutes is None else departure_flex_before_minutes
    )
    after = route.departure_flex_after_minutes if departure_flex_after_minutes is None else departure_flex_after_minutes
    if not limits.departure_comparison:
        before = after = 0
    flexibility_params(departure, before, after)  # validation only: 422 on a bad window
    job = await start_forecast_job(
        ForecastJob.Kind.ROUTE,
        await _current_user(request),
        route_job_params(route.id, departure, before, after),
    )
    status = 200 if job.status == ForecastJob.Status.DONE else 202
    return status, job_out(job)


def _create_with_quota(user, return_schedule_cron=None, return_schedule_description="", **values):
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        limits = entitlements_for_sync(user)
        if (
            values.get("active", True)
            and RecurringRoute.objects.filter(owner=user, active=True, return_of__isnull=True).count()
            >= limits.max_routes
        ):
            telemetry.event("route.action", action="quota", outcome="rejected", **telemetry.user_context(user))
            raise HttpError(402, "Your active route limit is reached. Plus includes 20 routes.")
        route = RecurringRoute.objects.create(owner=user, **values)
        _save_return(route, return_schedule_cron, return_schedule_description)
        return route


def _save_with_quota(user, route, return_schedule_cron=None, return_schedule_description=""):
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        existing = RecurringRoute.objects.get(pk=route.pk, owner=user)
        if route.active and not existing.active:
            limits = entitlements_for_sync(user)
            if (
                RecurringRoute.objects.filter(owner=user, active=True, return_of__isnull=True).count()
                >= limits.max_routes
            ):
                raise HttpError(402, "Your active route limit is reached.")
        route.save()
        _save_return(route, return_schedule_cron, return_schedule_description)


def _save_return(route, cron, description):
    if route.return_of_id:
        if cron:
            raise HttpError(422, "A return journey cannot have another return journey.")
        return
    returning = RecurringRoute.objects.filter(return_of=route).first()
    if cron is None and returning is None:
        return
    if cron == "":
        if returning:
            returning.delete()
        route._state.fields_cache["return_journey"] = None
        return
    values = {
        "owner": route.owner,
        "name": f"{route.name[:188]} – Rückfahrt",
        "description": route.description,
        "start_point": route.destination_point,
        "start_name": route.dest_name,
        "destination_point": route.start_point,
        "dest_name": route.start_name,
        "via_points": list(reversed(route.via_points or [])),
        "profile": route.profile,
        "schedule_cron": cron or returning.schedule_cron,
        "schedule_description": description or (returning.schedule_description if returning else cron),
        "active": route.active,
        "briefing_channel": route.briefing_channel,
        "departure_flex_before_minutes": route.departure_flex_before_minutes,
        "departure_flex_after_minutes": route.departure_flex_after_minutes,
    }
    if returning and (
        returning.start_point != values["start_point"]
        or returning.destination_point != values["destination_point"]
        or returning.profile != values["profile"]
        or returning.via_points != values["via_points"]
    ):
        values.update(sample_points=None, polyline=None, vertex_times=None, geometry_fetched_at=None)
    returning, _ = RecurringRoute.objects.update_or_create(return_of=route, defaults=values)
    RideBriefing.objects.filter(route=returning, status="pending").update(status="canceled")
    route._state.fields_cache["return_journey"] = returning
