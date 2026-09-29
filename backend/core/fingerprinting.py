"""First-party browser recognition. Signals are untrusted evidence, never authentication.

Rules that hold this together (CLAUDE.md, "Browser recognition"):

- **The server is the arbiter.** Every probe value is written by the client and can be
  invented. What the server can check is cost (a proof-of-work per challenge), consistency
  (the values against each other, against a worker and a fresh iframe, and against the HTTP
  headers it sees itself) and replay (single-use challenges).
- **Nothing diagnostic goes back.** ``verify`` answers with a lifetime only. The assessment
  stays in the cache behind a random receipt id; the cookie carries only that id.
- **Tiers, not verdicts.** ``suspicious``: something lied. ``low``: honest but not distinctive
  (iOS, canvas noise, software rendering, missing probes), so it must never deduplicate on
  its fingerprint. ``high``: distinctive and consistent.
- **The stable set excludes what changes by itself** (zoom, a second monitor, travel):
  ``display`` and ``locale`` count towards similarity only, never towards ``fingerprintId``.
- Raw probe values are never logged or stored; the cache holds keyed hashes only.
"""

import base64
import binascii
import hashlib
import ipaddress
import json
import re
import secrets
import time
from collections import Counter
from datetime import UTC, datetime
from typing import Literal

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.utils.crypto import constant_time_compare, salted_hmac
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from redis.exceptions import RedisError

CHALLENGE_TTL = 120
RECEIPT_TTL = 900
RETENTION = 30 * 24 * 3600
CONTEXT_COOKIE = "ml_browser_context"
RECEIPT_COOKIE = "ml_browser_receipt"
SALT = "meteolane.browser.v1"
EVIDENCE_VERSION = 2
WEIGHTS = {
    "canvas": 3,
    "audio": 2,
    "graphics": 3,
    "fonts": 2,
    "hardware": 2,
    "display": 1,
    "locale": 1,
    "math": 1,
    "media": 1,
}
# The parts that stay put while the device does. Zoom, monitors and time zones do not.
STABLE = ("canvas", "audio", "graphics", "fonts", "hardware", "math", "media")
# A fingerprint is only distinctive with all three renderers; without them many devices match.
DISTINCTIVE = ("canvas", "audio", "graphics")
CHECKS = ("navigator", "worker", "iframe", "integrity", "canvasIntegrity", "engine", "automation")
SIGNALS = {*WEIGHTS, *CHECKS}

# The proof-of-work: leading zero bits of SHA-256(seed ":" n). Measured with the JS solver at
# ~1.25 M hashes/s on a laptop, a phone ~5x slower: 16 bits ~0.05 s / ~0.3 s, 20 bits ~0.8 s /
# ~4 s. The cap keeps a phone behind a busy shared IP within the SPA's wait (WAIT_MS).
POW_MAX_BITS = 20
# New browser keys per IP and hour before each further step (+2 bits) makes the next proof dearer.
CHURN_FREE = 10
CHURN_STEP = 10
# ASCII digits only: Python's \d would also take other scripts' digits.
POW_NONCE = re.compile(r"[0-9]{1,16}")

Tier = Literal["high", "low", "suspicious"]
# Any of these means a value was forged or the browser is driven by a program.
SUSPICIOUS = frozenset(
    {
        "user_agent_mismatch",
        "worker_mismatch",
        "iframe_mismatch",
        "native_tampered",
        "engine_mismatch",
        "client_hints_mismatch",
        "automation",
        "invalid_navigator",
        "invalid_worker",
        "invalid_iframe",
        "invalid_integrity",
    }
)
# Honest, but shared by many devices or missing the parts that make a browser distinctive.
LOW = frozenset(
    {
        "canvas_noise",
        "iframe_canvas_mismatch",
        "software_renderer",
        "ios",
        "client_hints_missing",
        "ephemeral_key",
        "low_signal_coverage",
        "checks_unavailable",
    }
)
# The browser randomises its renderers on purpose or cannot keep its key: Safari's private tabs
# (each one its own storage, noise salted per tab), Brave, Firefox's resistFingerprinting.
PROTECTED = frozenset({"canvas_noise", "iframe_canvas_mismatch", "audio_unstable", "ephemeral_key"})
# What such a browser's noise leaves alone. Shared by every device of one model and browser version.
COARSE = ("graphics", "fonts", "hardware", "math", "media")
CACHE_ERRORS = (RedisError, OSError)


class Probe(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["ok", "unavailable", "timeout", "unstable"]
    value: str | None = Field(default=None, max_length=4096)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[2]
    persistent: bool
    signals: dict[str, Probe]


def keyed_id(purpose, value):
    return salted_hmac(f"{SALT}.{purpose}", value, algorithm="sha256").hexdigest()


def read_cookie(request, name, max_age):
    try:
        return signing.loads(request.COOKIES.get(name, ""), salt=f"{SALT}.{name}", max_age=max_age)
    except signing.BadSignature:
        return None


def set_cookie(response, name, value, max_age):
    response.set_cookie(
        name,
        signing.dumps(value, salt=f"{SALT}.{name}"),
        max_age=max_age,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite="Lax",
        path="/api/",
    )
    response["Cache-Control"] = "no-store"


def rate_limit(scope, identifier, limit):
    """A fixed window per minute, shared by every backend process. Fails open: it guards load."""
    window = int(time.time()) // 60
    key = f"browser:rate:{scope}:{keyed_id('rate', identifier)}:{window}"
    try:
        if cache.add(key, 1, timeout=120):
            return True
        return cache.incr(key) <= limit
    except ValueError:
        return False
    except CACHE_ERRORS as exc:
        logger.warning(f"Browser recognition rate limit unavailable: {exc}")
        return True


# --- Proof-of-work ---------------------------------------------------------------------------


def _churn_key(ip):
    return f"browser:churn:{keyed_id('churn', ip or '-')}:{int(time.time()) // 3600}"


def pow_bits(ip):
    """The challenge's difficulty: the configured base, dearer while one IP mints new keys."""
    base = settings.BROWSER_POW_BITS
    try:
        churn = cache.get(_churn_key(ip)) or 0
    except CACHE_ERRORS:
        churn = 0
    if churn < CHURN_FREE:
        return min(base, POW_MAX_BITS)
    return min(base + 2 * (1 + (churn - CHURN_FREE) // CHURN_STEP), POW_MAX_BITS)


def note_new_browser(browser_id, ip):
    """Count a key this server has not seen before against the IP's hourly churn."""
    try:
        if not cache.add(f"browser:seen:{browser_id}", 1, timeout=RETENTION):
            return
        key = _churn_key(ip)
        cache.add(key, 0, timeout=3700)
        cache.incr(key)
    except (ValueError, *CACHE_ERRORS) as exc:
        logger.warning(f"Browser churn counter unavailable: {exc}")


def pow_seed(challenge, payload):
    """One 64-character block: the solver hashes it once and then only the nonce block."""
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return hashlib.sha256(f"{challenge}\n{digest}".encode()).hexdigest()


def pow_valid(seed, nonce, bits):
    if not POW_NONCE.fullmatch(nonce):
        return False
    if bits <= 0:
        return True
    digest = hashlib.sha256(f"{seed}:{nonce}".encode()).digest()
    return int.from_bytes(digest) >> (256 - bits) == 0


def issue_challenge(context, bits):
    return signing.dumps(
        {"nonce": secrets.token_urlsafe(32), "context": keyed_id("context", context), "bits": bits}, salt=SALT
    )


# --- Proof ------------------------------------------------------------------------------------


def parse_evidence(payload):
    try:
        evidence = Evidence.model_validate_json(payload)
    except ValidationError as error:
        raise ValueError("Invalid browser evidence") from error
    if set(evidence.signals) != SIGNALS or any(
        (probe.status == "ok") != (probe.value is not None) for probe in evidence.signals.values()
    ):
        raise ValueError("Invalid browser probes")
    return evidence


def verify_proof(challenge, context, payload, public_key, signature, nonce):
    try:
        issued = signing.loads(challenge, salt=SALT, max_age=CHALLENGE_TTL)
    except signing.BadSignature as error:
        raise ValueError("Invalid or expired challenge") from error
    if not constant_time_compare(issued["context"], keyed_id("context", context)):
        raise ValueError("Challenge belongs to another browser context")
    if not pow_valid(pow_seed(challenge, payload), nonce, issued["bits"]):
        raise ValueError("Invalid proof-of-work")
    evidence = parse_evidence(payload)
    try:
        key_bytes = base64.b64decode(public_key, validate=True)
        key = serialization.load_der_public_key(key_bytes)
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
            raise TypeError("Only P-256 is supported")
        raw = base64.b64decode(signature, validate=True)
        if len(raw) != 64:
            raise ValueError("Invalid signature length")
        der = encode_dss_signature(int.from_bytes(raw[:32]), int.from_bytes(raw[32:]))
        key.verify(der, f"{challenge}\n{payload}\n{nonce}".encode(), ec.ECDSA(hashes.SHA256()))
    except (ValueError, TypeError, InvalidSignature, UnsupportedAlgorithm, binascii.Error) as error:
        raise ValueError("Invalid browser proof") from error
    # Only one valid submission wins, including across workers. Invalid proofs don't burn the nonce.
    if not cache.add(f"browser:used:{issued['nonce']}", True, timeout=CHALLENGE_TTL + 10):
        raise ValueError("Challenge already used")
    canonical_key = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return keyed_id("key", canonical_key), evidence


# --- Consistency ------------------------------------------------------------------------------


def _parsed(probe):
    """A probe's JSON value, or None when the probe failed or its value is not JSON."""
    if probe.status != "ok":
        return None
    try:
        return json.loads(probe.value)
    except ValueError, RecursionError:
        return None


def user_agent_profile(user_agent):
    """Engine, Chromium major version, platform and mobile flag as the UA string claims them."""
    ua = user_agent or ""
    ios = bool(re.search(r"iPhone|iPad|iPod", ua))
    if ios:
        engine = "webkit"
    elif "Firefox/" in ua and "Gecko/" in ua:
        engine = "gecko"
    elif re.search(r"Chrom(e|ium)/\d+", ua):
        engine = "blink"
    elif "Safari/" in ua and "Version/" in ua:
        engine = "webkit"
    else:
        engine = None
    major = re.search(r"Chrom(?:e|ium)/(\d+)", ua)
    if "Android" in ua:
        platform = "Android"
    elif "CrOS" in ua:
        platform = "Chrome OS"
    elif "Windows" in ua:
        platform = "Windows"
    elif "Macintosh" in ua:
        platform = "macOS"
    elif "Linux" in ua:
        platform = "Linux"
    else:
        platform = None
    return {
        "engine": engine,
        "ios": ios,
        "chromium": int(major.group(1)) if engine == "blink" and major else None,
        "platform": platform,
        "mobile": "Mobile" in ua,
        "headless": "HeadlessChrome" in ua,
    }


def js_engine(values):
    """The JavaScript engine by its own error messages, which a UA string cannot change."""
    if not isinstance(values, list) or len(values) != 4 or not all(isinstance(v, str) for v in values):
        return None
    array_length, to_fixed, repeat, stack = values
    votes = Counter()
    if array_length == "Invalid array length":
        votes["blink"] += 1
    elif array_length == "invalid array length":
        votes["gecko"] += 1
    elif "positive integer of safe magnitude" in array_length or "small enough positive integer" in array_length:
        votes["webkit"] += 1
    if "digits argument" in to_fixed:
        votes["blink"] += 1
    elif "precision" in to_fixed and "out of range" in to_fixed:
        votes["gecko"] += 1
    elif "argument must be between" in to_fixed:
        votes["webkit"] += 1
    if repeat.startswith("Invalid count value"):
        votes["blink"] += 1
    elif "repeat count" in repeat:
        votes["gecko"] += 1
    elif "String.prototype.repeat" in repeat:
        votes["webkit"] += 1
    if stack == "v8":
        votes["blink"] += 1
    # Two agreeing tells and none against: anything else is an engine this code does not know.
    if len(votes) == 1:
        engine, count = votes.most_common(1)[0]
        return engine if count >= 2 else None
    return None


def _brands(header):
    """``"Chromium";v="130", "Not?A_Brand";v="99"`` -> {"Chromium": "130", ...}."""
    return dict(re.findall(r'"([^"]+)"\s*;\s*v="([^"]*)"', header or ""))


def client_hint_flags(profile, headers):
    brands = headers.get("sec-ch-ua")
    platform = (headers.get("sec-ch-ua-platform") or "").strip('"')
    mobile = headers.get("sec-ch-ua-mobile")
    if profile["engine"] != "blink" or profile["ios"]:
        # Gecko and WebKit send no client hints; a request that has them is not what its UA says.
        return ["client_hints_mismatch"] if brands and profile["engine"] is not None else []
    if not brands:
        # Hardened Chromium builds strip them. A non-Chromium engine is caught by engine_mismatch.
        return ["client_hints_missing"]
    flags = []
    chromium = _brands(brands).get("Chromium")
    if chromium and profile["chromium"] is not None and chromium.split(".")[0] != str(profile["chromium"]):
        flags.append("client_hints_mismatch")
    # "Request desktop site" on Android sends a Linux UA with the Android hint.
    desktop_mode = platform == "Android" and profile["platform"] == "Linux"
    if platform and profile["platform"] and platform != profile["platform"] and not desktop_mode:
        flags.append("client_hints_mismatch")
    if mobile in {"?0", "?1"} and (mobile == "?1") != profile["mobile"] and not desktop_mode:
        flags.append("client_hints_mismatch")
    return sorted(set(flags))


def consistency_flags(signals, headers, profile):
    """Every lie or degradation the probes and headers show, as indicator names."""
    flags = []
    user_agent = headers.get("user-agent", "")
    navigator = _parsed(signals["navigator"])
    if signals["navigator"].status == "ok" and not (isinstance(navigator, list) and len(navigator) == 4):
        flags.append("invalid_navigator")
        navigator = None
    if navigator is not None and navigator[0] != user_agent:
        flags.append("user_agent_mismatch")
    worker = _parsed(signals["worker"])
    if signals["worker"].status == "ok" and not (isinstance(worker, list) and len(worker) == 4):
        flags.append("invalid_worker")
    # Worker locale can follow OS settings while a page has its own; UA, CPUs and platform can't.
    elif (
        navigator is not None
        and worker is not None
        and [worker[0], worker[1], worker[3]] != [navigator[0], navigator[1], navigator[3]]
    ):
        flags.append("worker_mismatch")
    flags += _iframe_flags(signals, navigator)
    tampered = _parsed(signals["integrity"])
    if signals["integrity"].status == "ok" and not isinstance(tampered, list):
        flags.append("invalid_integrity")
    elif tampered:
        flags.append("native_tampered")
    if signals["canvasIntegrity"].status == "ok" and signals["canvasIntegrity"].value != "clean":
        flags.append("canvas_noise")
    engine = js_engine(_parsed(signals["engine"]))
    if engine is not None and profile["engine"] is not None and engine != profile["engine"]:
        flags.append("engine_mismatch")
    automation = _parsed(signals["automation"])
    if isinstance(automation, dict):
        # Every desktop Chromium has window.chrome; a driven one may not.
        desktop_blink = profile["engine"] == "blink" and profile["platform"] not in {"Android", None}
        driven = automation.get("webdriver") is True or automation.get("cdc") is True
        if driven or (desktop_blink and automation.get("chrome") is False):
            flags.append("automation")
    if profile["headless"]:
        flags.append("automation")
    graphics = _parsed(signals["graphics"])
    renderer = str(graphics.get("renderer", "")) if isinstance(graphics, dict) else ""
    if re.search(r"SwiftShader|llvmpipe|softpipe|Basic Render Driver", renderer, re.IGNORECASE):
        flags.append("software_renderer")
    hardware = _parsed(signals["hardware"])
    touch = hardware[1] if isinstance(hardware, list) and len(hardware) == 3 else 0
    if profile["ios"] or (profile["platform"] == "macOS" and isinstance(touch, int) and touch > 1):
        flags.append("ios")
    flags += client_hint_flags(profile, headers)
    if any(signals[name].status != "ok" for name in ("iframe", "integrity", "canvasIntegrity", "engine")):
        flags.append("checks_unavailable")
    return flags


def _iframe_flags(signals, navigator):
    frame = _parsed(signals["iframe"])
    if signals["iframe"].status != "ok":
        return []
    if not (isinstance(frame, list) and len(frame) == 6):
        return ["invalid_iframe"]
    ua, platform, cpus, renderer, time_zone, canvas = frame
    pairs = []
    if navigator is not None:
        pairs += [(ua, navigator[0]), (platform, navigator[3]), (cpus, navigator[1])]
    graphics = _parsed(signals["graphics"])
    if isinstance(graphics, dict):
        pairs.append((renderer, graphics.get("renderer")))
    locale = _parsed(signals["locale"])
    if isinstance(locale, list) and locale:
        pairs.append((time_zone, locale[0]))
    # A part the frame could not read is null; that is degradation, not a lie.
    if any(a is not None and b is not None and a != b for a, b in pairs):
        return ["iframe_mismatch"]
    # The canvas alone differing is more likely native noise the read-back missed (it may differ
    # per canvas) than a lie: a script patching the canvas shows up as native_tampered.
    clean = signals["canvasIntegrity"].value == "clean" and signals["canvas"].status == "ok"
    if clean and canvas is not None and canvas != signals["canvas"].value:
        return ["iframe_canvas_mismatch"]
    return []


def tier_of(flags):
    if SUSPICIOUS.intersection(flags):
        return "suspicious"
    if LOW.intersection(flags):
        return "low"
    return "high"


# --- Assessment -------------------------------------------------------------------------------


def assess(browser_id, evidence, headers, previous_browser_id=None):
    """Score one verified submission. ``headers`` are the request's own (case does not matter)."""
    headers = {name.lower(): value for name, value in headers.items()}
    signals = evidence.signals
    profile = user_agent_profile(headers.get("user-agent", ""))
    indicators = [f"{name}_{probe.status}" for name, probe in signals.items() if probe.status != "ok"]
    indicators += consistency_flags(signals, headers, profile)
    if not evidence.persistent:
        indicators.append("ephemeral_key")
    components = {
        name: keyed_id(f"probe.{name}", probe.value)
        for name, probe in signals.items()
        if name in WEIGHTS and probe.status == "ok"
    }
    noisy_canvas = "canvas_noise" in indicators or "iframe_canvas_mismatch" in indicators
    stable = {
        name: components[name] for name in STABLE if name in components and not (name == "canvas" and noisy_canvas)
    }
    if any(name not in stable for name in DISTINCTIVE):
        indicators.append("low_signal_coverage")
    similarity = _similarity(browser_id, components)
    if similarity is not None and similarity < 0.7:
        indicators.append("fingerprint_changed")
    if previous_browser_id is not None and previous_browser_id != browser_id:
        indicators.append("browser_key_changed")
    tier = tier_of(indicators)
    protected = tier == "low" and bool(PROTECTED.intersection(indicators))
    fingerprint = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    return {
        "browserId": browser_id,
        "persistent": evidence.persistent,
        "fingerprintId": keyed_id("fingerprint", fingerprint) if tier == "high" else None,
        "coarsePrint": _coarse_print(components, headers) if protected else None,
        "tier": tier,
        "continuity": previous_browser_id == browser_id,
        "similarity": similarity,
        "indicators": sorted(set(indicators)),
        # Keyed hashes, for the /system panel's prefixes: which probe moved between two checks.
        "components": components,
    }


def _coarse_print(components, headers):
    """The parts a protected browser's noise leaves alone, plus its user agent; None without them.

    Many devices share it, so it is never a claim on its own: ``device_keys`` binds it to the
    network and the day.
    """
    coarse = {name: components[name] for name in COARSE if name in components}
    if not coarse:
        return None
    coarse["userAgent"] = keyed_id("probe.userAgent", headers.get("user-agent", ""))
    return keyed_id("coarse", json.dumps(coarse, sort_keys=True, separators=(",", ":")))


def ip_bucket(ip):
    """IPv4 as it is; IPv6 by its /64, since privacy addresses rotate the host part."""
    try:
        address = ipaddress.ip_address(ip or "")
    except ValueError:
        return ip or "-"
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def _similarity(browser_id, components):
    weight = sum(WEIGHTS[name] for name in components)
    baseline_key = f"browser:baseline:{browser_id}"
    try:
        baseline = cache.get(baseline_key)
        # Never replace the first baseline with an anomalous later observation.
        if baseline is None and weight >= 8:
            cache.add(baseline_key, components, timeout=RETENTION)
            baseline = cache.get(baseline_key)
    except CACHE_ERRORS as exc:
        logger.warning(f"Browser baseline unavailable: {exc}")
        return None
    if not baseline:
        return None
    union = set(baseline) | set(components)
    denominator = sum(WEIGHTS[name] for name in union)
    matching = sum(WEIGHTS[name] for name in union if baseline.get(name) == components.get(name))
    return round(matching / denominator, 3) if denominator else None


def previous_browser(context):
    try:
        return cache.get(f"browser:context-key:{keyed_id('context', context)}")
    except CACHE_ERRORS:
        return None


def store_receipt(response, context, assessment):
    """Keep the assessment server-side; the cookie only names it. False when the cache failed."""
    rid = secrets.token_urlsafe(18)
    try:
        cache.set(f"browser:receipt:{rid}", assessment, timeout=RECEIPT_TTL)
        cache.set(f"browser:context-key:{keyed_id('context', context)}", assessment["browserId"], timeout=RETENTION)
    except CACHE_ERRORS as exc:
        logger.warning(f"Browser receipt unavailable: {exc}")
        return False
    set_cookie(response, RECEIPT_COOKIE, {"context": keyed_id("context", context), "rid": rid}, RECEIPT_TTL)
    return True


def get_browser_assessment(request):
    """The verified, recent assessment of this browser; None means unknown.

    A receipt is a bearer cookie, not proof that the current request holds the key. It is
    bound to the browser-context cookie and lives 15 minutes.
    """
    if not settings.BROWSER_FINGERPRINT_ENABLED:
        return None
    receipt = read_cookie(request, RECEIPT_COOKIE, RECEIPT_TTL)
    context = read_cookie(request, CONTEXT_COOKIE, RETENTION)
    if not isinstance(receipt, dict) or not isinstance(context, str) or not isinstance(receipt.get("rid"), str):
        return None
    if not constant_time_compare(receipt.get("context", ""), keyed_id("context", context)):
        return None
    try:
        return cache.get(f"browser:receipt:{receipt['rid']}")
    except CACHE_ERRORS as exc:
        logger.warning(f"Browser receipt unavailable: {exc}")
        return None


def device_keys(assessment, ip=None):
    """The keys a claim can be made on. Suspicious or unknown: none. A fingerprint only when high.

    A protected browser (``coarsePrint``) also claims on its coarse print within its network and
    UTC day: a new private tab mints a new key, but not a new ``p:`` key. Look-alike devices
    behind one address (CGNAT, iCloud Private Relay) share it for that day, which is never
    stricter than the per-IP count an unrecognised browser falls back to.
    """
    if not isinstance(assessment, dict) or assessment.get("tier") not in {"high", "low"}:
        return []
    keys = []
    if assessment.get("persistent"):
        keys.append(f"b:{assessment['browserId']}")
    if assessment.get("tier") == "high" and assessment.get("fingerprintId"):
        keys.append(f"f:{assessment['fingerprintId']}")
    if assessment.get("coarsePrint") and ip:
        day = datetime.now(tz=UTC).date().isoformat()
        network = f"{assessment['coarsePrint']}|{ip_bucket(ip)}|{day}"
        keys.append(f"p:{keyed_id('network', network)}")
    return keys


def is_trusted(assessment):
    """Neither unknown nor suspicious: its claims can stand on their own."""
    return isinstance(assessment, dict) and assessment.get("tier") in {"high", "low"}


def log_assessment(assessment):
    # Tier and indicator names only; never a probe value, key or id.
    logger.info(f"Browser assessed: {assessment['tier']} {','.join(assessment['indicators']) or '-'}")
