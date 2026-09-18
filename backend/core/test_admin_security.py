"""The public admin: failed sign-in lockout (django-axes) and two-factor sign-in (django-otp)."""

import json
from io import StringIO
from pathlib import Path
from unittest import skipUnless

from axes.models import AccessAttempt
from django.conf import settings
from django.contrib import admin
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django_otp.admin import OTPAdminSite
from django_otp.oath import TOTP
from django_otp.plugins.otp_static.models import StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from core.models import User
from core.test_signup import TEST_SETTINGS, login_code, verified_user

PASSWORD = "Correct horse battery staple 2026!"
ADMIN = f"/{settings.ADMIN_PATH}/"


@override_settings(**TEST_SETTINGS)
class LoginLockoutTests(TestCase):
    """Too many failed sign-ins from one address lock that address out, whatever account it tries."""

    def setUp(self):
        cache.clear()
        self.client = Client(enforce_csrf_checks=True)
        self.user = verified_user(username="Rider", email="rider@example.test", password=PASSWORD)

    def login(self, identifier, password, **meta):
        self.client.get("/api/auth/session", **meta)
        # The SPA sends every identity as `username`; allauth tries it as an email first.
        return self.client.post(
            "/api/allauth/browser/v1/auth/login",
            data=json.dumps({"username": identifier, "password": password}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value,
            **meta,
        )

    def post(self, path, data):
        self.client.get("/api/auth/session")
        return self.client.post(
            path,
            data=json.dumps(data),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value,
        )

    def fail(self, times, identifier="Rider", **meta):
        for _ in range(times):
            self.login(identifier, "wrong", **meta)

    def test_a_locked_out_address_gets_a_json_429_even_with_the_right_password(self):
        self.fail(settings.AXES_FAILURE_LIMIT)

        response = self.login("Rider", PASSWORD)
        self.assertEqual(response.status_code, 429)
        # The SPA parses every body as JSON and shows `detail`.
        self.assertIn("Zu viele fehlgeschlagene Anmeldeversuche", response.json()["detail"])

    def test_trying_a_different_account_each_time_still_locks_the_address(self):
        for n in range(settings.AXES_FAILURE_LIMIT):
            self.login(f"someone{n}@example.test", "wrong")

        self.assertEqual(self.login("Rider", PASSWORD).status_code, 429)

    def test_the_lockout_follows_x_real_ip_and_spares_other_addresses(self):
        self.fail(settings.AXES_FAILURE_LIMIT, HTTP_X_REAL_IP="203.0.113.5")

        self.assertEqual(self.login("Rider", PASSWORD, HTTP_X_REAL_IP="203.0.113.5").status_code, 429)
        self.assertEqual(self.login("Rider", PASSWORD, HTTP_X_REAL_IP="198.51.100.7").status_code, 200)

    def test_a_successful_sign_in_resets_the_count(self):
        self.fail(settings.AXES_FAILURE_LIMIT - 1)
        self.assertEqual(self.login("Rider", PASSWORD).status_code, 200)
        self.client.logout()

        self.fail(settings.AXES_FAILURE_LIMIT - 1)
        self.assertEqual(self.login("Rider", PASSWORD).status_code, 200)

    def test_the_lockout_covers_passwords_only_not_sign_in_by_code(self):
        # Axes counts password checks. A code proves control of the mailbox, and guessing
        # one is capped by allauth instead (3 tries per code, 3 code requests a minute
        # per address), so a locked-out address can still get in by code.
        self.fail(settings.AXES_FAILURE_LIMIT)
        self.assertEqual(self.login("Rider", PASSWORD).status_code, 429)

        self.assertEqual(
            self.post("/api/allauth/browser/v1/auth/code/request", {"email": "rider@example.test"}).status_code, 401
        )
        self.assertEqual(
            self.post("/api/allauth/browser/v1/auth/code/confirm", {"code": login_code()}).status_code, 200
        )

    def test_allauth_does_not_lock_one_identity_out_before_axes(self):
        # allauth's own login_failed limit (5 per identity) is off, so axes alone decides.
        self.fail(settings.AXES_FAILURE_LIMIT - 1)

        self.assertEqual(self.login("Rider", PASSWORD).status_code, 200)

    def test_attempts_are_logged_under_the_identity_that_was_typed(self):
        self.login("RIDER@example.test", "wrong")

        self.assertEqual(AccessAttempt.objects.get().username, "rider@example.test")


class AdminOtpSwitchTests(SimpleTestCase):
    def test_production_settings_never_read_the_switch(self):
        # DJANGO_ADMIN_OTP=false is for a local machine only. Production keeps ADMIN_OTP
        # from base.py whatever the environment says.
        source = (Path(settings.BASE_DIR) / "backend" / "settings" / "production.py").read_text()
        self.assertNotIn("DJANGO_ADMIN_OTP", source)
        self.assertNotIn("ADMIN_OTP", source)


@skipUnless(settings.ADMIN_OTP, "DJANGO_ADMIN_OTP is off in .env")
class AdminTwoFactorTests(TestCase):
    """The admin needs a code from an authenticator app as well as the password."""

    def setUp(self):
        self.staff = User.objects.create_superuser(username="boss", email="boss@example.test", password=PASSWORD)

    def admin_login(self, device=None, token=""):
        return self.client.post(
            f"{ADMIN}login/",
            {
                "username": "boss",
                "password": PASSWORD,
                "otp_device": device.persistent_id if device else "",
                "otp_token": token,
                "next": ADMIN,
            },
        )

    def test_the_admin_site_is_the_otp_one(self):
        self.assertIsInstance(admin.site, OTPAdminSite)

    def test_the_password_alone_does_not_open_the_admin(self):
        TOTPDevice.objects.create(user=self.staff, name="default", confirmed=True)

        self.admin_login()
        response = self.client.get(ADMIN)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_a_session_from_the_app_login_does_not_open_the_admin(self):
        self.client.force_login(self.staff)

        self.assertEqual(self.client.get(ADMIN).status_code, 302)

    def test_password_and_a_valid_code_open_the_admin(self):
        device = TOTPDevice.objects.create(user=self.staff, name="default", confirmed=True)
        token = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift).token()

        response = self.admin_login(device, str(token).zfill(device.digits))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(ADMIN).status_code, 200)


class AddTotpDeviceCommandTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser(username="boss", email="boss@example.test", password=PASSWORD)
        self.rider = User.objects.create_user(username="rider", email="rider@example.test", password=PASSWORD)

    def run_command(self, *args):
        out = StringIO()
        call_command("add_totp_device", *args, stdout=out)
        return out.getvalue()

    def test_refuses_a_user_who_is_not_staff(self):
        with self.assertRaises(CommandError):
            self.run_command("--identifier", "rider")
        self.assertFalse(TOTPDevice.objects.exists())

    def test_creates_a_confirmed_device_and_backup_codes(self):
        output = self.run_command("--identifier", "BOSS@example.test", "--backup-codes", "3")

        device = TOTPDevice.objects.get(user=self.staff)
        self.assertTrue(device.confirmed)
        self.assertIn("otpauth://totp/", output)
        codes = StaticToken.objects.filter(device__user=self.staff).values_list("token", flat=True)
        self.assertEqual(len(codes), 3)
        for code in codes:
            self.assertIn(code, output)

    def test_a_second_device_needs_replace(self):
        self.run_command("--identifier", "boss")
        first = TOTPDevice.objects.get(user=self.staff)

        with self.assertRaises(CommandError):
            self.run_command("--identifier", "boss")

        self.run_command("--identifier", "boss", "--replace")
        replacement = TOTPDevice.objects.get(user=self.staff)
        self.assertNotEqual(replacement.key, first.key)
        self.assertEqual(StaticToken.objects.filter(device__user=self.staff).count(), 10)
