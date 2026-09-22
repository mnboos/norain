"""Via points: a route the user reshapes by hand, and the editor's preview line."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from asgiref.sync import async_to_sync
from django.test import Client, TestCase, override_settings

from . import tests as fixtures
from .api.recurring_route import MAX_VIA_POINTS, PREVIEW_LIMIT_PER_MINUTE
from .models import RecurringRoute
from .tasks import _refresh_route_geometry_async, start_forecast_job
from .weather import _route_body

GH_ROUTE = {
    "paths": [{"points": {"coordinates": [[9, 47], [9.02, 47.0], [9.01, 47.01]]}, "time": 900_000, "distance": 2500}]
}
VIA = [[9.02, 47.0]]


@override_settings(CACHES=fixtures.LOCMEM_CACHE, CHANNEL_LAYERS=fixtures.INMEM_CHANNELS)
class RouteEditingTests(TestCase):
    def setUp(self):
        fixtures.ForecastJobTests.setUp(self)
        self.client = Client()
        self.client.force_login(self.user)

    def _put(self, **changes):
        body = self.client.get(f"/api/routes/{self.route.id}").json() | changes
        with patch(
            "core.api.recurring_route.refresh_route_geometry", SimpleNamespace(aenqueue=AsyncMock())
        ) as geometry:
            response = self.client.put(f"/api/routes/{self.route.id}", body, content_type="application/json")
        return response, geometry.aenqueue

    def test_geometry_routes_through_via_points_in_order(self):
        self.route.via_points = VIA
        self.route.save()
        fetch = AsyncMock(return_value=GH_ROUTE)
        with (
            patch("core.weather._fetch_route", fetch),
            patch("core.tasks.refresh_route_thumbnail", SimpleNamespace(aenqueue=AsyncMock())),
        ):
            async_to_sync(_refresh_route_geometry_async)(str(self.route.id))
        self.assertEqual(fetch.await_args.args, ("bike", ((9.0, 47.0), (9.02, 47.0), (9.01, 47.01))))
        self.assertEqual(_route_body("bike", fetch.await_args.args[1])["points"], [[9, 47], [9.02, 47], [9.01, 47.01]])
        self.route.refresh_from_db()
        self.assertEqual(self.route.total_seconds, 900)

    def test_changing_via_points_rebuilds_geometry_and_the_forecast(self):
        self.route.geometry_fetched_at = datetime(2030, 1, 1, tzinfo=UTC)
        self.route.save()
        with patch("core.tasks.plan_forecast_job", SimpleNamespace(aenqueue=AsyncMock())):
            old_job = async_to_sync(start_forecast_job)("route", self.user, fixtures.ForecastJobTests._job_params(self))

        response, enqueue = self._put(viaPoints=VIA)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["via_points"], VIA)
        enqueue.assert_awaited_once_with(str(self.route.id))
        self.route.refresh_from_db()
        self.assertEqual(self.route.via_points, VIA)
        self.assertIsNone(self.route.sample_points)

        # The geometry task stamps a new revision, which is part of the job key.
        self.route.geometry_fetched_at = datetime(2030, 1, 2, tzinfo=UTC)
        self.route.save()
        with patch("core.tasks.plan_forecast_job", SimpleNamespace(aenqueue=AsyncMock())):
            new_job = async_to_sync(start_forecast_job)(
                "route", self.user, {"route_id": str(self.route.id), "departure_time": old_job.params["departure_time"]}
            )
        self.assertNotEqual(new_job.id, old_job.id)

    def test_unchanged_via_points_keep_the_geometry(self):
        self.route.via_points = VIA
        self.route.save()
        response, enqueue = self._put(viaPoints=[[9.02, 47]])
        self.assertEqual(response.status_code, 200, response.content)
        enqueue.assert_not_awaited()

    def test_return_journey_rides_the_via_points_backwards(self):
        response, _ = self._put(
            viaPoints=[[9.02, 47.0], [9.03, 47.01]],
            returnScheduleCron="0 17 * * *",
            returnScheduleDescription="Daily at 17:00",
        )
        self.assertEqual(response.status_code, 200, response.content)
        returning = RecurringRoute.objects.get(return_of=self.route)
        self.assertEqual(returning.via_points, [[9.03, 47.01], [9.02, 47.0]])
        returning.sample_points = [{"lat": 47.0}]
        returning.save()

        self._put(viaPoints=VIA)
        returning.refresh_from_db()
        self.assertEqual(returning.via_points, VIA)
        self.assertIsNone(returning.sample_points)

    def test_via_points_are_validated(self):
        self.assertEqual(self._put(viaPoints=[[200, 47]])[0].status_code, 422)
        self.assertEqual(self._put(viaPoints=[[9, 47, 1]])[0].status_code, 422)
        self.assertEqual(self._put(viaPoints=[[9, 47]] * (MAX_VIA_POINTS + 1))[0].status_code, 422)

    def _preview(self, points=None):
        body = {"profile": "bike", "points": points or [[9, 47], *VIA, [9.01, 47.01]]}
        return self.client.post("/api/routes/preview", body, content_type="application/json")

    def test_preview_returns_the_line_only(self):
        with patch("core.weather._fetch_route", AsyncMock(return_value=GH_ROUTE)) as fetch:
            response = self._preview()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            response.json(),
            {"coordinates": GH_ROUTE["paths"][0]["points"]["coordinates"], "distance_m": 2500, "time_s": 900},
        )
        self.assertEqual(len(fetch.await_args.args[1]), 3)

    def test_preview_without_a_route_is_a_422(self):
        error = httpx.HTTPStatusError(
            "no path", request=httpx.Request("POST", "http://gh"), response=httpx.Response(400)
        )
        with patch("core.weather._fetch_route", AsyncMock(side_effect=error)):
            self.assertEqual(self._preview().status_code, 422)
        self.assertEqual(self._preview([[9, 47]]).status_code, 422)

    def test_preview_is_rate_limited_and_needs_sign_in(self):
        # One fixed minute, so the window cannot roll over mid-test.
        clock = SimpleNamespace(now=lambda tz: datetime(2030, 1, 1, 8, 0, 30, tzinfo=tz))
        with (
            patch("core.weather._fetch_route", AsyncMock(return_value=GH_ROUTE)),
            patch("core.api.recurring_route.datetime", clock),
        ):
            for _ in range(PREVIEW_LIMIT_PER_MINUTE):
                self.assertEqual(self._preview().status_code, 200)
            self.assertEqual(self._preview().status_code, 429)
        self.client.logout()
        self.assertIn(self._preview().status_code, (401, 403))
