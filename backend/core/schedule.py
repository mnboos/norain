"""Schedule utilities for recurring routes.

Uses croniter to compute next departure times from cron expressions.
"""

from datetime import datetime, timedelta

from croniter import croniter
from loguru import logger


def next_departure(cron_expr: str, after: datetime | None = None) -> datetime | None:
    """Return the next datetime matching the cron expression after *after* (defaults to now).

    Returns ``None`` if the cron expression is invalid or cannot be parsed.
    """
    if after is None:
        after = datetime.now()

    try:
        it = croniter(cron_expr, after)
        return it.get_next(datetime)
    except:
        logger.exception("next_departure: failed to parse cron expression {!r}", cron_expr)
        raise


def upcoming_departures(
    cron_expr: str,
    count: int = 5,
    after: datetime | None = None,
) -> list[datetime]:
    """Return the next *count* departure datetimes matching the cron expression.

    Returns an empty list if the cron expression is invalid.
    """
    if after is None:
        after = datetime.now()

    try:
        it = croniter(cron_expr, after)
        return [it.get_next(datetime) for _ in range(count)]
    except:
        logger.exception("upcoming_departures: failed to parse cron expression {!r}", cron_expr)
        raise


def forecast_available_at(dt: datetime) -> bool:
    """Return True if *dt* falls within the 16-day Open-Meteo forecast window.

    Open-Meteo supports up to 16 days of forecast (today + 15 days).
    """
    today = datetime.now().date()
    available = today <= dt.date() <= today + timedelta(days=15)
    if not available:
        logger.debug(
            "forecast_available_at: dt={} is outside window [{} … {}]",
            dt.isoformat(),
            today.isoformat(),
            (today + timedelta(days=15)).isoformat(),
        )
    return available
