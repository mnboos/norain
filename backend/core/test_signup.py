"""Sign-up in two steps (emailed code, then profile), sign-in and password reset, through allauth's headless API."""

import json
import re
import time
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import unquote

from allauth.account.models import EmailAddress
from django.core import mail
from django.core.cache import cache
from django.core.mail import EmailMessage
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from core.mail import ReadableConsoleBackend
from core.models import User
from core.tasks import _purge_abandoned_signups

PASSWORD = "Correct horse battery staple 2026!"
ALLAUTH = "/api/allauth/browser/v1"

TEST_SETTINGS = {
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "DEFAULT_FROM_EMAIL": "noreply@example.test",
    "FRONTEND_URL": "http://frontend.example.test",
    # allauth's rate limits live in the cache; tests must not need Redis.
    "CACHES": {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
}


def verified_user(username="Rider", email="rider@example.test", password=PASSWORD, **fields) -> User:
    """An account that finished both sign-up steps, as allauth would have left it."""
    fields.setdefault("signup_completed", True)
    user = User.objects.create_user(username=username, email=email, password=password, **fields)
    EmailAddress.objects.create(user=user, email=email.lower(), primary=True, verified=True)
    return user


class SpaClient:
    """One browser: its own cookies, the CSRF header on every write, JSON in and out."""

    def __init__(self, **meta):
        self.client = Client(enforce_csrf_checks=True)
        self.meta = meta

    def csrf(self) -> dict:
        self.client.get("/api/auth/session", **self.meta)
        return {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value}

    def post(self, path, data=None, *, csrf=True):
        headers = self.csrf() if csrf else {}
        return self.client.post(
            path, data=json.dumps(data or {}), content_type="application/json", **headers, **self.meta
        )

    def session(self) -> dict:
        return self.client.get("/api/auth/session", **self.meta).json()

    def signup(self, email):
        return self.post(f"{ALLAUTH}/auth/signup", {"email": email})

    def verify(self, key):
        return self.post(f"{ALLAUTH}/auth/email/verify", {"key": key})

    def login(self, identifier, password=PASSWORD):
        return self.post(f"{ALLAUTH}/auth/login", {"username": identifier, "password": password})

    def logout(self):
        return self.client.delete(f"{ALLAUTH}/auth/session", **self.csrf(), **self.meta)

    def request_code(self, email):
        return self.post(f"{ALLAUTH}/auth/code/request", {"email": email})

    def confirm_code(self, code):
        return self.post(f"{ALLAUTH}/auth/code/confirm", {"code": code})

    def complete(self, username, password=PASSWORD, **fields):
        return self.post("/api/auth/complete-signup", {"username": username, "password": password, **fields})


def last_mail_match(pattern: str, index: int = -1) -> str:
    match = re.search(pattern, mail.outbox[index].body)
    assert match, mail.outbox[index].body
    return match.group(1)


def reset_key() -> str:
    return unquote(last_mail_match(r"http://frontend\.example\.test/account\?reset_key=(\S+)"))


def mailed_code(index: int = -1) -> str:
    """The code in a mail (the last by default): sign-up or sign-in, it sits alone on its line."""
    return last_mail_match(r"(?m)^([A-Z0-9]+(?:-[A-Z0-9]+)+)$", index)


def after_code_cooldown():
    """Move allauth's rate-limit clock past the 10 s between two codes for one address.

    Only allauth's clock: patching time.time would move the locmem cache's expiry too.
    """
    later = time.time() + 11
    return patch(
        "allauth.core.internal.ratelimit.time",
        SimpleNamespace(time=lambda: later, monotonic=time.monotonic, sleep=time.sleep),
    )


def pending_flows(response) -> list[str]:
    return [flow["id"] for flow in response.json()["data"]["flows"] if flow.get("is_pending")]


@override_settings(**TEST_SETTINGS)
class SignupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.browser = SpaClient()

    def sign_up_and_verify(self, email="rider@example.test") -> User:
        self.assertEqual(self.browser.signup(email).status_code, 401)
        response = self.browser.verify(mailed_code())
        self.assertEqual(response.status_code, 200, response.content)
        return User.objects.get(email=email)

    def test_step_one_takes_only_the_email_and_mails_a_code(self):
        self.assertEqual(
            self.browser.post(f"{ALLAUTH}/auth/signup", {"email": "a@example.test"}, csrf=False).status_code, 403
        )

        response = self.browser.signup("Rider@Example.test")
        # 401 with a pending flow is allauth's "next, verify the email".
        self.assertEqual(response.status_code, 401)
        self.assertIn("verify_email", pending_flows(response))

        user = User.objects.get(email__iexact="rider@example.test")
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.signup_completed)
        self.assertFalse(EmailAddress.objects.get(user=user).verified)
        # Usernames are public: the placeholder must not give away the address.
        self.assertTrue(user.username.startswith("fahrer-"))
        self.assertNotIn("rider", user.username.lower())
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(mailed_code())
        self.assertNotIn("http", mail.outbox[0].body.split("NoRain ·")[0])
        self.assertIn("Bestätigungscode", mail.outbox[0].subject)
        self.assertFalse(self.browser.session()["authenticated"])

    def test_the_code_signs_in_and_step_two_finishes_with_a_password(self):
        user = self.sign_up_and_verify()
        session = self.browser.session()
        self.assertTrue(session["authenticated"])
        self.assertFalse(session["user"]["signup_complete"])
        self.assertFalse(session["user"]["has_password"])
        self.assertTrue(EmailAddress.objects.get(user=user).verified)

        response = self.browser.complete("Velofahrer", default_profile="ebike")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["user"]["signup_complete"])
        self.assertEqual(response.json()["user"]["default_profile"], "ebike")
        # The new password must not have signed the user out.
        self.assertTrue(self.browser.session()["user"]["has_password"])

        self.browser.logout()
        for identifier in ("rider@example.test", "RIDER@EXAMPLE.TEST", "Velofahrer", "velofahrer"):
            with self.subTest(identifier=identifier):
                response = self.browser.login(identifier)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(self.browser.session()["user"]["username"], "Velofahrer")
                self.browser.logout()

    def test_step_two_without_a_password_leaves_sign_in_by_code(self):
        user = self.sign_up_and_verify()

        response = self.browser.post("/api/auth/complete-signup", {"username": "Velofahrer"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()["user"]["has_password"])
        self.assertEqual(response.json()["user"]["default_profile"], "bike")
        user.refresh_from_db()
        self.assertTrue(user.signup_completed)
        self.assertFalse(user.has_usable_password())
        self.assertTrue(self.browser.session()["authenticated"])

        self.browser.logout()
        self.assertEqual(self.browser.login("Velofahrer", "").status_code, 400)
        self.assertEqual(self.browser.request_code("rider@example.test").status_code, 401)
        self.assertEqual(self.browser.confirm_code(mailed_code()).status_code, 200)
        self.assertTrue(self.browser.session()["user"]["signup_complete"])

    def test_a_wrong_code_counts_and_the_third_ends_the_attempt(self):
        self.browser.signup("rider@example.test")
        code = mailed_code()

        for _ in range(3):
            self.assertEqual(self.browser.verify("WRONG-CODE").status_code, 400)
        # allauth drops the pending verification: the right code is no use any more.
        self.assertEqual(self.browser.verify(code).status_code, 409)
        self.assertFalse(self.browser.session()["authenticated"])

    def test_a_resent_code_replaces_the_first(self):
        self.browser.signup("rider@example.test")
        first = mailed_code()

        # Straight away is too soon: allauth answers 429 and keeps the first code.
        self.assertEqual(self.browser.post(f"{ALLAUTH}/auth/email/verify/resend").status_code, 429)
        with after_code_cooldown():
            response = self.browser.post(f"{ALLAUTH}/auth/email/verify/resend")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(mail.outbox), 2)
        second = mailed_code()
        self.assertNotEqual(first, second)

        self.assertEqual(self.browser.verify(first).status_code, 400)
        self.assertEqual(self.browser.verify(second).status_code, 200)

    def test_the_code_is_no_use_in_another_browser_but_sign_in_by_code_is(self):
        self.assertEqual(self.browser.signup("rider@example.test").status_code, 401)
        code = mailed_code()

        other = SpaClient()
        self.assertEqual(other.verify(code).status_code, 409)
        self.assertFalse(EmailAddress.objects.get(email="rider@example.test").verified)

        self.assertEqual(other.request_code("rider@example.test").status_code, 401)
        self.assertEqual(other.confirm_code(mailed_code()).status_code, 200)
        session = other.session()
        self.assertTrue(session["authenticated"])
        self.assertFalse(session["user"]["signup_complete"])
        self.assertTrue(EmailAddress.objects.get(email="rider@example.test").verified)
        self.assertEqual(other.complete("Rider").status_code, 200)

    def test_an_unverified_account_cannot_sign_in_by_password_but_by_code(self):
        self.browser.signup("rider@example.test")
        user = User.objects.get(email="rider@example.test")
        user.set_password(PASSWORD)
        user.save()

        with after_code_cooldown():
            response = self.browser.login("rider@example.test")
        self.assertEqual(response.status_code, 401)
        # allauth mails a new code; the SPA shows the code field for it.
        self.assertIn("verify_email", pending_flows(response))
        self.assertFalse(self.browser.session()["authenticated"])

        # A code proves the mailbox as well as the sign-up code does: allauth marks the
        # address verified and signs in, and step 2 is still due.
        other = SpaClient()
        self.assertEqual(other.request_code("rider@example.test").status_code, 401)
        self.assertEqual(other.confirm_code(mailed_code()).status_code, 200)
        self.assertTrue(EmailAddress.objects.get(user=user).verified)
        self.assertFalse(other.session()["user"]["signup_complete"])

    def test_a_registered_email_gets_the_same_reply_and_no_second_account(self):
        verified_user()
        mail.outbox.clear()

        response = self.browser.signup("RIDER@example.test")
        self.assertEqual(response.status_code, 401)
        self.assertIn("verify_email", pending_flows(response))
        self.assertEqual(User.objects.count(), 1)
        # allauth tells the owner of the address instead, with no code to use.
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("http://frontend.example.test/account?mode=code", mail.outbox[0].body)
        self.assertNotRegex(mail.outbox[0].body, r"(?m)^[A-Z0-9]+(?:-[A-Z0-9]+)+$")
        self.assertEqual(self.browser.verify("ABCD-EFGH").status_code, 400)

    def test_an_email_equal_to_a_username_is_refused(self):
        verified_user(username="handle@home.test", email="real@example.test")

        response = self.browser.signup("handle@home.test")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(User.objects.count(), 1)

    def test_step_two_keeps_the_username_rules(self):
        verified_user(username="Taken", email="taken@example.test")
        self.sign_up_and_verify()

        for username in ("tAkEn", "taken@example.test", "not a username", ""):
            with self.subTest(username=username):
                self.assertEqual(self.browser.complete(username).status_code, 400)
        self.assertEqual(self.browser.complete("Fresh", password="short").status_code, 400)
        self.assertEqual(self.browser.complete("Fresh", default_profile="tandem").status_code, 400)

        self.assertEqual(self.browser.complete("Fresh").status_code, 200)
        self.assertEqual(self.browser.complete("Other").status_code, 409)

    def test_step_two_needs_a_signed_in_user(self):
        self.assertEqual(self.browser.complete("Rider").status_code, 401)
        self.assertEqual(self.browser.client.get("/api/auth/username-available?username=x").status_code, 401)

    def test_the_live_username_check_uses_the_same_rules(self):
        verified_user(username="Taken", email="taken@example.test")
        user = self.sign_up_and_verify()

        def check(username):
            response = self.browser.client.get("/api/auth/username-available", {"username": username})
            self.assertEqual(response.status_code, 200)
            return response.json()["available"]

        self.assertTrue(check("Fresh"))
        self.assertTrue(check(user.username.upper()))
        for username in ("tAkEn", "taken@example.test", "not a username", ""):
            with self.subTest(username=username):
                self.assertFalse(check(username))

    def test_the_live_username_check_is_limited_per_account(self):
        self.sign_up_and_verify()
        with patch("core.auth.views.USERNAME_CHECKS_PER_MINUTE", 2):
            codes = [
                self.browser.client.get("/api/auth/username-available", {"username": "x"}).status_code for _ in range(3)
            ]
        self.assertEqual(codes, [200, 200, 429])

    def test_the_default_profile_can_be_changed_later(self):
        user = verified_user()
        self.browser.client.force_login(user)

        response = self.browser.post("/api/auth/profile", {"default_profile": "fast_ebike"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["default_profile"], "fast_ebike")
        self.assertEqual(self.browser.post("/api/auth/profile", {"default_profile": "car"}).status_code, 400)
        user.refresh_from_db()
        self.assertEqual(user.default_profile, "fast_ebike")

    def test_a_password_reset_does_not_skip_step_two(self):
        user = self.sign_up_and_verify()
        self.browser.logout()

        self.browser.post(f"{ALLAUTH}/auth/password/request", {"email": "rider@example.test"})
        key = reset_key()
        response = self.browser.post(f"{ALLAUTH}/auth/password/reset", {"key": key, "password": PASSWORD})
        # 401: allauth does not sign in on a reset (LOGIN_ON_PASSWORD_RESET is off).
        self.assertEqual(response.status_code, 401, response.content)

        user.refresh_from_db()
        self.assertTrue(user.has_usable_password())
        self.assertFalse(user.signup_completed)
        self.assertEqual(self.browser.login(user.username).status_code, 200)
        self.assertFalse(self.browser.session()["user"]["signup_complete"])


@override_settings(**TEST_SETTINGS)
class AbandonedSignupPurgeTests(TestCase):
    def age(self, user: User, days: int) -> None:
        User.objects.filter(pk=user.pk).update(created_at=timezone.now() - timedelta(days=days))

    def test_only_unverified_step_one_accounts_past_a_week_go(self):
        browser = SpaClient()
        browser.signup("old@example.test")
        browser = SpaClient()
        browser.signup("new@example.test")
        abandoned = User.objects.get(email="old@example.test")
        self.age(abandoned, 8)
        self.age(User.objects.get(email="new@example.test"), 1)

        # Verified by code but step 2 never done: a real account.
        verified_waiting = User.objects.create_user(username="waiting", email="waiting@example.test")
        EmailAddress.objects.create(user=verified_waiting, email="waiting@example.test", primary=True, verified=True)
        # Made by hand in the admin, verify_user not run yet: it has a password.
        by_hand = User.objects.create_user(username="byhand", email="byhand@example.test", password=PASSWORD)
        staff = User.objects.create_user(username="staff", email="staff@example.test", is_staff=True)
        for user in (verified_waiting, by_hand, staff):
            self.age(user, 30)

        self.assertEqual(_purge_abandoned_signups(), 1)
        self.assertFalse(User.objects.filter(pk=abandoned.pk).exists())
        self.assertEqual(
            set(User.objects.values_list("email", flat=True)),
            {"new@example.test", "waiting@example.test", "byhand@example.test", "staff@example.test"},
        )


@override_settings(**TEST_SETTINGS)
class SignInTests(TestCase):
    def setUp(self):
        cache.clear()
        self.browser = SpaClient()

    def test_a_username_containing_at_resolves_as_a_username(self):
        """UnicodeUsernameValidator allows "@", and the SPA sends every identity as `username`."""
        verified_user(username="handle@home", email="real@example.test")

        self.assertEqual(self.browser.login("handle@home").status_code, 200)
        self.assertEqual(self.browser.session()["user"]["email"], "real@example.test")

    def test_sign_in_queues_the_forecast_refresh_and_survives_a_queue_failure(self):
        user = verified_user()
        enqueue = Mock()
        with patch("core.auth.signals.refresh_user_forecasts", SimpleNamespace(enqueue=enqueue)):
            self.assertEqual(self.browser.login("Rider", "wrong").status_code, 400)
            enqueue.assert_not_called()
            self.assertEqual(self.browser.login("Rider").status_code, 200)
        enqueue.assert_called_once_with(user.pk)

        self.browser.logout()
        broken = SimpleNamespace(enqueue=Mock(side_effect=RuntimeError("queue down")))
        with patch("core.auth.signals.refresh_user_forecasts", broken):
            self.assertEqual(self.browser.login("Rider").status_code, 200)

    def test_a_superuser_can_sign_in_to_the_app(self):
        User.objects.create_superuser(username="admin", email="admin@example.test", password=PASSWORD)

        self.assertEqual(self.browser.login("admin").status_code, 200)
        self.assertTrue(self.browser.session()["user"]["signup_complete"])

    def test_a_password_reset_link_sets_a_new_password(self):
        verified_user()

        self.assertEqual(
            self.browser.post(f"{ALLAUTH}/auth/password/request", {"email": "rider@example.test"}).status_code, 200
        )
        key = reset_key()
        new_password = "Different correct battery staple 2026!"
        response = self.browser.post(f"{ALLAUTH}/auth/password/reset", {"key": key, "password": new_password})
        # 401: allauth does not sign in on a reset (LOGIN_ON_PASSWORD_RESET is off).
        self.assertEqual(response.status_code, 401, response.content)
        self.browser.logout()

        self.assertEqual(self.browser.login("Rider").status_code, 400)
        self.assertEqual(self.browser.login("Rider", new_password).status_code, 200)


class MigrationTests(TransactionTestCase):
    """0011 moves verification into allauth's EmailAddress."""

    before = [("core", "0010_recurringroute_last_viewed_at"), ("account", "0009_emailaddress_unique_primary_email")]
    after = [("core", "0011_allauth_email_addresses")]

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_existing_accounts_keep_their_state(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        old_user_model = executor.loader.project_state(self.before).apps.get_model("core", "User")
        old_user_model.objects.create(username="done", email="Done@example.test", email_verified=True)
        old_user_model.objects.create(username="waiting", email="waiting@example.test", is_active=False)
        old_user_model.objects.create(username="boss", email="boss@example.test", is_superuser=True)

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.after)

        # The historical models: later migrations added columns the current User has.
        apps = executor.loader.project_state(self.after).apps
        user_model = apps.get_model("core", "User")
        address_model = apps.get_model("account", "EmailAddress")
        emails = {address.user.username: address for address in address_model.objects.select_related("user")}
        self.assertEqual(emails["done"].email, "done@example.test")
        self.assertTrue(emails["done"].verified and emails["done"].primary)
        self.assertFalse(emails["waiting"].verified)
        self.assertTrue(emails["boss"].verified)
        self.assertTrue(all(user.signup_completed for user in user_model.objects.all()))
        # Woken so allauth will send it a new link; it still cannot sign in unverified.
        self.assertTrue(user_model.objects.get(username="waiting").is_active)


@override_settings(**TEST_SETTINGS)
class VerifyUserCommandTests(TestCase):
    def test_a_hand_made_account_can_sign_in_after_verify_user(self):
        User.objects.create_user(username="Rider", email="rider@example.test", password=PASSWORD, is_active=False)
        browser = SpaClient()
        browser.login("Rider")
        self.assertFalse(browser.session()["authenticated"])

        call_command("verify_user", identifier="RIDER@example.test", stdout=StringIO())

        self.assertEqual(browser.login("Rider").status_code, 200)
        # It already has the username and password an admin gave it: no step 2.
        self.assertTrue(browser.session()["user"]["signup_complete"])

    def test_a_changed_email_moves_the_primary_address(self):
        user = verified_user()
        User.objects.filter(pk=user.pk).update(email="new@example.test")

        call_command("verify_user", identifier="Rider", stdout=StringIO())

        primary = EmailAddress.objects.get(user=user, primary=True)
        self.assertEqual(primary.email, "new@example.test")
        self.assertTrue(primary.verified)


class ReadableConsoleBackendTests(SimpleTestCase):
    def test_a_long_link_comes_out_whole(self):
        link = "http://localhost:3000/account?reset_key=2-cz3k4q-6a1b2c3d4e5f60718293a4b5c6d7e8f9"
        stream = StringIO()
        message = EmailMessage("NoRain: Password Reset", f"Click the link below.\n\n{link}\n", "a@b.test", ["c@d.test"])

        ReadableConsoleBackend(stream=stream).send_messages([message])

        # Django's own console backend would print "reset_key=3D2-…" with "=" line breaks.
        self.assertIn(f"\n{link}\n", stream.getvalue())
        self.assertNotIn("=3D", stream.getvalue())
        self.assertIn("To: c@d.test", stream.getvalue())
