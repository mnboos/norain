import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import stripe
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils.http import urlsafe_base64_encode

from core.api.route_weather import ForecastUncertainty, RouteWeatherOut, RouteWeatherSummary, WeatherSample
from core.auth.tokens import email_verification_token_generator
from core.entitlements import FREE, PRO, entitlements_for_sync, strip_uncertainty
from core.grid import (
    _ensemble_at,
    _from_open_meteo,
    _from_owm,
    _get_ensemble_cell_sync,
    _get_forecast_cell_sync,
    _nearest_index,
    extract_sample,
)
from core.models import (
    EnsembleCell,
    ForecastCell,
    Plan,
    ProcessedStripeEvent,
    RecurringRoute,
    Subscription,
    route_line,
    route_point,
)
from core.schedule import next_departure
from core.tasks import _known_samples, _prewarm_routes, _refresh_route_thumbnail_async
from core.thumbnails import MAX_THUMBNAIL_VERTICES, compute_route_thumbnail, simplify_path
from core.weather import (
    _bearing_deg,
    _cumulative_times_s,
    _forecast_days,
    _sample_indices,
    _summarize,
    _wind_components,
)


class GeometryTests(SimpleTestCase):
    def test_route_geometries_keep_longitude_latitude_order(self):
        point = route_point(47.5, 9.3)
        line = route_line([[9.3, 47.5], [9.4, 47.6]])

        self.assertEqual(point.srid, 4326)
        self.assertEqual((point.x, point.y), (9.3, 47.5))
        self.assertEqual(line.srid, 4326)
        self.assertEqual(list(line.coords), [(9.3, 47.5), (9.4, 47.6)])

    def test_bearing_cardinal(self):
        # due north, due east, due south, due west (from origin)
        self.assertAlmostEqual(_bearing_deg(0, 0, 0, 1), 0.0, places=1)
        self.assertAlmostEqual(_bearing_deg(0, 0, 1, 0), 90.0, places=1)
        self.assertAlmostEqual(_bearing_deg(0, 0, 0, -1), 180.0, places=1)
        self.assertAlmostEqual(_bearing_deg(0, 0, -1, 0), 270.0, places=1)

    def test_cumulative_times_distance_weighted(self):
        # three vertices along the equator: 0->1deg, then 1->3deg (twice as long).
        coords = [[0, 0], [1, 0], [3, 0]]
        # one interval covering all points, total 90s -> split 30s / 60s by length
        cum = _cumulative_times_s(coords, [[0, 2, 90_000]])
        self.assertAlmostEqual(cum[0], 0.0)
        self.assertAlmostEqual(cum[1], 30.0, places=1)
        self.assertAlmostEqual(cum[2], 90.0, places=1)

    def test_sample_indices_includes_endpoints(self):
        cum = [0.0, 100.0, 200.0, 300.0, 400.0]
        idx = _sample_indices(cum, interval_s=200)
        # targets 0, 200, 400 -> nearest vertices 0, 2, 4
        self.assertEqual(idx[0], 0)
        self.assertIn(2, idx)
        self.assertEqual(idx[-1], 4)


class WindTests(SimpleTestCase):
    def test_pure_headwind(self):
        # travelling north (bearing 0), wind FROM the north (0) -> full headwind
        head, cross = _wind_components(20.0, 0.0, 0.0)
        self.assertAlmostEqual(head, 20.0, places=3)
        self.assertAlmostEqual(cross, 0.0, places=3)

    def test_pure_tailwind(self):
        # travelling north, wind FROM the south (180) -> full tailwind (negative head)
        head, cross = _wind_components(20.0, 180.0, 0.0)
        self.assertAlmostEqual(head, -20.0, places=3)
        self.assertAlmostEqual(cross, 0.0, places=3)

    def test_pure_crosswind(self):
        head, cross = _wind_components(20.0, 90.0, 0.0)
        self.assertAlmostEqual(head, 0.0, places=3)
        self.assertAlmostEqual(cross, 20.0, places=3)


class ForecastMatchTests(SimpleTestCase):
    def test_nearest_index(self):
        times = ["2026-06-03T08:00", "2026-06-03T08:15", "2026-06-03T08:30"]
        self.assertEqual(_nearest_index(times, datetime.fromisoformat("2026-06-03T08:12")), 1)
        self.assertEqual(_nearest_index(times, datetime.fromisoformat("2026-06-03T08:29")), 2)

    def test_from_open_meteo_prefers_15min(self):
        data = {
            "minutely_15": {
                "time": ["2026-06-03T08:00", "2026-06-03T08:15", "2026-06-03T08:30"],
                # convective shower: total precipitation, but the large-scale `rain` field is empty
                "rain": [0.0, 0.0, 0.0],
                "precipitation": [0.0, 1.2, 0.0],
                "temperature_2m": [12.0, 12.5, 13.0],
                "wind_speed_10m": [10.0, 11.0, 12.0],
                "wind_gusts_10m": [18.0, 19.0, 20.0],
                "wind_direction_10m": [270.0, 270.0, 270.0],
                "weather_code": [3, 61, 3],
            }
        }
        out = _from_open_meteo(data, datetime.fromisoformat("2026-06-03T08:14"))
        self.assertEqual(out["source"], "open-meteo")
        self.assertAlmostEqual(out["rain_mm"], 1.2)
        self.assertEqual(out["weather_code"], 61)
        self.assertAlmostEqual(out["wind_speed"], 11.0)

    def test_from_open_meteo_falls_back_to_hourly_when_out_of_range(self):
        data = {
            "minutely_15": {
                "time": ["2026-06-03T08:00", "2026-06-03T08:15"],
                "rain": [0.0, 0.0],
                "temperature_2m": [12.0, 12.5],
                "wind_speed_10m": [10.0, 11.0],
                "wind_gusts_10m": [18.0, 19.0],
                "wind_direction_10m": [270.0, 270.0],
                "weather_code": [3, 3],
            },
            "hourly": {
                "time": ["2026-06-05T08:00", "2026-06-05T09:00"],
                "rain": [2.0, 0.0],
                "temperature_2m": [15.0, 16.0],
                "wind_speed_10m": [20.0, 21.0],
                "wind_gusts_10m": [30.0, 31.0],
                "wind_direction_10m": [180.0, 180.0],
                "weather_code": [63, 3],
            },
        }
        # eta is two days out -> beyond the 15-min block, must use hourly
        out = _from_open_meteo(data, datetime.fromisoformat("2026-06-05T08:10"))
        self.assertAlmostEqual(out["rain_mm"], 2.0)
        self.assertEqual(out["weather_code"], 63)

    def test_from_open_meteo_clamps_to_last_sample_when_eta_out_of_range(self):
        # eta sits 5 days past the end of every block — the hourly block clamps
        # to the last available data point instead of returning None.
        data = {
            "minutely_15": {
                "time": ["2026-06-03T08:00", "2026-06-03T08:15"],
                "precipitation": [0.0, 0.0],
                "temperature_2m": [12.0, 12.5],
                "wind_speed_10m": [10.0, 11.0],
                "wind_gusts_10m": [18.0, 19.0],
                "wind_direction_10m": [270.0, 270.0],
                "weather_code": [3, 3],
            },
            "hourly": {
                "time": ["2026-06-03T08:00", "2026-06-03T09:00"],
                "precipitation": [0.0, 0.0],
                "temperature_2m": [12.0, 13.0],
                "wind_speed_10m": [10.0, 12.0],
                "wind_gusts_10m": [18.0, 20.0],
                "wind_direction_10m": [270.0, 270.0],
                "weather_code": [3, 3],
            },
        }
        out = _from_open_meteo(data, datetime.fromisoformat("2026-06-08T14:00"))
        # clamps to the last hourly entry (June 3 09:00)
        self.assertIsNotNone(out)
        self.assertEqual(out["source"], "open-meteo")
        self.assertAlmostEqual(out["temp"], 13.0)
        self.assertAlmostEqual(out["wind_speed"], 12.0)

    def test_from_open_meteo_returns_none_when_no_usable_data(self):
        """No blocks at all returns None."""
        self.assertIsNone(_from_open_meteo({}, datetime.fromisoformat("2026-06-03T08:00")))
        # Blocks present but no time arrays
        self.assertIsNone(_from_open_meteo({"hourly": {}}, datetime.fromisoformat("2026-06-03T08:00")))

    def test_from_open_meteo_clamps_hourly_nearby_eta(self):
        """Eta 1.5h before the first hourly entry — clamping picks the first entry."""
        data = {
            "hourly": {
                "time": ["2026-06-03T10:00", "2026-06-03T11:00"],
                "precipitation": [1.0, 0.0],
                "temperature_2m": [20.0, 21.0],
                "wind_speed_10m": [5.0, 6.0],
                "wind_gusts_10m": [8.0, 9.0],
                "wind_direction_10m": [180.0, 180.0],
                "weather_code": [61, 3],
            }
        }
        out = _from_open_meteo(data, datetime.fromisoformat("2026-06-03T08:30"))
        self.assertEqual(out["source"], "open-meteo")
        # nearest entry is 10:00 (1.5h away — clamped)
        self.assertAlmostEqual(out["temp"], 20.0)
        self.assertAlmostEqual(out["rain_mm"], 1.0)


class EnsembleAtTests(SimpleTestCase):
    def _data(self):
        # 4 precipitation series (a control + three members) over three hours
        return {
            "hourly": {
                "time": ["2026-06-20T00:00", "2026-06-20T01:00", "2026-06-20T02:00"],
                "precipitation_icon_seamless_eps": [0.0, 0.5, 0.0],
                "precipitation_member01_icon_seamless_eps": [0.0, 0.2, 0.0],
                "precipitation_member02_icon_seamless_eps": [0.0, 0.05, 0.0],  # below 0.1 -> dry member
                "precipitation_member01_meteoswiss_icon_ch1_ensemble": [0.0, 0.0, 0.0],
            }
        }

    def test_pop_and_if_wet_amount(self):
        # at 01:00 two of four series are wet (0.5, 0.2) -> pop 0.5, "if it rains" mean (0.5+0.2)/2 = 0.35
        pop, rain_if_wet = _ensemble_at(self._data(), datetime.fromisoformat("2026-06-20T01:00"))
        self.assertAlmostEqual(pop, 0.5)
        self.assertAlmostEqual(rain_if_wet, 0.35)
        # at 00:00 none are wet -> pop 0.0 and amount 0.0 (no wet members to average)
        self.assertEqual(_ensemble_at(self._data(), datetime.fromisoformat("2026-06-20T00:00")), (0.0, 0.0))

    def test_skips_members_with_no_value(self):
        # a regional model out of its domain (all None) must not count toward the total
        data = self._data()
        data["hourly"]["precipitation_member01_meteoswiss_icon_ch2_ensemble"] = [None, None, None]
        pop, rain_if_wet = _ensemble_at(data, datetime.fromisoformat("2026-06-20T01:00"))
        self.assertAlmostEqual(pop, 0.5)
        self.assertAlmostEqual(rain_if_wet, 0.35)

    def test_returns_none_when_out_of_range(self):
        self.assertIsNone(_ensemble_at(self._data(), datetime.fromisoformat("2026-06-25T01:00")))
        self.assertIsNone(_ensemble_at({"hourly": {}}, datetime.fromisoformat("2026-06-20T01:00")))


class SummarizeTests(SimpleTestCase):
    @staticmethod
    def _sample(eta, *, pop=None, rain_mm=0.0, rain_if_wet=None, headwind=0.0):
        return WeatherSample(
            lat=47.5,
            lon=9.3,
            elapsed_s=0,
            eta=eta,
            rain_mm=rain_mm,
            pop=pop,
            rain_if_wet=rain_if_wet,
            temp=15.0,
            wind_speed=10.0,
            wind_dir=270.0,
            headwind=headwind,
            crosswind=0.0,
            weather_desc="",
        )

    def test_verdict_uses_ensemble_probability(self):
        samples = [
            self._sample("2026-06-20T14:00", pop=0.0, rain_if_wet=0.0),
            # over verdict, dry control run, but ensemble has an "if it rains" amount
            self._sample("2026-06-20T14:30", pop=0.4, rain_mm=0.0, rain_if_wet=0.3),
            self._sample("2026-06-20T15:00", pop=0.1, rain_if_wet=0.2),
        ]
        s = _summarize(samples, "open-meteo")
        self.assertTrue(s.will_rain)  # probability-driven even though every rain_mm is 0
        self.assertEqual(s.first_rain_eta, "2026-06-20T14:30")
        self.assertAlmostEqual(s.rain_probability, 0.4)
        self.assertAlmostEqual(s.rain_amount, 0.3)  # if-it-rains amount at the peak-pop point

    def test_rain_amount_is_nonzero_when_risk_is_nonzero(self):
        # the bug: deterministic dry (rain_mm 0.0) but the ensemble has risk -> amount must still be > 0,
        # taken at the peak-risk point so risk and amount tell one coherent story.
        samples = [
            self._sample("2026-06-20T14:00", pop=0.1, rain_mm=0.0, rain_if_wet=0.5),
            self._sample("2026-06-20T14:30", pop=0.4, rain_mm=0.0, rain_if_wet=0.2),  # peak risk
        ]
        s = _summarize(samples, "open-meteo")
        self.assertAlmostEqual(s.rain_probability, 0.4)
        self.assertAlmostEqual(s.rain_amount, 0.2)  # if-it-rains amount at the peak-pop point
        self.assertAlmostEqual(s.max_rain_mm, 0.0)  # deterministic amount stays 0 -> no contradiction

    def test_probability_below_verdict_is_no_rain(self):
        samples = [
            self._sample("2026-06-20T14:00", pop=0.1, rain_if_wet=0.2),
            self._sample("2026-06-20T14:30", pop=0.2, rain_if_wet=0.3),
        ]
        s = _summarize(samples, "open-meteo")
        self.assertFalse(s.will_rain)
        self.assertAlmostEqual(s.rain_probability, 0.2)
        self.assertAlmostEqual(s.rain_amount, 0.3)  # peak-pop point

    def test_falls_back_to_amount_when_no_pop_stays_coherent(self):
        # ensemble down for the whole route (no pop anywhere): verdict from the deterministic amount,
        # and rain_probability is None so the UI shows mm instead of contradicting with "0% trocken".
        samples = [self._sample("2026-06-20T14:00", rain_mm=0.0), self._sample("2026-06-20T14:30", rain_mm=0.5)]
        s = _summarize(samples, "open-meteo")
        self.assertTrue(s.will_rain)  # no ensemble pop anywhere -> amount branch (0.5 >= 0.1)
        self.assertEqual(s.first_rain_eta, "2026-06-20T14:30")
        self.assertIsNone(s.rain_probability)  # no probability available -> not a phantom 0%
        self.assertAlmostEqual(s.rain_amount, 0.5)  # show the real deterministic amount
        # coherence: never "rain expected" while reporting a 0% probability
        self.assertFalse(s.will_rain and s.rain_probability == 0.0)

    def test_empty_samples(self):
        s = _summarize([], "open-meteo")
        self.assertFalse(s.will_rain)
        self.assertAlmostEqual(s.max_rain_mm, 0.0)
        self.assertIsNone(s.rain_probability)
        self.assertAlmostEqual(s.rain_amount, 0.0)

    def test_no_risk_means_zero_amount(self):
        # ensemble present but no member wet -> 0% and rain_amount 0.0 so the UI shows "trocken"
        samples = [self._sample("2026-06-20T14:00", pop=0.0, rain_if_wet=0.0)]
        s = _summarize(samples, "open-meteo")
        self.assertAlmostEqual(s.rain_probability, 0.0)
        self.assertAlmostEqual(s.rain_amount, 0.0)


class OwmExtractTests(SimpleTestCase):
    """Tests for _from_owm and the source-aware extract_sample dispatcher."""

    @staticmethod
    def _owm_data(hourly_entries: list[dict]) -> dict:
        return {"hourly": hourly_entries}

    def test_normal_hourly_extraction(self):
        """OWM list-of-dicts hourly with standard fields."""
        eta = datetime.fromisoformat("2026-06-20T14:00")
        data = self._owm_data(
            [
                {
                    "dt": int(datetime(2026, 6, 20, 13, 0).timestamp()),
                    "temp": 18.0,
                    "wind_speed": 2.0,  # m/s
                    "wind_deg": 270,
                    "wind_gust": 5.0,
                    "pop": 0.3,
                },
                {
                    "dt": int(datetime(2026, 6, 20, 14, 0).timestamp()),
                    "temp": 19.0,
                    "wind_speed": 3.0,
                    "wind_deg": 180,
                    "wind_gust": 6.0,
                    "pop": 0.5,
                },
            ]
        )
        out = _from_owm(data, eta)
        self.assertEqual(out["source"], "openweathermap")
        self.assertAlmostEqual(out["temp"], 19.0)
        self.assertAlmostEqual(out["wind_speed"], 3.0 * 3.6)  # m/s → km/h
        self.assertAlmostEqual(out["wind_gust"], 6.0 * 3.6)
        self.assertAlmostEqual(out["wind_dir"], 180.0)
        self.assertAlmostEqual(out["pop"], 0.5)
        self.assertAlmostEqual(out["rain_mm"], 0.0)

    def test_rain_as_float_owm3(self):
        """OWM One Call 3.0 returns rain as a float."""
        eta = datetime.fromisoformat("2026-06-20T14:00")
        data = self._owm_data(
            [
                {
                    "dt": int(eta.timestamp()),
                    "temp": 15.0,
                    "wind_speed": 1.0,
                    "wind_deg": 0,
                    "pop": 0.8,
                    "rain": 2.5,  # float, not {"1h": 2.5}
                }
            ]
        )
        out = _from_owm(data, eta)
        self.assertAlmostEqual(out["rain_mm"], 2.5)

    def test_rain_as_object_legacy(self):
        """Legacy OWM format where rain is a dict {'1h': value}."""
        eta = datetime.fromisoformat("2026-06-20T14:00")
        data = self._owm_data(
            [
                {
                    "dt": int(eta.timestamp()),
                    "temp": 15.0,
                    "wind_speed": 1.0,
                    "wind_deg": 0,
                    "pop": 0.8,
                    "rain": {"1h": 1.5},
                }
            ]
        )
        out = _from_owm(data, eta)
        self.assertAlmostEqual(out["rain_mm"], 1.5)

    def test_missing_rain(self):
        """No rain key in the entry → rain_mm = 0.0."""
        eta = datetime.fromisoformat("2026-06-20T14:00")
        data = self._owm_data(
            [
                {
                    "dt": int(eta.timestamp()),
                    "temp": 15.0,
                    "wind_speed": 1.0,
                    "wind_deg": 90,
                }
            ]
        )
        out = _from_owm(data, eta)
        self.assertAlmostEqual(out["rain_mm"], 0.0)

    def test_rejects_dict_hourly(self):
        """Open-Meteo-style dict hourly → _from_owm returns None (no crash)."""
        # This is the regression test for the reported bug:
        # Open-Meteo hourly is a dict {time: [...], temperature_2m: [...]}
        data = {
            "hourly": {
                "time": ["2026-06-20T14:00"],
                "temperature_2m": [15.0],
                "wind_speed_10m": [10.0],
            }
        }
        eta = datetime.fromisoformat("2026-06-20T14:00")
        self.assertIsNone(_from_owm(data, eta))

    def test_empty_hourly_list(self):
        """Empty hourly list returns None."""
        self.assertIsNone(_from_owm(self._owm_data([]), datetime.now()))

    def test_hourly_missing_key(self):
        """No 'hourly' key at all returns None."""
        self.assertIsNone(_from_owm({}, datetime.now()))

    def test_extract_sample_routes_to_owm(self):
        """extract_sample with source='openweathermap' uses OWM parser."""
        eta = datetime.fromisoformat("2026-06-20T14:00")
        data = self._owm_data(
            [
                {
                    "dt": int(eta.timestamp()),
                    "temp": 20.0,
                    "wind_speed": 2.0,
                    "wind_deg": 180,
                }
            ]
        )
        out = extract_sample(data, eta, source="openweathermap")
        self.assertEqual(out["source"], "openweathermap")
        self.assertAlmostEqual(out["temp"], 20.0)

    def test_extract_sample_routes_to_open_meteo(self):
        """extract_sample with source='open-meteo' uses OM parser."""
        eta = datetime.fromisoformat("2026-06-20T14:00")
        data = {
            "hourly": {
                "time": [eta.isoformat()],
                "temperature_2m": [25.0],
                "wind_speed_10m": [5.0],
                "wind_gusts_10m": [8.0],
                "wind_direction_10m": [90.0],
                "weather_code": [1],
                "rain": [0.0],
            }
        }
        out = extract_sample(data, eta, source="open-meteo")
        self.assertEqual(out["source"], "open-meteo")
        self.assertAlmostEqual(out["temp"], 25.0)

    def test_extract_sample_none_source_falls_back(self):
        """extract_sample with source=None uses heuristic (OM first)."""
        eta = datetime.fromisoformat("2026-06-20T14:00")
        data = {
            "hourly": {
                "time": [eta.isoformat()],
                "temperature_2m": [22.0],
                "wind_speed_10m": [4.0],
                "wind_gusts_10m": [7.0],
                "wind_direction_10m": [270.0],
                "weather_code": [2],
                "rain": [0.0],
            }
        }
        out = extract_sample(data, eta)  # source=None
        self.assertEqual(out["source"], "open-meteo")
        self.assertAlmostEqual(out["temp"], 22.0)

    def test_from_open_meteo_rejects_list_hourly(self):
        """OM parser gracefully returns None on OWM list-form hourly (no crash)."""
        eta = datetime.fromisoformat("2026-06-20T14:00")
        data = {"hourly": [{"dt": int(eta.timestamp()), "temp": 15.0, "wind_speed": 2.0, "wind_deg": 180}]}
        self.assertIsNone(_from_open_meteo(data, eta))


class ForecastDaysTests(SimpleTestCase):
    def test_window_sized_from_today_not_departure(self):
        today = date(2026, 6, 16)
        # a same-day trip still needs 2 days; a pick 5 days out needs 7
        self.assertEqual(_forecast_days(datetime(2026, 6, 16, 14, 0), today), 2)
        self.assertEqual(_forecast_days(datetime(2026, 6, 21, 14, 0), today), 7)

    def test_clamped_to_open_meteo_limits(self):
        today = date(2026, 6, 16)
        # far future clamps to 16; a past eta floors to 1
        self.assertEqual(_forecast_days(datetime(2026, 12, 31, 0, 0), today), 16)
        self.assertEqual(_forecast_days(datetime(2026, 6, 10, 0, 0), today), 1)


class PlottingTests(SimpleTestCase):
    """Tests for figure generation with empty/invalid input."""

    @staticmethod
    def _make_forecast(samples: list[WeatherSample] | None = None) -> RouteWeatherOut:
        return RouteWeatherOut(
            line=[[9.5, 47.5], [9.6, 47.6]],
            total_seconds=600,
            total_distance_m=5000.0,
            samples=samples or [],
            summary=RouteWeatherSummary(
                will_rain=False,
                first_rain_eta=None,
                first_rain_place=None,
                max_rain_mm=0.0,
                rain_probability=None,
                rain_amount=0.0,
                max_headwind=0.0,
                source="open-meteo",
            ),
        )

    def test_empty_samples_returns_placeholder_figures(self):
        from core.plotting import generate_forecast_figures

        forecast = self._make_forecast([])
        figures = generate_forecast_figures(forecast)

        self.assertEqual(len(figures), 3)
        for fig in figures:
            self.assertIn("data", fig)
            self.assertIn("layout", fig)

    def test_placeholder_figures_contain_message(self):
        from core.plotting import generate_forecast_figures

        forecast = self._make_forecast([])
        figures = generate_forecast_figures(forecast)

        # All 3 figures are Cartesian placeholders with the no-data message.
        for fig in figures:
            annotation_texts = [a.get("text", "") for a in fig["layout"].get("annotations", [])]
            self.assertTrue(
                any("Keine Wetterdaten" in t for t in annotation_texts),
                f"Expected 'Keine Wetterdaten' in annotations, got {annotation_texts}",
            )

    def test_figures_only_use_cartesian_traces(self):
        """Figures must stick to scatter/bar - the frontend registers only those."""
        from core.plotting import generate_forecast_figures

        samples = [
            WeatherSample(
                lat=47.5,
                lon=9.5,
                elapsed_s=0,
                eta="2026-06-20T14:00",
                rain_mm=0.0,
                temp=18.0,
                wind_speed=10.0,
                wind_dir=270.0,
                headwind=5.0,
                crosswind=2.0,
                weather_desc="klar",
            ),
            WeatherSample(
                lat=47.55,
                lon=9.55,
                elapsed_s=300,
                eta="2026-06-20T14:05",
                rain_mm=2.5,
                temp=17.0,
                wind_speed=12.0,
                wind_dir=180.0,
                headwind=10.0,
                crosswind=0.0,
                weather_desc="Regen",
            ),
            WeatherSample(
                lat=47.6,
                lon=9.6,
                elapsed_s=600,
                eta="2026-06-20T14:10",
                rain_mm=0.3,
                temp=19.0,
                wind_speed=8.0,
                wind_dir=90.0,
                headwind=0.0,
                crosswind=5.0,
                weather_desc="bewölkt",
            ),
        ]
        forecast = self._make_forecast(samples)
        figures = generate_forecast_figures(forecast)

        self.assertEqual(len(figures), 3)
        trace_types = {t.get("type", "scatter") for fig in figures for t in fig["data"]}
        self.assertTrue(
            trace_types <= {"scatter", "bar"},
            f"Unexpected trace types for the trimmed plotly.js bundle: {trace_types}",
        )


class CellCacheTests(TestCase):
    """Tests for forecast_days-aware cell cache freshness."""

    def setUp(self):
        self.lat_r = 47.50
        self.lon_r = 9.55
        self.day_key = date(2026, 6, 20)

    def _create_forecast_cell(self, forecast_days: int, source: str = "open-meteo") -> ForecastCell:
        return ForecastCell.objects.create(
            lat_r=self.lat_r,
            lon_r=self.lon_r,
            day_key=self.day_key,
            source=source,
            forecast_days=forecast_days,
            data={"hourly": {"time": [], "temperature_2m": []}},
        )

    def _create_ensemble_cell(self, forecast_days: int) -> EnsembleCell:
        return EnsembleCell.objects.create(
            lat_r=self.lat_r,
            lon_r=self.lon_r,
            day_key=self.day_key,
            forecast_days=forecast_days,
            data={"_norain_request_version": 2, "hourly": {"time": [], "precipitation": []}},
        )

    def test_rejects_insufficient_forecast_days(self):
        self._create_forecast_cell(forecast_days=2)
        cell = _get_forecast_cell_sync(self.lat_r, self.lon_r, self.day_key, "open-meteo", forecast_days=5)
        self.assertIsNone(cell)

    def test_accepts_sufficient_forecast_days(self):
        self._create_forecast_cell(forecast_days=5)
        cell = _get_forecast_cell_sync(self.lat_r, self.lon_r, self.day_key, "open-meteo", forecast_days=5)
        self.assertIsNotNone(cell)
        self.assertEqual(cell.forecast_days, 5)

    def test_accepts_when_forecast_days_is_none(self):
        """Backward compat: forecast_days=None skips the horizon check."""
        self._create_forecast_cell(forecast_days=2)
        cell = _get_forecast_cell_sync(self.lat_r, self.lon_r, self.day_key, "open-meteo", forecast_days=None)
        self.assertIsNotNone(cell)

    def test_ensemble_rejects_insufficient_days(self):
        self._create_ensemble_cell(forecast_days=2)
        cell = _get_ensemble_cell_sync(self.lat_r, self.lon_r, self.day_key, forecast_days=5)
        self.assertIsNone(cell)

    def test_ensemble_accepts_sufficient_days(self):
        self._create_ensemble_cell(forecast_days=5)
        cell = _get_ensemble_cell_sync(self.lat_r, self.lon_r, self.day_key, forecast_days=5)
        self.assertIsNotNone(cell)

    def test_ensemble_accepts_when_days_none(self):
        self._create_ensemble_cell(forecast_days=2)
        cell = _get_ensemble_cell_sync(self.lat_r, self.lon_r, self.day_key, forecast_days=None)
        self.assertIsNotNone(cell)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.test",
    FRONTEND_URL="http://frontend.example.test",
)
class AuthApiTests(TestCase):
    """Exercise verification, session, CSRF, and route isolation at the HTTP boundary."""

    password = "Correct horse battery staple 2026!"

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def csrf_headers(self):
        self.client.get("/api/auth/session")
        return {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value}

    def post_json(self, path, data, *, csrf=True):
        headers = self.csrf_headers() if csrf else {}
        return self.client.post(path, data=json.dumps(data), content_type="application/json", **headers)

    @staticmethod
    def route(owner):
        return RecurringRoute.objects.create(
            owner=owner,
            name="Commute",
            start_point=route_point(47.5, 9.3),
            start_name="Start",
            destination_point=route_point(47.6, 9.4),
            dest_name="Destination",
            schedule_cron="0 8 * * 1",
            schedule_description="Monday at 08:00",
        )

    def test_signup_requires_csrf_and_verification_before_login(self):
        response = self.post_json(
            "/api/auth/signup",
            {"email": "Rider@Example.test", "username": "Rider", "password": self.password},
            csrf=False,
        )
        self.assertEqual(response.status_code, 403)

        response = self.post_json(
            "/api/auth/signup",
            {"email": "Rider@Example.test", "username": "Rider", "password": self.password},
        )
        self.assertEqual(response.status_code, 201)
        user = get_user_model().objects.get(email="rider@example.test")
        self.assertFalse(user.is_active)
        self.assertFalse(user.email_verified)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("frontend.example.test/account", mail.outbox[0].body)

        response = self.post_json("/api/auth/login", {"email": user.email, "password": self.password})
        self.assertEqual(response.status_code, 401)

        response = self.post_json(
            "/api/auth/verify-email",
            {
                "uid": urlsafe_base64_encode(str(user.pk).encode()),
                "token": email_verification_token_generator.make_token(user),
            },
        )
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(user.email_verified)

        response = self.post_json("/api/auth/login", {"email": user.email, "password": self.password})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["authenticated"])
        self.assertEqual(response.json()["user"]["email"], user.email)
        self.assertEqual(response.json()["user"]["username"], "Rider")

    def test_login_accepts_either_email_or_username(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="Velofahrer", email="rider@example.test", password=self.password, email_verified=True
        )

        # Both identities work, and neither is case-sensitive.
        for identifier in ("rider@example.test", "RIDER@EXAMPLE.TEST", "Velofahrer", "velofahrer"):
            with self.subTest(identifier=identifier):
                response = self.post_json("/api/auth/login", {"identifier": identifier, "password": self.password})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["user"]["username"], "Velofahrer")
                self.post_json("/api/auth/logout", {})

        # The older "email" key still carries a username, so an un-updated client keeps working.
        response = self.post_json("/api/auth/login", {"email": "Velofahrer", "password": self.password})
        self.assertEqual(response.status_code, 200)

    def test_superuser_can_login_without_email_verification(self):
        user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.test", password=self.password
        )
        self.assertTrue(user.email_verified)

        # The exemption also keeps superusers made before the custom manager change usable.
        user.email_verified = False
        user.save(update_fields=["email_verified"])
        response = self.post_json("/api/auth/login", {"identifier": "admin", "password": self.password})
        self.assertEqual(response.status_code, 200)

    def test_login_rejects_unverified_account_by_either_identity(self):
        User = get_user_model()
        User.objects.create_user(
            username="newcomer", email="new@example.test", password=self.password, email_verified=False
        )

        for identifier in ("new@example.test", "newcomer"):
            with self.subTest(identifier=identifier):
                response = self.post_json("/api/auth/login", {"identifier": identifier, "password": self.password})
                self.assertEqual(response.status_code, 401)

    def test_username_containing_at_sign_resolves_as_a_username(self):
        """UnicodeUsernameValidator allows "@", so the lookup must not route on it."""
        User = get_user_model()
        User.objects.create_user(
            username="handle@home", email="real@example.test", password=self.password, email_verified=True
        )

        response = self.post_json("/api/auth/login", {"identifier": "handle@home", "password": self.password})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["email"], "real@example.test")

    def test_signup_rejects_a_username_taken_in_any_case_or_column(self):
        User = get_user_model()
        User.objects.create_user(
            username="Rider", email="taken@example.test", password=self.password, email_verified=True
        )

        response = self.post_json(
            "/api/auth/signup",
            {"email": "fresh@example.test", "username": "rIdEr", "password": self.password},
        )
        self.assertEqual(response.status_code, 400)

        # A username equal to somebody's email address would make the login lookup ambiguous.
        response = self.post_json(
            "/api/auth/signup",
            {"email": "fresh@example.test", "username": "taken@example.test", "password": self.password},
        )
        self.assertEqual(response.status_code, 400)

        response = self.post_json(
            "/api/auth/signup",
            {"email": "fresh@example.test", "username": "not a username", "password": self.password},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_routes_are_authenticated_csrf_protected_and_owned(self):
        User = get_user_model()
        owner = User.objects.create_user(username="owner", email="owner@example.test", password=self.password)
        other = User.objects.create_user(username="other", email="other@example.test", password=self.password)
        owned_route = self.route(owner)
        other_route = self.route(other)

        response = self.client.get("/api/routes")
        self.assertEqual(response.status_code, 401)

        self.client.force_login(owner)
        response = self.client.get("/api/routes")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()], [str(owned_route.id)])

        response = self.client.get(f"/api/routes/{other_route.id}")
        self.assertEqual(response.status_code, 404)
        response = self.client.get(f"/api/routes/{other_route.id}/forecast?date=2026-09-12&time=08:00")
        self.assertEqual(response.status_code, 404)

        data = {
            "name": "New commute",
            "startLat": 47.5,
            "startLon": 9.3,
            "startName": "Start",
            "destLat": 47.6,
            "destLon": 9.4,
            "destName": "Destination",
            "scheduleCron": "0 8 * * 1",
            "scheduleDescription": "Monday at 08:00",
        }
        with patch("core.api.recurring_route.refresh_route_geometry", SimpleNamespace(aenqueue=AsyncMock())):
            response = self.post_json("/api/routes", data)
        self.assertEqual(response.status_code, 200)
        created = RecurringRoute.objects.get(id=response.json()["id"])
        self.assertEqual(created.owner, owner)
        self.assertEqual((created.start_point.x, created.start_point.y), (9.3, 47.5))
        self.assertEqual((created.destination_point.x, created.destination_point.y), (9.4, 47.6))
        self.assertEqual(response.json()["start_lat"], 47.5)
        self.assertEqual(response.json()["dest_lon"], 9.4)

        invalid = {**data, "startLat": 91}
        response = self.post_json("/api/routes", invalid)
        self.assertEqual(response.status_code, 422)

        response = self.client.delete(f"/api/routes/{owned_route.id}")
        self.assertEqual(response.status_code, 403)
        response = self.client.delete(f"/api/routes/{other_route.id}", **self.csrf_headers())
        self.assertEqual(response.status_code, 404)
        self.assertTrue(RecurringRoute.objects.filter(id=other_route.id).exists())

    def test_password_reset_requires_a_valid_one_time_token(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="rider", email="rider@example.test", password=self.password, email_verified=True
        )

        response = self.post_json("/api/auth/password-reset", {"email": user.email})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)

        new_password = "Different correct battery staple 2026!"
        response = self.post_json(
            "/api/auth/password-reset/confirm",
            {
                "uid": urlsafe_base64_encode(str(user.pk).encode()),
                "token": default_token_generator.make_token(user),
                "password": new_password,
            },
        )
        self.assertEqual(response.status_code, 200)
        response = self.post_json("/api/auth/login", {"email": user.email, "password": self.password})
        self.assertEqual(response.status_code, 401)
        response = self.post_json("/api/auth/login", {"email": user.email, "password": new_password})
        self.assertEqual(response.status_code, 200)


class SimplifyPathTests(SimpleTestCase):
    """The route shape reduction behind the list thumbnails."""

    @staticmethod
    def _line(n: int) -> list[list[float]]:
        # A zig-zag, so Douglas-Peucker cannot collapse it to a straight line.
        return [[9.0 + i / 1000, 47.0 + (i % 2) / 1000] for i in range(n)]

    def test_short_line_is_kept_whole(self):
        line = self._line(10)
        path, index_map = simplify_path(line, [{"idx": 0}, {"idx": 9}])
        self.assertEqual(path, line)
        self.assertEqual(index_map[0], 0)
        self.assertEqual(index_map[9], 9)

    def test_long_line_is_reduced_but_keeps_every_sample_vertex(self):
        line = self._line(5000)
        sample_idx = list(range(0, 5000, 250))
        path, index_map = simplify_path(line, [{"idx": i} for i in sample_idx])

        self.assertLessEqual(len(path), MAX_THUMBNAIL_VERTICES)
        self.assertGreaterEqual(len(path), 2)
        for i in sample_idx:
            self.assertIn(i, index_map)
            # The mapped vertex must be the same coordinate, not merely a nearby one.
            self.assertEqual(path[index_map[i]], line[i])

    def test_mapped_indices_preserve_order_and_endpoints(self):
        line = self._line(3000)
        sample_idx = [0, 700, 1500, 2999]
        path, index_map = simplify_path(line, [{"idx": i} for i in sample_idx])

        mapped = [index_map[i] for i in sample_idx]
        self.assertEqual(mapped, sorted(mapped))
        self.assertEqual(path[0], line[0])
        self.assertEqual(path[-1], line[-1])

    def test_empty_polyline_is_handled(self):
        self.assertEqual(simplify_path([], []), ([], {}))


class RouteThumbnailTests(TestCase):
    """compute_route_thumbnail must read warm cells only and never invent a value."""

    def setUp(self):
        self.departure = next_departure("0 8 * * *")
        self.polyline = [[9.0 + i / 1000, 47.0 + (i % 2) / 1000] for i in range(40)]
        self.sample_points = [
            {
                "lat": self.polyline[i][1],
                "lon": self.polyline[i][0],
                "lat_r": round(self.polyline[i][1], 2),
                "lon_r": round(self.polyline[i][0], 2),
                "elapsed_s": i * 600,
                "idx": i,
            }
            for i in (0, 10, 20, 30)
        ]
        self.route = RecurringRoute.objects.create(
            name="Commute",
            start_point=route_point(47.0, 9.0),
            start_name="Start",
            destination_point=route_point(47.001, 9.039),
            dest_name="Destination",
            schedule_cron="0 8 * * *",
            schedule_description="Daily at 08:00",
            polyline=route_line(self.polyline),
            sample_points=self.sample_points,
            total_seconds=1800,
            total_distance_m=8000.0,
        )

    def _warm_cell(self, sp: dict, source: str = "open-meteo") -> None:
        """Store a cell covering the whole departure day at the sample point's grid cell."""
        times = [f"{self.departure.date().isoformat()}T{h:02d}:00" for h in range(24)]
        ForecastCell.objects.update_or_create(
            lat_r=sp["lat_r"],
            lon_r=sp["lon_r"],
            day_key=self.departure.date(),
            source=source,
            defaults={
                "forecast_days": 16,
                "data": {
                    "hourly": {
                        "time": times,
                        "temperature_2m": [15.0] * 24,
                        "precipitation": [0.4] * 24,
                        "wind_speed_10m": [12.0] * 24,
                        "wind_direction_10m": [90.0] * 24,
                    }
                },
            },
        )

    @staticmethod
    def _no_network():
        """Patch the fetching accessors where weather.py binds them, not where they live.

        weather.py imports both names into its own namespace, so patching core.grid.* would
        not intercept the call and the test would pass while still spending API requests.
        """
        return (
            patch("core.weather.get_or_fetch_forecast_cell", AsyncMock(side_effect=AssertionError("fetched!"))),
            patch("core.weather.get_or_fetch_ensemble_cell", AsyncMock(side_effect=AssertionError("fetched!"))),
        )

    def _compute(self):
        forecast_patch, ensemble_patch = self._no_network()
        with forecast_patch, ensemble_patch:
            return async_to_sync(compute_route_thumbnail)(self.route)

    def test_warm_cells_produce_scoreable_samples_without_fetching(self):
        for sp in self.sample_points:
            self._warm_cell(sp)

        thumb = self._compute()

        self.assertEqual(thumb["departure"], self.departure.isoformat())
        self.assertEqual(len(thumb["samples"]), len(self.sample_points))
        self.assertTrue(all(s is not None for s in thumb["samples"]))
        for entry in thumb["samples"]:
            self.assertEqual(entry["temp"], 15.0)
            self.assertEqual(entry["rain_mm"], 0.4)
            # The vertex index must address a real point on the reduced path.
            self.assertLess(entry["i"], len(thumb["path"]))
            self.assertIn("headwind", entry)

    def test_cold_cells_stay_none_rather_than_guessed(self):
        # Warm only the first sample point; the rest have no data at all.
        self._warm_cell(self.sample_points[0])

        thumb = self._compute()

        self.assertIsNotNone(thumb["samples"][0])
        self.assertEqual([s for s in thumb["samples"][1:]], [None, None, None])

    def test_openweathermap_cells_are_found_too(self):
        """A cell laid down by the OWM fallback is real data; it must not read as a miss."""
        owm_hourly = [
            {
                "dt": int(
                    datetime.combine(self.departure.date(), datetime.min.time()).replace(hour=h, tzinfo=UTC).timestamp()
                ),
                "temp": 9.0,
                "rain": 1.2,
                "wind_speed": 4.0,
                "wind_deg": 180,
            }
            for h in range(24)
        ]
        for sp in self.sample_points:
            ForecastCell.objects.create(
                lat_r=sp["lat_r"],
                lon_r=sp["lon_r"],
                day_key=self.departure.date(),
                source="openweathermap",
                forecast_days=16,
                data={"hourly": owm_hourly},
            )

        thumb = self._compute()

        self.assertTrue(any(s is not None for s in thumb["samples"]))

    def test_a_cold_pass_does_not_overwrite_a_good_thumbnail(self):
        """Cells expire every 2 h, so most passes find nothing warm.

        Overwriting each time would leave the glyph grey almost permanently.
        """
        for sp in self.sample_points:
            self._warm_cell(sp)
        async_to_sync(_refresh_route_thumbnail_async)(str(self.route.id))
        self.route.refresh_from_db()
        self.assertEqual(_known_samples(self.route.thumbnail), len(self.sample_points))

        # Everything goes stale; the next scheduled pass finds no usable cell.
        ForecastCell.objects.all().delete()
        async_to_sync(_refresh_route_thumbnail_async)(str(self.route.id))

        self.route.refresh_from_db()
        self.assertEqual(_known_samples(self.route.thumbnail), len(self.sample_points))

    def test_a_new_departure_does_replace_the_thumbnail(self):
        """The no-regress rule must not pin a glyph to a ride that has already happened."""
        for sp in self.sample_points:
            self._warm_cell(sp)
        async_to_sync(_refresh_route_thumbnail_async)(str(self.route.id))
        self.route.refresh_from_db()

        # Pretend the stored glyph was built for a departure that has since passed.
        stored = dict(self.route.thumbnail)
        stored["departure"] = "2020-01-01T08:00:00+01:00"
        RecurringRoute.objects.filter(id=self.route.id).update(thumbnail=stored)
        ForecastCell.objects.all().delete()

        async_to_sync(_refresh_route_thumbnail_async)(str(self.route.id))
        self.route.refresh_from_db()
        self.assertEqual(self.route.thumbnail["departure"], self.departure.isoformat())
        self.assertEqual(_known_samples(self.route.thumbnail), 0)

    def test_losing_geometry_clears_the_stored_shape(self):
        """After start/destination change the old path is simply wrong; don't keep drawing it."""
        for sp in self.sample_points:
            self._warm_cell(sp)
        async_to_sync(_refresh_route_thumbnail_async)(str(self.route.id))

        RecurringRoute.objects.filter(id=self.route.id).update(sample_points=None, polyline=None)
        async_to_sync(_refresh_route_thumbnail_async)(str(self.route.id))

        self.route.refresh_from_db()
        self.assertIsNone(self.route.thumbnail)

    def test_route_without_geometry_has_no_thumbnail(self):
        self.route.sample_points = None
        self.route.polyline = None
        self.assertIsNone(self._compute())


class EntitlementTests(TestCase):
    """Free-tier limits, at each of the three places they have to hold."""

    password = "correct-horse-battery-staple"

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        User = get_user_model()
        self.user = User.objects.create_user(
            username="rider", email="rider@example.test", password=self.password, email_verified=True
        )
        self.client.force_login(self.user)

    def csrf_headers(self):
        self.client.get("/api/auth/session")
        return {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value}

    def make_pro(self, **overrides):
        return Subscription.objects.update_or_create(
            user=self.user,
            defaults={"plan": Plan.PRO, "status": "active", **overrides},
        )[0]

    # Sentinel, so owner=None can mean "an ownerless legacy route" rather than "default".
    UNSET = object()

    def add_route(self, name="Route", owner=UNSET, **overrides):
        return RecurringRoute.objects.create(
            owner=self.user if owner is self.UNSET else owner,
            name=name,
            start_point=route_point(47.5, 9.3),
            start_name="Start",
            destination_point=route_point(47.6, 9.4),
            dest_name="Destination",
            schedule_cron="0 8 * * 1",
            schedule_description="Monday at 08:00",
            **overrides,
        )

    def post_route(self, name="New"):
        data = {
            "name": name,
            "startLat": 47.5,
            "startLon": 9.3,
            "startName": "Start",
            "destLat": 47.6,
            "destLon": 9.4,
            "destName": "Destination",
            "scheduleCron": "0 8 * * 1",
            "scheduleDescription": "Monday at 08:00",
        }
        with patch("core.api.recurring_route.refresh_route_geometry", SimpleNamespace(aenqueue=AsyncMock())):
            return self.client.post(
                "/api/routes", data=json.dumps(data), content_type="application/json", **self.csrf_headers()
            )

    # -- route count ------------------------------------------------------

    def test_free_tier_is_capped_at_two_active_routes(self):
        self.assertEqual(FREE.max_routes, 2)
        self.assertEqual(self.post_route("first").status_code, 200)
        self.assertEqual(self.post_route("second").status_code, 200)

        refused = self.post_route("third")
        self.assertEqual(refused.status_code, 402)
        self.assertEqual(RecurringRoute.objects.filter(owner=self.user).count(), 2)

    def test_a_freed_slot_can_be_reused(self):
        """Guards the >= / > boundary: at the limit exactly, deleting one must let one in."""
        first = self.add_route("first")
        self.add_route("second")
        self.assertEqual(self.post_route("third").status_code, 402)

        self.client.delete(f"/api/routes/{first.id}", **self.csrf_headers())
        self.assertEqual(self.post_route("third").status_code, 200)

    def test_inactive_routes_do_not_consume_a_slot(self):
        self.add_route("first")
        self.add_route("archived", active=False)
        self.assertEqual(self.post_route("second").status_code, 200)

    def test_pro_tier_is_not_capped(self):
        self.make_pro()
        for i in range(4):
            self.assertEqual(self.post_route(f"route-{i}").status_code, 200)

    def test_expired_or_cancelled_pro_falls_back_to_free(self):
        for overrides in (
            {"status": "canceled"},
            {"current_period_end": datetime(2020, 1, 1, tzinfo=UTC)},
        ):
            with self.subTest(**overrides):
                self.make_pro(**overrides)
                self.assertEqual(entitlements_for_sync(self.user), FREE)

        self.make_pro(status="trialing", current_period_end=None)
        self.assertEqual(entitlements_for_sync(self.user), PRO)

    def test_account_without_a_subscription_row_is_free(self):
        self.assertEqual(entitlements_for_sync(self.user), FREE)

    # -- ensemble uncertainty --------------------------------------------

    def test_uncertainty_is_stripped_for_free_but_not_for_pro(self):
        sample = WeatherSample(
            lat=47.5,
            lon=9.3,
            elapsed_s=0,
            eta="2026-09-14T08:00:00",
            rain_mm=0.0,
            temp=15.0,
            wind_speed=10.0,
            wind_dir=90.0,
            headwind=5.0,
            crosswind=1.0,
            weather_desc="klar",
            pop=0.3,
            rain_if_wet=1.2,
            uncertainty=ForecastUncertainty(
                metrics={},
                models=[],
                requested_models=[],
                forecast_time="2026-09-14T08:00:00",
                fetched_at="2026-09-14T06:00:00",
            ),
        )
        self.assertFalse(FREE.ensemble_uncertainty)
        strip_uncertainty([sample])
        self.assertIsNone(sample.uncertainty)
        # The rain probability is not part of the paywall — a forecast app has to say
        # whether it might rain.
        self.assertEqual(sample.pop, 0.3)
        self.assertEqual(sample.rain_if_wet, 1.2)
        self.assertTrue(PRO.ensemble_uncertainty)

    # -- pre-warm fan-out (the actual API spend) -------------------------

    def test_prewarm_keeps_the_oldest_routes_of_a_downgraded_account(self):
        oldest = self.add_route("oldest")
        middle = self.add_route("middle")
        newest = self.add_route("newest")
        # Created together, so pin created_at explicitly to make "oldest" meaningful.
        for route, day in ((oldest, 1), (middle, 2), (newest, 3)):
            RecurringRoute.objects.filter(id=route.id).update(created_at=datetime(2026, 1, day, tzinfo=UTC))

        selected = async_to_sync(_prewarm_routes)()
        self.assertEqual([r.name for r in selected], ["oldest", "middle"])

    def test_prewarm_skips_ownerless_routes(self):
        self.add_route("orphan", owner=None)
        self.assertEqual(async_to_sync(_prewarm_routes)(), [])

    def test_prewarm_covers_every_route_for_pro(self):
        self.make_pro()
        for i in range(4):
            self.add_route(f"route-{i}")
        self.assertEqual(len(async_to_sync(_prewarm_routes)()), 4)


@override_settings(STRIPE_WEBHOOK_SECRET="whsec_test", STRIPE_SECRET_KEY="sk_test", STRIPE_PRICE_ID_PRO="price_test")
class StripeWebhookTests(TestCase):
    """The webhook is the only thing allowed to change a tier, so it has to be strict."""

    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(
            username="rider", email="rider@example.test", password="x", email_verified=True
        )
        self.subscription = Subscription.objects.create(user=self.user, stripe_customer_id="cus_123", plan=Plan.FREE)

    def post_event(self, event: dict):
        """Deliver an event, with signature verification stubbed to accept it."""
        with patch("core.api.billing.stripe.Webhook.construct_event", return_value=event):
            return self.client.post(
                "/api/billing/webhook",
                data=json.dumps({"ignored": True}),
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1,v1=deadbeef",
            )

    @staticmethod
    def subscription_event(event_id: str, event_type: str, **obj):
        return {
            "id": event_id,
            "type": event_type,
            "data": {"object": {"id": "sub_123", "customer": "cus_123", **obj}},
        }

    def test_an_invalid_signature_is_rejected_and_changes_nothing(self):
        with patch(
            "core.api.billing.stripe.Webhook.construct_event",
            side_effect=stripe.SignatureVerificationError("bad sig", "sig_header"),
        ):
            response = self.client.post(
                "/api/billing/webhook",
                data=json.dumps({"type": "customer.subscription.updated"}),
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1,v1=forged",
            )
        self.assertEqual(response.status_code, 400)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.plan, Plan.FREE)
        self.assertEqual(ProcessedStripeEvent.objects.count(), 0)

    def test_the_webhook_needs_no_csrf_token(self):
        """It is mounted outside the Ninja API precisely so Stripe's POST is not 403'd."""
        response = self.post_event(
            self.subscription_event("evt_csrf", "customer.subscription.created", status="active")
        )
        self.assertEqual(response.status_code, 200)

    def test_an_active_subscription_grants_pro(self):
        period_end = int(datetime(2031, 1, 1, tzinfo=UTC).timestamp())
        self.post_event(
            self.subscription_event(
                "evt_1", "customer.subscription.updated", status="active", current_period_end=period_end
            )
        )
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.plan, Plan.PRO)
        self.assertEqual(self.subscription.current_period_end, datetime(2031, 1, 1, tzinfo=UTC))
        self.assertEqual(entitlements_for_sync(self.user), PRO)

    def test_a_replayed_event_is_applied_only_once(self):
        active = self.subscription_event("evt_dupe", "customer.subscription.updated", status="active")
        self.assertEqual(self.post_event(active).status_code, 200)
        self.assertEqual(entitlements_for_sync(self.user), PRO)

        # Stripe redelivers the same id after someone has since cancelled: the stale
        # replay must not resurrect Pro.
        self.subscription.plan = Plan.FREE
        self.subscription.status = "canceled"
        self.subscription.save()

        response = self.post_event(active)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["duplicate"])
        self.assertEqual(entitlements_for_sync(self.user), FREE)
        self.assertEqual(ProcessedStripeEvent.objects.count(), 1)

    def test_deletion_downgrades_to_free(self):
        self.post_event(self.subscription_event("evt_a", "customer.subscription.updated", status="active"))
        self.assertEqual(entitlements_for_sync(self.user), PRO)

        self.post_event(self.subscription_event("evt_b", "customer.subscription.deleted", status="canceled"))
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.plan, Plan.FREE)
        self.assertEqual(entitlements_for_sync(self.user), FREE)

    def test_a_failed_payment_downgrades_to_free(self):
        self.post_event(self.subscription_event("evt_a", "customer.subscription.updated", status="active"))
        self.post_event(self.subscription_event("evt_c", "invoice.payment_failed"))
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, "past_due")
        self.assertEqual(entitlements_for_sync(self.user), FREE)

    def test_an_unknown_event_is_acknowledged_without_changing_anything(self):
        response = self.post_event(self.subscription_event("evt_x", "customer.created"))
        self.assertEqual(response.status_code, 200)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.plan, Plan.FREE)

    def test_checkout_completion_records_ids_but_does_not_grant_pro(self):
        """The success redirect is not evidence; the subscription events decide the tier."""
        self.post_event(
            {
                "id": "evt_checkout",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "customer": "cus_123",
                        "subscription": "sub_999",
                        "client_reference_id": str(self.user.pk),
                    }
                },
            }
        )
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.stripe_subscription_id, "sub_999")
        self.assertEqual(self.subscription.plan, Plan.FREE)


class BillingEndpointTests(TestCase):
    """The non-webhook billing endpoints."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        User = get_user_model()
        self.user = User.objects.create_user(
            username="rider", email="rider@example.test", password="x", email_verified=True
        )

    def test_entitlements_reports_the_free_tier_and_route_usage(self):
        self.client.force_login(self.user)
        payload = self.client.get("/api/billing/entitlements").json()
        self.assertEqual(payload["plan"], "free")
        self.assertEqual(payload["maxRoutes"], 2)
        self.assertEqual(payload["routeCount"], 0)
        self.assertFalse(payload["ensembleUncertainty"])
        self.assertFalse(payload["billingConfigured"])

    def test_checkout_requires_authentication(self):
        self.client.get("/api/auth/session")
        response = self.client.post(
            "/api/billing/checkout",
            data="{}",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value,
        )
        self.assertEqual(response.status_code, 401)

    def test_checkout_reports_unconfigured_billing_rather_than_failing_obscurely(self):
        self.client.force_login(self.user)
        self.client.get("/api/auth/session")
        response = self.client.post(
            "/api/billing/checkout",
            data="{}",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value,
        )
        self.assertEqual(response.status_code, 503)
