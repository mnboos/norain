"""Best-effort product metrics. Explicit context never includes credentials or bodies."""

import os
from collections.abc import Callable
from contextlib import suppress
from contextvars import ContextVar
from functools import wraps
from time import perf_counter
from typing import Any, Concatenate, ParamSpec, TypeVar

import httpx
import sentry_sdk
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest, HttpResponse
from sentry_sdk import logger as sentry_logger
from sentry_sdk import metrics

# SDK failures are deliberately swallowed here; logging them through Sentry would recurse.
# ruff: noqa: BLE001, S110

_context: ContextVar[dict[str, Any] | None] = ContextVar("norain_metrics", default=None)

# `action` keeps the view's own signature: a decorated view still reads as a view.
P = ParamSpec("P")
R = TypeVar("R", bound=HttpResponse)


def attributes(**values):
    return {
        k: v
        for k, v in {"component": "backend", **(_context.get() or {}), **values}.items()
        if isinstance(v, (str, int, float, bool))
    }


def emit(metric_type, name, value: float = 1, *, unit=None, **values):
    # Observability must not break the operation being observed.
    with suppress(Exception):
        getattr(metrics, metric_type)(f"norain.{name}", value, unit=unit, attributes=attributes(**values))


def event(name, **values):
    emit("count", name, **values)
    with suppress(Exception):
        sentry_logger.info(f"norain.{name}", attributes=attributes(**values))


def user_context(user: AbstractBaseUser | AnonymousUser | None):
    try:
        from .entitlements import entitlements_for_sync

        return (
            {"user.id": str(user.pk), "plan": str(entitlements_for_sync(user).plan)}
            if user and user.is_authenticated
            else {"plan": "free"}
        )
    except Exception:
        return {}


def route_context(route):
    return {
        "route.id": str(route.pk),
        "route.name": route.name,
        "profile": route.profile,
        "start_lat": route.start_lat,
        "start_lon": route.start_lon,
        "dest_lat": route.dest_lat,
        "dest_lon": route.dest_lon,
    }


async def job_context(job):
    try:
        from asgiref.sync import sync_to_async

        from .models import RecurringRoute

        result = {
            "job.id": str(job.pk),
            "feature": job.kind,
            **{
                k: job.params[k]
                for k in ("profile", "start_lat", "start_lon", "dest_lat", "dest_lon")
                if k in job.params
            },
        }
        if job.owner_id:
            result.update(await sync_to_async(lambda: user_context(job.owner))())
        else:
            result["plan"] = "free"
        if route_id := job.params.get("route_id"):
            route = await RecurringRoute.objects.filter(pk=route_id).afirst()
            if route:
                result.update(route_context(route))
    except Exception:
        return {}
    else:
        return result


async def bind_job(job):
    _context.set(await job_context(job))


def error_outcome(error):
    if isinstance(error, httpx.TimeoutException):
        return "timeout"
    if isinstance(error, httpx.HTTPStatusError):
        return "rate_limited" if error.response.status_code == 429 else "http_error"
    if isinstance(error, httpx.HTTPError):
        return "network_error"
    if isinstance(error, (ValueError, KeyError, IndexError, TypeError)):
        return "invalid_response"
    return "error"


def provider(name, *, optional_key=None):
    """Inside any response cache: only actual provider attempts are measured."""

    def decorate(fn):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            started, outcome = perf_counter(), "success"
            attempted = optional_key is None or bool(os.environ.get(optional_key))
            try:
                result = await fn(*args, **kwargs)
                if not isinstance(result, (dict, list)):
                    outcome = "invalid_response"
            except Exception as exc:
                outcome = error_outcome(exc)
                raise
            else:
                return result
            finally:
                if attempted:
                    emit("count", "provider.request", provider=name, outcome=outcome)
                    emit(
                        "distribution",
                        "provider.duration",
                        perf_counter() - started,
                        unit="second",
                        provider=name,
                        outcome=outcome,
                    )

        return wrapped

    return decorate


def stage(name):
    def decorate(fn):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            token = _context.set({})
            started, outcome = perf_counter(), "returned"
            try:
                with sentry_sdk.isolation_scope() as scope:
                    scope.set_user(None)
                    try:
                        return await fn(*args, **kwargs)
                    except Exception:
                        outcome = "error"
                        raise
                    finally:
                        emit(
                            "distribution",
                            "forecast.stage.duration",
                            perf_counter() - started,
                            unit="second",
                            stage=name,
                            outcome=outcome,
                        )
            finally:
                _context.reset(token)

        return wrapped

    return decorate


def action(
    domain: str, name: str
) -> Callable[[Callable[Concatenate[HttpRequest, P], R]], Callable[Concatenate[HttpRequest, P], R]]:
    """HTTP action attempts, separate from persisted business milestones."""

    def decorate(fn: Callable[Concatenate[HttpRequest, P], R]) -> Callable[Concatenate[HttpRequest, P], R]:
        @wraps(fn)
        def wrapped(request: HttpRequest, *args: P.args, **kwargs: P.kwargs) -> R:
            context = user_context(getattr(request, "user", None))
            try:
                response = fn(request, *args, **kwargs)
            except Exception:
                event(f"{domain}.action", action=name, outcome="error", **context)
                raise
            status = response.status_code
            event(
                f"{domain}.action",
                action=name,
                outcome="success" if status < 400 else "unavailable" if status == 503 else "rejected",
                **(
                    user_context(request.user)
                    if getattr(getattr(request, "user", None), "is_authenticated", False)
                    else context
                ),
            )
            return response

        return wrapped

    return decorate


def completed(job, outcome):
    event("forecast.completed", outcome=outcome)
    if outcome != "success":
        return
    samples = (job.result or {}).get("samples") or []
    expected = len((job.geometry or {}).get("sample_points") or [])
    if expected:
        emit("distribution", "forecast.sample_coverage", min(1, len(samples) / expected))
        emit("distribution", "forecast.station_coverage", sum(bool(s.get("station_count")) for s in samples) / expected)
    if job.cells_total:
        emit("distribution", "forecast.failed_cell_ratio", job.cells_failed / job.cells_total)
    candidates = ((job.result or {}).get("departure_inputs") or {}).get("candidates") or []
    if candidates:
        emit(
            "distribution",
            "forecast.candidate_coverage",
            sum(bool(c.get("complete")) for c in candidates) / len(candidates),
        )


def sample_queues():
    """Scheduler-owned snapshot; still reports when all workers have stopped."""
    try:
        from django.db.models import Case, Count, DateTimeField, F, Min, When
        from django.db.models.functions import Greatest
        from django.utils import timezone
        from django_tasks_db.models import DBTaskResult, get_date_max

        from .jobs import JOB_STALL_TIMEOUT
        from .models import ForecastJob

        now = timezone.now()
        rows = (
            DBTaskResult.objects.ready()
            .annotate(
                eligible=Case(
                    When(run_after=get_date_max(), then=F("enqueued_at")),
                    default=Greatest("enqueued_at", "run_after"),
                    output_field=DateTimeField(),
                )
            )
            .values("queue_name")
            .annotate(depth=Count("pk"), oldest=Min("eligible"))
        )
        queues = {row["queue_name"]: row for row in rows}
        token = _context.set({})
        try:
            for queue in {"default", "cells", "compute", "forecasts"} | queues.keys():
                row = queues.get(queue)
                emit("gauge", "queue.depth", row["depth"] if row else 0, queue=queue)
                emit(
                    "gauge",
                    "queue.oldest_ready_age",
                    max(0, (now - row["oldest"]).total_seconds()) if row else 0,
                    unit="second",
                    queue=queue,
                )
            emit(
                "gauge",
                "forecast.stalled",
                ForecastJob.objects.exclude(status__in=ForecastJob.TERMINAL_STATUSES)
                .filter(updated_at__lt=now - JOB_STALL_TIMEOUT)
                .count(),
            )
        finally:
            _context.reset(token)
    except Exception:
        pass
