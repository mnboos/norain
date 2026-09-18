"""What each billing tier is allowed to do, and every quota in one place.

Deliberately independent of Stripe: these functions read the local Subscription row, which
the webhook keeps in sync. That keeps the enforcement sites testable without API keys, and
means a tier set by hand in the Django admin behaves exactly like a paid one.

Enforced at these places — miss any one and the limit is not real:
  * create_route / update_route (core/api/recurring_route.py) — the route count.
  * compute_route_weather_job (core/tasks.py) — the ensemble spread is stripped before the
    result is stored, and the station correction is only computed for accounts that have it.
  * _prewarm_routes (core/tasks.py) — the pre-warm fan-out, which is what actually spends
    the Open-Meteo budget. It is nowhere near the HTTP layer, so it is the easy one to forget.
  * plan_forecast_job (core/tasks.py) — the station task, which spends the Weather
    Underground budget. The pre-warm scan never fetches stations at all.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from asgiref.sync import sync_to_async
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser

from .forecast_schemas import WeatherSample
from .models import Plan, RecurringRoute, Subscription, User


@dataclass(frozen=True)
class Entitlements:
    """The limits for one tier. `max_routes=None` means unlimited."""

    plan: str
    max_routes: int | None
    ensemble_uncertainty: bool
    station_correction: bool
    departure_comparison: bool = False
    max_briefing_routes: int = 0

    @property
    def is_pro(self) -> bool:
        return self.plan == Plan.PRO

    def result_marker(self) -> dict[str, bool]:
        """The limits that shape a stored forecast, recorded in it so a tier change can be detected.

        Compared by ``get_or_start_job``: a result built for another tier is never reused.
        """
        return {
            "ensemble_uncertainty": self.ensemble_uncertainty,
            "station_correction": self.station_correction,
            "departure_comparison": self.departure_comparison,
        }


FREE = Entitlements(plan=Plan.FREE, max_routes=2, ensemble_uncertainty=False, station_correction=False)
PRO = Entitlements(
    plan=Plan.PRO,
    max_routes=20,
    ensemble_uncertainty=True,
    station_correction=True,
    departure_comparison=True,
    max_briefing_routes=5,
)

BY_PLAN = {Plan.FREE: FREE, Plan.PRO: PRO}


def _entitlements_for_subscription(subscription: Subscription | None) -> Entitlements:
    """Paid entitlements only while the subscription is actually current."""
    if subscription is None:
        return FREE
    if subscription.complimentary_until and subscription.complimentary_until > datetime.now(tz=UTC):
        return PRO
    if subscription.trial_ends_at and subscription.trial_ends_at > datetime.now(tz=UTC):
        return PRO
    if subscription.plan != Plan.PRO:
        return FREE
    if subscription.status and subscription.status not in Subscription.ACTIVE_STATUSES:
        return FREE
    if subscription.current_period_end is not None and subscription.current_period_end <= datetime.now(tz=UTC):
        return FREE
    return PRO


def entitlements_for_sync(user: AbstractBaseUser | AnonymousUser | None) -> Entitlements:
    """Tier for a user. Anonymous users, and users with no Subscription row, are free."""
    if user is None or not getattr(user, "is_authenticated", False):
        return FREE
    subscription = Subscription.objects.filter(user=user).first()
    return _entitlements_for_subscription(subscription)


async def entitlements_for(user: AbstractBaseUser | AnonymousUser | None) -> Entitlements:
    return await sync_to_async(entitlements_for_sync)(user)


def subscription_for_sync(user: User) -> Subscription:
    """The user's Subscription row, created on the free tier if absent."""
    return Subscription.objects.get_or_create(user=user)[0]


def strip_uncertainty(samples: list[WeatherSample]) -> None:
    """Remove the ensemble spread from samples in place, for accounts without it.

    Mutates the samples, so it is only safe on a per-request object. Both call sites build
    their `RouteWeatherOut` fresh; if anything ever caches one, a free request would strip
    the spread out of the cached copy and Pro accounts would silently lose it too.

    `pop` and `rain_if_wet` deliberately stay: ensemble cells are shared between all
    accounts and pre-warmed anyway, so serving the rain probability costs nothing extra,
    and a forecast app that will not say whether it might rain is not worth using. What
    Pro buys is the p10/median/p90 spread and the per-model breakdown.
    """
    for sample in samples:
        sample.uncertainty = None


def allowed_route_ids(user) -> list:
    if user is None or not user.is_authenticated:
        return []
    limits = entitlements_for_sync(user)
    routes = RecurringRoute.objects.filter(owner=user, active=True, return_of__isnull=True).order_by(
        "-free_selected", "created_at", "id"
    )
    roots = list(routes.values_list("id", flat=True)[: limits.max_routes])
    return roots + list(
        RecurringRoute.objects.filter(owner=user, active=True, return_of_id__in=roots).values_list("id", flat=True)
    )


def briefing_route_ids(user) -> list:
    limits = entitlements_for_sync(user)
    if not limits.max_briefing_routes:
        return []
    roots = list(
        RecurringRoute.objects.filter(
            id__in=allowed_route_ids(user), return_of__isnull=True, briefing_channel__in=["email", "push"]
        )
        .order_by("created_at", "id")
        .values_list("id", flat=True)[: limits.max_briefing_routes]
    )
    return roots + list(
        RecurringRoute.objects.filter(owner=user, active=True, return_of_id__in=roots)
        .exclude(briefing_channel="")
        .values_list("id", flat=True)
    )


async def forecast_params_for(user, params: dict) -> dict:
    """Normalize saved windows after expiry, including already queued jobs."""
    if (await entitlements_for(user)).departure_comparison:
        return params
    return {
        k: v
        for k, v in params.items()
        if k
        not in {
            "departure_flex_before_minutes",
            "departure_flex_after_minutes",
        }
    }


def access_source(user) -> str:
    """Keep gifts and trials out of paid conversion metrics."""
    if user is None or not user.is_authenticated:
        return "free"
    subscription = Subscription.objects.filter(user=user).first()
    if not subscription or not _entitlements_for_subscription(subscription).is_pro:
        return "free"
    now = datetime.now(tz=UTC)
    if (
        subscription.stripe_subscription_id
        and subscription.status in Subscription.ACTIVE_STATUSES
        and (subscription.current_period_end is None or subscription.current_period_end > now)
    ):
        return "paid"
    if subscription.complimentary_until and subscription.complimentary_until > now:
        return "complimentary"
    if subscription.trial_ends_at and subscription.trial_ends_at > now:
        return "trial"
    return "admin"
