"""Django identity backend and session authentication for Ninja routes."""

from django.contrib.auth.backends import ModelBackend
from django.db.models import Q
from django.http import HttpRequest
from ninja.errors import HttpError
from ninja.utils import check_csrf

from ..models import User


async def session_auth(request: HttpRequest):
    """Authenticate a Django session after validating CSRF for every API request."""
    error_response = check_csrf(request)
    if error_response:
        raise HttpError(403, "CSRF check failed.")

    user = await request.auser()
    return user if user.is_authenticated else None


class IdentityBackend(ModelBackend):
    """Authenticate a verified account by email address *or* username.

    A single ``Q`` resolves either form. Branching on whether the identifier contains
    ``"@"`` would be wrong: Django's ``UnicodeUsernameValidator`` permits ``@`` in a
    username, so such a check would send a legitimate username down the email path.
    """

    def authenticate(self, request, username=None, password=None, email=None, identifier=None, **kwargs):
        raw = identifier if identifier is not None else email
        if raw is None:
            # Django's own username/password call path (admin login, createsuperuser).
            return super().authenticate(request, username=username, password=password, **kwargs)

        value = raw.strip().lower() if isinstance(raw, str) else ""
        if not value or password is None:
            return None

        try:
            user = User.objects.get(Q(email__iexact=value) | Q(username__iexact=value))
        except (User.DoesNotExist, User.MultipleObjectsReturned):
            # Match Django's normal authentication timing for an unknown identity.
            User().set_password(password)
            return None
        # Superusers are created locally by a trusted administrator and have no email
        # verification flow. The custom manager marks new ones verified; the explicit
        # exemption also covers superusers created before that behavior existed.
        if (
            (not user.email_verified and not user.is_superuser)
            or not self.user_can_authenticate(user)
            or not user.check_password(password)
        ):
            return None
        return user
