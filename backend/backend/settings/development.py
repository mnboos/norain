"""Local development and test settings."""

import os

from .base import *

DEBUG = True

_frontend_port = os.environ.get("FRONTEND_PORT") or "3000"
CORS_ALLOWED_ORIGINS = [f"http://localhost:{_frontend_port}", f"http://127.0.0.1:{_frontend_port}"]
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# Avoid reaching for a local SMTP server during development.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
