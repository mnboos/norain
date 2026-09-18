"""django-allauth adapters: NoRain's rules on top of allauth's defaults."""

from types import SimpleNamespace
from urllib.parse import quote, urlparse

from allauth.account.adapter import DefaultAccountAdapter
from allauth.headless import app_settings as headless_settings
from allauth.headless.adapter import DefaultHeadlessAdapter
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest

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
        # is the backend's; the mails should say NoRain and point at the app.
        site = SimpleNamespace(name="NoRain", domain=urlparse(settings.FRONTEND_URL).netloc)
        super().send_mail(template_prefix, email, {**context, "current_site": site})

    def clean_username(self, username: str, shallow: bool = False) -> str:
        username = super().clean_username(username, shallow=shallow)
        # allauth passes shallow=True while it generates a username from the email's local
        # part in a loop. Such a name has no "@", so it can never equal an email.
        if not shallow and User.objects.filter(email__iexact=username).exists():
            raise ValidationError("This username is already taken.")
        return username

    def clean_email(self, email: str) -> str:
        email = super().clean_email(email)
        # Usernames are public anyway, so saying so reveals nothing that isn't already visible.
        if User.objects.filter(username__iexact=email).exists():
            raise ValidationError("This email address cannot be used.")
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
