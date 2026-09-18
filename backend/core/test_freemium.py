import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from django.test import Client, TestCase, override_settings

from core.api.briefings import validate_subscription
from core.briefings import briefing_body, deliver
from core.entitlements import FREE, PRO, allowed_route_ids, briefing_route_ids, entitlements_for_sync
from core.jobs import restrict_job_result
from core.models import (
    ForecastJob,
    Plan,
    RecurringRoute,
    RideBriefing,
    Subscription,
    User,
    route_point,
)
from core.tests import INMEM_CHANNELS, LOCMEM_CACHE


@override_settings(CACHES=LOCMEM_CACHE, CHANNEL_LAYERS=INMEM_CHANNELS)
class FreemiumTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="beta", email="beta@example.test", password="x")
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.client.get("/api/auth/session")
        self.headers = {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value}

    def post(self, path, body):
        return self.client.post(path, json.dumps(body), content_type="application/json", **self.headers)

    def plus(self, **values):
        return Subscription.objects.update_or_create(user=self.user, defaults={"plan": Plan.PRO, **values})[0]

    def route(self, **values):
        return RecurringRoute.objects.create(
            owner=self.user,
            name="Commute",
            start_point=route_point(47, 9),
            destination_point=route_point(47.1, 9.1),
            start_name="Home",
            dest_name="Work",
            schedule_cron="0 8 * * 1-5",
            schedule_description="Weekdays",
            **values,
        )

    def test_trial_once_no_payment_and_expiry(self):
        response = self.post("/api/billing/trial", {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["maxRoutes"], 20)
        subscription = Subscription.objects.get(user=self.user)
        self.assertEqual(subscription.trial_ends_at - subscription.trial_started_at, timedelta(days=14))
        self.assertFalse(subscription.stripe_customer_id)
        self.assertEqual(self.post("/api/billing/trial", {}).status_code, 409)
        subscription.trial_ends_at = datetime.now(UTC) - timedelta(seconds=1)
        subscription.save()
        self.assertEqual(entitlements_for_sync(self.user), FREE)
        self.assertEqual(self.post("/api/billing/trial", {}).status_code, 409)
        self.assertFalse(self.client.get("/api/billing/entitlements").json()["trialEligible"])

    def test_trial_authentication_and_csrf(self):
        self.assertEqual(self.client.post("/api/billing/trial").status_code, 403)
        self.client.logout()
        self.client.get("/api/auth/session")
        self.headers = {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value}
        self.assertEqual(self.post("/api/billing/trial", {}).status_code, 401)

    def test_complimentary_access_is_independent_of_billing_and_trial(self):
        sub = Subscription.objects.create(user=self.user, complimentary_until=datetime.now(UTC) + timedelta(days=90))
        self.assertEqual(entitlements_for_sync(self.user), PRO)
        self.assertIsNone(sub.trial_started_at)
        sub.complimentary_until = datetime.now(UTC) - timedelta(seconds=1)
        sub.save()
        self.assertEqual(entitlements_for_sync(self.user), FREE)

    def test_choose_free_routes_preserves_others_and_checks_ownership(self):
        routes = [self.route() for _ in range(3)]
        self.assertEqual(
            self.post("/api/billing/free-routes", {"routeIds": [str(r.id) for r in routes[1:]]}).status_code, 200
        )
        self.assertEqual(set(allowed_route_ids(self.user)), {r.id for r in routes[1:]})
        self.assertEqual(RecurringRoute.objects.count(), 3)
        self.assertEqual(
            self.post("/api/billing/free-routes", {"routeIds": [str(r.id) for r in routes]}).status_code, 400
        )
        self.assertEqual(
            self.post("/api/billing/free-routes", {"routeIds": ["00000000-0000-0000-0000-000000000000"]}).status_code,
            404,
        )

    def test_free_comparison_is_rejected_without_work(self):
        route = self.route(sample_points=[{}])
        with patch("core.api.recurring_route.start_forecast_job", new_callable=AsyncMock) as start:
            response = self.client.get(
                f"/api/routes/{route.id}/forecast",
                {"date": "2026-09-20", "time": "08:00", "departure_flex_after_minutes": 30},
            )
        self.assertEqual(response.status_code, 402)
        start.assert_not_awaited()

    def test_saved_paid_window_reverts_to_basic_after_expiry(self):
        route = self.route(sample_points=[{}], departure_flex_before_minutes=30)
        with patch("core.tasks.plan_forecast_job", SimpleNamespace(aenqueue=AsyncMock())):
            response = self.client.get(f"/api/routes/{route.id}/forecast", {"date": "2026-09-20", "time": "08:00"})
        self.assertEqual(response.status_code, 202)
        job = ForecastJob.objects.get(pk=response.json()["job_id"])
        self.assertNotIn("departure_flex_before_minutes", job.params)

    def test_expired_result_is_stripped_without_mutating_stored_payload(self):
        result = {"departure_inputs": {"test": True}, "samples": [{"uncertainty": {"x": 1}, "pop": 0.3}]}
        job = SimpleNamespace(result=result)
        restrict_job_result(job, FREE)
        self.assertNotIn("departure_inputs", job.result)
        self.assertIsNone(job.result["samples"][0]["uncertainty"])
        self.assertEqual(job.result["samples"][0]["pop"], 0.3)
        self.assertIn("departure_inputs", result)

    @override_settings(
        BILLING_ENABLED=True,
        STRIPE_SECRET_KEY="test",
        STRIPE_PRICE_ID_PLUS_ANNUAL="annual",
        STRIPE_PRICE_ID_PLUS_MONTHLY="monthly",
    )
    def test_checkout_selects_only_server_prices_and_no_duplicate_subscription(self):
        Subscription.objects.create(user=self.user, stripe_customer_id="customer")
        create = Mock(return_value=SimpleNamespace(url="https://checkout.stripe.com/test", id="session"))
        stripe = SimpleNamespace(v1=SimpleNamespace(checkout=SimpleNamespace(sessions=SimpleNamespace(create=create))))
        with patch("core.api.billing._client", return_value=stripe):
            for interval in ("annual", "monthly"):
                Subscription.objects.filter(user=self.user).update(checkout_session_id="")
                self.assertEqual(self.post("/api/billing/checkout", {"interval": interval}).status_code, 200)
                self.assertEqual(create.call_args.kwargs["params"]["line_items"], [{"price": interval, "quantity": 1}])
            self.assertEqual(self.post("/api/billing/checkout", {"interval": "attacker-price"}).status_code, 400)
            self.plus(status="active", stripe_subscription_id="sub")
            self.assertEqual(self.post("/api/billing/checkout", {}).status_code, 409)

    @override_settings(BRIEFING_EMAIL_ENABLED=True)
    def test_briefing_quota_and_expiry(self):
        self.plus()
        routes = [self.route() for _ in range(6)]
        for i, route in enumerate(routes):
            response = self.post("/api/briefings/preferences", {"routeId": str(route.id), "channel": "email"})
            self.assertEqual(response.status_code, 200 if i < 5 else 402)
        Subscription.objects.filter(user=self.user).update(plan=Plan.FREE)
        self.assertEqual(briefing_route_ids(self.user), [])
        self.assertEqual(
            self.post("/api/briefings/preferences", {"routeId": str(routes[0].id), "channel": ""}).status_code, 200
        )

    def test_push_endpoint_rejects_internal_urls(self):
        keys = {
            "auth": base64.urlsafe_b64encode(b"a" * 16).decode(),
            "p256dh": base64.urlsafe_b64encode(b"\x04" + b"b" * 64).decode(),
        }
        for endpoint in (
            "http://localhost/push",
            "https://127.0.0.1/push",
            "https://fcm.googleapis.com.evil.test/push",
            "https://fcm.googleapis.com:8443/push",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                validate_subscription({"endpoint": endpoint, "keys": keys})
        self.assertEqual(
            validate_subscription({"endpoint": "https://fcm.googleapis.com/push/id", "keys": keys})[1], keys
        )

    def test_two_way_ride_uses_one_slot_and_two_independent_geometries(self):
        body = {
            "name": "Commute",
            "startLat": 47,
            "startLon": 9,
            "startName": "Home",
            "destLat": 47.1,
            "destLon": 9.1,
            "destName": "Work",
            "scheduleCron": "0 8 * * 1-5",
            "scheduleDescription": "Morning",
            "returnScheduleCron": "0 17 * * 1-5",
            "returnScheduleDescription": "Evening",
        }
        with patch(
            "core.api.recurring_route.refresh_route_geometry", SimpleNamespace(aenqueue=AsyncMock())
        ) as geometry:
            response = self.post("/api/routes", body)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(geometry.aenqueue.await_count, 2)
            self.assertEqual(self.post("/api/routes", body).status_code, 200)
            self.assertEqual(self.post("/api/routes", body).status_code, 402)
        root = RecurringRoute.objects.get(id=response.json()["id"])
        returning = root.return_journey
        self.assertEqual(returning.start_point, root.destination_point)
        self.assertEqual(returning.destination_point, root.start_point)
        self.assertEqual(returning.schedule_cron, "0 17 * * 1-5")
        self.assertEqual(self.client.get("/api/billing/entitlements").json()["routeCount"], 2)
        self.assertEqual(len(self.client.get("/api/routes").json()), 2)
        self.assertEqual(len(allowed_route_ids(self.user)), 4)
        self.client.delete(f"/api/routes/{root.id}", **self.headers)
        self.assertFalse(RecurringRoute.objects.filter(pk=returning.id).exists())

    def test_plus_is_capped_at_twenty(self):
        self.plus()
        for _ in range(20):
            self.route()
        from ninja.errors import HttpError

        from core.api.recurring_route import _create_with_quota

        with self.assertRaises(HttpError) as caught:
            _create_with_quota(self.user, active=True)
        self.assertEqual(caught.exception.status_code, 402)

    def briefing(self, **values):
        now = datetime.now(UTC)
        route = self.route(briefing_channel="email")
        return RideBriefing.objects.create(
            route=route,
            departure=now + timedelta(hours=1),
            earliest_departure=now + timedelta(hours=1),
            due_at=now - timedelta(minutes=11),
            channel="email",
            **values,
        )

    @override_settings(BRIEFING_EMAIL_ENABLED=True)
    def test_delivery_once_and_missing_data_message(self):
        self.plus()
        briefing = self.briefing()
        with patch("core.briefings.send_mail", return_value=1) as send:
            deliver(briefing.pk)
            deliver(briefing.pk)
        send.assert_called_once()
        self.assertIn("nicht verfügbar", send.call_args.args[1])
        briefing.refresh_from_db()
        self.assertEqual(briefing.status, "sent")

    @override_settings(BRIEFING_EMAIL_ENABLED=True)
    def test_queued_delivery_stops_after_expiry_or_opt_out(self):
        briefing = self.briefing()
        with patch("core.briefings.send_mail") as send:
            deliver(briefing.pk)
        send.assert_not_called()
        briefing.refresh_from_db()
        self.assertEqual(briefing.status, "canceled")

    def test_incomplete_weather_does_not_recommend_dry_ride(self):
        job = SimpleNamespace(
            status=ForecastJob.Status.DONE, cells_failed=1, geometry={}, result={"samples": [{"pop": 0}]}
        )
        body = briefing_body(job, self.route())
        self.assertIn("unvollständig", body)
        self.assertNotIn("0 %", body)

    @override_settings(BRIEFING_EMAIL_ENABLED=True)
    def test_opt_out_cancels_queued_message(self):
        self.plus()
        briefing = self.briefing()
        self.assertEqual(
            self.post("/api/briefings/preferences", {"routeId": str(briefing.route_id), "channel": ""}).status_code, 200
        )
        with patch("core.briefings.send_mail") as send:
            deliver(briefing.pk)
        send.assert_not_called()

    @override_settings(BRIEFING_EMAIL_ENABLED=True)
    def test_ambiguous_send_is_not_retried(self):
        self.plus()
        briefing = self.briefing()
        with patch("core.briefings.send_mail", side_effect=TimeoutError) as send:
            deliver(briefing.pk)
            deliver(briefing.pk)
        send.assert_called_once()
        briefing.refresh_from_db()
        self.assertEqual(briefing.status, "failed")
        self.assertTrue(briefing.body)

    @override_settings(BRIEFING_EMAIL_ENABLED=True)
    def test_paired_briefings_share_one_preference_and_one_quota_slot(self):
        self.plus()
        root = self.route()
        returning = self.route(return_of=root)
        self.assertEqual(
            self.post("/api/briefings/preferences", {"routeId": str(root.id), "channel": "email"}).status_code, 200
        )
        returning.refresh_from_db()
        self.assertEqual(returning.briefing_channel, "email")
        self.assertEqual(set(briefing_route_ids(self.user)), {root.id, returning.id})
        self.assertEqual(len(self.client.get("/api/briefings/preferences").json()["routes"]), 1)

    @override_settings(BRIEFING_EMAIL_ENABLED=True)
    def test_scheduler_prepares_and_delivers_once(self):
        from core.briefings import refresh_briefings

        self.plus()
        now = datetime(2030, 6, 3, 5, 0, tzinfo=UTC)
        route = self.route(briefing_channel="email")
        job = ForecastJob.objects.create(
            owner=self.user,
            kind=ForecastJob.Kind.ROUTE,
            key="briefing-job",
            params={},
            status=ForecastJob.Status.DONE,
            geometry={"sample_points": [{}]},
            result={"samples": [{"pop": 0.5, "temp": 15, "rain_rate_mm_h": 0.2}]},
        )
        with (
            patch("core.briefings.datetime") as clock,
            patch("core.briefings.start_forecast_job", new_callable=AsyncMock, return_value=job) as start,
            patch("core.briefings.send_mail", return_value=1) as send,
        ):
            clock.now.return_value = now
            refresh_briefings.func()
            refresh_briefings.func()
        start.assert_awaited_once()
        send.assert_called_once()
        self.assertEqual(RideBriefing.objects.filter(route=route).count(), 1)

    @override_settings(BRIEFING_EMAIL_ENABLED=True)
    def test_daily_cap_prevents_more_sends(self):
        self.plus()
        briefing = self.briefing()
        now = datetime.now(UTC)
        for i in range(10):
            RideBriefing.objects.create(
                route=briefing.route,
                departure=now + timedelta(days=i + 1),
                earliest_departure=now,
                due_at=now,
                channel="email",
                delivery_started_at=now,
                status="sent",
            )
        with patch("core.briefings.send_mail") as send:
            deliver(briefing.pk)
        send.assert_not_called()
        briefing.refresh_from_db()
        self.assertEqual(briefing.status, "capped")

    @override_settings(VAPID_PUBLIC_KEY="public", VAPID_PRIVATE_KEY="private", VAPID_SUBJECT="mailto:test@example.com")
    def test_expired_push_subscription_is_removed(self):
        from pywebpush import WebPushException

        from core.briefings import _send_push
        from core.models import PushSubscription

        PushSubscription.objects.create(user=self.user, endpoint="https://fcm.googleapis.com/push/id", keys={})
        with patch(
            "core.briefings.webpush", side_effect=WebPushException("gone", response=SimpleNamespace(status_code=410))
        ):
            self.assertFalse(_send_push(self.user, "Forecast", "https://example.com", "ride"))
        self.assertEqual(PushSubscription.objects.count(), 0)

    @override_settings(BILLING_ENABLED=True, STRIPE_SECRET_KEY="test", STRIPE_PRICE_ID_PLUS_ANNUAL="annual")
    def test_checkout_reuses_open_session_and_allows_resubscription_after_cancel(self):
        Subscription.objects.create(
            user=self.user, stripe_customer_id="customer", checkout_session_id="old", checkout_interval="annual"
        )
        previous = SimpleNamespace(status="open", url="https://checkout.stripe.com/old")
        sessions = SimpleNamespace(
            retrieve=Mock(return_value=previous),
            create=Mock(return_value=SimpleNamespace(id="new", url="https://checkout.stripe.com/new")),
        )
        stripe = SimpleNamespace(v1=SimpleNamespace(checkout=SimpleNamespace(sessions=sessions)))
        with patch("core.api.billing._client", return_value=stripe):
            self.assertEqual(self.post("/api/billing/checkout", {}).json()["url"], previous.url)
            sessions.create.assert_not_called()
            previous.status = "complete"
            self.assertEqual(self.post("/api/billing/checkout", {}).status_code, 409)
            Subscription.objects.filter(user=self.user).update(stripe_subscription_id="sub_old", status="canceled")
            self.assertEqual(self.post("/api/billing/checkout", {}).status_code, 200)
            sessions.create.assert_called_once()
