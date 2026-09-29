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
  unknown or suspicious one shares ``SUSPICIOUS_VOTES_PER_IP_PER_DAY`` with its IP. The claim
  is recorded per voter, so withdrawing releases exactly what was claimed.
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
from django.utils import translation
from django.utils.translation import gettext
from loguru import logger
from redis.exceptions import RedisError

from core import fingerprinting
from core.countries import COUNTRY_NAMES
from core.models import CoverageArea, CoverageSubscription, User

VOTER_COOKIE = "meteolane_voter"
VOTER_COOKIE_MAX_AGE = 2 * 365 * 24 * 3600
VOTES_PER_IP_PER_HOUR = 30
SUBSCRIBE_PER_IP_PER_HOUR = 10
# Anonymous votes from a browser that could not be recognised, or lied, per IP and day.
SUSPICIOUS_VOTES_PER_IP_PER_DAY = 3
DEVICE_VOTE_TTL = 30 * 24 * 3600
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
    return hashlib.sha256(f"{settings.SECRET_KEY}:{ip or '-'}".encode()).hexdigest()[:24]


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


def claim_anonymous_vote(ip: str | None, code: str) -> bool:
    """One anonymous vote per area and IP a day, whatever the cookie says. Fails open."""
    try:
        return bool(cache.add(_anonymous_vote_key(ip, code), 1, 24 * 3600))
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
    """
    if not settings.BROWSER_FINGERPRINT_ENABLED:
        return None
    try:
        if not fingerprinting.is_trusted(assessment):
            bucket = f"coverage:suspvote:{_ip_key(ip)}:{datetime.now(tz=UTC).date().isoformat()}"
            cache.add(bucket, 0, 25 * 3600)
            if cache.incr(bucket) > SUSPICIOUS_VOTES_PER_IP_PER_DAY:
                cache.decr(bucket)
                return "limit"
            cache.set(_device_claim_key(voter, code), {"keys": [], "bucket": bucket}, DEVICE_VOTE_TTL)
            return None
        claimed: list[str] = []
        for key in fingerprinting.device_keys(assessment):
            if not cache.add(_device_vote_key(key, code), 1, DEVICE_VOTE_TTL):
                cache.delete_many([_device_vote_key(k, code) for k in claimed])
                return "device"
            claimed.append(key)
        cache.set(_device_claim_key(voter, code), {"keys": claimed, "bucket": None}, DEVICE_VOTE_TTL)
    except (RedisError, OSError, ValueError) as exc:
        logger.warning(f"Coverage device claim unavailable: {exc}")
    return None


def release_device_vote(voter: str, code: str) -> None:
    """Undo ``claim_device_vote`` for a withdrawn or refused vote."""
    try:
        claim = cache.get(_device_claim_key(voter, code))
        if not isinstance(claim, dict):
            return
        cache.delete_many([_device_vote_key(k, code) for k in claim.get("keys", [])] + [_device_claim_key(voter, code)])
        if claim.get("bucket"):
            with suppress(ValueError):  # the day's count already expired
                cache.decr(claim["bucket"])
    except (RedisError, OSError) as exc:
        logger.warning(f"Coverage device claim unavailable: {exc}")


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
