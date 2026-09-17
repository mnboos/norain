"""Secure settings for the Docker Compose production deployment."""

import re

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.loguru import LoguruIntegration
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.integrations.socket import SocketIntegration

from .base import *

sentry_sdk.init(
    # dsn="",
    dsn=os.environ.get("SENTRY_DSN_BACKEND"),
    enable_logs=True,
    enable_metrics=True,
    enable_tracing=True,
    # debug=True,
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
    profile_lifecycle="trace",
)


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} must be set in production.")
    return value


def csv_env(name: str) -> list[str]:
    return [item.strip() for item in required_env(name).split(",") if item.strip()]


SECRET_KEY = required_env("DJANGO_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS = csv_env("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = csv_env("DJANGO_CSRF_TRUSTED_ORIGINS")

# Caddy routes the same value (/{$DJANGO_ADMIN_PATH}/*), and it does not strip slashes the
# way this does, so allow only one plain path segment.
ADMIN_PATH = required_env("DJANGO_ADMIN_PATH")
if not re.fullmatch(r"[A-Za-z0-9_-]+", ADMIN_PATH) or ADMIN_PATH == "admin":
    raise RuntimeError("DJANGO_ADMIN_PATH must be one segment of letters, digits, - or _, and not 'admin'.")

CORS_ALLOWED_ORIGINS = []
CORS_ALLOW_CREDENTIALS = False

STATIC_ROOT = BASE_DIR / "static"
STATIC_URL = "/static/"

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

EMAIL_HOST = required_env("EMAIL_HOST")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = required_env("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = required_env("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "").lower() == "true"
DEFAULT_FROM_EMAIL = required_env("DEFAULT_FROM_EMAIL")
FRONTEND_URL = required_env("FRONTEND_URL").rstrip("/")

# Stripe is optional, including in production. Keep the empty defaults from base;
# billing endpoints report unavailable until credentials are configured.
