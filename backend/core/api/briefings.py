"""Notification preferences and browser push subscriptions (session + CSRF)."""

import base64
import json
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from core import telemetry
from core.entitlements import allowed_route_ids, briefing_route_ids, entitlements_for_sync
from core.models import PushSubscription, RecurringRoute, RideBriefing, User


def push_configured():
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY and settings.VAPID_SUBJECT)


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
        return JsonResponse({"detail": "Authentication required."}, status=401)
    if request.method == "GET":
        return JsonResponse(_payload(user))
    try:
        data = json.loads(request.body)
        route_id = UUID(data["routeId"])
        channel = data["channel"]
        if channel not in {"", "email", "push"}:
            raise ValueError
    except ValueError, TypeError, KeyError:
        return JsonResponse({"detail": "Choose email, push or off."}, status=400)
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        route = RecurringRoute.objects.filter(owner=user, id=route_id, return_of__isnull=True).first()
        if route is None:
            return JsonResponse({"detail": "Route not found."}, status=404)
        if channel:
            limits = entitlements_for_sync(user)
            if not limits.max_briefing_routes or route.id not in allowed_route_ids(user):
                return JsonResponse({"detail": "Ride briefings require an active Plus route."}, status=402)
            count = (
                RecurringRoute.objects.filter(owner=user, active=True, return_of__isnull=True)
                .exclude(briefing_channel="")
                .exclude(id=route.id)
                .count()
            )
            if count >= limits.max_briefing_routes:
                return JsonResponse({"detail": "Plus includes briefings for five routes."}, status=402)
            if channel == "email" and not settings.BRIEFING_EMAIL_ENABLED:
                return JsonResponse({"detail": "Email briefings are not configured."}, status=503)
            if channel == "push" and (not push_configured() or not PushSubscription.objects.filter(user=user).exists()):
                return JsonResponse({"detail": "Enable browser notifications first."}, status=400)
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
        or parsed.hostname not in settings.PUSH_ENDPOINT_HOSTS
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
        return JsonResponse({"detail": "Authentication required."}, status=401)
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
        return JsonResponse({"detail": "Invalid browser push subscription."}, status=400)
    if not entitlements_for_sync(user).is_pro:
        return JsonResponse({"detail": "Push briefings require Plus."}, status=402)
    if not push_configured():
        return JsonResponse({"detail": "Push is not configured."}, status=503)
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        existing = PushSubscription.objects.filter(endpoint=endpoint).first()
        if existing and existing.user_id != user.pk:
            return JsonResponse(
                {"detail": "This browser is connected to another account. Disable notifications there first."},
                status=409,
            )
        if not existing and PushSubscription.objects.filter(user=user).count() >= 5:
            return JsonResponse({"detail": "Maximum five push devices per account."}, status=400)
        PushSubscription.objects.update_or_create(endpoint=endpoint, defaults={"user": user, "keys": keys})
    return JsonResponse({"ok": True})
