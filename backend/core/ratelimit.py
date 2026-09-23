"""Call budgets and adaptive back-off for the external weather providers.

Every worker replica fetches, so the counters live in the cache (Redis), never in the
process. A provider's ``Limit`` lists fixed windows in *calls* as the provider counts them;
``acquire`` spends a weighted call against all of them at once or refuses and says how long
to wait.

The adaptive part: a 429 means the provider counts differently from what we assumed (a
weight too low, another client on the same IP, a limit changed without notice). So a 429
starts a cooldown that every worker honours (``Retry-After``, else the window the reply
names, else doubling per consecutive 429) and halves a *factor* on the shortest window.
Only there: halving the hour or the day would lock a half-spent window for the rest of it,
and every cell would go to the fallback over a minute-level 429.
The factor climbs back a step per quiet minute. A burst of 429s from several workers counts
once: only the reply that starts the cooldown adapts.

Failure mode is per provider. Open-Meteo fails *open* (a cache outage costs pacing, never
forecasts, like ``claims.py``); OpenWeatherMap and Weather Underground fail *closed*
(going over costs money or can get the key switched off, like the old ``_spend_call``).
"""

import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from django.core.cache import cache
from loguru import logger
from redis.exceptions import RedisError

from .telemetry import emit

SCALE = 100  # counters hold centi-calls: the cache has no float increment

FACTOR_FLOOR = 0.1
FACTOR_STEP = 0.1
FACTOR_TTL = 7 * 86400  # a lost factor resets to 1.0, which is only the configured limit
RECOVERY_EVERY = 60
RECOVERY_QUIET = 300  # no step back up until this long after the last cooldown ended
MAX_COOLDOWN = 3600

CACHE_ERRORS = (RedisError, OSError, ValueError)  # ValueError: incr on a key that just expired


@dataclass(frozen=True)
class Limit:
    provider: str
    windows: tuple[tuple[int, float], ...]  # (seconds, calls)
    fail_open: bool
    base_cooldown: int = 60


class ProviderThrottled(Exception):  # noqa: N818 -- a state, not an error
    """The provider may not be called for ``retry_after`` seconds.

    Deliberately not an ``httpx.HTTPError`` / ``ValueError``: the fetch paths catch those and
    fall back to another provider, which is exactly what a throttled caller must decide itself.
    """

    def __init__(self, provider: str, retry_after: float):
        super().__init__(f"{provider} throttled for {retry_after:.0f} s")
        self.provider = provider
        self.retry_after = retry_after


def describe_failure(exc: Exception) -> str:
    """An error for the log. httpx puts the whole URL in a status error, API key included."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code} from {exc.request.url.host}"
    return f"{type(exc).__name__}: {exc}"


def _now() -> float:
    return time.time()


def _key(limit: Limit, name: str) -> str:
    return f"rl:{limit.provider}:{name}"


def cooldown_left(limit: Limit, now: float | None = None) -> float:
    """Seconds until the provider may be called again after a 429, 0 when not cooling down."""
    now = _now() if now is None else now
    try:
        until = cache.get(_key(limit, "cooldown"))
    except CACHE_ERRORS:
        return 0.0
    return max(0.0, until - now) if until else 0.0


def _factor(limit: Limit) -> float:
    """The current scale on every window, after one recovery step if one is due."""
    factor = cache.get(_key(limit, "factor"), 1.0)
    if factor >= 1.0 or cache.get(_key(limit, "strikes")) is not None:
        return factor
    if cache.add(_key(limit, "raised"), 1, RECOVERY_EVERY):
        factor = min(1.0, factor + FACTOR_STEP)
        cache.set(_key(limit, "factor"), factor, FACTOR_TTL)
        logger.info(f"{limit.provider}: rate factor back up to {factor:.1f}")
    return factor


def acquire(limit: Limit, weight: float = 1.0) -> float:
    """Spend ``weight`` calls, or refuse: 0.0 means go ahead, anything else is seconds to wait.

    A refusal spends nothing: counters already raised for this call are lowered again.
    """
    now = _now()
    try:
        if wait := cooldown_left(limit, now):
            emit("count", "provider.throttled", provider=limit.provider, reason="cooldown")
            return wait
        factor = _factor(limit)
        units = math.ceil(weight * SCALE)
        shortest = min(seconds for seconds, _ in limit.windows)
        spent: list[str] = []
        for seconds, calls in limit.windows:
            key = _key(limit, f"{seconds}:{int(now // seconds)}")
            cache.add(key, 0, seconds + 60)
            if cache.incr(key, units) > calls * (factor if seconds == shortest else 1.0) * SCALE:
                for taken in (*spent, key):
                    cache.decr(taken, units)
                emit("count", "provider.throttled", provider=limit.provider, reason="budget")
                return max(1.0, seconds - now % seconds)
            spent.append(key)
    except CACHE_ERRORS as exc:
        if limit.fail_open:
            logger.warning(f"{limit.provider} call budget unavailable, calling anyway: {exc}")
            return 0.0
        logger.warning(f"{limit.provider} call budget unavailable, skipping the call: {exc}")
        return float(limit.base_cooldown)
    return 0.0


def _retry_after(response) -> float | None:
    try:
        value = float(response.headers.get("Retry-After"))
    except AttributeError, TypeError, ValueError:
        return None
    return value if value > 0 else None


def _reason(response) -> str:
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 -- any unreadable body just has no reason
        return ""
    reason = body.get("reason") if isinstance(body, dict) else None
    return reason if isinstance(reason, str) else ""


def _until_window_end(reason: str, now: float) -> float | None:
    """Seconds to the end of the window a 429 reason names, if it names hour or day.

    A guess at the wording (Open-Meteo says e.g. "Hourly API request limit exceeded"); the
    reason is logged with every 429, so check it there before relying on more of it.
    """
    text = reason.lower()
    for word, seconds in (("daily", 86400), ("day", 86400), ("hour", 3600)):
        if word in text:
            return seconds - now % seconds
    return None


def record_throttle(limit: Limit, response) -> float:
    """Adapt to a 429: start a shared cooldown and halve the factor. Returns the cooldown."""
    now = _now()
    reason = _reason(response)
    try:
        strikes = cache.get(_key(limit, "strikes"), 0)
        cooldown = (
            _retry_after(response)
            or _until_window_end(reason, now)
            or min(MAX_COOLDOWN, limit.base_cooldown * 2**strikes)
        )
        if not cache.add(_key(limit, "cooldown"), now + cooldown, math.ceil(cooldown)):
            # Another worker's 429 from the same burst already adapted.
            return cooldown_left(limit, now) or cooldown
        cache.set(_key(limit, "strikes"), strikes + 1, math.ceil(cooldown) + RECOVERY_QUIET)
        factor = max(FACTOR_FLOOR, cache.get(_key(limit, "factor"), 1.0) / 2)
        cache.set(_key(limit, "factor"), factor, FACTOR_TTL)
    except CACHE_ERRORS as exc:
        logger.warning(f"Could not record the {limit.provider} 429: {exc}")
        return float(limit.base_cooldown)
    resume = datetime.fromtimestamp(now + cooldown, tz=UTC)
    logger.warning(
        f"{limit.provider} answered 429 ({reason or 'no reason given'}); pausing until {resume:%H:%M:%S} UTC, "
        f"rate factor now {factor:.2f}"
    )
    return cooldown
