from dataclasses import replace

from django.test import SimpleTestCase

from core.api.recurring_route import _thumbnail_out
from core.jobs import wind_arrows_at_detail
from core.ride_quality import (
    RIDE_QUALITY,
    rain_impact,
    ride_score,
    score_band,
    score_sample,
    wind_effort,
    wind_effort_level,
    worst_ride_score,
)


def sample(**over):
    base = {
        "rain_mm": 0,
        "rain_rate_mm_h": 0,
        "precipitation_interval_s": 3600,
        "temp": 18,
        "headwind": 0,
        "wind_power_w": None,
    }
    return {**base, **over}


def score(**over) -> float:
    rq = ride_score(sample(**over))
    assert rq is not None
    return rq.score


class RideScoreTests(SimpleTestCase):
    def test_zero_for_a_dry_mild_tailwind_ride_and_one_for_the_worst(self):
        self.assertEqual(score(rain_rate_mm_h=0, temp=18, headwind=-12), 0)
        self.assertEqual(score(rain_rate_mm_h=8, temp=-10, headwind=45), 1)

    def test_rises_monotonically_with_rain(self):
        series = [score(rain_rate_mm_h=mm) for mm in (0, 0.2, 1, 2.5, 5, 9)]
        self.assertEqual(series, sorted(series))
        self.assertGreater(score(rain_rate_mm_h=3), score(rain_rate_mm_h=0.5))

    def test_rises_monotonically_with_headwind(self):
        series = [score(headwind=kmh) for kmh in (-20, 0, 5, 10, 20, 30, 50)]
        self.assertEqual(series, sorted(series))

    def test_rises_as_temperature_leaves_the_comfortable_band(self):
        for comfortable in (14, 18, 22):
            self.assertEqual(score(temp=comfortable), 0)
        self.assertGreater(score(temp=5), score(temp=12))
        self.assertGreater(score(temp=30), score(temp=24))
        self.assertGreater(score(temp=-10), score(temp=0))

    def test_a_tailwind_only_removes_the_penalty(self):
        self.assertEqual(score(headwind=-30), score(headwind=0))
        self.assertEqual(score(wind_power_w=-80), score(wind_power_w=0))

    def test_scores_the_wind_effort_when_known(self):
        # 10 km/h into a fast e-bike costs more than 20 km/h into a slow bike.
        fast = ride_score(sample(headwind=10, wind_power_w=231))
        slow = ride_score(sample(headwind=20, wind_power_w=90))
        self.assertEqual(fast.wind, 1)
        self.assertLess(slow.wind, fast.wind)
        self.assertEqual(ride_score(sample(headwind=30, wind_power_w=0)).wind, 0)

    def test_falls_back_to_the_headwind_curve_and_matches_it(self):
        self.assertEqual(ride_score(sample(headwind=20, wind_power_w=None)).wind, 0.65)
        self.assertEqual(ride_score(sample(headwind=20, wind_power_w=130)).wind, 0.65)
        self.assertEqual(ride_score(sample(headwind=None, wind_power_w=130)).wind, 0.65)
        self.assertIsNone(ride_score(sample(headwind=None, wind_power_w=None)))
        self.assertIsNone(ride_score(sample(headwind=float("nan"), wind_power_w=None)))

    def test_names_the_dominant_weighted_factor(self):
        self.assertEqual(ride_score(sample(rain_rate_mm_h=4)).worst, "rain")
        self.assertEqual(ride_score(sample(headwind=28)).worst, "wind")
        self.assertEqual(ride_score(sample(temp=-5)).worst, "temp")

    def test_derives_the_rate_from_the_accumulation_when_the_interval_is_known(self):
        # 0.5 mm over 15 min == 2 mm/h
        derived = ride_score(sample(rain_rate_mm_h=None, rain_mm=0.5, precipitation_interval_s=900))
        self.assertAlmostEqual(derived.rain, ride_score(sample(rain_rate_mm_h=2)).rain)

    def test_never_assumes_an_hourly_bucket(self):
        self.assertIsNone(ride_score(sample(rain_rate_mm_h=None, rain_mm=0.5, precipitation_interval_s=None)))
        self.assertIsNone(ride_score(sample(rain_rate_mm_h=None, rain_mm=0.5, precipitation_interval_s=0)))

    def test_only_names_a_cause_when_one_factor_dominates(self):
        self.assertIn("v. a. Regen", ride_score(sample(rain_rate_mm_h=3)).label)
        # Drizzle, headwind and cold together: wind is the largest term, but at well under
        # half the total it is not an honest culprit. Pinned weights, so tuning the live
        # config cannot change which factor is largest here.
        balanced = replace(RIDE_QUALITY, weights={"rain": 0.55, "wind": 0.25, "temp": 0.2}, sensitivity=1)
        mixed = ride_score(sample(rain_rate_mm_h=0.5, headwind=20, temp=8), balanced)
        self.assertEqual(mixed.worst, "wind")
        self.assertEqual(mixed.label, "gut")
        self.assertEqual(ride_score(sample()).label, "sehr gut")

    def test_bands(self):
        self.assertEqual([score_band(s) for s in (0, 0.19, 0.2, 0.5, 1)], [0, 0, 1, 2, 4])


class RideQualityConfigTests(SimpleTestCase):
    heavy_rain = sample(rain_rate_mm_h=6)
    # Derived from the live config, so tuning RIDE_QUALITY does not break these tests.
    rain_weight = RIDE_QUALITY.weights["rain"]

    def test_heavy_rain_alone_stops_at_the_rain_weight(self):
        self.assertAlmostEqual(ride_score(self.heavy_rain, replace(RIDE_QUALITY, sensitivity=1)).score, self.rain_weight)

    def test_a_heavier_weight_pushes_one_factor_to_the_dark_end(self):
        config = replace(RIDE_QUALITY, weights={**RIDE_QUALITY.weights, "rain": 1})
        rq = ride_score(self.heavy_rain, config)
        self.assertEqual(rq.score, 1)
        self.assertEqual(rq.label, "sehr schlecht · v. a. Regen")

    def test_sensitivity_bends_the_score_but_keeps_both_ends(self):
        w = self.rain_weight
        self.assertAlmostEqual(ride_score(self.heavy_rain, replace(RIDE_QUALITY, sensitivity=2)).score, w**0.5)
        self.assertAlmostEqual(ride_score(self.heavy_rain, replace(RIDE_QUALITY, sensitivity=0.5)).score, w**2)
        self.assertEqual(ride_score(sample(), replace(RIDE_QUALITY, sensitivity=2)).score, 0)

    def test_the_cause_comes_from_the_unbent_shares(self):
        mixed = sample(rain_rate_mm_h=0.5, headwind=20, temp=8)
        balanced = replace(RIDE_QUALITY, weights={"rain": 0.55, "wind": 0.25, "temp": 0.2}, sensitivity=1)
        harsh = ride_score(mixed, replace(balanced, sensitivity=3))
        self.assertAlmostEqual(harsh.worst_share, ride_score(mixed, balanced).worst_share)
        self.assertNotIn("v. a.", harsh.label)


class RainImpactTests(SimpleTestCase):
    """Rain = the worse of the main run and the ensemble's chance × amount-if-wet."""

    # 2 mm/h sits on RAIN_CURVE between (1, 0.5) and (2.5, 0.8).
    CURVE_2MM = 0.5 + (2 - 1) / 1.5 * 0.3

    def test_a_chance_of_rain_counts_even_when_the_main_run_is_dry(self):
        dry_but_risky = sample(rain_rate_mm_h=0, pop=0.3, rain_if_wet=2)
        default = replace(RIDE_QUALITY, rain_risk_aversion=2)
        self.assertAlmostEqual(rain_impact(dry_but_risky, default), self.CURVE_2MM * 0.3**0.5)
        self.assertAlmostEqual(rain_impact(dry_but_risky, replace(RIDE_QUALITY, rain_risk_aversion=1)), self.CURVE_2MM * 0.3)
        self.assertEqual(ride_score(dry_but_risky, default).rain, rain_impact(dry_but_risky, default))

    def test_the_main_run_wins_when_it_is_worse(self):
        wet_main = sample(rain_rate_mm_h=3, pop=0.1, rain_if_wet=0.5)
        self.assertAlmostEqual(rain_impact(wet_main), rain_impact(sample(rain_rate_mm_h=3)))

    def test_more_risk_aversion_never_lowers_the_impact(self):
        risky = sample(pop=0.3, rain_if_wet=2)
        series = [rain_impact(risky, replace(RIDE_QUALITY, rain_risk_aversion=a)) for a in (1, 1.5, 2, 4)]
        self.assertEqual(series, sorted(series))
        # A non-positive aversion has no meaningful curve and falls back to the expected value.
        self.assertAlmostEqual(rain_impact(risky, replace(RIDE_QUALITY, rain_risk_aversion=0)), self.CURVE_2MM * 0.3)

    def test_no_chance_or_no_amount_adds_nothing(self):
        self.assertEqual(rain_impact(sample(pop=0, rain_if_wet=4)), 0)
        # A pop without an amount (the OWM fallback) leaves the main run to decide.
        self.assertEqual(rain_impact(sample(rain_rate_mm_h=0, pop=0.8, rain_if_wet=None)), 0)

    def test_the_ensemble_alone_is_enough_to_score(self):
        only_ensemble = sample(rain_rate_mm_h=None, rain_mm=None, precipitation_interval_s=None, pop=0.5, rain_if_wet=1)
        self.assertIsNotNone(ride_score(only_ensemble))
        self.assertIsNone(rain_impact(sample(rain_rate_mm_h=None, precipitation_interval_s=None)))


class WindEffortTests(SimpleTestCase):
    def test_levels_follow_the_wind_curve_breakpoints(self):
        cases = {-20: "Wind hilft", 0.4: "keiner", 1: "niedrig", 49: "niedrig", 50: "mittel", 129: "mittel",
                 130: "hoch", 229: "hoch", 230: "sehr hoch"}
        for watts, level in cases.items():
            self.assertEqual(wind_effort_level(watts), level, watts)
        self.assertIsNone(wind_effort_level(None))
        self.assertIsNone(wind_effort_level(float("nan")))

    def test_effort_share_for_arrow_size(self):
        self.assertEqual(wind_effort(None), 0)
        self.assertEqual(wind_effort(-50), 0)
        self.assertAlmostEqual(wind_effort(115), 0.5)
        self.assertEqual(wind_effort(1000), 1)


class ServedRideQualityTests(SimpleTestCase):
    """The scores reach the client only through what the API serves, computed on read."""

    def test_score_sample_adds_the_served_fields_without_touching_the_stored_dict(self):
        stored = sample(rain_rate_mm_h=3, wind_power_w=140)
        served = score_sample(stored)
        self.assertAlmostEqual(served["ride_score"], ride_score(stored).score, places=4)
        self.assertIn("Regen", served["ride_label"])
        self.assertEqual(served["wind_effort_level"], "hoch")
        self.assertNotIn("ride_score", stored)

    def test_unscorable_sample_is_served_as_none(self):
        served = score_sample(sample(headwind=None))
        self.assertIsNone(served["ride_score"])
        self.assertIsNone(served["ride_label"])

    def test_thumbnail_serves_the_worst_sample_and_no_raw_weather(self):
        blob = {
            "departure": "2026-09-14T08:00:00+02:00",
            "path": [[9.0, 47.0], [9.1, 47.1]],
            "samples": [{"i": 0, **sample()}, None, {"i": 1, **sample(rain_rate_mm_h=4)}],
            "computed_at": "2026-09-14T07:00:00+02:00",
        }
        out = _thumbnail_out(blob).model_dump()
        self.assertNotIn("samples", out)
        self.assertEqual(out["ride_score"], round(worst_ride_score(blob["samples"]).score, 4))
        self.assertIn("v. a. Regen", out["ride_label"])

    def test_thumbnail_without_scorable_samples_has_no_verdict(self):
        out = _thumbnail_out({"departure": None, "path": [], "samples": [None]})
        self.assertIsNone(out.ride_score)
        self.assertIsNone(out.ride_label)
        self.assertIsNone(_thumbnail_out(None))

    def test_wind_arrows_carry_the_effort_level_and_share(self):
        segment = {"lat": 47, "lon": 9, "bearing": 0, "wind_speed": 20, "wind_dir": 0, "wind_coverage": 1,
                   "start_m": 0, "wind_power_w": 115}
        [arrow] = wind_arrows_at_detail({"wind_segments": [segment]}, "full")
        self.assertEqual(arrow["wind_effort_level"], "mittel")
        self.assertAlmostEqual(arrow["wind_effort"], 0.5)
