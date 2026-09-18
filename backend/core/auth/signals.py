"""Side effects of allauth's sign-up and sign-in flows.

These used to live in our own auth views. allauth runs the flows now, so they hang off
its signals. They are allauth's signals, not Django's: ``force_login`` in tests and the
admin sign-in do not send them.
"""

from allauth.account.signals import (
    email_confirmed,
    password_changed,
    password_reset,
    password_set,
    user_logged_in,
    user_signed_up,
)
from django.db import transaction
from django.dispatch import receiver
from loguru import logger

from core import telemetry
from core.tasks import refresh_user_forecasts


@receiver(user_logged_in)
def refresh_forecasts_after_login(sender, request, user, **kwargs) -> None:
    """Queue the check for stale forecasts on the user's routes. Never fails the sign-in."""
    telemetry.event("account.action", action="login", outcome="success", **telemetry.user_context(user))
    try:
        refresh_user_forecasts.enqueue(user.pk)
    except Exception:  # noqa: BLE001 -- a sign-in that worked must not turn into a 500 over pre-warming
        logger.exception(f"Could not enqueue refresh_user_forecasts for user {user.pk}")


@receiver(user_signed_up)
def count_signup(sender, request, user, **kwargs) -> None:
    context = telemetry.user_context(user)
    transaction.on_commit(lambda: telemetry.event("account.action", action="created", outcome="success", **context))


@receiver(email_confirmed)
def count_verification(sender, request, email_address, **kwargs) -> None:
    telemetry.event(
        "account.action", action="verified", outcome="success", **telemetry.user_context(email_address.user)
    )


@receiver(password_set)
@receiver(password_changed)
@receiver(password_reset)
def count_password_change(sender, request, user, **kwargs) -> None:
    telemetry.event("account.action", action="password_changed", outcome="success", **telemetry.user_context(user))
