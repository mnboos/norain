"""JSON endpoints around allauth: the session state and the second sign-up step.

Sign-up, sign-in, email verification, sign-in by code and password reset are allauth's
headless API under /api/allauth/. Only what allauth has no endpoint for lives here.
"""

import json

from allauth.account.adapter import get_adapter
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from core import telemetry
from core.models import User


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
            },
        }
    return {"authenticated": False, "user": None}


@require_GET
@ensure_csrf_cookie
def session_view(request: HttpRequest) -> HttpResponse:
    """Return session state and establish a CSRF cookie for SPA requests."""
    return JsonResponse(_account_payload(request.user))


@require_POST
@csrf_protect
@telemetry.action("account", "complete_signup")
def complete_signup_view(request: HttpRequest) -> HttpResponse:
    """Step 2 of sign-up: the verified, signed-in user picks a username and a password.

    allauth created the account in step 1 with a generated username and no usable
    password. The SPA keeps the user on this form until it succeeds.
    """
    user = request.user
    if not isinstance(user, User) or not user.is_authenticated:
        return JsonResponse({"detail": "Sign in first."}, status=401)
    if user.signup_completed:
        return JsonResponse({"detail": "This account is already set up."}, status=409)

    data = _json_body(request)
    username = data.get("username")
    password = data.get("password")
    username = username.strip() if isinstance(username, str) else ""
    password = password if isinstance(password, str) else ""
    if not username or not password:
        return JsonResponse({"detail": "Username and password are required."}, status=400)

    # The generated username is the user's own, so keeping it is allowed.
    if username.lower() != user.username.lower():
        try:
            username = get_adapter().clean_username(username)
        except ValidationError as exc:
            return JsonResponse({"detail": " ".join(exc.messages)}, status=400)
    user.username = username
    try:
        validate_password(password, user)
    except ValidationError as exc:
        return JsonResponse({"detail": " ".join(exc.messages)}, status=400)

    user.set_password(password)
    user.signup_completed = True
    try:
        with transaction.atomic():
            user.save(update_fields=["username", "password", "signup_completed"])
    except IntegrityError:
        # Another account took the name between the check and the save.
        return JsonResponse({"detail": "This username is already taken."}, status=400)
    # A new password rotates the session hash; without this the user is signed out.
    update_session_auth_hash(request, user)
    return JsonResponse(_account_payload(user))
