"""Sign-up in two steps, sign-in and password reset, through allauth's headless API."""

import json
import re
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

from core.mail import ReadableConsoleBackend
from core.models import User

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

    def complete(self, username, password=PASSWORD):
        return self.post("/api/auth/complete-signup", {"username": username, "password": password})


def last_mail_match(pattern: str) -> str:
    match = re.search(pattern, mail.outbox[-1].body)
    assert match, mail.outbox[-1].body
    return match.group(1)


def verify_key() -> str:
    # The link carries the key URL-encoded; the SPA reads it decoded from route.query.
    return unquote(last_mail_match(r"http://frontend\.example\.test/account\?verify_key=(\S+)"))


def reset_key() -> str:
    return unquote(last_mail_match(r"http://frontend\.example\.test/account\?reset_key=(\S+)"))


def login_code() -> str:
    # The code sits alone on its own line in allauth's mail.
    return last_mail_match(r"(?m)^([A-Z0-9]+(?:-[A-Z0-9]+)+)$")


@override_settings(**TEST_SETTINGS)
class SignupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.browser = SpaClient()

    def sign_up_and_verify(self, email="rider@example.test") -> User:
        self.assertEqual(self.browser.signup(email).status_code, 401)
        response = self.browser.verify(verify_key())
        self.assertEqual(response.status_code, 200, response.content)
        return User.objects.get(email=email)

    def test_step_one_takes_only_the_email_and_needs_csrf(self):
        self.assertEqual(
            self.browser.post(f"{ALLAUTH}/auth/signup", {"email": "a@example.test"}, csrf=False).status_code, 403
        )

        response = self.browser.signup("Rider@Example.test")
        # 401 with a pending flow is allauth's "next, verify the email".
        self.assertEqual(response.status_code, 401)
        self.assertIn(
            "verify_email", [flow["id"] for flow in response.json()["data"]["flows"] if flow.get("is_pending")]
        )

        user = User.objects.get(email__iexact="rider@example.test")
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.signup_completed)
        self.assertFalse(EmailAddress.objects.get(user=user).verified)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("http://frontend.example.test/account?verify_key=", mail.outbox[0].body)
        self.assertIn("NoRain", mail.outbox[0].body)
        self.assertFalse(self.browser.session()["authenticated"])

    def test_the_link_in_the_same_browser_signs_in_and_step_two_finishes(self):
        user = self.sign_up_and_verify()
        session = self.browser.session()
        self.assertTrue(session["authenticated"])
        self.assertFalse(session["user"]["signup_complete"])
        self.assertTrue(EmailAddress.objects.get(user=user).verified)

        response = self.browser.complete("Velofahrer")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["user"]["signup_complete"])
        # The new password must not have signed the user out.
        self.assertTrue(self.browser.session()["user"]["signup_complete"])

        self.browser.logout()
        for identifier in ("rider@example.test", "RIDER@EXAMPLE.TEST", "Velofahrer", "velofahrer"):
            with self.subTest(identifier=identifier):
                response = self.browser.login(identifier)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(self.browser.session()["user"]["username"], "Velofahrer")
                self.browser.logout()

    def test_the_link_in_another_browser_verifies_and_the_code_signs_in(self):
        self.assertEqual(self.browser.signup("rider@example.test").status_code, 401)
        key = verify_key()

        other = SpaClient()
        self.assertEqual(other.verify(key).status_code, 401)
        self.assertFalse(other.session()["authenticated"])
        self.assertTrue(EmailAddress.objects.get(email="rider@example.test").verified)

        self.assertEqual(other.request_code("rider@example.test").status_code, 401)
        self.assertEqual(other.confirm_code(login_code()).status_code, 200)
        session = other.session()
        self.assertTrue(session["authenticated"])
        self.assertFalse(session["user"]["signup_complete"])
        self.assertEqual(other.complete("Rider").status_code, 200)

    def test_an_unverified_account_cannot_sign_in_by_password_but_by_code(self):
        self.browser.signup("rider@example.test")
        user = User.objects.get(email="rider@example.test")
        user.set_password(PASSWORD)
        user.save()

        response = self.browser.login("rider@example.test")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(self.browser.session()["authenticated"])

        # A code proves the mailbox as well as the link does: allauth marks the address
        # verified and signs in, and step 2 is still due.
        other = SpaClient()
        self.assertEqual(other.request_code("rider@example.test").status_code, 401)
        self.assertEqual(other.confirm_code(login_code()).status_code, 200)
        self.assertTrue(EmailAddress.objects.get(user=user).verified)
        self.assertFalse(other.session()["user"]["signup_complete"])

    def test_a_registered_email_gets_the_same_reply_and_no_second_account(self):
        verified_user()
        mail.outbox.clear()

        response = self.browser.signup("RIDER@example.test")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(User.objects.count(), 1)
        # allauth tells the owner of the address instead of the person signing up.
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn("verify_key=", mail.outbox[0].body)

    def test_an_email_equal_to_a_username_is_refused(self):
        verified_user(username="handle@home.test", email="real@example.test")

        response = self.browser.signup("handle@home.test")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(User.objects.count(), 1)

    def test_a_generated_username_avoids_an_existing_one_in_any_case(self):
        verified_user(username="Rider", email="first@example.test")

        user = self.sign_up_and_verify("rider@example.test")
        self.assertNotEqual(user.username.lower(), "rider")

    def test_step_two_keeps_the_username_rules(self):
        verified_user(username="Taken", email="taken@example.test")
        self.sign_up_and_verify()

        for username in ("tAkEn", "taken@example.test", "not a username"):
            with self.subTest(username=username):
                self.assertEqual(self.browser.complete(username).status_code, 400)
        self.assertEqual(self.browser.complete("Fresh", password="short").status_code, 400)

        self.assertEqual(self.browser.complete("Fresh").status_code, 200)
        self.assertEqual(self.browser.complete("Other").status_code, 409)

    def test_step_two_needs_a_signed_in_user(self):
        self.assertEqual(self.browser.complete("Rider").status_code, 401)

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

        emails = {address.user.username: address for address in EmailAddress.objects.select_related("user")}
        self.assertEqual(emails["done"].email, "done@example.test")
        self.assertTrue(emails["done"].verified and emails["done"].primary)
        self.assertFalse(emails["waiting"].verified)
        self.assertTrue(emails["boss"].verified)
        self.assertTrue(all(user.signup_completed for user in User.objects.all()))
        # Woken so allauth will send it a new link; it still cannot sign in unverified.
        self.assertTrue(User.objects.get(username="waiting").is_active)


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
