"""The public coverage page: where Meteolane works, votes for where it should, "tell me when".

Open to anyone (``optional_session_auth``, which still checks CSRF). The rules are in
``core.coverage``.
"""

from collections import Counter
from contextlib import suppress
from datetime import date
from typing import Literal

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.utils.translation import get_language, gettext
from ninja import Router
from ninja.errors import HttpError
from pydantic import Field, field_validator

from .. import coverage, fingerprinting
from ..auth.backend import optional_session_auth
from ..auth.lockout import client_ip
from ..countries import COUNTRY_NAMES
from ..models import CoverageArea, CoverageVote, User
from ..schemas import CamelSchema

router = Router(auth=optional_session_auth, tags=["Coverage"])


class CoverageAreaOut(CamelSchema):
    code: str
    # Only for a region; the SPA names a country from its code, in the reader's language.
    name: str | None = None
    name_en: str | None = None
    # None: nobody listed it, but somebody voted for it.
    status: Literal["covered", "planned", "candidate"] | None = None
    note: str | None = None
    note_en: str | None = None
    covered_since: date | None = None
    votes: int = 0
    voted: bool = False


class CoverageOut(CamelSchema):
    areas: list[CoverageAreaOut]
    # Every country that can be voted for (not covered yet), as ISO codes.
    countries: list[str]


class VoteOut(CamelSchema):
    code: str
    votes: int
    voted: bool


class SubscribeIn(CamelSchema):
    email: str = Field(max_length=254)

    @field_validator("email")
    @classmethod
    def check_email(cls, value: str) -> str:
        value = value.strip()
        try:
            validate_email(value)
        except ValidationError:
            raise ValueError(gettext("Bitte gib eine gültige E-Mail-Adresse ein.")) from None
        return value


class SubscribeOut(CamelSchema):
    # "pending": a confirmation mail is on its way (or was, recently). Always the answer for a
    # visitor, so the form never says whether an address was known.
    status: Literal["pending", "confirmed"]


class TokenIn(CamelSchema):
    token: str = Field(min_length=20, max_length=64)


class ConfirmOut(CamelSchema):
    code: str


def _viewer(request: HttpRequest) -> User | None:
    user = getattr(request, "auth", None)
    return user if isinstance(user, User) and user.is_authenticated else None


def _voter(request: HttpRequest) -> str | None:
    return coverage.voter_key(_viewer(request), coverage.voter_token(request.COOKIES.get(coverage.VOTER_COOKIE)))


def _normalize(code: str) -> str:
    code = code.strip().upper()
    if not 2 <= len(code) <= 6:
        raise HttpError(404, gettext("Unbekanntes Gebiet."))
    return code


async def _votable_area(code: str) -> CoverageArea | None:
    area = await CoverageArea.objects.filter(code=code).afirst()
    if area is not None and area.status == CoverageArea.Status.COVERED:
        raise HttpError(409, gettext("Dieses Gebiet deckt Meteolane schon ab."))
    if not coverage.votable(code, area):
        raise HttpError(404, gettext("Unbekanntes Gebiet."))
    return area


def _area_out(area: CoverageArea | None, code: str, votes: int, voted: bool) -> CoverageAreaOut:
    if area is None:
        return CoverageAreaOut(code=code, votes=votes, voted=voted)
    return CoverageAreaOut(
        code=area.code,
        name=area.name or None,
        name_en=area.name_en or None,
        status=area.status,
        note=area.note or None,
        note_en=area.note_en or None,
        covered_since=area.covered_since,
        votes=votes,
        voted=voted,
    )


@router.get("/coverage", response=CoverageOut)
async def get_coverage(request: HttpRequest):
    """Covered and planned areas, every area with votes, and the countries one may vote for."""
    areas = {a.code: a async for a in CoverageArea.objects.all()}
    counts = Counter({row["area_code"]: row["n"] async for row in _vote_counts()})
    voter = _voter(request)
    mine = (
        {c async for c in CoverageVote.objects.filter(voter=voter).values_list("area_code", flat=True)}
        if voter
        else set()
    )
    codes = set(areas) | {c for c in counts if c in COUNTRY_NAMES}
    covered = {c for c, a in areas.items() if a.status == CoverageArea.Status.COVERED}
    return CoverageOut(
        areas=[_area_out(areas.get(c), c, counts[c], c in mine) for c in sorted(codes)],
        countries=sorted(c for c in COUNTRY_NAMES if c not in covered),
    )


def _vote_counts():
    return CoverageVote.objects.values("area_code").annotate(n=Count("id")).values("area_code", "n")


def _set_voter_cookie(response: HttpResponse, token: str) -> None:
    response.set_cookie(
        coverage.VOTER_COOKIE,
        token,
        max_age=coverage.VOTER_COOKIE_MAX_AGE,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite="Lax",
    )


async def _vote_out(code: str, voted: bool) -> VoteOut:
    return VoteOut(code=code, votes=await CoverageVote.objects.filter(area_code=code).acount(), voted=voted)


@router.put("/coverage/{code}/vote", response=VoteOut)
async def vote(request: HttpRequest, response: HttpResponse, code: str):
    """Vote for an area Meteolane does not cover yet; voting twice changes nothing."""
    code = _normalize(code)
    await _votable_area(code)
    user = _viewer(request)
    token = coverage.voter_token(request.COOKIES.get(coverage.VOTER_COOKIE))
    if user is None and token is None:
        token = coverage.new_voter_token()
        _set_voter_cookie(response, token)
    voter = coverage.voter_key(user, token)
    if await CoverageVote.objects.filter(area_code=code, voter=voter).aexists():
        return await _vote_out(code, voted=True)
    ip = client_ip(request)
    if not coverage.within_hourly_limit("vote", ip, coverage.VOTES_PER_IP_PER_HOUR):
        raise HttpError(429, gettext("Zu viele Stimmen. Bitte versuche es später noch einmal."))
    if user is None:
        # The browser's claim first: a refused one leaves nothing behind, and a refused IP
        # claim below releases it again.
        refused = coverage.claim_device_vote(voter, fingerprinting.get_browser_assessment(request), ip, code)
        if refused == "device":
            raise HttpError(
                429,
                gettext("Mit diesem Browser wurde schon für dieses Gebiet gestimmt. Melde dich an, um mitzustimmen."),
            )
        if refused == "limit":
            raise HttpError(
                429,
                gettext("Von hier aus wurden heute schon viele Stimmen abgegeben. Melde dich an, um mitzustimmen."),
            )
        if not coverage.claim_anonymous_vote(ip, code):
            coverage.release_device_vote(voter, code)
            raise HttpError(
                429,
                gettext("Von hier aus wurde heute schon für dieses Gebiet gestimmt. Melde dich an, um mitzustimmen."),
            )
    with suppress(IntegrityError):  # a parallel request cast the same vote
        await CoverageVote.objects.acreate(area_code=code, voter=voter)
    return await _vote_out(code, voted=True)


@router.delete("/coverage/{code}/vote", response=VoteOut)
async def withdraw_vote(request: HttpRequest, code: str):
    code = _normalize(code)
    voter = _voter(request)
    if voter is not None:
        deleted, _ = await CoverageVote.objects.filter(area_code=code, voter=voter).adelete()
        if deleted and _viewer(request) is None:
            coverage.release_anonymous_vote(client_ip(request), code)
            coverage.release_device_vote(voter, code)
    return await _vote_out(code, voted=False)


@router.post("/coverage/{code}/notify", response=SubscribeOut)
async def subscribe(request: HttpRequest, code: str, data: SubscribeIn):
    """Leave an address to be told when the area is covered. It is confirmed by mail first."""
    code = _normalize(code)
    await _votable_area(code)
    if not coverage.within_hourly_limit("notify", client_ip(request), coverage.SUBSCRIBE_PER_IP_PER_HOUR):
        raise HttpError(429, gettext("Zu viele Anfragen. Bitte versuche es später noch einmal."))
    language = get_language() if get_language() in dict(settings.LANGUAGES) else settings.LANGUAGE_CODE
    status = await sync_to_async(coverage.subscribe)(code, data.email, language, _viewer(request))
    return SubscribeOut(status=status)


@router.post("/coverage/notify/confirm", response=ConfirmOut)
async def confirm_subscription(request: HttpRequest, data: TokenIn):
    row = await sync_to_async(coverage.confirm)(data.token)
    if row is None:
        raise HttpError(404, gettext("Dieser Link ist abgelaufen oder wurde schon abbestellt."))
    return ConfirmOut(code=row.area_code)


@router.post("/coverage/notify/unsubscribe", response={204: None})
async def unsubscribe(request: HttpRequest, data: TokenIn):
    """Always 204: whether the link was still valid is nobody's business but the mailbox's."""
    await sync_to_async(coverage.unsubscribe)(data.token)
    return 204, None
