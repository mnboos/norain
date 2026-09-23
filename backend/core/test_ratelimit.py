"""Provider call budgets, adaptive back-off after a 429, and cell tasks that wait instead of failing."""

import os
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from . import grid
from .jobs import job_key
from .models import ForecastCell, ForecastJob, User
from .ratelimit import Limit, ProviderThrottled, acquire, cooldown_left, describe_failure, record_throttle
from .tasks import MAX_CELL_DEFERS, _refresh_ensemble_cell_async, _refresh_forecast_cell_async

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
INMEM_CHANNELS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

DAY = date(2026, 9, 23)
OM_DATA = {"hourly": {"time": []}}
T0 = 1_800_000_000.0  # on a whole minute, hour and day


def _limit(fail_open=True, **kw) -> Limit:
    return Limit("test", ((60, 3), (3600, 5)), fail_open=fail_open, **kw)


def _response(status=429, headers=None, body=None) -> httpx.Response:
    return httpx.Response(status, headers=headers or {}, json=body, request=httpx.Request("GET", "https://x.test"))


def _status_error(status=429, **kw) -> httpx.HTTPStatusError:
    response = _response(status, **kw)
    return httpx.HTTPStatusError("boom", request=response.request, response=response)


@override_settings(CACHES=LOCMEM_CACHE)
class AcquireTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_spends_weighted_calls_until_a_window_is_full(self):
        with patch("core.ratelimit._now", return_value=T0 + 15):
            self.assertEqual([acquire(_limit(), 1.5) for _ in range(3)], [0.0, 0.0, 45.0])

    def test_the_full_window_decides_the_wait_and_a_refusal_spends_nothing(self):
        with patch("core.ratelimit._now", return_value=T0):
            acquire(_limit(), 3)
        with patch("core.ratelimit._now", return_value=T0 + 60):
            acquire(_limit(), 2)
            # The minute has room, the hour does not.
            self.assertEqual(acquire(_limit(), 1), 3540.0)
        self.assertEqual(cache.get(f"rl:test:60:{int((T0 + 60) // 60)}"), 200)
        self.assertEqual(cache.get(f"rl:test:3600:{int(T0 // 3600)}"), 500)

    def test_failure_mode_without_the_cache(self):
        with patch("core.ratelimit.cache.incr", side_effect=ConnectionError("redis down")):
            self.assertEqual(acquire(_limit(fail_open=True)), 0.0)
            self.assertGreater(acquire(_limit(fail_open=False)), 0)


@override_settings(CACHES=LOCMEM_CACHE)
class AdaptiveTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_retry_after_sets_a_shared_cooldown_and_halves_the_rate(self):
        with patch("core.ratelimit._now", return_value=T0):
            self.assertEqual(record_throttle(_limit(), _response(headers={"Retry-After": "30"})), 30)
            self.assertEqual(cooldown_left(_limit()), 30)
            self.assertEqual(acquire(_limit()), 30)
        self.assertEqual(cache.get("rl:test:factor"), 0.5)

    def test_a_burst_of_429s_adapts_once(self):
        with patch("core.ratelimit._now", return_value=T0):
            for _ in range(4):
                record_throttle(_limit(), _response())
        self.assertEqual(cache.get("rl:test:factor"), 0.5)

    def test_a_reason_naming_the_hour_waits_for_the_next_hour(self):
        with patch("core.ratelimit._now", return_value=T0 + 600):
            wait = record_throttle(
                _limit(), _response(body={"error": True, "reason": "Hourly API request limit exceeded"})
            )
        self.assertEqual(wait, 3000)

    def test_consecutive_429s_back_off_exponentially(self):
        waits = []
        for i in range(3):
            with patch("core.ratelimit._now", return_value=T0 + i * 1000):
                waits.append(record_throttle(_limit(), _response()))
            cache.delete("rl:test:cooldown")  # the cache expires by the real clock, not the patched one
        self.assertEqual(waits, [60, 120, 240])

    def test_the_reduced_rate_limits_calls_and_recovers_when_quiet(self):
        cache.set("rl:test:factor", 0.5)
        cache.set("rl:test:strikes", 1)  # a 429 not long ago: no recovery yet
        with patch("core.ratelimit._now", return_value=T0 + 15):
            self.assertEqual([acquire(_limit()) for _ in range(2)], [0.0, 45.0])  # 3 * 0.5
        cache.delete("rl:test:strikes")
        with patch("core.ratelimit._now", return_value=T0 + 75):
            acquire(_limit())
        self.assertEqual(cache.get("rl:test:factor"), 0.6)
        with patch("core.ratelimit._now", return_value=T0 + 80):
            acquire(_limit())
        self.assertEqual(cache.get("rl:test:factor"), 0.6, "one step per minute")

    def test_a_429_does_not_lock_a_half_spent_hour(self):
        limit = Limit("test", ((60, 10), (3600, 10)), fail_open=True)
        with patch("core.ratelimit._now", return_value=T0):
            acquire(limit, 6)
            record_throttle(limit, _response())
        cache.delete("rl:test:cooldown")
        with patch("core.ratelimit._now", return_value=T0 + 60):
            self.assertEqual(acquire(limit, 1), 0.0)

    def test_throttled_is_not_something_the_fallback_paths_catch(self):
        self.assertFalse(issubclass(ProviderThrottled, (httpx.HTTPError, KeyError, ValueError)))


@override_settings(CACHES=LOCMEM_CACHE)
class GridGateTests(TestCase):
    def setUp(self):
        cache.clear()

    def _get(self, **kw):
        return async_to_sync(grid.get_or_fetch_forecast_cell)(47.0, 9.0, DAY, 2, **kw)

    def test_a_429_raises_instead_of_spending_an_owm_call(self):
        owm = AsyncMock(side_effect=AssertionError("no fallback while throttled"))
        with (
            patch.object(grid, "_fetch_open_meteo", AsyncMock(side_effect=_status_error())),
            patch.object(grid, "_fetch_owm", owm),
            self.assertRaises(ProviderThrottled),
        ):
            self._get(allow_fallback=False)
        self.assertGreater(cooldown_left(grid.open_meteo_limit()), 0)

    def test_a_cooldown_skips_open_meteo_entirely(self):
        record_throttle(grid.open_meteo_limit(), _response())
        with (
            patch.object(grid, "_fetch_open_meteo", AsyncMock(side_effect=AssertionError("in cooldown"))),
            self.assertRaises(ProviderThrottled),
        ):
            self._get(allow_fallback=False)

    def test_other_open_meteo_failures_still_fall_back(self):
        owm = AsyncMock(return_value={"hourly": [{"dt": 0}]})
        with (
            patch.dict(os.environ, {"OPENWEATHERMAP_API_KEY": "k"}),
            patch.object(grid, "_fetch_open_meteo", AsyncMock(side_effect=_status_error(503))),
            patch.object(grid, "_fetch_owm", owm),
        ):
            cell = self._get(allow_fallback=False)
        self.assertEqual(cell.source, "openweathermap")

    def test_an_exhausted_owm_budget_makes_no_call(self):
        with (
            patch.dict(os.environ, {"OPENWEATHERMAP_API_KEY": "k", "OPENWEATHERMAP_DAILY_CAP": "0"}),
            patch.object(grid, "_fetch_open_meteo", AsyncMock(side_effect=_status_error())),
            patch.object(grid, "_fetch_owm", AsyncMock(side_effect=AssertionError("over budget"))),
        ):
            self.assertIsNone(self._get(allow_fallback=True))

    def test_requests_are_weighted_as_open_meteo_counts_them(self):
        self.assertEqual(grid.open_meteo_weight(20, 7), 2.0)
        self.assertAlmostEqual(grid.open_meteo_weight(20, 16), 2 * 16 / 14)
        self.assertEqual(grid.open_meteo_weight(5, 2, models=3), 1.5)
        self.assertEqual(grid.open_meteo_weight(3, 1), 1.0)

    def test_the_budget_follows_the_env(self):
        with patch.dict(os.environ, {"OPEN_METEO_LIMIT_MINUTE": "10"}):
            self.assertEqual(grid.open_meteo_limit().windows[0], (60, 10 * grid.OPEN_METEO_MARGIN))

    def test_a_commercial_key_moves_to_the_customer_host(self):
        with patch.dict(os.environ, {"OPEN_METEO_API_KEY": "secret"}):
            url, params = grid._open_meteo_request(grid.ENSEMBLE_URL, {"a": 1})
        self.assertEqual(
            (url, params), ("https://customer-ensemble-api.open-meteo.com/v1/ensemble", {"a": 1, "apikey": "secret"})
        )

    def test_the_log_line_never_carries_the_url(self):
        error = httpx.HTTPStatusError(
            "boom",
            request=httpx.Request("GET", "https://api.test/x?apikey=secret"),
            response=httpx.Response(429),
        )
        self.assertNotIn("secret", describe_failure(error))


@override_settings(CACHES=LOCMEM_CACHE, CHANNEL_LAYERS=INMEM_CHANNELS)
class CellDeferralTests(TestCase):
    def setUp(self):
        cache.clear()
        user = User.objects.create_user(username="rider", email="rider@example.com", password="pw")
        params = {"route": "x"}
        self.job = ForecastJob.objects.create(
            key=job_key(ForecastJob.Kind.ROUTE, user.id, params),
            kind=ForecastJob.Kind.ROUTE,
            owner=user,
            params=params,
            status=ForecastJob.Status.FETCHING,
            cells_total=2,
        )

    def _run(self, task_path, fn, fetch_path, fetch, attempt=0):
        enqueue = AsyncMock()
        task = SimpleNamespace(using=Mock(return_value=SimpleNamespace(aenqueue=enqueue)))
        with (
            patch(task_path, task),
            patch(fetch_path, fetch),
            patch("core.tasks.compute_route_weather_job", SimpleNamespace(aenqueue=AsyncMock())),
        ):
            async_to_sync(fn)(47.0, 9.0, DAY.isoformat(), 2, str(self.job.id), attempt)
        self.job.refresh_from_db()
        return enqueue

    def test_a_throttled_forecast_cell_defers_without_settling(self):
        fetch = AsyncMock(side_effect=ProviderThrottled("open-meteo", 30))
        enqueue = self._run(
            "core.tasks.refresh_forecast_cell",
            _refresh_forecast_cell_async,
            "core.tasks.get_or_fetch_forecast_cell",
            fetch,
        )
        enqueue.assert_awaited_once_with(47.0, 9.0, DAY.isoformat(), 2, str(self.job.id), attempt=1)
        self.assertEqual((self.job.cells_settled, self.job.cells_failed), (0, 0))
        self.assertEqual(fetch.await_args.kwargs, {"allow_fallback": False})

    def test_the_last_attempt_allows_the_fallback_and_settles(self):
        fetch = AsyncMock(return_value=None)
        enqueue = self._run(
            "core.tasks.refresh_forecast_cell",
            _refresh_forecast_cell_async,
            "core.tasks.get_or_fetch_forecast_cell",
            fetch,
            attempt=MAX_CELL_DEFERS,
        )
        enqueue.assert_not_awaited()
        self.assertEqual(fetch.await_args.kwargs, {"allow_fallback": True})
        self.assertEqual((self.job.cells_settled, self.job.cells_failed), (1, 1))

    def test_an_hourly_cooldown_goes_straight_to_the_fallback(self):
        cell = ForecastCell(source="openweathermap")
        fetch = AsyncMock(side_effect=[ProviderThrottled("open-meteo", 3000), cell])
        enqueue = self._run(
            "core.tasks.refresh_forecast_cell",
            _refresh_forecast_cell_async,
            "core.tasks.get_or_fetch_forecast_cell",
            fetch,
        )
        enqueue.assert_not_awaited()
        self.assertEqual(fetch.await_args.kwargs, {"allow_fallback": True})
        self.assertEqual((self.job.cells_settled, self.job.cells_failed), (1, 0))

    def test_a_short_wait_is_waited_out_in_the_worker(self):
        cell = ForecastCell(source="open-meteo")
        fetch = AsyncMock(side_effect=[ProviderThrottled("open-meteo", 0.01), cell])
        enqueue = self._run(
            "core.tasks.refresh_forecast_cell",
            _refresh_forecast_cell_async,
            "core.tasks.get_or_fetch_forecast_cell",
            fetch,
        )
        enqueue.assert_not_awaited()
        self.assertEqual(fetch.await_count, 2)
        self.assertEqual(self.job.cells_settled, 1)

    def test_a_throttled_ensemble_cell_defers_then_settles_failed(self):
        fetch = AsyncMock(side_effect=ProviderThrottled("open-meteo", 30))
        enqueue = self._run(
            "core.tasks.refresh_ensemble_cell",
            _refresh_ensemble_cell_async,
            "core.tasks.get_or_fetch_ensemble_cell",
            fetch,
        )
        enqueue.assert_awaited_once()
        self.assertEqual(self.job.cells_settled, 0)

        fetch = AsyncMock(return_value=None)
        self._run(
            "core.tasks.refresh_ensemble_cell",
            _refresh_ensemble_cell_async,
            "core.tasks.get_or_fetch_ensemble_cell",
            fetch,
            attempt=MAX_CELL_DEFERS,
        )
        self.assertEqual(fetch.await_args.kwargs, {"raise_throttled": False})
        self.assertEqual((self.job.cells_settled, self.job.cells_failed), (1, 1))
