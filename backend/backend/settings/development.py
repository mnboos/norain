"""Local development and test settings."""

from backend.observability import initialize_sentry

from .base import *

DEBUG = True

_frontend_port = os.environ.get("FRONTEND_PORT") or "3000"
CORS_ALLOWED_ORIGINS = [f"http://localhost:{_frontend_port}", f"http://127.0.0.1:{_frontend_port}"]
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# Avoid reaching for a local SMTP server during development. Prints the plain body, so a
# link copied from the console works (Django's own console backend prints encoded text).
EMAIL_BACKEND = "core.mail.ReadableConsoleBackend"

# Set DJANGO_ADMIN_OTP=false in .env to open the local admin with the password alone.
ADMIN_OTP = os.environ.get("DJANGO_ADMIN_OTP", "true").strip().lower() not in {"0", "false", "no", "off"}

# Match the browser's Vite environment; never send test failures to the live DSN.
if "test" not in sys.argv:
    initialize_sentry("development")
