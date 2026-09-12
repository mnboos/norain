"""In-flight claims for grid-cell fetches.

Many sample points share one ~1 km² cell, and both the request path and the hourly
pre-warm scan enqueue cell fetches. Without a claim, every caller that sees a cold cell
enqueues its own task and the same provider request is made several times over.

The claim lives in the cache rather than on the cell row on purpose: ``ForecastCell``
and ``EnsembleCell`` set ``fetched_at`` with ``auto_now=True``, so saving a claim field
would bump it and make a stale cell look fresh. A cache key also expires on its own, so
a worker that dies mid-fetch needs no reaper.
"""

from datetime import date

from django.core.cache import cache
from loguru import logger
from redis.exceptions import RedisError

# Longer than the 30 s provider timeout so a claim outlives the fetch it guards, and far
# shorter than the hourly pre-warm cadence so a lost release always heals before the next
# scan would want the cell again. Keep that relationship if either number moves.
CLAIM_TTL = 300


def _key(kind: str, lat_r: float, lon_r: float, day_key: str | date, forecast_days: int) -> str:
    if isinstance(day_key, date):
        day_key = day_key.isoformat()
    return f"cellclaim:{kind}:{lat_r}:{lon_r}:{day_key}:{forecast_days}"


def claim_cell(kind: str, lat_r: float, lon_r: float, day_key: str | date, forecast_days: int) -> bool:
    """True when this caller now owns the fetch for this cell and should enqueue it.

    Fails *open*: if the cache is unreachable the claim is granted, so a Redis outage
    costs deduplication (the same cell gets fetched a few times) rather than forecasts.
    """
    try:
        return cache.add(_key(kind, lat_r, lon_r, day_key, forecast_days), 1, CLAIM_TTL)
    except (RedisError, OSError) as exc:  # the cache is an optimisation, never a gate
        logger.warning(f"Cell claim unavailable, proceeding without deduplication: {exc}")
        return True


def release_cell(kind: str, lat_r: float, lon_r: float, day_key: str | date, forecast_days: int) -> None:
    """Drop a claim so the cell can be retried.

    Must run on failure as well as success. A cell fetch legitimately fails when both
    providers are down; holding the claim through that would make the next scan find the
    cell cold *and* unclaimable, stretching one failed fetch across the whole TTL.
    """
    try:
        cache.delete(_key(kind, lat_r, lon_r, day_key, forecast_days))
    except (RedisError, OSError) as exc:  # the cache is an optimisation, never a gate
        # The TTL is the backstop; nothing is permanently stuck.
        logger.warning(f"Could not release cell claim: {exc}")
