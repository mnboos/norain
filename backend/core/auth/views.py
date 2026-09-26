"""JSON endpoints around allauth: the session state, the second sign-up step and the profile.

Sign-up, sign-in, email verification, sign-in by code and password reset are allauth's
headless API under /api/allauth/. Only what allauth has no endpoint for lives here.
"""

import json
from datetime import UTC, datetime

from allauth.account.adapter import get_adapter
from django.contrib import admin
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import reverse
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from loguru import logger
from redis.exceptions import RedisError

from core import telemetry
from core.models import User
from core.route_input import RoutingProfile

# Per account, for the live check in step 2 (debounced in the form).
USERNAME_CHECKS_PER_MINUTE = 30


def _json_body(request: HttpRequest) -> dict:
    try:
        body = json.loads(request.body)
    except UnicodeDecodeError, json.JSONDecodeError:
        return {}
    return body if isinstance(body, dict) else {}


def _account_payload(user: AbstractBaseUser | AnonymousUser | None) -> dict:
    if isinstance(user, User) and user.is_authenticated:
        return {
            "authenticated": True,
            "user": {
                "id": str(user.pk),
                "email": user.email,
                "username": user.username,
                "signup_complete": user.signup_completed,
                "has_password": user.has_usable_password(),
                "default_profile": user.default_profile,
            },
        }
    return {"authenticated": False, "user": None}


@require_GET
@ensure_csrf_cookie
def session_view(request: HttpRequest) -> HttpResponse:
    """Return session state and establish a CSRF cookie for SPA requests."""
    payload = _account_payload(request.user)
    if request.user.is_authenticated and request.user.is_active and request.user.is_staff:
        payload["system"] = {
            "allowed": admin.site.has_permission(request),
            "login_url": reverse("admin:login") + "?next=/system",
        }
    return JsonResponse(payload)


def _signed_in(request: HttpRequest) -> User | None:
    user = request.user
    return user if isinstance(user, User) and user.is_authenticated else None


def _clean_username(user: User, username: object) -> str:
    """The username rules of sign-up. Raises ValidationError."""
    username = username.strip() if isinstance(username, str) else ""
    if not username:
        raise ValidationError("A username is required.")
    # The generated username is the user's own, so keeping it is allowed.
    if username.lower() == user.username.lower():
        return username
    return get_adapter().clean_username(username)


def _clean_profile(value: object) -> str:
    try:
        return RoutingProfile(value).value
    except ValueError:
        raise ValidationError("Unknown bike profile.") from None


def _username_check_allowed(user_id: int) -> bool:
    """Fails open, like the route-preview limit: it only guards the database."""
    key = f"usernamecheck:{user_id}:{int(datetime.now(tz=UTC).timestamp() // 60)}"
    try:
        cache.add(key, 0, 90)
        return cache.incr(key) <= USERNAME_CHECKS_PER_MINUTE
    except (RedisError, OSError, ValueError) as exc:
        logger.warning(f"Username check limit unavailable: {exc}")
        return True


@require_POST
@csrf_protect
@telemetry.action("account", "complete_signup")
def complete_signup_view(request: HttpRequest) -> HttpResponse:
    """Step 2 of sign-up: the verified, signed-in user sets up the account.

    allauth created the account in step 1 with a generated username and no usable
    password. Step 2 takes the username, the default bike profile and, optionally, a
    password: without one the account keeps signing in by emailed code. The SPA keeps the
    user on this form until it succeeds.
    """
    user = _signed_in(request)
    if user is None:
        return JsonResponse({"detail": "Sign in first."}, status=401)
    if user.signup_completed:
        return JsonResponse({"detail": "This account is already set up."}, status=409)

    data = _json_body(request)
    password = data.get("password")
    password = password if isinstance(password, str) else ""
    try:
        user.username = _clean_username(user, data.get("username"))
        user.default_profile = _clean_profile(data.get("default_profile", user.default_profile))
        if password:
            validate_password(password, user)
    except ValidationError as exc:
        return JsonResponse({"detail": " ".join(exc.messages)}, status=400)

    fields = ["username", "default_profile", "signup_completed"]
    if password:
        user.set_password(password)
        fields.append("password")
    user.signup_completed = True
    try:
        with transaction.atomic():
            user.save(update_fields=fields)
    except IntegrityError:
        # Another account took the name between the check and the save.
        return JsonResponse({"detail": "This username is already taken."}, status=400)
    if password:
        # A new password rotates the session hash; without this the user is signed out.
        update_session_auth_hash(request, user)
    return JsonResponse(_account_payload(user))


@require_GET
def username_available_view(request: HttpRequest) -> HttpResponse:
    """The live check behind step 2's username field: the same rules as the save.

    Signed-in only. Usernames are public anyway, so the answer reveals nothing new, but
    it is still limited per account.
    """
    user = _signed_in(request)
    if user is None:
        return JsonResponse({"detail": "Sign in first."}, status=401)
    if not _username_check_allowed(user.pk):
        return JsonResponse({"detail": "Too many checks. Please wait a moment."}, status=429)
    try:
        _clean_username(user, request.GET.get("username"))
    except ValidationError as exc:
        return JsonResponse({"available": False, "detail": " ".join(exc.messages)})
    return JsonResponse({"available": True, "detail": ""})


@require_POST
@csrf_protect
def profile_view(request: HttpRequest) -> HttpResponse:
    """Change what step 2 set that is not a sign-in identity: the default bike profile."""
    user = _signed_in(request)
    if user is None:
        return JsonResponse({"detail": "Sign in first."}, status=401)
    try:
        user.default_profile = _clean_profile(_json_body(request).get("default_profile"))
    except ValidationError as exc:
        return JsonResponse({"detail": " ".join(exc.messages)}, status=400)
    user.save(update_fields=["default_profile"])
    return JsonResponse(_account_payload(user))
