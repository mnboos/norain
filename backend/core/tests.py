from datetime import date, datetime, timedelta, timezone

from django.test import SimpleTestCase, TestCase

from core.grid import (
    _ensemble_at,
    _from_open_meteo,
    _from_owm,
    _get_ensemble_cell_sync,
    _get_forecast_cell_sync,
    _nearest_index,
    extract_sample,
)
from core.models import EnsembleCell, ForecastCell
from core.weather import (
    _bearing_deg,
    _cumulative_times_s,
    _forecast_days,
    _sample_indices,
    _summarize,
    _wind_components,
)
from core.weather_schemas import RouteWeatherOut, RouteWeatherSummary, WeatherSample


class GeometryTests(SimpleTestCase):
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
        self.assertIsNone(_from_open_meteo(
            {"hourly": {}}, datetime.fromisoformat("2026-06-03T08:00")
        ))

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
        data = {
            "hourly": [
                {"dt": int(eta.timestamp()), "temp": 15.0, "wind_speed": 2.0, "wind_deg": 180}
            ]
        }
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
            annotation_texts = [
                a.get("text", "") for a in fig["layout"].get("annotations", [])
            ]
            self.assertTrue(
                any("Keine Wetterdaten" in t for t in annotation_texts),
                f"Expected 'Keine Wetterdaten' in annotations, got {annotation_texts}",
            )

    def test_figures_only_use_cartesian_traces(self):
        """Figures must stick to scatter/bar - the frontend registers only those."""
        from core.plotting import generate_forecast_figures

        samples = [
            WeatherSample(
                lat=47.5, lon=9.5, elapsed_s=0, eta="2026-06-20T14:00",
                rain_mm=0.0, temp=18.0, wind_speed=10.0, wind_dir=270.0,
                headwind=5.0, crosswind=2.0, weather_desc="klar",
            ),
            WeatherSample(
                lat=47.55, lon=9.55, elapsed_s=300, eta="2026-06-20T14:05",
                rain_mm=2.5, temp=17.0, wind_speed=12.0, wind_dir=180.0,
                headwind=10.0, crosswind=0.0, weather_desc="Regen",
            ),
            WeatherSample(
                lat=47.6, lon=9.6, elapsed_s=600, eta="2026-06-20T14:10",
                rain_mm=0.3, temp=19.0, wind_speed=8.0, wind_dir=90.0,
                headwind=0.0, crosswind=5.0, weather_desc="bewölkt",
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
            data={"hourly": {"time": [], "precipitation": []}},
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
