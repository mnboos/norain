"""Secure settings for the Docker Compose production deployment."""

import os

from .base import *


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

STRIPE_SECRET_KEY = required_env("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = required_env("STRIPE_WEBHOOK_SECRET")
STRIPE_PRICE_ID_PRO = required_env("STRIPE_PRICE_ID_PRO")
