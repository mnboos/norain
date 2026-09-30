"""Per-browser limits on starting a sign-up and requesting a sign-in code.

Both mail a code to whatever address is typed, and both are allauth's endpoints, so the
limit sits in front of them as middleware. It stacks on Caddy's per-IP flood limits and
allauth's per-address ones:

- A recognised browser (``core.fingerprinting.device_keys``) is counted per key, whatever
  its network: a fresh cookie or another IP does not reset it. A browser that randomises its
  probes (Safari's private tabs) is also counted by its coarse print on its network, so a new
  private tab does not start afresh either.
- An unknown, keyless or suspicious browser shares one count per IP (an IPv6 address by its
  /56). A suspicious browser pays it on top of its own key's: lying never frees a browser.

Only requests allauth accepted are counted (a refused address costs nothing), so parallel
requests can overshoot a limit by a few. It fails open, like the other per-minute limits:
this guards the mail budget, not access.
Password sign-in and code *confirmation* are not touched (axes and allauth own those).
"""

import time
from collections.abc import Callable

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.translation import gettext
from loguru import logger
from redis.exceptions import RedisError

from core import fingerprinting as fp
from core.auth.lockout import client_ip

_PREFIX = "/api/allauth/browser/v1/auth/"
# path -> (name, window in seconds, per browser key, per IP when unknown or suspicious). The two
# limits are equal on purpose: after how many requests the refusals start must not say whether
# the browser was recognised.
RULES = {
    f"{_PREFIX}signup": ("signup", 24 * 3600, 3, 3),
    f"{_PREFIX}code/request": ("code", 3600, 10, 10),
}


# allauth's answer to an accepted sign-up or code request: 401 ("verify your email" / "enter
# the code") or 200. A refused one (400, allauth's own 429) spends nothing.
ACCEPTED = {200, 401}


def counters(request: HttpRequest) -> list[tuple[str, int, int]]:
    """``(cache key, limit, window)`` for every count this request is held to; empty if none."""
    if request.method != "POST" or not settings.BROWSER_FINGERPRINT_ENABLED:
        return []
    rule = RULES.get(request.path)
    if rule is None:
        return []
    name, window, per_key, per_ip = rule
    slot = int(time.time()) // window
    ip = client_ip(request)
    assessment = fp.get_browser_assessment(request)
    keys = fp.device_keys(assessment, ip)
    # Every key is counted, so neither a new cookie nor a new key alone starts afresh. A
    # network key (``p:``) stands for whoever shares the address, so it gets the IP's
    # allowance: look-alikes behind one CGNAT are never held tighter than without it.
    held = [
        (
            f"auth:device:{name}:{fp.keyed_id('auth', key)}:{slot}",
            per_ip if key.startswith("p:") else per_key,
            window,
        )
        for key in keys
    ]
    young = settings.BROWSER_KEY_AGE_SIGNUPS and not fp.is_established(assessment)
    if not keys or not fp.is_trusted(assessment) or young:
        # Unknown, keyless or suspicious: the IP's shared count (an IPv6 address by its /56),
        # which a suspicious browser pays on top of its own key's.
        held.append((f"auth:device:{name}:ip:{fp.keyed_id('auth', fp.ip_floor(ip))}:{slot}", per_ip, window))
    return held


def refused(held: list[tuple[str, int, int]]) -> bool:
    try:
        return any((cache.get(key) or 0) >= limit for key, limit, _ in held)
    except (RedisError, OSError) as exc:
        logger.warning(f"Device throttle unavailable: {exc}")
        return False


def count(held: list[tuple[str, int, int]]) -> None:
    try:
        for key, _, window in held:
            cache.add(key, 0, window + 60)
            cache.incr(key)
    except (RedisError, OSError, ValueError) as exc:
        logger.warning(f"Device throttle unavailable: {exc}")


class DeviceThrottleMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        held = counters(request)
        if held and refused(held):
            # JSON with `detail`, like the axes lockout and Caddy's 429s: the SPA parses every body.
            return JsonResponse(
                {"detail": gettext("Zu viele Versuche von diesem Browser. Bitte versuche es später noch einmal.")},
                status=429,
            )
        response = self.get_response(request)
        # Only what allauth accepted counts: a typo in the address costs nothing.
        if held and response.status_code in ACCEPTED:
            count(held)
        return response
