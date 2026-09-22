"""Session authentication for Ninja routes."""

from django.http import HttpRequest
from ninja.errors import HttpError
from ninja.utils import check_csrf


async def optional_session_auth(request: HttpRequest):
    """Public planner access while retaining CSRF and signed-in ownership."""
    error_response = check_csrf(request)
    if error_response:
        raise HttpError(403, "CSRF check failed.")
    return await request.auser()


async def session_auth(request: HttpRequest):
    """Authenticate a Django session after validating CSRF for every API request."""
    error_response = check_csrf(request)
    if error_response:
        raise HttpError(403, "CSRF check failed.")

    user = await request.auser()
    return user if user.is_authenticated else None
