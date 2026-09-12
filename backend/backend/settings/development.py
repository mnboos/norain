"""Local development and test settings."""

from .base import *

DEBUG = True

CORS_ALLOWED_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# Avoid reaching for a local SMTP server during development.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
