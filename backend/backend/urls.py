"""
URL configuration for backend project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path
from django_otp.admin import OTPAdminSite

from core.api import api
from core.api.billing import checkout_view, entitlements_view, free_routes_view, portal_view, trial_view, webhook_view
from core.api.briefings import preferences_view, push_view
from core.auth.views import complete_signup_view, session_view


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


# The admin is public, so it asks for a one-time code as well as the password.
# Only the development settings can turn this off (ADMIN_OTP).
if settings.ADMIN_OTP:
    admin.site.__class__ = OTPAdminSite

urlpatterns = [
    path("healthz", healthz),
    path(f"{settings.ADMIN_PATH}/", admin.site.urls),
    path("api/auth/session", session_view),
    path("api/auth/complete-signup", complete_signup_view),
    # Sign-up, sign-in, verification and password reset: allauth's JSON API. Before
    # api.urls, which would otherwise claim every path under /api/.
    path("api/allauth/", include("allauth.headless.urls")),
    # Billing lives outside the Ninja API: NinjaAPI(auth=session_auth) CSRF-checks every
    # route it owns, which would reject Stripe's webhook POST with 403.
    path("api/briefings/preferences", preferences_view),
    path("api/briefings/push", push_view),
    path("api/billing/trial", trial_view),
    path("api/billing/free-routes", free_routes_view),
    path("api/billing/entitlements", entitlements_view),
    path("api/billing/checkout", checkout_view),
    path("api/billing/portal", portal_view),
    path("api/billing/webhook", webhook_view),
    path("api/", api.urls),
]
