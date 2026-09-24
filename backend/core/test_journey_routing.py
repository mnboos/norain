"""Regressions for routed visits, geometry preservation and final-line limits."""

import asyncio
from dataclasses import replace
from itertools import pairwise
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from .journey_geometry import Limits, LineMeasure, check_limits, longest_visited_gaps, measured_geometry, repair_breaks
from .journey_planner import MAX_POI_SNAP_M, JourneyPlanner, RoutingBudget
from .journeys import rank_day
from .pois import PoiHit
from .weather import _fetch_route, _path_geometry, route_legs


def geometry(points, times=None):
    points = [list(p) for p in points]
    if times is None:
        from .geo import vertex_distances

        times = [d / 5 for d in vertex_distances(points)]
    result = measured_geometry(points, times)
    result["waypoint_indices"] = list(range(len(points)))
    return result


async def fake_geometry(profile, points, *args, **kwargs):
    # Every requested waypoint is identifiable, with intermediate vertices for pauses.
    coords, indices = [list(points[0])], [0]
    for a, b in pairwise(points):
        coords.extend([a[0] + (b[0] - a[0]) * i / 20, a[1] + (b[1] - a[1]) * i / 20] for i in range(1, 21))
        indices.append(len(coords) - 1)
    result = geometry(coords)
    result["waypoint_indices"] = indices
    return result


async def fake_legs(profile, legs, *args, **kwargs):
    result = []
    for points in legs:
        g = await fake_geometry(profile, points)
        result.append((g["total_seconds"], g["total_distance_m"]))
    return result


class MeasuredJourneyTests(SimpleTestCase):
    def test_later_slow_leg_gets_its_own_boundary(self):
        g = geometry([(8 + i * 0.01, 47) for i in range(6)], [0, 100, 200, 700, 1200, 1700])
        m = LineMeasure(g)
        self.assertEqual(m.boundary(0, Limits(600)), 2)
        self.assertEqual(m.boundary(2, Limits(600)), 3)
        breaks = repair_breaks(g, [], Limits(600))
        self.assertFalse(check_limits(g, breaks, Limits(), Limits(600))["legs"])
        self.assertEqual([b["index"] for b in breaks], [2, 3, 4])

    def test_boundary_pauses_have_final_positions_without_pois(self):
        g = geometry([(8 + i * 0.01, 47) for i in range(8)], [i * 100 for i in range(8)])
        breaks = repair_breaks(g, [], Limits(250))
        self.assertEqual([b["elapsed_s"] for b in breaks], [200, 400, 600])
        self.assertTrue(all(not b["pois"] for b in breaks))
        self.assertFalse(check_limits(g, breaks, Limits(), Limits(250))["legs"])

    def test_start_and_end_legs_are_validated_and_slack_is_only_for_day(self):
        g = geometry([(8, 47), (8.01, 47), (8.02, 47)], [0, 650, 1050])
        breaks = [{"index": 1}]
        result = check_limits(g, breaks, Limits(1000), Limits(600), last_day=True)
        self.assertNotIn("day", result)
        self.assertEqual(result["legs"], [{"leg": 1, "over_s": 50, "over_m": 0}])
        self.assertIn("day", check_limits(g, breaks, Limits(1000), Limits(600)))

    def test_only_visited_pois_close_gaps_and_each_category_counts(self):
        g = geometry([(8 + i * 0.01, 47) for i in range(4)], [0, 100, 800, 1000])
        events = [{"index": 1, "pois": [{"category": "drinking_water"}, {"category": "toilets"}]}]
        gaps = longest_visited_gaps(g, events, ["drinking_water", "toilets", "food"])
        self.assertEqual(gaps["drinking_water"]["s"], 900)
        self.assertEqual(gaps["toilets"]["s"], 900)
        self.assertEqual(gaps["food"]["s"], 1000)

    def test_new_and_legacy_gaps_rank_with_specific_overrun_reasons(self):
        rows = rank_day(
            [
                {
                    "id": "over",
                    "total_seconds": 100,
                    "leg_seconds": 600,
                    "leg_m": 1000,
                    "gaps": {"drinking_water": {"s": 900, "m": 1200}},
                    "limit_overruns": {"day": {"over_m": 500}, "legs": [{"leg": 2, "over_s": 60}]},
                },
                {"id": "ok", "total_seconds": 200, "leg_m": 1000, "gaps": {"toilets": 1500}},
            ]
        )
        self.assertTrue(rows[1]["recommended"])
        self.assertIn("Tageslimit: ~0.5 km zu weit", rows[0]["reasons"])
        self.assertIn("Etappe 2: ~1 min zu lang", rows[0]["reasons"])
        self.assertIn("Trinkwasser: 15 min / 1 km ohne", rows[0]["reasons"])

    def test_path_geometry_keeps_exact_visit_boundaries(self):
        raw = {
            "points": {"coordinates": [[8, 47], [8.01, 47], [8, 47], [8.02, 47]]},
            "distance": 4000,
            "time": 300000,
            "details": {"time": [[0, 3, 300000]], "leg_time": [[0, 2, 200000], [2, 3, 100000]]},
        }
        self.assertEqual(_path_geometry(raw, 300)["waypoint_indices"], [0, 2, 3])


class RoutedPlannerTests(SimpleTestCase):
    def planner(self, hits=None, legs=None, build=None, budget=None):
        return JourneyPlanner(
            "bike",
            None,
            budget or RoutingBudget(),
            build or fake_geometry,
            legs or fake_legs,
            AsyncMock(return_value=hits or []),
        )

    def test_two_alternatives_without_pois_are_preserved(self):
        async def run():
            build = AsyncMock(side_effect=AssertionError("unnecessary reroute"))
            planner = self.planner(build=build)
            paths = [geometry([(8, 47), (8.01, 47.01), (8.02, 47)]), geometry([(8, 47), (8.01, 46.99), (8.02, 47)])]
            result = [await planner.stage(p, [], [], Limits(600), Limits()) for p in paths]
            self.assertEqual(result[0]["geometry"], paths[0])
            self.assertEqual(result[1]["geometry"], paths[1])

        async_to_sync(run)()

    def window_poi(self):
        """A path (~1500 s) and a water tap 10 m off it inside the first leg's break window
        (Limits(600) ends that leg at vertex 7), so only the routed answer decides."""
        path = async_to_sync(fake_geometry)("bike", [(8, 47), (8.1, 47)])
        hit = PoiHit(
            osm_ref="n1",
            category="drinking_water",
            name="",
            lon=8.03,
            lat=47.0001,
            along_m=LineMeasure(path).meters[6],
            offset_m=10,
        )
        return path, hit

    def test_a_routable_window_poi_is_chosen(self):
        path, hit = self.window_poi()
        result = async_to_sync(self.planner([hit]).stage)(path, [], ["drinking_water"], Limits(600), Limits())
        self.assertEqual([d["osm_ref"] for d in result["detours"]], ["n1"])
        self.assertEqual([p["osm_ref"] for p in result["breaks"][0]["pois"]], ["n1"])

    def test_short_day_still_visits_every_requested_category(self):
        path, water = self.window_poi()
        toilet = replace(water, osm_ref="toilet", category="toilets", lon=8.07)
        for limits in (Limits(meters=25000), Limits(seconds=7200), Limits()):
            with self.subTest(limits=limits):
                planner = self.planner([water, toilet])
                result = async_to_sync(planner.stage)(path, [], ["drinking_water", "toilets"], limits, Limits())
                self.assertEqual({d["category"] for d in result["detours"]}, {"drinking_water", "toilets"})
                self.assertEqual(
                    {p["category"] for b in result["breaks"] for p in b["pois"]}, {"drinking_water", "toilets"}
                )
                self.assertEqual(len(result["via_points"]), 2)
                self.assertFalse(result["limit_overruns"]["legs"])

    def test_useful_stop_beats_cheaper_candidates_at_start_before_prefilter(self):
        path, water = self.window_poi()
        early = [replace(water, osm_ref=f"early{i}", lon=8.002 + i * 0.001, offset_m=0) for i in range(8)]
        useful = replace(water, osm_ref="useful", lon=8.07, lat=47.0001, offset_m=12)
        result = async_to_sync(self.planner([*early, useful]).stage)(
            path, [], ["drinking_water"], Limits(meters=25000), Limits()
        )
        self.assertEqual([p["osm_ref"] for p in result["detours"]], ["useful"])

    def test_failed_preferred_geometry_widens_search(self):
        path, water = self.window_poi()
        preferred = replace(water, osm_ref="preferred", lon=8.07)
        fallback = replace(water, osm_ref="fallback", lon=8.05)

        async def build(profile, points, *args, **kwargs):
            if any(abs(p[0] - preferred.lon) < 1e-8 and abs(p[1] - preferred.lat) < 1e-8 for p in points[1:-1]):
                raise ValueError("no accessible entrance")
            return await fake_geometry(profile, points)

        result = async_to_sync(self.planner([preferred, fallback], build=build).stage)(
            path, [], ["drinking_water"], Limits(meters=25000), Limits()
        )
        self.assertEqual([p["osm_ref"] for p in result["detours"]], ["fallback"])

    def test_nearby_facilities_are_both_routed_after_location_selection(self):
        path, water = self.window_poi()
        water = replace(water, lon=8.07)
        nearby = replace(water, osm_ref="nearby_wc", category="toilets", lon=8.072, lat=47.0002)
        other = replace(water, osm_ref="other_wc", category="toilets", lon=8.082, lat=47, offset_m=0)
        result = async_to_sync(self.planner([water, nearby, other]).stage)(
            path, [], ["drinking_water", "toilets"], Limits(meters=25000), Limits()
        )
        self.assertEqual({p["osm_ref"] for p in result["detours"]}, {water.osm_ref, nearby.osm_ref})
        self.assertIn([nearby.lon, nearby.lat], result["geometry"]["polyline"])
        self.assertIn([water.lon, water.lat], result["geometry"]["polyline"])

    def test_short_day_does_not_claim_unreachable_requested_pois(self):
        path, water = self.window_poi()
        planner = self.planner([water], legs=AsyncMock(side_effect=lambda p, legs, *a, **k: [None] * len(legs)))
        result = async_to_sync(planner.stage)(path, [], ["drinking_water"], Limits(meters=25000), Limits())
        self.assertFalse(result["via_points"])
        self.assertFalse(result["breaks"])
        self.assertEqual(result["gaps"]["drinking_water"]["m"], round(LineMeasure(path).meters[-1], 1))
        ranked = rank_day([{**result, "id": "missing", "total_seconds": path["total_seconds"]}])
        self.assertIn("Kein erreichbarer Stopp für Trinkwasser gefunden.", ranked[0]["reasons"])

    def test_failed_nearby_poi_does_not_satisfy_category(self):
        path, hit = self.window_poi()
        planner = self.planner([hit], legs=AsyncMock(side_effect=lambda p, legs, *a, **k: [None] * len(legs)))
        result = async_to_sync(planner.stage)(path, [], ["drinking_water"], Limits(600), Limits())
        self.assertFalse(result["detours"])
        self.assertEqual(result["gaps"]["drinking_water"]["s"], round(path["vertex_times"][-1]))
        self.assertTrue(all(not b["pois"] for b in result["breaks"]))

    def test_a_poi_near_by_air_but_far_by_road_is_never_chosen(self):
        path, hit = self.window_poi()
        # 10 m off the line, but the only way there is a 5 km loop each way (across the river).
        far = AsyncMock(side_effect=lambda p, legs, *a, **k: [(60.0, 5000.0)] * len(legs))
        result = async_to_sync(self.planner([hit], legs=far).stage)(path, [], ["drinking_water"], Limits(600), Limits())
        self.assertFalse(result["detours"])
        self.assertTrue(all(not b["pois"] for b in result["breaks"]))

    def test_budget_exhaustion_never_selects_unchecked_poi(self):
        async def run():
            path = await fake_geometry("bike", [(8, 47), (8.1, 47)])
            hit = PoiHit(osm_ref="n1", category="food", name="", lon=8.03, lat=47, along_m=2000, offset_m=0)
            legs = AsyncMock(side_effect=AssertionError("budget exhausted"))
            planner = self.planner([hit], legs=legs, budget=RoutingBudget(used=10000))
            result = await planner.stage(path, [], ["food"], Limits(600), Limits())
            self.assertFalse(result["detours"])
            self.assertTrue(result["breaks"])

        async_to_sync(run)()

    def test_insertion_preserves_prefix_suffix_and_mandatory_occurrence(self):
        async def run():
            path = await fake_geometry("bike", [(8, 47), (8.2, 47)])
            m = LineMeasure(path)
            events = [{"index": 10, "point": path["polyline"][10], "mandatory": True}]
            hit = PoiHit(
                osm_ref="n1", category="food", name="", lon=8.11, lat=47.002, along_m=m.meters[11], offset_m=200
            )
            planner = self.planner()
            choices = await planner.candidates(path, events, [hit], 0, 19, Limits())
            choice = choices[0]
            inserted, mapped, visit = await planner.insert(path, events, choice, 0, Limits(), is_break=True)
            self.assertEqual(inserted["polyline"][: choice["a"] + 1], path["polyline"][: choice["a"] + 1])
            self.assertEqual(
                inserted["polyline"][-(len(path["polyline"]) - choice["b"]) :], path["polyline"][choice["b"] :]
            )
            mandatory = next(e for e in mapped if e.get("mandatory"))
            self.assertEqual(inserted["polyline"][mandatory["index"]], events[0]["point"])
            self.assertLess(mandatory["index"], visit["index"])
            self.assertEqual(inserted["polyline"][visit["index"]], [hit.lon, hit.lat])

        async_to_sync(run)()

    def test_single_physical_stop_groups_categories_not_nearby_objects(self):
        async def run():
            path = await fake_geometry("bike", [(8, 47), (8.1, 47)])
            hit = PoiHit(
                osm_ref="n1",
                category="food",
                name="",
                lon=8.05,
                lat=47,
                along_m=LineMeasure(path).meters[10],
                offset_m=0,
            )
            hits = [hit, replace(hit, category="toilets"), replace(hit, osm_ref="n2", category="drinking_water")]
            choices = await self.planner().candidates(path, [], hits, 0, 19, Limits(), categories_first=True)
            self.assertEqual({h.category for h in choices[0]["group"]}, {"food", "toilets"})
            self.assertEqual({h.osm_ref for h in choices[0]["group"]}, {"n1"})

        async_to_sync(run)()

    def test_distant_snapping_rejects_break_and_lodging_without_changing_path(self):
        path, original_hit = self.window_poi()

        async def run(category, shift):
            hit = replace(original_hit, category=category)

            async def snapped(profile, points, *args, **kwargs):
                shifted = [list(p) for p in points]
                # GH still returns a successful route, but its POI waypoint is elsewhere.
                shifted[1][1] += shift
                return await fake_geometry(profile, shifted)

            planner = self.planner(build=snapped)
            choices = await planner.candidates(path, [], [hit], 0, len(path["polyline"]) - 1, Limits())
            before = list(path["polyline"])
            result = await planner.insert(path, [], choices[0], 0, Limits(), is_break=category != "lodging")
            self.assertIsNone(result)
            self.assertEqual(path["polyline"], before)
            self.assertEqual(planner.budget.failures, 1)

        for category in ("drinking_water", "lodging"):
            with self.subTest(category=category):
                async_to_sync(run)(category, 0.003)  # about 334 metres

    def test_minor_snapping_inaccuracy_is_accepted(self):
        path, hit = self.window_poi()

        async def run():

            async def snapped(profile, points, *args, **kwargs):
                shifted = [list(p) for p in points]
                shifted[1][1] += 0.00005  # about 5.6 m: entrance/map accuracy
                return await fake_geometry(profile, shifted)

            planner = self.planner(build=snapped)
            choices = await planner.candidates(path, [], [hit], 0, len(path["polyline"]) - 1, Limits())
            result = await planner.insert(path, [], choices[0], 0, Limits())
            self.assertIsNotNone(result)
            self.assertLess(6, MAX_POI_SNAP_M)

        async_to_sync(run)()

    def test_existing_poi_cannot_be_snapped_away_by_later_insertion(self):
        async def run():
            path = await fake_geometry("bike", [(8, 47), (8.2, 47)])
            measure = LineMeasure(path)
            event = {"index": 10, "point": path["polyline"][10], "pois": [{"osm_ref": "previous", "category": "food"}]}
            hit = PoiHit(
                osm_ref="next", category="toilets", name="", lon=8.11, lat=47, along_m=measure.meters[11], offset_m=0
            )

            async def snapped(profile, points, *args, **kwargs):
                shifted = [list(p) for p in points]
                shifted[1][1] += 0.003  # previous POI; new POI itself remains correct
                return await fake_geometry(profile, shifted)

            planner = self.planner(build=snapped)
            choice = (await planner.candidates(path, [event], [hit], 0, 19, Limits()))[0]
            self.assertIsNone(await planner.insert(path, [event], choice, 0, Limits()))
            self.assertEqual(event["index"], 10)

        async_to_sync(run)()

    def test_return_window_finds_a_second_visit_to_the_same_poi(self):
        async def run():
            path = await fake_geometry("bike", [(8, 47), (8.1, 47), (8, 47)])
            measure = LineMeasure(path)
            hit = PoiHit(
                osm_ref="water",
                category="drinking_water",
                name="",
                lon=8.05,
                lat=47,
                along_m=measure.meters[10],
                offset_m=0,
            )
            planner = self.planner([hit])
            hits = await planner.window_hits(path, 20, 40, ["drinking_water"], 2500)
            self.assertEqual(planner.pois_along.await_args.args[0], path["polyline"][20:41])
            self.assertEqual(measure.index(hits[0].along_m), 30)
            # A visit on the outward leg must not suppress the return visit.
            events = [{"index": 10, "point": [hit.lon, hit.lat], "pois": [hit.as_json()]}]
            choices = await planner.candidates(path, events, hits, 20, 40, Limits())
            self.assertEqual(len(choices), 1)
            result = await planner.insert(path, events, choices[0], 20, Limits(), is_break=True)
            self.assertIsNotNone(result)
            _, mapped, visit = result
            self.assertEqual(sum(bool(e.get("pois")) for e in mapped), 2)
            self.assertGreater(visit["index"], 20)

        async_to_sync(run)()

    def test_window_projection_uses_segment_distance_when_route_turns(self):
        async def run():
            path = geometry([(8, 47), (8.1, 47), (8.1, 47.1)])
            measure = LineMeasure(path)
            hit = PoiHit(
                osm_ref="corner", category="food", name="", lon=8.1, lat=47, along_m=measure.meters[-1] / 2, offset_m=0
            )
            hits = await self.planner([hit]).window_hits(path, 0, 2, ["food"], 2500)
            self.assertAlmostEqual(hits[0].along_m, measure.meters[1])

        async_to_sync(run)()

    def test_fallback_routing_preserves_repeated_waypoint_occurrences(self):
        async def no_details(profile, points, *a, **k):
            result = await fake_geometry(profile, points)
            result.pop("waypoint_indices")
            return result

        async def run():
            points = [(8, 47), (8.1, 47), (8, 47), (8.2, 47)]
            path, indices = await self.planner(build=no_details).route(points)
            self.assertEqual(len(indices), 4)
            self.assertGreater(indices[2], indices[0])
            self.assertEqual([path["polyline"][i] for i in indices], [list(p) for p in points])

        async_to_sync(run)()


class RouteLegTests(SimpleTestCase):
    def test_two_event_loops_failures_flags_and_cache_isolation(self):
        active = 0
        peak = 0

        async def post(body):
            nonlocal active, peak
            self.assertTrue(body["points"])
            self.assertFalse(body["calc_points"])
            self.assertFalse(body["elevation"])
            self.assertNotIn("details", body)
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.001)
                if body["points"][1][0] == 9:
                    raise ValueError("unreachable")
                return {"paths": [{"time": 120000, "distance": 500}]}
            finally:
                active -= 1

        async def run():
            points = [((8, 47), (9 + i * 0.01, 47)) for i in range(8)]
            results = await route_legs("bike", points, limiter=asyncio.Semaphore(4), memo={})
            self.assertIsNone(results[0])
            self.assertEqual(results[1], (120, 500))

        before = _fetch_route.cache_info()
        with patch("core.weather._post_route", side_effect=post):
            async_to_sync(run)()
            async_to_sync(run)()
        self.assertEqual(_fetch_route.cache_info(), before)
        self.assertEqual(peak, 4)
