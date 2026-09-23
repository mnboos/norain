import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings

from .forecast_schemas import WeatherSample
from .grid import (
    ENSEMBLE_REQUEST_VERSION,
    _fetch_ensemble,
    _from_open_meteo,
    _from_owm,
    _get_ensemble_cell_sync,
    get_or_fetch_ensemble_cell,
)
from .models import EnsembleCell
from .schedule import LOCAL_TZ
from .uncertainty import ensemble_central, ensemble_weight, extract_uncertainty
from .weather import compute_route_weather

# Provider timestamps are Swiss wall time with the zone left off.
ETA = datetime(2026, 9, 10, 12, tzinfo=LOCAL_TZ).replace(tzinfo=None)
FETCHED = ETA.replace(tzinfo=UTC)


def ensemble_data():
    return {
        "_norain_request_version": ENSEMBLE_REQUEST_VERSION,
        "hourly": {
            "time": ["2026-09-10T12:00", "2026-09-10T13:00"],
            "precipitation_a": [0.0, 0.0],
            "precipitation_member01_a": [1.0, 0.0],
            "precipitation_member02_a": [3.0, 0.0],
            "precipitation_member01_b": [0.0, None],
            "temperature_2m_a": [10.0, 12.0],
            "temperature_2m_member01_a": [20.0, 14.0],
            "wind_speed_10m_a": [10.0, 12.0],
            "wind_speed_10m_member01_a": [20.0, 14.0],
            "wind_direction_10m_a": [359.0, 0.0],
            "wind_direction_10m_member01_a": [1.0, 0.0],
            "wind_gusts_10m_a": [None, None],
        },
    }


def uncertainty(data=None, eta=ETA, bearing=0.0):
    return extract_uncertainty(data or ensemble_data(), eta, bearing, FETCHED, ["a", "b"])


def sample(elapsed=0, **kwargs):
    defaults = {
        "lat": 47.5,
        "lon": 9.5,
        "elapsed_s": elapsed,
        "eta": (ETA + timedelta(seconds=elapsed)).isoformat(),
        "rain_mm": 0.25,
        "precipitation_interval_s": 900,
        "rain_rate_mm_h": 1.0,
        "temp": 15.0,
        "wind_speed": 10.0,
        "wind_dir": 0.0,
        "headwind": 10.0,
        "crosswind": 0.0,
        "weather_desc": "Bewölkt",
    }
    return WeatherSample(**(defaults | kwargs))


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class UncertaintyTests(SimpleTestCase):
    def test_ranges_weight_members_not_models_and_keep_dry_members(self):
        result = uncertainty()
        self.assertEqual(result.pop, 0.5)
        self.assertEqual(result.rain_if_wet, 2.0)
        rain = result.metrics["precipitation"]
        self.assertEqual((rain.member_count, rain.p10, rain.median, rain.p90), (4, 0.0, 0.5, 2.4))
        self.assertAlmostEqual(result.models[0].pop, 2 / 3)
        self.assertIsNone(result.models[1].pop)  # one member cannot supply a range/probability
        self.assertEqual(result.metrics["temperature"].median, 15.0)

    def test_control_is_not_counted_twice(self):
        data = ensemble_data()
        data["hourly"]["precipitation_member00_a"] = [0.0, 0.0]
        self.assertEqual(uncertainty(data).metrics["precipitation"].member_count, 4)

    def test_wind_pairing_and_wraparound(self):
        result = uncertainty()
        self.assertAlmostEqual(result.metrics["headwind"].median, 14.998, places=3)
        self.assertAlmostEqual(result.metrics["crosswind"].median, 0.262, places=3)
        self.assertAlmostEqual(uncertainty(bearing=180).metrics["headwind"].median, -14.998, places=3)
        data = ensemble_data()
        del data["hourly"]["wind_direction_10m_member01_a"]
        result = uncertainty(data)
        self.assertEqual(result.metrics["headwind"].member_count, 1)
        self.assertIsNone(result.metrics["headwind"].median)
        self.assertEqual(result.metrics["windSpeed"].member_count, 2)

    def test_missing_values_and_non_finite_are_not_zero(self):
        data = ensemble_data()
        data["hourly"]["precipitation_member04_a"] = [float("nan"), None]
        result = uncertainty(data)
        self.assertEqual(result.metrics["precipitation"].member_count, 4)
        self.assertEqual(result.metrics["windGust"].member_count, 0)
        self.assertIsNone(result.metrics["windGust"].p10)

    def test_all_dry_and_partial_models(self):
        result = uncertainty(eta=ETA + timedelta(hours=1))
        self.assertEqual(result.pop, 0.0)
        self.assertEqual(result.rain_if_wet, 0.0)
        self.assertEqual(result.metrics["precipitation"].p90, 0.0)
        self.assertEqual([m.model for m in result.models], ["a"])
        self.assertEqual(result.requested_models, ["a", "b"])

    def test_no_extrapolation_or_empty_data(self):
        self.assertIsNone(uncertainty(eta=ETA - timedelta(seconds=1)))
        self.assertIsNone(uncertainty(eta=ETA + timedelta(hours=1, seconds=1)))
        self.assertIsNone(uncertainty({"hourly": {"time": []}}))
        self.assertIsNone(uncertainty({"hourly": []}))

    def test_aware_eta_and_metadata(self):
        result = uncertainty(eta=datetime(2026, 9, 10, 10, tzinfo=UTC))
        self.assertEqual(result.forecast_time, "2026-09-10T12:00:00+02:00")
        self.assertEqual(result.fetched_at, FETCHED.isoformat())
        self.assertEqual(result.precipitation_interval_s, 3600)

    def test_reporting_intervals_and_missing_gusts(self):
        block = {"time": [ETA.isoformat()], "precipitation": [0.25], "temperature_2m": [15.0]}
        minute = _from_open_meteo({"minutely_15": block}, ETA)
        hourly = _from_open_meteo({"hourly": block | {"precipitation": [1.0]}}, ETA)
        owm = _from_owm({"hourly": [{"dt": ETA.timestamp(), "rain": {"1h": 1.0}}]}, ETA)
        self.assertIsNone(minute["wind_gust"])
        self.assertEqual(
            [x["rain_mm"] * 3600 / x["precipitation_interval_s"] for x in (minute, hourly, owm)], [1.0, 1.0, 1.0]
        )

    async def test_route_integration_ensemble_and_provider_fallback(self):
        cell = SimpleNamespace(data={}, source="openweathermap")
        ens = SimpleNamespace(data=ensemble_data(), fetched_at=FETCHED)
        point = {"lat": 47.5, "lon": 9.5, "lat_r": 47.5, "lon_r": 9.5, "elapsed_s": 0, "idx": 0}
        extracted = {
            "rain_mm": 0.25,
            "precipitation_interval_s": 900,
            "temp": 15.0,
            "wind_speed": 10.0,
            "wind_dir": 0.0,
            "wind_gust": None,
            "weather_code": None,
            "pop": 0.7,
            "source": "openweathermap",
        }
        with (
            patch("core.weather.get_or_fetch_forecast_cell", AsyncMock(return_value=cell)),
            patch("core.weather.get_or_fetch_ensemble_cell", AsyncMock(return_value=ens)) as fetch,
            patch("core.weather.extract_sample", return_value=extracted),
        ):
            args = {
                "start_lat": 47.5,
                "start_lon": 9.5,
                "dest_lat": 47.6,
                "dest_lon": 9.6,
                "profile": "bike",
                "departure_time": ETA.isoformat(),
                "sample_points": [point],
                "polyline": [[9.5, 47.5], [9.6, 47.6]],
                "total_seconds": 600,
                "total_distance_m": 1000.0,
            }
            result = await compute_route_weather(**args)
            s = result.samples[0]
            self.assertEqual(s.pop, 0.5)
            self.assertEqual(s.probability_source, "open-meteo-ensemble")
            self.assertEqual(s.rain_rate_mm_h, 1.0)
            self.assertIsNotNone(s.uncertainty)
            fetch.return_value = None
            result = await compute_route_weather(**args)
            self.assertEqual(result.samples[0].pop, 0.7)
            self.assertEqual(result.samples[0].probability_source, "openweathermap")
            self.assertIsNone(result.samples[0].uncertainty)

    async def test_fetch_requests_variables_and_does_not_memoize(self):
        response = Mock()
        response.json.return_value = {"hourly": {}}
        with patch("core.grid.httpx.AsyncClient") as client:
            get = client.return_value.__aenter__.return_value.get = AsyncMock(return_value=response)
            first = await _fetch_ensemble(47.5, 9.5, 2, "2026-09-10")
            await _fetch_ensemble(47.5, 9.5, 2, "2026-09-10")
            self.assertEqual(get.await_count, 2)
            self.assertIn("wind_direction_10m", get.call_args.kwargs["params"]["hourly"])
            self.assertEqual(first["_norain_request_version"], ENSEMBLE_REQUEST_VERSION)


class EnsembleCentralTests(SimpleTestCase):
    def test_weight_ramps_from_48_to_72_hours(self):
        for hours, weight in {0: 0.0, 47: 0.0, 48: 0.0, 60: 0.5, 72: 1.0, 100: 1.0}.items():
            self.assertAlmostEqual(ensemble_weight(FETCHED + timedelta(hours=hours), FETCHED), weight, msg=hours)

    def test_naive_eta_is_swiss_wall_time(self):
        eta = datetime(2026, 9, 13, 12)
        reference = datetime(2026, 9, 10, 12, tzinfo=LOCAL_TZ)
        self.assertEqual(ensemble_weight(eta, reference), 1.0)
        self.assertEqual(ensemble_weight(eta, reference + timedelta(hours=12)), 0.5)

    def test_temperature_median_and_wind_speed_median_along_mean_direction(self):
        central = ensemble_central(ensemble_data(), ETA, ["a", "b"])
        self.assertEqual(central.temp, 15.0)
        self.assertIsNone(central.wind_gust)  # no member has a gust
        # 10 km/h from 359° and 20 km/h from 1°: median speed 15 km/h, mean direction ~0.33°.
        self.assertAlmostEqual(math.hypot(central.wind.east, central.wind.north), 15.0, places=6)
        self.assertAlmostEqual(math.degrees(math.atan2(central.wind.east, central.wind.north)), 1 / 3, places=2)

    def test_disagreeing_directions_do_not_become_calm(self):
        data = ensemble_data()
        data["hourly"]["wind_speed_10m_a"] = [20.0, 0.0]
        data["hourly"]["wind_speed_10m_member01_a"] = [20.0, 0.0]
        data["hourly"]["wind_direction_10m_a"] = [80.0, 0.0]
        data["hourly"]["wind_direction_10m_member01_a"] = [100.0, 0.0]
        wind = ensemble_central(data, ETA, ["a", "b"]).wind
        self.assertAlmostEqual(math.hypot(wind.east, wind.north), 20.0, places=6)  # not 20·cos 10°

    def test_opposite_winds_have_no_central_wind(self):
        data = ensemble_data()
        data["hourly"]["wind_speed_10m_a"] = [20.0, 0.0]
        data["hourly"]["wind_speed_10m_member01_a"] = [20.0, 0.0]
        data["hourly"]["wind_direction_10m_a"] = [90.0, 0.0]
        data["hourly"]["wind_direction_10m_member01_a"] = [270.0, 0.0]
        central = ensemble_central(data, ETA, ["a", "b"])
        self.assertIsNone(central.wind)
        self.assertEqual(central.temp, 15.0)

    def test_one_member_or_outside_hours_is_none(self):
        one = {"hourly": {"time": ["2026-09-10T12:00"], "temperature_2m_a": [10.0], "wind_speed_10m_a": [5.0],
                          "wind_direction_10m_a": [0.0]}}
        self.assertIsNone(ensemble_central(one, ETA, ["a"]))
        self.assertIsNone(ensemble_central(ensemble_data(), ETA - timedelta(hours=1), ["a", "b"]))


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class EnsembleBlendRouteTests(SimpleTestCase):
    """compute_route_weather moves temp and wind toward the ensemble as the lead time grows."""

    async def compute(self, lead_hours):
        cell = SimpleNamespace(data={}, source="open-meteo")
        ens = SimpleNamespace(data=ensemble_data(), fetched_at=ETA.replace(tzinfo=LOCAL_TZ) - timedelta(hours=lead_hours))
        point = {"lat": 47.5, "lon": 9.5, "lat_r": 47.5, "lon_r": 9.5, "elapsed_s": 0, "idx": 0}
        # Wind from the south: the ensemble's is from the north, so the headwind flips sign.
        extracted = {
            "rain_mm": 0.0, "precipitation_interval_s": 900, "temp": 5.0, "wind_speed": 10.0,
            "wind_dir": 180.0, "wind_gust": 20.0, "weather_code": 3, "source": "open-meteo",
        }
        with (
            patch("core.weather.get_or_fetch_forecast_cell", AsyncMock(return_value=cell)),
            patch("core.weather.get_or_fetch_ensemble_cell", AsyncMock(return_value=ens)),
            patch("core.weather.extract_sample", return_value=extracted),
        ):
            result = await compute_route_weather(
                start_lat=47.5, start_lon=9.5, dest_lat=47.6, dest_lon=9.5, profile="bike",
                departure_time=ETA.isoformat(), sample_points=[point], polyline=[[9.5, 47.5], [9.5, 47.6]],
                total_seconds=600, total_distance_m=11_000.0,
            )
        return result.samples[0]

    async def test_single_run_below_48_hours(self):
        s = await self.compute(24)
        self.assertEqual((s.temp, s.wind_speed, s.wind_dir, s.wind_gust), (5.0, 10.0, 180.0, 20.0))
        self.assertIsNone(s.ensemble_weight)
        self.assertLess(s.headwind, 0)

    async def test_ensemble_from_72_hours(self):
        s = await self.compute(96)
        self.assertEqual(s.ensemble_weight, 1.0)
        self.assertEqual(s.temp, 15.0)  # the ensemble median, the chart's median line
        self.assertEqual(s.wind_gust, 20.0)  # no ensemble gust: the single run's stays
        self.assertAlmostEqual(s.wind_speed, 15.0, delta=0.1)
        self.assertLess(min(s.wind_dir, 360 - s.wind_dir), 1)
        self.assertGreater(s.headwind, 0)
        self.assertAlmostEqual(s.headwind, s.uncertainty.metrics["headwind"].median, delta=0.1)

    async def test_halfway_at_60_hours(self):
        s = await self.compute(60)
        self.assertEqual(s.ensemble_weight, 0.5)
        self.assertEqual(s.temp, 10.0)
        # Half of 10 km/h from the south plus half of ~15 km/h from the north: ~2.5 km/h from the north.
        self.assertAlmostEqual(s.wind_speed, 2.5, delta=0.1)


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class UncertaintyCacheTests(TestCase):
    def setUp(self):
        self.cell = EnsembleCell.objects.create(
            lat_r=47.5, lon_r=9.5, day_key=ETA.date(), forecast_days=2, data={"hourly": {"time": []}}
        )

    def test_old_payload_invalidated_and_partial_current_payload_accepted(self):
        args = (47.5, 9.5, ETA.date(), 2)
        self.assertIsNone(_get_ensemble_cell_sync(*args))
        self.cell.data["_norain_request_version"] = ENSEMBLE_REQUEST_VERSION
        self.cell.save()
        self.assertIsNotNone(_get_ensemble_cell_sync(*args))
        EnsembleCell.objects.filter(pk=self.cell.pk).update(fetched_at=datetime.now(UTC) - timedelta(hours=3))
        self.assertIsNone(_get_ensemble_cell_sync(*args))

    async def test_cache_upgrade_fetches_once_even_with_unsupported_variables(self):
        payload = {"_norain_request_version": ENSEMBLE_REQUEST_VERSION, "hourly": {"time": []}}
        with patch("core.grid._fetch_ensemble", AsyncMock(return_value=payload)) as fetch:
            await get_or_fetch_ensemble_cell(47.5, 9.5, ETA.date(), 2)
            await get_or_fetch_ensemble_cell(47.5, 9.5, ETA.date(), 2)
            self.assertEqual(fetch.await_count, 1)
