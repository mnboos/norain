"""The language: German by default, English from the browser, the account's setting above both.

See "Internationalisation" in CLAUDE.md. The msgids are German, so a test in German reads the
source text and one in English proves the compiled catalog (core/locale/en) is there.
"""

from types import SimpleNamespace

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import translation
from django.utils.translation import gettext

from core.briefings import briefing_body
from core.models import User
from core.test_signup import TEST_SETTINGS, SpaClient, verified_user

MISSING_ROUTE = "/api/public/routes/no-such-route"
# allauth puts this in front of every subject ("MeteoLane: "); it is not translated.
SUBJECT_PREFIX = settings.ACCOUNT_EMAIL_SUBJECT_PREFIX


class CatalogTests(SimpleTestCase):
    def test_the_english_catalog_is_compiled(self):
        # Fails when core/locale/en/LC_MESSAGES/django.mo is missing or older than the .po.
        with translation.override("en"):
            self.assertEqual(gettext("Route nicht gefunden."), "Route not found.")
        with translation.override("de"):
            self.assertEqual(gettext("Route nicht gefunden."), "Route nicht gefunden.")

    def test_a_briefing_is_worded_in_the_active_language(self):
        route = SimpleNamespace(name="Pendeln")
        with translation.override("en"):
            self.assertIn("weather data is not available", briefing_body(None, route))
        with translation.override("de"):
            self.assertIn("Wetterdaten sind derzeit nicht verfügbar", briefing_body(None, route))


@override_settings(**TEST_SETTINGS)
class RequestLanguageTests(TestCase):
    def test_german_without_any_preference(self):
        response = Client().get(MISSING_ROUTE)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Route nicht gefunden.")

    def test_an_anonymous_visitor_gets_the_browser_language(self):
        response = Client().get(MISSING_ROUTE, HTTP_ACCEPT_LANGUAGE="en-GB,en;q=0.9")
        self.assertEqual(response.json()["detail"], "Route not found.")

    def test_the_account_language_wins_over_the_browser(self):
        client = Client()
        client.force_login(verified_user(language="en"))
        response = client.get(MISSING_ROUTE, HTTP_ACCEPT_LANGUAGE="de-CH")
        self.assertEqual(response.json()["detail"], "Route not found.")

    def test_the_language_is_part_of_the_session_and_can_be_changed_alone(self):
        user = verified_user(default_profile="ebike")
        browser = SpaClient()
        browser.client.force_login(user)
        self.assertEqual(browser.session()["user"]["language"], "de")

        response = browser.post("/api/auth/profile", {"language": "en"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["user"]["language"], "en")
        user.refresh_from_db()
        # Changing the language leaves the bike profile alone.
        self.assertEqual((user.language, user.default_profile), ("en", "ebike"))

        self.assertEqual(browser.post("/api/auth/profile", {"language": "fr"}).status_code, 400)
        self.assertEqual(browser.post("/api/auth/profile", {}).status_code, 400)


@override_settings(**TEST_SETTINGS)
class SignupLanguageTests(TestCase):
    def setUp(self):
        # allauth's rate limits live in the cache; another test's sign-up must not block these.
        cache.clear()

    def test_a_sign_up_in_english_starts_an_english_account_and_mails_in_english(self):
        browser = SpaClient(HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(browser.signup("rider@example.test").status_code, 401)
        self.assertEqual(User.objects.get(email="rider@example.test").language, "en")
        self.assertEqual(mail.outbox[-1].subject, SUBJECT_PREFIX + "Your confirmation code")

    def test_a_mail_to_an_existing_account_uses_its_owner_language(self):
        verified_user(email="rider@example.test", language="de")
        # Someone with an English browser tries to sign up with that address.
        SpaClient(HTTP_ACCEPT_LANGUAGE="en").signup("rider@example.test")
        self.assertEqual(mail.outbox[-1].subject, SUBJECT_PREFIX + "Du hast bereits ein Konto")
