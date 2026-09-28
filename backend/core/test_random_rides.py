"""Random rides: candidate generation, sizing, planning and the API around them."""

import math
from datetime import UTC, datetime, time, timedelta
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import Client, SimpleTestCase, TestCase, override_settings

from . import tests as fixtures
from .geo import haversine_m
from .journey_geometry import Limits, measured_geometry
from .models import Journey, JourneyDay, JourneyStage, Plan, RecurringRoute, Subscription, User, route_line, route_point
from .random_rides import (
    LENGTH_TOLERANCE,
    PICK_VARIANTS,
    RandomPrefs,
    detour_via,
    generate_candidate,
    headings,
    length_ratio,
    offset_point,
    size,
)
from .schedule import local_today
from .tasks import _plan_journey_async
from .weather import RoundTrip, _route_body

SPEED_M_S = 5.0
# How much longer than asked the fake GraphHopper makes a loop, as the real one often does.
LOOP_OVERSHOOT = 1.4


def line_geometry(points) -> dict:
    """A geometry through ``points`` with ~20 vertices per leg, ridden at ``SPEED_M_S``."""
    coords = [list(points[0])]
    for a, b in pairwise(points):
        coords.extend([a[0] + (b[0] - a[0]) * i / 20, a[1] + (b[1] - a[1]) * i / 20] for i in range(1, 21))
    along = [0.0]
    for a, b in pairwise(coords):
        along.append(along[-1] + haversine_m(a[0], a[1], b[0], b[1]))
    return measured_geometry(coords, [d / SPEED_M_S for d in along])


async def fake_round_trip(profile, start, round_trip: RoundTrip, *args, **kwargs):
    """A square loop leaving towards the heading, ``LOOP_OVERSHOOT`` times the asked length."""
    side = round_trip.distance_m * LOOP_OVERSHOOT / 4
    heading = round_trip.heading or 0.0
    corners = [tuple(start)]
    for turn in range(3):
        corners.append(offset_point(*corners[-1], heading + 90 * turn, side))
    return line_geometry([*corners, tuple(start)])


async def fake_geometry(profile, points, *args, **kwargs):
    return line_geometry(points)


class GenerationTests(SimpleTestCase):
    def test_detour_via_lies_on_the_ellipse_towards_the_heading(self):
        start, dest = (8.0, 47.0), (8.2, 47.0)
        direct = haversine_m(*start, *dest)
        for heading in (0, 45, 180, 300):
            via = detour_via(start, dest, 2 * direct, heading)
            total = haversine_m(*start, *via) + haversine_m(*via, *dest)
            self.assertAlmostEqual(total / (2 * direct), 1, delta=0.01)
        self.assertGreater(detour_via(start, dest, 2 * direct, 0)[1], 47.0, "north of the line")
        self.assertLess(detour_via(start, dest, 2 * direct, 180)[1], 47.0, "south of the line")

    def test_no_via_when_the_direct_route_is_long_enough(self):
        start, dest = (8.0, 47.0), (8.2, 47.0)
        self.assertIsNone(detour_via(start, dest, haversine_m(*start, *dest), 0))

    def test_headings_spread_and_follow_the_preference(self):
        free = headings(RandomPrefs(seed=5), 3)
        self.assertEqual(len({round(h) for h in free}), 3)
        self.assertEqual(free, headings(RandomPrefs(seed=5), 3), "deterministic in the seed")
        self.assertNotEqual(free, headings(RandomPrefs(seed=6), 3))
        east = headings(RandomPrefs(heading=90, seed=5), 3)
        self.assertEqual(east[1], 90)
        self.assertTrue(all(40 <= h <= 140 for h in east))
        self.assertEqual(headings(RandomPrefs(heading=350, seed=1), 1), [350])

    def test_prefs_from_json(self):
        self.assertEqual(RandomPrefs.from_json(None), RandomPrefs())
        prefs = RandomPrefs.from_json({"round_trip": False, "heading": 370, "seed": 4})
        self.assertEqual(prefs, RandomPrefs(round_trip=False, heading=10, seed=4))
        self.assertEqual(RandomPrefs.from_json(prefs.as_json()), prefs)

    def test_round_trip_request_body(self):
        body = _route_body("bike", ((8.0, 47.0),), round_trip=RoundTrip(30_000.4, 7, 450))
        self.assertEqual(body["algorithm"], "round_trip")
        self.assertEqual(body["round_trip.distance"], 30_000)
        self.assertEqual(body["round_trip.seed"], 7)
        self.assertEqual(body["headings"], [90])
        self.assertEqual(body["points"], [[8.0, 47.0]])
        self.assertNotIn("headings", _route_body("bike", ((8.0, 47.0),), round_trip=RoundTrip(1000, 1)))

    def test_sizing_corrects_an_overshooting_loop(self):
        target = Limits(meters=40_000)
        loop = async_to_sync(generate_candidate)(
            profile="bike",
            points=((8.0, 47.0), (8.0, 47.0)),
            prefs=RandomPrefs(seed=1),
            target=target,
            index=0,
            heading=45,
            build_round_trip=AsyncMock(side_effect=fake_round_trip),
            build_geometry=AsyncMock(side_effect=AssertionError("a loop is a round trip")),
            model=None,
            interval_seconds=900,
        )
        self.assertLessEqual(abs(length_ratio(loop, target) - 1), LENGTH_TOLERANCE)
        self.assertLess(haversine_m(*loop["polyline"][0], *loop["polyline"][-1]), 1, "ends where it starts")

    def test_a_time_target_sizes_by_riding_time(self):
        target = Limits(seconds=2 * 3600)
        loop = async_to_sync(generate_candidate)(
            profile="bike",
            points=((8.0, 47.0), (8.0, 47.0)),
            prefs=RandomPrefs(seed=1),
            target=target,
            index=0,
            heading=0,
            build_round_trip=AsyncMock(side_effect=fake_round_trip),
            build_geometry=AsyncMock(),
            model=None,
            interval_seconds=900,
        )
        self.assertAlmostEqual(loop["total_seconds"] / 7200, 1, delta=LENGTH_TOLERANCE)

    def test_point_to_point_takes_the_long_way_to_the_destination(self):
        target = Limits(meters=50_000)
        routed = AsyncMock(side_effect=fake_geometry)
        ride = async_to_sync(generate_candidate)(
            profile="bike",
            points=((8.0, 47.0), (8.2, 47.0)),
            prefs=RandomPrefs(round_trip=False, seed=1),
            target=target,
            index=0,
            heading=0,
            build_round_trip=AsyncMock(side_effect=AssertionError("not a loop")),
            build_geometry=routed,
            model=None,
            interval_seconds=900,
        )
        self.assertEqual(len(routed.await_args.args[1]), 3, "start, via, destination")
        self.assertLessEqual(abs(length_ratio(ride, target) - 1), LENGTH_TOLERANCE)
        self.assertEqual(ride["polyline"][-1], [8.2, 47.0])

    def test_sizing_keeps_the_best_attempt_when_a_later_one_fails(self):
        calls = []

        async def route(guess):
            calls.append(guess)
            if len(calls) > 1:
                raise ValueError("no route")
            return {"total_seconds": 0, "total_distance_m": guess * 2}

        best = async_to_sync(size)(route, Limits(meters=10_000), 10_000)
        self.assertEqual(best["total_distance_m"], 20_000)
        self.assertEqual(len(calls), 2)
        self.assertTrue(math.isclose(calls[1], 5_000))


@override_settings(CACHES=fixtures.LOCMEM_CACHE, CHANNEL_LAYERS=fixtures.INMEM_CHANNELS)
class RandomRideTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="rider", email="r@example.com", password="pw")
        self.ride = Journey.objects.create(
            owner=self.user,
            name="Runde",
            kind=Journey.Kind.RANDOM,
            random_prefs={"round_trip": True, "heading": None, "seed": 11},
            start_point=route_point(47.0, 8.0),
            start_name="A",
            destination_point=route_point(47.0, 8.0),
            dest_name="A",
            start_date=local_today() + timedelta(days=1),
            earliest_start=time(9),
            latest_arrival=time(23, 59),
            max_day_distance_m=40_000,
        )

    def _plan(self, loop=fake_round_trip):
        loops = AsyncMock(side_effect=loop)
        routes = SimpleNamespace(aenqueue=AsyncMock())
        with (
            patch("core.tasks.build_round_trip", loops),
            patch("core.tasks.build_geometry", AsyncMock(side_effect=fake_geometry)) as geometry,
            patch("core.tasks._pois_along", AsyncMock(return_value=[])),
            patch("core.tasks.plan_journey_routes", routes),
        ):
            async_to_sync(_plan_journey_async)(str(self.ride.id), self.ride.plan_revision)
        routes.aenqueue.assert_not_awaited()
        self.ride.refresh_from_db()
        return loops, geometry

    def test_every_account_gets_three_variants_to_pick_from(self):
        loops, _ = self._plan()
        self.assertEqual(self.ride.plan_status, Journey.PlanStatus.DONE, self.ride.plan_error)
        day = self.ride.days.get()
        self.assertEqual(day.start, day.end)
        stages = list(day.stages.all())
        self.assertEqual(len(stages), PICK_VARIANTS, "picking costs routing only, so free gets three too")
        self.assertEqual(len({call.args[2].seed for call in loops.await_args_list[::1]}), PICK_VARIANTS)
        for stage in stages:
            self.assertAlmostEqual(stage.total_distance_m / 40_000, 1, delta=LENGTH_TOLERANCE)
            self.assertFalse(stage.limit_overruns.get("day"))

    def test_the_weather_mode_gets_the_tiers_candidates_and_only_with_plus(self):
        from .journey_planner import RoutingBudget
        from .tasks import _random_ride_day

        self.ride.random_prefs = {**self.ride.random_prefs, "consider_weather": True}
        self.ride.save()

        def plan(weather_routing):
            limits = SimpleNamespace(max_journey_alternatives=2, weather_routing=weather_routing)
            with (
                patch("core.tasks.build_round_trip", AsyncMock(side_effect=fake_round_trip)),
                patch("core.tasks._pois_along", AsyncMock(return_value=[])),
            ):
                return async_to_sync(_random_ride_day)(self.ride, limits, None, RoutingBudget())

        self.assertEqual(len(plan(True)["stages"]), 2, "weighed by the weather: the tier's alternatives")
        self.assertEqual(len(plan(False)["stages"]), PICK_VARIANTS, "no longer Plus: back to picking")

    def test_pro_compares_three_different_loops(self):
        Subscription.objects.create(
            user=self.user, plan=Plan.PRO, complimentary_until=datetime.now(UTC) + timedelta(days=1)
        )
        loops, _ = self._plan()
        stages = list(self.ride.days.get().stages.all())
        self.assertEqual([s.rank for s in stages], [0, 1, 2])
        requests = [call.args[2] for call in loops.await_args_list]
        self.assertEqual(len({r.seed for r in requests}), 3)
        self.assertEqual(len({round(r.heading) for r in requests}), 3)

    def test_point_to_point_routes_through_a_via(self):
        self.ride.random_prefs = {"round_trip": False, "seed": 3}
        self.ride.destination_point = route_point(47.0, 8.2)
        self.ride.save()
        loops, geometry = self._plan()
        loops.assert_not_awaited()
        day = self.ride.days.get()
        self.assertEqual(day.end, [8.2, 47.0])
        self.assertEqual(len(geometry.await_args_list[0].args[1]), 3)

    def test_no_candidate_fails_the_plan_with_a_reason(self):
        self._plan(loop=AsyncMock(side_effect=ValueError("no route")))
        self.assertEqual(self.ride.plan_status, Journey.PlanStatus.FAILED)
        self.assertEqual(self.ride.plan_error, "no_round_found")
        self.assertFalse(self.ride.days.exists())


@override_settings(CACHES=fixtures.LOCMEM_CACHE, CHANNEL_LAYERS=fixtures.INMEM_CHANNELS)
class RandomRideApiTests(TestCase):
    BODY = {
        "name": "Feierabendrunde",
        "kind": "random",
        "startLat": 47.0,
        "startLon": 8.0,
        "startName": "A",
        "destLat": 0,
        "destLon": 0,
        "destName": "",
        "startDate": (local_today() + timedelta(days=1)).isoformat(),
        "earliestStart": "17:30",
        "latestArrival": "23:59",
        "maxDaySeconds": 7200,
        "randomPrefs": {"roundTrip": True, "heading": 90},
    }
    TOUR = {
        "name": "Tour",
        "startLat": 47.0,
        "startLon": 8.0,
        "startName": "A",
        "destLat": 47.0,
        "destLon": 9.0,
        "destName": "B",
        "startDate": (local_today() + timedelta(days=1)).isoformat(),
        "maxDayDistanceM": 80000,
    }

    def setUp(self):
        self.user = User.objects.create_user(username="rider", email="r@example.com", password="pw")
        self.client = Client()
        self.client.force_login(self.user)

    def _post(self, body):
        with patch("core.api.journey.plan_journey", SimpleNamespace(aenqueue=AsyncMock())) as plan:
            response = self.client.post("/api/journeys", body, content_type="application/json")
        return response, plan.aenqueue

    def test_a_loop_ends_at_its_start_and_gets_dice(self):
        response, enqueue = self._post(self.BODY)
        self.assertEqual(response.status_code, 201, response.content)
        ride = Journey.objects.get()
        self.assertEqual(ride.kind, Journey.Kind.RANDOM)
        self.assertEqual((ride.destination_point.x, ride.destination_point.y), (8.0, 47.0))
        self.assertEqual(ride.dest_name, "A")
        self.assertEqual(ride.random_prefs["heading"], 90)
        self.assertGreater(ride.random_prefs["seed"], 0)
        enqueue.assert_awaited_once_with(str(ride.id), 0)
        out = response.json()
        self.assertEqual(out["kind"], "random")
        self.assertTrue(out["random_prefs"]["round_trip"])

    def test_random_rides_have_their_own_quota(self):
        self.assertEqual(self._post(self.TOUR)[0].status_code, 201)
        for _ in range(3):
            self.assertEqual(self._post(self.BODY)[0].status_code, 201)
        response, enqueue = self._post(self.BODY)
        self.assertEqual(response.status_code, 402)
        enqueue.assert_not_awaited()
        tours = self.client.get("/api/journeys?kind=tour").json()
        self.assertEqual([j["kind"] for j in tours], ["tour"])
        self.assertEqual(len(self.client.get("/api/journeys?kind=random").json()), 3)

    def test_replan_rerolls_and_edit_keeps_the_dice(self):
        self._post(self.BODY)
        ride = Journey.objects.get()
        seed = ride.random_prefs["seed"]
        with patch("core.api.journey.plan_journey", SimpleNamespace(aenqueue=AsyncMock())):
            response = self.client.put(
                f"/api/journeys/{ride.id}", {**self.BODY, "maxDaySeconds": 3600}, content_type="application/json"
            )
            self.assertEqual(response.status_code, 200, response.content)
            ride.refresh_from_db()
            self.assertEqual(ride.random_prefs["seed"], seed)
            response = self.client.post(f"/api/journeys/{ride.id}/plan")
            self.assertEqual(response.status_code, 202)
            ride.refresh_from_db()
            self.assertNotEqual(ride.random_prefs["seed"], seed)
            self.assertEqual(ride.plan_revision, 2)
            response = self.client.put(f"/api/journeys/{ride.id}", self.TOUR, content_type="application/json")
            self.assertEqual(response.status_code, 422)


@override_settings(CACHES=fixtures.LOCMEM_CACHE, CHANNEL_LAYERS=fixtures.INMEM_CHANNELS)
class WeatherChoiceTests(TestCase):
    """Riding around bad weather is the rider's choice, and a Plus feature."""

    BODY = RandomRideApiTests.BODY

    def setUp(self):
        self.user = User.objects.create_user(username="rider", email="r@example.com", password="pw")
        self.client = Client()
        self.client.force_login(self.user)

    def _plus(self):
        Subscription.objects.create(
            user=self.user, plan=Plan.PRO, complimentary_until=datetime.now(UTC) + timedelta(days=1)
        )

    def _post(self, body):
        with patch("core.api.journey.plan_journey", SimpleNamespace(aenqueue=AsyncMock())):
            response = self.client.post("/api/journeys", body, content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content)
        return Journey.objects.get(id=response.json()["id"])

    def test_off_unless_chosen(self):
        self._plus()
        ride = self._post(self.BODY)
        self.assertFalse(ride.weather_prefs["avoid_rain"])
        self.assertFalse(ride.weather_prefs["avoid_headwind"])
        chosen = self._post({**self.BODY, "weatherPrefs": {"avoidRain": True, "avoidHeadwind": False}})
        self.assertTrue(chosen.weather_prefs["avoid_rain"])
        self.assertFalse(chosen.weather_prefs["avoid_headwind"])

    def test_a_free_account_cannot_store_it_on(self):
        ride = self._post({**self.BODY, "weatherPrefs": {"avoidRain": True, "avoidHeadwind": True}})
        self.assertEqual((ride.weather_prefs["avoid_rain"], ride.weather_prefs["avoid_headwind"]), (False, False))
        with patch("core.api.journey.plan_journey", SimpleNamespace(aenqueue=AsyncMock())):
            self.client.put(
                f"/api/journeys/{ride.id}",
                {**self.BODY, "weatherPrefs": {"avoidRain": True}},
                content_type="application/json",
            )
        ride.refresh_from_db()
        self.assertFalse(ride.weather_prefs["avoid_rain"], "an edit does not switch it on either")

    def test_entitlements_say_whether_the_account_has_it(self):
        self.assertFalse(self.client.get("/api/billing/entitlements").json()["weatherRouting"])
        self._plus()
        self.assertTrue(self.client.get("/api/billing/entitlements").json()["weatherRouting"])


@override_settings(CACHES=fixtures.LOCMEM_CACHE, CHANNEL_LAYERS=fixtures.INMEM_CHANNELS)
class RandomRideWeatherTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="rider", email="r@example.com", password="pw")
        Subscription.objects.create(
            user=self.user, plan=Plan.PRO, complimentary_until=datetime.now(UTC) + timedelta(days=1)
        )
        self.ride = Journey.objects.create(
            owner=self.user,
            name="Runde",
            kind=Journey.Kind.RANDOM,
            random_prefs={"round_trip": True, "seed": 11},
            start_point=route_point(47.0, 8.0),
            start_name="A",
            destination_point=route_point(47.0, 8.0),
            dest_name="A",
            start_date=local_today() + timedelta(days=1),
            earliest_start=time(9),
            latest_arrival=time(23, 59),
            max_day_distance_m=40_000,
            weather_prefs={"avoid_rain": True, "avoid_headwind": True},
        )
        self.ride.random_prefs["consider_weather"] = True
        self.ride.save()

    def _plan(self, *, warm: bool):
        field = {"rows": 1}
        cells = SimpleNamespace(aenqueue=AsyncMock())
        replan = SimpleNamespace(aenqueue=AsyncMock())

        async def cached_keys(keys, windows):
            day = self.ride.start_date
            return ({(k[0], k[1], day) for k in keys} if warm else set()), set()

        with (
            patch("core.tasks.build_round_trip", AsyncMock(side_effect=fake_round_trip)) as loops,
            patch("core.tasks._pois_along", AsyncMock(return_value=[])),
            patch("core.tasks.get_cached_cell_keys", cached_keys),
            patch("core.tasks.weather_field", AsyncMock(return_value=field)),
            patch("core.tasks.refresh_forecast_cell", cells),
            patch("core.tasks.plan_journey", SimpleNamespace(using=lambda **_: replan)),
        ):
            async_to_sync(_plan_journey_async)(str(self.ride.id), self.ride.plan_revision)
        self.ride.refresh_from_db()
        return loops, cells, replan, field

    def test_cold_cells_are_fetched_and_the_plan_waits(self):
        loops, cells, replan, _ = self._plan(warm=False)
        self.assertEqual(self.ride.plan_status, Journey.PlanStatus.WEATHER)
        self.assertEqual(self.ride.plan_attempts, 1)
        self.assertGreater(cells.aenqueue.await_count, 0)
        replan.aenqueue.assert_awaited_once_with(str(self.ride.id), self.ride.plan_revision)
        loops.assert_not_awaited()

    def test_warm_cells_route_every_candidate_around_the_weather(self):
        loops, cells, _, field = self._plan(warm=True)
        self.assertEqual(self.ride.plan_status, Journey.PlanStatus.DONE, self.ride.plan_error)
        cells.aenqueue.assert_not_awaited()
        self.assertTrue(all(call.kwargs.get("weather") is field for call in loops.await_args_list))
        self.assertTrue(self.ride.days.get().weather_routed)

    def test_without_the_choice_or_without_plus_nothing_is_warmed(self):
        self.ride.weather_prefs = {"avoid_rain": False, "avoid_headwind": False}
        self.ride.save()
        loops, cells, _, _ = self._plan(warm=False)
        cells.aenqueue.assert_not_awaited()
        self.assertNotIn("weather", loops.await_args.kwargs)
        self.assertFalse(self.ride.days.get().weather_routed)

        Subscription.objects.filter(user=self.user).delete()
        self.ride.weather_prefs = {"avoid_rain": True, "avoid_headwind": True}
        self.ride.plan_revision += 1
        self.ride.save()
        loops, cells, _, _ = self._plan(warm=False)
        cells.aenqueue.assert_not_awaited()
        self.assertNotIn("weather", loops.await_args.kwargs)

    def test_the_area_reaches_a_quarter_of_the_length(self):
        from .random_rides import area_cells

        cells = area_cells(((8.0, 47.0), (8.0, 47.0)), "bike", Limits(meters=40_000))
        lats = sorted({c[0] for c in cells})
        self.assertIn((47.0, 8.0), cells)
        self.assertAlmostEqual(lats[-1] - 47.0, 0.1, delta=0.03, msg="10 km north is ~0.09°")
        self.assertEqual(len(cells), len(set(cells)))


@override_settings(CACHES=fixtures.LOCMEM_CACHE, CHANNEL_LAYERS=fixtures.INMEM_CHANNELS)
class PickVariantsApiTests(TestCase):
    """Without the weather mode the rider picks variants and saves them as routes."""

    def setUp(self):
        self.user = User.objects.create_user(username="rider", email="r@example.com", password="pw")
        self.client = Client()
        self.client.force_login(self.user)
        self.ride = Journey.objects.create(
            owner=self.user,
            name="Runde",
            kind=Journey.Kind.RANDOM,
            random_prefs={"round_trip": True, "seed": 11},
            start_point=route_point(47.0, 8.0),
            start_name="Zuhause",
            destination_point=route_point(47.0, 8.0),
            dest_name="Zuhause",
            start_date=local_today() + timedelta(days=1),
            earliest_start=time(9),
            latest_arrival=time(23, 59),
            max_day_distance_m=40_000,
            plan_status=Journey.PlanStatus.DONE,
        )
        day = JourneyDay.objects.create(
            journey=self.ride, index=0, date=self.ride.start_date, start=[8.0, 47.0], end=[8.0, 47.0]
        )
        self.stages = []
        for rank in range(3):
            geometry = async_to_sync(fake_round_trip)(
                "bike", (8.0, 47.0), RoundTrip(20_000 + rank * 1000, rank, 90 * rank)
            )
            self.stages.append(
                JourneyStage.objects.create(
                    day=day,
                    rank=rank,
                    polyline=route_line(geometry["polyline"]),
                    total_seconds=geometry["total_seconds"],
                    total_distance_m=geometry["total_distance_m"],
                    sample_points=geometry["sample_points"],
                    vertex_times=geometry["vertex_times"],
                    vertex_elevations=[400.0] * len(geometry["polyline"]),
                    geometry_fetched_at=datetime.now(UTC),
                )
            )

    def _save(self, stage, **body):
        with patch("core.api.recurring_route.refresh_route_geometry", SimpleNamespace(aenqueue=AsyncMock())) as geo:
            response = self.client.post(
                f"/api/journeys/{self.ride.id}/stages/{stage.id}/route",
                {"scheduleCron": "0 9 * * 6", "scheduleDescription": "Sa um 09:00", **body},
                content_type="application/json",
            )
        return response, geo.aenqueue

    def test_reading_the_variants_starts_no_forecast(self):
        with patch("core.api.journey.start_forecast_job", AsyncMock(side_effect=AssertionError("forecast"))):
            response = self.client.get(f"/api/journeys/{self.ride.id}")
        self.assertEqual(response.status_code, 200, response.content)
        day = response.json()["days"][0]
        self.assertFalse(day["forecast_available"])
        self.assertEqual(len(day["stages"]), 3)
        with patch("core.api.journey.start_forecast_job", AsyncMock(side_effect=AssertionError("forecast"))):
            response = self.client.get(f"/api/journeys/{self.ride.id}/stages/{self.stages[0].id}/forecast")
        self.assertEqual(response.status_code, 409)

    def test_a_picked_variant_becomes_a_route_on_its_line(self):
        response, geometry = self._save(self.stages[1], name="Samstagsrunde")
        self.assertEqual(response.status_code, 200, response.content)
        route = RecurringRoute.objects.get()
        self.assertEqual(route.name, "Samstagsrunde")
        self.assertEqual(route.geometry_source, "imported")
        self.assertEqual([p[:2] for p in route.imported_coordinates], self.stages[1].polyline_coordinates)
        self.assertEqual(route.imported_coordinates[0][2], 400.0, "heights come along")
        self.assertEqual(route.duration_seconds, self.stages[1].total_seconds)
        self.assertEqual((route.start_name, route.schedule_cron), ("Zuhause", "0 9 * * 6"))
        geometry.assert_awaited_once_with(str(route.id))

    def test_picking_several_counts_against_the_route_quota(self):
        names = []
        for stage in self.stages:
            response, _ = self._save(stage)
            names.append(response.status_code)
        self.assertEqual(names, [200, 200, 402], "free: two active routes")
        self.assertEqual(
            sorted(RecurringRoute.objects.values_list("name", flat=True)),
            ["Runde – Variante 1", "Runde – Variante 2"],
        )

    def test_only_the_owner_and_only_random_rides(self):
        other = User.objects.create_user(username="other", email="o@example.com", password="pw")
        self.client.force_login(other)
        self.assertEqual(self._save(self.stages[0])[0].status_code, 404)
        self.client.force_login(self.user)
        Journey.objects.filter(id=self.ride.id).update(kind=Journey.Kind.TOUR)
        self.assertEqual(self._save(self.stages[0])[0].status_code, 422)

    def test_the_weather_mode_is_stored_only_with_plus(self):
        body = {**RandomRideApiTests.BODY, "randomPrefs": {"roundTrip": True, "considerWeather": True}}
        with patch("core.api.journey.plan_journey", SimpleNamespace(aenqueue=AsyncMock())):
            free = self.client.post("/api/journeys", body, content_type="application/json").json()
        self.assertFalse(free["random_prefs"]["consider_weather"])
        Subscription.objects.create(
            user=self.user, plan=Plan.PRO, complimentary_until=datetime.now(UTC) + timedelta(days=1)
        )
        with patch("core.api.journey.plan_journey", SimpleNamespace(aenqueue=AsyncMock())):
            plus = self.client.post("/api/journeys", body, content_type="application/json").json()
        self.assertTrue(plus["random_prefs"]["consider_weather"])

    def test_each_variant_says_how_much_it_climbs(self):
        stage = self.stages[0]
        heights = [400.0 + (i % 20) * 5 for i in range(len(stage.polyline_coordinates))]
        JourneyStage.objects.filter(id=stage.id).update(vertex_elevations=heights)
        with patch("core.api.journey.start_forecast_job", AsyncMock()):
            stages = self.client.get(f"/api/journeys/{self.ride.id}").json()["days"][0]["stages"]
        self.assertGreater(stages[0]["ascent_m"], 0)
        self.assertEqual(stages[1]["ascent_m"], 0, "a flat line climbs nothing")
