"""JSON endpoints for email verification, session authentication, and password recovery."""

import json
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from loguru import logger

from core.tasks import refresh_user_forecasts

from .tokens import email_verification_token_generator


def _json_body(request: HttpRequest) -> dict:
    try:
        body = json.loads(request.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


def _email(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _password(value: object) -> str:
    return value if isinstance(value, str) else ""


def _username(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _account_payload(user) -> dict:
    if user and user.is_authenticated:
        return {"authenticated": True, "user": {"email": user.email, "username": user.username}}
    return {"authenticated": False, "user": None}


def _identity_taken(value: str) -> bool:
    """True when *value* already names an account, as either an email or a username.

    Checked across both columns in both directions: if one account's username could equal
    another's email, the login lookup would match two rows and lock both users out.
    """
    return get_user_model().objects.filter(Q(email__iexact=value) | Q(username__iexact=value)).exists()


def _frontend_link(path: str, **query: str) -> str:
    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000").rstrip("/")
    return f"{frontend_url}{path}?{urlencode(query)}"


def _send_verification_email(user) -> None:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = email_verification_token_generator.make_token(user)
    link = _frontend_link("/account", uid=uid, token=token)
    send_mail(
        "Confirm your NoRain email address",
        f"Confirm your email address to activate your NoRain account:\n\n{link}",
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
    )


def _send_password_reset_email(user) -> None:
    uid = urlsafe_base64_encode(force_bytes(user.pk))

    token = default_token_generator.make_token(user)
    link = _frontend_link("/account", reset_uid=uid, reset_token=token)
    send_mail(
        "Reset your NoRain password",
        f"Reset your NoRain password using this link:\n\n{link}",
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
    )


def _user_from_uid(uid: str):
    try:
        user_id = force_str(urlsafe_base64_decode(uid))
    except (TypeError, ValueError, OverflowError):
        return None
    try:
        return get_user_model().objects.get(pk=user_id)
    except get_user_model().DoesNotExist:
        return None


@require_GET
@ensure_csrf_cookie
def session_view(request: HttpRequest):
    """Return session state and establish a CSRF cookie for SPA requests."""
    return JsonResponse(_account_payload(request.user))


@require_POST
@csrf_protect
def signup_view(request: HttpRequest):
    """Create an inactive account and send its one-time verification link."""
    data = _json_body(request)
    email = _email(data.get("email"))
    username = _username(data.get("username"))
    password = _password(data.get("password"))
    if not email or not username or not password:
        return JsonResponse({"detail": "Email, username and password are required."}, status=400)
    try:
        validate_email(email)
    except ValidationError:
        return JsonResponse({"detail": "Enter a valid email address."}, status=400)
    try:
        UnicodeUsernameValidator()(username)
    except ValidationError as exc:
        return JsonResponse({"detail": " ".join(exc.messages)}, status=400)

    User = get_user_model()
    user = User(username=username, email=email, is_active=False)
    try:
        validate_password(password, user)
    except ValidationError as exc:
        return JsonResponse({"detail": " ".join(exc.messages)}, status=400)

    # A taken username is safe to report plainly — usernames are public by nature, and
    # without this the form has no way to tell the user why nothing happened.
    if _identity_taken(username):
        return JsonResponse({"detail": "This username is already taken."}, status=400)

    # The email, by contrast, stays undisclosed: the response below is identical whether
    # or not the address is registered. An unverified account may still request a fresh link.
    existing = User.objects.filter(email__iexact=email).first()
    if existing is None:
        with transaction.atomic():
            user.set_password(password)
            user.save()
            _send_verification_email(user)
    elif not existing.email_verified:
        _send_verification_email(existing)

    return JsonResponse({"detail": "If this address is available, check your email to confirm it."}, status=201)


@require_POST
@csrf_protect
def verify_email_view(request: HttpRequest):
    """Activate a user only after proving control of the verification mailbox."""
    data = _json_body(request)
    user = _user_from_uid(str(data.get("uid", "")))
    token = str(data.get("token", ""))
    if not user or not email_verification_token_generator.check_token(user, token):
        return JsonResponse({"detail": "This verification link is invalid or has expired."}, status=400)

    user.is_active = True
    user.email_verified = True
    user.save(update_fields=["is_active", "email_verified"])
    return JsonResponse({"detail": "Email verified. You can now sign in."})


@require_POST
@csrf_protect
def login_view(request: HttpRequest):
    """Authenticate an activated account by email or username, rotating its session id."""
    data = _json_body(request)
    # "identifier" is an email address or a username; "email" stays accepted so an older
    # client keeps working.
    identifier = _email(data.get("identifier") if data.get("identifier") is not None else data.get("email"))
    password = _password(data.get("password"))
    user = authenticate(request, identifier=identifier, password=password)
    if not user:
        return JsonResponse({"detail": "Invalid credentials, or the email has not been verified."}, status=401)

    login(request, user)
    _refresh_forecasts_after_login(user)
    return JsonResponse(_account_payload(user))


def _refresh_forecasts_after_login(user) -> None:
    """Queue the check for stale forecasts on the user's routes. Never fails the sign-in."""
    # Imported here: core.tasks reaches core.api, whose package imports back into core.

    try:
        refresh_user_forecasts.enqueue(user.pk)
    except Exception:  # a sign-in that worked must not turn into a 500 over pre-warming
        logger.exception(f"Could not enqueue refresh_user_forecasts for user {user.pk}")


@require_POST
@csrf_protect
def logout_view(request: HttpRequest):
    logout(request)
    return JsonResponse({"authenticated": False, "user": None})


@require_POST
@csrf_protect
def password_reset_view(request: HttpRequest):
    """Send a reset link without disclosing whether an email is registered."""
    email = _email(_json_body(request).get("email"))
    user = get_user_model().objects.filter(email__iexact=email, email_verified=True, is_active=True).first()
    if user:
        _send_password_reset_email(user)
    return JsonResponse({"detail": "If an active account exists, a password-reset link has been sent."})


@require_POST
@csrf_protect
def password_reset_confirm_view(request: HttpRequest):
    """Set a new password when presented with a valid, one-time reset token."""
    data = _json_body(request)
    user = _user_from_uid(str(data.get("uid", "")))
    token = str(data.get("token", ""))
    password = _password(data.get("password"))

    if not user or not default_token_generator.check_token(user, token):
        return JsonResponse({"detail": "This password-reset link is invalid or has expired."}, status=400)
    try:
        validate_password(password, user)
    except ValidationError as exc:
        return JsonResponse({"detail": " ".join(exc.messages)}, status=400)

    user.set_password(password)
    user.save(update_fields=["password"])
    return JsonResponse({"detail": "Password updated. You can now sign in."})
