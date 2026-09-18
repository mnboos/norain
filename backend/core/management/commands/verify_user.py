"""Mark a user's email as verified so they can sign in to the app.

Usage: python manage.py verify_user --identifier you@example.test

allauth refuses a sign-in until the account's primary `EmailAddress` is verified.
`createsuperuser` sets that already; this is for accounts made by hand in the admin or
the shell, which get no `EmailAddress` row at all.
"""

from allauth.account.models import EmailAddress
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
        # Only one primary address per user: an older row may hold it if the email changed.
        EmailAddress.objects.filter(user=user).exclude(email__iexact=user.email).update(primary=False)
        address, _ = EmailAddress.objects.get_or_create(user=user, email=user.email.lower())
        address.primary = True
        address.verified = verified
        address.save(update_fields=["primary", "verified"])
        if verified:
            # Sign-in also requires is_active; verifying without it would be a confusing no-op.
            user.is_active = True
            # An account made by hand already has the username and password an admin gave
            # it, so the app should not send it through step 2 of sign-up.
            if user.has_usable_password():
                user.signup_completed = True
            user.save(update_fields=["is_active", "signup_completed"])

        state = "verified" if verified else "unverified"
        self.stdout.write(self.style.SUCCESS(f"{user.username} <{user.email}> is now {state}"))
