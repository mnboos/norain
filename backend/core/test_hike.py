"""The hike profile: accepted like the bike profiles, scored and routed as a walker."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from core.api.recurring_route import _thumbnail_out
from core.api.route_weather import check_routing_profile
from core.departures import comparison_view
from core.jobs import wind_arrows_at_detail
from core.ride_quality import (
    HIKE_RIDE_QUALITY,
    RIDE_QUALITY,
    config_for,
    ride_score,
    score_sample,
)
from core.road_prefs import RoadPrefs, is_penalty_only, road_prefs_model
from core.route_input import RoutingProfile
from core.tasks import _weather_prefs


def sample(**over):
    base = {
        "rain_mm": 0,
        "rain_rate_mm_h": 0,
        "precipitation_interval_s": 3600,
        "temp": 18,
        "headwind": 0,
        "wind_power_w": None,
        "wind_speed": 5,
        "wind_gust": 10,
    }
    return {**base, **over}


class HikeProfileTests(SimpleTestCase):
    def test_hike_is_a_routing_profile(self):
        self.assertEqual(check_routing_profile("hike"), "hike")
        self.assertEqual(RoutingProfile("hike"), RoutingProfile.HIKE)

    def test_config_per_profile(self):
        self.assertIs(config_for("hike"), HIKE_RIDE_QUALITY)
        for profile in ("bike", "ebike", "fast_ebike", None):
            self.assertIs(config_for(profile), RIDE_QUALITY)


class HikeScoringTests(SimpleTestCase):
    def test_a_headwind_effort_does_not_spoil_a_hike(self):
        windy_for_a_cyclist = sample(wind_power_w=230, headwind=30, wind_speed=20, wind_gust=28)
        self.assertEqual(ride_score(windy_for_a_cyclist, HIKE_RIDE_QUALITY).wind, 0)
        self.assertEqual(ride_score(windy_for_a_cyclist, RIDE_QUALITY).wind, 1)

    def test_gusts_spoil_a_hike(self):
        calm = ride_score(sample(wind_gust=20), HIKE_RIDE_QUALITY)
        gale = ride_score(sample(wind_gust=90), HIKE_RIDE_QUALITY)
        self.assertEqual(gale.wind, 1)
        self.assertGreater(gale.score, calm.score)

    def test_mean_wind_stands_in_for_a_missing_gust(self):
        rq = ride_score(sample(wind_gust=None, wind_speed=60), HIKE_RIDE_QUALITY)
        self.assertEqual(rq.wind, 1, "60 km/h mean wind ≈ 90 km/h gusts")

    def test_no_wind_at_all_still_scores_a_hike(self):
        rq = ride_score(sample(wind_gust=None, wind_speed=None, headwind=None), HIKE_RIDE_QUALITY)
        self.assertIsNotNone(rq)
        self.assertEqual(rq.wind, 0)

    def test_rain_and_frost_count_as_for_the_bike(self):
        wet = sample(rain_mm=5, rain_rate_mm_h=5)
        self.assertEqual(ride_score(wet, HIKE_RIDE_QUALITY).rain, ride_score(wet, RIDE_QUALITY).rain)
        icy = sample(temp=-3)
        self.assertEqual(ride_score(icy, HIKE_RIDE_QUALITY).frost, ride_score(icy, RIDE_QUALITY).frost)

    def test_a_hike_sample_has_no_wind_effort_level(self):
        served = score_sample(sample(wind_power_w=150), HIKE_RIDE_QUALITY)
        self.assertIsNone(served["wind_effort_level"])
        self.assertEqual(score_sample(sample(wind_power_w=150))["wind_effort_level"], "high")

    def test_hike_arrows_are_sized_by_the_wind_not_the_effort(self):
        segment = {
            "lat": 47,
            "lon": 9,
            "bearing": 0,
            "wind_speed": 40,
            "wind_gust": 60,
            "wind_dir": 0,
            "wind_coverage": 1,
            "start_m": 0,
            "wind_power_w": 115,
        }
        [arrow] = wind_arrows_at_detail({"wind_segments": [segment], "profile": "hike"}, "full")
        self.assertIsNone(arrow["wind_power_w"])
        self.assertIsNone(arrow["wind_effort_level"])
        self.assertGreater(arrow["wind_effort"], 0)
        [bike] = wind_arrows_at_detail({"wind_segments": [segment]}, "full")
        self.assertEqual(bike["wind_effort_level"], "medium")

    def test_thumbnail_is_scored_by_the_route_profile(self):
        blob = {"departure": None, "path": [], "samples": [sample(wind_power_w=230, headwind=30)]}
        self.assertLess(_thumbnail_out(blob, "hike").ride_score, _thumbnail_out(blob, "bike").ride_score)

    def test_departure_comparison_reads_the_stored_profile(self):
        stored = {
            "requested_time": "2099-06-20T08:00:00+02:00",
            "window_start": "2099-06-20T08:00:00+02:00",
            "window_end": "2099-06-20T08:00:00+02:00",
            "candidates": [
                {
                    "departure_time": "2099-06-20T08:00:00+02:00",
                    "arrival_time": "2099-06-20T10:00:00+02:00",
                    "complete": True,
                    "samples": [
                        {**sample(wind_power_w=230, headwind=30), "elapsed_s": 0, "sample_index": 0},
                        {**sample(wind_power_w=230, headwind=30), "elapsed_s": 600, "sample_index": 1},
                    ],
                }
            ],
        }
        bike = comparison_view(stored)["candidates"][0]["ride_score"]
        hike = comparison_view({**stored, "profile": "hike"})["candidates"][0]["ride_score"]
        self.assertLess(hike, bike)


class HikeRoutingTests(SimpleTestCase):
    def test_off_network_means_the_hiking_network(self):
        prefs = RoadPrefs(traffic="avoid_off_network")
        self.assertEqual(road_prefs_model(prefs, "hike")["priority"][0]["if"], "foot_network == MISSING")
        self.assertEqual(road_prefs_model(prefs)["priority"][0]["if"], "bike_network == MISSING")
        self.assertTrue(is_penalty_only(road_prefs_model(prefs, "hike")))

    def test_a_hike_never_routes_around_headwind(self):
        prefs = {"avoid_rain": True, "avoid_headwind": True}
        hike = _weather_prefs(SimpleNamespace(profile="hike", weather_prefs=prefs))
        self.assertEqual(hike, {"avoid_rain": True, "avoid_headwind": False})
        bike = _weather_prefs(SimpleNamespace(profile="bike", weather_prefs=prefs))
        self.assertTrue(bike["avoid_headwind"])
        self.assertTrue(prefs["avoid_headwind"], "the stored preferences are left alone")
