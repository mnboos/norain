"""Loopback-only integration fixture: real fingerprint endpoints, no database or provider calls."""

import json
import os
import sys
from pathlib import Path
from wsgiref.simple_server import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.argv.append("test")  # Development settings must not initialize Sentry for this fixture.
os.environ["DJANGO_SETTINGS_MODULE"] = "backend.settings.development"

from backend import load_dotenv

load_dotenv()

from django.conf import settings

settings.SECRET_KEY = "local-fingerprint-integration-fixture-only"
settings.DEBUG = False
settings.ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
settings.CSRF_TRUSTED_ORIGINS = ["http://127.0.0.1:8128", "http://localhost:8128"]
settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
settings.ROOT_URLCONF = __name__
settings.BROWSER_FINGERPRINT_ENABLED = True
# The relay meter's echo chain runs in every test browser; without Cloudflare it stays unmeasured.
settings.BROWSER_PATH_METER = "observe"
# Every lie check as if enforced: an honest browser here must not trip even the observed ones.
settings.BROWSER_OBSERVE_ONLY = frozenset()

import django

django.setup()
settings.MIDDLEWARE = ["django.middleware.csrf.CsrfViewMiddleware"]

from django.core.wsgi import get_wsgi_application
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.urls import path

from core.api import api
from core.fingerprinting import get_browser_assessment


def csrf(request):
    return JsonResponse({"token": get_token(request)})


def assessment(request):
    # Fixture only: the real API never shows a browser its own assessment.
    return JsonResponse(get_browser_assessment(request), safe=False)


urlpatterns = [
    path("api/fingerprint/test-csrf", csrf),
    path("api/fingerprint/test-assessment", assessment),
    path("api/", api.urls),
]


def rewrite_headers(app):
    """Fixture only: ``X-Test-Headers`` (JSON) replaces headers the browser's network stack sets.

    Neither a page nor Playwright's ``route.continue`` can change Sec-Fetch-* or Sec-CH-UA, so the
    spoof tests have the fixture play the forger that sends them.
    """

    def wrapped(environ, start_response):
        for name, value in json.loads(environ.pop("HTTP_X_TEST_HEADERS", "{}")).items():
            environ["HTTP_" + name.upper().replace("-", "_")] = value
        return app(environ, start_response)

    return wrapped


with make_server("127.0.0.1", 8127, rewrite_headers(get_wsgi_application())) as server:
    server.serve_forever()
