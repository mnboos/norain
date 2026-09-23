"""One provider request per grid cell, however many callers want it at once."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

from asgiref.sync import sync_to_async
from django.test import TransactionTestCase, override_settings

from . import cell_lease, grid
from .models import CellFetchLease, EnsembleCell, ForecastCell

DAY = date(2026, 9, 23)
OM_DATA = {"hourly": {"time": []}}
ENSEMBLE_DATA = {"hourly": {"time": []}, "_norain_request_version": grid.ENSEMBLE_REQUEST_VERSION}


def _slow(result):
    async def fetch(*args, **kwargs):
        # Long enough that every concurrent caller has missed the cache before the store.
        await asyncio.sleep(0.2)
        if isinstance(result, Exception):
            raise result
        return result

    return fetch


def _hold_lease(kind: str = "forecast", expires_in: timedelta = cell_lease.LEASE_TTL) -> CellFetchLease:
    return CellFetchLease.objects.create(
        kind=kind, lat_r=47.0, lon_r=9.0, day_key=DAY, expires_at=datetime.now(tz=UTC) + expires_in
    )


# TransactionTestCase: concurrent callers must see each other's committed lease rows.
# locmem: the fetches spend from core.ratelimit's budget, which must not be the dev Redis's.
@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
@patch.object(cell_lease, "POLL_INTERVAL", 0.01)
class CellFetchLeaseTests(TransactionTestCase):
    async def test_concurrent_forecast_fetches_call_the_provider_once(self):
        fetch = AsyncMock(side_effect=_slow(OM_DATA))
        with patch.object(grid, "_fetch_open_meteo", fetch), patch.object(grid, "_fetch_owm", AsyncMock()) as owm:
            cells = await asyncio.gather(*(grid.get_or_fetch_forecast_cell(47.0, 9.0, DAY, 2) for _ in range(3)))

        self.assertEqual(fetch.await_count, 1)
        owm.assert_not_awaited()
        self.assertEqual({cell.pk for cell in cells}, {(await ForecastCell.objects.aget()).pk})
        self.assertFalse(await CellFetchLease.objects.aexists())

    async def test_concurrent_ensemble_fetches_call_the_provider_once(self):
        fetch = AsyncMock(side_effect=_slow(ENSEMBLE_DATA))
        with patch.object(grid, "_fetch_ensemble", fetch):
            cells = await asyncio.gather(*(grid.get_or_fetch_ensemble_cell(47.0, 9.0, DAY, 2) for _ in range(3)))

        self.assertEqual(fetch.await_count, 1)
        self.assertEqual({cell.pk for cell in cells}, {(await EnsembleCell.objects.aget()).pk})

    async def test_a_later_caller_reads_the_stored_cell(self):
        fetch = AsyncMock(return_value=OM_DATA)
        with patch.object(grid, "_fetch_open_meteo", fetch):
            await grid.get_or_fetch_forecast_cell(47.0, 9.0, DAY, 2)
            await grid.get_or_fetch_forecast_cell(47.0, 9.0, DAY, 2)
        self.assertEqual(fetch.await_count, 1)

    async def test_callers_waiting_on_a_failed_fetch_do_not_fetch_again(self):
        """A provider that just refused must not be asked again by every waiter."""
        fetch = AsyncMock(side_effect=_slow(ValueError("429")))
        owm = AsyncMock(return_value=None)
        with patch.object(grid, "_fetch_open_meteo", fetch), patch.object(grid, "_fetch_owm", owm):
            cells = await asyncio.gather(*(grid.get_or_fetch_forecast_cell(47.0, 9.0, DAY, 2) for _ in range(3)))

        self.assertEqual(cells, [None, None, None])
        self.assertEqual((fetch.await_count, owm.await_count), (1, 1))
        self.assertFalse(await CellFetchLease.objects.aexists())

    async def test_waiter_reads_what_the_holder_stored(self):
        lease = await sync_to_async(_hold_lease)()
        fetch = AsyncMock(return_value=OM_DATA)
        with patch.object(grid, "_fetch_open_meteo", fetch):
            waiter = asyncio.create_task(grid.get_or_fetch_forecast_cell(47.0, 9.0, DAY, 2))
            await asyncio.sleep(0.1)
            self.assertFalse(waiter.done())
            stored = await ForecastCell.objects.acreate(
                lat_r=47.0, lon_r=9.0, day_key=DAY, forecast_days=2, data=OM_DATA, source="open-meteo"
            )
            await lease.adelete()
            self.assertEqual((await waiter).pk, stored.pk)
        fetch.assert_not_awaited()

    async def test_waiter_needing_a_longer_horizon_fetches_it(self):
        """A 3-day cell does not answer a 16-day request: that one is a different fetch."""
        lease = await sync_to_async(_hold_lease)()
        fetch = AsyncMock(return_value=OM_DATA)
        with patch.object(grid, "_fetch_open_meteo", fetch):
            waiter = asyncio.create_task(grid.get_or_fetch_forecast_cell(47.0, 9.0, DAY, 16))
            await asyncio.sleep(0.1)
            await ForecastCell.objects.acreate(
                lat_r=47.0, lon_r=9.0, day_key=DAY, forecast_days=3, data=OM_DATA, source="open-meteo"
            )
            await lease.adelete()
            cell = await waiter
        self.assertEqual(fetch.await_count, 1)
        self.assertEqual(fetch.await_args.args[2], 16)
        self.assertEqual(cell.forecast_days, 16)

    async def test_an_expired_lease_is_taken_over(self):
        """A worker that died mid-fetch must not block the cell for good."""
        await sync_to_async(_hold_lease)(expires_in=timedelta(seconds=-1))
        fetch = AsyncMock(return_value=OM_DATA)
        with patch.object(grid, "_fetch_open_meteo", fetch):
            self.assertIsNotNone(await grid.get_or_fetch_forecast_cell(47.0, 9.0, DAY, 2))
        self.assertEqual(fetch.await_count, 1)
        self.assertFalse(await CellFetchLease.objects.aexists())

    def test_a_stale_holder_cannot_release_its_successors_lease(self):
        first = cell_lease._acquire_sync("forecast", 47.0, 9.0, DAY)
        self.assertIsNone(cell_lease._acquire_sync("forecast", 47.0, 9.0, DAY))
        CellFetchLease.objects.update(expires_at=datetime.now(tz=UTC) - timedelta(seconds=1))
        second = cell_lease._acquire_sync("forecast", 47.0, 9.0, DAY)
        self.assertIsNotNone(second)

        cell_lease._release_sync("forecast", 47.0, 9.0, DAY, first)
        self.assertEqual(CellFetchLease.objects.get().token, second)

    def test_leases_are_per_kind(self):
        self.assertIsNotNone(cell_lease._acquire_sync("forecast", 47.0, 9.0, DAY))
        self.assertIsNotNone(cell_lease._acquire_sync("ensemble", 47.0, 9.0, DAY))
