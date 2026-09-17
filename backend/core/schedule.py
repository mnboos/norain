"""Schedule utilities for recurring routes.

Uses croniter to compute next departure times from cron expressions.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from croniter import croniter
from loguru import logger

# Cron schedules, provider timestamps and the forecast window are all Swiss wall time,
# whatever zone the server itself runs in.
LOCAL_TZ = ZoneInfo("Europe/Zurich")


def local_now() -> datetime:
    return datetime.now(tz=LOCAL_TZ)


def local_today() -> date:
    """Today in Swiss local time: the origin of the Open-Meteo forecast window."""
    return local_now().date()


def _local(after: datetime | None) -> datetime:
    """Read *after* as Swiss wall time; a value without a zone already is."""
    if after is None:
        return local_now()
    return after.astimezone(LOCAL_TZ) if after.tzinfo is not None else after.replace(tzinfo=LOCAL_TZ)


def check_schedule_cron(cron_expr: str) -> str:
    """Accept only a plain 5-field cron expression that croniter can parse.

    croniter also takes a seconds field and ``@daily``-style aliases; the form never
    sends those, so refusing them keeps every stored schedule in one shape.
    """
    if len(cron_expr.split()) != 5 or not croniter.is_valid(cron_expr):
        raise ValueError(f"invalid cron expression {cron_expr!r}; expected 5 fields")
    return cron_expr


def next_departure(cron_expr: str, after: datetime | None = None) -> datetime | None:
    """Return the next datetime matching the cron expression after *after* (defaults to now).

    Returns ``None`` if the cron expression is invalid or cannot be parsed.
    """
    try:
        it = croniter(cron_expr, _local(after))
        return it.get_next(datetime)
    except (ValueError, KeyError) as exc:
        # A warning, not a traceback: the route list is polled every 60 s and would log
        # the same stack for one bad row each time.
        logger.warning("next_departure: invalid cron expression {!r}: {}", cron_expr, exc)
        return None


def upcoming_departures(
    cron_expr: str,
    count: int = 5,
    after: datetime | None = None,
) -> list[datetime]:
    """Return the next *count* departure datetimes matching the cron expression.

    Returns an empty list if the cron expression is invalid.
    """
    try:
        it = croniter(cron_expr, _local(after))
        return [it.get_next(datetime) for _ in range(count)]
    except (ValueError, KeyError) as exc:
        logger.warning("upcoming_departures: invalid cron expression {!r}: {}", cron_expr, exc)
        return []


def forecast_available_at(dt: datetime) -> bool:
    """Return True if *dt* falls within the 16-day Open-Meteo forecast window.

    Open-Meteo supports up to 16 days of forecast (today + 15 days).
    """
    today = local_today()
    available = today <= dt.date() <= today + timedelta(days=15)
    if not available:
        logger.debug(
            "forecast_available_at: dt={} is outside window [{} … {}]",
            dt.isoformat(),
            today.isoformat(),
            (today + timedelta(days=15)).isoformat(),
        )
    return available
