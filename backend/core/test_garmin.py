import hashlib
import json
from contextlib import ExitStack
from datetime import UTC, date, datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from django.test import Client, RequestFactory, SimpleTestCase, TestCase

from core.api.garmin import earliest_journey, earliest_route, journey_forecast, next_ride_view, weather_fields
from core.models import GarminToken, User


class GarminSelectionTests(SimpleTestCase):
    def test_feed_selects_earliest_source_and_limits_queries_to_token_owner(self):
        now = datetime(2030, 6, 3, 5, 0, tzinfo=UTC)
        owner = SimpleNamespace(pk=42)
        route = SimpleNamespace(
            pk="route", name="Commute", schedule_cron="0 10 * * *", total_seconds=1800, total_distance_m=9000
        )
        day = SimpleNamespace(
            pk="day",
            date=date(2030, 6, 3),
            index=0,
            journey=SimpleNamespace(name="Tour", earliest_start=time(9)),
        )
        stage = SimpleNamespace(total_seconds=7200, total_distance_m=40000)
        job = SimpleNamespace(status="pending")
        request = RequestFactory().get("/api/garmin/next-ride", HTTP_AUTHORIZATION="Bearer secret")
        with ExitStack() as stack:

            def patched(name, **kwargs):
                return stack.enter_context(patch("core.api.garmin." + name, **kwargs))

            clock = patched("datetime", wraps=datetime)
            clock.now.return_value = now
            tokens = patched("GarminToken.objects")
            tokens.select_related.return_value.filter.return_value.first.return_value = SimpleNamespace(user=owner)
            routes = patched("RecurringRoute.objects")
            routes.filter.return_value.only.return_value = [route]
            days = patched("JourneyDay.objects")
            days.filter.return_value.select_related.return_value.prefetch_related.return_value.distinct.return_value = [
                day
            ]
            permitted = patched("allowed_route_ids", return_value=["route"])
            patched("entitlements_for_sync")
            patched("forecast_available_at", return_value=True)
            journey_job = patched("journey_forecast", return_value=(stage, job, None))
            route_job = patched("start_forecast_job", new_callable=AsyncMock, return_value=job)
            patched("carry_stale", return_value=None)

            response = next_ride_view(request)
            ride = json.loads(response.content)["ride"]
            self.assertEqual(ride["id"], "day")
            self.assertEqual(ride["name"], "Tour - day 1")
            self.assertEqual(ride["distanceKm"], 40)
            self.assertEqual(ride["departureEpoch"], int(datetime(2030, 6, 3, 7, tzinfo=UTC).timestamp()))
            self.assertEqual(ride["weatherStatus"], "Weather refreshing")
            self.assertEqual(response["Cache-Control"], "no-store")
            permitted.assert_called_with(owner)
            routes.filter.assert_called_with(owner=owner, active=True, pk__in=["route"])
            self.assertIs(days.filter.call_args.kwargs["journey__owner"], owner)
            route_job.assert_not_awaited()

            # An earlier recurring departure wins even if a journey exists.
            route.schedule_cron = "0 8 * * *"
            response = next_ride_view(request)
            ride = json.loads(response.content)["ride"]
            self.assertEqual(ride["id"], "route")
            self.assertEqual(ride["durationMinutes"], 30)
            journey_job.assert_called_once()
            route_job.assert_awaited_once()

            # A finished job without usable samples must not promise a refresh forever.
            job.status = "done"
            response = next_ride_view(request)
            self.assertEqual(json.loads(response.content)["ride"]["weatherStatus"], "Weather unavailable")

    def test_feed_rejects_cookie_only_authentication_without_database_access(self):
        request = RequestFactory().get("/api/garmin/next-ride")
        request.user = SimpleNamespace(is_authenticated=True)
        self.assertEqual(next_ride_view(request).status_code, 401)

    @patch("core.api.garmin.rank_day")
    @patch("core.api.garmin.carry_stale")
    @patch("core.api.garmin.start_forecast_job", new_callable=AsyncMock)
    @patch("core.api.garmin._stage_params")
    def test_journey_uses_recommended_alternative(self, params, start, carry, ranking):
        def stage(pk):
            return SimpleNamespace(
                pk=pk, total_seconds=3600, gaps={}, leg_seconds=0, leg_m=0, limit_overruns={}, detours=[]
            )

        first, recommended = stage("first"), stage("recommended")
        day = SimpleNamespace(stages=Mock(), journey=Mock())
        day.stages.all.return_value = [first, recommended]
        ranking.return_value = [{"id": "first", "recommended": False}, {"id": "recommended", "recommended": True}]
        result = journey_forecast(day, Mock(), Mock(), datetime.now(UTC))
        self.assertIs(result[0], recommended)
        self.assertEqual(start.await_count, 2)
        self.assertEqual(carry.call_count, 2)
        self.assertEqual(params.call_count, 2)

    def test_orders_actual_departures_and_includes_return_trip(self):
        now = datetime(2030, 6, 3, 5, 0, tzinfo=UTC)
        later = SimpleNamespace(pk="a", schedule_cron="0 18 * * *")
        returning = SimpleNamespace(pk="z", schedule_cron="30 7 * * *")
        invalid = SimpleNamespace(pk="b", schedule_cron="invalid")
        self.assertIs(earliest_route([later, invalid, returning], now)[2], returning)
        self.assertEqual(earliest_route([returning], now)[0].hour, 5)

    def test_departed_ride_rolls_to_next_occurrence(self):
        route = SimpleNamespace(pk="a", schedule_cron="0 8 * * *")
        now = datetime(2030, 6, 3, 6, 0, tzinfo=UTC)
        self.assertEqual(earliest_route([route], now)[0].date(), date(2030, 6, 4))
        self.assertIsNone(earliest_route([], now))

    def test_journey_days_ignore_past_and_sort_by_time(self):
        now = datetime(2030, 6, 3, 6, 0, tzinfo=UTC)

        def day(pk, hour):
            return SimpleNamespace(pk=pk, date=date(2030, 6, 3), journey=SimpleNamespace(earliest_start=time(hour)))

        earlier, later, past = day("z", 9), day("a", 15), day("b", 7)
        selected = earliest_journey([later, past, earlier], now)
        self.assertIs(selected[2], earlier)
        recurring = earliest_route([SimpleNamespace(pk="r", schedule_cron="0 10 * * *")], now)
        self.assertLess(selected[0], recurring[0])

    def test_weather_uses_peak_rain_and_temperature_range(self):
        weather = weather_fields(
            {
                "samples": [
                    {"temp": 12, "pop": 0.2, "rain_rate_mm_h": 0, "headwind": -2},
                    None,
                    {"temp": 18, "pop": 0.7, "rain_rate_mm_h": 2.5, "headwind": 12},
                ]
            }
        )
        self.assertEqual(
            weather, {"tempMin": 12, "tempMax": 18, "rainProbability": 70, "rainRate": 2.5, "headwind": 12}
        )
        self.assertTrue(all(value is None for value in weather_fields(None).values()))


class GarminCredentialTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="watch", password="test")
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.client.get("/api/auth/session")
        self.headers = {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value}

    def issue(self):
        return self.client.post("/api/garmin/token", {}, content_type="application/json", **self.headers).json()[
            "token"
        ]

    def test_token_rotation_and_revocation(self):
        first = self.issue()
        self.assertEqual(GarminToken.objects.get(user=self.user).digest, hashlib.sha256(first.encode()).hexdigest())
        second = self.issue()
        self.assertNotEqual(first, second)
        feed = "/api/garmin/next-ride"
        self.assertEqual(self.client.get(feed, HTTP_AUTHORIZATION="Bearer " + first).status_code, 401)
        result = self.client.get(feed, HTTP_AUTHORIZATION="Bearer " + second)
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.json()["ride"])
        self.client.delete("/api/garmin/token", **self.headers)
        self.assertEqual(self.client.get(feed, HTTP_AUTHORIZATION="Bearer " + second).status_code, 401)

    def test_session_does_not_authorize_watch_feed_and_csrf_is_required(self):
        self.assertEqual(self.client.get("/api/garmin/next-ride").status_code, 401)
        self.assertEqual(self.client.post("/api/garmin/token").status_code, 403)
        self.assertEqual(self.client.delete("/api/garmin/token").status_code, 403)
