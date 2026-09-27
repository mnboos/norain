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
from .models import Journey, Plan, Subscription, User, route_point
from .random_rides import (
    LENGTH_TOLERANCE,
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

    def test_a_free_account_gets_one_loop_as_one_day(self):
        self._plan()
        self.assertEqual(self.ride.plan_status, Journey.PlanStatus.DONE, self.ride.plan_error)
        day = self.ride.days.get()
        self.assertEqual(day.start, day.end)
        stage = day.stages.get()
        self.assertAlmostEqual(stage.total_distance_m / 40_000, 1, delta=LENGTH_TOLERANCE)
        self.assertFalse(stage.limit_overruns.get("day"))

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
        self.assertIn("keine passende Runde", self.ride.plan_error)
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
