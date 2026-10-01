"""The coverage page: covered areas, votes (with and without an account) and "tell me when"."""

import re
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from . import coverage
from .models import CoverageArea, CoverageSubscription, CoverageVote, User

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def _token(body: str, action: str) -> str:
    match = re.search(rf"/coverage\?{action}=([\w-]+)", body)
    assert match, body
    return match.group(1)


@override_settings(CACHES=LOCMEM, FRONTEND_URL="https://app.test")
class CoverageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(REMOTE_ADDR="10.0.0.1")

    def _user(self, name="ana", verified=True) -> User:
        user = User.objects.create_user(username=name, email=f"{name}@example.test", password="pw-123456789")
        EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=verified)
        return user

    # -- the list ---------------------------------------------------------------------------

    def test_lists_switzerland_as_covered_to_anyone(self):
        body = self.client.get("/api/coverage").json()
        ch = next(a for a in body["areas"] if a["code"] == "CH")
        self.assertEqual(ch["status"], "covered")
        self.assertNotIn("CH", body["countries"])
        self.assertIn("DE", body["countries"])

    def test_a_region_is_listed_with_its_names(self):
        CoverageArea.objects.create(code="it-32", name="Südtirol", name_en="South Tyrol")
        area = next(a for a in self.client.get("/api/coverage").json()["areas"] if a["code"] == "IT-32")
        self.assertEqual((area["status"], area["name"], area["name_en"]), ("candidate", "Südtirol", "South Tyrol"))

    # -- votes ------------------------------------------------------------------------------

    def _votes(self, code: str) -> tuple[int, bool]:
        """The area's shown count and whether this client voted; an area nobody lists is (0, False)."""
        areas = self.client.get("/api/coverage").json()["areas"]
        area = next((a for a in areas if a["code"] == code), None)
        return (area["votes"], area["voted"]) if area else (0, False)

    def test_an_anonymous_vote_counts_once_and_is_remembered_by_cookie(self):
        reply = self.client.put("/api/coverage/de/vote")
        # Counted at the next settlement, not before.
        self.assertEqual(reply.json(), {"code": "DE", "votes": 0, "voted": True})
        self.assertIn(coverage.VOTER_COOKIE, reply.cookies)
        self.assertTrue(reply.cookies[coverage.VOTER_COOKIE]["httponly"])
        self.assertEqual(self.client.put("/api/coverage/DE/vote").json()["voted"], True)
        self.assertEqual(self._votes("DE"), (0, True))
        coverage.settle_votes()
        self.assertEqual(self._votes("DE"), (1, True))
        self.assertTrue(CoverageVote.objects.get().voter.startswith("anon:"))

    def test_a_new_cookie_from_the_same_address_is_answered_alike_but_never_counts(self):
        counted = self.client.put("/api/coverage/DE/vote")
        other = Client(REMOTE_ADDR="10.0.0.1")
        refused = other.put("/api/coverage/DE/vote")
        self.assertEqual((refused.status_code, refused.json()), (counted.status_code, counted.json()))
        # Its voter still sees the vote as cast; it simply never counts.
        area = next(a for a in other.get("/api/coverage").json()["areas"] if a["code"] == "DE")
        self.assertTrue(area["voted"])
        self.assertEqual(other.put("/api/coverage/AT/vote").status_code, 200)
        Client(REMOTE_ADDR="10.0.0.2").put("/api/coverage/DE/vote")
        coverage.settle_votes()
        self.assertEqual(self._votes("DE")[0], 2)
        self.assertEqual(CoverageVote.objects.filter(accepted=False).count(), 1)

    def test_nothing_anyone_can_see_changes_before_the_settlement(self):
        self.client.put("/api/coverage/DE/vote")
        coverage.settle_votes()
        Client(REMOTE_ADDR="10.0.0.2").put("/api/coverage/DE/vote")
        self.assertEqual(coverage.settle_votes(), 0)  # once per UTC day
        self.assertEqual(self._votes("DE")[0], 1)
        coverage.settle_votes(now=datetime.now(tz=UTC) + timedelta(days=1))
        self.assertEqual(self._votes("DE")[0], 2)

    def test_without_settled_tallies_only_votes_from_before_today_show(self):
        self.client.put("/api/coverage/DE/vote")
        Client(REMOTE_ADDR="10.0.0.2").put("/api/coverage/DE/vote")
        two_days_ago = datetime.now(tz=UTC) - timedelta(days=2)
        CoverageVote.objects.filter(voter__startswith="anon:").update(created_at=two_days_ago)
        Client(REMOTE_ADDR="10.0.0.3").put("/api/coverage/DE/vote")
        cache.delete(coverage.TALLY_KEY)
        self.assertEqual(self._votes("DE")[0], 2)

    def test_without_settled_tallies_a_withdrawal_from_today_changes_nothing_yet(self):
        self.client.put("/api/coverage/DE/vote")
        two_days_ago = datetime.now(tz=UTC) - timedelta(days=2)
        CoverageVote.objects.update(created_at=two_days_ago)
        self.client.delete("/api/coverage/DE/vote")
        cache.delete(coverage.TALLY_KEY)
        self.assertEqual(coverage.published_tallies(), {"DE": 1})

    def test_switching_the_ledger_off_settles_pending_withdrawals_at_once(self):
        self.client.put("/api/coverage/DE/vote")
        self.client.delete("/api/coverage/DE/vote")
        with self.settings(COVERAGE_BLIND_LEDGER=False):
            self.assertEqual(coverage.settle_votes(), 1)
        self.assertFalse(CoverageVote.objects.exists())
        Client(REMOTE_ADDR="10.0.0.1").put("/api/coverage/DE/vote")
        self.assertTrue(CoverageVote.objects.get().accepted, "its address claim was released")

    def test_a_withdrawal_takes_effect_at_the_settlement(self):
        self.client.put("/api/coverage/DE/vote")
        coverage.settle_votes()
        self.assertEqual(self.client.delete("/api/coverage/DE/vote").json(), {"code": "DE", "votes": 1, "voted": False})
        self.assertEqual(self._votes("DE"), (1, False))
        # Cast again before the settlement: the vote simply stands.
        self.client.put("/api/coverage/DE/vote")
        self.client.delete("/api/coverage/DE/vote")
        self.assertEqual(coverage.settle_votes(now=datetime.now(tz=UTC) + timedelta(days=1)), 1)
        self.assertFalse(CoverageVote.objects.exists())
        self.assertEqual(self._votes("DE"), (0, False))

    def test_a_settled_withdrawal_releases_the_address_claim(self):
        self.client.put("/api/coverage/DE/vote")
        self.client.delete("/api/coverage/DE/vote")
        coverage.settle_votes()
        Client(REMOTE_ADDR="10.0.0.1").put("/api/coverage/DE/vote")
        self.assertTrue(CoverageVote.objects.get().accepted)

    def test_tallies_can_be_published_in_steps(self):
        for n in range(7):
            Client(REMOTE_ADDR=f"10.0.1.{n}").put("/api/coverage/DE/vote")
        for n in range(3):
            Client(REMOTE_ADDR=f"10.0.2.{n}").put("/api/coverage/AT/vote")
        with self.settings(COVERAGE_TALLY_STEP=5):
            coverage.settle_votes()
        self.assertEqual(coverage.published_tallies(), {"DE": 5})

    def test_without_the_ledger_votes_count_live_and_refusals_answer_429(self):
        with self.settings(COVERAGE_BLIND_LEDGER=False):
            self.assertEqual(self.client.put("/api/coverage/DE/vote").json()["votes"], 1)
            self.assertEqual(Client(REMOTE_ADDR="10.0.0.1").put("/api/coverage/DE/vote").status_code, 429)
            self.assertEqual(self.client.delete("/api/coverage/DE/vote").json()["votes"], 0)
            self.assertEqual(self.client.put("/api/coverage/DE/vote").json()["votes"], 1)
            self.assertEqual(coverage.settle_votes(), 0)

    def test_an_account_votes_as_itself(self):
        user = self._user()
        self.client.put("/api/coverage/DE/vote")
        self.client.force_login(user)
        # Same address, but an account's vote is its own.
        self.client.put("/api/coverage/DE/vote")
        coverage.settle_votes()
        self.assertEqual(self._votes("DE")[0], 2)
        self.assertTrue(CoverageVote.objects.filter(voter=f"user:{user.pk}").exists())

    def test_a_covered_or_unknown_area_takes_no_votes(self):
        self.assertEqual(self.client.put("/api/coverage/CH/vote").status_code, 409)
        self.assertEqual(self.client.put("/api/coverage/ZZ/vote").status_code, 404)
        self.assertEqual(self.client.put("/api/coverage/IT-32/vote").status_code, 404)
        CoverageArea.objects.create(code="IT-32", name="Südtirol")
        self.assertEqual(self.client.put("/api/coverage/IT-32/vote").status_code, 200)
        self.assertFalse(CoverageVote.objects.filter(area_code__in=["CH", "ZZ"]).exists())

    def test_votes_per_address_are_limited(self):
        with patch.object(coverage, "VOTES_PER_IP_PER_HOUR", 2):
            codes = ["AT", "DE", "FR"]
            replies = [Client(REMOTE_ADDR="10.0.0.9").put(f"/api/coverage/{c}/vote").status_code for c in codes]
        self.assertEqual(replies, [200, 200, 429])

    # -- "tell me when" ---------------------------------------------------------------------

    def test_an_address_gets_a_confirmation_first_and_only_once_in_a_while(self):
        reply = self.client.post(
            "/api/coverage/DE/notify", {"email": "Rider@Example.test"}, content_type="application/json"
        )
        self.assertEqual(reply.json(), {"status": "pending"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["rider@example.test"])
        self.assertIn("Deutschland", mail.outbox[0].subject)
        row = CoverageSubscription.objects.get()
        self.assertIsNone(row.confirmed_at)
        self.assertEqual(_token(mail.outbox[0].body, "confirm"), row.token)
        self.assertEqual(_token(mail.outbox[0].body, "unsubscribe"), row.token)

        again = self.client.post(
            "/api/coverage/DE/notify", {"email": "rider@example.test"}, content_type="application/json"
        )
        self.assertEqual(again.json(), {"status": "pending"})
        self.assertEqual(len(mail.outbox), 1)

        confirmed = self.client.post(
            "/api/coverage/notify/confirm", {"token": row.token}, content_type="application/json"
        )
        self.assertEqual(confirmed.json(), {"code": "DE"})
        row.refresh_from_db()
        self.assertIsNotNone(row.confirmed_at)
        # A confirmed address answers like any other and gets no further mail.
        known = self.client.post(
            "/api/coverage/DE/notify", {"email": "rider@example.test"}, content_type="application/json"
        )
        self.assertEqual((known.json(), len(mail.outbox)), ({"status": "pending"}, 1))

    def test_the_mail_follows_the_page_language(self):
        self.client.post(
            "/api/coverage/DE/notify",
            {"email": "rider@example.test"},
            content_type="application/json",
            headers={"accept-language": "en"},
        )
        self.assertEqual(mail.outbox[0].subject, "Please confirm: a message once Meteolane covers Germany")
        self.assertEqual(CoverageSubscription.objects.get().language, "en")

    def test_an_accounts_own_verified_address_needs_no_confirmation(self):
        user = self._user()
        self.client.force_login(user)
        reply = self.client.post("/api/coverage/DE/notify", {"email": user.email}, content_type="application/json")
        self.assertEqual(reply.json(), {"status": "confirmed"})
        self.assertEqual(len(mail.outbox), 0)
        self.assertIsNotNone(CoverageSubscription.objects.get().confirmed_at)

    def test_someone_elses_address_still_needs_confirmation(self):
        self.client.force_login(self._user())
        reply = self.client.post(
            "/api/coverage/DE/notify", {"email": "b@example.test"}, content_type="application/json"
        )
        self.assertEqual((reply.json(), len(mail.outbox)), ({"status": "pending"}, 1))

    def test_bad_input_is_refused(self):
        bad = self.client.post("/api/coverage/DE/notify", {"email": "nope"}, content_type="application/json")
        self.assertEqual(bad.status_code, 422)
        covered = self.client.post(
            "/api/coverage/CH/notify", {"email": "a@example.test"}, content_type="application/json"
        )
        self.assertEqual(covered.status_code, 409)
        unknown = self.client.post("/api/coverage/notify/confirm", {"token": "x" * 32}, content_type="application/json")
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(len(mail.outbox), 0)

    def test_unsubscribe_deletes_the_address_and_says_nothing(self):
        self.client.post("/api/coverage/DE/notify", {"email": "a@example.test"}, content_type="application/json")
        token = CoverageSubscription.objects.get().token
        for _ in range(2):
            reply = self.client.post(
                "/api/coverage/notify/unsubscribe", {"token": token}, content_type="application/json"
            )
            self.assertEqual(reply.status_code, 204)
        self.assertFalse(CoverageSubscription.objects.exists())

    # -- covered ----------------------------------------------------------------------------

    def _subscription(self, email, *, confirmed=True, language="de"):
        return CoverageSubscription.objects.create(
            area_code="DE",
            email=email,
            language=language,
            token=f"token-{email}-0123456789",
            confirmed_at=datetime.now(tz=UTC) if confirmed else None,
        )

    def test_saving_an_area_as_covered_enqueues_the_mails(self):
        area = CoverageArea.objects.create(code="DE", status=CoverageArea.Status.PLANNED)
        with (
            patch("core.signals.notify_area_covered") as task,
            self.captureOnCommitCallbacks(execute=True),
        ):
            area.save()
            task.enqueue.assert_not_called()
            area.status = CoverageArea.Status.COVERED
            area.save()
        task.enqueue.assert_called_once_with("DE")
        area.refresh_from_db()
        self.assertIsNotNone(area.covered_since)

    def test_the_covered_mail_goes_once_to_confirmed_addresses_only(self):
        self._subscription("a@example.test")
        self._subscription("b@example.test", language="en")
        self._subscription("pending@example.test", confirmed=False)
        self.assertEqual(coverage.notify_covered("DE"), 0)  # not covered yet
        with patch("core.signals.notify_area_covered"):
            CoverageArea.objects.create(code="DE", status=CoverageArea.Status.COVERED)

        self.assertEqual(coverage.notify_covered("DE"), 2)
        self.assertEqual(coverage.notify_covered("DE"), 0)
        subjects = {m.to[0]: m.subject for m in mail.outbox}
        self.assertEqual(set(subjects), {"a@example.test", "b@example.test"})
        self.assertIn("Deutschland", subjects["a@example.test"])
        self.assertIn("Germany", subjects["b@example.test"])
        self.assertEqual(list(CoverageSubscription.objects.values_list("email", flat=True)), ["pending@example.test"])

    def test_unconfirmed_addresses_are_purged_after_a_week(self):
        old = self._subscription("old@example.test", confirmed=False)
        self._subscription("new@example.test", confirmed=False)
        self._subscription("kept@example.test")
        CoverageSubscription.objects.filter(id=old.id).update(created_at=datetime.now(tz=UTC) - timedelta(days=8))
        CoverageSubscription.objects.filter(email="kept@example.test").update(
            created_at=datetime.now(tz=UTC) - timedelta(days=30)
        )
        self.assertEqual(coverage.purge_unconfirmed(), 1)
        self.assertEqual(
            set(CoverageSubscription.objects.values_list("email", flat=True)), {"new@example.test", "kept@example.test"}
        )
