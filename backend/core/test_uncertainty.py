from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from django.test import SimpleTestCase, TestCase

from .grid import (
    ENSEMBLE_REQUEST_VERSION,
    _fetch_ensemble,
    _from_open_meteo,
    _from_owm,
    _get_ensemble_cell_sync,
    get_or_fetch_ensemble_cell,
)
from .models import EnsembleCell
from .plotting import generate_forecast_figures
from .uncertainty import extract_uncertainty
from .weather import _summarize, compute_route_weather
from .weather_schemas import RouteWeatherOut, WeatherSample

ETA = datetime(2026, 9, 10, 12)  # noqa: DTZ001 -- provider timestamps are Swiss local wall time
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

    def test_chart_gaps_time_positions_and_sample_indices(self):
        points = [
            sample(0, uncertainty=uncertainty(), pop=0.5),
            sample(300),
            sample(600, uncertainty=uncertainty(), pop=0.0),
        ]
        forecast = RouteWeatherOut(
            line=[],
            total_seconds=600,
            total_distance_m=3000.0,
            samples=points,
            summary=_summarize(points, "open-meteo"),
        )
        figures = generate_forecast_figures(forecast)
        median = next(
            t
            for t in figures[0]["data"]
            if t.get("legendgroup") == "temperature" and t.get("mode") == "lines+markers" and not t["line"].get("dash")
        )
        self.assertEqual(list(median["x"]), [0.0, 5.0, 10.0])
        self.assertEqual(list(median["y"]), [15.0, None, 15.0])
        self.assertEqual(median["customdata"][2][0], 2)
        bands = [t for t in figures[0]["data"] if t.get("fill") == "tonexty"]
        self.assertEqual(len(bands), 2)
        pop = next(t for t in figures[1]["data"] if t.get("yaxis") == "y2")
        self.assertEqual(list(pop["y"]), [50.0, None, 0.0])
        gust = next(t for t in figures[2]["data"] if t.get("legendgroup") == "windGust" and t["line"].get("dash") == "dot")
        self.assertEqual(list(gust["y"]), [None, None, None])
        # One legend entry per metric; a lone series (temperature) gets no legend at all.
        self.assertFalse(figures[0]["layout"]["showlegend"])
        for figure in figures:
            shown = [t["legendgroup"] for t in figure["data"] if t.get("showlegend", True)]
            self.assertEqual(len(shown), len(set(shown)))

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
