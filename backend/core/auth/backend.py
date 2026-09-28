"""Session authentication for Ninja routes."""

from django.http import HttpRequest
from django.utils.translation import gettext
from ninja.errors import HttpError
from ninja.utils import check_csrf


async def optional_session_auth(request: HttpRequest):
    """Public reads (a public route, a forecast job by its id) that still know a signed-in user."""
    error_response = check_csrf(request)
    if error_response:
        raise HttpError(403, gettext("CSRF-Prüfung fehlgeschlagen."))
    return await request.auser()


async def session_auth(request: HttpRequest):
    """Authenticate a Django session after validating CSRF for every API request."""
    error_response = check_csrf(request)
    if error_response:
        raise HttpError(403, gettext("CSRF-Prüfung fehlgeschlagen."))

    user = await request.auser()
    return user if user.is_authenticated else None
