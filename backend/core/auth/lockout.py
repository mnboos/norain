"""Hooks for django-axes, which locks out a client after too many failed sign-ins."""

from django.http import HttpRequest, JsonResponse


def client_ip(request: HttpRequest) -> str | None:
    """The client address, as Caddy resolved it.

    daphne runs without proxy headers, so ``REMOTE_ADDR`` is always Caddy. Caddy sets
    ``X-Real-IP`` from ``{client_ip}`` and overwrites any value the client sent, so it is
    the only header trusted here. ``X-Forwarded-For`` is not: its shape differs between
    the direct and the outer-proxy setup. Without Caddy (development) it falls back to
    ``REMOTE_ADDR``.
    """
    return request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR")


def attempted_identity(request: HttpRequest, credentials: dict | None) -> str:
    """What the client typed as its identity, for the axes log and ``axes_reset_username``.

    allauth calls ``authenticate(email=…)`` or ``authenticate(username=…)`` (the SPA always
    sends ``username``), and so does the admin login; axes only looks for ``username`` on
    its own.
    """
    credentials = credentials or {}
    value = credentials.get("email") or credentials.get("username")
    return value.strip().lower() if isinstance(value, str) else ""


def lockout_response(request: HttpRequest, *args) -> JsonResponse:
    """JSON, because the SPA's request() parses every response body as JSON.

    axes calls this with ``(request, original_response, credentials)`` or
    ``(request, credentials)`` depending on the path; neither is needed here.
    """
    return JsonResponse(
        {"detail": "Zu viele fehlgeschlagene Anmeldeversuche. Bitte in 30 Minuten erneut versuchen."},
        status=429,
    )
