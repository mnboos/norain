"""Several grid cells per Open-Meteo request: the batch fetch and the batch cell task."""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from . import grid
from .claims import claim_cell
from .models import CellFetchLease, EnsembleCell, ForecastCell, ForecastJob
from .ratelimit import cooldown_left
from .tasks import MAX_CELL_DEFERS, _refresh_cells_async

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
INMEM_CHANNELS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
DAY = date(2026, 10, 1)
CELLS = [(47.0, 9.0), (47.01, 9.0), (47.02, 9.0)]


def om(i: int) -> dict:
    return {"hourly": {"time": [], "marker": [i]}}


def answer_each(cells, forecast_days, day_key):
    return [om(i) for i, _ in enumerate(cells)]


def _status_error(status, body=None):
    response = httpx.Response(status, json=body, request=httpx.Request("GET", "https://x.test"))
    return httpx.HTTPStatusError("boom", request=response.request, response=response)


@override_settings(CACHES=LOCMEM_CACHE)
class ForecastBatchTests(TestCase):
    def setUp(self):
        cache.clear()

    def fetch(self, cells=CELLS, **kw):
        return async_to_sync(grid.fetch_forecast_cells)(cells, DAY, 2, **kw)

    def test_the_missing_cells_go_out_in_one_request(self):
        ForecastCell.objects.create(
            lat_r=47.0, lon_r=9.0, day_key=DAY, forecast_days=2, source="open-meteo", data=om(9)
        )
        request = AsyncMock(side_effect=answer_each)
        with patch.object(grid, "_fetch_open_meteo_batch", request):
            batch = self.fetch()
        request.assert_awaited_once()
        self.assertEqual(request.await_args.args[0], CELLS[1:])  # the warm cell is not asked for
        self.assertEqual(batch.throttled, [])
        self.assertEqual(
            {cell: row.data["hourly"]["marker"] for cell, row in batch.cells.items()},
            {CELLS[0]: [9], CELLS[1]: [0], CELLS[2]: [1]},
        )
        self.assertEqual(ForecastCell.objects.filter(source="open-meteo").count(), 3)
        self.assertFalse(CellFetchLease.objects.exists(), "every lease released")

    def test_a_batch_larger_than_the_budget_shrinks_to_what_fits(self):
        spend = Mock(side_effect=[0.0, 0.0, 42.0])
        with (
            patch.object(grid, "acquire", spend),
            patch.object(grid, "_fetch_open_meteo_batch", AsyncMock(side_effect=answer_each)) as request,
        ):
            batch = self.fetch(allow_fallback=False)
        self.assertEqual(request.await_args.args[0], CELLS[:2])
        self.assertEqual((batch.throttled, batch.wait), ([CELLS[2]], 42.0))
        self.assertNotIn(CELLS[2], batch.cells)

    def test_a_429_leaves_the_whole_batch_throttled(self):
        with (
            patch.object(grid, "_fetch_open_meteo_batch", AsyncMock(side_effect=_status_error(429))),
            patch.object(grid, "_fetch_met", AsyncMock(side_effect=AssertionError("no fallback while throttled"))),
        ):
            batch = self.fetch(allow_fallback=False)
        self.assertEqual(batch.throttled, CELLS)
        self.assertGreater(cooldown_left(grid.open_meteo_limit()), 0)

    def test_a_failed_request_falls_back_cell_by_cell(self):
        met = AsyncMock(return_value={"properties": {"timeseries": [{"time": "2026-10-01T10:00:00Z", "data": {}}]}})
        with (
            patch.object(grid, "_fetch_open_meteo_batch", AsyncMock(side_effect=_status_error(503))),
            patch.object(grid, "_fetch_met", met),
        ):
            batch = self.fetch(allow_fallback=False)
        self.assertEqual(met.await_count, 3)
        self.assertEqual({row.source for row in batch.cells.values()}, {"met-norway"})

    def test_a_short_answer_is_a_failure_not_a_misfiled_cell(self):
        with (
            patch.object(grid, "_fetch_open_meteo_batch", AsyncMock(return_value=[om(0)])),
            patch.object(grid, "_fetch_met", AsyncMock(side_effect=_status_error(503))),
        ):
            batch = self.fetch(allow_fallback=True)
        self.assertEqual(batch.cells, dict.fromkeys(CELLS))
        self.assertFalse(ForecastCell.objects.exists())

    def test_a_cell_another_caller_is_fetching_is_left_to_it(self):
        CellFetchLease.objects.create(
            kind="forecast",
            lat_r=47.02,
            lon_r=9.0,
            day_key=DAY,
            token="00000000-0000-0000-0000-000000000001",
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )
        single = AsyncMock(return_value="what the holder stored")
        with (
            patch.object(grid, "_fetch_open_meteo_batch", AsyncMock(side_effect=answer_each)) as request,
            patch.object(grid, "get_or_fetch_forecast_cell", single),
        ):
            batch = self.fetch()
        self.assertEqual(request.await_args.args[0], CELLS[:2])
        single.assert_awaited_once_with(47.02, 9.0, DAY, 2, allow_fallback=True)
        self.assertEqual(batch.cells[CELLS[2]], "what the holder stored")


@override_settings(CACHES=LOCMEM_CACHE)
class EnsembleBatchTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_cells_are_snapped_and_weighted_by_member(self):
        request = AsyncMock(
            side_effect=lambda cells, days, day: [
                {"hourly": {"time": []}, "_norain_request_version": grid.ENSEMBLE_REQUEST_VERSION} for _ in cells
            ]
        )
        with (
            patch.object(grid, "_fetch_ensemble_batch", request),
            patch.object(grid, "acquire", Mock(return_value=0.0)) as spend,
        ):
            batch = async_to_sync(grid.fetch_ensemble_cells)([(47.01, 9.02), (47.06, 9.0)], DAY, 2)
        self.assertEqual(request.await_args.args[0], [(47.0, 9.0), (47.05, 9.0)])
        self.assertEqual([call.args[1] for call in spend.call_args_list], [36.0, 36.0])
        self.assertEqual(set(batch.cells), {(47.0, 9.0), (47.05, 9.0)})
        self.assertEqual(EnsembleCell.objects.count(), 2)


class BatchRequestTests(SimpleTestCase):
    def _transport(self, body):
        seen = {}

        def handler(request):
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json=body)

        real = httpx.AsyncClient
        client = patch(
            "core.grid.httpx.AsyncClient",
            side_effect=lambda *a, **kw: real(*a, transport=httpx.MockTransport(handler), **kw),
        )
        return client, seen

    def test_locations_are_comma_joined_and_a_list_comes_back(self):
        client, seen = self._transport([om(0), om(1)])
        with client:
            results = async_to_sync(grid._fetch_open_meteo_batch)(CELLS[:2], 2, DAY.isoformat())
        self.assertEqual((seen["params"]["latitude"], seen["params"]["longitude"]), ("47.0,47.01", "9.0,9.0"))
        self.assertEqual(len(results), 2)
        # "Avoid shade" reads the sunshine by the hour; the 15-minute block would cost twice.
        self.assertIn("sunshine_duration", seen["params"]["hourly"].split(","))
        self.assertNotIn("sunshine_duration", seen["params"]["minutely_15"].split(","))

    def test_one_location_answers_a_bare_object(self):
        client, _ = self._transport({"hourly": {"time": []}})
        with client:
            results = async_to_sync(grid._fetch_ensemble_batch)(CELLS[:1], 2, DAY.isoformat())
        self.assertEqual(results, [{"hourly": {"time": []}, "_norain_request_version": grid.ENSEMBLE_REQUEST_VERSION}])


@override_settings(CACHES=LOCMEM_CACHE, CHANNEL_LAYERS=INMEM_CHANNELS)
class BatchTaskTests(TestCase):
    def setUp(self):
        cache.clear()
        self.job = ForecastJob.objects.create(
            key="batch", kind="adhoc", params={}, status=ForecastJob.Status.FETCHING, cells_total=5
        )

    def run_task(self, batch, *, attempt=0, kind="forecast"):
        fetch = AsyncMock(return_value=batch)
        defer = AsyncMock()
        target = "core.tasks.fetch_forecast_cells" if kind == "forecast" else "core.tasks.fetch_ensemble_cells"
        with (
            patch(target, fetch),
            patch(
                "core.tasks.refresh_forecast_cells", SimpleNamespace(using=lambda **kw: SimpleNamespace(aenqueue=defer))
            ),
            patch("core.tasks.publish", AsyncMock()),
        ):
            async_to_sync(_refresh_cells_async)(
                kind, [list(c) for c in CELLS], DAY.isoformat(), 2, str(self.job.id), attempt
            )
        self.job.refresh_from_db()
        return fetch, defer

    def test_each_cell_settles_and_failures_count(self):
        for cell in CELLS:
            claim_cell("forecast", *cell, DAY.isoformat(), 2)
        self.run_task(grid.CellBatch({CELLS[0]: "row", CELLS[1]: "row", CELLS[2]: None}))
        self.assertEqual((self.job.cells_settled, self.job.cells_failed), (3, 1))
        self.assertTrue(all(claim_cell("forecast", *cell, DAY.isoformat(), 2) for cell in CELLS), "claims released")

    def test_throttled_cells_are_deferred_not_settled(self):
        fetch, defer = self.run_task(grid.CellBatch({CELLS[0]: "row"}, [CELLS[1], CELLS[2]], 30.0))
        fetch.assert_awaited_once_with(CELLS, DAY.isoformat(), 2, allow_fallback=False)
        defer.assert_awaited_once_with(
            [list(CELLS[1]), list(CELLS[2])], DAY.isoformat(), 2, str(self.job.id), attempt=1
        )
        self.assertEqual(self.job.cells_settled, 1)

    def test_the_hourly_limit_falls_back_at_once(self):
        throttled = grid.CellBatch({}, CELLS, 3000.0)
        fallen_back = grid.CellBatch(dict.fromkeys(CELLS, "row"))
        fetch = AsyncMock(side_effect=[throttled, fallen_back])
        with patch("core.tasks.fetch_forecast_cells", fetch), patch("core.tasks.publish", AsyncMock()):
            async_to_sync(_refresh_cells_async)(
                "forecast", [list(c) for c in CELLS], DAY.isoformat(), 2, str(self.job.id)
            )
        self.assertEqual(fetch.await_args.kwargs, {"allow_fallback": True})
        self.job.refresh_from_db()
        self.assertEqual((self.job.cells_settled, self.job.cells_failed), (3, 0))

    def test_the_last_attempt_allows_the_fallback(self):
        fetch, _ = self.run_task(grid.CellBatch(dict.fromkeys(CELLS, "row")), attempt=MAX_CELL_DEFERS)
        self.assertEqual(fetch.await_args.kwargs, {"allow_fallback": True})

    def test_ensemble_batches_ask_to_be_told_when_throttled(self):
        fetch, _ = self.run_task(grid.CellBatch(dict.fromkeys([(47.0, 9.0)], "row")), kind="ensemble")
        self.assertEqual(fetch.await_args.args[0], [(47.0, 9.0)] * 3)  # snapped to the lattice
        self.assertEqual(fetch.await_args.kwargs, {"raise_throttled": True})
