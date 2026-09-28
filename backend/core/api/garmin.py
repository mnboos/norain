"""Compact read-only feed for Connect IQ, with a separate revocable credential."""

import hashlib
import secrets
from datetime import UTC, datetime
from math import isfinite

from asgiref.sync import async_to_sync
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from core.departures import route_job_params
from core.entitlements import allowed_route_ids, entitlements_for_sync
from core.jobs import carry_stale
from core.journeys import rank_day
from core.models import ForecastJob, GarminToken, Journey, JourneyDay, RecurringRoute
from core.schedule import LOCAL_TZ, forecast_available_at, next_departure
from core.tasks import start_forecast_job

from .journey import _stage_params


def response(data, status=200):
    result = JsonResponse(data, status=status)
    result["Cache-Control"] = "no-store"
    return result


@require_http_methods(["GET", "POST", "DELETE"])
@csrf_protect
def token_view(request):
    if not request.user.is_authenticated or not request.user.is_active:
        return response({"detail": "Sign in required"}, 401)
    if request.method == "DELETE":
        GarminToken.objects.filter(user=request.user).delete()
        return response({"connected": False})
    if request.method == "GET":
        return response({"connected": GarminToken.objects.filter(user=request.user).exists()})
    token = secrets.token_urlsafe(32)
    GarminToken.objects.update_or_create(
        user=request.user, defaults={"digest": hashlib.sha256(token.encode()).hexdigest()}
    )
    return response({"token": token, "connected": True})


def earliest_route(routes, now):
    """Order by actual instants, including return routes; never by name or row order."""
    candidates = []
    for route in routes:
        departure = next_departure(route.schedule_cron, after=now)
        if departure is not None and departure.astimezone(UTC) > now:
            candidates.append((departure.astimezone(UTC), str(route.pk), route))
    return min(candidates, default=None, key=lambda item: item[:2])


def weather_fields(result):
    samples = (result or {}).get("samples") or []

    def values(key):
        return [
            s[key] for s in samples if isinstance(s, dict) and isinstance(s.get(key), (int, float)) and isfinite(s[key])
        ]

    temps, pops, rain, wind = (values(k) for k in ("temp", "pop", "rain_rate_mm_h", "headwind"))
    return {
        "tempMin": round(min(temps), 1) if temps else None,
        "tempMax": round(max(temps), 1) if temps else None,
        "rainProbability": round(max(pops) * 100) if pops else None,
        "rainRate": round(max(rain), 1) if rain else None,
        "headwind": round(max(wind), 1) if wind else None,
    }


def earliest_journey(days, now):
    candidates = []
    for day in days:
        departure = datetime.combine(day.date, day.journey.earliest_start, tzinfo=LOCAL_TZ).astimezone(UTC)
        if departure > now:
            candidates.append((departure, str(day.pk), day))
    return min(candidates, default=None, key=lambda item: item[:2])


def journey_forecast(day, user, limits, now):
    stages = list(day.stages.all())
    rows, forecasts = [], {}
    for stage in stages:
        job = async_to_sync(start_forecast_job)(
            ForecastJob.Kind.JOURNEY_STAGE, user, _stage_params(day.journey, day, stage, limits)
        )
        result = carry_stale(job, limits.result_marker(), now)
        forecasts[stage.pk] = (stage, job, result)
        rows.append(
            {
                "id": stage.pk,
                "total_seconds": stage.total_seconds,
                "gaps": stage.gaps,
                "leg_seconds": stage.leg_seconds,
                "leg_m": stage.leg_m,
                "limit_overruns": stage.limit_overruns,
                "detours": stage.detours,
                "result": result,
            }
        )
    ranked = rank_day(rows)
    best = next(row for row in ranked if row["recommended"])
    return forecasts[best["id"]]


@require_http_methods(["GET"])
def next_ride_view(request):
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer ") or len(authorization) > 100:
        return response({"detail": "Watch token required"}, 401)
    digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
    credential = GarminToken.objects.select_related("user").filter(digest=digest, user__is_active=True).first()
    if credential is None:
        return response({"detail": "Invalid watch token"}, 401)
    user = credential.user
    now = datetime.now(UTC)
    routes = RecurringRoute.objects.filter(owner=user, active=True, pk__in=allowed_route_ids(user)).only(
        "id", "name", "schedule_cron", "total_seconds", "total_distance_m"
    )
    selected = earliest_route(routes, now)
    days = (
        JourneyDay.objects.filter(
            journey__owner=user,
            journey__kind=Journey.Kind.TOUR,
            journey__plan_status=Journey.PlanStatus.DONE,
            date__gte=now.astimezone(LOCAL_TZ).date(),
            stages__isnull=False,
        )
        .select_related("journey")
        .prefetch_related("stages")
        .distinct()
    )
    journey = earliest_journey(days, now)
    is_journey = journey is not None and (selected is None or journey[:2] < selected[:2])
    if is_journey:
        selected = journey
    if selected is None:
        return response({"ride": None})
    departure, _, route = selected
    name = f"{route.journey.name} - day {route.index + 1}" if is_journey else route.name
    ride_id = str(route.pk)
    local = departure.astimezone(LOCAL_TZ)
    result = None
    status = "Outside forecast window"
    limits = entitlements_for_sync(user)
    job = None
    if forecast_available_at(local):
        if is_journey:
            route, job, result = journey_forecast(route, user, limits, now)
        else:
            job = async_to_sync(start_forecast_job)(
                ForecastJob.Kind.ROUTE,
                user,
                route_job_params(route.pk, local.isoformat(), 0, 0),
            )
            # The same age and entitlement checks as the web forecast apply on the watch.
            result = carry_stale(job, limits.result_marker(), now)
        status = "Weather refreshing"
        if result:
            status = "Latest cached weather" if job.status != ForecastJob.Status.DONE else "Weather updated"
        elif job.status == ForecastJob.Status.FAILED:
            status = "Weather unavailable"
    elif is_journey:
        route = route.stages.first()
    weather = weather_fields(result)
    if job is not None and job.status == ForecastJob.Status.DONE and all(v is None for v in weather.values()):
        status = "Weather unavailable"
    return response(
        {
            "ride": {
                "id": ride_id,
                "name": name,
                "departureEpoch": int(departure.timestamp()),
                "departureLabel": local.strftime("%d.%m. %H:%M") + " " + local.tzname(),
                "distanceKm": round(route.total_distance_m / 1000, 1) if route.total_distance_m is not None else None,
                "durationMinutes": round(route.total_seconds / 60) if route.total_seconds is not None else None,
                "weatherStatus": status,
                **weather,
            }
        }
    )
