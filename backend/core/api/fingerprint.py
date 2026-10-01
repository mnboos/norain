"""Bounded, CSRF-protected browser-recognition endpoints. The rules are in ``core.fingerprinting``."""

import secrets

from django.conf import settings
from django.http import HttpResponse
from django.utils.translation import gettext
from loguru import logger
from ninja import Router
from ninja.errors import HttpError
from ninja.utils import check_csrf
from pydantic import Field

from .. import fingerprinting as fp
from ..auth.lockout import client_ip
from ..schemas import CamelSchema


def fingerprint_auth(request):
    if not settings.BROWSER_FINGERPRINT_ENABLED:
        raise HttpError(404, gettext("Die Browser-Erkennung ist ausgeschaltet."))
    if check_csrf(request):
        raise HttpError(403, gettext("CSRF-Prüfung fehlgeschlagen."))
    # Check before Ninja parses a body. The proxy also bounds request sizes.
    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > 24000 or len(request.body) > 24000:
            raise HttpError(413, gettext("Die Browser-Daten sind zu gross."))
    except ValueError as error:
        raise HttpError(400, gettext("Die Browser-Prüfung ist fehlgeschlagen.")) from error
    # The relay meter's echoes count apart: three per proof would crowd out challenges behind CGNAT.
    scope, limit = ("echo", 180) if request.path.endswith("/echo") else ("ip", 120)
    if not fp.rate_limit(scope, fp.ip_floor(client_ip(request)), limit):
        raise HttpError(429, gettext("Zu viele Anfragen. Bitte versuche es später noch einmal."))
    return True


router = Router(tags=["fingerprint"], auth=fingerprint_auth)


class Challenge(CamelSchema):
    challenge: str
    expires_in: int
    # Leading zero bits the proof-of-work needs; higher while one address mints new keys.
    difficulty: int
    # Whether to run the echo chain (the relay meter is on); the same for every browser.
    echo: bool = False


class EchoIn(CamelSchema):
    challenge: str = Field(max_length=1024)
    step: int = Field(ge=1, le=fp.ECHO_STEPS)
    token: str = Field(default="", max_length=32)


class EchoOut(CamelSchema):
    token: str


class Submission(CamelSchema):
    challenge: str = Field(max_length=1024)
    payload: str = Field(max_length=16000)
    public_key: str = Field(max_length=256)
    signature: str = Field(max_length=128)
    pow: str = Field(max_length=16)


class Receipt(CamelSchema):
    # Nothing else: which checks passed is the server's business, not a forger's hint.
    expires_in: int


@router.post("/challenge", response=Challenge, by_alias=True)
def challenge(request, response: HttpResponse):
    context = fp.read_cookie(request, fp.CONTEXT_COOKIE, fp.RETENTION)
    if not isinstance(context, str):
        context = secrets.token_urlsafe(32)
    if not fp.rate_limit("context", context, 30):
        raise HttpError(429, gettext("Zu viele Anfragen. Bitte versuche es später noch einmal."))
    fp.set_cookie(response, fp.CONTEXT_COOKIE, context, fp.RETENTION)
    bits = fp.pow_bits(client_ip(request))
    fp.count_stat(f"pow:{bits}")
    return {
        "challenge": fp.issue_challenge(context, bits),
        "expiresIn": fp.CHALLENGE_TTL,
        "difficulty": bits,
        "echo": fp.meter_on(),
    }


@router.post("/echo", response=EchoOut, by_alias=True)
def echo(request, echo_in: EchoIn):
    """One round trip of the relay meter; the server times it, the reply says nothing about it."""
    context = fp.read_cookie(request, fp.CONTEXT_COOKIE, fp.RETENTION)
    if not fp.meter_on() or not isinstance(context, str):
        raise HttpError(400, gettext("Die Browser-Prüfung ist fehlgeschlagen."))
    try:
        token = fp.echo(echo_in.challenge, context, echo_in.step, echo_in.token, request.headers)
    except ValueError as error:
        fp.count_stat(f"refused:{getattr(error, 'reason', 'echo')}")
        raise HttpError(400, gettext("Die Browser-Prüfung ist fehlgeschlagen.")) from error
    except fp.CACHE_ERRORS as error:
        raise HttpError(503, gettext("Die Browser-Prüfung ist gerade nicht verfügbar.")) from error
    return {"token": token}


@router.post("/verify", response=Receipt, by_alias=True)
def verify(request, response: HttpResponse, submission: Submission):
    context = fp.read_cookie(request, fp.CONTEXT_COOKIE, fp.RETENTION)
    if not isinstance(context, str):
        raise HttpError(400, gettext("Die Browser-Prüfung ist fehlgeschlagen."))
    try:
        browser_id, evidence = fp.verify_proof(
            submission.challenge,
            context,
            submission.payload,
            submission.public_key,
            submission.signature,
            submission.pow,
        )
    except ValueError as error:
        logger.info(f"Browser proof refused: {error}")
        fp.count_stat(f"refused:{getattr(error, 'reason', 'evidence')}")
        raise HttpError(400, gettext("Die Browser-Prüfung ist fehlgeschlagen.")) from error
    except fp.CACHE_ERRORS as error:
        # The nonce claim is the replay guard: without the cache, no receipt.
        logger.warning(f"Browser proof unavailable: {error}")
        raise HttpError(503, gettext("Die Browser-Prüfung ist gerade nicht verfügbar.")) from error
    ip = client_ip(request)
    first_seen = fp.note_new_browser(browser_id, ip)
    assessment = fp.assess(browser_id, evidence, request.headers, fp.previous_browser(context), first_seen)
    if fp.meter_on():
        assessment.update(fp.path_fields(fp.take_echo(submission.challenge), ip))
    fp.note_fingerprint(assessment["fingerprintId"], browser_id, ip)
    fp.log_assessment(assessment)
    fp.record_stats(assessment)
    if fp.is_class_e(ip):
        # Cloudflare's Pseudo IPv4 ("overwrite") hides every IPv6 prefix from the IP floors.
        fp.count_stat("ip_class_e")
    if not fp.store_receipt(response, context, assessment):
        raise HttpError(503, gettext("Die Browser-Prüfung ist gerade nicht verfügbar."))
    return {"expiresIn": fp.RECEIPT_TTL}
