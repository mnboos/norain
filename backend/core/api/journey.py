"""Journeys: one-off rides over one or more days, planned by NoRain.

The endpoints never route or fetch: create and update enqueue ``plan_journey``, and a stage's
weather is an ordinary forecast job (``JOURNEY_STAGE``). Reading a journey starts or joins
those jobs, like opening a saved route does, and ranks each day's alternatives from whatever
has finished. The ranking is computed on read (``core.journeys.rank_day``), never stored.
"""

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db.models import Count, F
from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError
from pydantic import Field, field_validator, model_validator

from ..departures import local_iso
from ..entitlements import entitlements_for
from ..forecast_schemas import ForecastJobOut
from ..geo import simplify_line, vertex_distances
from ..journeys import FILL_CORRIDOR_M, LODGING_CORRIDOR_M, LODGING_WINDOW, lodging_candidates, rank_day
from ..models import ForecastJob, Journey, JourneyDay, JourneyStage, route_point
from ..pois import LODGING_KINDS, POI_CATEGORIES, pois_along_sync
from ..schedule import LOCAL_TZ, forecast_available_at
from ..schemas import CamelSchema
from ..tasks import plan_journey, start_forecast_job
from .recurring_route import RoadPrefsIn, _current_user, check_via_points
from .route_weather import check_routing_profile, job_out

router = Router(tags=["Journeys"])

# The line each stage carries in the journey payload: enough for an overview map. The full
# line comes with the stage's forecast.
OVERVIEW_TOLERANCE_M = 50.0


def check_categories(values: list[str]) -> list[str]:
    unknown = set(values) - set(POI_CATEGORIES)
    if unknown:
        raise ValueError(f"unknown categories: {', '.join(sorted(unknown))}")
    return list(dict.fromkeys(values))


def check_lodging_kinds(values: list[str]) -> list[str]:
    unknown = set(values) - set(LODGING_KINDS)
    if unknown:
        raise ValueError(f"unknown lodging kinds: {', '.join(sorted(unknown))}")
    return list(dict.fromkeys(values))


class WeatherPrefsIn(CamelSchema):
    avoid_rain: bool = True
    avoid_headwind: bool = True
    # How far after the earliest start the departure may move, for the comparison (Plus).
    departure_window_minutes: int = Field(default=60, ge=0, le=120, multiple_of=15)


class JourneyIn(CamelSchema):
    name: str = Field(min_length=1, max_length=200)
    start_lat: float = Field(ge=-90, le=90)
    start_lon: float = Field(ge=-180, le=180)
    start_name: str
    dest_lat: float = Field(ge=-90, le=90)
    dest_lon: float = Field(ge=-180, le=180)
    dest_name: str
    via_points: list[list[float]] = Field(default_factory=list)
    profile: str = "bike"
    start_date: date
    earliest_start: time = time(8, 0)
    latest_arrival: time = time(18, 0)
    max_day_seconds: int | None = Field(default=None, ge=1800, le=16 * 3600)
    max_day_distance_m: int | None = Field(default=None, ge=5_000, le=400_000)
    max_leg_seconds: int | None = Field(default=None, ge=600, le=12 * 3600)
    max_leg_distance_m: int | None = Field(default=None, ge=2_000, le=200_000)
    poi_categories: list[str] = Field(default_factory=list)
    lodging_kinds: list[str] = Field(default_factory=list)
    road_prefs: RoadPrefsIn = Field(default_factory=RoadPrefsIn)
    weather_prefs: WeatherPrefsIn = Field(default_factory=WeatherPrefsIn)

    _profile = field_validator("profile")(check_routing_profile)
    _via_points = field_validator("via_points")(check_via_points)
    _categories = field_validator("poi_categories")(check_categories)
    _lodging = field_validator("lodging_kinds")(check_lodging_kinds)

    @model_validator(mode="after")
    def check_limits(self):
        if not (self.max_day_seconds or self.max_day_distance_m):
            raise ValueError("a day needs a time or distance limit")
        if self.latest_arrival <= self.earliest_start:
            raise ValueError("the latest arrival must be after the earliest start")
        return self


class PoiOut(CamelSchema):
    osm_ref: str
    category: str
    name: str = ""
    lon: float
    lat: float
    along_m: float
    offset_m: float


class BreakOut(CamelSchema):
    along_m: float
    elapsed_s: int
    lon: float
    lat: float
    pois: list[PoiOut] = Field(default_factory=list)


class JourneyStageOut(CamelSchema):
    id: UUID
    rank: int
    distance_m: float
    total_seconds: int
    path: list[list[float]] = Field(description="The line, simplified for an overview map")
    via_points: list[list[float]] = Field(default_factory=list)
    breaks: list[BreakOut] = Field(default_factory=list)
    gaps: dict[str, float] = Field(default_factory=dict, description="Longest stretch without each wanted category, m")
    detours: list[PoiOut] = Field(default_factory=list)
    detour_m: float = 0
    # The forecast, started or joined when the journey is read; None outside the forecast window.
    forecast_job_id: UUID | None = None
    forecast_status: str | None = None
    departure_time: str | None = Field(default=None, description="Planned departure the forecast is for")
    ride_score: float | None = Field(default=None, ge=0, le=1)
    ride_label: str | None = None
    recommended_departure: str | None = None
    recommended: bool = False
    reasons: list[str] = Field(default_factory=list)


class JourneyDayOut(CamelSchema):
    id: UUID
    index: int
    date: date
    start: list[float]
    end: list[float]
    lodging: PoiOut | None = None
    lodging_missing: bool = False
    weather_routed: bool = False
    forecast_available: bool = False
    stages: list[JourneyStageOut] = Field(default_factory=list)


class JourneyOut(CamelSchema):
    id: UUID
    name: str
    start_lat: float
    start_lon: float
    start_name: str
    dest_lat: float
    dest_lon: float
    dest_name: str
    via_points: list[list[float]]
    profile: str
    start_date: date
    earliest_start: time
    latest_arrival: time
    max_day_seconds: int | None
    max_day_distance_m: int | None
    max_leg_seconds: int | None
    max_leg_distance_m: int | None
    poi_categories: list[str]
    lodging_kinds: list[str]
    road_prefs: RoadPrefsIn
    weather_prefs: WeatherPrefsIn
    plan_status: str
    plan_error: str = ""
    planned_at: datetime | None = None
    day_count: int = 0
    days: list[JourneyDayOut] = Field(default_factory=list)


def _journey_out(journey: Journey, days: list[JourneyDayOut] | None = None, day_count: int | None = None) -> JourneyOut:
    return JourneyOut(
        id=journey.id,
        name=journey.name,
        start_lat=journey.start_point.y,
        start_lon=journey.start_point.x,
        start_name=journey.start_name,
        dest_lat=journey.destination_point.y,
        dest_lon=journey.destination_point.x,
        dest_name=journey.dest_name,
        via_points=journey.via_points or [],
        profile=journey.profile,
        start_date=journey.start_date,
        earliest_start=journey.earliest_start,
        latest_arrival=journey.latest_arrival,
        max_day_seconds=journey.max_day_seconds,
        max_day_distance_m=journey.max_day_distance_m,
        max_leg_seconds=journey.max_leg_seconds,
        max_leg_distance_m=journey.max_leg_distance_m,
        poi_categories=journey.poi_categories or [],
        lodging_kinds=journey.lodging_kinds or [],
        road_prefs=RoadPrefsIn(**(journey.road_prefs or {})),
        weather_prefs=WeatherPrefsIn(**(journey.weather_prefs or {})),
        plan_status=journey.plan_status,
        plan_error=journey.plan_error,
        planned_at=journey.planned_at,
        day_count=day_count if day_count is not None else len(days or []),
        days=days or [],
    )


def _values(data: JourneyIn) -> dict:
    values = data.model_dump(exclude={"start_lat", "start_lon", "dest_lat", "dest_lon", "road_prefs", "weather_prefs"})
    values["start_point"] = route_point(data.start_lat, data.start_lon)
    values["destination_point"] = route_point(data.dest_lat, data.dest_lon)
    values["road_prefs"] = data.road_prefs.prefs().as_json()
    values["weather_prefs"] = data.weather_prefs.model_dump()
    return values


async def _owned_journey(request: HttpRequest, journey_id: UUID) -> Journey:
    user = await _current_user(request)
    try:
        return await Journey.objects.aget(id=journey_id, owner=user)
    except Journey.DoesNotExist:
        raise HttpError(404, "Journey not found.") from None


async def _replan(journey: Journey) -> Journey:
    """Start a new plan. Any running one sees the new revision and stops writing."""
    await Journey.objects.filter(id=journey.id).aupdate(
        plan_revision=F("plan_revision") + 1,
        plan_status=Journey.PlanStatus.PENDING,
        plan_error="",
        plan_state=None,
        plan_attempts=0,
    )
    await journey.arefresh_from_db()
    await plan_journey.aenqueue(str(journey.id), journey.plan_revision)
    return journey


@router.get("/journeys", response=list[JourneyOut])
async def list_journeys(request: HttpRequest):
    user = await _current_user(request)
    query = Journey.objects.filter(owner=user).annotate(day_total=Count("days")).defer("plan_state")
    return [_journey_out(journey, day_count=journey.day_total) async for journey in query]


@router.post("/journeys", response={201: JourneyOut})
async def create_journey(request: HttpRequest, data: JourneyIn):
    """Create a journey and start planning it. The plan arrives on ``GET /journeys/{id}``."""
    user = await _current_user(request)
    limits = await entitlements_for(user)
    if await Journey.objects.filter(owner=user).acount() >= limits.max_journeys:
        raise HttpError(
            402, f"Der {limits.plan}-Tarif erlaubt {limits.max_journeys} Reisen. Lösche eine oder wechsle zu Plus."
        )
    journey = await Journey.objects.acreate(owner=user, **_values(data))
    await plan_journey.aenqueue(str(journey.id), journey.plan_revision)
    return 201, _journey_out(journey)


@router.put("/journeys/{journey_id}", response=JourneyOut)
async def update_journey(request: HttpRequest, journey_id: UUID, data: JourneyIn):
    """Change a journey. Every change re-plans it: the days depend on all of the inputs."""
    journey = await _owned_journey(request, journey_id)
    for field, value in _values(data).items():
        setattr(journey, field, value)
    await journey.asave()
    return _journey_out(await _replan(journey))


@router.post("/journeys/{journey_id}/plan", response={202: JourneyOut})
async def replan_journey(request: HttpRequest, journey_id: UUID):
    """Plan again with the same inputs: newer weather, POIs or graph."""
    journey = await _owned_journey(request, journey_id)
    return 202, _journey_out(await _replan(journey))


@router.delete("/journeys/{journey_id}", response={204: None})
async def delete_journey(request: HttpRequest, journey_id: UUID):
    journey = await _owned_journey(request, journey_id)
    await journey.adelete()
    return 204, None


def _stage_params(journey: Journey, day: JourneyDay, stage: JourneyStage, limits) -> dict:
    """The stage's forecast job params. The key hashes them, so this is the one place."""
    departure = datetime.combine(day.date, journey.earliest_start, tzinfo=LOCAL_TZ)
    params = {"journey_stage_id": str(stage.id), "departure_time": local_iso(departure)}
    window = (journey.weather_prefs or {}).get("departure_window_minutes", 0)
    if window and limits.departure_comparison:
        params |= {"departure_flex_before_minutes": 0, "departure_flex_after_minutes": window}
    return params


def _in_forecast_window(journey: Journey, day: JourneyDay, stage: JourneyStage) -> bool:
    departure = datetime.combine(day.date, journey.earliest_start, tzinfo=LOCAL_TZ)
    arrival = departure + timedelta(seconds=stage.total_seconds)
    return departure >= datetime.now(tz=UTC) - timedelta(hours=1) and forecast_available_at(arrival)


def _poi_out(value: dict | None) -> PoiOut | None:
    return PoiOut(**value) if value else None


@router.get("/journeys/{journey_id}", response=JourneyOut)
async def get_journey(request: HttpRequest, journey_id: UUID):
    """The journey with its plan: days, alternatives and, where the forecast reaches, their
    weather and ranking. Opening it starts (or joins) each stage's forecast."""
    journey = await _owned_journey(request, journey_id)
    user = await _current_user(request)
    limits = await entitlements_for(user)

    days_out = []
    async for day in journey.days.prefetch_related("stages"):
        stages = list(day.stages.all())
        available = any(_in_forecast_window(journey, day, stage) for stage in stages)
        rows, jobs = [], {}
        for stage in stages:
            if available and _in_forecast_window(journey, day, stage):
                job = await start_forecast_job(
                    ForecastJob.Kind.JOURNEY_STAGE, user, _stage_params(journey, day, stage, limits)
                )
                jobs[stage.id] = job
            rows.append(
                {
                    "id": stage.id,
                    "total_seconds": stage.total_seconds,
                    "gaps": stage.gaps,
                    "detours": stage.detours,
                    "result": jobs[stage.id].result
                    if stage.id in jobs and jobs[stage.id].status == ForecastJob.Status.DONE
                    else None,
                }
            )
        # A leg limit in time becomes metres per stage when it is planned; the day's alternatives
        # are judged against the shortest of them.
        day_leg = min((s.leg_m for s in stages if s.leg_m), default=0.0)
        ranking = {row["id"]: row for row in rank_day(rows, day_leg)}
        stages_out = []
        for stage in stages:
            ranked = ranking.get(stage.id, {})
            job = jobs.get(stage.id)
            line = stage.polyline_coordinates
            keep = simplify_line(line, set(), OVERVIEW_TOLERANCE_M)
            stages_out.append(
                JourneyStageOut(
                    id=stage.id,
                    rank=stage.rank,
                    distance_m=stage.total_distance_m,
                    total_seconds=stage.total_seconds,
                    path=[line[i] for i in keep],
                    via_points=stage.via_points,
                    breaks=[BreakOut(**b) for b in stage.breaks],
                    gaps=stage.gaps,
                    detours=[PoiOut(**d) for d in stage.detours],
                    detour_m=stage.detour_m,
                    forecast_job_id=job.id if job else None,
                    forecast_status=job.status if job else None,
                    departure_time=job.params.get("departure_time") if job else None,
                    ride_score=ranked.get("ride_score"),
                    ride_label=ranked.get("ride_label"),
                    recommended_departure=ranked.get("departure"),
                    recommended=bool(ranked.get("recommended")) and len(stages) > 1,
                    reasons=ranked.get("reasons", []),
                )
            )
        days_out.append(
            JourneyDayOut(
                id=day.id,
                index=day.index,
                date=day.date,
                start=day.start,
                end=day.end,
                lodging=_poi_out(day.lodging),
                lodging_missing=day.lodging_missing,
                weather_routed=day.weather_routed,
                forecast_available=available,
                stages=stages_out,
            )
        )
    return _journey_out(journey, days_out)


async def _owned_stage(request: HttpRequest, journey_id: UUID, stage_id: UUID) -> tuple[Journey, JourneyStage]:
    journey = await _owned_journey(request, journey_id)
    stage = await JourneyStage.objects.select_related("day").filter(id=stage_id, day__journey=journey).afirst()
    if stage is None:
        raise HttpError(404, "Stage not found.")
    return journey, stage


@router.get("/journeys/{journey_id}/stages/{stage_id}/forecast", response={200: ForecastJobOut, 202: ForecastJobOut})
async def journey_stage_forecast(request: HttpRequest, journey_id: UUID, stage_id: UUID):
    """The full forecast of one stage, as a saved route's: 200 when fresh, else 202 and a job."""
    journey, stage = await _owned_stage(request, journey_id, stage_id)
    if not _in_forecast_window(journey, stage.day, stage):
        raise HttpError(409, "Diese Etappe liegt ausserhalb des Vorhersagezeitraums.")
    user = await _current_user(request)
    limits = await entitlements_for(user)
    job = await start_forecast_job(
        ForecastJob.Kind.JOURNEY_STAGE, user, _stage_params(journey, stage.day, stage, limits)
    )
    return (200 if job.status == ForecastJob.Status.DONE else 202), job_out(job)


@router.get("/journeys/{journey_id}/stages/{stage_id}/pois", response=list[PoiOut])
async def journey_stage_pois(
    request: HttpRequest, journey_id: UUID, stage_id: UUID, categories: str = "", lodging: bool = False
):
    """POIs in the area of one stage, for the map: within ``FILL_CORRIDOR_M``, where the gap fill
    looks for a detour, so a village a kilometre off the line shows too (``offset_m`` says how
    far). ``categories`` is a comma-separated filter; empty means every category, unless only
    ``lodging`` is asked for.

    ``lodging`` adds the places the day could have ended at: lodging of the journey's kinds
    within ``LODGING_CORRIDOR_M`` in the day's last ``LODGING_WINDOW``, where the planner looked.
    None on the last day, which ends at the destination."""
    journey, stage = await _owned_stage(request, journey_id, stage_id)
    wanted = [c for c in categories.split(",") if c] or ([] if lodging else list(POI_CATEGORIES))
    try:
        check_categories(wanted)
    except ValueError as exc:
        raise HttpError(422, str(exc)) from None
    line = stage.polyline_coordinates
    hits = await sync_to_async(pois_along_sync)(line, wanted, FILL_CORRIDOR_M) if wanted else []
    if lodging and (stage.day.lodging or stage.day.lodging_missing):
        total = vertex_distances(line)[-1] if len(line) > 1 else 0.0
        near_end = lodging_candidates(
            await sync_to_async(pois_along_sync)(line, ["lodging"], LODGING_CORRIDOR_M),
            journey.lodging_kinds or [],
            total * (1 - LODGING_WINDOW),
        )
        known = {hit.osm_ref for hit in hits}
        hits = sorted([*hits, *(hit for hit in near_end if hit.osm_ref not in known)], key=lambda hit: hit.along_m)
    return [PoiOut(**hit.as_json()) for hit in hits]
