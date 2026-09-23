"""The fetch lease: at most one provider request per grid cell at a time.

``core.claims`` only deduplicates *enqueuing*, fails open, and is deliberately ignored by
job planning, so several tasks for one cell can still run side by side. This lease is
what makes the fetch itself happen once: it is taken inside ``grid.get_or_fetch_*``,
the holder re-checks the cache before fetching, and a caller that finds the lease held
waits for it and then only reads what the holder stored.

It lives in Postgres rather than Redis because it must fail *closed*: without a lock
there is no deduplication at all, and without Postgres a fetched cell could not be stored
anyway, so this costs no availability. A row that outlives a dead worker simply expires.

Waiters must never wait on the enqueue claim instead: a task still sitting in the queue
holds that one, possibly behind the waiter itself.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

from asgiref.sync import sync_to_async
from django.db import IntegrityError, transaction

from core.models import CellFetchLease

# The forecast worst case is Open-Meteo and then OWM, 30 s timeout each (grid.py), plus
# the store. The lease must outlive that, or a slow holder loses it mid-fetch and a
# waiter fetches the same cell again. Keep it above the sum if a timeout moves.
LEASE_TTL = timedelta(seconds=90)
POLL_INTERVAL = 0.5


def _key(kind: str, lat_r: float, lon_r: float, day_key: date) -> dict:
    return {"kind": kind, "lat_r": lat_r, "lon_r": lon_r, "day_key": day_key}


def _acquire_sync(kind: str, lat_r: float, lon_r: float, day_key: date) -> uuid.UUID | None:
    """The new token when this caller now holds the lease, None when someone else does."""
    key = _key(kind, lat_r, lon_r, day_key)
    token = uuid.uuid4()
    now = datetime.now(tz=UTC)
    # One UPDATE, so of two callers finding the same expired lease only one takes it over.
    if CellFetchLease.objects.filter(**key, expires_at__lt=now).update(token=token, expires_at=now + LEASE_TTL):
        return token
    try:
        # A savepoint: a failed INSERT must not poison a surrounding transaction.
        with transaction.atomic():
            CellFetchLease.objects.create(**key, token=token, expires_at=now + LEASE_TTL)
    except IntegrityError:
        return None
    return token


def _release_sync(kind: str, lat_r: float, lon_r: float, day_key: date, token: uuid.UUID) -> None:
    # By token, so a holder that ran past its lease cannot delete its successor's.
    CellFetchLease.objects.filter(**_key(kind, lat_r, lon_r, day_key), token=token).delete()


def _held_sync(kind: str, lat_r: float, lon_r: float, day_key: date) -> bool:
    return CellFetchLease.objects.filter(
        **_key(kind, lat_r, lon_r, day_key), expires_at__gte=datetime.now(tz=UTC)
    ).exists()


@asynccontextmanager
async def fetch_lease(kind: str, lat_r: float, lon_r: float, day_key: date) -> AsyncIterator[bool]:
    """Yield True to the one caller that may fetch this cell, False to everyone else.

    A caller that gets False has already waited for the holder to finish (or its lease to
    expire) and must only read the cache; it must not fetch.
    """
    token = await sync_to_async(_acquire_sync)(kind, lat_r, lon_r, day_key)
    if token is None:
        deadline = datetime.now(tz=UTC) + LEASE_TTL
        # Polled: the holder is usually another worker process, which no asyncio.Event reaches.
        while datetime.now(tz=UTC) < deadline and await sync_to_async(_held_sync)(kind, lat_r, lon_r, day_key):  # noqa: ASYNC110
            await asyncio.sleep(POLL_INTERVAL)
        yield False
        return
    try:
        yield True
    finally:
        await sync_to_async(_release_sync)(kind, lat_r, lon_r, day_key, token)
