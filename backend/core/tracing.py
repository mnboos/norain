"""Continue browser/API traces across the database task queue.

Trace headers live alongside args/kwargs in the existing JSON envelope, never in
business arguments. Native TaskContext makes them available to the worker without
patching django-tasks internals or changing queued task signatures.
"""

from functools import wraps
from inspect import Parameter, signature

import sentry_sdk
from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.tasks import task
from django_tasks_db.models import DBTaskResult


@receiver(pre_save, sender=DBTaskResult, dispatch_uid="core.task_trace_headers")
def store_task_trace(sender, instance, raw=False, **kwargs):
    if raw or not instance._state.adding or not sentry_sdk.is_initialized():
        return
    headers = {
        "sentry-trace": sentry_sdk.get_traceparent(),
        "baggage": sentry_sdk.get_baggage(),
    }
    instance.args_kwargs = {
        **instance.args_kwargs,
        "sentry": {key: value for key, value in headers.items() if value},
    }


def traced_task(**options):
    """Django task with an isolated consumer transaction and original error capture."""

    def decorate(func):
        @wraps(func)
        def run(context, *args, **kwargs):
            db_result = getattr(context.task_result, "db_result", None)
            headers = (db_result.args_kwargs.get("sentry") or {}) if db_result is not None else {}
            if not isinstance(headers, dict):
                headers = {}
            headers = {
                key: value
                for key, value in headers.items()
                if key in {"sentry-trace", "baggage"} and isinstance(value, str)
            }
            with sentry_sdk.isolation_scope() as scope, sentry_sdk.new_scope() as current:
                scope.clear()
                current.clear()
                transaction = sentry_sdk.continue_trace(
                    headers, op="queue.process", name=f"{func.__module__}.{func.__name__}"
                )
                with sentry_sdk.start_transaction(transaction) as span:
                    span.set_data("messaging.message.id", context.task_result.id)
                    span.set_data("messaging.destination.name", context.task_result.task.queue_name)
                    scope.set_tag("task.id", context.task_result.id)
                    try:
                        return func(*args, **kwargs)
                    except Exception as exc:
                        # db_worker catches exceptions itself; capture before losing this trace.
                        sentry_sdk.capture_exception(exc)
                        raise

        # Preserve the importable task path and expose context to Django's validator.
        original = signature(func)
        run.__signature__ = original.replace(
            parameters=[
                Parameter("context", Parameter.POSITIONAL_ONLY),
                *original.parameters.values(),
            ]
        )
        return task(takes_context=True, **options)(run)

    return decorate
