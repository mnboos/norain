"""Prepare and deliver a single briefing 60 minutes before the earliest departure."""

import json
from datetime import UTC, datetime, timedelta

from asgiref.sync import async_to_sync
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.tasks import task
from django.utils import translation
from django.utils.translation import gettext, gettext_lazy
from pywebpush import WebPushException, webpush

from core import departures, telemetry
from core.api.briefings import push_configured
from core.entitlements import briefing_route_ids
from core.models import ForecastJob, PushSubscription, RecurringRoute, RideBriefing, User
from core.schedule import LOCAL_TZ, next_departure
from core.tasks import start_forecast_job
from core.weather import mean_felt_temp

# The departure comparison's reason is a code (core.forecast_schemas.DepartureExplanation); the
# SPA words it in its catalogs, the briefing here.
EXPLANATIONS = {
    "insufficient_data": gettext_lazy("Nicht genügend Wetterdaten zum Vergleichen der Abfahrtszeiten."),
    "best_in_window": gettext_lazy("Voraussichtlich die günstigsten Bedingungen im gewählten Zeitfenster."),
    "requested_equivalent": gettext_lazy("Ähnliche Bedingungen – deine gewünschte Abfahrtszeit passt bereits."),
    "requested_best": gettext_lazy("Deine gewünschte Abfahrtszeit bietet bereits die günstigsten Bedingungen."),
    "less_rain": gettext_lazy("Weniger Regen während deiner Fahrt."),
    "less_wind": gettext_lazy("Weniger Gegenwind während deiner Fahrt."),
    "milder": gettext_lazy("Angenehmere Temperaturen während deiner Fahrt."),
    "less_frost": gettext_lazy("Geringeres Frostrisiko während deiner Fahrt."),
}

LEAD = timedelta(minutes=60)
PREPARE = timedelta(minutes=10)
DELIVERY_GRACE = timedelta(minutes=10)


def briefing_body(job, route):
    """Never turn missing or incomplete weather into an assurance of a dry ride.

    Worded in the active language; ``deliver`` activates the owner's.
    """
    if not job or job.status != ForecastJob.Status.DONE or not job.result:
        return gettext(
            "%(route)s: Wetterdaten sind derzeit nicht verfügbar. Bitte prüfe die Vorhersage vor der Abfahrt."
        ) % {"route": route.name}
    result = job.result
    samples = result.get("samples") or []
    expected = len((job.geometry or {}).get("sample_points") or [])
    if not samples or job.cells_failed or (expected and len(samples) < expected):
        return gettext("%(route)s: Die Wetterdaten sind unvollständig. Bitte prüfe die Vorhersage vor der Abfahrt.") % {
            "route": route.name
        }
    lines = [gettext("Deine Fahrt: %(route)s") % {"route": route.name}]
    comparison = result.get("departure_inputs")
    if comparison:
        view = departures.comparison_view(comparison)
        recommended = view.get("recommended_time")
        if recommended:
            time = departures.instant(recommended).astimezone(LOCAL_TZ).strftime("%H:%M")
            explanation = EXPLANATIONS.get(view.get("explanation", ""), "")
            lines.append(
                gettext("Empfohlene Abfahrt: %(time)s. %(explanation)s") % {"time": time, "explanation": explanation}
            )
    pops = [s.get("pop") for s in samples]
    rates = [s.get("rain_rate_mm_h") for s in samples]
    if all(isinstance(v, (int, float)) for v in pops):
        lines.append(
            gettext("Höchstes Regenrisiko an einem Punkt: %(percent)s %%.") % {"percent": round(max(pops) * 100)}
        )
    elif all(isinstance(v, (int, float)) for v in rates):
        lines.append(
            gettext(
                "Prognostizierte maximale Regenintensität: %(rate).1f mm/h. Regenrisiko nicht vollständig verfügbar."
            )
            % {"rate": max(rates)}
        )
    else:
        lines.append(gettext("Regenrisiko nicht vollständig verfügbar."))
    temps = [s.get("temp") for s in samples if isinstance(s.get("temp"), (int, float))]
    if temps:
        lines.append(
            gettext("Temperatur entlang der Route: %(low).0f–%(high).0f °C.") % {"low": min(temps), "high": max(temps)}
        )
    felt = mean_felt_temp(samples)
    if felt is not None:
        lines.append(gettext("Gefühlt im Schnitt: %(felt).0f °C.") % {"felt": felt})
    lines.append(gettext("Die Vorhersage kann sich ändern; dies ist keine laufende Wetterwarnung."))
    return "\n".join(lines)


def _send_push(user, body, url, tag):
    if not push_configured():
        return False
    sent = False
    for device in PushSubscription.objects.filter(user=user):
        try:
            webpush(
                subscription_info={"endpoint": device.endpoint, "keys": device.keys},
                data=json.dumps({"title": gettext("MeteoLane – Deine Fahrt"), "body": body, "url": url, "tag": tag}),
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.VAPID_SUBJECT},
                ttl=600,
                timeout=10,
            )
            sent = True
        except WebPushException as exc:
            if exc.response is not None and exc.response.status_code in {404, 410}:
                device.delete()
            telemetry.event("briefing.delivery", channel="push", outcome="failed")
    return sent


def deliver(briefing_id, now=None):
    now = now or datetime.now(tz=UTC)
    with transaction.atomic():
        owner_id = RideBriefing.objects.filter(pk=briefing_id).values_list("route__owner_id", flat=True).first()
        if owner_id is None:
            return
        User.objects.select_for_update().get(pk=owner_id)
        briefing = (
            RideBriefing.objects.select_for_update(of=("self",))
            .select_related("route__owner", "job")
            .get(pk=briefing_id)
        )
        if briefing.status != "pending" or now < briefing.due_at:
            return
        user = briefing.route.owner
        if user is None:
            return
        eligible = briefing_route_ids(user)
        route = briefing.route
        if route.id not in eligible or route.briefing_channel != briefing.channel or now >= briefing.earliest_departure:
            briefing.status = "canceled"
            briefing.save(update_fields=["status"])
            return
        day_start = now.astimezone(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        if RideBriefing.objects.filter(route__owner=user, delivery_started_at__gte=day_start).count() >= 10:
            briefing.status = "capped"
            briefing.save(update_fields=["status"])
            return
        if (
            briefing.job is None or briefing.job.status not in {ForecastJob.Status.DONE, ForecastJob.Status.FAILED}
        ) and now < briefing.due_at + DELIVERY_GRACE:
            return
        # Stored and sent as it is, so it is worded once, in the owner's language.
        with translation.override(user.language):
            briefing.body = briefing_body(briefing.job, route)
        briefing.status = "sending"
        briefing.delivery_started_at = now
        briefing.save(update_fields=["body", "status", "delivery_started_at"])
    # The committed claim prevents duplicate deliveries even with multiple schedulers.
    url = f"{settings.FRONTEND_URL.rstrip('/')}/routes/{route.id}"
    sent = False
    try:
        # Preferences or entitlements may have changed while preparing the message.
        route.refresh_from_db()
        if route.id in briefing_route_ids(user) and route.briefing_channel == briefing.channel:
            with translation.override(user.language):
                if briefing.channel == "email" and settings.BRIEFING_EMAIL_ENABLED:
                    manage = gettext("Briefings verwalten: %(url)s") % {
                        "url": f"{settings.FRONTEND_URL.rstrip('/')}/account"
                    }
                    sent = bool(
                        send_mail(
                            gettext("MeteoLane – Deine Fahrt"),
                            f"{briefing.body}\n\n{url}\n\n{manage}",
                            settings.DEFAULT_FROM_EMAIL,
                            [user.email],
                        )
                    )
                elif briefing.channel == "push":
                    sent = _send_push(user, briefing.body, url, f"ride-{briefing.id}")
    except Exception:  # noqa: BLE001 -- ambiguous external delivery must not be retried
        telemetry.event("briefing.delivery", channel=briefing.channel, outcome="error")
    RideBriefing.objects.filter(pk=briefing.pk).update(
        status="sent" if sent else "failed", sent_at=now if sent else None
    )
    telemetry.event("briefing.delivery", channel=briefing.channel, outcome="sent" if sent else "failed")


@task()
def refresh_briefings():
    now = datetime.now(tz=UTC)
    # Recover local state after a process dies during an external send, without resending.
    RideBriefing.objects.filter(status="sending", delivery_started_at__lt=now - timedelta(minutes=10)).update(
        status="unknown"
    )
    users = User.objects.filter(
        recurring_routes__active=True, recurring_routes__briefing_channel__in=["email", "push"]
    ).distinct()
    for user in users:
        for route in RecurringRoute.objects.filter(id__in=briefing_route_ids(user)):
            departure = next_departure(route.schedule_cron, after=now)
            if departure is None:
                continue
            earliest = departure - timedelta(minutes=route.departure_flex_before_minutes)
            due = earliest - LEAD
            if not due - PREPARE <= now < earliest:
                continue
            briefing, _ = RideBriefing.objects.get_or_create(
                route=route,
                departure=departure,
                defaults={
                    "earliest_departure": earliest,
                    "due_at": due,
                    "channel": route.briefing_channel,
                },
            )
            if briefing.status != "pending":
                continue
            if briefing.job_id is None:
                job = async_to_sync(start_forecast_job)(
                    ForecastJob.Kind.ROUTE,
                    user,
                    departures.route_job_params(
                        route.id,
                        departure.isoformat(),
                        route.departure_flex_before_minutes,
                        route.departure_flex_after_minutes,
                    ),
                )
                RideBriefing.objects.filter(pk=briefing.pk, status="pending").update(job=job)
            deliver(briefing.pk, now)
    # Includes pending rows whose route schedule, activation or plan changed.
    RideBriefing.objects.filter(status="pending", earliest_departure__lte=now).update(status="canceled")
    RideBriefing.objects.filter(departure__lt=now - timedelta(days=30)).delete()
