"""Public routes: sharing settings, photos, comments, likes, and a visitor's own forecast.

Two routers. ``owner_router`` (session auth) is the owner's side: publish or hide a route,
choose its privacy zone, manage its photos. ``public_router`` (optional session auth) is
what anyone can read at ``/r/<slug>``; writing there (comment, like, copy) needs a session.

Everything a visitor sees comes from ``core.public_routes.public_geometry``: never the start,
the destination, their names, the via points or the schedule. A photo inside a privacy zone
keeps its picture but loses its map position. Tests pin each of those.
"""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.gis.geos import Polygon
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import FileResponse, HttpRequest
from django.utils.translation import gettext
from loguru import logger
from ninja import File, Form, Router, UploadedFile
from ninja.errors import HttpError
from pydantic import Field, field_validator
from redis.exceptions import RedisError

from ..auth.backend import optional_session_auth, session_auth
from ..departures import candidate_times
from ..elevation import ElevationOut
from ..entitlements import entitlements_for_sync
from ..forecast_schemas import ForecastJobOut
from ..geo import simplify_line
from ..models import ForecastJob, RecurringRoute, RouteComment, RouteLike, RoutePhoto, User, route_point
from ..photos import PhotoError, process_photo
from ..public_routes import PRIVACY_ZONE_CHOICES, ascent_m, in_privacy_zone, new_slug, public_geometry
from ..schedule import check_schedule_cron
from ..schemas import CamelSchema
from ..tasks import start_forecast_job
from .elevation import profile
from .recurring_route import RecurringRouteIn, RecurringRouteOut, create_route
from .route_weather import job_out

owner_router = Router(auth=session_auth, tags=["Sharing"])
public_router = Router(auth=optional_session_auth, tags=["Public routes"])

# Enough vertices to draw a card's glyph; the detail page gets the whole line.
CARD_PATH_VERTICES = 80
COMMENT_LIMIT_PER_MINUTE = 6
PUBLIC_PAGE_SIZE = 24


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SharingIn(CamelSchema):
    visibility: Literal["private", "public"]
    privacy_zone_m: int = 500

    @field_validator("privacy_zone_m")
    @classmethod
    def check_zone(cls, value: int) -> int:
        if value not in PRIVACY_ZONE_CHOICES:
            raise ValueError(gettext("Eine von %(choices)s.") % {"choices": ", ".join(map(str, PRIVACY_ZONE_CHOICES))})
        return value


class SharingOut(CamelSchema):
    visibility: Literal["private", "public"]
    public_slug: str | None = None
    published_at: datetime | None = None
    privacy_zone_m: int
    # What the public sees with this zone; None when the zone leaves too little or there is
    # no geometry yet.
    public_distance_m: float | None = None
    public_line: list[list[float]] | None = None


class PhotoOut(CamelSchema):
    id: UUID
    url: str
    thumbnail_url: str
    width: int
    height: int
    caption: str
    lat: float | None = None
    lon: float | None = None
    created_at: datetime


class PhotoUpdateIn(CamelSchema):
    caption: str = Field(default="", max_length=500)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)


class PublicRouteSummary(CamelSchema):
    slug: str
    name: str
    description: str
    author: str
    profile: str
    distance_m: float
    duration_s: int
    ascent_m: float | None = None
    published_at: datetime | None = None
    like_count: int = 0
    comment_count: int = 0
    photo_count: int = 0
    cover_url: str | None = None
    path: list[list[float]] = Field(default_factory=list)


class PublicRouteDetail(PublicRouteSummary):
    line: list[list[float]]
    photos: list[PhotoOut]
    is_owner: bool = False
    liked: bool = False


class CommentIn(CamelSchema):
    body: str = Field(min_length=1, max_length=2000)

    @field_validator("body")
    @classmethod
    def strip_body(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError(gettext("Der Kommentar ist leer."))
        return value


class CommentOut(CamelSchema):
    id: UUID
    author: str
    body: str
    created_at: datetime
    edited_at: datetime | None = None
    can_edit: bool = False
    can_delete: bool = False


class LikeOut(CamelSchema):
    liked: bool
    like_count: int


class CopyIn(CamelSchema):
    name: str | None = Field(default=None, max_length=200)
    schedule_cron: str = "0 8 * * 6"
    schedule_description: str = Field(default="Jeden Samstag um 08:00", max_length=200)

    _schedule_cron = field_validator("schedule_cron")(check_schedule_cron)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _viewer(request: HttpRequest) -> User | None:
    user = getattr(request, "auth", None)
    return user if isinstance(user, User) and user.is_authenticated else None


def _require_viewer(request: HttpRequest) -> User:
    user = _viewer(request)
    if user is None:
        raise HttpError(401, gettext("Bitte melde dich an."))
    return user


async def _owned_route(request: HttpRequest, route_id: UUID) -> RecurringRoute:
    try:
        return await RecurringRoute.objects.aget(id=route_id, owner=_require_viewer(request))
    except RecurringRoute.DoesNotExist:
        raise HttpError(404, gettext("Route nicht gefunden.")) from None


async def _public_route(slug: str) -> RecurringRoute:
    route = (
        await RecurringRoute.objects.select_related("owner")
        .filter(public_slug=slug, visibility=RecurringRoute.Visibility.PUBLIC, owner__isnull=False)
        .afirst()
    )
    if route is None:
        raise HttpError(404, gettext("Route nicht gefunden."))
    return route


def _allowed(key: str, limit: int) -> bool:
    """Per-minute counter. Fails open, like the preview limit: it guards load, not access."""
    key = f"{key}:{int(datetime.now(tz=UTC).timestamp() // 60)}"
    try:
        cache.add(key, 0, 90)
        return cache.incr(key) <= limit
    except (RedisError, OSError, ValueError) as exc:
        logger.warning(f"Community rate limit unavailable: {exc}")
        return True


def photo_out(photo: RoutePhoto, *, public: bool) -> PhotoOut:
    lon = lat = None
    if photo.location is not None:
        lon, lat = photo.location.x, photo.location.y
        if public and in_privacy_zone(photo.route, lon, lat):
            lon = lat = None
    return PhotoOut(
        id=photo.id,
        url=f"/api/photos/{photo.id}/full",
        thumbnail_url=f"/api/photos/{photo.id}/thumb",
        width=photo.width,
        height=photo.height,
        caption=photo.caption,
        lat=lat,
        lon=lon,
        created_at=photo.created_at,
    )


def _card_path(line: list[list[float]]) -> list[list[float]]:
    if len(line) <= CARD_PATH_VERTICES:
        return line
    tolerance = 5.0
    kept = simplify_line(line, (), tolerance)
    while len(kept) > CARD_PATH_VERTICES:
        tolerance *= 2
        kept = simplify_line(line, (), tolerance)
    return [[round(line[i][0], 5), round(line[i][1], 5)] for i in kept]


def _summary_values(route: RecurringRoute, geometry: dict) -> dict:
    cover = next(iter(getattr(route, "cover_photos", None) or ()), None)
    return {
        "slug": route.public_slug,
        "name": route.name,
        "description": route.description,
        "author": route.owner.username,
        "profile": route.profile,
        "distance_m": geometry["total_distance_m"],
        "duration_s": geometry["total_seconds"],
        "ascent_m": ascent_m(geometry["vertex_elevations"]),
        "published_at": route.published_at,
        "like_count": getattr(route, "like_count", 0),
        "comment_count": getattr(route, "comment_count", 0),
        "photo_count": getattr(route, "photo_count", 0),
        "cover_url": f"/api/photos/{cover.id}/thumb" if cover else None,
        "path": _card_path(geometry["polyline"]),
    }


def _with_counts(query):
    return query.annotate(
        like_count=Count("likes", distinct=True),
        comment_count=Count("comments", distinct=True),
        photo_count=Count("photos", distinct=True),
    )


def _sharing_out(route: RecurringRoute) -> SharingOut:
    geometry = public_geometry(route)
    return SharingOut(
        visibility=route.visibility,
        public_slug=route.public_slug,
        published_at=route.published_at,
        privacy_zone_m=route.privacy_zone_m,
        public_distance_m=geometry["total_distance_m"] if geometry else None,
        public_line=geometry["polyline"] if geometry else None,
    )


# ---------------------------------------------------------------------------
# The owner's sharing settings
# ---------------------------------------------------------------------------


@owner_router.get("/routes/{route_id}/sharing", response=SharingOut)
async def get_sharing(request: HttpRequest, route_id: UUID):
    return _sharing_out(await _owned_route(request, route_id))


@owner_router.put("/routes/{route_id}/sharing", response=SharingOut)
async def update_sharing(request: HttpRequest, route_id: UUID, data: SharingIn):
    """Publish or hide a route, and choose how much of either end stays hidden."""
    route = await _owned_route(request, route_id)
    route.privacy_zone_m = data.privacy_zone_m
    if data.visibility == RecurringRoute.Visibility.PUBLIC:
        if not route.polyline:
            raise HttpError(409, gettext("Die Strecke wird noch berechnet. Bitte gleich nochmal versuchen."))
        if public_geometry(route) is None:
            raise HttpError(
                422, gettext("Zwischen den Privatsphäre-Zonen bleibt zu wenig Strecke. Wähle eine kleinere Zone.")
            )
        route.published_at = route.published_at or datetime.now(tz=UTC)
    route.visibility = data.visibility
    fields = ["visibility", "privacy_zone_m", "published_at", "public_slug", "updated_at"]
    for _ in range(5):
        route.public_slug = route.public_slug or (new_slug() if data.visibility == "public" else None)
        try:
            await route.asave(update_fields=fields)
            break
        except IntegrityError:
            route.public_slug = None  # a slug collision: roll again
    else:
        raise HttpError(503, gettext("Bitte nochmal versuchen."))
    return _sharing_out(route)


# ---------------------------------------------------------------------------
# The owner's photos
# ---------------------------------------------------------------------------


@owner_router.get("/routes/{route_id}/photos", response=list[PhotoOut])
async def list_photos(request: HttpRequest, route_id: UUID):
    route = await _owned_route(request, route_id)
    return [photo_out(p, public=False) async for p in route.photos.select_related("route")]


def _store_photo(route: RecurringRoute, user: User, data: bytes, caption: str, location) -> RoutePhoto:
    processed = process_photo(data)
    photo = RoutePhoto(
        route=route,
        uploader=user,
        caption=caption,
        location=location,
        width=processed.width,
        height=processed.height,
    )
    with transaction.atomic():
        # Locked so two parallel uploads cannot both take the last free slot.
        RecurringRoute.objects.select_for_update().filter(id=route.id).first()
        limit = entitlements_for_sync(user)
        if RoutePhoto.objects.filter(route=route).count() >= limit.max_route_photos:
            raise HttpError(402, gettext("Höchstens %(n)s Fotos pro Route.") % {"n": limit.max_route_photos})
        photo.image.save(f"{photo.id}.jpg", ContentFile(processed.image), save=False)
        photo.thumbnail.save(f"{photo.id}-thumb.jpg", ContentFile(processed.thumbnail), save=False)
        photo.save()
    return photo


@owner_router.post("/routes/{route_id}/photos", response=PhotoOut)
async def upload_photo(
    request: HttpRequest,
    route_id: UUID,
    file: File[UploadedFile],
    caption: Form[str] = "",
    lat: Form[float | None] = None,
    lon: Form[float | None] = None,
):
    """Add a photo. It is re-encoded without metadata; its position is only what the owner sets."""
    route = await _owned_route(request, route_id)
    user = _require_viewer(request)
    if file.size is not None and file.size > settings.PHOTO_UPLOAD_MAX_BYTES:
        raise HttpError(413, gettext("Das Foto ist grösser als 20 MB."))
    if len(caption) > 500:
        raise HttpError(422, gettext("Die Bildunterschrift ist zu lang."))
    location = None
    if lat is not None and lon is not None:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise HttpError(422, gettext("Ungültige Position."))
        location = route_point(lat, lon)
    try:
        photo = await sync_to_async(_store_photo)(route, user, file.read(), caption.strip(), location)
    except PhotoError as exc:
        raise HttpError(422, str(exc)) from None
    photo.route = route
    return photo_out(photo, public=False)


async def _owned_photo(request: HttpRequest, route_id: UUID, photo_id: UUID) -> RoutePhoto:
    route = await _owned_route(request, route_id)
    photo = await RoutePhoto.objects.filter(id=photo_id, route=route).afirst()
    if photo is None:
        raise HttpError(404, gettext("Foto nicht gefunden."))
    photo.route = route
    return photo


@owner_router.patch("/routes/{route_id}/photos/{photo_id}", response=PhotoOut)
async def update_photo(request: HttpRequest, route_id: UUID, photo_id: UUID, data: PhotoUpdateIn):
    photo = await _owned_photo(request, route_id, photo_id)
    photo.caption = data.caption.strip()
    photo.location = route_point(data.lat, data.lon) if data.lat is not None and data.lon is not None else None
    await photo.asave(update_fields=["caption", "location"])
    return photo_out(photo, public=False)


@owner_router.delete("/routes/{route_id}/photos/{photo_id}", response={204: None})
async def delete_photo(request: HttpRequest, route_id: UUID, photo_id: UUID):
    photo = await _owned_photo(request, route_id, photo_id)
    await photo.adelete()  # the files go too, see core.signals
    return 204, None


# ---------------------------------------------------------------------------
# Photo files
# ---------------------------------------------------------------------------


@public_router.get("/photos/{photo_id}/{size}", response={200: None})
async def photo_file(request: HttpRequest, photo_id: UUID, size: Literal["full", "thumb"]):
    """The image itself: to anyone when its route is public, else only to the route's owner."""
    photo = await RoutePhoto.objects.select_related("route").filter(id=photo_id).afirst()
    viewer = _viewer(request)
    public = photo is not None and photo.route.visibility == RecurringRoute.Visibility.PUBLIC
    if photo is None or not (public or (viewer and viewer.id == photo.route.owner_id)):
        raise HttpError(404, gettext("Foto nicht gefunden."))
    field = photo.image if size == "full" else photo.thumbnail
    try:
        handle = await sync_to_async(field.open)("rb")
    except FileNotFoundError:
        raise HttpError(404, gettext("Foto nicht gefunden.")) from None
    response = FileResponse(handle, content_type="image/jpeg")
    # Files never change (a new photo is a new id). An hour keeps a route made private again
    # from lingering long in shared caches.
    response["Cache-Control"] = f"{'public' if public else 'private'}, max-age=3600, immutable"
    response["X-Content-Type-Options"] = "nosniff"
    return response


# ---------------------------------------------------------------------------
# Reading a public route
# ---------------------------------------------------------------------------


def _public_query():
    return (
        RecurringRoute.objects.filter(
            visibility=RecurringRoute.Visibility.PUBLIC,
            public_slug__isnull=False,
            owner__isnull=False,
            polyline__isnull=False,
        )
        .select_related("owner")
        .defer("thumbnail", "imported_coordinates")
    )


@public_router.get("/public/routes", response=list[PublicRouteSummary])
async def list_public_routes(
    request: HttpRequest,
    sort: Literal["new", "popular"] = "new",
    q: str = "",
    bbox: str | None = None,
    offset: int = 0,
):
    """Discover public routes, newest or most liked first, optionally inside a map box.

    ``bbox`` is ``minLon,minLat,maxLon,maxLat``. It is checked against the public line, so a
    route never turns up for a box that only its hidden ends touch.
    """
    query = _with_counts(_public_query())
    if q.strip():
        query = query.filter(Q(name__icontains=q.strip()) | Q(description__icontains=q.strip()))
    box = None
    if bbox:
        try:
            box = [float(v) for v in bbox.split(",")]
            if len(box) != 4:
                raise ValueError
        except ValueError:
            raise HttpError(422, gettext("bbox ist minLon,minLat,maxLon,maxLat")) from None
        query = query.filter(polyline__intersects=Polygon.from_bbox(box))
    order = ("-like_count", "-published_at") if sort == "popular" else ("-published_at",)
    offset = max(0, offset)
    rows = [r async for r in query.order_by(*order)[offset : offset + PUBLIC_PAGE_SIZE * 2]]
    await sync_to_async(_attach_covers)(rows)
    result = []
    for route in rows:
        geometry = public_geometry(route)
        if geometry is None:
            continue
        if box and not any(box[0] <= lon <= box[2] and box[1] <= lat <= box[3] for lon, lat in geometry["polyline"]):
            continue
        result.append(PublicRouteSummary(**_summary_values(route, geometry)))
        if len(result) == PUBLIC_PAGE_SIZE:
            break
    return result


def _attach_covers(routes: list[RecurringRoute]) -> None:
    covers: dict = {}
    for photo in RoutePhoto.objects.filter(route__in=routes).order_by("route_id", "created_at").only("id", "route_id"):
        covers.setdefault(photo.route_id, [photo])
    for route in routes:
        route.cover_photos = covers.get(route.id, [])


@public_router.get("/public/routes/{slug}", response=PublicRouteDetail)
async def get_public_route(request: HttpRequest, slug: str):
    route = await _public_route(slug)
    route = await _with_counts(_public_query()).aget(id=route.id)
    geometry = public_geometry(route)
    if geometry is None:
        raise HttpError(404, gettext("Route nicht gefunden."))
    photos = [p async for p in route.photos.all()]
    for photo in photos:
        photo.route = route
    route.cover_photos = photos[:1]
    viewer = _viewer(request)
    liked = bool(viewer) and await RouteLike.objects.filter(route=route, user=viewer).aexists()
    return PublicRouteDetail(
        **_summary_values(route, geometry),
        line=geometry["polyline"],
        photos=[photo_out(p, public=True) for p in photos],
        is_owner=bool(viewer) and viewer.id == route.owner_id,
        liked=liked,
    )


@public_router.get("/public/routes/{slug}/elevation", response=ElevationOut)
async def public_route_elevation(request: HttpRequest, slug: str):
    geometry = public_geometry(await _public_route(slug))
    if geometry is None:
        raise HttpError(404, gettext("Route nicht gefunden."))
    line = geometry["polyline"]
    elevations = geometry["vertex_elevations"]
    coordinates = [[*p, h] for p, h in zip(line, elevations, strict=True)] if elevations else line
    return await profile(coordinates, geometry["total_seconds"], geometry["vertex_times"])


@public_router.get(
    "/public/routes/{slug}/forecast", response={200: ForecastJobOut, 202: ForecastJobOut}, auth=session_auth
)
async def public_route_forecast(request: HttpRequest, slug: str, date: str, time: str):
    """The weather on this route for the visitor's own departure: a forecast job like any other.

    Signed-in visitors only, like all planning. The job is the visitor's, so the result is
    shaped for the visitor's tier, and it runs on the public line only.
    """
    route = await _public_route(slug)
    departure = f"{date}T{time}"
    try:
        candidate_times({"departure_time": departure})
    except ValueError:
        raise HttpError(422, gettext("Ungültige Abfahrtszeit.")) from None
    job = await start_forecast_job(
        ForecastJob.Kind.PUBLIC_ROUTE,
        _viewer(request),
        {"public_route_id": str(route.id), "departure_time": departure},
    )
    status = 200 if job.status == ForecastJob.Status.DONE else 202
    return status, job_out(job)


# ---------------------------------------------------------------------------
# Comments and likes
# ---------------------------------------------------------------------------


def _comment_out(comment: RouteComment, viewer: User | None, route_owner_id: int | None) -> CommentOut:
    mine = viewer is not None and viewer.id == comment.author_id
    return CommentOut(
        id=comment.id,
        author=comment.author.username,
        body=comment.body,
        created_at=comment.created_at,
        edited_at=comment.edited_at,
        can_edit=mine,
        can_delete=mine or (viewer is not None and viewer.id == route_owner_id),
    )


@public_router.get("/public/routes/{slug}/comments", response=list[CommentOut])
async def list_comments(request: HttpRequest, slug: str):
    route = await _public_route(slug)
    viewer = _viewer(request)
    return [
        _comment_out(c, viewer, route.owner_id)
        async for c in route.comments.select_related("author").order_by("created_at")
    ]


@public_router.post("/public/routes/{slug}/comments", response=CommentOut)
async def add_comment(request: HttpRequest, slug: str, data: CommentIn):
    viewer = _require_viewer(request)
    route = await _public_route(slug)
    if not _allowed(f"comment:{viewer.id}", COMMENT_LIMIT_PER_MINUTE):
        raise HttpError(429, gettext("Zu viele Kommentare. Bitte kurz warten."))
    comment = await RouteComment.objects.acreate(route=route, author=viewer, body=data.body)
    comment.author = viewer
    return _comment_out(comment, viewer, route.owner_id)


async def _comment(comment_id: UUID) -> RouteComment:
    comment = await RouteComment.objects.select_related("author", "route").filter(id=comment_id).afirst()
    if comment is None:
        raise HttpError(404, gettext("Kommentar nicht gefunden."))
    return comment


@public_router.patch("/public/comments/{comment_id}", response=CommentOut)
async def edit_comment(request: HttpRequest, comment_id: UUID, data: CommentIn):
    viewer = _require_viewer(request)
    comment = await _comment(comment_id)
    if comment.author_id != viewer.id:
        raise HttpError(404, gettext("Kommentar nicht gefunden."))
    comment.body = data.body
    comment.edited_at = datetime.now(tz=UTC)
    await comment.asave(update_fields=["body", "edited_at"])
    return _comment_out(comment, viewer, comment.route.owner_id)


@public_router.delete("/public/comments/{comment_id}", response={204: None})
async def delete_comment(request: HttpRequest, comment_id: UUID):
    """The author may delete a comment, and so may the route's owner: it is their page."""
    viewer = _require_viewer(request)
    comment = await _comment(comment_id)
    if viewer.id not in {comment.author_id, comment.route.owner_id}:
        raise HttpError(404, gettext("Kommentar nicht gefunden."))
    await comment.adelete()
    return 204, None


async def _like_out(route: RecurringRoute, liked: bool) -> LikeOut:
    return LikeOut(liked=liked, like_count=await RouteLike.objects.filter(route=route).acount())


@public_router.put("/public/routes/{slug}/like", response=LikeOut)
async def like_route(request: HttpRequest, slug: str):
    viewer = _require_viewer(request)
    route = await _public_route(slug)
    await RouteLike.objects.aget_or_create(route=route, user=viewer)
    return await _like_out(route, liked=True)


@public_router.delete("/public/routes/{slug}/like", response=LikeOut)
async def unlike_route(request: HttpRequest, slug: str):
    viewer = _require_viewer(request)
    route = await _public_route(slug)
    await RouteLike.objects.filter(route=route, user=viewer).adelete()
    return await _like_out(route, liked=False)


# ---------------------------------------------------------------------------
# Riding it yourself
# ---------------------------------------------------------------------------


@public_router.post("/public/routes/{slug}/copy", response=RecurringRouteOut)
async def copy_route(request: HttpRequest, slug: str, data: CopyIn):
    """Save the public line as one of the visitor's own routes, with their own schedule.

    It is an imported path, so the copy rides exactly the line shown (never the hidden ends)
    and counts against the visitor's route quota like any other route.
    """
    _require_viewer(request)
    route = await _public_route(slug)
    geometry = public_geometry(route)
    if geometry is None:
        raise HttpError(404, gettext("Route nicht gefunden."))
    line = geometry["polyline"]
    route_in = RecurringRouteIn(
        name=data.name or route.name,
        description=f"Öffentliche Route von {route.owner.username}",
        start_lat=line[0][1],
        start_lon=line[0][0],
        start_name="Start",
        dest_lat=line[-1][1],
        dest_lon=line[-1][0],
        dest_name="Ziel",
        geometry_source="imported",
        imported_coordinates=line,
        duration_seconds=max(1, geometry["total_seconds"]),
        profile=route.profile,
        schedule_cron=data.schedule_cron,
        schedule_description=data.schedule_description,
    )
    # create_route applies the route quota (402) and enqueues the geometry, as for any new route.
    return await create_route(request, route_in)
