"""Where Meteolane works, and where visitors would like it to: votes and "tell me when" mails.

Anyone may vote, signed in or not, and may leave an address to be told when an area is
covered. Rules that hold this together:

- **A vote is one per voter and area.** An account votes as ``user:<id>``; a visitor without
  one as ``anon:`` plus a hash of a random token kept in the ``VOTER_COOKIE`` cookie. No IP
  address is stored with a vote. Clearing the cookie would allow another vote, so the IP
  counts in the cache only: ``VOTES_PER_IP_PER_HOUR`` over all areas, and one anonymous vote
  per area and IP a day. Both fail open, like the other per-minute limits: this is a wish
  list, not an election.
- **A recognised browser votes once per area for 30 days**, whatever its cookie or network
  (``claim_device_vote``, keys from ``core.fingerprinting.device_keys``). This stacks on the
  IP limits, never replaces them. A ``low`` browser claims only its key, never its
  fingerprint: look-alike devices (iPhones of one model) must not block each other. An
  unknown one, one without any key, and a suspicious one share ``SUSPICIOUS_VOTES_PER_IP_PER_DAY``
  with their IP (the suspicious one still claims its own key too). The claim is recorded per
  voter, so withdrawing releases exactly what was claimed. IPv6 addresses count by their /56.
- **The ledger is blind** (``COVERAGE_BLIND_LEDGER``). A vote the limits refused is stored
  anyway (``accepted=False``) and shown to its voter as cast, and a withdrawal waits for the
  daily ``settle_votes``, which also publishes the tallies. So no reply says whether a vote
  counted: whoever probes the limits learns it a day later, as the day's change in a tally.
  Only the per-IP flood limit still answers 429.
- **An address is confirmed before anything else is sent to it** (double opt-in): anyone can
  type anyone's address. The confirmation mail goes at most once per ``RESEND_AFTER``, and the
  reply is the same whether the address was new, pending or confirmed, so the form reveals
  nothing. A signed-in account's own verified address needs no confirmation.
- **The covered mail goes once.** Saving a ``CoverageArea`` as covered enqueues
  ``tasks.notify_area_covered``, which mails every confirmed address and deletes its row.
"""

import hashlib
import secrets
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from allauth.account.models import EmailAddress
from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import translation
from django.utils.translation import gettext
from loguru import logger
from redis.exceptions import RedisError

from core import fingerprinting
from core.countries import COUNTRY_NAMES
from core.models import CoverageArea, CoverageSubscription, CoverageVote, User

VOTER_COOKIE = "meteolane_voter"
VOTER_COOKIE_MAX_AGE = 2 * 365 * 24 * 3600
VOTES_PER_IP_PER_HOUR = 30
SUBSCRIBE_PER_IP_PER_HOUR = 10
# Anonymous votes from a browser that could not be recognised, or lied, per IP and day.
SUSPICIOUS_VOTES_PER_IP_PER_DAY = 3
DEVICE_VOTE_TTL = 30 * 24 * 3600
# The last settlement's tallies (``settle_votes``); outlives a missed day.
TALLY_KEY = "coverage:tallies"
TALLY_TTL = 48 * 3600
RESEND_AFTER = timedelta(minutes=10)
# Unconfirmed addresses are dropped after this (``tasks._purge_unconfirmed_coverage``).
COVERAGE_CONFIRM_RETENTION = timedelta(days=7)


def area_name(code: str, language: str, area: CoverageArea | None = None) -> str:
    """The area's name for a mail: the admin's for a region, ICU's for a country."""
    if area is not None and area.name:
        return (area.name_en or area.name) if language == "en" else area.name
    names = COUNTRY_NAMES.get(code)
    if names is None:
        return code
    return names[1] if language == "en" else names[0]


def votable(code: str, area: CoverageArea | None) -> bool:
    """A country by its ISO code, or a region the admin listed; never one already covered."""
    if area is not None:
        return area.status != CoverageArea.Status.COVERED
    return code in COUNTRY_NAMES


def voter_token(raw: str | None) -> str | None:
    """The anonymous voter's token from the cookie, when it looks like one of ours."""
    if raw and 20 <= len(raw) <= 64 and raw.replace("-", "").replace("_", "").isalnum():
        return raw
    return None


def new_voter_token() -> str:
    return secrets.token_urlsafe(24)


def voter_key(user: User | None, token: str | None) -> str | None:
    if user is not None:
        return f"user:{user.pk}"
    if token is None:
        return None
    return "anon:" + hashlib.sha256(token.encode()).hexdigest()[:48]


def _ip_key(ip: str | None) -> str:
    # An IPv6 address counts by its /56 (``ip_floor``); IPv4 keys are what they always were.
    return hashlib.sha256(f"{settings.SECRET_KEY}:{fingerprinting.ip_floor(ip)}".encode()).hexdigest()[:24]


def within_hourly_limit(kind: str, ip: str | None, limit: int) -> bool:
    """A per-IP counter per hour. Fails open: it guards load, not access."""
    key = f"coverage:{kind}:{_ip_key(ip)}:{int(datetime.now(tz=UTC).timestamp() // 3600)}"
    try:
        cache.add(key, 0, 3700)
        return cache.incr(key) <= limit
    except (RedisError, OSError, ValueError) as exc:
        logger.warning(f"Coverage rate limit unavailable: {exc}")
        return True


def _anonymous_vote_key(ip: str | None, code: str) -> str:
    return f"coverage:anonvote:{_ip_key(ip)}:{code}"


def claim_anonymous_vote(ip: str | None, code: str, voter: str | None = None) -> bool:
    """One anonymous vote per area and IP a day, whatever the cookie says. Fails open.

    With ``voter``, the claim is remembered with the voter's other claims, so the daily
    settlement can release it without the address (``release_device_vote``).
    """
    key = _anonymous_vote_key(ip, code)
    try:
        if not cache.add(key, 1, 24 * 3600):
            return False
        if voter is not None:
            claim_key = _device_claim_key(voter, code)
            claim = cache.get(claim_key)
            cache.set(claim_key, {**(claim if isinstance(claim, dict) else {}), "anon": key}, DEVICE_VOTE_TTL)
    except (RedisError, OSError) as exc:
        logger.warning(f"Coverage vote claim unavailable: {exc}")
    return True


def release_anonymous_vote(ip: str | None, code: str) -> None:
    """A withdrawn vote may be cast again from the same place."""
    try:
        cache.delete(_anonymous_vote_key(ip, code))
    except (RedisError, OSError) as exc:
        logger.warning(f"Coverage vote claim unavailable: {exc}")


def _device_vote_key(key: str, code: str) -> str:
    return f"coverage:devvote:{key}:{code}"


def _device_claim_key(voter: str, code: str) -> str:
    return f"coverage:devvote-of:{voter}:{code}"


def claim_device_vote(voter: str, assessment: dict | None, ip: str | None, code: str) -> str | None:
    """One vote per recognised browser and area; a shared daily count for the rest. Fails open.

    Returns None when the vote may go ahead, else why not: ``"device"`` (this browser already
    voted for the area) or ``"limit"`` (the unrecognised browsers of this IP used today's
    votes). Whatever is claimed is remembered under the voter, so ``release_device_vote``
    undoes it after the receipt has long expired.

    Nothing a browser can trigger leaves it freer: a suspicious one keeps its own key and pays
    the IP's shared count on top, and so does one without any key or with a key still too young.
    """
    if not settings.BROWSER_FINGERPRINT_ENABLED:
        return None
    try:
        keys = fingerprinting.device_keys(assessment, ip)
        bucket = None
        # A key younger than BROWSER_KEY_AGE pays the IP's count too: minting keys buys no votes.
        if not fingerprinting.is_trusted(assessment) or not keys or not fingerprinting.is_established(assessment):
            bucket = f"coverage:suspvote:{_ip_key(ip)}:{datetime.now(tz=UTC).date().isoformat()}"
            cache.add(bucket, 0, 25 * 3600)
            if cache.incr(bucket) > SUSPICIOUS_VOTES_PER_IP_PER_DAY:
                cache.decr(bucket)
                return "limit"
        claimed: list[str] = []
        for key in keys:
            if not cache.add(_device_vote_key(key, code), 1, DEVICE_VOTE_TTL):
                cache.delete_many([_device_vote_key(k, code) for k in claimed])
                if bucket is not None:
                    cache.decr(bucket)
                return "device"
            claimed.append(key)
        cache.set(_device_claim_key(voter, code), {"keys": claimed, "bucket": bucket}, DEVICE_VOTE_TTL)
    except (RedisError, OSError, ValueError) as exc:
        logger.warning(f"Coverage device claim unavailable: {exc}")
    return None


def release_device_vote(voter: str, code: str) -> None:
    """Undo ``claim_device_vote`` (and a remembered ``claim_anonymous_vote``) for a withdrawn or refused vote."""
    try:
        claim = cache.get(_device_claim_key(voter, code))
        if not isinstance(claim, dict):
            return
        keys = [_device_vote_key(k, code) for k in claim.get("keys", [])] + [_device_claim_key(voter, code)]
        cache.delete_many(keys + ([claim["anon"]] if claim.get("anon") else []))
        if claim.get("bucket"):
            with suppress(ValueError):  # the day's count already expired
                cache.decr(claim["bucket"])
    except (RedisError, OSError) as exc:
        logger.warning(f"Coverage device claim unavailable: {exc}")


def claim_vote(voter: str, assessment: dict | None, ip: str | None, code: str) -> str | None:
    """Every claim an anonymous vote makes, browser first; None when all of them held.

    Else why not: ``"device"`` or ``"limit"`` (``claim_device_vote``), or ``"address"`` (this IP
    already voted for the area today). A refused step leaves nothing claimed behind.
    """
    refused = claim_device_vote(voter, assessment, ip, code)
    if refused is not None:
        return refused
    if not claim_anonymous_vote(ip, code, voter):
        release_device_vote(voter, code)
        return "address"
    return None


# --- The blind ledger -------------------------------------------------------------------------


def counted_votes():
    """The votes a tally counts: the ones the limits let through and nobody withdrew."""
    return CoverageVote.objects.filter(accepted=True, withdrawn_at__isnull=True)


def _tallies(step: int = 1, before: datetime | None = None) -> dict[str, int]:
    """Counted votes per area, rounded down to ``step``; with ``before``, as they stood then.

    As they stood: cast before it, and not withdrawn before it either, so a withdrawal since then
    changes nothing yet (it would say at once whether that vote had counted).
    """
    if before is None:
        votes = counted_votes()
    else:
        votes = CoverageVote.objects.filter(accepted=True, created_at__lt=before).filter(
            Q(withdrawn_at__isnull=True) | Q(withdrawn_at__gte=before)
        )
    rows = votes.values("area_code").annotate(n=Count("id")).values_list("area_code", "n")
    return {code: n - n % step for code, n in rows if n >= step}


def published_tallies() -> dict[str, int]:
    """Votes per area as the page shows them: the last settlement's, or live without the ledger.

    Should the settled tallies be gone from the cache, the count up to the start of the UTC day
    stands in: a live count would say at once whether a vote counted.
    """
    if not settings.COVERAGE_BLIND_LEDGER:
        return _tallies()
    try:
        settled = cache.get(TALLY_KEY)
    except (RedisError, OSError) as exc:
        logger.warning(f"Coverage tallies unavailable: {exc}")
        settled = None
    if isinstance(settled, dict) and isinstance(settled.get("counts"), dict):
        return settled["counts"]
    today = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return _tallies(settings.COVERAGE_TALLY_STEP, before=today)


def settle_votes(now: datetime | None = None) -> int:
    """The daily settlement, once per UTC day: withdrawals take effect and the tallies publish.

    Until then a vote changes nothing anyone can see, so no reply says whether the limits let
    it count. Returns how many withdrawn votes were removed. With the ledger switched off,
    withdrawals take effect at once; those still pending from before go on every pass.
    """
    if not settings.COVERAGE_BLIND_LEDGER:
        return _remove_withdrawn()
    day = (now or datetime.now(tz=UTC)).date().isoformat()
    try:
        settled = cache.get(TALLY_KEY)
    except (RedisError, OSError) as exc:
        logger.warning(f"Coverage settlement skipped, cache unavailable: {exc}")
        return 0
    if isinstance(settled, dict) and settled.get("day") == day:
        return 0
    removed = _remove_withdrawn()
    try:
        cache.set(TALLY_KEY, {"day": day, "counts": _tallies(settings.COVERAGE_TALLY_STEP)}, TALLY_TTL)
    except (RedisError, OSError) as exc:
        logger.warning(f"Coverage tallies not published: {exc}")
    return removed


def _remove_withdrawn() -> int:
    """Delete withdrawn votes and release their claims; how many went."""
    removed = 0
    withdrawn = CoverageVote.objects.filter(withdrawn_at__isnull=False).values_list("pk", "voter", "area_code")
    for pk, voter, code in withdrawn:
        # Row by row: a vote cast again since it was listed has lost its withdrawn_at and stays.
        deleted, _ = CoverageVote.objects.filter(pk=pk, withdrawn_at__isnull=False).delete()
        if deleted:
            release_device_vote(voter, code)
            removed += 1
    return removed


def _link(action: str, token: str) -> str:
    return f"{settings.FRONTEND_URL.rstrip('/')}/coverage?{action}={token}"


def _owns_verified(user: User | None, email: str) -> bool:
    return (
        user is not None
        and user.email.lower() == email
        and EmailAddress.objects.filter(user=user, email__iexact=email, verified=True).exists()
    )


def subscribe(code: str, email: str, language: str, user: User | None) -> str:
    """Ask to be told when ``code`` is covered. Returns ``"confirmed"`` or ``"pending"``.

    ``"confirmed"`` only for a signed-in account's own verified address; every other case,
    new, pending or already confirmed, answers ``"pending"`` so the form reveals nothing.
    """
    email = email.strip().lower()
    trusted = _owns_verified(user, email)
    now = datetime.now(tz=UTC)
    with transaction.atomic():
        row = CoverageSubscription.objects.select_for_update().filter(area_code=code, email__iexact=email).first()
        if row is None:
            row = CoverageSubscription(area_code=code, email=email, token=secrets.token_urlsafe(32))
        if row.confirmed_at is not None:
            return "confirmed" if trusted else "pending"
        row.language = language
        if trusted:
            row.confirmed_at = now
            _save_new(row)
            return "confirmed"
        if row.confirmation_sent_at is not None and now - row.confirmation_sent_at < RESEND_AFTER:
            return "pending"
        row.confirmation_sent_at = now
        _save_new(row)
    _send_confirmation(row)
    return "pending"


def _save_new(row: CoverageSubscription) -> None:
    try:
        with transaction.atomic():
            row.save()
    except IntegrityError:
        # A parallel request for the same address won; its mail is on the way.
        logger.info(f"Coverage subscription for {row.area_code} already being created")


def _send_confirmation(row: CoverageSubscription) -> None:
    area = CoverageArea.objects.filter(code=row.area_code).first()
    try:
        with translation.override(row.language):
            name = area_name(row.area_code, row.language, area)
            subject = gettext("Bitte bestätige: Nachricht, sobald Meteolane %(area)s abdeckt") % {"area": name}
            body = gettext(
                "Hallo,\n\n"
                "Du möchtest eine E-Mail erhalten, sobald Meteolane %(area)s abdeckt. "
                "Bitte bestätige das mit diesem Link:\n\n"
                "%(url)s\n\n"
                "Warst du das nicht? Dann ignoriere diese E-Mail einfach. Ohne Bestätigung "
                "löschen wir die Adresse nach 7 Tagen.\n\n"
                "Abbestellen kannst du jederzeit hier:\n%(unsubscribe)s\n"
            ) % {"area": name, "url": _link("confirm", row.token), "unsubscribe": _link("unsubscribe", row.token)}
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [row.email])
    except Exception:  # noqa: BLE001 -- the form's reply must not depend on the mail server
        logger.exception("Could not send the coverage confirmation")


def confirm(token: str) -> CoverageSubscription | None:
    row = CoverageSubscription.objects.filter(token=token).first()
    if row is None:
        return None
    if row.confirmed_at is None:
        row.confirmed_at = datetime.now(tz=UTC)
        row.save(update_fields=["confirmed_at"])
    return row


def unsubscribe(token: str) -> None:
    CoverageSubscription.objects.filter(token=token).delete()


def notify_covered(code: str) -> int:
    """Mail every confirmed address that asked about ``code``; each row goes once it is sent.

    Rows are locked and skipped when locked, so two passes never mail one address twice. A row
    whose mail fails stays for the next pass.
    """
    area = CoverageArea.objects.filter(code=code).first()
    if area is None or area.status != CoverageArea.Status.COVERED:
        return 0
    sent = 0
    ids = list(
        CoverageSubscription.objects.filter(area_code=code, confirmed_at__isnull=False).values_list("id", flat=True)
    )
    for row_id in ids:
        with transaction.atomic():
            row = CoverageSubscription.objects.select_for_update(skip_locked=True).filter(id=row_id).first()
            if row is None:
                continue
            try:
                with translation.override(row.language):
                    name = area_name(code, row.language, area)
                    subject = gettext("Meteolane deckt jetzt %(area)s ab") % {"area": name}
                    body = gettext(
                        "Hallo,\n\n"
                        "Du wolltest wissen, wann Meteolane %(area)s abdeckt: ab sofort kannst du dort "
                        "Routen planen und das Wetter entlang der Strecke sehen.\n\n"
                        "%(url)s\n\n"
                        "Das war die einzige Nachricht dazu. Deine Adresse haben wir jetzt gelöscht.\n"
                    ) % {"area": name, "url": settings.FRONTEND_URL.rstrip("/") + "/"}
                send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [row.email])
            except Exception:  # noqa: BLE001 -- one bad address must not stop the others
                logger.exception(f"Could not send the coverage notice for {code}")
                continue
            row.delete()
            sent += 1
    return sent


def purge_unconfirmed() -> int:
    cutoff = datetime.now(tz=UTC) - COVERAGE_CONFIRM_RETENTION
    return CoverageSubscription.objects.filter(confirmed_at__isnull=True, created_at__lt=cutoff).delete()[0]
