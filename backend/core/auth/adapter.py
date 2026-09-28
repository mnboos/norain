"""django-allauth adapters: MeteoLane's rules on top of allauth's defaults."""

import secrets
from types import SimpleNamespace
from urllib.parse import quote, urlparse

from allauth.account.adapter import DefaultAccountAdapter
from allauth.headless import app_settings as headless_settings
from allauth.headless.adapter import DefaultHeadlessAdapter
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest
from django.utils import translation
from django.utils.translation import gettext

from ..models import User
from .lockout import client_ip


class AccountAdapter(DefaultAccountAdapter):
    """Keep every email and every username apart, across both columns.

    Both are sign-in identities, and allauth tries a typed identity as an email first and
    then as a username. If one account's username could equal another's email, one sign-in
    would match two accounts. allauth already refuses a duplicate within a column (ignoring
    case); these two checks close the gap between the columns.
    """

    def get_client_ip(self, request: HttpRequest) -> str:
        """The address allauth's rate limits count: the same one axes counts.

        allauth's own ``TRUSTED_CLIENT_IP_HEADER`` has no fallback, so without Caddy
        (development, tests) every request would be refused.
        """
        ip = client_ip(request)
        if not ip:
            raise PermissionDenied("Unable to determine client IP address")
        return ip

    def send_mail(self, template_prefix: str, email: str, context: dict) -> None:
        # Without django.contrib.sites allauth names the site after the request host, which
        # is the backend's; the mails should say MeteoLane and point at the app.
        site = SimpleNamespace(name="MeteoLane", domain=urlparse(settings.FRONTEND_URL).netloc)
        frontend_url = settings.FRONTEND_URL.rstrip("/")
        # In the recipient's language when the address has an account ("account exists" goes
        # to its owner, whoever asked); a new address gets the language of the request.
        recipient = User.objects.filter(email__iexact=email).only("language").first()
        language = recipient.language if recipient else translation.get_language()
        with translation.override(language):
            super().send_mail(template_prefix, email, {**context, "current_site": site, "frontend_url": frontend_url})

    def save_user(self, request: HttpRequest, user: User, form, commit: bool = True) -> User:
        """Start the account in the language the sign-up form was shown in."""
        user.language = translation.get_supported_language_variant(translation.get_language() or settings.LANGUAGE_CODE)
        return super().save_user(request, user, form, commit=commit)

    def populate_username(self, request: HttpRequest, user: User) -> None:
        """A neutral placeholder until step 2, never one made from the email.

        allauth's default takes the email's local part, and usernames are public: an
        abandoned sign-up would show part of someone's address. The SPA suggests the local
        part in step 2 instead, where only the owner sees it.
        """
        if user.username:
            return
        while True:
            username = f"fahrer-{secrets.token_hex(4)}"
            if not User.objects.filter(username__iexact=username).exists():
                user.username = username
                return

    def clean_username(self, username: str, shallow: bool = False) -> str:
        username = super().clean_username(username, shallow=shallow)
        # allauth passes shallow=True while it generates a username from the email's local
        # part in a loop. Such a name has no "@", so it can never equal an email.
        if not shallow and User.objects.filter(email__iexact=username).exists():
            raise ValidationError(gettext("Dieser Benutzername ist schon vergeben."))
        return username

    def clean_email(self, email: str) -> str:
        email = super().clean_email(email)
        # Usernames are public anyway, so saying so reveals nothing that isn't already visible.
        if User.objects.filter(username__iexact=email).exists():
            raise ValidationError(gettext("Diese E-Mail-Adresse kann nicht verwendet werden."))
        return email


class HeadlessAdapter(DefaultHeadlessAdapter):
    """Build the links in allauth's mails from FRONTEND_URL.

    ``HEADLESS_FRONTEND_URLS`` holds paths only. The host is added here, when the mail is
    sent, because production.py sets FRONTEND_URL after base.py builds that dict.
    """

    def get_frontend_url(self, urlname: str, **kwargs) -> str | None:
        path = headless_settings.FRONTEND_URLS.get(urlname)
        if path is None:
            return super().get_frontend_url(urlname, **kwargs)
        for name, value in kwargs.items():
            path = path.replace(f"{{{name}}}", quote(str(value), safe=""))
        return f"{settings.FRONTEND_URL.rstrip('/')}{path}"
