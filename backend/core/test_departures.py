from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from asgiref.sync import async_to_sync
from django.test import Client, SimpleTestCase, TestCase, override_settings

from . import departures
from . import tests as fixtures
from .departures import aggregate, candidate_times, cell_covers, comparison_view, fetch_windows, instant, local_iso
from .forecast_schemas import DepartureComparison
from .jobs import forecast_view, job_snapshot
from .models import ForecastCell, ForecastJob
from .ride_quality import RideQualityConfig
from .schedule import LOCAL_TZ
from .tasks import _assemble_forecast_job_async, _compute_route_weather_job_async, _plan_forecast_job_async


def sample(elapsed=0, rain=0, temp=18, power=0):
    return {"elapsed_s": elapsed, "rain_rate_mm_h": rain, "temp": temp, "wind_power_w": power}


def stored_comparison(rains=(2, 0, 2)):
    times = candidate_times(
        {"departure_time": "2030-06-01T08:00", "departure_flex_before_minutes": 15, "departure_flex_after_minutes": 15}
    )
    return {
        "requested_time": local_iso(times[1]),
        "window_start": local_iso(times[0]),
        "window_end": local_iso(times[-1]),
        "candidates": [
            {
                "departure_time": local_iso(t),
                "arrival_time": local_iso(t + timedelta(hours=1)),
                "complete": True,
                "samples": [sample(0, rain), sample(3600, rain)],
            }
            for t, rain in zip(times, rains)
        ],
    }


class DepartureRankingTests(SimpleTestCase):
    def test_asymmetric_window_and_requested_time(self):
        times = candidate_times(
            {
                "departure_time": "2030-06-01T08:07",
                "departure_flex_before_minutes": 30,
                "departure_flex_after_minutes": 60,
            }
        )
        self.assertEqual(len(times), 7)
        self.assertEqual(local_iso(times[0]), "2030-06-01T07:37:00+02:00")
        self.assertEqual(local_iso(times[-1]), "2030-06-01T09:07:00+02:00")
        self.assertEqual(len(candidate_times({"departure_time": "2030-06-01T08:07"})), 1)
        self.assertEqual(
            len(
                candidate_times(
                    {
                        "departure_time": "2030-06-01T08:07",
                        "departure_flex_before_minutes": 120,
                        "departure_flex_after_minutes": 120,
                    }
                )
            ),
            17,
        )

    def test_window_validation(self):
        for value in (-15, 7, 135):
            with self.subTest(value=value), self.assertRaises(ValueError):
                departures.check_flexibility(value)

    def test_daylight_saving_uses_real_instants(self):
        spring = candidate_times({"departure_time": "2030-03-31T01:45", "departure_flex_after_minutes": 30})
        self.assertEqual([t.astimezone(LOCAL_TZ).hour for t in spring], [1, 3, 3])
        fall = candidate_times({"departure_time": "2030-10-27T02:45:00+02:00", "departure_flex_after_minutes": 30})
        self.assertEqual(local_iso(fall[1]), "2030-10-27T02:00:00+01:00")
        with self.assertRaises(ValueError):
            instant("2030-03-31T02:30")

    def test_window_covers_both_dates_and_latest_arrival(self):
        windows = fetch_windows(
            {"departure_time": "2030-06-01T23:45", "departure_flex_after_minutes": 30},
            [{"elapsed_s": 86400}],
            datetime(2030, 6, 1, tzinfo=UTC).date(),
        )
        self.assertEqual(windows, [("2030-06-01", 4), ("2030-06-02", 4)])

    def test_duration_weighting_and_worst_stretch(self):
        self.assertAlmostEqual(aggregate([0, 1, 0], [{"elapsed_s": x} for x in (0, 60, 3600)]), 0.625)
        # A short bad stretch matters, but not as much as sustained bad weather.
        short = aggregate([1, 0, 0], [{"elapsed_s": x} for x in (0, 60, 3600)])
        long = aggregate([1, 0, 0], [{"elapsed_s": x} for x in (0, 3540, 3600)])
        self.assertGreater(short, 0.25)
        self.assertLess(short, long)
        self.assertEqual(aggregate([0.4], [{"elapsed_s": 0}]), 0.4)

    def test_rank_whole_ride_not_departure_weather(self):
        stored = stored_comparison((0, 0, 0))
        stored["candidates"][1]["samples"][1]["rain_rate_mm_h"] = 5
        result = comparison_view(stored)
        self.assertEqual(result["recommended_time"], stored["window_start"])
        self.assertIn("Weniger Regen", result["explanation"])
        DepartureComparison.model_validate(result)

    def test_equivalent_conditions_prefer_requested_departure(self):
        stored = stored_comparison((0, 0.01, 0))
        result = comparison_view(stored)
        self.assertEqual(result["recommended_time"], stored["requested_time"])
        self.assertIn("Ähnliche", result["explanation"])

    def test_missing_weather_is_never_good_weather(self):
        stored = stored_comparison((3, 0, 3))
        stored["candidates"][1]["complete"] = False
        result = comparison_view(stored)
        self.assertEqual(result["recommended_time"], stored["window_start"])
        self.assertIsNone(result["candidates"][1]["ride_score"])
        for candidate in stored["candidates"]:
            candidate["samples"] = []
        self.assertIsNone(comparison_view(stored)["recommended_time"])

    def test_past_candidates_expire_when_served(self):
        stored = stored_comparison((0, 0, 0))
        now = instant(stored["requested_time"]) + timedelta(seconds=1)
        self.assertEqual(comparison_view(stored, now)["recommended_time"], stored["window_end"])
        self.assertIsNone(comparison_view(stored, now + timedelta(days=1))["recommended_time"])

    def test_ranking_uses_current_scoring_configuration(self):
        stored = stored_comparison((0, 0, 0))
        stored["candidates"][0]["samples"] = [sample(0, rain=4), sample(3600, rain=4)]
        stored["candidates"][1]["samples"] = [sample(0, power=230), sample(3600, power=230)]
        stored["candidates"][2]["complete"] = False
        with patch("core.ride_quality.RIDE_QUALITY", RideQualityConfig(weights={"rain": 1})):
            self.assertEqual(comparison_view(stored)["recommended_time"], stored["requested_time"])
        with patch("core.ride_quality.RIDE_QUALITY", RideQualityConfig(weights={"wind": 1})):
            self.assertEqual(comparison_view(stored)["recommended_time"], stored["window_start"])

    def test_http_and_websocket_view_hides_stored_inputs(self):
        stored = stored_comparison()
        job = SimpleNamespace(
            id="12556618-a41e-4a11-b014-04501f6c1761",
            status="done",
            cells_settled=0,
            cells_total=0,
            error="",
            updated_at=datetime.now(UTC),
            result={**fixtures._finished_payload(), "departure_inputs": stored},
        )
        view = forecast_view(job)
        self.assertNotIn("departure_inputs", view)
        self.assertNotIn("samples", view["departure_comparison"]["candidates"][0])
        self.assertEqual(job_snapshot(job)["result"], view)

    def test_provider_coverage_and_missing_precipitation(self):
        eta = instant("2030-06-01T08:00")
        hourly = {"time": ["2030-06-01T07:00", "2030-06-01T09:00"], "temperature_2m": [18, 18], "precipitation": [0, 0]}
        self.assertTrue(cell_covers({"hourly": hourly}, eta, "open-meteo"))
        self.assertFalse(cell_covers({"hourly": hourly}, eta + timedelta(hours=2), "open-meteo"))
        del hourly["precipitation"]
        self.assertFalse(cell_covers({"hourly": hourly}, eta, "open-meteo"))
        owm = {"hourly": [{"dt": int((eta + timedelta(hours=i)).timestamp()), "temp": 18} for i in (-1, 1)]}
        self.assertTrue(cell_covers(owm, eta, "openweathermap"))
        self.assertFalse(cell_covers(owm, eta + timedelta(hours=2), "openweathermap"))


@override_settings(CACHES=fixtures.LOCMEM_CACHE, CHANNEL_LAYERS=fixtures.INMEM_CHANNELS)
class DepartureJobTests(TestCase):
    setUp = fixtures.ForecastJobTests.setUp
    _job_params = fixtures.ForecastJobTests._job_params
    _make_job = fixtures.ForecastJobTests._make_job

    def test_flexible_route_still_builds_geometry(self):
        from .tasks import _refresh_route_geometry_async

        self.route.departure_flex_after_minutes = 60
        self.route.save()
        route = {"paths": [{"points": {"coordinates": [[9, 47], [9.01, 47.01]]},
                            "time": 600_000, "distance": 1500}]}
        with patch("core.weather._fetch_route", AsyncMock(return_value=route)), patch(
            "core.tasks.refresh_route_thumbnail", SimpleNamespace(aenqueue=AsyncMock())
        ):
            async_to_sync(_refresh_route_geometry_async)(str(self.route.id))
        self.route.refresh_from_db()
        self.assertEqual(self.route.total_seconds, 600)
        self.assertEqual(self.route.vertex_times, [0, 600])
        self.assertEqual(self.route.departure_flex_after_minutes, 60)

    def test_crud_persists_window_without_rebuilding_geometry(self):
        client = Client()
        client.force_login(self.user)
        route = client.get(f"/api/routes/{self.route.id}").json()
        route.update(departure_flex_before_minutes=15, departure_flex_after_minutes=90)
        with patch(
            "core.api.recurring_route.refresh_route_geometry", SimpleNamespace(aenqueue=AsyncMock())
        ) as geometry:
            response = client.put(f"/api/routes/{self.route.id}", route, content_type="application/json")
            self.assertEqual(response.status_code, 200, response.content)
            geometry.aenqueue.assert_not_awaited()
        self.route.refresh_from_db()
        self.assertEqual(self.route.departure_flex_before_minutes, 15)
        self.assertEqual(self.route.departure_flex_after_minutes, 90)
        self.assertEqual(self.route.schedule_cron, "0 8 * * *")
        route["departure_flex_before_minutes"] = 121
        self.assertEqual(
            client.put(f"/api/routes/{self.route.id}", route, content_type="application/json").status_code, 422
        )

    def test_midnight_window_and_prewarm_cover_both_dates(self):
        from .tasks import _scan_route_forecasts_async

        dep = self.departure.replace(hour=23, minute=45)
        self.route.departure_flex_after_minutes = 30
        self.route.save()
        job = self._make_job(
            params={**self._job_params(), "departure_time": dep.isoformat(), "departure_flex_after_minutes": 30}
        )
        forecast, ensemble = AsyncMock(), AsyncMock()
        with (
            patch("core.tasks.refresh_forecast_cell", SimpleNamespace(aenqueue=forecast)),
            patch("core.tasks.refresh_ensemble_cell", SimpleNamespace(aenqueue=ensemble)),
        ):
            async_to_sync(_plan_forecast_job_async)(str(job.id))
        self.assertEqual(forecast.await_count, 4)
        self.assertEqual(ensemble.await_count, 4)
        self.assertEqual(
            {call.args[2] for call in forecast.await_args_list},
            {dep.date().isoformat(), (dep + timedelta(days=1)).date().isoformat()},
        )
        fixtures.cache.clear()
        forecast.reset_mock()
        ensemble.reset_mock()
        with (
            patch("core.tasks.upcoming_departures", return_value=[dep]),
            patch("core.tasks.refresh_forecast_cell", SimpleNamespace(aenqueue=forecast)),
            patch("core.tasks.refresh_ensemble_cell", SimpleNamespace(aenqueue=ensemble)),
            patch("core.tasks.refresh_route_thumbnail") as thumbnail,
        ):
            thumbnail.using.return_value.aenqueue = AsyncMock()
            async_to_sync(_scan_route_forecasts_async)(str(self.route.id))
        self.assertEqual(forecast.await_count, 4)
        self.assertEqual(ensemble.await_count, 4)

    def test_saved_defaults_and_explicit_zero_override(self):
        self.route.departure_flex_before_minutes = 30
        self.route.departure_flex_after_minutes = 60
        self.route.save()
        with patch("core.tasks.plan_forecast_job", SimpleNamespace(aenqueue=AsyncMock())):
            response = self.client.get(
                f"/api/routes/{self.route.id}/forecast", {"date": self.departure.date().isoformat(), "time": "08:00"}
            )
            job = ForecastJob.objects.get(id=response.json()["job_id"])
            self.assertEqual(job.params["departure_flex_before_minutes"], 30)
            response = self.client.get(
                f"/api/routes/{self.route.id}/forecast",
                {
                    "date": self.departure.date().isoformat(),
                    "time": "08:00",
                    "departure_flex_before_minutes": 0,
                    "departure_flex_after_minutes": 0,
                },
            )
            fixed = ForecastJob.objects.get(id=response.json()["job_id"])
            self.assertNotEqual(job.key, fixed.key)
            self.assertFalse(departures.enabled(fixed.params))

    def test_invalid_window_is_422_before_work_is_queued(self):
        with patch("core.tasks.plan_forecast_job", SimpleNamespace(aenqueue=AsyncMock())) as planner:
            response = self.client.get(
                f"/api/routes/{self.route.id}/forecast",
                {
                    "date": self.departure.date().isoformat(),
                    "time": "08:00",
                    "departure_flex_after_minutes": 7,
                },
            )
            self.assertEqual(response.status_code, 422)
            planner.aenqueue.assert_not_awaited()

    def test_window_plans_cells_once_per_date_not_once_per_candidate(self):
        job = self._make_job(params={**self._job_params(), "departure_flex_after_minutes": 120})
        forecast, ensemble = AsyncMock(), AsyncMock()
        with (
            patch("core.tasks.refresh_forecast_cell", SimpleNamespace(aenqueue=forecast)),
            patch("core.tasks.refresh_ensemble_cell", SimpleNamespace(aenqueue=ensemble)),
        ):
            async_to_sync(_plan_forecast_job_async)(str(job.id))
        self.assertEqual(forecast.await_count, 2)
        self.assertEqual(ensemble.await_count, 2)
        job.refresh_from_db()
        self.assertEqual(job.cells_total, 4)

    def test_real_window_computation_reads_each_cell_once(self):
        departure = self.departure.replace(hour=8, minute=0, second=0, microsecond=0)
        times = [(departure + timedelta(hours=i)).replace(tzinfo=None).isoformat() for i in range(-2, 5)]
        for lat, lon in ((47, 9), (47.01, 9.01)):
            ForecastCell.objects.create(
                lat_r=lat,
                lon_r=lon,
                day_key=departure.date(),
                source="open-meteo",
                forecast_days=16,
                data={
                    "hourly": {
                        "time": times,
                        "temperature_2m": [18] * 7,
                        "precipitation": [0] * 7,
                        "wind_speed_10m": [12] * 7,
                        "wind_direction_10m": [90] * 7,
                    }
                },
            )
        geometry = {
            "polyline": self.route.polyline_coordinates,
            "sample_points": self.sample_points,
            "vertex_times": [0, 300, 600],
            "total_seconds": 600,
            "total_distance_m": 1500,
        }
        job = self._make_job(
            status=ForecastJob.Status.ASSEMBLING,
            geometry=geometry,
            params={**self._job_params(), "departure_flex_after_minutes": 60},
        )
        from .weather import get_cached_forecast_cell

        with (
            patch("core.weather.get_cached_forecast_cell", wraps=get_cached_forecast_cell) as cached,
            patch("core.weather.get_or_fetch_forecast_cell", side_effect=AssertionError("provider fetch")),
            patch("core.tasks.assemble_forecast_job", SimpleNamespace(enqueue=Mock())),
        ):
            async_to_sync(_compute_route_weather_job_async)(str(job.id))
        self.assertEqual(cached.call_count, 2)
        async_to_sync(_assemble_forecast_job_async)(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.status, ForecastJob.Status.DONE, job.error)
        result = forecast_view(job)["departure_comparison"]
        self.assertEqual(len(result["candidates"]), 5)
        self.assertTrue(all(c["available"] for c in result["candidates"]))
        self.assertEqual(result["recommended_time"], result["requested_time"])
