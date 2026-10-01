"""Operations queries must stay private, bounded and free of forecast side effects."""

from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice
from redis.exceptions import ConnectionError as RedisConnectionError

from backend.asgi import application

from . import data_coverage, jobs
from .api.system import cell_ring, coverage_state, viewport_boxes
from .auth.admin_access import has_system_access
from .consumers import SystemEventsConsumer
from .grid import ENSEMBLE_REQUEST_VERSION, MAX_CELL_AGE
from .models import (
    EnsembleCell,
    ForecastCell,
    ForecastJob,
    Journey,
    JourneyDay,
    JourneyStage,
    RecurringRoute,
    User,
    route_line,
    route_point,
)
from .schedule import LOCAL_TZ
from .system_events import notify_system
from .test_fingerprinting import LOCMEM, recognise
from .test_signup import TEST_SETTINGS


class SystemCoverageTests(SimpleTestCase):
    def setUp(self):
        self.now = datetime(2030, 6, 1, 8, tzinfo=UTC)
        self.row = SimpleNamespace(
            fetched_at=self.now,
            forecast_days=2,
            source="openweathermap",
            data={"hourly": [{"dt": self.now.timestamp(), "temp": 15}]},
        )

    def state(self, rows, days=2, etas=None, **kwargs):
        return coverage_state(rows, days, etas or [self.now], self.now, **kwargs)

    def test_age_boundary_and_insufficient_horizon(self):
        self.assertEqual(self.state([]), "missing")
        self.row.fetched_at = self.now - MAX_CELL_AGE
        self.assertEqual(self.state([self.row]), "usable")
        self.assertEqual(self.state([self.row], days=3), "insufficient")
        self.row.fetched_at -= timedelta(microseconds=1)
        self.assertEqual(self.state([self.row]), "stale")

    def test_source_formats_and_fallback_coverage(self):
        bad = SimpleNamespace(fetched_at=self.now, forecast_days=2, source="open-meteo", data={"hourly": []})
        self.assertEqual(self.state([bad]), "insufficient")
        self.assertEqual(self.state([bad, self.row]), "insufficient")
        bad.forecast_days = 1
        self.assertEqual(self.state([bad, self.row]), "usable")
        self.assertEqual(self.state([self.row], etas=[self.now, self.now + timedelta(hours=1)]), "insufficient")

    def test_ensemble_requires_current_version_and_real_samples(self):
        self.row.data = {
            "_norain_request_version": ENSEMBLE_REQUEST_VERSION,
            "hourly": {"time": ["2030-06-01T10:00"], "temperature_2m_member01": [15], "temperature_2m_member02": [16]},
        }
        self.assertEqual(self.state([self.row], ensemble=True), "usable")
        self.row.data["_norain_request_version"] = -1
        self.assertEqual(self.state([self.row], ensemble=True), "insufficient")
        self.row.data = {"_norain_request_version": ENSEMBLE_REQUEST_VERSION, "hourly": {"time": ["2030-06-01T10:00"]}}
        self.assertEqual(self.state([self.row], ensemble=True), "insufficient")

    def test_grid_and_dateline_bounds(self):
        self.assertEqual(
            cell_ring(47, 8), [[7.995, 46.995], [8.005, 46.995], [8.005, 47.005], [7.995, 47.005], [7.995, 46.995]]
        )
        self.assertEqual(len(viewport_boxes("170,-10,-170,10")), 2)


# ADMIN_OTP: exercise the production policy even when local development turns it off.
@override_settings(**TEST_SETTINGS, ADMIN_OTP=True)
class SystemApiTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.user = User.objects.create_user(
            username="Operator", email="operator@example.test", is_staff=True, signup_completed=True
        )
        self.device = TOTPDevice.objects.create(user=self.user, name="admin", confirmed=True)
        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session["otp_device_id"] = self.device.persistent_id
        session.save()
        self.route = RecurringRoute.objects.create(
            name="Commute",
            start_point=route_point(47, 8),
            destination_point=route_point(47.01, 8.01),
            start_name="A",
            dest_name="B",
            schedule_cron="0 8 * * *",
            schedule_description="Daily",
            polyline=route_line([[8, 47], [8.01, 47.01]]),
            sample_points=[{"lat_r": 47.0, "lon_r": 8.0, "elapsed_s": 0}],
            total_seconds=1800,
            total_distance_m=5000,
        )

    def get(self, path, **params):
        response = self.client.get("/api/system/" + path, params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def cell(self, model=ForecastCell, **kwargs):
        defaults = {
            "lat_r": 47.0,
            "lon_r": 8.0,
            "day_key": self.now.astimezone(LOCAL_TZ).date(),
            "forecast_days": 2,
            "data": {},
        }
        if model is ForecastCell:
            defaults["source"] = "open-meteo"
        return model.objects.create(**(defaults | kwargs))

    def test_all_endpoints_enforce_admin_otp(self):
        endpoints = [
            "summary",
            "map?layer=routes",
            "cells?lat=47&lon=8",
            f"coverage/route/{self.route.pk}",
            "jobs",
            "browser",
            "data-coverage",
        ]
        offline = httpx.ConnectError("offline")
        self.enterContext(patch("core.data_coverage._fetch_graphhopper", side_effect=offline))
        self.enterContext(patch("core.data_coverage._fetch_photon_status", side_effect=offline))
        self.enterContext(patch("core.data_coverage._read_photon_manifest", return_value=None))
        for path in endpoints:
            self.get(path)
        session = self.client.session
        del session["otp_device_id"]
        session.save()
        for path in endpoints:
            self.assertEqual(self.client.get("/api/system/" + path).status_code, 403)
        self.user.is_staff = False
        self.user.save(update_fields=["is_staff"])
        for path in endpoints:
            self.assertEqual(self.client.get("/api/system/" + path).status_code, 403)
        self.client.logout()
        for path in endpoints:
            self.assertEqual(self.client.get("/api/system/" + path).status_code, 401)

    def test_session_capability_matches_admin_access(self):
        capability = self.client.get("/api/auth/session").json()["system"]
        self.assertTrue(capability["allowed"])
        self.assertIn("next=/system", capability["login_url"])
        session = self.client.session
        del session["otp_device_id"]
        session.save()
        self.assertFalse(self.client.get("/api/auth/session").json()["system"]["allowed"])

    @override_settings(CACHES=LOCMEM, BROWSER_FINGERPRINT_ENABLED=True, BROWSER_POW_BITS=4)
    def test_browser_shows_only_the_requesting_browsers_own_assessment(self):
        unknown = self.get("browser")
        self.assertEqual(unknown, {"enabled": True, "pow_bits": 4, "assessment": None})
        recognise(self.client, "high", browser="b" * 64, fingerprint="f" * 64)
        assessment = self.get("browser")["assessment"]
        self.assertEqual(assessment["tier"], "high")
        self.assertEqual(assessment["indicators"], [])
        # Prefixes only, never the full keyed ids.
        self.assertEqual(assessment["browser_id"], "b" * 8)
        self.assertEqual(assessment["fingerprint_id"], "f" * 8)
        self.assertEqual(assessment["keys"], ["b:" + "b" * 8, "f:" + "f" * 8])
        self.assertEqual(assessment["components"], {})
        recognise(self.client, "low", browser="c" * 64)
        self.assertIsNone(self.get("browser")["assessment"]["fingerprint_id"])
        with self.settings(BROWSER_FINGERPRINT_ENABLED=False):
            self.assertEqual(self.get("browser")["enabled"], False)
            self.assertIsNone(self.get("browser")["assessment"])

    def test_latest_cells_filters_history_and_global_counts(self):
        older = self.cell(day_key=self.now.date() - timedelta(days=1))
        ForecastCell.objects.filter(pk=older.pk).update(fetched_at=self.now - timedelta(hours=3))
        newest = self.cell(source="openweathermap")
        self.cell(model=EnsembleCell)
        self.cell(lat_r=48)
        page = self.get("map", layer="cells", bbox="7,46,9,47.5", limit=1)
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["id"], f"forecast:{newest.pk}")
        all_cells = self.get("map", layer="cells", limit=1)
        self.assertEqual(all_cells["total"], 2)
        self.assertEqual(all_cells["next_offset"], 1)
        self.assertEqual(self.get("map", layer="cells", limit=1, offset=1)["items"][0]["lat"], 48)
        filtered = self.get("map", layer="cells", source="open-meteo", day=older.day_key.isoformat())
        self.assertEqual(filtered["items"][0]["id"], f"forecast:{older.pk}")
        self.assertEqual(self.get("map", layer="cells", kind="ensemble")["total"], 1)
        history = self.get("cells", lat=47, lon=8, limit=1)
        self.assertEqual(history["total"], 3)
        self.assertEqual(history["next_offset"], 1)
        self.assertNotIn("data", history["items"][0])
        summary = self.get("summary")
        self.assertEqual(summary["cache_locations"], 2)
        self.assertEqual(sum(item["stale"] for item in summary["caches"]), 1)
        self.assertEqual(summary["bounds"], [8, 47, 8.01, 47.01])

    def test_routes_include_ownerless_inactive_and_viewport_intersections(self):
        self.route.active = False
        self.route.save(update_fields=["active"])
        self.assertEqual(self.get("map", layer="routes")["total"], 1)
        self.assertEqual(self.get("map", layer="routes", active=True)["total"], 0)
        self.assertEqual(self.get("map", layer="routes", bbox="8.004,47.004,8.006,47.006")["total"], 1)
        self.assertEqual(self.get("map", layer="routes", bbox="1,1,2,2")["total"], 0)
        self.assertEqual(self.client.get("/api/system/map", {"layer": "cells", "bbox": "nan,0,1,1"}).status_code, 422)

    def journey_stage(self, rank=0):
        journey = Journey.objects.create(
            owner=self.user,
            name="Trip",
            start_point=route_point(47, 8),
            destination_point=route_point(47.01, 8.01),
            start_name="A",
            dest_name="B",
            start_date=self.now.date() + timedelta(days=1),
            earliest_start=time(8),
            latest_arrival=time(18),
        )
        day = JourneyDay.objects.create(
            journey=journey, index=0, date=journey.start_date, start=[8, 47], end=[8.01, 47.01]
        )
        return JourneyStage.objects.create(
            day=day,
            rank=rank,
            polyline=self.route.polyline,
            total_seconds=1800,
            total_distance_m=5000,
            sample_points=self.route.sample_points,
            vertex_times=[0, 1800],
            geometry_fetched_at=self.now,
        )

    def test_journey_alternatives_and_unavailable_coverage(self):
        stage = self.journey_stage()
        self.journey_stage(rank=1)
        self.assertEqual(self.get("map", layer="journeys")["total"], 1)
        self.assertEqual(self.get("map", layer="journeys", alternatives=True)["total"], 2)
        self.assertIsNone(self.get(f"coverage/stage/{stage.pk}")["unavailable"])
        stage.day.date = self.now.date() - timedelta(days=1)
        stage.day.save(update_fields=["date"])
        self.assertIn("Vergangenheit", self.get(f"coverage/stage/{stage.pk}")["unavailable"])
        stage.day.date = self.now.date() + timedelta(days=20)
        stage.day.save(update_fields=["date"])
        self.assertIn("ausserhalb", self.get(f"coverage/stage/{stage.pk}")["unavailable"])

    def test_read_only_coverage_across_midnight_and_short_horizon(self):
        departure = datetime.combine(
            self.now.astimezone(LOCAL_TZ).date() + timedelta(days=1), time(23, 50), tzinfo=LOCAL_TZ
        )
        self.route.sample_points = [
            {"lat_r": 47.0, "lon_r": 8.0, "elapsed_s": 0},
            {"lat_r": 47.0, "lon_r": 8.0, "elapsed_s": 3600},
        ]
        self.route.save(update_fields=["sample_points"])
        row = self.cell(
            source="openweathermap",
            day_key=departure.date(),
            forecast_days=1,
            data={
                "hourly": [
                    {"dt": departure.timestamp(), "temp": 15},
                    {"dt": (departure + timedelta(hours=1)).timestamp(), "temp": 16},
                ]
            },
        )
        with (
            patch("core.api.system.next_departure", return_value=departure),
            patch("httpx.AsyncClient") as http,
            patch("core.tasks.plan_forecast_job") as task,
        ):
            self.assertEqual(self.get(f"coverage/route/{self.route.pk}")["points"][0]["forecast"], "insufficient")
            ForecastCell.objects.filter(pk=row.pk).update(forecast_days=16)
            self.assertEqual(self.get(f"coverage/route/{self.route.pk}")["points"][0]["forecast"], "usable")
            self.get("summary")
            self.get("map", layer="cells")
            self.get("cells", lat=47, lon=8)
            self.get("jobs")
            http.assert_not_called()
            task.enqueue.assert_not_called()
            task.aenqueue.assert_not_called()
        self.assertEqual(ForecastJob.objects.count(), 0)
        original_time = row.fetched_at
        row.refresh_from_db()
        self.assertEqual(row.fetched_at, original_time)

    def test_job_window_stalls_pagination_and_missing_geometry(self):
        old = self.now - timedelta(days=2)
        for index, status in enumerate(["fetching", "done", "failed"]):
            job = ForecastJob.objects.create(
                key=str(index), kind="route", params={}, status=status, error="Failure" if status == "failed" else ""
            )
            if status != "failed":
                ForecastJob.objects.filter(pk=job.pk).update(updated_at=old)
        page = self.get("jobs", limit=1)
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["items"][0]["status"], "failed")
        self.assertTrue(self.get("jobs", offset=1)["items"][0]["possibly_stalled"])
        self.assertEqual(page["stall_timeout_seconds"], 300)
        self.route.polyline = None
        self.route.save(update_fields=["polyline"])
        self.assertEqual(self.get("summary")["missing_geometry"], 1)
        self.assertTrue(self.get(f"coverage/route/{self.route.pk}")["unavailable"])


@override_settings(ADMIN_OTP=True)
class SystemAccessTests(TestCase):
    """The access rule must not depend on admin.site: before daphne serves its first HTTP
    request the OTPAdminSite swap in backend/urls.py has not happened yet."""

    def setUp(self):
        self.user = User.objects.create_user(username="Operator", email="operator@example.test", is_staff=True)
        self.device = TOTPDevice.objects.create(user=self.user, name="admin", confirmed=True)

    def test_staff_needs_a_verified_device_of_their_own(self):
        self.assertTrue(has_system_access(self.user, {"otp_device_id": self.device.persistent_id}))
        self.assertFalse(has_system_access(self.user, {}))
        self.assertFalse(has_system_access(self.user, None))
        other = User.objects.create_user(username="Other", email="other@example.test", is_staff=True)
        foreign = TOTPDevice.objects.create(user=other, name="admin", confirmed=True)
        self.assertFalse(has_system_access(self.user, {"otp_device_id": foreign.persistent_id}))

    def test_staff_and_active_are_required(self):
        session = {"otp_device_id": self.device.persistent_id}
        self.user.is_staff = False
        self.assertFalse(has_system_access(self.user, session))
        self.user.is_staff, self.user.is_active = True, False
        self.assertFalse(has_system_access(self.user, session))
        self.assertFalse(has_system_access(AnonymousUser(), session))

    @override_settings(ADMIN_OTP=False)
    def test_without_admin_otp_staff_is_enough(self):
        self.assertTrue(has_system_access(self.user, {}))


INMEM_CHANNELS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


@override_settings(**TEST_SETTINGS, ADMIN_OTP=True, CHANNEL_LAYERS=INMEM_CHANNELS)
class SystemEventsConsumerTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="Operator", email="operator@example.test", is_staff=True)
        self.device = TOTPDevice.objects.create(user=self.user, name="admin", confirmed=True)
        throttle = patch.dict(
            SystemEventsConsumer.THROTTLE, {"jobs": 0.1, "cells": 0.3, "routes": 0.3, "journeys": 0.3}
        )
        throttle.start()
        self.addCleanup(throttle.stop)

    def session_cookie(self, *, verified=True):
        client = Client()
        client.force_login(self.user)
        session = client.session
        if verified:
            session["otp_device_id"] = self.device.persistent_id
        session.save()
        return [(b"cookie", f"sessionid={session.session_key}".encode())]

    async def connect(self, headers=()):
        communicator = WebsocketCommunicator(application, "/ws/system/", headers=list(headers))
        connected, code = await communicator.connect()
        return communicator, connected, code

    def test_refuses_anyone_but_verified_staff(self):
        unverified = self.session_cookie(verified=False)

        async def run():
            results = []
            for headers in ((), unverified):
                communicator, connected, _ = await self.connect(headers)
                # Accepted, then closed with the code, so a browser can tell it was refused.
                results.append((connected, await communicator.receive_output()))
                await communicator.disconnect()
            return results

        refused = (True, {"type": "websocket.close", "code": 4003})
        self.assertEqual(async_to_sync(run)(), [refused, refused])

    def test_hello_then_throttled_topics(self):
        headers = self.session_cookie()

        async def run():
            communicator, connected, _ = await self.connect(headers)
            self.assertTrue(connected)
            frames = [await communicator.receive_json_from()]
            await notify_system("cells")
            frames.append(await communicator.receive_json_from())
            # Inside the cells window: held back and merged, while jobs goes out at once.
            await notify_system("cells")
            await notify_system("cells")
            await notify_system("jobs")
            frames.append(await communicator.receive_json_from())
            frames.append(await communicator.receive_json_from(timeout=2))
            self.assertTrue(await communicator.receive_nothing(0.5))
            await communicator.disconnect()
            return frames

        self.assertEqual(
            async_to_sync(run)(),
            [
                {"type": "hello", "topics": ["cells", "jobs", "routes", "journeys"]},
                {"type": "changed", "topics": ["cells"]},
                {"type": "changed", "topics": ["jobs"]},
                {"type": "changed", "topics": ["cells"]},
            ],
        )


class SystemNotifyTests(SimpleTestCase):
    def test_a_failing_layer_never_fails_the_write(self):
        layer = SimpleNamespace(group_send=AsyncMock(side_effect=RedisConnectionError("down")))
        with patch("core.system_events.get_channel_layer", return_value=layer):
            async_to_sync(notify_system)("cells")
        layer.group_send.assert_awaited_once()

    def test_job_progress_notifies_the_dashboard(self):
        job = ForecastJob(key="k", kind="route", params={})
        with (
            patch("core.jobs.notify_system", new=AsyncMock()) as notify,
            patch("core.jobs.get_channel_layer", return_value=None),
        ):
            async_to_sync(jobs.publish)(job)
        notify.assert_awaited_once_with("jobs")


GRAPH_REPORT = {
    "release": "20261001T120000-7",
    "artifact": {
        "revision": "abc",
        "status": "validated",
        "built_at": "2026-10-01T12:30:00+00:00",
        "osm_file": {"name": "bike-ch.osm.pbf", "size": 3, "modified": "2026-09-30T08:00:00+00:00"},
    },
    "terrain": "0123abcd",
    "manifest": {
        "zoom": 15,
        "fallback_zoom": 12,
        "catalog_version": "2026-09",
        "sources": [{"name": "alps", "min_lon": 5, "min_lat": 45, "max_lon": 11, "max_lat": 48}],
        "fallback_source": {"name": "planet"},
        "coverage": [
            {"zoom": 15, "positions": 768, "decoded": True, "missing": 300, "void": 1, "examples": []},
            {"zoom": 12, "positions": 12, "decoded": False},
        ],
    },
    "cells_source": "graph",
    # Two neighbours in one row, one cell below; (9, 9) is terrain only, not the graph's.
    "cells": [[1066, 717], [1067, 717], [1066, 718]],
    "cell_coverage": {
        "zoom": 15,
        "mask_zoom": 11,
        "per_cell": 256,
        "cells": [[1066, 717, 256, 0], [1067, 717, 200, None], [1066, 718, 0, None], [9, 9, 256, 0]],
    },
}
PHOTON_STATUS = {"status": "Ok", "import_date": "2026-09-12T23:03:36Z", "version": "1.0.1"}
PHOTON_MANIFEST = {
    "imported_at": "2026-09-13T01:00:00Z",
    "sources": ["photon-dump-switzerland-liechtenstein-1.0-latest.jsonl.zst"],
    "countries": {"li": 20, "ch": 900},
}


class DataCoverageTests(SimpleTestCase):
    def test_adjacent_cells_in_a_row_merge_and_rows_stay_apart(self):
        boxes = data_coverage.merge_cells([(1067, 717), (1066, 717), (1066, 718), (1070, 717)])
        self.assertEqual(len(boxes), 3)
        (west, south, east, north), *_ = boxes
        self.assertLess(west, east)
        self.assertLess(south, north)
        # The two-cell run spans twice the width of a single cell.
        single = boxes[1]
        self.assertAlmostEqual(east - west, 2 * (single[2] - single[0]), places=4)
        self.assertEqual(data_coverage.merge_cells([]), [])

    def test_elevation_levels(self):
        self.assertEqual(data_coverage.elevation_level(256, 0, 256), "full")
        self.assertEqual(data_coverage.elevation_level(256, None, 256), "full")
        self.assertEqual(data_coverage.elevation_level(256, 3, 256), "partial")
        self.assertEqual(data_coverage.elevation_level(17, None, 256), "partial")
        self.assertEqual(data_coverage.elevation_level(0, None, 256), "fallback")

    def build(self, graph=GRAPH_REPORT, status=PHOTON_STATUS, manifest=PHOTON_MANIFEST):
        def answer(value):
            return {"side_effect": value} if isinstance(value, Exception) else {"return_value": value}

        with (
            patch("core.data_coverage._fetch_graphhopper", **answer(graph)),
            patch("core.data_coverage._fetch_photon_status", **answer(status)),
            patch("core.data_coverage._read_photon_manifest", **answer(manifest)),
        ):
            return data_coverage.build_data_coverage()

    def test_report_is_summarised_for_the_graph_cells_only(self):
        result = self.build()
        self.assertEqual(result["graph"]["cells"], 3)
        self.assertEqual(result["graph"]["osm_file"], "bike-ch.osm.pbf")
        self.assertEqual(len(result["road_boxes"]), 2)
        self.assertEqual(result["terrain"]["cell_counts"], {"full": 1, "partial": 1, "fallback": 1})
        self.assertEqual([len(result["elevation_boxes"][k]) for k in ("full", "partial", "fallback")], [1, 1, 1])
        self.assertEqual(result["terrain"]["sources"][0]["bbox"], [5, 45, 11, 48])
        self.assertEqual(result["terrain"]["checks"][0]["missing"], 300)
        west, south, east, north = result["bounds"]
        self.assertTrue(6 < west < east < 8 and 46 < south < north < 48)
        self.assertEqual(result["photon"]["countries"][0], {"code": "CH", "places": 900})
        self.assertEqual(result["photon"]["import_date"].year, 2026)

    def test_parts_fail_on_their_own(self):
        result = self.build(graph=httpx.ConnectError("down"))
        self.assertIsNone(result["graph"])
        self.assertIsNone(result["terrain"])
        self.assertEqual(result["road_boxes"], [])
        self.assertIsNotNone(result["photon"])
        result = self.build(status=httpx.ConnectError("down"), manifest=None)
        self.assertIsNone(result["photon"])
        self.assertEqual(result["graph"]["cells"], 3)
        # Photon down but its file is there: the countries still show.
        result = self.build(status=httpx.ConnectError("down"))
        self.assertFalse(result["photon"]["reachable"])
        self.assertEqual(len(result["photon"]["countries"]), 2)

    def test_bounding_box_terrain_without_any_cells(self):
        manifest = {**GRAPH_REPORT["manifest"], "bounds": [9.0, 47.0, 9.3, 47.1]}
        report = {**GRAPH_REPORT, "cells": None, "cells_source": "terrain", "manifest": manifest, "cell_coverage": None}
        result = self.build(graph=report)
        self.assertEqual(result["graph"]["cells_source"], "bounds")
        self.assertGreater(result["graph"]["cells"], 0)
        west, south, east, north = result["bounds"]
        self.assertTrue(west <= 9.0 and east >= 9.3 and south <= 47.0 and north >= 47.1)

    def test_terrain_without_cell_coverage_and_index_without_file(self):
        report = {**GRAPH_REPORT, "cell_coverage": None, "cells_source": "terrain"}
        result = self.build(graph=report, manifest=None)
        self.assertIsNone(result["terrain"]["cell_counts"])
        self.assertEqual(result["elevation_boxes"], {"full": [], "partial": [], "fallback": []})
        self.assertEqual(result["graph"]["cells_source"], "terrain")
        self.assertFalse(result["photon"]["manifest"])
        self.assertIsNone(result["photon"]["countries"])

    @override_settings(CACHES=LOCMEM)
    def test_cached_only_when_every_part_answered(self):
        from django.core.cache import cache

        cache.clear()
        with (
            patch("core.data_coverage._fetch_graphhopper", side_effect=httpx.ConnectError("down")) as graph,
            patch("core.data_coverage._fetch_photon_status", return_value=PHOTON_STATUS),
            patch("core.data_coverage._read_photon_manifest", return_value=PHOTON_MANIFEST),
        ):
            data_coverage.data_coverage()
            data_coverage.data_coverage()
            self.assertEqual(graph.call_count, 2)
            graph.side_effect, graph.return_value = None, GRAPH_REPORT
            data_coverage.data_coverage()
            data_coverage.data_coverage()
            self.assertEqual(graph.call_count, 3)

    @override_settings(CACHES=LOCMEM)
    def test_cache_outage_still_answers(self):
        with (
            patch("core.data_coverage.cache.get", side_effect=RedisConnectionError("down")),
            patch("core.data_coverage.cache.set", side_effect=RedisConnectionError("down")),
            patch("core.data_coverage._fetch_graphhopper", return_value=GRAPH_REPORT),
            patch("core.data_coverage._fetch_photon_status", return_value=PHOTON_STATUS),
            patch("core.data_coverage._read_photon_manifest", return_value=PHOTON_MANIFEST),
        ):
            self.assertEqual(data_coverage.data_coverage()["graph"]["cells"], 3)


@override_settings(**TEST_SETTINGS, ADMIN_OTP=False)
class DataCoverageApiTests(TestCase):
    def test_endpoint_serves_the_summary_and_boxes(self):
        user = User.objects.create_user(
            username="Operator", email="operator@example.test", is_staff=True, signup_completed=True
        )
        self.client.force_login(user)
        with (
            patch("core.data_coverage._fetch_graphhopper", return_value=GRAPH_REPORT),
            patch("core.data_coverage._fetch_photon_status", return_value=PHOTON_STATUS),
            patch("core.data_coverage._read_photon_manifest", return_value=PHOTON_MANIFEST),
        ):
            response = self.client.get("/api/system/data-coverage")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["graph"]["cells_source"], "graph")
        self.assertEqual(body["terrain"]["cell_counts"]["fallback"], 1)
        self.assertEqual(len(body["road_boxes"]), 2)
        self.assertEqual(body["photon"]["countries"][1]["code"], "LI")
