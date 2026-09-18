"""Metric semantics at business boundaries; no telemetry or provider network calls."""

import asyncio
import json
import re
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import unquote

import httpx
from asgiref.sync import async_to_sync
from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

from . import grid, telemetry
from .jobs import get_or_start_job, set_status
from .models import ForecastJob, ProcessedStripeEvent, Subscription, User
from .test_signup import TEST_SETTINGS


class TelemetryTests(SimpleTestCase):
    def test_emission_failure_does_not_affect_application(self):
        with (
            patch.object(telemetry.metrics, "count", side_effect=RuntimeError),
            patch.object(telemetry.sentry_logger, "info", side_effect=RuntimeError),
        ):
            telemetry.event("account.action", action="created")

    def test_explicit_product_attributes_are_preserved(self):
        with patch.object(telemetry.metrics, "count") as count:
            telemetry.event(
                "search.completed",
                **{
                    "user.id": "42",
                    "search.query": "St. Gallen",
                    "search.lat": 47.4245,
                    "route.name": "Morning commute",
                },
            )
        attrs = count.call_args.kwargs["attributes"]
        self.assertEqual(attrs["component"], "backend")
        self.assertEqual(attrs["search.query"], "St. Gallen")
        self.assertEqual(attrs["search.lat"], 47.4245)
        self.assertEqual(attrs["user.id"], "42")

    async def test_stage_context_isolated_between_concurrent_jobs_and_restored(self):
        @telemetry.stage("computation")
        async def work(user):
            telemetry._context.set({"user.id": user})
            await asyncio.sleep(0)
            telemetry.event("test")

        token = telemetry._context.set({"user.id": "parent"})
        try:
            with patch.object(telemetry.metrics, "count") as count:
                await asyncio.gather(work("one"), work("two"))
            self.assertEqual([c.kwargs["attributes"]["user.id"] for c in count.call_args_list], ["one", "two"])
            self.assertEqual(telemetry._context.get(), {"user.id": "parent"})
        finally:
            telemetry._context.reset(token)

    async def test_provider_rate_limit_is_counted_and_reraised(self):
        response = httpx.Response(429, request=httpx.Request("GET", "https://example.test"))

        @telemetry.provider("open-meteo")
        async def fetch():
            response.raise_for_status()

        with (
            patch.object(telemetry.metrics, "count") as count,
            patch.object(telemetry.metrics, "distribution") as duration,
            self.assertRaises(httpx.HTTPStatusError),
        ):
            await fetch()
        self.assertEqual(count.call_args.kwargs["attributes"]["outcome"], "rate_limited")
        self.assertEqual(duration.call_args.kwargs["unit"], "second")

    async def test_missing_owm_key_is_not_a_provider_request(self):
        with (
            patch.dict("os.environ", {"OPENWEATHERMAP_API_KEY": ""}),
            patch.object(telemetry.metrics, "count") as count,
        ):
            self.assertIsNone(await grid._fetch_owm(47, 9))
        self.assertEqual([c.args[0] for c in count.call_args_list], ["norain.weather.fallback"])
        self.assertEqual(count.call_args.kwargs["attributes"]["outcome"], "unconfigured")

    async def test_owm_cache_hit_counts_one_logical_lookup(self):
        cell = SimpleNamespace(source="openweathermap")
        with (
            patch.object(grid, "_get_forecast_cell_sync", side_effect=[None, cell]),
            patch.object(telemetry.metrics, "count") as count,
        ):
            self.assertIs(await grid.get_cached_forecast_cell(47, 9, "2026-09-17", 2), cell)
        count.assert_called_once()
        self.assertEqual(count.call_args.kwargs["attributes"]["outcome"], "hit")

    async def test_provider_failure_falls_back_and_reports_recovery(self):
        with (
            patch.object(grid, "get_cached_forecast_cell", new=AsyncMock(return_value=None)),
            patch.object(grid, "_fetch_open_meteo", new=AsyncMock(side_effect=httpx.ReadTimeout("timeout"))),
            patch.object(grid, "_fetch_owm", new=AsyncMock(return_value={"hourly": []})),
            patch.object(grid, "_store_forecast_cell_sync", return_value="stored"),
            patch.object(telemetry.metrics, "count") as count,
        ):
            self.assertEqual(await grid.get_or_fetch_forecast_cell(47, 9, "2026-09-17", 2), "stored")
        self.assertEqual([c.kwargs["attributes"]["outcome"] for c in count.call_args_list], ["needed", "recovered"])

    def test_partial_and_empty_forecast_coverage(self):
        for samples, expected in [([{"station_count": 2}], 0.5), ([], 0)]:
            job = SimpleNamespace(
                result={
                    "samples": samples,
                    "departure_inputs": {"candidates": [{"complete": True}, {"complete": False}]},
                },
                geometry={"sample_points": [{}, {}]},
                cells_total=4,
                cells_failed=1,
            )
            with patch.object(telemetry.metrics, "distribution") as distribution:
                telemetry.completed(job, "success")
            values = {c.args[0]: c.args[1] for c in distribution.call_args_list}
            self.assertEqual(values["norain.forecast.sample_coverage"], expected)
            self.assertEqual(values["norain.forecast.failed_cell_ratio"], 0.25)
            self.assertEqual(values["norain.forecast.candidate_coverage"], 0.5)


class TelemetryDatabaseTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="metrics", email="metrics@example.test")

    def test_job_reuse_and_restart_are_separate_from_completions(self):
        params = {"departure_time": "2026-09-18T08:00", "start_lat": 47, "start_lon": 9}
        with patch.object(telemetry, "event") as event:
            job = async_to_sync(get_or_start_job)("adhoc", self.user, params)[0]
            async_to_sync(get_or_start_job)("adhoc", self.user, params)
            ForecastJob.objects.filter(pk=job.pk).update(status="failed")
            async_to_sync(get_or_start_job)("adhoc", self.user, params)
        self.assertEqual([c.kwargs["outcome"] for c in event.call_args_list], ["new", "joined", "restart_failed"])
        self.assertTrue(all(c.kwargs["user.id"] == str(self.user.pk) for c in event.call_args_list))

    def test_terminal_failure_only_counted_once(self):
        job = ForecastJob.objects.create(key="terminal", kind="adhoc", params={}, status="planning")
        with patch.object(telemetry, "completed") as completed, patch("core.jobs.publish", new=AsyncMock()):
            async_to_sync(set_status)(job, "failed", error="routing")
            async_to_sync(set_status)(job, "failed", error="routing")
        completed.assert_called_once()

    def test_queue_snapshot_excludes_future_work_and_reports_eligibility_age(self):
        now = timezone.now()
        common = {
            "task_path": "core.tasks.refresh_upcoming_forecasts",
            "args_kwargs": {"args": [], "kwargs": {}},
            "backend_name": "default",
            "queue_name": "cells",
        }
        ready = DBTaskResult.objects.create(**common, run_after=now - timedelta(seconds=30))
        DBTaskResult.objects.filter(pk=ready.pk).update(enqueued_at=now - timedelta(hours=1))
        DBTaskResult.objects.create(**common, run_after=now + timedelta(hours=1))
        with patch.object(telemetry.metrics, "gauge") as gauge, patch("django.utils.timezone.now", return_value=now):
            telemetry.sample_queues()
        values = {(c.args[0], c.kwargs["attributes"].get("queue")): c.args[1] for c in gauge.call_args_list}
        self.assertEqual(values[("norain.queue.depth", "cells")], 1)
        self.assertEqual(values[("norain.queue.oldest_ready_age", "cells")], 30)
        self.assertEqual(values[("norain.queue.depth", "compute")], 0)
        self.assertEqual(values[("norain.forecast.stalled", None)], 0)

    def test_session_returns_stable_string_user_id(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/api/auth/session").json()["user"]["id"], str(self.user.pk))

    @override_settings(**TEST_SETTINGS)
    def test_signup_creation_emits_after_commit_but_not_for_a_known_address(self):
        signup = "/api/allauth/browser/v1/auth/signup"
        body = json.dumps({"email": "new_metrics@example.test"})
        with patch.object(telemetry, "event") as event:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.client.post(signup, data=body, content_type="application/json").status_code, 401)
                self.assertFalse(any(c.kwargs.get("action") == "created" for c in event.call_args_list))
            # The same address again: allauth mails the owner and creates nothing.
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.client.post(signup, data=body, content_type="application/json").status_code, 401)
            key = unquote(re.search(r"verify_key=(\S+)", mail.outbox[0].body).group(1))
            self.client.post(
                "/api/allauth/browser/v1/auth/email/verify",
                data=json.dumps({"key": key}),
                content_type="application/json",
            )
        self.assertEqual(sum(c.kwargs.get("action") == "created" for c in event.call_args_list), 1)
        self.assertEqual(sum(c.kwargs.get("action") == "verified" for c in event.call_args_list), 1)

    def test_search_empty_results_include_requested_text_location_and_user(self):
        self.client.force_login(self.user)
        with (
            patch("core.api.places.retrieve_places", new=AsyncMock(return_value=[])),
            patch.object(telemetry, "event") as event,
        ):
            response = self.client.get(
                "/api/search", {"query": "St. Gallen", "lat": 47.4245, "lon": 9.3767, "zoom": 12}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(event.call_args.kwargs["outcome"], "empty")
        self.assertEqual(event.call_args.kwargs["search.query"], "St. Gallen")
        self.assertEqual(event.call_args.kwargs["search.lat"], 47.4245)
        self.assertEqual(event.call_args.kwargs["user.id"], str(self.user.pk))

    def test_route_creation_and_quota_have_separate_outcomes(self):
        self.client.force_login(self.user)
        body = {
            "name": "Morning commute",
            "startLat": 47.5,
            "startLon": 9.3,
            "startName": "Start",
            "destLat": 47.6,
            "destLon": 9.4,
            "destName": "End",
            "scheduleCron": "0 8 * * 1",
            "scheduleDescription": "Monday",
        }
        with (
            patch("core.api.recurring_route.refresh_route_geometry", SimpleNamespace(aenqueue=AsyncMock())),
            patch.object(telemetry, "event") as event,
        ):
            for expected in (200, 200, 402):
                response = self.client.post("/api/routes", data=json.dumps(body), content_type="application/json")
                self.assertEqual(response.status_code, expected)
        self.assertEqual([c.kwargs["action"] for c in event.call_args_list], ["created", "created", "quota"])
        self.assertEqual(event.call_args_list[0].kwargs["route.name"], "Morning commute")
        self.assertEqual(event.call_args_list[0].kwargs["start_lat"], 47.5)
        self.assertEqual(event.call_args.kwargs["outcome"], "rejected")

    @override_settings(STRIPE_WEBHOOK_SECRET="test")
    def test_webhook_retries_do_not_repeat_subscription_transition(self):
        Subscription.objects.create(user=self.user, stripe_customer_id="cus_metrics")
        payload = {
            "id": "evt_metrics",
            "type": "customer.subscription.updated",
            "data": {"object": {"customer": "cus_metrics", "id": "sub_metrics", "status": "active"}},
        }
        with (
            patch("core.api.billing.stripe.Webhook.construct_event", return_value=payload),
            patch.object(telemetry, "event") as event,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.client.post("/api/billing/webhook").status_code, 200)
            self.assertTrue(self.client.post("/api/billing/webhook").json()["duplicate"])
        self.assertEqual(sum(c.args[0] == "subscription.transition" for c in event.call_args_list), 1)
        self.assertEqual(event.call_args.kwargs["outcome"], "duplicate")

    @override_settings(STRIPE_WEBHOOK_SECRET="test")
    def test_failed_webhook_application_rolls_back_claim_and_emits_no_conversion(self):
        payload = {"id": "evt_retry", "type": "checkout.session.completed", "data": {"object": {}}}
        with (
            patch("core.api.billing.stripe.Webhook.construct_event", return_value=payload),
            patch("core.api.billing._handle_event", side_effect=RuntimeError("apply failed")),
            patch.object(telemetry, "event") as event,
            self.assertRaises(RuntimeError),
        ):
            self.client.post("/api/billing/webhook")
        self.assertFalse(ProcessedStripeEvent.objects.filter(event_id="evt_retry").exists())
        event.assert_called_once_with("billing.webhook", outcome="error", event_type="checkout.session.completed")
