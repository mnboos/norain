"""Set up two-factor sign-in for a staff user, so they can reach the admin.

Usage: python manage.py add_totp_device --identifier you@example.test [--replace]

The admin asks for a code from an authenticator app (django-otp), and a device can only be
added from inside the admin — so the first one has to come from here. Prints the key to
enter in the app, plus one-time backup codes for when the phone is lost.
"""

from base64 import b32encode

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice


class Command(BaseCommand):
    help = "Create an authenticator-app device and backup codes for a staff user"

    def add_arguments(self, parser):
        parser.add_argument("--identifier", required=True, help="Email address or username")
        parser.add_argument("--replace", action="store_true", help="Remove the user's existing devices first")
        parser.add_argument("--backup-codes", type=int, default=10, help="How many one-time backup codes (default 10)")

    def handle(self, *args, **options):
        value = options["identifier"].strip().lower()
        User = get_user_model()
        try:
            user = User.objects.get(Q(email__iexact=value) | Q(username__iexact=value))
        except User.DoesNotExist:
            raise CommandError(f"No user matching {value!r}.") from None
        except User.MultipleObjectsReturned:
            raise CommandError(f"{value!r} matches more than one user — pass the exact username.") from None

        if not user.is_staff:
            raise CommandError(f"{user.username} is not staff and cannot use the admin.")

        with transaction.atomic():
            if TOTPDevice.objects.filter(user=user).exists():
                if not options["replace"]:
                    raise CommandError(f"{user.username} already has a device. Pass --replace to swap it.")
                TOTPDevice.objects.filter(user=user).delete()
                StaticDevice.objects.filter(user=user).delete()

            device = TOTPDevice.objects.create(user=user, name="default", confirmed=True)

            backup = StaticDevice.objects.create(user=user, name="backup", confirmed=True)
            codes = [StaticToken.random_token() for _ in range(max(options["backup_codes"], 0))]
            StaticToken.objects.bulk_create(StaticToken(device=backup, token=code) for code in codes)

        # The base32 form is what authenticator apps accept for manual entry.
        key = b32encode(device.bin_key).decode()
        self.stdout.write(self.style.SUCCESS(f"Device created for {user.username} <{user.email}>"))
        self.stdout.write(f"Key (enter it in your authenticator app): {key}")
        self.stdout.write(f"Or open this link on the phone: {device.config_url}")
        if codes:
            self.stdout.write("Backup codes, each works once. Store them now; they are not shown again:")
            for code in codes:
                self.stdout.write(f"  {code}")
