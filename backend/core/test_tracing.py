"""Real SDK trace continuity, with an in-memory transport and no Sentry traffic."""

import httpx
import sentry_sdk
from django.core.asgi import get_asgi_application
from django.http import HttpResponse
from django.tasks import TaskContext
from django.test import AsyncClient, SimpleTestCase, TestCase, override_settings
from django.urls import path
from django_tasks_db.models import DBTaskResult
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.transport import Transport

from .tracing import traced_task


def broken_view(request):
    raise RuntimeError("trace continuity test")


def healthy_view(request):
    return HttpResponse("ok")


urlpatterns = [path("api/trace-error", broken_view), path("api/trace-ok", healthy_view)]


@traced_task()
def failing_task():
    raise RuntimeError("worker trace test")


@traced_task()
def parent_task():
    return failing_task.enqueue().id


class MemoryTransport(Transport):
    def __init__(self):
        super().__init__()
        self.envelopes = []

    def capture_envelope(self, envelope):
        self.envelopes.append(envelope)

    def items(self, kind):
        return [
            item.payload.json for envelope in self.envelopes for item in envelope.items if item.headers["type"] == kind
        ]


class SentryTestMixin:
    def setUp(self):
        super().setUp()
        self.transport = MemoryTransport()
        self.sentry_client = sentry_sdk.Client(
            dsn="https://public@example.invalid/1",
            transport=self.transport,
            default_integrations=False,
            integrations=[DjangoIntegration()],
            traces_sample_rate=1.0,
        )
        self.scope_manager = sentry_sdk.isolation_scope()
        self.scope = self.scope_manager.__enter__()
        self.scope.set_client(self.sentry_client)
        self.addCleanup(self.sentry_client.close)
        self.addCleanup(self.scope_manager.__exit__, None, None, None)


@override_settings(ROOT_URLCONF=__name__, ALLOWED_HOSTS=["testserver"])
class HttpTracingTests(SentryTestMixin, SimpleTestCase):
    async def test_asgi_error_continues_browser_trace(self):
        trace_id = "1234567890abcdef1234567890abcdef"
        parent_span = "1234567890abcdef"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=get_asgi_application()), base_url="http://testserver"
        ) as client:
            response = await client.get(
                "/api/trace-error",
                headers={
                    "sentry-trace": f"{trace_id}-{parent_span}-1",
                    "baggage": f"sentry-trace_id={trace_id},sentry-sampled=true",
                },
            )
        self.assertEqual(response.status_code, 500)
        self.sentry_client.flush()
        errors = self.transport.items("event")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["contexts"]["trace"]["trace_id"], trace_id)
        transactions = self.transport.items("transaction")
        self.assertTrue(any(t["contexts"]["trace"]["parent_span_id"] == parent_span for t in transactions))

    @override_settings(CORS_ALLOWED_ORIGINS=["http://localhost:3000"])
    async def test_local_browser_preflight_allows_both_trace_headers(self):
        response = await AsyncClient().options(
            "/api/trace-ok",
            headers={
                "origin": "http://localhost:3000",
                "access-control-request-method": "GET",
                "access-control-request-headers": "sentry-trace,baggage",
            },
        )
        self.assertEqual(response["access-control-allow-origin"], "http://localhost:3000")
        self.assertIn("sentry-trace", response["access-control-allow-headers"])
        self.assertIn("baggage", response["access-control-allow-headers"])


class QueuedTracingTests(SentryTestMixin, TestCase):
    async def test_async_enqueue_carries_the_request_trace(self):
        with sentry_sdk.start_transaction(name="async API request") as request:
            queued = await failing_task.aenqueue()
        stored = await DBTaskResult.objects.aget(pk=queued.id)
        self.assertTrue(stored.args_kwargs["sentry"]["sentry-trace"].startswith(request.trace_id))

    def test_fanout_preserves_trace_and_captures_worker_error(self):
        with sentry_sdk.start_transaction(name="browser API request", op="http.server") as request:
            queued = parent_task.enqueue()
        stored = DBTaskResult.objects.get(pk=queued.id)
        self.assertEqual(stored.args_kwargs["args"], [])
        self.assertEqual(stored.args_kwargs["kwargs"], {})
        self.assertTrue(stored.args_kwargs["sentry"]["sentry-trace"].startswith(request.trace_id))

        # Like db_worker: reconstruct from the persisted row in a fresh scope.
        result = stored.task_result
        child_id = result.task.call(TaskContext(task_result=result))
        child = DBTaskResult.objects.get(pk=child_id).task_result
        with self.assertRaisesRegex(RuntimeError, "worker trace test"):
            child.task.call(TaskContext(task_result=child))
        self.sentry_client.flush()
        error = self.transport.items("event")[0]
        self.assertEqual(error["contexts"]["trace"]["trace_id"], request.trace_id)
        self.assertEqual(error["tags"]["task.id"], child.id)
        tasks = [t for t in self.transport.items("transaction") if t["contexts"]["trace"]["op"] == "queue.process"]
        self.assertEqual(len(tasks), 2)
        self.assertEqual(
            tasks[0]["contexts"]["trace"]["parent_span_id"], stored.args_kwargs["sentry"]["sentry-trace"].split("-")[1]
        )
        self.assertEqual(
            tasks[1]["contexts"]["trace"]["parent_span_id"],
            child.db_result.args_kwargs["sentry"]["sentry-trace"].split("-")[1],
        )

    def test_legacy_task_without_headers_starts_a_new_trace_and_restores_scope(self):
        stored = DBTaskResult.objects.create(
            task_path="core.test_tracing.failing_task", args_kwargs={"args": [], "kwargs": {}}, backend_name="default"
        )
        # Simulate a task queued before propagation was installed.
        stored.args_kwargs.pop("sentry", None)
        with sentry_sdk.start_transaction(name="unrelated") as unrelated:
            sentry_sdk.set_user({"id": "unrelated-user"})
            with self.assertRaises(RuntimeError):
                result = stored.task_result
                result.task.call(TaskContext(task_result=result))
            self.assertIs(sentry_sdk.get_current_span(), unrelated)
        self.sentry_client.flush()
        error = self.transport.items("event")[0]
        self.assertNotEqual(error["contexts"]["trace"]["trace_id"], unrelated.trace_id)
        self.assertNotIn("user", error)
