"""Stripe Checkout, the customer portal, and the subscription webhook.

These are plain Django views mounted directly in backend/urls.py, *not* on the Ninja
router. `core.api` builds `NinjaAPI(auth=session_auth)`, which applies session
authentication and a hard CSRF check to every route it owns; Stripe's webhook POST carries
neither a session cookie nor a CSRF token and would be rejected with 403 before reaching
any handler.

Subscription state is derived only from verified webhook events. The Checkout success
redirect is not evidence of anything — the browser may never load it, and a user can visit
it directly.
"""

import json
from datetime import UTC, datetime

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_GET, require_POST
from loguru import logger

from ..entitlements import entitlements_for_sync, subscription_for_sync
from ..models import Plan, ProcessedStripeEvent, RecurringRoute, Subscription

# Events that can change what an account is entitled to. Anything else is acknowledged
# with 200 (so Stripe stops retrying) and ignored.
HANDLED_EVENTS = {
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.payment_failed",
}


def _client() -> stripe.StripeClient | None:
    return stripe.StripeClient(settings.STRIPE_SECRET_KEY) if settings.STRIPE_SECRET_KEY else None


def _user(request: HttpRequest):
    """The signed-in user, or None."""
    return request.user if request.user.is_authenticated else None


def _not_configured() -> JsonResponse:
    return JsonResponse({"detail": "Billing is not configured on this server."}, status=503)


def _entitlements_payload(user) -> dict:
    limits = entitlements_for_sync(user)
    subscription = Subscription.objects.filter(user=user).first() if user.is_authenticated else None
    return {
        "plan": limits.plan,
        "maxRoutes": limits.max_routes,
        "ensembleUncertainty": limits.ensemble_uncertainty,
        "routeCount": RecurringRoute.objects.filter(owner=user, active=True).count() if user.is_authenticated else 0,
        "status": subscription.status if subscription else "",
        "currentPeriodEnd": subscription.current_period_end.isoformat()
        if subscription and subscription.current_period_end
        else None,
        "cancelAtPeriodEnd": bool(subscription and subscription.cancel_at_period_end),
        "billingConfigured": bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_PRICE_ID_PRO),
    }


@require_GET
def entitlements_view(request: HttpRequest):
    """What the current account may do — drives the upgrade prompts in the UI."""
    return JsonResponse(_entitlements_payload(request.user))


@require_POST
@csrf_protect
def checkout_view(request: HttpRequest):
    """Start a Stripe Checkout session for the Pro subscription."""
    user = _user(request)
    if user is None:
        return JsonResponse({"detail": "Authentication required."}, status=401)
    client = _client()
    if client is None or not settings.STRIPE_PRICE_ID_PRO:
        return _not_configured()

    subscription = subscription_for_sync(user)
    if not subscription.stripe_customer_id:
        customer = client.customers.create(params={"email": user.email, "metadata": {"user_id": str(user.pk)}})
        subscription.stripe_customer_id = customer.id
        subscription.save(update_fields=["stripe_customer_id", "updated_at"])

    session = client.checkout.sessions.create(
        params={
            "mode": "subscription",
            "customer": subscription.stripe_customer_id,
            "line_items": [{"price": settings.STRIPE_PRICE_ID_PRO, "quantity": 1}],
            "success_url": f"{settings.FRONTEND_URL.rstrip('/')}/account?checkout=success",
            "cancel_url": f"{settings.FRONTEND_URL.rstrip('/')}/account?checkout=cancelled",
            # Lets the webhook find the user even if the customer id ever changes.
            "client_reference_id": str(user.pk),
            "subscription_data": {"metadata": {"user_id": str(user.pk)}},
        }
    )
    return JsonResponse({"url": session.url})


@require_POST
@csrf_protect
def portal_view(request: HttpRequest):
    """Open the Stripe billing portal so the user can cancel or change payment details."""
    user = _user(request)
    if user is None:
        return JsonResponse({"detail": "Authentication required."}, status=401)
    client = _client()
    if client is None:
        return _not_configured()

    subscription = subscription_for_sync(user)
    if not subscription.stripe_customer_id:
        return JsonResponse({"detail": "No billing account yet."}, status=400)

    session = client.billing_portal.sessions.create(
        params={
            "customer": subscription.stripe_customer_id,
            "return_url": f"{settings.FRONTEND_URL.rstrip('/')}/account",
        }
    )
    return JsonResponse({"url": session.url})


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


def _subscription_for_event(obj: dict) -> Subscription | None:
    """Find the local row for a Stripe object, by user metadata or customer id."""
    user_id = (obj.get("metadata") or {}).get("user_id") or obj.get("client_reference_id")
    if user_id:
        user = get_user_model().objects.filter(pk=user_id).first()
        if user is not None:
            return subscription_for_sync(user)

    customer_id = obj.get("customer")
    if customer_id:
        return Subscription.objects.filter(stripe_customer_id=customer_id).first()
    return None


def _period_end(obj: dict) -> datetime | None:
    value = obj.get("current_period_end")
    return datetime.fromtimestamp(value, tz=UTC) if value else None


def _apply_subscription(subscription: Subscription, obj: dict) -> None:
    """Mirror a Stripe subscription object onto the local row."""
    status = obj.get("status") or ""
    subscription.stripe_subscription_id = obj.get("id") or subscription.stripe_subscription_id
    subscription.status = status
    subscription.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
    subscription.current_period_end = _period_end(obj) or subscription.current_period_end
    # Only a subscription Stripe still considers live grants Pro.
    subscription.plan = Plan.PRO if status in Subscription.ACTIVE_STATUSES else Plan.FREE
    if customer := obj.get("customer"):
        subscription.stripe_customer_id = customer
    subscription.save()


def _handle_event(event_type: str, obj: dict) -> None:
    subscription = _subscription_for_event(obj)
    if subscription is None:
        logger.warning(f"Stripe {event_type}: no local subscription matched {obj.get('customer')!r}")
        return

    if event_type == "checkout.session.completed":
        # The session itself carries no status; record the ids and let the
        # customer.subscription.* events that follow set the tier.
        if customer := obj.get("customer"):
            subscription.stripe_customer_id = customer
        if sub_id := obj.get("subscription"):
            subscription.stripe_subscription_id = sub_id
        subscription.save()
    elif event_type == "customer.subscription.deleted":
        subscription.plan = Plan.FREE
        subscription.status = obj.get("status") or "canceled"
        subscription.cancel_at_period_end = False
        subscription.save()
    elif event_type == "invoice.payment_failed":
        subscription.status = "past_due"
        subscription.plan = Plan.FREE
        subscription.save()
    else:  # customer.subscription.created / updated
        _apply_subscription(subscription, obj)


@csrf_exempt
@require_POST
def webhook_view(request: HttpRequest):
    """Apply a Stripe subscription event, exactly once.

    CSRF-exempt by necessity — the caller is Stripe, not a browser — so the signature
    check is what authenticates the request and must never be skipped.
    """
    if not settings.STRIPE_WEBHOOK_SECRET:
        return _not_configured()

    try:
        event = stripe.Webhook.construct_event(
            payload=request.body,
            sig_header=request.headers.get("Stripe-Signature", ""),
            secret=settings.STRIPE_WEBHOOK_SECRET,
        )
    except (ValueError, stripe.SignatureVerificationError):
        # Never log the body: an unverified payload is attacker-controlled.
        logger.warning("Stripe webhook rejected: invalid payload or signature")
        return HttpResponse(status=400)

    event_id = event["id"]
    event_type = event["type"]
    # Claim the id first. Stripe retries on any non-2xx, and a duplicate delivery must not
    # re-apply the change.
    created = ProcessedStripeEvent.objects.get_or_create(event_id=event_id, defaults={"event_type": event_type})[1]
    if not created:
        logger.debug(f"Stripe webhook {event_id} already processed")
        return JsonResponse({"received": True, "duplicate": True})

    if event_type in HANDLED_EVENTS:
        obj = event["data"]["object"]
        _handle_event(event_type, obj if isinstance(obj, dict) else json.loads(str(obj)))

    return JsonResponse({"received": True})
