"""Wind physics, missing-data boundaries and geometry/timing contracts."""

import json
import math
from datetime import datetime
from time import perf_counter

from django.test import SimpleTestCase

from .forecast_schemas import RouteWeatherOut
from .geo import vertex_distances
from .grid import _from_open_meteo, _from_owm
from .schedule import LOCAL_TZ
from .test_uncertainty import ETA, FETCHED, ensemble_data
from .test_uncertainty import sample as weather_sample
from .uncertainty import extract_uncertainty
from .weather import _summarize
from .wind import (
    SampleWind,
    WeightedDirection,
    compute_wind_profile,
    felt_temperature,
    ground_bucket,
    normalize_wind,
    project_support,
    project_wind,
    resolve_vertex_times,
    sample_airspeed,
    valid_vertex_times,
    wind_power,
)


def fixture(coords, speed=20, wind_speed=30, wind_dir=0, anchor_indices=None):
    distances = vertex_distances(coords)
    times = [d * 3.6 / speed for d in distances]
    indices = anchor_indices if anchor_indices is not None else list(range(len(coords)))
    points = [{"idx": i, "elapsed_s": times[i]} for i in indices]
    wind = [normalize_wind(wind_speed, wind_dir) for _ in points]
    return points, wind, times


def profile(coords, *, wind_dir=0, wind_speed=30, speed=20, **kwargs):
    points, wind, times = fixture(coords, speed, wind_speed, wind_dir)
    return compute_wind_profile(
        coords, points, wind, resolve_vertex_times(coords, points, times), vertex_distances(coords)[-1], **kwargs
    )


class WindTests(SimpleTestCase):
    def test_provider_missing_wind_is_not_north_or_calm(self):
        eta = datetime(2026, 9, 12, 12, tzinfo=LOCAL_TZ)
        wall_time = eta.replace(tzinfo=None).isoformat()
        meteo = _from_open_meteo({"hourly": {"time": [wall_time], "temperature_2m": [18]}}, eta)
        owm = _from_owm({"hourly": [{"dt": eta.timestamp(), "temp": 18}]}, eta)
        for sample in (meteo, owm):
            self.assertIsNone(sample["wind_speed"])
            self.assertIsNone(sample["wind_dir"])
            self.assertEqual(sample["temp"], 18)

    def test_aware_departure_matches_provider_local_time(self):
        data = {
            "hourly": {
                "time": ["2026-09-13T12:00:00", "2026-09-13T13:00:00"],
                "wind_speed_10m": [10, 20],
                "wind_direction_10m": [0, 90],
            }
        }
        self.assertEqual(_from_open_meteo(data, datetime.fromisoformat("2026-09-13T10:00:00Z"))["wind_speed"], 10)

    def test_ensemble_projects_members_before_quantiles(self):
        data = ensemble_data()
        data["hourly"]["wind_direction_10m_a"] = [270, 270]
        data["hourly"]["wind_direction_10m_member01_a"] = [270, 270]
        support = [WeightedDirection(0, 1, 1), WeightedDirection(0, -1, 1)]
        result = extract_uncertainty(data, ETA, None, FETCHED, ["a", "b"], wind_support=support)
        self.assertEqual(result.metrics["headwind"].median, 0)
        self.assertEqual(result.metrics["crosswind"].median, 15)
        empty = extract_uncertainty(data, ETA, None, FETCHED, ["a", "b"], wind_support=[])
        self.assertIsNone(empty.metrics["crosswind"].median)
        self.assertIsNotNone(empty.pop)

    def test_legacy_payload_defaults(self):
        samples = [weather_sample()]
        forecast = RouteWeatherOut(
            line=[[0, 0], [0, 0.01]],
            total_seconds=200,
            total_distance_m=1000,
            samples=samples,
            summary=_summarize(samples, "open-meteo"),
        )
        self.assertEqual(forecast.wind_segments, [])
        self.assertIsNone(forecast.summary.wind_distribution)

    def test_cardinal_crosswind_sign_and_support_absolute(self):
        east = normalize_wind(10, 90)
        west = normalize_wind(10, 270)
        self.assertAlmostEqual(project_wind(east, 0)[1], 10)
        self.assertAlmostEqual(project_wind(west, 0)[1], -10)
        h, c = project_support(west, [WeightedDirection(0, 1, 1), WeightedDirection(0, -1, 1)])
        self.assertAlmostEqual(h, 0)
        self.assertAlmostEqual(c, 10)

    def test_local_apparent_before_averaging_and_out_and_back_buckets(self):
        result = profile([[0, 0], [0, 0.01], [0, 0]])
        d = result.distribution
        self.assertAlmostEqual(d["mean_felt_speed"], 30)
        self.assertAlmostEqual(d["max_felt_speed"], 50)
        self.assertAlmostEqual(d["headwind_m"], result.total_distance_m / 2)
        self.assertAlmostEqual(d["tailwind_m"], result.total_distance_m / 2)
        self.assertEqual(d["unknown_m"], 0)

    def test_calm_and_matching_tailwind(self):
        calm = profile([[0, 0], [0, 0.01]], wind_speed=0)
        self.assertAlmostEqual(calm.distribution["mean_felt_speed"], 20)
        self.assertAlmostEqual(calm.distribution["calm_m"], calm.total_distance_m)
        tail = profile([[0, 0], [0, 0.01]], wind_speed=20, wind_dir=180)
        self.assertAlmostEqual(tail.distribution["mean_felt_speed"], 0)
        self.assertTrue(all(s["felt_angle"] is None for s in tail.segments))
        self.assertAlmostEqual(tail.distribution["tailwind_m"], tail.total_distance_m)

    def test_missing_middle_anchor_is_not_bridged(self):
        coords = [[0, 0], [0, 0.01], [0, 0.02]]
        points, wind, times = fixture(coords)
        wind[1] = None
        result = compute_wind_profile(coords, points, wind, resolve_vertex_times(coords, points, times), 2000)
        self.assertAlmostEqual(result.distribution["unknown_m"], 2000)
        self.assertTrue(all(s["felt_speed"] is None for s in result.segments))
        self.assertEqual(result.samples[0].coverage, 0)
        self.assertEqual(result.samples[0].headwind, 30)

    def test_missing_north_calm_and_invalid_numbers(self):
        self.assertIsNotNone(normalize_wind(10, 0))
        self.assertIsNone(normalize_wind(10, None))
        self.assertIsNotNone(normalize_wind(0, None))
        for invalid in (None, True, -1, float("nan"), float("inf")):
            self.assertIsNone(normalize_wind(invalid, 0))

    def test_vector_interpolation_wrap_and_opposition(self):
        coords = [[0, 0], [0, 0.0005]]
        profile = fixture(coords)
        points, times = profile[0], profile[2]
        for directions in ((350, 10), (0, 180)):
            winds = [normalize_wind(10, d) for d in directions]
            result = compute_wind_profile(coords, points, winds, resolve_vertex_times(coords, points, times), 50)
            midpoint = result.segments[0]
            if directions == (350, 10):
                self.assertAlmostEqual(midpoint["wind_speed"], 10 * math.cos(math.radians(10)))
                self.assertAlmostEqual(midpoint["wind_dir"] % 360, 0)
            else:
                self.assertAlmostEqual(midpoint["wind_speed"], 0)
                self.assertIsNone(midpoint["wind_dir"])

    def test_timing_fallback_and_unavailable_ground_wind(self):
        coords = [[0, 0], [0, 0.001], [0, 0.002]]
        points, wind, times = fixture(coords, anchor_indices=[0, 2])
        self.assertTrue(valid_vertex_times(coords, times))
        fallback = resolve_vertex_times(coords, points, [0, 0, 0])
        self.assertEqual(fallback.source, "sample-interpolation")
        self.assertAlmostEqual(fallback.values[1], times[1])
        for point in points:
            point["elapsed_s"] = 0
        missing = resolve_vertex_times(coords, points)
        result = compute_wind_profile(coords, points, wind, missing, 200)
        self.assertEqual(result.distribution["timing_source"], "unavailable")
        self.assertIsNone(result.distribution["mean_felt_speed"])
        self.assertAlmostEqual(result.distribution["headwind_m"], 200)

    def test_empty_repeated_and_boundary_cases(self):
        for coords in ([], [[0, 0]], [[0, 0], [0, 0]]):
            result = compute_wind_profile(coords, [], [], resolve_vertex_times(coords, []), 10)
            self.assertEqual(result.distribution["unknown_m"], 10)
            self.assertEqual(result.segments, [])
        for angle, expected in ((0, "headwind_m"), (45, "crosswind_m"), (135, "crosswind_m"), (180, "tailwind_m")):
            head, cross = project_wind(normalize_wind(10, angle), 0)
            self.assertEqual(ground_bucket(head, cross), expected)

    def test_tiny_reversed_kink_does_not_define_support(self):
        coords = [[0, 0], [0, 0.005], [0, 0.00498], [0, 0.01]]
        points, wind, times = fixture(coords, anchor_indices=[0, 2, 3])
        result = compute_wind_profile(coords, points, wind, resolve_vertex_times(coords, points, times), 1000)
        self.assertGreater(result.samples[1].headwind, 25)
        self.assertGreater(result.distribution["tailwind_m"], 0)

    def test_wind_power_grows_with_rider_speed(self):
        self.assertEqual(wind_power(20, 0, 0), 0)
        self.assertAlmostEqual(wind_power(20, 10, 0), 64.3, delta=0.1)
        self.assertAlmostEqual(wind_power(40, 10, 0), 231.5, delta=0.1)
        self.assertLess(wind_power(20, -10, 0), 0)
        self.assertGreater(wind_power(20, 0, 10), 0)

    def test_profile_wind_power_uses_support_speed_not_edge_speed(self):
        coords = [[0, 0], [0, 0.005], [0, 0.01]]
        distances = vertex_distances(coords)
        # First half at 10 km/h, second half at 50 km/h: the sample supports average both.
        times = [0.0, distances[1] * 3.6 / 10, distances[1] * 3.6 / 10 + (distances[2] - distances[1]) * 3.6 / 50]
        points = [{"idx": i, "elapsed_s": times[i]} for i in range(3)]
        wind = [normalize_wind(10, 0) for _ in points]
        result = compute_wind_profile(coords, points, wind, resolve_vertex_times(coords, points, times), distances[-1])
        middle = result.samples[1]
        lo, hi = distances[1] / 2, (distances[1] + distances[2]) / 2
        t_lo, t_hi = times[1] / 2, times[1] + (times[2] - times[1]) / 2
        expected = wind_power((hi - lo) * 3.6 / (t_hi - t_lo), 10, 0)
        self.assertAlmostEqual(middle.wind_power_w, expected, delta=0.5)
        self.assertLess(middle.wind_power_w, wind_power(50, 10, 0))
        self.assertAlmostEqual(result.distribution["max_wind_power_w"], max(s.wind_power_w for s in result.samples))
        self.assertTrue(all(s["wind_power_w"] is not None for s in result.segments))

    def test_felt_temperature_is_the_wind_chill_and_never_warmer(self):
        self.assertAlmostEqual(felt_temperature(0, 25), -5.9, delta=0.05)  # Environment Canada table: -6
        self.assertEqual(felt_temperature(5, 3), 5)  # below the formula's airspeed floor
        for temp in (-10, 0, 10, 20, 30):
            self.assertLessEqual(felt_temperature(temp, 40), temp)
        self.assertLess(felt_temperature(5, 40), felt_temperature(5, 20))

    def test_airspeed_is_riding_speed_plus_wind(self):
        self.assertIsNone(sample_airspeed(SampleWind(headwind=10, cross_abs_mean=0)))
        self.assertEqual(sample_airspeed(SampleWind(rider_speed=20)), 20)
        self.assertAlmostEqual(sample_airspeed(SampleWind(rider_speed=20, headwind=10, cross_abs_mean=0)), 30)
        self.assertAlmostEqual(sample_airspeed(SampleWind(rider_speed=20, headwind=-5, cross_abs_mean=0)), 15)
        self.assertAlmostEqual(sample_airspeed(SampleWind(rider_speed=20, headwind=0, cross_abs_mean=15)), 25)

    def test_profile_sets_the_support_riding_speed(self):
        result = profile([[0, 0], [0, 0.005], [0, 0.01]], speed=20)
        for sample in result.samples:
            self.assertAlmostEqual(sample.rider_speed, 20, delta=0.1)

    def test_wind_power_is_none_without_timing(self):
        coords = [[0, 0], [0, 0.001], [0, 0.002]]
        points, wind, _ = fixture(coords, anchor_indices=[0, 2])
        for point in points:
            point["elapsed_s"] = 0
        result = compute_wind_profile(coords, points, wind, resolve_vertex_times(coords, points), 200)
        self.assertTrue(all(s.wind_power_w is None for s in result.samples))
        self.assertTrue(all(s.rider_speed is None for s in result.samples))
        self.assertIsNotNone(result.samples[0].headwind)
        self.assertIsNone(result.distribution["max_wind_power_w"])
        self.assertTrue(all(s["wind_power_w"] is None for s in result.segments))

    def test_display_cap_convergence_conservation_and_disabled_segments(self):
        coords = [[0, 0], [0, 0.9]]
        points, winds, times = fixture(coords)
        winds[-1] = normalize_wind(15, 90)
        resolved = resolve_vertex_times(coords, points, times)
        start = perf_counter()
        result = compute_wind_profile(coords, points, winds, resolved, 100000)
        capped = compute_wind_profile(coords, points, winds, resolved, 100000, max_segments=20)
        finer = compute_wind_profile(coords, points, winds, resolved, 100000, integration_step_m=12.5)
        plain = compute_wind_profile(coords, points, winds, resolved, 100000, include_segments=False)
        self.assertEqual(len(result.segments), 500)
        self.assertEqual(result.distribution, capped.distribution)
        self.assertEqual(result.samples, plain.samples)
        self.assertEqual(plain.segments, [])
        self.assertIsNone(plain.distribution)
        self.assertAlmostEqual(
            sum(result.distribution[k] for k in ("headwind_m", "crosswind_m", "tailwind_m", "calm_m", "unknown_m")),
            100000,
        )
        self.assertLess(abs(result.distribution["mean_felt_speed"] - finer.distribution["mean_felt_speed"]), 0.1)
        for key in ("headwind_m", "crosswind_m", "tailwind_m"):
            self.assertLess(abs(result.distribution[key] - finer.distribution[key]), 500)
        payload = json.dumps(result.segments, allow_nan=False)
        print(f"100 km wind fixture: four profiles {perf_counter() - start:.3f}s; segments {len(payload)} bytes")
