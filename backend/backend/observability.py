"""Shared Sentry setup for the API, workers, and scheduler."""

import os

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.loguru import LoguruIntegration
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.integrations.socket import SocketIntegration


def initialize_sentry(environment: str) -> None:
    if not os.environ.get("SENTRY_DSN_BACKEND"):
        return
    sentry_sdk.init(
        dsn=os.environ.get("SENTRY_DSN_BACKEND"),
        environment=environment,
        # The image sets SENTRY_RELEASE to the commit; sentry-sdk reads it on its own.
        enable_logs=True,
        enable_metrics=True,
        integrations=[
            DjangoIntegration(
                cache_spans=True,
                middleware_spans=True,
                signals_spans=True,
            ),
            LoguruIntegration(),
            RedisIntegration(),
            SocketIntegration(),
        ],
        # Set traces_sample_rate to 1.0 to capture 100%
        # of transactions for performance monitoring.
        # We recommend adjusting this value in production.
        traces_sample_rate=1.0,
        # To set a uniform sample rate
        # Set profiles_sample_rate to 1.0 to profile 100%
        # of sampled transactions.
        # We recommend adjusting this value in production
        profiles_sample_rate=1.0,
        # If you wish to associate users to errors (assuming you are using
        # django.contrib.auth) you may enable sending PII data.
        send_default_pii=True,
    )
