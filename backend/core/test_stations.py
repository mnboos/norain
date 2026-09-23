"""Weather Underground station correction: parsing, budget, fetching, and its use in forecasts."""

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from core import stations
from core.entitlements import entitlements_for_sync
from core.jobs import STATION_JOB_LIFETIME, get_or_start_job, job_key
from core.models import (
    ForecastCell,
    ForecastJob,
    Plan,
    RecurringRoute,
    StationLookup,
    StationObservation,
    Subscription,
    route_line,
    route_point,
)
from core.stations import (
    MAX_STATIONS_PER_JOB,
    Reading,
    lead_weight,
    parse_nearby,
    parse_observation,
    pick_stations,
    ride_in_window,
    station_correction,
)
from core.tasks import (
    _assemble_forecast_job_async,
    _compute_route_weather_job_async,
    _plan_forecast_job_async,
    _refresh_station_observations_async,
)
from core.thumbnails import compute_route_thumbnail
from core.weather import compute_route_weather

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
INMEM_CHANNELS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
ZURICH = ZoneInfo("Europe/Zurich")
WITH_KEY = {"WEATHERUNDERGROUND_API_KEY": "test-key"}
WITHOUT_KEY = {"WEATHERUNDERGROUND_API_KEY": ""}
T0 = datetime(2026, 9, 12, 8, 0, tzinfo=UTC)


def _reading(temp=None, rate=None, station_id="S", observed_at=T0):
    return Reading(station_id, observed_at, temp, rate)


def _no_network():
    """Every accessor that could spend a request, patched where the code under test binds it."""
    boom = AsyncMock(side_effect=AssertionError("fetched!"))
    return (
        patch("core.weather.get_or_fetch_forecast_cell", boom),
        patch("core.weather.get_or_fetch_ensemble_cell", boom),
        patch("core.stations._fetch_nearby", boom),
        patch("core.stations._fetch_observation", boom),
    )


class ParseTests(SimpleTestCase):
    def test_nearby_parallel_arrays(self):
        data = {
            "location": {
                "stationId": ["A", "B", "C"],
                "latitude": [47.0, None, 47.2],
                "longitude": [9.0, 9.1, 9.2],
                "qcStatus": [1, -1, 0],
                "updateTimeUtc": [1776811136, 1776821504, 1755981297],
            }
        }
        parsed = parse_nearby(data)
        # B has no latitude and is dropped rather than placed at 0°.
        self.assertEqual([s["id"] for s in parsed], ["A", "C"])
        self.assertEqual(parsed[1]["qc"], 0)

    def test_nearby_empty_or_malformed(self):
        self.assertEqual(parse_nearby({}), [])
        self.assertEqual(parse_nearby({"location": "nope"}), [])

    def test_observation(self):
        data = {
            "observations": [{
                "stationID": "A", "epoch": 1789200000, "lat": 47.0, "lon": 9.0, "qcStatus": 1,
                "metric": {"temp": 17.3, "precipRate": 0.0},
            }]
        }
        obs = parse_observation(data)
        self.assertEqual(obs["temp"], 17.3)
        self.assertEqual(obs["precip_rate"], 0.0)
        self.assertEqual(obs["observed_at"], datetime.fromtimestamp(1789200000, tz=UTC))

    def test_observation_missing_values_stay_missing(self):
        base = {"stationID": "A", "epoch": 1789200000, "lat": 47.0, "lon": 9.0}
        # A missing temperature must not become 0 °C.
        only_rain = parse_observation({"observations": [{**base, "metric": {"precipRate": 1.2}}]})
        self.assertIsNone(only_rain["temp"])
        self.assertIsNone(parse_observation({"observations": [{**base, "metric": {}}]}))
        self.assertIsNone(parse_observation({"observations": [{**base, "metric": {"temp": 3}, "epoch": None}]}))
        self.assertIsNone(parse_observation({"observations": []}))


class CorrectionTests(SimpleTestCase):
    def test_lead_weight_fades_linearly(self):
        horizon = timedelta(hours=2)
        self.assertEqual(lead_weight(T0, T0, horizon), 1.0)
        self.assertAlmostEqual(lead_weight(T0 + timedelta(hours=1), T0, horizon), 0.5)
        self.assertAlmostEqual(lead_weight(T0 - timedelta(hours=1), T0, horizon), 0.5)
        self.assertEqual(lead_weight(T0 + timedelta(hours=3), T0, horizon), 0.0)

    def test_naive_eta_is_swiss_local_time(self):
        naive = T0.astimezone(ZURICH).replace(tzinfo=None)
        self.assertEqual(lead_weight(naive, T0, timedelta(hours=2)), 1.0)

    def test_temperature_offset_drops_sunny_outlier(self):
        readings = [_reading(18.0), _reading(18.5), _reading(26.0)]
        correction = station_correction(readings, model_temp_now=15.0)
        self.assertAlmostEqual(correction.temp_offset, 3.25)
        self.assertEqual(correction.station_count, 3)

    def test_temperature_offset_is_clamped(self):
        correction = station_correction([_reading(30.0), _reading(30.0)], model_temp_now=15.0)
        self.assertEqual(correction.temp_offset, stations.MAX_TEMP_OFFSET_C)

    def test_one_station_is_not_enough(self):
        self.assertIsNone(station_correction([_reading(20.0, 2.0)], model_temp_now=15.0))

    def test_no_model_temperature_means_no_offset(self):
        correction = station_correction([_reading(20.0, 0.0), _reading(20.0, 0.0)], model_temp_now=None)
        self.assertIsNone(correction.temp_offset)
        self.assertEqual(correction.wet_share, 0.0)

    def test_wet_share_and_rate(self):
        readings = [_reading(rate=0.0), _reading(rate=1.0), _reading(rate=3.0), _reading(rate=None)]
        correction = station_correction(readings, model_temp_now=None)
        self.assertAlmostEqual(correction.wet_share, 2 / 3)
        self.assertEqual(correction.wet_rate_mm_h, 2.0)

    def test_ride_in_window(self):
        self.assertTrue(ride_in_window(T0 + timedelta(minutes=90), 600, T0))
        self.assertFalse(ride_in_window(T0 + timedelta(hours=3), 600, T0))
        # Started three hours ago, still riding: the end is near now.
        self.assertTrue(ride_in_window(T0 - timedelta(hours=3), 3 * 3600, T0))
        self.assertFalse(ride_in_window(T0 - timedelta(hours=5), 600, T0))

    def test_pick_stations_serves_every_point_and_respects_the_cap(self):
        points = [(47.0, 9.0), (47.3, 9.0)]  # ~33 km apart
        near_first = [{"id": f"A{i}", "lat": 47.0 + i * 0.001, "lon": 9.0} for i in range(5)]
        near_second = [{"id": f"B{i}", "lat": 47.3 + i * 0.001, "lon": 9.0} for i in range(5)]
        far = [{"id": "F", "lat": 48.0, "lon": 9.0}]
        picked = pick_stations(points, near_first + near_second + far)
        self.assertEqual(sorted(picked), ["A0", "A1", "B0", "B1"])

        many = [(47.0 + i * 0.1, 9.0) for i in range(10)]
        candidates = [{"id": f"S{i}{j}", "lat": 47.0 + i * 0.1, "lon": 9.0} for i in range(10) for j in range(2)]
        self.assertEqual(len(pick_stations(many, candidates)), MAX_STATIONS_PER_JOB)


@override_settings(CACHES=LOCMEM_CACHE)
class BudgetTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_minute_cap(self):
        with patch.object(stations, "MINUTE_CAP", 2):
            self.assertEqual([stations._spend_call() for _ in range(3)], [True, True, False])

    def test_day_cap(self):
        with patch.object(stations, "DAILY_CAP", 1):
            self.assertEqual([stations._spend_call() for _ in range(2)], [True, False])

    def test_fails_closed_without_the_cache(self):
        with patch("core.ratelimit.cache.incr", side_effect=ConnectionError("redis down")):
            self.assertFalse(stations._spend_call())

    def test_blocked_after_429(self):
        response = Mock(status_code=429)
        with patch.dict(os.environ, WITH_KEY), patch("core.stations.httpx.AsyncClient") as client:
            client.return_value.__aenter__.return_value.get = AsyncMock(return_value=response)
            self.assertIsNone(async_to_sync(stations._fetch_nearby)(47.0, 9.0))
        self.assertFalse(stations._spend_call())

    def test_no_key_makes_no_call(self):
        with patch.dict(os.environ, WITHOUT_KEY), patch("core.stations.httpx.AsyncClient") as client:
            self.assertIsNone(async_to_sync(stations._fetch_observation)("A"))
        client.assert_not_called()

    def test_204_means_no_stations(self):
        response = Mock(status_code=204)
        with patch.dict(os.environ, WITH_KEY), patch("core.stations.httpx.AsyncClient") as client:
            client.return_value.__aenter__.return_value.get = AsyncMock(return_value=response)
            self.assertEqual(async_to_sync(stations._fetch_nearby)(47.0, 9.0), [])


def _now_local_minute() -> datetime:
    return datetime.now(tz=ZURICH).replace(second=0, microsecond=0)


class _NearNowRoute:
    """A three-sample ride starting now, with warm cells and two station readings."""

    def build(self, *, cell_source="open-meteo", pop=None):
        self.departure = _now_local_minute()
        self.departure_time = self.departure.replace(tzinfo=None).isoformat()
        self.observed_at = self.departure.astimezone(UTC)
        self.sample_points = [
            {"lat": 47.000, "lon": 9.000, "lat_r": 47.0, "lon_r": 9.0, "elapsed_s": 0, "idx": 0},
            {"lat": 47.005, "lon": 9.000, "lat_r": 47.01, "lon_r": 9.0, "elapsed_s": 3600, "idx": 1},
            {"lat": 47.010, "lon": 9.000, "lat_r": 47.01, "lon_r": 9.0, "elapsed_s": 3 * 3600, "idx": 2},
        ]
        self.polyline = [[sp["lon"], sp["lat"]] for sp in self.sample_points]
        start = self.departure.replace(tzinfo=None, minute=0) - timedelta(hours=6)
        hours = [start + timedelta(hours=h) for h in range(36)]
        for lat_r, lon_r in ((47.0, 9.0), (47.01, 9.0)):
            if cell_source == "open-meteo":
                data = {"hourly": {
                    "time": [h.isoformat(timespec="minutes") for h in hours],
                    "temperature_2m": [15.0] * len(hours),
                    "precipitation": [0.0] * len(hours),
                    "wind_speed_10m": [10.0] * len(hours),
                    "wind_direction_10m": [180.0] * len(hours),
                }}
            else:
                data = {"hourly": [
                    {"dt": int(h.replace(tzinfo=ZURICH).timestamp()), "temp": 15.0, "wind_speed": 3.0,
                     "wind_deg": 180, "pop": pop}
                    for h in hours
                ]}
            ForecastCell.objects.create(
                lat_r=lat_r, lon_r=lon_r, day_key=self.departure.date(), source=cell_source,
                forecast_days=16, data=data,
            )

    def add_readings(self, temps=(18.0, 18.5), rates=(0.0, 0.0)):
        for i, (temp, rate) in enumerate(zip(temps, rates, strict=True)):
            StationObservation.objects.create(
                station_id=f"IZH{i}", lat=47.002 + i * 0.001, lon=9.001, observed_at=self.observed_at,
                temp=temp, precip_rate=rate, qc_status=1,
            )

    def compute(self, enabled=True, include_uncertainty=False):
        patches = _no_network()
        with patches[0], patches[1], patches[2], patches[3]:
            return async_to_sync(compute_route_weather)(
                start_lat=47.0, start_lon=9.0, dest_lat=47.01, dest_lon=9.0, profile="bike",
                departure_time=self.departure_time, sample_points=self.sample_points, polyline=self.polyline,
                total_seconds=3 * 3600, total_distance_m=1100.0, cache_only=True,
                include_uncertainty=include_uncertainty, station_correction_enabled=enabled,
            )


class ComputeRouteWeatherStationTests(_NearNowRoute, TestCase):
    def setUp(self):
        self.build()
        self.add_readings()

    def test_temperature_is_corrected_and_fades(self):
        forecast = self.compute()
        temps = [s.temp for s in forecast.samples]
        self.assertAlmostEqual(temps[0], 18.2, places=1)  # 15 + 1.0 * 3.25
        self.assertAlmostEqual(temps[1], 16.6, places=1)  # 15 + 0.5 * 3.25
        self.assertEqual(temps[2], 15.0)  # three hours out: the plain model
        self.assertEqual([s.station_count for s in forecast.samples], [2, 2, None])
        self.assertTrue(forecast.summary.station_corrected)

    def test_disabled_leaves_the_model_alone(self):
        forecast = self.compute(enabled=False)
        self.assertEqual([s.temp for s in forecast.samples], [15.0, 15.0, 15.0])
        self.assertEqual([s.station_count for s in forecast.samples], [None, None, None])
        self.assertFalse(forecast.summary.station_corrected)

    def test_old_readings_are_ignored(self):
        StationObservation.objects.update(observed_at=self.observed_at - timedelta(hours=1))
        forecast = self.compute()
        self.assertEqual([s.temp for s in forecast.samples], [15.0, 15.0, 15.0])

    def test_unchecked_stations_are_used(self):
        """Only stations flagged as possibly wrong are skipped; a missing flag is not a bad one."""
        StationObservation.objects.update(qc_status=None)
        forecast = self.compute()
        self.assertAlmostEqual(forecast.samples[0].temp, 18.2, places=1)

    def test_distrusted_station_is_ignored(self):
        StationObservation.objects.filter(station_id="IZH1").update(qc_status=0)
        forecast = self.compute()
        self.assertEqual(forecast.samples[0].temp, 15.0)  # one station left is not enough


class ThumbnailStationTests(_NearNowRoute, TestCase):
    """The list label reads the same numbers as the map, and never fetches to do it."""

    def setUp(self):
        self.build()
        self.add_readings()
        self.user = get_user_model().objects.create_user(
            username="rider", email="rider@example.com", password="pw"
        )
        self.route = RecurringRoute.objects.create(
            owner=self.user, name="Commute", start_point=route_point(47.0, 9.0), start_name="Start",
            destination_point=route_point(47.01, 9.0), dest_name="Destination", schedule_cron="0 8 * * *",
            schedule_description="Daily", polyline=route_line(self.polyline), sample_points=self.sample_points,
            total_seconds=3 * 3600, total_distance_m=1100.0,
        )

    def thumbnail_temps(self):
        patches = _no_network()
        with patches[0], patches[1], patches[2], patches[3], patch(
            "core.thumbnails.next_departure", return_value=self.departure.replace(tzinfo=None)
        ):
            thumbnail = async_to_sync(compute_route_thumbnail)(self.route)
        return [sample["temp"] for sample in thumbnail["samples"]]

    def test_free_owner_gets_the_model(self):
        self.assertEqual(self.thumbnail_temps(), [15.0, 15.0, 15.0])

    def test_pro_owner_gets_the_corrected_temperature(self):
        Subscription.objects.create(user=self.user, plan=Plan.PRO, status="active")
        temps = self.thumbnail_temps()
        self.assertAlmostEqual(temps[0], 18.2, places=1)
        self.assertEqual(temps[2], 15.0)


class RainPresenceTests(_NearNowRoute, TestCase):
    def setUp(self):
        self.build(cell_source="openweathermap", pop=0.0)

    def test_measured_rain_raises_the_probability(self):
        self.add_readings(temps=(15.0, 15.0), rates=(1.5, 2.5))
        forecast = self.compute()
        self.assertEqual(forecast.samples[0].pop, 1.0)
        self.assertEqual(forecast.samples[0].rain_if_wet, 2.0)
        # One hour out the rain weight has faded to zero.
        self.assertEqual(forecast.samples[1].pop, 0.0)
        self.assertTrue(forecast.summary.will_rain)

    def test_dry_stations_do_not_invent_rain(self):
        self.add_readings(temps=(15.0, 15.0), rates=(0.0, 0.0))
        forecast = self.compute()
        self.assertEqual(forecast.samples[0].pop, 0.0)
        self.assertIsNone(forecast.samples[0].rain_if_wet)


@override_settings(CACHES=LOCMEM_CACHE, CHANNEL_LAYERS=INMEM_CHANNELS)
class StationJobTests(_NearNowRoute, TestCase):
    def setUp(self):
        cache.clear()
        self.build()
        self.user = get_user_model().objects.create_user(
            username="rider", email="rider@example.com", password="pw"
        )

    def make_pro(self):
        Subscription.objects.update_or_create(user=self.user, defaults={"plan": Plan.PRO, "status": "active"})

    def make_job(self, **fields):
        params = {"start_lat": 47.0, "start_lon": 9.0, "dest_lat": 47.01, "dest_lon": 9.0, "profile": "bike",
                  "departure_time": self.departure_time}
        return ForecastJob.objects.create(
            key=job_key(ForecastJob.Kind.ADHOC, self.user.id, params), kind=ForecastJob.Kind.ADHOC,
            owner=self.user, params=params,
            geometry={"polyline": self.polyline, "sample_points": self.sample_points,
                      "total_seconds": 3 * 3600, "total_distance_m": 1100.0},
            **fields,
        )

    def plan(self, job):
        station_enqueue = AsyncMock()
        with patch("core.tasks.refresh_forecast_cell", SimpleNamespace(aenqueue=AsyncMock())), patch(
            "core.tasks.refresh_ensemble_cell", SimpleNamespace(aenqueue=AsyncMock())
        ), patch("core.tasks.refresh_station_observations", SimpleNamespace(aenqueue=station_enqueue)), patch(
            "core.tasks.compute_route_weather_job", SimpleNamespace(aenqueue=AsyncMock())
        ):
            async_to_sync(_plan_forecast_job_async)(str(job.id))
        job.refresh_from_db()
        return station_enqueue

    def test_planning_adds_the_station_task_for_pro(self):
        self.make_pro()
        job = self.make_job()
        with patch.dict(os.environ, WITH_KEY):
            station_enqueue = self.plan(job)
        station_enqueue.assert_awaited_once_with(str(job.id))
        self.assertEqual(job.cells_total, 2 * 2 + 1)

    def test_planning_skips_stations_for_free_accounts(self):
        job = self.make_job()
        with patch.dict(os.environ, WITH_KEY):
            station_enqueue = self.plan(job)
        station_enqueue.assert_not_awaited()
        self.assertEqual(job.cells_total, 4)

    def test_planning_skips_stations_without_a_key(self):
        self.make_pro()
        job = self.make_job()
        with patch.dict(os.environ, WITHOUT_KEY):
            station_enqueue = self.plan(job)
        station_enqueue.assert_not_awaited()

    def test_planning_skips_stations_for_a_ride_tomorrow(self):
        self.make_pro()
        self.departure_time = (self.departure + timedelta(days=1)).replace(tzinfo=None).isoformat()
        job = self.make_job()
        with patch.dict(os.environ, WITH_KEY):
            station_enqueue = self.plan(job)
        station_enqueue.assert_not_awaited()

    def test_station_task_uses_cached_lookups_and_fresh_readings(self):
        self.make_pro()
        job = self.make_job(status=ForecastJob.Status.FETCHING, cells_total=1)
        for lat_c, lon_c in {stations._lookup_cell(sp["lat"], sp["lon"]) for sp in self.sample_points}:
            StationLookup.objects.create(lat_c=lat_c, lon_c=lon_c, stations=[
                {"id": "FRESH", "lat": 47.001, "lon": 9.0, "qc": 1, "updated": None},
                {"id": "STALE", "lat": 47.002, "lon": 9.0, "qc": 1, "updated": None},
            ])
        StationObservation.objects.create(
            station_id="FRESH", lat=47.001, lon=9.0, observed_at=self.observed_at, temp=16
        )
        StationObservation.objects.create(
            station_id="STALE", lat=47.002, lon=9.0, observed_at=self.observed_at, temp=16
        )
        StationObservation.objects.filter(station_id="STALE").update(fetched_at=self.observed_at - timedelta(hours=1))

        fetch_observation = AsyncMock(return_value=None)
        with patch.dict(os.environ, WITH_KEY), patch(
            "core.stations._fetch_nearby", AsyncMock(side_effect=AssertionError("looked up!"))
        ), patch("core.stations._fetch_observation", fetch_observation), patch(
            "core.tasks.compute_route_weather_job", SimpleNamespace(aenqueue=AsyncMock())
        ):
            async_to_sync(_refresh_station_observations_async)(str(job.id))

        fetch_observation.assert_awaited_once_with("STALE")
        job.refresh_from_db()
        self.assertEqual(job.cells_settled, 1)
        self.assertEqual(job.cells_failed, 0)  # missing readings never shorten the forecast's life

    def test_queued_station_task_rechecks_expired_access(self):
        job = self.make_job(status=ForecastJob.Status.FETCHING, cells_total=1)
        with patch("core.tasks.refresh_stations_for_ride", new_callable=AsyncMock) as fetch, patch(
            "core.tasks.compute_route_weather_job", SimpleNamespace(aenqueue=AsyncMock())
        ):
            async_to_sync(_refresh_station_observations_async)(str(job.id))
        fetch.assert_not_awaited()
        job.refresh_from_db()
        self.assertEqual(job.cells_settled, 1)

    def test_station_task_failure_still_settles(self):
        """The task fails loudly, but the job it belongs to still goes on to assembly."""
        self.make_pro()
        job = self.make_job(status=ForecastJob.Status.FETCHING, cells_total=1)
        assemble = AsyncMock()
        with patch.dict(os.environ, WITH_KEY), patch(
            "core.tasks.compute_route_weather_job", SimpleNamespace(aenqueue=assemble)
        ), patch(
            "core.tasks.refresh_stations_for_ride", AsyncMock(side_effect=RuntimeError("boom"))
        ), self.assertRaises(RuntimeError):
            async_to_sync(_refresh_station_observations_async)(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.cells_settled, 1)
        assemble.assert_awaited_once()

    def assemble(self):
        job = self.make_job(status=ForecastJob.Status.ASSEMBLING)
        patches = _no_network()
        with patches[0], patches[1], patches[2], patches[3]:
            async_to_sync(_compute_route_weather_job_async)(str(job.id))
            async_to_sync(_assemble_forecast_job_async)(str(job.id))
        job.refresh_from_db()
        return job

    def test_assembly_corrects_only_for_pro(self):
        self.add_readings()
        free = self.assemble()
        self.assertEqual(free.result["samples"][0]["temp"], 15.0)
        self.assertIsNone(free.result["samples"][0]["station_count"])
        self.assertFalse(free.result["summary"]["station_corrected"])

        free.delete()
        self.make_pro()
        pro = self.assemble()
        self.assertAlmostEqual(pro.result["samples"][0]["temp"], 18.2, places=1)
        self.assertEqual(pro.result["samples"][0]["station_count"], 2)
        self.assertTrue(pro.result["summary"]["station_corrected"])

    def reuse(self, age):
        marker = entitlements_for_sync(self.user).result_marker()
        job = self.make_job(status=ForecastJob.Status.DONE, result={"samples": [], "entitlements": marker})
        ForecastJob.objects.filter(id=job.id).update(updated_at=datetime.now(tz=UTC) - age)
        with patch.dict(os.environ, WITH_KEY):
            return async_to_sync(get_or_start_job)(job.kind, self.user, job.params)[1]

    def test_near_now_pro_job_is_replanned_when_readings_are_old(self):
        self.make_pro()
        self.assertTrue(self.reuse(STATION_JOB_LIFETIME + timedelta(minutes=1)))

    def test_near_now_pro_job_is_reused_while_fresh(self):
        self.make_pro()
        self.assertFalse(self.reuse(STATION_JOB_LIFETIME - timedelta(minutes=1)))

    def test_free_job_keeps_the_normal_lifetime(self):
        self.assertFalse(self.reuse(STATION_JOB_LIFETIME + timedelta(minutes=1)))
