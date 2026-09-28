"""Read-only operations data. These views deliberately never use forecast/job endpoints."""

from datetime import date, datetime, timedelta
from math import isfinite
from typing import Literal
from uuid import UUID

from django.contrib.gis.db.models import Extent, GeometryField
from django.contrib.gis.geos import Polygon
from django.db.models import Count, Max, Min, Q
from django.db.models.functions import Cast
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Query, Router
from ninja.errors import HttpError
from ninja.utils import check_csrf
from pydantic import Field

from ..auth.admin_access import has_system_access
from ..departures import cell_covers, instant
from ..geo import simplify_line
from ..grid import ENSEMBLE_MODELS, ENSEMBLE_REQUEST_VERSION, MAX_CELL_AGE
from ..jobs import JOB_STALL_TIMEOUT
from ..models import EnsembleCell, ForecastCell, ForecastJob, Journey, JourneyStage, RecurringRoute
from ..schedule import LOCAL_TZ, next_departure
from ..schemas import CamelSchema
from ..uncertainty import extract_uncertainty
from ..weather import forecast_days_for


def system_auth(request):
    if check_csrf(request):
        raise HttpError(403, "CSRF check failed.")
    if not request.user.is_authenticated:
        raise HttpError(401, "Sign in first.")
    if not has_system_access(request.user, request.session):
        raise HttpError(403, "Administrator access and admin verification are required.")
    return request.user


router = Router(tags=["system"], auth=system_auth)


class SystemCacheCount(CamelSchema):
    kind: str
    source: str
    records: int
    fresh: int
    stale: int
    locations: int


class SystemSummary(CamelSchema):
    generated_at: datetime
    max_cell_age_seconds: int
    recurring_routes: int
    journeys: int
    stages: int
    missing_geometry: int
    cache_locations: int
    caches: list[SystemCacheCount]
    bounds: list[float] | None


class SystemFeature(CamelSchema):
    id: str
    kind: str
    coordinates: list[list[float]]
    name: str = ""
    profile: str = ""
    active: bool | None = None
    rank: int | None = None
    source: str = ""
    fetched_at: datetime | None = None
    day_key: date | None = None
    forecast_days: int | None = None
    lat: float | None = None
    lon: float | None = None


class SystemMapPage(CamelSchema):
    items: list[SystemFeature]
    total: int
    next_offset: int | None
    generated_at: datetime


class SystemMapFilter(CamelSchema):
    layer: Literal["routes", "journeys", "cells"]
    bbox: str | None = None
    zoom: float = Field(default=8, ge=0, le=24)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=1000)
    kind: Literal["forecast", "ensemble"] = "forecast"
    source: Literal["all", "open-meteo", "openweathermap"] = "all"
    day: date | None = None
    active: bool | None = None
    profile: Literal["bike", "ebike", "fast_ebike", "hike"] | None = None
    alternatives: bool = False


def viewport_boxes(value):
    if value is None:
        return []
    try:
        west, south, east, north = (float(n) for n in value.split(","))
    except (TypeError, ValueError) as exc:
        raise HttpError(422, "bbox must be west,south,east,north.") from exc
    if not all(isfinite(n) for n in (west, south, east, north)) or not (
        -180 <= west <= 180 and -180 <= east <= 180 and -90 <= south < north <= 90
    ):
        raise HttpError(422, "Invalid map bounds.")
    return [(west, south, east, north)] if west <= east else [(west, south, 180, north), (-180, south, east, north)]


def cell_ring(lat, lon):
    west, east = max(-180, lon - 0.005), min(180, lon + 0.005)
    south, north = max(-90, lat - 0.005), min(90, lat + 0.005)
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


def cell_feature(row, kind):
    return {
        "id": f"{kind}:{row['id']}",
        "kind": kind,
        "coordinates": cell_ring(row["lat_r"], row["lon_r"]),
        "lat": row["lat_r"],
        "lon": row["lon_r"],
        "source": row.get("source", "open-meteo-ensemble"),
        "fetched_at": row["fetched_at"],
        "day_key": row["day_key"],
        "forecast_days": row["forecast_days"],
    }


CELL_FIELDS = ("id", "lat_r", "lon_r", "fetched_at", "day_key", "forecast_days")


@router.get("/summary", response=SystemSummary)
def summary(request):
    now = timezone.now()
    caches = []
    for kind, model in (("forecast", ForecastCell), ("ensemble", EnsembleCell)):
        sources = (
            list(model.objects.values_list("source", flat=True).distinct())
            if kind == "forecast"
            else ["open-meteo-ensemble"]
        )
        for source in sources:
            rows = model.objects.filter(source=source) if kind == "forecast" else model.objects.all()
            counts = rows.aggregate(
                records=Count("pk"), fresh=Count("pk", filter=Q(fetched_at__gte=now - MAX_CELL_AGE))
            )
            caches.append(
                {
                    "kind": kind,
                    "source": source,
                    **counts,
                    "stale": counts["records"] - counts["fresh"],
                    "locations": rows.values("lat_r", "lon_r").distinct().count(),
                }
            )
    extents = []
    for model in (RecurringRoute, JourneyStage):
        extent = model.objects.aggregate(bounds=Extent(Cast("polyline", GeometryField())))["bounds"]
        if extent:
            extents.append(extent)
    if not extents:
        for model in (ForecastCell, EnsembleCell):
            b = model.objects.aggregate(west=Min("lon_r"), east=Max("lon_r"), south=Min("lat_r"), north=Max("lat_r"))
            if b["west"] is not None:
                extents.append((b["west"] - 0.005, b["south"] - 0.005, b["east"] + 0.005, b["north"] + 0.005))
    bounds = (
        [min(b[0] for b in extents), min(b[1] for b in extents), max(b[2] for b in extents), max(b[3] for b in extents)]
        if extents
        else None
    )
    locations = (
        ForecastCell.objects.values_list("lat_r", "lon_r")
        .union(EnsembleCell.objects.values_list("lat_r", "lon_r"))
        .count()
    )
    return {
        "generated_at": now,
        "max_cell_age_seconds": int(MAX_CELL_AGE.total_seconds()),
        "recurring_routes": RecurringRoute.objects.count(),
        "journeys": Journey.objects.count(),
        "stages": JourneyStage.objects.count(),
        "missing_geometry": RecurringRoute.objects.filter(polyline__isnull=True).count()
        + Journey.objects.exclude(days__stages__isnull=False).count(),
        "cache_locations": locations,
        "caches": caches,
        "bounds": bounds,
    }


@router.get("/map", response=SystemMapPage)
def map_features(request, filters: Query[SystemMapFilter]):
    boxes = viewport_boxes(filters.bbox)
    if filters.layer == "cells":
        model = ForecastCell if filters.kind == "forecast" else EnsembleCell
        rows = model.objects.all()
        if boxes:
            rows = rows.filter(
                Q(
                    *(
                        Q(lon_r__gte=w - 0.005, lon_r__lte=e + 0.005, lat_r__gte=s - 0.005, lat_r__lte=n + 0.005)
                        for w, s, e, n in boxes
                    ),
                    _connector=Q.OR,
                )
            )
        if filters.day:
            rows = rows.filter(day_key=filters.day)
        if filters.kind == "forecast" and filters.source != "all":
            rows = rows.filter(source=filters.source)
        fields = (*CELL_FIELDS, "source") if filters.kind == "forecast" else CELL_FIELDS
        rows = rows.order_by("lat_r", "lon_r", "-fetched_at", "-id").distinct("lat_r", "lon_r").values(*fields)
        total = rows.count()
        items = [cell_feature(row, filters.kind) for row in rows[filters.offset : filters.offset + filters.limit]]
    else:
        recurring = filters.layer == "routes"
        rows = RecurringRoute.objects.filter(polyline__isnull=False) if recurring else JourneyStage.objects.all()
        if boxes:
            rows = rows.filter(Q(*(Q(polyline__intersects=Polygon.from_bbox(box)) for box in boxes), _connector=Q.OR))
        if recurring and filters.active is not None:
            rows = rows.filter(active=filters.active)
        if filters.profile:
            rows = rows.filter(**{"profile" if recurring else "day__journey__profile": filters.profile})
        if not recurring and not filters.alternatives:
            rows = rows.filter(rank=0)
        total = rows.count()
        fields = (
            ("id", "polyline", "name", "profile", "active")
            if recurring
            else ("id", "polyline", "rank", "day__index", "day__journey__name", "day__journey__profile")
        )
        items = []
        for row in rows.order_by("pk").values(*fields)[filters.offset : filters.offset + filters.limit]:
            coords = [list(p[:2]) for p in row["polyline"].coords]
            tolerance = 100 if filters.zoom < 8 else 50 if filters.zoom < 12 else 10 if filters.zoom < 15 else 0
            if tolerance:
                coords = [coords[i] for i in simplify_line(coords, [], tolerance)]
            items.append(
                {
                    "id": str(row["id"]),
                    "kind": "route" if recurring else "stage",
                    "coordinates": coords,
                    "name": row["name"] if recurring else f"{row['day__journey__name']} · {row['day__index'] + 1}",
                    "profile": row["profile"] if recurring else row["day__journey__profile"],
                    "active": row.get("active"),
                    "rank": row.get("rank"),
                }
            )
    following = filters.offset + len(items)
    return {
        "items": items,
        "total": total,
        "next_offset": following if following < total else None,
        "generated_at": timezone.now(),
    }


@router.get("/cells", response=SystemMapPage)
def cell_history(request, lat: float, lon: float, offset: int = 0, limit: int = 25):
    if (
        not isfinite(lat)
        or not isfinite(lon)
        or not -90 <= lat <= 90
        or not -180 <= lon <= 180
        or offset < 0
        or not 1 <= limit <= 100
    ):
        raise HttpError(422, "Invalid cell or page.")
    # Union only metadata; raw forecast payloads never leave the server.
    from django.db.models import CharField, Value

    fields = (*CELL_FIELDS, "source", "kind")
    forecasts = (
        ForecastCell.objects.filter(lat_r=lat, lon_r=lon)
        .annotate(kind=Value("forecast", output_field=CharField()))
        .values(*fields)
    )
    ensembles = (
        EnsembleCell.objects.filter(lat_r=lat, lon_r=lon)
        .annotate(
            source=Value("open-meteo-ensemble", output_field=CharField()),
            kind=Value("ensemble", output_field=CharField()),
        )
        .values(*fields)
    )
    rows = forecasts.union(ensembles, all=True).order_by("-fetched_at", "kind", "id")
    total = rows.count()
    items = [cell_feature(row, row["kind"]) for row in rows[offset : offset + limit]]
    return {
        "items": items,
        "total": total,
        "next_offset": offset + len(items) if offset + len(items) < total else None,
        "generated_at": timezone.now(),
    }


class SystemCoveragePoint(CamelSchema):
    lat: float
    lon: float
    forecast: Literal["usable", "stale", "missing", "insufficient"]
    ensemble: Literal["usable", "stale", "missing", "insufficient"]


class SystemCoverage(CamelSchema):
    id: str
    kind: str
    name: str
    profile: str
    distance_m: float | None
    duration_seconds: int | None
    geometry_fetched_at: datetime | None
    departure: datetime | None
    unavailable: str | None
    points: list[SystemCoveragePoint]


def coverage_state(rows, required_days, etas, now, *, ensemble=False):
    if not ensemble:
        rows = sorted(
            (row for row in rows if row.source in ("open-meteo", "openweathermap")),
            key=lambda row: row.source != "open-meteo",
        )
    if not rows:
        return "missing"
    fresh = [row for row in rows if now - row.fetched_at <= MAX_CELL_AGE]
    if not fresh:
        return "stale"
    for row in fresh:
        if row.forecast_days < required_days or not isinstance(row.data, dict):
            continue
        if ensemble:
            if row.data.get("_norain_request_version") != ENSEMBLE_REQUEST_VERSION:
                continue
            try:
                if all(
                    extract_uncertainty(row.data, eta, None, row.fetched_at, ENSEMBLE_MODELS.split(",")) is not None
                    for eta in etas
                ):
                    return "usable"
            except ValueError, TypeError, KeyError, IndexError:
                continue
        else:
            try:
                if all(cell_covers(row.data, eta, row.source) for eta in etas):
                    return "usable"
            except ValueError, TypeError, KeyError, IndexError, OverflowError:
                return "insufficient"
            # The cache reader prefers eligible Open-Meteo records even when their
            # payload is incomplete; do not suggest OWM will be chosen in that case.
            return "insufficient"
    return "insufficient"


@router.get("/coverage/{kind}/{item_id}", response=SystemCoverage)
def coverage(request, kind: Literal["route", "stage"], item_id: UUID):
    now = timezone.now()
    if kind == "route":
        route = get_object_or_404(RecurringRoute, pk=item_id)
        departure = next_departure(route.schedule_cron, after=now)
        name, profile = route.name, route.profile
    else:
        route = get_object_or_404(JourneyStage.objects.select_related("day__journey"), pk=item_id)
        journey = route.day.journey
        departure = datetime.combine(route.day.date, journey.earliest_start, tzinfo=LOCAL_TZ)
        name, profile = journey.name, journey.profile
    samples = route.sample_points or []
    unavailable = None
    if not route.polyline or not samples:
        unavailable = "Keine gespeicherte Routengeometrie oder Messpunkte."
    elif departure is None:
        unavailable = "Kein gültiger Abfahrtstermin."
    else:
        departure = instant(departure)
        end = departure + timedelta(seconds=max(p["elapsed_s"] for p in samples))
        today = now.astimezone(LOCAL_TZ).date()
        if departure < now:
            unavailable = "Diese Abfahrt liegt in der Vergangenheit."
        elif end.astimezone(LOCAL_TZ).date() > today + timedelta(days=15):
            unavailable = "Diese Fahrt liegt ausserhalb des Vorhersagezeitraums."
    points = []
    if unavailable is None:
        day = departure.astimezone(LOCAL_TZ).date()
        days = forecast_days_for(departure.astimezone(LOCAL_TZ), samples, now.astimezone(LOCAL_TZ).date())
        etas = {}
        for p in samples:
            etas.setdefault((p["lat_r"], p["lon_r"]), []).append(departure + timedelta(seconds=p["elapsed_s"]))
        from itertools import batched

        for batch in batched(list(etas), 500, strict=False):
            locations = Q(*(Q(lat_r=lat, lon_r=lon) for lat, lon in batch), _connector=Q.OR)
            grouped = []
            for model in (ForecastCell, EnsembleCell):
                cells = {}
                for cell in model.objects.filter(locations, day_key=day):
                    cells.setdefault((cell.lat_r, cell.lon_r), []).append(cell)
                grouped.append(cells)
            for lat, lon in batch:
                key = (lat, lon)
                points.append(
                    {
                        "lat": lat,
                        "lon": lon,
                        "forecast": coverage_state(grouped[0].get(key, []), days, etas[key], now),
                        "ensemble": coverage_state(grouped[1].get(key, []), days, etas[key], now, ensemble=True),
                    }
                )
    return {
        "id": str(route.pk),
        "kind": kind,
        "name": name,
        "profile": profile,
        "distance_m": route.total_distance_m,
        "duration_seconds": route.total_seconds,
        "geometry_fetched_at": route.geometry_fetched_at,
        "departure": departure,
        "unavailable": unavailable,
        "points": points,
    }


class SystemJob(CamelSchema):
    id: str
    kind: str
    status: str
    cells_total: int
    cells_settled: int
    cells_failed: int
    created_at: datetime
    updated_at: datetime
    error: str
    possibly_stalled: bool


class SystemJobsPage(CamelSchema):
    items: list[SystemJob]
    total: int
    next_offset: int | None
    # A stalled job writes nothing, so no change notice ever says so: the page re-derives
    # possibly_stalled from updated_at on its own clock.
    stall_timeout_seconds: int


@router.get("/jobs", response=SystemJobsPage)
def jobs(request, offset: int = 0, limit: int = 25):
    if offset < 0 or not 1 <= limit <= 100:
        raise HttpError(422, "Invalid page.")
    now = timezone.now()
    rows = ForecastJob.objects.filter(
        ~Q(status__in=ForecastJob.TERMINAL_STATUSES) | Q(updated_at__gte=now - timedelta(hours=24))
    )
    total = rows.count()
    fields = (
        "id",
        "kind",
        "status",
        "cells_total",
        "cells_settled",
        "cells_failed",
        "created_at",
        "updated_at",
        "error",
    )
    items = []
    for row in rows.order_by("-updated_at", "pk").values(*fields)[offset : offset + limit]:
        row["id"] = str(row["id"])
        row["error"] = row["error"][:300]
        row["possibly_stalled"] = (
            row["status"] not in ForecastJob.TERMINAL_STATUSES and now - row["updated_at"] > JOB_STALL_TIMEOUT
        )
        items.append(row)
    return {
        "items": items,
        "total": total,
        "next_offset": offset + len(items) if offset + len(items) < total else None,
        "stall_timeout_seconds": int(JOB_STALL_TIMEOUT.total_seconds()),
    }
