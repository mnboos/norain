"""What each billing tier is allowed to do, and every quota in one place.

Deliberately independent of Stripe: these functions read the local Subscription row, which
the webhook keeps in sync. That keeps the enforcement sites testable without API keys, and
means a tier set by hand in the Django admin behaves exactly like a paid one.

Enforced at these places — miss any one and the limit is not real:
  * create_route / update_route (core/api/recurring_route.py) — the route count.
  * assemble_forecast_job (core/tasks.py) — the ensemble spread is stripped before the
    result is stored, and the station correction is only computed for accounts that have it.
  * _prewarm_routes (core/tasks.py) — the pre-warm fan-out, which is what actually spends
    the Open-Meteo budget. It is nowhere near the HTTP layer, so it is the easy one to forget.
  * plan_forecast_job (core/tasks.py) — the station task, which spends the Weather
    Underground budget. The pre-warm scan never fetches stations at all.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from asgiref.sync import sync_to_async

from .models import Plan, Subscription


@dataclass(frozen=True)
class Entitlements:
    """The limits for one tier. `max_routes=None` means unlimited."""

    plan: str
    max_routes: int | None
    ensemble_uncertainty: bool
    station_correction: bool

    @property
    def is_pro(self) -> bool:
        return self.plan == Plan.PRO

    def result_marker(self) -> dict:
        """The limits that shape a stored forecast, recorded in it so a tier change can be detected.

        Compared by ``get_or_start_job``: a result built for another tier is never reused.
        """
        return {"ensemble_uncertainty": self.ensemble_uncertainty, "station_correction": self.station_correction}


FREE = Entitlements(plan=Plan.FREE, max_routes=2, ensemble_uncertainty=False, station_correction=False)
PRO = Entitlements(plan=Plan.PRO, max_routes=None, ensemble_uncertainty=True, station_correction=True)

BY_PLAN = {Plan.FREE: FREE, Plan.PRO: PRO}


def _entitlements_for_subscription(subscription: Subscription | None) -> Entitlements:
    """Paid entitlements only while the subscription is actually current."""
    if subscription is None or subscription.plan != Plan.PRO:
        return FREE
    if subscription.status and subscription.status not in Subscription.ACTIVE_STATUSES:
        return FREE
    if subscription.current_period_end is not None and subscription.current_period_end <= datetime.now(tz=UTC):
        return FREE
    return PRO


def entitlements_for_sync(user) -> Entitlements:
    """Tier for a user. Anonymous users, and users with no Subscription row, are free."""
    if user is None or not getattr(user, "is_authenticated", False):
        return FREE
    subscription = Subscription.objects.filter(user=user).first()
    return _entitlements_for_subscription(subscription)


async def entitlements_for(user) -> Entitlements:
    return await sync_to_async(entitlements_for_sync)(user)


def subscription_for_sync(user) -> Subscription:
    """The user's Subscription row, created on the free tier if absent."""
    return Subscription.objects.get_or_create(user=user)[0]


def strip_uncertainty(samples) -> None:
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
