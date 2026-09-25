"""GPX interchange, exact geometry, public access and saved-route persistence."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase, override_settings

from . import tests as fixtures
from .gpx import MAX_GPX_BYTES, distances, exact_geometry, guided_points, parse_gpx, serialize_gpx, validate_track
from .jobs import job_key
from .models import ForecastJob, RecurringRoute
from .tasks import _job_geometry, _refresh_route_geometry_async
from .wind import valid_vertex_times

POINTS = [[9.0, 47.0, 500], [9.015, 47.005, 520], [9.01, 47.01, 505]]


class GpxTests(SimpleTestCase):
    def test_round_trip_preserves_every_point_and_elevation(self):
        result = parse_gpx(serialize_gpx('Ride <&> "Zürich"', POINTS))
        self.assertEqual(result[0]["coordinates"], POINTS)
        self.assertEqual(result[0]["name"], 'Ride <&> "Zürich"')
        self.assertNotIn(b"<time>", serialize_gpx("ride", POINTS))

    def test_versions_routes_and_namespaces(self):
        for version in ("1.0", "1.1"):
            for ns in ("", f' xmlns="http://www.topografix.com/GPX/1/{version[-1]}"'):
                with self.subTest(version=version, ns=ns):
                    raw = (
                        f'<gpx version="{version}"{ns}>'
                        '<rte><rtept lat="47" lon="9"/><rtept lat="48" lon="10"/></rte>'
                        "</gpx>"
                    )
                    self.assertEqual(parse_gpx(raw.encode())[0]["coordinates"], [[9, 47], [10, 48]])

    def test_segments_are_separate_choices(self):
        raw = (
            b'<gpx version="1.1"><trk><name>Ride</name>'
            b'<trkseg><trkpt lat="47" lon="9"/><trkpt lat="48" lon="10"/></trkseg>'
            b'<trkseg><trkpt lat="49" lon="11"/><trkpt lat="50" lon="12"/></trkseg>'
            b"</trk></gpx>"
        )
        paths = parse_gpx(raw)
        self.assertEqual(len(paths), 2)
        self.assertEqual(paths[0]["coordinates"][-1], [10, 48])
        self.assertEqual(paths[1]["coordinates"][0], [11, 49])

    def test_rejects_invalid_and_waypoint_only_files(self):
        for raw in (
            b"broken",
            b'<gpx version="2"/>',
            b'<gpx version="1.1"><wpt lat="47" lon="9"/></gpx>',
            b'<gpx version="1.1"><rte><rtept lat="NaN" lon="9"/><rtept lat="47" lon="9"/></rte></gpx>',
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_gpx(raw)

    def test_dtd_and_entities_rejected_in_utf8_and_utf16(self):
        xml = (
            '<!DOCTYPE gpx [<!ENTITY leak SYSTEM "file:///not-read">]>'
            '<gpx version="1.1"><rte><name>&leak;</name></rte></gpx>'
        )
        for encoding in ("utf-8", "utf-16"):
            with self.subTest(encoding=encoding), self.assertRaises(ValueError):
                parse_gpx(xml.encode(encoding))

    def test_size_and_point_limits(self):
        with self.assertRaises(ValueError):
            parse_gpx(b" " * (MAX_GPX_BYTES + 1))
        with self.assertRaises(ValueError):
            validate_track([[9, 47]] * 100001)
        with self.assertRaises(ValueError):
            validate_track([[9, 47], [9, 47]])
        for points in ([[9, 47], [181, 47]], [[9, 47], [9, float("inf")]], [[9, 47], [9, 48, float("nan")]]):
            with self.assertRaises(ValueError):
                validate_track(points)

    def test_sparse_track_is_sampled_between_vertices(self):
        geometry = exact_geometry([[9, 47], [9.1, 47]], 3600)
        self.assertEqual(len(geometry["sample_points"]), 13)
        self.assertEqual(geometry["sample_points"][6]["elapsed_s"], 1800)
        self.assertAlmostEqual(geometry["sample_points"][6]["lon"], 9.05)
        self.assertTrue(valid_vertex_times(geometry["polyline"], geometry["vertex_times"]))

    def test_distance_weights_times_and_retains_loops(self):
        points = [*POINTS, POINTS[0]]
        geometry = exact_geometry(points, 1200)
        self.assertEqual(geometry["polyline"][0], geometry["polyline"][-1])
        self.assertAlmostEqual(geometry["total_distance_m"], distances(points)[-1])
        self.assertTrue(valid_vertex_times(geometry["polyline"], geometry["vertex_times"]))
        self.assertEqual(geometry["vertex_times"][-1], 1200)

    def test_timing_limits(self):
        for value in (0, -1, float("inf"), 1382401):
            with self.assertRaises(ValueError):
                exact_geometry(POINTS, value)

    def test_guided_points_preserve_order_corners_and_endpoints(self):
        points = [[9 + i * 0.001, 47 + (i % 7) * 0.001] for i in range(100)]
        selected = guided_points(points)
        self.assertLessEqual(len(selected), 17)
        self.assertEqual(selected[0], points[0])
        self.assertEqual(selected[-1], points[-1])
        self.assertEqual([points.index(p) for p in selected], sorted(points.index(p) for p in selected))
        self.assertEqual(selected, guided_points(points))

    def test_cache_key_separates_shapes_modes_and_times(self):
        base = {"coordinates": POINTS, "geometry_source": "imported", "duration_seconds": 600}
        key = job_key("adhoc", None, base)
        for changes in (
            {"duration_seconds": 900},
            {"coordinates": list(reversed(POINTS))},
            {"geometry_source": "graphhopper"},
        ):
            self.assertNotEqual(key, job_key("adhoc", None, base | changes))


@override_settings(CACHES=fixtures.LOCMEM_CACHE, CHANNEL_LAYERS=fixtures.INMEM_CHANNELS)
class GpxApiTests(TestCase):
    def setUp(self):
        fixtures.ForecastJobTests.setUp(self)
        self.client = Client()
        self.client.force_login(self.user)

    def put(self, **changes):
        body = self.client.get(f"/api/routes/{self.route.id}").json() | changes
        with patch("core.api.recurring_route.refresh_route_geometry", SimpleNamespace(aenqueue=AsyncMock())) as task:
            response = self.client.put(f"/api/routes/{self.route.id}", body, content_type="application/json")
        return response, task.aenqueue

    def import_route(self):
        response, _ = self.put(geometrySource="imported", importedCoordinates=POINTS, durationSeconds=1800)
        self.assertEqual(response.status_code, 200, response.content)
        self.route.refresh_from_db()

    def test_public_import_export_and_no_saved_route_created(self):
        self.client.logout()
        before = RecurringRoute.objects.count()
        response = self.client.post(
            "/api/gpx/import", {"file": SimpleUploadedFile("ride.gpx", serialize_gpx("Ride", POINTS))}
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()[0]["coordinates"], POINTS)
        response = self.client.post(
            "/api/gpx/export", {"name": "Ride", "coordinates": POINTS}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(parse_gpx(response.content)[0]["coordinates"], POINTS)
        self.assertEqual(RecurringRoute.objects.count(), before)

    def test_csrf_is_still_required_for_anonymous_uploads(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(
            "/api/gpx/import", {"file": SimpleUploadedFile("ride.gpx", serialize_gpx("Ride", POINTS))}
        )
        self.assertEqual(response.status_code, 403)

    def test_rate_limit(self):
        self.client.logout()
        with patch("time.time", return_value=1900000000):
            for _ in range(30):
                response = self.client.post("/api/gpx/export", {"coordinates": POINTS}, content_type="application/json")
                self.assertEqual(response.status_code, 200)
            self.assertEqual(
                self.client.post(
                    "/api/gpx/export", {"coordinates": POINTS}, content_type="application/json"
                ).status_code,
                429,
            )

    def test_invalid_upload_leaves_saved_route_unchanged(self):
        response = self.client.post("/api/gpx/import", {"file": SimpleUploadedFile("bad.gpx", b"bad")})
        self.assertEqual(response.status_code, 422)
        self.route.refresh_from_db()
        self.assertEqual(self.route.geometry_source, "graphhopper")

    def test_public_forecast_uses_exact_geometry_without_routing(self):
        self.client.logout()
        with patch("core.tasks.plan_forecast_job", SimpleNamespace(aenqueue=AsyncMock())):
            response = self.client.post(
                "/api/route_weather",
                {
                    "geometry_source": "imported",
                    "coordinates": POINTS,
                    "duration_seconds": 1800,
                    "departure_time": self.departure.isoformat(),
                },
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 202, response.content)
        job = ForecastJob.objects.get(id=response.json()["job_id"])
        self.assertIsNone(job.owner_id)
        with patch("core.tasks.build_geometry", AsyncMock(side_effect=AssertionError("routed"))):
            geometry = async_to_sync(_job_geometry)(job)
        self.assertEqual(geometry["total_seconds"], 1800)
        self.assertEqual(self.client.get(f"/api/forecast_jobs/{job.id}").status_code, 200)
        self.assertEqual(
            parse_gpx(self.client.get(f"/api/forecast_jobs/{job.id}/gpx").content)[0]["coordinates"], POINTS
        )

    def test_guided_forecast_keeps_via_points(self):
        with patch("core.tasks.plan_forecast_job", SimpleNamespace(aenqueue=AsyncMock())):
            response = self.client.post(
                "/api/route_weather",
                {"coordinates": [p[:2] for p in POINTS], "departure_time": self.departure.isoformat()},
                content_type="application/json",
            )
        job = ForecastJob.objects.get(id=response.json()["job_id"])
        with patch("core.tasks.build_geometry", AsyncMock(return_value={})) as build:
            async_to_sync(_job_geometry)(job)
        self.assertEqual(build.await_args.args[1], tuple(tuple(p[:2]) for p in POINTS))

    def test_refresh_and_backfill_never_reroute_import(self):
        self.import_route()
        with (
            patch("core.tasks.build_geometry", AsyncMock(side_effect=AssertionError("routed"))),
            patch("core.tasks.refresh_route_thumbnail", SimpleNamespace(aenqueue=AsyncMock())),
        ):
            async_to_sync(_refresh_route_geometry_async)(str(self.route.id))
            async_to_sync(_refresh_route_geometry_async)(str(self.route.id), backfill_only=True)
        self.route.refresh_from_db()
        self.assertEqual(self.route.imported_coordinates, POINTS)
        self.assertEqual(self.route.total_seconds, 1800)
        self.assertTrue(valid_vertex_times(self.route.polyline_coordinates, self.route.vertex_times))

    def test_schedule_edit_preserves_import_and_duration_edit_invalidates(self):
        self.import_route()
        response, task = self.put(name="Changed", scheduleCron="0 9 * * *")
        self.assertEqual(response.status_code, 200, response.content)
        task.assert_not_awaited()
        self.assertEqual(response.json()["imported_coordinates"], POINTS)
        response, task = self.put(durationSeconds=2400)
        self.assertEqual(response.status_code, 200, response.content)
        task.assert_awaited_once()
        self.route.refresh_from_db()
        self.assertIsNone(self.route.geometry_fetched_at)
        self.assertIsNone(self.route.thumbnail)

    def test_export_and_detail_are_private_but_list_omits_coordinates(self):
        self.import_route()
        response = self.client.get(f"/api/routes/{self.route.id}/gpx")
        self.assertEqual(parse_gpx(response.content)[0]["coordinates"], POINTS)
        self.assertIsNone(self.client.get("/api/routes").json()[0]["imported_coordinates"])
        self.client.logout()
        self.assertIn(self.client.get(f"/api/routes/{self.route.id}/gpx").status_code, (401, 404))

    def test_return_import_reverses_points_and_duration(self):
        self.import_route()
        response, _ = self.put(returnScheduleCron="0 17 * * *", returnScheduleDescription="17:00")
        self.assertEqual(response.status_code, 200, response.content)
        returning = RecurringRoute.objects.get(return_of=self.route)
        self.assertEqual(returning.imported_coordinates, list(reversed(POINTS)))
        self.assertEqual(returning.duration_seconds, 1800)
        self.assertEqual(returning.geometry_source, "imported")

    def test_missing_duration_and_bad_shape_rejected(self):
        response, _ = self.put(geometrySource="imported", importedCoordinates=POINTS, durationSeconds=None)
        self.assertEqual(response.status_code, 422)
        response, _ = self.put(geometrySource="imported", importedCoordinates=[[9, 47]], durationSeconds=100)
        self.assertEqual(response.status_code, 422)
