"""Mark a user's email as verified so they can sign in to the app.

Usage: python manage.py verify_user --identifier you@example.test

`IdentityBackend` refuses any account whose `email_verified` is False, and
`createsuperuser` cannot set it — so a fresh superuser can reach /admin but not the SPA
until this runs (or until the verification email link is followed).
"""

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from core.models import User


class Command(BaseCommand):
    help = "Mark a user's email address as verified (and the user active)"

    def add_arguments(self, parser):
        parser.add_argument("--identifier", required=True, help="Email address or username")
        parser.add_argument("--unverify", action="store_true", help="Revoke verification instead")

    def handle(self, *args, **options):
        value = options["identifier"].strip().lower()
        try:
            user = User.objects.get(Q(email__iexact=value) | Q(username__iexact=value))
        except User.DoesNotExist:
            raise CommandError(f"No user matching {value!r}.") from None
        except User.MultipleObjectsReturned:
            raise CommandError(f"{value!r} matches more than one user — pass the exact username.") from None

        verified = not options["unverify"]
        user.email_verified = verified
        # Sign-in also requires is_active; verifying without it would be a confusing no-op.
        fields = ["email_verified"]
        if verified and not user.is_active:
            user.is_active = True
            fields.append("is_active")
        user.save(update_fields=fields)

        state = "verified" if verified else "unverified"
        self.stdout.write(self.style.SUCCESS(f"{user.username} <{user.email}> is now {state}"))
