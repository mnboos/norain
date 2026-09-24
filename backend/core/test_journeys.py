"""Journey mode: POIs, road preferences, weather zones, day and break planning, stage jobs."""

import json
import re
import tempfile
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase, override_settings

from . import tests as fixtures
from .entitlements import FREE
from .geo import vertex_distances
from .journeys import lodging_candidates, rank_day
from .management.commands.import_pois import import_pois
from .models import (
    ForecastJob,
    Journey,
    JourneyDay,
    JourneyStage,
    Plan,
    Poi,
    Subscription,
    User,
    route_line,
    route_point,
)
from .pois import PoiHit, categories, filter_tags, pois_along_sync
from .road_prefs import RoadPrefs, is_penalty_only, merge_models, road_prefs_model
from .schedule import local_today
from .tasks import (
    _job_geometry,
    _plan_forecast_job_async,
    _plan_journey_async,
    _plan_journey_routes_async,
    _wants_stations,
    start_forecast_job,
)
from .weather import _route_body
from .weather_routing import corridor_cells, headwind_condition, zone_model

SCRIPT = Path(settings.BASE_DIR).parent / "docker" / "osm-extract-pois.sh"


def straight_geometry(km: float, lon0: float = 8.0, lat: float = 47.0, speed_m_s: float = 5.0) -> dict:
    """An eastward line with a vertex every ~76 m, ridden at a constant speed."""
    step = 0.001  # degrees of longitude, ~76 m at 47° N
    count = int(km * 1000 / 76) + 1
    polyline = [[round(lon0 + i * step, 6), lat] for i in range(count)]
    along = vertex_distances(polyline)
    times = [d / speed_m_s for d in along]
    samples = [
        {"lat": lat, "lon": p[0], "lat_r": round(lat, 2), "lon_r": round(p[0], 2), "elapsed_s": int(times[i]), "idx": i}
        for i, p in enumerate(polyline)
        if i % 4 == 0 or i == count - 1
    ]
    return {
        "polyline": polyline,
        "vertex_times": times,
        "sample_points": samples,
        "total_seconds": int(times[-1]),
        "total_distance_m": round(along[-1] * 1.002, 1),  # GraphHopper's is a little longer
    }


def hit(category: str, along_m: float, offset_m: float = 50.0, ref: str | None = None, **tags) -> PoiHit:
    return PoiHit(
        osm_ref=ref or f"n{category}{int(along_m)}",
        category=category,
        name="",
        lon=8.0,
        lat=47.0,
        along_m=along_m,
        offset_m=offset_m,
        tags=tags,
    )


# --------------------------------------------------------------------------- POIs
class PoiCategoryTests(SimpleTestCase):
    def test_every_tag_in_the_map_is_in_the_extraction_script(self):
        script = SCRIPT.read_text()
        filters: dict[str, set[str]] = {}
        for key, values in re.findall(r"nwr/(\w+)=([\w,]+)", script):
            filters.setdefault(key, set()).update(values.split(","))
        missing = {(k, v) for k, v in filter_tags() if v not in filters.get(k, set())}
        self.assertEqual(missing, set())

    def test_categories(self):
        self.assertEqual(categories({"amenity": "drinking_water"}), ["drinking_water"])
        self.assertEqual(categories({"man_made": "water_tap", "drinking_water": "yes"}), ["drinking_water"])
        self.assertEqual(categories({"man_made": "water_tap"}), [], "a tap that does not say drinkable is not water")
        self.assertEqual(categories({"amenity": "toilets", "access": "private"}), [])
        self.assertEqual(categories({"tourism": "camp_site"}), ["lodging"])
        self.assertEqual(categories({"amenity": "charging_station"}), [], "a car charger is not an e-bike charger")

    def test_vending_machines_by_what_they_sell(self):
        def vending(value: str | None) -> list[str]:
            tags = {"amenity": "vending_machine"} | ({"vending": value} if value is not None else {})
            return categories(tags)

        self.assertEqual(vending("excrement_bags"), [], "a dog-bag dispenser is no stop")
        self.assertEqual(vending("parking_tickets;public_transport_tickets"), [])
        self.assertEqual(vending(None), [], "a machine that does not say what it sells")
        self.assertEqual(vending("drinks;sweets"), ["vending_drinks", "vending_sweets"])
        self.assertEqual(vending("Drinks, Snacks"), ["vending_food", "vending_drinks"], "commas and capitals")
        self.assertEqual(vending("coffee"), ["vending_coffee"])
        self.assertEqual(vending("bicycle_tube"), ["bike_repair"])


class PoiImportTests(TestCase):
    def _write(self, features: list[dict]) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix=".geojsonseq", delete=False)  # noqa: SIM115
        for feature in features:
            handle.write(json.dumps(feature) + "\n")
        handle.close()
        self.addCleanup(Path(handle.name).unlink)
        return Path(handle.name)

    def test_import_replaces_and_takes_a_point_on_areas(self):
        Poi.objects.create(osm_ref="n1", category="toilets", location=route_point(47, 8))
        path = self._write(
            [
                {
                    "type": "Feature",
                    "id": "n2",
                    "properties": {"amenity": "toilets", "name": "WC", "phone": "x"},
                    "geometry": {"type": "Point", "coordinates": [8.1, 47.1]},
                },
                {
                    "type": "Feature",
                    "id": "w3",
                    "properties": {"tourism": "camp_site"},
                    "geometry": {"type": "Polygon", "coordinates": [[[8, 47], [8.01, 47], [8.01, 47.01], [8, 47]]]},
                },
                {
                    "type": "Feature",
                    "id": "n4",
                    "properties": {"highway": "crossing"},
                    "geometry": {"type": "Point", "coordinates": [8, 47]},
                },
            ]
        )
        self.assertEqual(import_pois(path), 2)
        self.assertEqual(set(Poi.objects.values_list("osm_ref", flat=True)), {"n2", "w3"})
        self.assertEqual(Poi.objects.get(osm_ref="n2").tags, {"amenity": "toilets"}, "only whitelisted tags are kept")

    def test_a_machine_selling_several_things_is_one_row_per_category(self):
        path = self._write(
            [
                {
                    "type": "Feature",
                    "id": "n5",
                    "properties": {"amenity": "vending_machine", "vending": "drinks;sweets;condoms"},
                    "geometry": {"type": "Point", "coordinates": [8, 47]},
                }
            ]
        )
        self.assertEqual(import_pois(path), 2)
        self.assertEqual(
            sorted(Poi.objects.values_list("osm_ref", "category")), [("n5", "vending_drinks"), ("n5", "vending_sweets")]
        )

    def test_a_failed_import_keeps_the_old_pois(self):
        Poi.objects.create(osm_ref="n1", category="toilets", location=route_point(47, 8))
        path = self._write(
            [
                {
                    "type": "Feature",
                    "id": "n2",
                    "properties": {"amenity": "toilets"},
                    "geometry": {"type": "Point", "coordinates": [8, 47]},
                }
            ]
        )
        path.write_text(path.read_text() + "{not json\n")
        with self.assertRaises(json.JSONDecodeError):
            import_pois(path)
        self.assertEqual(list(Poi.objects.values_list("osm_ref", flat=True)), ["n1"])

    def test_pois_along_orders_by_position_and_measures_offset(self):
        Poi.objects.create(osm_ref="n1", category="toilets", location=route_point(47.001, 8.02), tags={"a": "b"})
        Poi.objects.create(osm_ref="n2", category="toilets", location=route_point(47.0, 8.005))
        Poi.objects.create(osm_ref="n3", category="toilets", location=route_point(47.05, 8.01))  # ~5.5 km off
        Poi.objects.create(osm_ref="n4", category="food", location=route_point(47.0, 8.01))
        hits = pois_along_sync([[8.0, 47.0], [8.03, 47.0]], ["toilets"], 500)
        self.assertEqual([h.osm_ref for h in hits], ["n2", "n1"])
        self.assertAlmostEqual(hits[1].offset_m, 111, delta=5)
        self.assertAlmostEqual(hits[1].along_m, 1520, delta=30)
        self.assertEqual(hits[1].tags, {"a": "b"})

    def test_planner_queries_return_leg_poi_occurrence_in_postgis(self):
        from .journey_geometry import Limits, LineMeasure
        from .journey_planner import JourneyPlanner, RoutingBudget
        from .tasks import _pois_along
        from .test_journey_routing import fake_geometry, fake_legs

        Poi.objects.create(osm_ref="return_water", category="drinking_water", location=route_point(47, 8.05))

        async def run():
            path = await fake_geometry("bike", [(8, 47), (8.1, 47), (8, 47)])
            planner = JourneyPlanner("bike", None, RoutingBudget(), fake_geometry, fake_legs, _pois_along)
            hits = await planner.window_hits(path, 20, 40, ["drinking_water"], 2500)
            self.assertEqual(len(hits), 1)
            self.assertEqual(LineMeasure(path).index(hits[0].along_m), 30)
            choices = await planner.candidates(path, [], hits, 20, 40, Limits())
            self.assertEqual(len(choices), 1)
            result = await planner.insert(path, [], choices[0], 20, Limits(), is_break=True)
            self.assertIsNotNone(result)
            self.assertGreater(result[2]["index"], 20)

        async_to_sync(run)()


# --------------------------------------------------------------------------- GraphHopper requests
class RoadPrefsTests(SimpleTestCase):
    def test_every_combination_is_penalty_only(self):
        for surface in ("any", "avoid_unpaved", "paved_only"):
            for climbing in ("neutral", "avoid"):
                for traffic in ("neutral", "avoid_main", "avoid_off_network"):
                    for towns in ("neutral", "avoid"):
                        model = road_prefs_model(RoadPrefs(surface, climbing, traffic, towns))
                        self.assertTrue(is_penalty_only(model), model)

    def test_no_preferences_no_model(self):
        self.assertEqual(road_prefs_model(RoadPrefs()), {})

    def test_route_body(self):
        points = ((8.0, 47.0), (8.1, 47.1))
        self.assertNotIn("custom_model", _route_body("bike", points))
        self.assertNotIn("algorithm", _route_body("bike", points, alternatives=1))
        body = _route_body("bike", points, {"priority": [{"if": "x", "multiply_by": "0.5"}]}, 3)
        self.assertEqual(body["algorithm"], "alternative_route")
        self.assertEqual(body["alternative_route.max_paths"], 3)
        self.assertEqual(body["custom_model"]["priority"][0]["multiply_by"], "0.5")

    def test_merge_keeps_chains_and_areas(self):
        merged = merge_models(
            road_prefs_model(RoadPrefs(climbing="avoid")),
            {"priority": [{"if": "in_a_0", "multiply_by": "0.2"}], "areas": {"features": [{"id": "a_0"}]}},
        )
        self.assertEqual([next(iter(s)) for s in merged["priority"]], ["if", "else_if", "if"])
        self.assertEqual(merged["areas"]["features"], [{"id": "a_0"}])


class WeatherZoneTests(SimpleTestCase):
    def test_headwind_condition_wraps_north(self):
        self.assertEqual(headwind_condition(0), "(orientation >= 315 || orientation < 45)")
        self.assertEqual(headwind_condition(2), "(orientation >= 45 && orientation < 135)")

    def test_rain_and_wind_become_penalty_zones(self):
        heavy = {"rain_mm": 2.0, "precipitation_interval_s": 900, "wind_speed": 5, "wind_dir": 0}
        dry_windy = {"rain_mm": 0.0, "precipitation_interval_s": 900, "wind_speed": 40, "wind_dir": 270}
        weather = [((47.0, 8.0), heavy), ((47.0, 8.05), heavy), ((47.5, 8.5), dry_windy)]
        model = zone_model(weather, avoid_rain=True, avoid_headwind=True)
        self.assertTrue(is_penalty_only(model))
        conditions = [s.get("if") or s.get("else_if") for s in model["priority"]]
        self.assertEqual(conditions[0], "in_rain0_0", "two adjacent wet cells are one polygon")
        self.assertIn("orientation >= 225 && orientation < 315", conditions[1])
        self.assertEqual(len(model["areas"]["features"]), 2)

    def test_nothing_to_avoid(self):
        calm = {"rain_mm": 0.0, "precipitation_interval_s": 900, "wind_speed": 5, "wind_dir": 0}
        self.assertEqual(zone_model([((47.0, 8.0), calm)], avoid_rain=True, avoid_headwind=True), {})
        wet = {"rain_mm": 3.0, "precipitation_interval_s": 900}
        self.assertEqual(zone_model([((47.0, 8.0), wet)], avoid_rain=False, avoid_headwind=True), {})

    def test_corridor_cells_are_lattice_keys_with_the_nearest_eta(self):
        cells = corridor_cells(
            [{"lat": 47.0, "lon": 8.0, "elapsed_s": 0}, {"lat": 47.0, "lon": 8.5, "elapsed_s": 3600}]
        )
        self.assertTrue(all(round(k[1] * 20) == k[1] * 20 or abs(k[1] * 20 - round(k[1] * 20)) < 1e-9 for k in cells))
        self.assertEqual(cells[(47.0, 8.0)], 0)
        self.assertEqual(cells[(47.0, 8.5)], 3600)


# --------------------------------------------------------------------------- planning
class JourneyPlanningTests(SimpleTestCase):
    def test_lodging_candidates_filter_kind_and_window(self):
        hotel, camp = hit("lodging", 70_000, tourism="hotel"), hit("lodging", 76_000, tourism="camp_site")
        early = hit("lodging", 10_000, tourism="hotel")
        self.assertEqual(lodging_candidates([hotel, camp, early], ["hotel"]), [hotel, early])
        self.assertEqual(lodging_candidates([hotel, camp, early], [], 60_000), [hotel, camp], "no kinds: every kind")


class RankingTests(SimpleTestCase):
    def _result(self, rain_mm: float) -> dict:
        sample = {"rain_mm": rain_mm, "precipitation_interval_s": 900, "headwind": 0, "temp": 18, "elapsed_s": 0}
        return {"samples": [sample, {**sample, "elapsed_s": 600}], "departure_time": "2030-06-01T08:00:00+02:00"}

    def test_the_dry_alternative_wins_and_reasons_are_words(self):
        rows = rank_day(
            [
                {"id": "wet", "total_seconds": 3600, "gaps": {}, "detours": [], "result": self._result(3.0)},
                {
                    "id": "dry",
                    "total_seconds": 3900,
                    "gaps": {"toilets": 30_000},
                    "detours": [],
                    "result": self._result(0),
                },
            ],
            20_000,
        )
        by_id = {row["id"]: row for row in rows}
        self.assertTrue(by_id["dry"]["recommended"])
        self.assertFalse(by_id["wet"]["recommended"])
        self.assertIn("Toilette: 30 km ohne", by_id["dry"]["reasons"])
        self.assertIn("8 % länger als die schnellste Variante", by_id["dry"]["reasons"])
        self.assertNotIn("_combined", by_id["dry"], "the ranking value stays on the server")

    def test_unscored_stages_still_rank(self):
        rows = rank_day([{"id": "a", "total_seconds": 60, "gaps": {}, "detours": [], "result": None}], 0)
        self.assertIsNone(rows[0]["ride_score"])
        self.assertTrue(rows[0]["recommended"])


# --------------------------------------------------------------------------- tasks
def _gh_path(km: float, lon0: float = 8.0) -> dict:
    return straight_geometry(km, lon0)


@override_settings(CACHES=fixtures.LOCMEM_CACHE, CHANNEL_LAYERS=fixtures.INMEM_CHANNELS)
class JourneyTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="traveller", email="t@example.com", password="pw")
        self.journey = Journey.objects.create(
            owner=self.user,
            name="Tour",
            start_point=route_point(47.0, 8.0),
            start_name="A",
            destination_point=route_point(47.0, 10.0),
            dest_name="B",
            start_date=local_today() + timedelta(days=10),
            earliest_start=time(8),
            latest_arrival=time(20),
            max_day_distance_m=80_000,
            max_leg_distance_m=25_000,
            poi_categories=["drinking_water"],
            weather_prefs={"avoid_rain": True, "avoid_headwind": True},
        )
        Poi.objects.create(
            osm_ref="n1", category="lodging", tags={"tourism": "hotel"}, location=route_point(47.0005, 8.99)
        )

    def _plan(self, *, paths=None):
        from .test_journey_routing import fake_geometry, fake_legs

        geometries = AsyncMock(side_effect=lambda profile, points, n, *a, **k: paths or [_gh_path(70, points[0][0])])
        routes = SimpleNamespace(aenqueue=AsyncMock(), using=lambda **_: SimpleNamespace(aenqueue=AsyncMock()))
        with (
            patch("core.tasks.build_geometry", AsyncMock(side_effect=fake_geometry)) as geometry,
            patch("core.tasks.route_legs", AsyncMock(side_effect=fake_legs)),
            patch("core.tasks.build_geometries", geometries),
            patch("core.tasks.plan_journey_routes", routes),
        ):
            async_to_sync(_plan_journey_async)(str(self.journey.id), self.journey.plan_revision)
            async_to_sync(_plan_journey_routes_async)(str(self.journey.id), self.journey.plan_revision)
        self.journey.refresh_from_db()
        return geometry, geometries

    def test_plans_days_ending_at_lodging_with_stages(self):
        _, geometries = self._plan()
        self.assertEqual(self.journey.plan_status, Journey.PlanStatus.DONE, self.journey.plan_error)
        days = list(self.journey.days.all())
        self.assertEqual(days[0].lodging["osm_ref"], "n1")
        self.assertFalse(days[0].weather_routed, "ten days out is too far to route around weather")
        self.assertEqual(geometries.await_args.args[2], 1, "a free account gets one path per day")
        stage = days[0].stages.get()
        self.assertEqual(stage.leg_m, 25_000)
        self.assertGreater(len(stage.breaks), 0)

    def test_pro_gets_alternatives(self):
        Subscription.objects.create(
            user=self.user, plan=Plan.PRO, complimentary_until=datetime.now(UTC) + timedelta(days=1)
        )
        _, geometries = self._plan()
        self.assertEqual(geometries.await_args.args[2], 3)

    def test_a_stale_revision_writes_nothing(self):
        revision = self.journey.plan_revision
        Journey.objects.filter(id=self.journey.id).update(plan_revision=revision + 1)
        with patch("core.tasks.build_geometry", AsyncMock(side_effect=AssertionError("routed!"))):
            async_to_sync(_plan_journey_async)(str(self.journey.id), revision)
        self.assertEqual(JourneyDay.objects.count(), 0)

    def test_remainder_failure_marks_plan_failed_and_does_not_enqueue(self):
        from .test_journey_routing import fake_geometry

        calls = 0

        async def route(profile, points, *args):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise ValueError("no remainder")
            return await fake_geometry(profile, points)

        enqueue = AsyncMock()
        with (
            patch("core.tasks.build_geometry", side_effect=route),
            patch("core.tasks._pois_along", AsyncMock(return_value=[])),
            patch("core.tasks.plan_journey_routes", SimpleNamespace(aenqueue=enqueue)),
        ):
            async_to_sync(_plan_journey_async)(str(self.journey.id), self.journey.plan_revision)
        self.journey.refresh_from_db()
        self.assertEqual(self.journey.plan_status, Journey.PlanStatus.FAILED)
        enqueue.assert_not_awaited()

    def test_loop_waypoints_are_assigned_once_in_visit_order(self):
        from .journey_planner import RoutingBudget
        from .tasks import _plan_day_ends
        from .test_journey_routing import fake_geometry

        self.journey.via_points = [[8.7, 47], [8.0, 47], [9.2, 47]]
        self.journey.save()
        with (
            patch("core.tasks.build_geometry", side_effect=fake_geometry),
            patch("core.tasks._pois_along", AsyncMock(return_value=[])),
        ):
            days = async_to_sync(_plan_day_ends)(self.journey, FREE, None, RoutingBudget())
        self.assertEqual([p for day in days for p in day["vias"]], self.journey.via_points)
        self.assertGreater(len(days), 2)

    def test_stored_plan_round_trips_limits_gaps_and_lodging(self):
        from .tasks import _store_journey_plan
        from .test_journey_routing import geometry

        path = geometry([[8, 47], [8.01, 47]], [0, 900])
        stage = {
            "geometry": path,
            "rank": 0,
            "via_points": [],
            "breaks": [],
            "gaps": {"drinking_water": {"s": 900, "m": 1500}, "toilets": 1700},
            "detours": [],
            "detour_m": 0,
            "leg_m": 1000,
            "leg_seconds": 600,
            "limit_overruns": {
                "day": {"over_s": 60, "over_m": 200},
                "legs": [{"leg": 1, "over_s": 300, "over_m": 500}],
            },
        }
        day = {
            "index": 0,
            "date": (local_today() + timedelta(days=30)).isoformat(),
            "start": [8, 47],
            "end": [8.01, 47],
            "lodging": None,
            "lodging_detour": {"s": 20, "m": 100},
            "lodging_missing": False,
            "weather": False,
            "stages": [stage],
        }
        _store_journey_plan(self.journey.id, self.journey.plan_revision, [day])
        self.client.force_login(self.user)
        response = self.client.get(f"/api/journeys/{self.journey.id}")
        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()["days"][0]
        self.assertEqual(result["lodging_detour"], {"s": 20, "m": 100})
        stored = result["stages"][0]
        self.assertEqual(stored["leg_seconds"], 600)
        self.assertEqual(stored["limit_overruns"]["legs"][0]["leg"], 1)
        self.assertEqual(stored["gaps"]["toilets"], {"s": None, "m": 1700})
        self.assertIn("Tageslimit: ~1 min zu lang", stored["reasons"])
        self.assertIn("Etappe 1: ~5 min zu lang", stored["reasons"])

    def test_weather_routing_only_for_pro_days_near_now(self):
        Subscription.objects.create(
            user=self.user, plan=Plan.PRO, complimentary_until=datetime.now(UTC) + timedelta(days=1)
        )
        self.journey.start_date = local_today() + timedelta(days=2)
        self.journey.save()
        from .test_journey_routing import fake_geometry, fake_legs

        cells = SimpleNamespace(aenqueue=AsyncMock())
        with (
            patch("core.tasks.build_geometry", AsyncMock(side_effect=fake_geometry)),
            patch("core.tasks.route_legs", AsyncMock(side_effect=fake_legs)),
            patch("core.tasks.refresh_forecast_cell", cells),
            patch(
                "core.tasks.plan_journey_routes",
                SimpleNamespace(aenqueue=AsyncMock(), using=lambda **_: SimpleNamespace(aenqueue=AsyncMock())),
            ),
        ):
            async_to_sync(_plan_journey_async)(str(self.journey.id), self.journey.plan_revision)
        self.journey.refresh_from_db()
        flags = [day["weather"] for day in self.journey.plan_state["days"]]
        self.assertEqual(flags, [True, False], "the second day is past WEATHER_ROUTING_DAYS")
        self.assertEqual(self.journey.plan_status, Journey.PlanStatus.WEATHER)
        self.assertGreater(cells.aenqueue.await_count, 0)


@override_settings(CACHES=fixtures.LOCMEM_CACHE, CHANNEL_LAYERS=fixtures.INMEM_CHANNELS)
class JourneyStageJobTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="traveller", email="t@example.com", password="pw")
        self.journey = Journey.objects.create(
            owner=self.user,
            name="Tour",
            start_point=route_point(47, 8),
            start_name="A",
            destination_point=route_point(47, 8.1),
            dest_name="B",
            start_date=local_today() + timedelta(days=1),
            earliest_start=time(8),
            latest_arrival=time(18),
            max_day_distance_m=80_000,
            plan_status="done",
        )
        day = JourneyDay.objects.create(
            journey=self.journey, index=0, date=self.journey.start_date, start=[8, 47], end=[8.1, 47]
        )
        geometry = straight_geometry(7)
        self.stage = JourneyStage.objects.create(
            day=day,
            rank=0,
            polyline=route_line(geometry["polyline"]),
            total_seconds=geometry["total_seconds"],
            total_distance_m=geometry["total_distance_m"],
            sample_points=geometry["sample_points"],
            vertex_times=geometry["vertex_times"],
            geometry_fetched_at=datetime.now(UTC),
        )

    def _job(self, owner=None):
        with patch("core.tasks.plan_forecast_job", SimpleNamespace(aenqueue=AsyncMock())):
            return async_to_sync(start_forecast_job)(
                ForecastJob.Kind.JOURNEY_STAGE,
                owner or self.user,
                {"journey_stage_id": str(self.stage.id), "departure_time": "2030-06-01T08:00:00+02:00"},
            )

    def test_geometry_comes_from_the_stage_and_the_revision_is_keyed(self):
        job = self._job()
        self.assertEqual(job.params["geometry_revision"], self.stage.geometry_fetched_at.isoformat())
        geometry = async_to_sync(_job_geometry)(job)
        self.assertEqual(geometry["total_seconds"], self.stage.total_seconds)

    def test_never_spends_station_calls(self):
        job = self._job()
        with patch("core.tasks.api_key", return_value="key"):
            self.assertFalse(async_to_sync(_wants_stations)(job, 600))

    def test_another_account_cannot_forecast_the_stage(self):
        other = User.objects.create_user(username="other", email="o@example.com", password="pw")
        job = self._job(owner=other)
        async_to_sync(_plan_forecast_job_async)(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.status, ForecastJob.Status.FAILED)


# --------------------------------------------------------------------------- API
@override_settings(CACHES=fixtures.LOCMEM_CACHE, CHANNEL_LAYERS=fixtures.INMEM_CHANNELS)
class JourneyApiTests(TestCase):
    BODY = {
        "name": "Tour",
        "startLat": 47.0,
        "startLon": 8.0,
        "startName": "A",
        "destLat": 47.0,
        "destLon": 9.0,
        "destName": "B",
        "startDate": (local_today() + timedelta(days=1)).isoformat(),
        "maxDayDistanceM": 80000,
        "maxLegDistanceM": 25000,
        "poiCategories": ["drinking_water", "toilets"],
        "roadPrefs": {"surface": "avoid_unpaved"},
    }

    def setUp(self):
        self.user = User.objects.create_user(username="traveller", email="t@example.com", password="pw")
        self.client = Client()
        self.client.force_login(self.user)

    def _post(self, body=None):
        with patch("core.api.journey.plan_journey", SimpleNamespace(aenqueue=AsyncMock())) as plan:
            response = self.client.post("/api/journeys", body or self.BODY, content_type="application/json")
        return response, plan.aenqueue

    def test_create_enqueues_planning_and_respects_the_quota(self):
        response, enqueue = self._post()
        self.assertEqual(response.status_code, 201, response.content)
        journey = Journey.objects.get()
        self.assertEqual(journey.road_prefs["surface"], "avoid_unpaved")
        enqueue.assert_awaited_once_with(str(journey.id), 0)
        response, enqueue = self._post()
        self.assertEqual(response.status_code, 402)
        enqueue.assert_not_awaited()

    def test_validation(self):
        response, _ = self._post({**self.BODY, "poiCategories": ["casino"]})
        self.assertEqual(response.status_code, 422)
        response, _ = self._post({**self.BODY, "maxDayDistanceM": None})
        self.assertEqual(response.status_code, 422)

    def test_update_replans_with_a_new_revision(self):
        self._post()
        journey = Journey.objects.get()
        with patch("core.api.journey.plan_journey", SimpleNamespace(aenqueue=AsyncMock())) as plan:
            response = self.client.put(
                f"/api/journeys/{journey.id}", {**self.BODY, "name": "Neu"}, content_type="application/json"
            )
        self.assertEqual(response.status_code, 200, response.content)
        plan.aenqueue.assert_awaited_once_with(str(journey.id), 1)

    def test_reading_starts_the_stage_forecasts(self):
        self._post()
        journey = Journey.objects.get()
        day = JourneyDay.objects.create(journey=journey, index=0, date=journey.start_date, start=[8, 47], end=[9, 47])
        geometry = straight_geometry(7)
        stage = JourneyStage.objects.create(
            day=day,
            rank=0,
            polyline=route_line(geometry["polyline"]),
            total_seconds=geometry["total_seconds"],
            total_distance_m=geometry["total_distance_m"],
            sample_points=geometry["sample_points"],
            vertex_times=geometry["vertex_times"],
            geometry_fetched_at=datetime.now(UTC),
            leg_m=25_000,
        )
        with patch("core.tasks.plan_forecast_job", SimpleNamespace(aenqueue=AsyncMock())) as plan:
            response = self.client.get(f"/api/journeys/{journey.id}")
        self.assertEqual(response.status_code, 200, response.content)
        stage_out = response.json()["days"][0]["stages"][0]
        job = ForecastJob.objects.get(kind=ForecastJob.Kind.JOURNEY_STAGE)
        self.assertEqual(stage_out["forecast_job_id"], str(job.id))
        self.assertEqual(job.params["journey_stage_id"], str(stage.id))
        self.assertNotIn("departure_flex_after_minutes", job.params, "the departure window is Plus")
        plan.aenqueue.assert_awaited_once()

        other = User.objects.create_user(username="other", email="o@example.com", password="pw")
        self.client.force_login(other)
        self.assertEqual(self.client.get(f"/api/journeys/{journey.id}").status_code, 404)

    def test_a_refreshing_stage_is_ranked_by_its_stale_result(self):
        """The recommendation must not blink out every time a stage forecast is renewed."""
        self._post()
        journey = Journey.objects.get()
        self._stage_on_day(journey, 0)
        stale = {
            "samples": [],
            "summary": {},
            "computed_at": datetime.now(UTC).isoformat(),
            "entitlements": FREE.result_marker(),  # built for this tier, or the read drops it
        }
        with patch("core.tasks.plan_forecast_job", SimpleNamespace(aenqueue=AsyncMock())):
            self.client.get(f"/api/journeys/{journey.id}")
            ForecastJob.objects.update(status=ForecastJob.Status.FETCHING, stale_result=stale)
            with patch("core.api.journey.rank_day", return_value=[]) as rank_day:
                response = self.client.get(f"/api/journeys/{journey.id}")
        self.assertEqual(response.status_code, 200, response.content)
        rows = rank_day.call_args.args[0]
        self.assertEqual(rows[0]["result"], stale)
        self.assertEqual(response.json()["days"][0]["stages"][0]["forecast_status"], "fetching")

    def test_limits_detours_and_both_gap_shapes_round_trip(self):
        self._post()
        journey = Journey.objects.get()
        stage = self._stage_on_day(journey, 0, lodging_detour={"s": 240, "m": 900.0})
        JourneyStage.objects.filter(id=stage.id).update(
            leg_seconds=3600,
            leg_m=25_000,
            # A legacy metres-only gap next to a new one that breaks the time limit only.
            gaps={"drinking_water": 30_000.0, "toilets": {"s": 4200, "m": 20_000.0}},
            detours=[{**hit("toilets", 3000).as_json(), "detour_s": 180, "detour_m": 1200.0}],
            limit_overruns={"day": {"over_s": 0, "over_m": 2500.0}, "legs": [{"leg": 2, "over_s": 300, "over_m": 0}]},
        )
        with patch("core.tasks.plan_forecast_job", SimpleNamespace(aenqueue=AsyncMock())):
            response = self.client.get(f"/api/journeys/{journey.id}")
        self.assertEqual(response.status_code, 200, response.content)
        day = response.json()["days"][0]
        stage_out = day["stages"][0]
        self.assertEqual(day["lodging_detour"], {"s": 240, "m": 900.0})
        self.assertEqual(stage_out["leg_seconds"], 3600)
        self.assertEqual(stage_out["gaps"]["drinking_water"], {"s": None, "m": 30_000.0})
        self.assertEqual(stage_out["gaps"]["toilets"], {"s": 4200, "m": 20_000.0})
        self.assertEqual(stage_out["limit_overruns"]["day"]["over_m"], 2500.0)
        self.assertEqual(stage_out["limit_overruns"]["legs"][0]["leg"], 2)
        self.assertEqual(stage_out["detours"][0]["detour_m"], 1200.0)
        reasons = stage_out["reasons"]
        self.assertIn("Trinkwasser: 30 km ohne", reasons)
        self.assertIn("Toilette: 70 min ohne", reasons)
        self.assertIn("Umweg ~1.2 km zu Toilette", reasons)
        self.assertIn("Tageslimit: ~2.5 km zu weit", reasons)
        self.assertIn("Etappe 2: ~5 min zu lang", reasons)
        self.assertFalse(any(r.startswith("Tageslimit:") and "min" in r for r in reasons), "distance-only overrun")

    def _stage_on_day(self, journey, index: int, **day_fields) -> JourneyStage:
        day = JourneyDay.objects.create(
            journey=journey, index=index, date=journey.start_date, start=[8, 47], end=[8.13, 47], **day_fields
        )
        geometry = straight_geometry(10)  # 8.0 .. ~8.13 °E
        return JourneyStage.objects.create(
            day=day,
            rank=0,
            polyline=route_line(geometry["polyline"]),
            total_seconds=geometry["total_seconds"],
            total_distance_m=geometry["total_distance_m"],
            sample_points=geometry["sample_points"],
            vertex_times=geometry["vertex_times"],
            geometry_fetched_at=datetime.now(UTC),
        )

    def test_stage_pois_offer_lodging_where_the_day_could_end(self):
        self._post()
        journey = Journey.objects.get()
        journey.lodging_kinds = ["hotel"]
        journey.save()
        lodging = {"osm_ref": "n_hotel", "category": "lodging", "name": "", "lon": 8.12, "lat": 47.005}
        lodging |= {"along_m": 9000.0, "offset_m": 550.0}
        stage = self._stage_on_day(journey, 0, lodging=lodging)
        last = self._stage_on_day(journey, 1)
        Poi.objects.create(osm_ref="n_wc", category="toilets", location=route_point(47.001, 8.05))
        Poi.objects.create(
            osm_ref="n_hotel", category="lodging", location=route_point(47.005, 8.12), tags={"tourism": "hotel"}
        )
        Poi.objects.create(
            osm_ref="n_late", category="lodging", location=route_point(47.01, 8.11), tags={"tourism": "hotel"}
        )
        Poi.objects.create(
            osm_ref="n_early", category="lodging", location=route_point(47.005, 8.02), tags={"tourism": "hotel"}
        )
        Poi.objects.create(
            osm_ref="n_camp", category="lodging", location=route_point(47.005, 8.125), tags={"tourism": "camp_site"}
        )

        def refs(target, query):
            response = self.client.get(f"/api/journeys/{journey.id}/stages/{target.id}/pois?{query}")
            self.assertEqual(response.status_code, 200, response.content)
            return [poi["osm_ref"] for poi in response.json()]

        self.assertEqual(refs(stage, "categories=toilets"), ["n_wc"], "no lodging unless asked for")
        self.assertEqual(refs(stage, "categories=toilets&lodging=true"), ["n_wc", "n_late", "n_hotel"])
        self.assertEqual(refs(stage, "lodging=true"), ["n_late", "n_hotel"], "lodging alone is not every category")
        self.assertEqual(
            refs(last, "categories=toilets&lodging=true"), ["n_wc"], "the last day ends at the destination"
        )

    def test_stage_pois_show_the_area_not_only_the_line(self):
        self._post()
        journey = Journey.objects.get()
        stage = self._stage_on_day(journey, 0)
        Poi.objects.create(osm_ref="n_on", category="toilets", location=route_point(47.001, 8.02))
        Poi.objects.create(osm_ref="n_village", category="toilets", location=route_point(47.009, 8.05))  # ~1 km
        Poi.objects.create(osm_ref="n_far", category="toilets", location=route_point(47.027, 8.08))  # ~3 km

        response = self.client.get(f"/api/journeys/{journey.id}/stages/{stage.id}/pois?categories=toilets")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([poi["osm_ref"] for poi in response.json()], ["n_on", "n_village"])
