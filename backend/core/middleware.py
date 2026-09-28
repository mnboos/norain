"""The signed-in account's language, on top of what LocaleMiddleware read from the request."""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.utils import translation

from core.models import User


class UserLanguageMiddleware:
    """Activate ``User.language`` for a signed-in request.

    LocaleMiddleware already picked a language from Accept-Language (the SPA sends its own
    locale there), which is all an anonymous visitor has. An account's setting wins, so API
    messages follow it on every device, like the mails and briefings sent without a request.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)
        if isinstance(user, User) and user.is_authenticated and user.language:
            translation.activate(user.language)
            request.LANGUAGE_CODE = translation.get_language()
        return self.get_response(request)
