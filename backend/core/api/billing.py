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
from datetime import UTC, datetime, timedelta
from typing import Any

import stripe
from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_GET, require_POST
from loguru import logger
from stripe.params import CustomerCreateParams
from stripe.params.billing_portal import SessionCreateParams as PortalSessionParams
from stripe.params.checkout import (
    SessionCreateParams as CheckoutSessionParams,
)
from stripe.params.checkout import (
    SessionCreateParamsLineItem,
    SessionCreateParamsSubscriptionData,
)

from .. import telemetry
from ..entitlements import allowed_route_ids, entitlements_for_sync, subscription_for_sync
from ..models import Plan, ProcessedStripeEvent, RecurringRoute, Subscription, User

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


def _user(request: HttpRequest) -> User | None:
    """The signed-in user, or None."""
    user = request.user
    return user if isinstance(user, User) else None


def _not_configured() -> JsonResponse:
    return JsonResponse({"detail": "Billing is not configured on this server."}, status=503)


def _entitlements_payload(user: AbstractBaseUser | AnonymousUser) -> dict[str, Any]:
    limits = entitlements_for_sync(user)
    subscription = Subscription.objects.filter(user=user).first() if user.is_authenticated else None
    return {
        "plan": limits.plan,
        "maxRoutes": limits.max_routes,
        "ensembleUncertainty": limits.ensemble_uncertainty,
        "departureComparison": limits.departure_comparison,
        "maxBriefingRoutes": limits.max_briefing_routes,
        "trialEligible": bool(
            user.is_authenticated and not limits.is_pro and (not subscription or not subscription.trial_started_at)
        ),
        "complimentaryUntil": subscription.complimentary_until.isoformat()
        if subscription and subscription.complimentary_until
        else None,
        "trialEndsAt": subscription.trial_ends_at.isoformat() if subscription and subscription.trial_ends_at else None,
        "paidSubscription": bool(
            subscription and subscription.stripe_subscription_id and subscription.status in Subscription.ACTIVE_STATUSES
        ),
        "prices": {"annual": 29, "monthly": 3.9, "currency": "EUR"},
        "routeCount": RecurringRoute.objects.filter(owner=user, active=True, return_of__isnull=True).count()
        if user.is_authenticated
        else 0,
        "status": subscription.status if subscription else "",
        "currentPeriodEnd": subscription.current_period_end.isoformat()
        if subscription and subscription.current_period_end
        else None,
        "cancelAtPeriodEnd": bool(subscription and subscription.cancel_at_period_end),
        "billingConfigured": bool(
            settings.BILLING_ENABLED and settings.STRIPE_SECRET_KEY and _price("annual") and _price("monthly")
        ),
    }


@require_GET
def entitlements_view(request: HttpRequest) -> HttpResponse:
    """What the current account may do — drives the upgrade prompts in the UI."""
    return JsonResponse(_entitlements_payload(request.user))


@require_POST
@csrf_protect
@telemetry.action("billing", "checkout")
def checkout_view(request: HttpRequest) -> HttpResponse:
    """Start a Stripe Checkout session for the Pro subscription."""
    user = _user(request)
    if user is None:
        return JsonResponse({"detail": "Authentication required."}, status=401)
    try:
        data = json.loads(request.body or b"{}")
        interval = data.get("interval", "annual")
        if interval not in {"annual", "monthly"}:
            raise ValueError
    except ValueError, AttributeError, TypeError:
        return JsonResponse({"detail": "Choose annual or monthly billing."}, status=400)
    client = _client()
    price = _price(interval)
    if not settings.BILLING_ENABLED or client is None or not price:
        return _not_configured()

    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        subscription = subscription_for_sync(user)
        if subscription.stripe_subscription_id and subscription.status in {
            "active",
            "trialing",
            "past_due",
            "unpaid",
            "incomplete",
            "paused",
        }:
            return JsonResponse({"detail": "Manage your existing subscription in the billing portal."}, status=409)
        if subscription.checkout_session_id:
            previous = client.v1.checkout.sessions.retrieve(subscription.checkout_session_id)
            if previous.status == "complete" and not (
                subscription.stripe_subscription_id and subscription.status in {"canceled", "incomplete_expired"}
            ):
                return JsonResponse(
                    {"detail": "Payment is being processed. Please refresh your account shortly."}, status=409
                )
            if previous.status == "open" and subscription.checkout_interval == interval and previous.url:
                return JsonResponse({"url": previous.url})
            if previous.status == "open":
                client.v1.checkout.sessions.expire(subscription.checkout_session_id)
        if not subscription.stripe_customer_id:
            customer = client.v1.customers.create(
                params=CustomerCreateParams(email=user.email, metadata={"user_id": str(user.pk)}),
                options={"idempotency_key": f"norain-customer-{user.pk}"},
            )
            subscription.stripe_customer_id = customer.id
            subscription.save(update_fields=["stripe_customer_id", "updated_at"])

        previous_session = subscription.checkout_session_id or "initial"
        session = client.v1.checkout.sessions.create(
            params=CheckoutSessionParams(
                mode="subscription",
                customer=subscription.stripe_customer_id,
                line_items=[SessionCreateParamsLineItem(price=price, quantity=1)],
                success_url=f"{settings.FRONTEND_URL.rstrip('/')}/account?checkout=success",
                cancel_url=f"{settings.FRONTEND_URL.rstrip('/')}/account?checkout=cancelled",
                # Lets the webhook find the user even if the customer id ever changes.
                client_reference_id=str(user.pk),
                subscription_data=SessionCreateParamsSubscriptionData(metadata={"user_id": str(user.pk)}),
            ),
            options={"idempotency_key": f"norain-checkout-{user.pk}-{interval}-{previous_session}"},
        )
        subscription.checkout_session_id = session.id
        subscription.checkout_interval = interval
        subscription.save(update_fields=["checkout_session_id", "checkout_interval", "updated_at"])

    if not session.url:
        # Only a hosted session has a URL, and only while it is still active. There is
        # nothing to redirect the browser to, and the server is configured correctly, so
        # this is not the 503 that _not_configured reports.
        logger.error(f"Stripe checkout session {session.id} came back without a URL")
        return JsonResponse({"detail": "Stripe did not return a checkout URL."}, status=502)
    return JsonResponse({"url": session.url})


@require_POST
@csrf_protect
@telemetry.action("billing", "portal")
def portal_view(request: HttpRequest) -> HttpResponse:
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

    session = client.v1.billing_portal.sessions.create(
        params=PortalSessionParams(
            customer=subscription.stripe_customer_id,
            return_url=f"{settings.FRONTEND_URL.rstrip('/')}/account",
        )
    )
    return JsonResponse({"url": session.url})


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


def _subscription_for_event(obj: dict) -> Subscription | None:
    """Find the local row for a Stripe object, by user metadata or customer id."""
    user_id = (obj.get("metadata") or {}).get("user_id") or obj.get("client_reference_id")
    if user_id:
        user = User.objects.filter(pk=user_id).first()
        if user is not None:
            return subscription_for_sync(user)

    customer_id = obj.get("customer")
    if customer_id:
        return Subscription.objects.filter(stripe_customer_id=customer_id).first()
    return None


def _period_end(obj: dict) -> datetime | None:
    """When the current billing period ends.

    Stripe moved this off the subscription and onto its items in API version
    2025-03-31.basil, so a payload from a current API version has no top-level field at
    all. Older payloads still carry it, so read both. With several items on different
    cycles, the latest one is the date the account keeps paying for.
    """
    value = obj.get("current_period_end")
    if value is None:
        items = (obj.get("items") or {}).get("data") or []
        ends = [item.get("current_period_end") for item in items if isinstance(item, dict)]
        value = max((end for end in ends if isinstance(end, int)), default=None)
    return datetime.fromtimestamp(value, tz=UTC) if isinstance(value, int) else None


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


def _handle_event(event_type: str, obj: dict) -> bool:
    subscription = _subscription_for_event(obj)
    if subscription is None:
        logger.warning(f"Stripe {event_type}: no local subscription matched {obj.get('customer')!r}")
        return False

    before = (subscription.plan, subscription.status, subscription.cancel_at_period_end)
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

    context = {**telemetry.user_context(subscription.user), "event_type": event_type}
    after = (subscription.plan, subscription.status, subscription.cancel_at_period_end)
    if before != after:
        transaction.on_commit(
            lambda: telemetry.event(
                "subscription.transition",
                outcome="applied",
                from_plan=before[0],
                to_plan=after[0],
                from_status=before[1],
                to_status=after[1],
                cancel_at_period_end=after[2],
                **context,
            )
        )
    if event_type in {"checkout.session.completed", "invoice.payment_failed"}:
        transaction.on_commit(
            lambda: telemetry.event("billing.action", action=event_type, outcome="applied", **context)
        )
    return True


@csrf_exempt
@require_POST
def webhook_view(request: HttpRequest) -> HttpResponse:
    """Apply a Stripe subscription event, exactly once.

    CSRF-exempt by necessity — the caller is Stripe, not a browser — so the signature
    check is what authenticates the request and must never be skipped.
    """
    if not settings.STRIPE_WEBHOOK_SECRET:
        return _not_configured()

    # Deliberately the module-level verifier rather than StripeClient.construct_event:
    # this view needs no secret key, and a webhook-only deployment should not have to set
    # one just to verify a signature.
    try:
        event = stripe.Webhook.construct_event(
            payload=request.body,
            sig_header=request.headers.get("Stripe-Signature", ""),
            secret=settings.STRIPE_WEBHOOK_SECRET,
        )
    except ValueError, stripe.SignatureVerificationError:
        # Never log the body: an unverified payload is attacker-controlled.
        logger.warning("Stripe webhook rejected: invalid payload or signature")
        telemetry.event("billing.webhook", outcome="rejected")
        return HttpResponse(status=400)

    event_id = event["id"]
    event_type = event["type"]
    # Claim the id first. Stripe retries on any non-2xx, and a duplicate delivery must not
    # re-apply the change.
    context = {"event_type": event_type if event_type in HANDLED_EVENTS else "other"}
    try:
        with transaction.atomic():
            created = ProcessedStripeEvent.objects.get_or_create(
                event_id=event_id, defaults={"event_type": event_type}
            )[1]
            if not created:
                telemetry.event("billing.webhook", outcome="duplicate", **context)
                return JsonResponse({"received": True, "duplicate": True})
            outcome = "ignored"
            if event_type in HANDLED_EVENTS:
                # StripeObject stopped subclassing dict in stripe 15, so the real
                # webhook takes to_dict(); the tests hand back a plain dict.
                obj = event["data"]["object"]
                applied = _handle_event(event_type, obj if isinstance(obj, dict) else obj.to_dict())
                outcome = "applied" if applied else "unmatched"
            transaction.on_commit(lambda: telemetry.event("billing.webhook", outcome=outcome, **context))
    except Exception:
        telemetry.event("billing.webhook", outcome="error", **context)
        raise
    return JsonResponse({"received": True})


def _price(interval: str) -> str:
    if interval == "annual":
        return settings.STRIPE_PRICE_ID_PLUS_ANNUAL or settings.STRIPE_PRICE_ID_PRO
    return settings.STRIPE_PRICE_ID_PLUS_MONTHLY


@require_POST
@csrf_protect
def trial_view(request):
    user = _user(request)
    if user is None:
        return JsonResponse({"detail": "Authentication required."}, status=401)
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        subscription = subscription_for_sync(user)
        if subscription.trial_started_at or entitlements_for_sync(user).is_pro:
            return JsonResponse({"detail": "The trial is available once per account."}, status=409)
        subscription.trial_started_at = datetime.now(tz=UTC)
        subscription.trial_ends_at = subscription.trial_started_at + timedelta(days=14)
        subscription.save(update_fields=["trial_started_at", "trial_ends_at", "updated_at"])
    telemetry.event("billing.action", action="trial_started", outcome="success", **telemetry.user_context(user))
    return JsonResponse(_entitlements_payload(user))


@require_POST
@csrf_protect
def free_routes_view(request):
    user = _user(request)
    if user is None:
        return JsonResponse({"detail": "Authentication required."}, status=401)
    try:
        data = json.loads(request.body)
        ids = data["routeIds"]
        if (
            not isinstance(ids, list)
            or len(ids) > 2
            or any(not isinstance(i, str) for i in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError
        from uuid import UUID

        ids = [UUID(i) for i in ids]
    except ValueError, TypeError, KeyError:
        return JsonResponse({"detail": "Choose up to two of your active routes."}, status=400)
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        routes = RecurringRoute.objects.filter(owner=user, active=True, return_of__isnull=True)
        if routes.filter(id__in=ids).count() != len(ids):
            return JsonResponse({"detail": "Route not found."}, status=404)
        routes.update(free_selected=False)
        routes.filter(id__in=ids).update(free_selected=True)
    return JsonResponse(
        {
            "routeIds": [
                str(i)
                for i in RecurringRoute.objects.filter(
                    id__in=allowed_route_ids(user), return_of__isnull=True
                ).values_list("id", flat=True)
            ]
        }
    )
