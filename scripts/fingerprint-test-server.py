"""Loopback-only integration fixture: real fingerprint endpoints, no database or provider calls."""

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

with make_server("127.0.0.1", 8127, get_wsgi_application()) as server:
    server.serve_forever()
