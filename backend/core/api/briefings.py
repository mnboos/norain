"""Notification preferences and browser push subscriptions (session + CSRF)."""

import base64
import json
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.http import JsonResponse
from django.utils.translation import gettext
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from core import telemetry
from core.entitlements import allowed_route_ids, briefing_route_ids, entitlements_for_sync
from core.models import PushSubscription, RecurringRoute, RideBriefing, User
from core.push import push_configured, send_push

PUSH_TEST_COOLDOWN_SECONDS = 30


def _payload(user):
    allowed = set(allowed_route_ids(user))
    briefing_ids = set(briefing_route_ids(user))
    return {
        "pushPublicKey": settings.VAPID_PUBLIC_KEY if push_configured() else "",
        "emailConfigured": settings.BRIEFING_EMAIL_ENABLED,
        "pushDeviceCount": PushSubscription.objects.filter(user=user).count(),
        "routes": [
            {
                "id": str(r.id),
                "name": r.name,
                "active": r.active,
                "available": r.id in allowed,
                "freeSelected": r.free_selected,
                "channel": r.briefing_channel,
                "briefingActive": r.id in briefing_ids,
            }
            for r in RecurringRoute.objects.filter(owner=user, return_of__isnull=True).order_by("created_at", "id")
        ],
        "recent": [
            {
                "id": b.id,
                "routeName": b.route.name,
                "body": b.body,
                "status": b.status,
                "departure": b.departure.isoformat(),
            }
            for b in RideBriefing.objects.filter(route__owner=user)
            .exclude(body="")
            .select_related("route")
            .order_by("-departure")[:10]
        ],
    }


@require_http_methods(["GET", "POST"])
@csrf_protect
def preferences_view(request):
    user = request.user
    if not isinstance(user, User):
        return JsonResponse({"detail": gettext("Anmeldung erforderlich.")}, status=401)
    if request.method == "GET":
        return JsonResponse(_payload(user))
    try:
        data = json.loads(request.body)
        route_id = UUID(data["routeId"])
        channel = data["channel"]
        if channel not in {"", "email", "push"}:
            raise ValueError
    except ValueError, TypeError, KeyError:
        return JsonResponse({"detail": gettext("Wähle E-Mail, Push oder Aus.")}, status=400)
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        route = RecurringRoute.objects.filter(owner=user, id=route_id, return_of__isnull=True).first()
        if route is None:
            return JsonResponse({"detail": gettext("Route nicht gefunden.")}, status=404)
        if channel:
            limits = entitlements_for_sync(user)
            if not limits.max_briefing_routes or route.id not in allowed_route_ids(user):
                return JsonResponse({"detail": gettext("Briefings brauchen eine aktive Plus-Route.")}, status=402)
            count = (
                RecurringRoute.objects.filter(owner=user, active=True, return_of__isnull=True)
                .exclude(briefing_channel="")
                .exclude(id=route.id)
                .count()
            )
            if count >= limits.max_briefing_routes:
                return JsonResponse({"detail": gettext("Plus umfasst Briefings für fünf Routen.")}, status=402)
            if channel == "email" and not settings.BRIEFING_EMAIL_ENABLED:
                return JsonResponse({"detail": gettext("E-Mail-Briefings sind nicht eingerichtet.")}, status=503)
            if channel == "push" and (not push_configured() or not PushSubscription.objects.filter(user=user).exists()):
                return JsonResponse({"detail": gettext("Erlaube zuerst Benachrichtigungen im Browser.")}, status=400)
        if route.briefing_channel != channel:
            RideBriefing.objects.filter(route=route, status="pending").update(status="canceled")
        route.briefing_channel = channel
        route.save(update_fields=["briefing_channel", "updated_at"])
        RecurringRoute.objects.filter(return_of=route).update(briefing_channel=channel)
        RideBriefing.objects.filter(route__return_of=route, status="pending").update(status="canceled")
    telemetry.event("briefing.preference", channel=channel or "off", outcome="saved", **telemetry.user_context(user))
    return JsonResponse(_payload(user))


def validate_subscription(data):
    endpoint = data["endpoint"]
    if not isinstance(endpoint, str) or len(endpoint) > 2048:
        raise ValueError
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.port not in {None, 443}
        or not parsed.hostname
        or (
            parsed.hostname not in settings.PUSH_ENDPOINT_HOSTS
            and not parsed.hostname.endswith(settings.PUSH_ENDPOINT_HOST_SUFFIXES)
        )
    ):
        raise ValueError
    keys = data["keys"]
    # Reject invalid encryption keys before they reach a delivery worker.
    for name, length in (("auth", 16), ("p256dh", 65)):
        value = keys[name]
        if not isinstance(value, str) or len(value) > 100:
            raise ValueError
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        if len(decoded) != length or (name == "p256dh" and decoded[0] != 4):
            raise ValueError
    return endpoint, {k: keys[k] for k in ("auth", "p256dh")}


@require_http_methods(["POST", "DELETE"])
@csrf_protect
def push_view(request):
    user = request.user
    if not isinstance(user, User):
        return JsonResponse({"detail": gettext("Anmeldung erforderlich.")}, status=401)
    try:
        data = json.loads(request.body)
        if request.method == "DELETE":
            endpoint = data["endpoint"]
            if not isinstance(endpoint, str):
                raise ValueError
            PushSubscription.objects.filter(user=user, endpoint=endpoint).delete()
            return JsonResponse({"ok": True})
        endpoint, keys = validate_subscription(data)
    except ValueError, TypeError, KeyError, AttributeError:
        return JsonResponse({"detail": gettext("Ungültige Push-Anmeldung des Browsers.")}, status=400)
    if not entitlements_for_sync(user).is_pro:
        return JsonResponse({"detail": gettext("Push-Briefings brauchen Plus.")}, status=402)
    if not push_configured():
        return JsonResponse({"detail": gettext("Push ist nicht eingerichtet.")}, status=503)
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        existing = PushSubscription.objects.filter(endpoint=endpoint).first()
        if existing and existing.user_id != user.pk:
            return JsonResponse(
                {
                    "detail": gettext(
                        "Dieser Browser ist mit einem anderen Konto verbunden. "
                        "Schalte die Benachrichtigungen dort zuerst aus."
                    )
                },
                status=409,
            )
        if not existing and PushSubscription.objects.filter(user=user).count() >= 5:
            return JsonResponse({"detail": gettext("Höchstens fünf Push-Geräte pro Konto.")}, status=400)
        PushSubscription.objects.update_or_create(endpoint=endpoint, defaults={"user": user, "keys": keys})
    return JsonResponse({"ok": True})


def _test_cooldown_free(user):
    """One test per account per cooldown. Fails open: a cache outage costs the limit, not the test."""
    try:
        return cache.add(f"push-test:{user.pk}", 1, PUSH_TEST_COOLDOWN_SECONDS)
    except Exception:  # noqa: BLE001
        return True


@require_http_methods(["POST"])
@csrf_protect
def push_test_view(request):
    """Send a test notification to this browser's registration, right away."""
    user = request.user
    if not isinstance(user, User):
        return JsonResponse({"detail": gettext("Anmeldung erforderlich.")}, status=401)
    try:
        endpoint = json.loads(request.body)["endpoint"]
        if not isinstance(endpoint, str):
            raise ValueError
    except ValueError, TypeError, KeyError:
        return JsonResponse({"detail": gettext("Ungültige Push-Anmeldung des Browsers.")}, status=400)
    if not entitlements_for_sync(user).is_pro:
        return JsonResponse({"detail": gettext("Push-Briefings brauchen Plus.")}, status=402)
    if not push_configured():
        return JsonResponse({"detail": gettext("Push ist nicht eingerichtet.")}, status=503)
    device = PushSubscription.objects.filter(user=user, endpoint=endpoint).first()
    if device is None:
        return JsonResponse({"detail": gettext("Push ist auf diesem Gerät nicht aktiviert.")}, status=404)
    if not _test_cooldown_free(user):
        return JsonResponse({"detail": gettext("Warte kurz, bevor du erneut testest.")}, status=429)
    sent = send_push(
        [device],
        gettext("MeteoLane – Testbenachrichtigung"),
        gettext("Push-Benachrichtigungen funktionieren auf diesem Gerät."),
        f"{settings.FRONTEND_URL.rstrip('/')}/account",
        "test",
    )
    telemetry.event("briefing.push_test", outcome="sent" if sent else "failed", **telemetry.user_context(user))
    if not sent:
        return JsonResponse({"detail": gettext("Der Push-Dienst hat die Nachricht abgelehnt.")}, status=502)
    return JsonResponse({"ok": True})
