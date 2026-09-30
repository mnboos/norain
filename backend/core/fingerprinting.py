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
- **Never shed keys.** Fewer claim keys mean looser limits, so nothing a browser can trigger
  at will may leave it with fewer: a ``suspicious`` browser keeps its key (the signature still
  proves it) and pays the IP's count on top, and a browser without keys pays the IP's count.
- **New lie checks start in observe mode** (``settings.BROWSER_OBSERVE_ONLY``): named in the
  indicators and counted in the stats, but tier-neutral until their *solo* count (fired with
  no other lie) stayed at 0 for 14 days and a device matrix was clean.
- **IP floors count an IPv6 address by its /56** (``ip_floor``): a /64 costs nothing to rotate.
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
from datetime import UTC, datetime, timedelta
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
        # Observed first (BROWSER_OBSERVE_ONLY):
        "client_hints_grease_mismatch",
        "fetch_metadata_mismatch",
        "graphics_platform_mismatch",
        "realm_tampered",
        "native_stack_tampered",
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
        # Observed first (BROWSER_OBSERVE_ONLY):
        "fetch_metadata_missing",
        "fingerprint_common",
    }
)
# What renderer noise leaves alone (Safari's private tabs salt it per tab, Brave and Firefox's
# resistFingerprinting randomise it). Shared by every device of one model and browser version.
COARSE = ("graphics", "fonts", "hardware", "math", "media")
CACHE_ERRORS = (RedisError, OSError)
CLASS_E = ipaddress.ip_network("240.0.0.0/4")


class Probe(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["ok", "unavailable", "timeout", "unstable"]
    value: str | None = Field(default=None, max_length=4096)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[2]
    persistent: bool
    signals: dict[str, Probe]


def _now():
    """The clock the counters use; tests patch this, never ``time.time`` (locmem expiry reads that)."""
    return time.time()


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
    window = int(_now()) // 60
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
    return f"browser:churn:{keyed_id('churn', ip_floor(ip))}:{int(_now()) // 3600}"


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
    """When this server first saw the key (epoch seconds); a new one counts against the IP's churn.

    The marker lives 30 days from the key's last proof. A marker from before it held a time
    reads 1, so such a key counts as long established. None when the cache is down.
    """
    seen = f"browser:seen:{browser_id}"
    try:
        if cache.add(seen, int(_now()), timeout=RETENTION):
            key = _churn_key(ip)
            cache.add(key, 0, timeout=3700)
            cache.incr(key)
        else:
            cache.touch(seen, RETENTION)
        first_seen = cache.get(seen)
    except (ValueError, *CACHE_ERRORS) as exc:
        logger.warning(f"Browser churn counter unavailable: {exc}")
        return None
    return first_seen if isinstance(first_seen, int) else None


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


class ProofRefusedError(ValueError):
    """A refused proof. ``reason`` is a short code for the stats; the reply never carries it."""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def verify_proof(challenge, context, payload, public_key, signature, nonce):
    try:
        issued = signing.loads(challenge, salt=SALT, max_age=CHALLENGE_TTL)
    except signing.BadSignature as error:
        raise ProofRefusedError("challenge", "Invalid or expired challenge") from error
    if not constant_time_compare(issued["context"], keyed_id("context", context)):
        raise ProofRefusedError("context", "Challenge belongs to another browser context")
    if not pow_valid(pow_seed(challenge, payload), nonce, issued["bits"]):
        raise ProofRefusedError("pow", "Invalid proof-of-work")
    try:
        evidence = parse_evidence(payload)
    except ValueError as error:
        raise ProofRefusedError("evidence", str(error)) from error
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
        raise ProofRefusedError("signature", "Invalid browser proof") from error
    # Only one valid submission wins, including across workers. Invalid proofs don't burn the nonce.
    if not cache.add(f"browser:used:{issued['nonce']}", True, timeout=CHALLENGE_TTL + 10):
        raise ProofRefusedError("replay", "Challenge already used")
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
    firefox = re.search(r"Firefox/(\d+)", ua)
    safari = re.search(r"Version/(\d+)\.(\d+)", ua)
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
        "firefox": int(firefox.group(1)) if engine == "gecko" and firefox else None,
        "safari": (int(safari.group(1)), int(safari.group(2))) if engine == "webkit" and safari else None,
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


# Chromium derives the made-up brand in Sec-CH-UA from its own major version (GetGreasedUserAgent-
# BrandVersion in components/embedder_support/user_agent_utils.cc), and every Chromium fork keeps
# that code. A header template edited to claim another version keeps the old brand.
GREASE_CHARS = (" ", "(", ":", "-", ".", "/", ")", ";", "=", "?", "_")
GREASE_VERSIONS = ("8", "99", "24")
GREASE_SINCE = 115


def _grease_matches(major, listed):
    """Whether the brand list carries the GREASE brand Chromium ``major`` itself would send."""
    if major < GREASE_SINCE:
        return True
    name = f"Not{GREASE_CHARS[major % 11]}A{GREASE_CHARS[(major + 1) % 11]}Brand"
    return listed.get(name, "").split(".")[0] == GREASE_VERSIONS[major % 3]


def fetch_metadata_flags(profile, headers):
    """The verify request is a JSON ``fetch`` POST from the page; its Sec-Fetch-* can say nothing else.

    A browser version that sends them always sends them, so their absence there is noted too
    (``low``: a proxy or an extension may strip them).
    """
    site, mode, dest = (headers.get(f"sec-fetch-{name}") for name in ("site", "mode", "dest"))
    if site is None and mode is None and dest is None:
        sends = (
            (profile["chromium"] or 0) >= 80
            or (profile["firefox"] or 0) >= 90
            or (profile["safari"] or (0, 0)) >= (16, 4)
        )
        return ["fetch_metadata_missing"] if sends else []
    if (
        site not in {"same-origin", "same-site"}
        or mode not in {"cors", "same-origin"}
        or dest != "empty"
        or headers.get("sec-fetch-user") is not None
    ):
        return ["fetch_metadata_mismatch"]
    return []


# What the renderer string can name only on one OS, through ANGLE's backends. D3D12 is left out:
# WSL passes it through to a Linux browser.
DIRECT3D = re.compile(r"Direct3D(?:9|11)|D3D11|D3D9", re.IGNORECASE)
METAL = re.compile(r"\bMetal\b", re.IGNORECASE)
NON_APPLE_GPU = re.compile(r"Intel|AMD|Radeon|NVIDIA|GeForce", re.IGNORECASE)
# WebAssembly's 0/0 NaN as the hardware makes it (the ``math`` probe): x86 sets the sign bit.
ARM_NAN = "7fc00000"


def graphics_flags(profile, graphics, math):
    """Hardware that cannot exist: a GPU interface of another OS, or an Apple-silicon Mac with a PC GPU.

    Masked renderer strings (Safari's "Apple GPU", resistFingerprinting's, Brave's generic one)
    name no backend or vendor, so they never trip it; neither does a phone.
    """
    renderer = str(graphics.get("renderer", "")) if isinstance(graphics, dict) else ""
    if not renderer or profile["mobile"] or profile["ios"] or profile["platform"] in {None, "Android"}:
        return []
    platform = profile["platform"]
    if DIRECT3D.search(renderer) and platform != "Windows":
        return ["graphics_platform_mismatch"]
    if METAL.search(renderer) and platform != "macOS":
        return ["graphics_platform_mismatch"]
    nan = math[-1] if isinstance(math, list) and math and isinstance(math[-1], list) else None
    # Apple silicon runs no PC GPU, and an Intel Mac cannot make ARM's NaN.
    if platform == "macOS" and NON_APPLE_GPU.search(renderer) and nan and nan[0] == ARM_NAN:
        return ["graphics_platform_mismatch"]
    return []


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
    listed = _brands(brands)
    chromium = listed.get("Chromium")
    if chromium and profile["chromium"] is not None and chromium.split(".")[0] != str(profile["chromium"]):
        flags.append("client_hints_mismatch")
    if chromium and chromium.split(".")[0].isdigit() and not _grease_matches(int(chromium.split(".")[0]), listed):
        flags.append("client_hints_grease_mismatch")
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
        flags += sorted({_tamper_kind(str(entry)) for entry in tampered})
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
    flags += graphics_flags(profile, graphics, _parsed(signals["math"]))
    hardware = _parsed(signals["hardware"])
    touch = hardware[1] if isinstance(hardware, list) and len(hardware) == 3 else 0
    if profile["ios"] or (profile["platform"] == "macOS" and isinstance(touch, int) and touch > 1):
        flags.append("ios")
    flags += client_hint_flags(profile, headers)
    flags += fetch_metadata_flags(profile, headers)
    # A check that did not run cannot catch a lie, so skipping one must cost the browser something.
    if any(signals[name].status != "ok" for name in CHECKS):
        flags.append("checks_unavailable")
    return flags


def _tamper_kind(entry):
    """``tamperedNatives`` entries by what they caught (realm.ts).

    The user-agent data targets came after the first release, so they report through an
    observed indicator until their own solo count allows enforcing them as ``native_tampered``.
    """
    if entry.startswith(("receiver:", "frame:", "NavigatorUAData.")) or "userAgentData" in entry:
        return "realm_tampered"
    if entry.startswith("stack:"):
        return "native_stack_tampered"
    return "native_tampered"


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
    """The tier the indicators earn; those still observed (``BROWSER_OBSERVE_ONLY``) earn nothing."""
    active = set(flags) - settings.BROWSER_OBSERVE_ONLY
    if SUSPICIOUS.intersection(active):
        return "suspicious"
    if LOW.intersection(active):
        return "low"
    return "high"


# --- Assessment -------------------------------------------------------------------------------


def assess(browser_id, evidence, headers, previous_browser_id=None, first_seen=None):
    """Score one verified submission. ``headers`` are the request's own (case does not matter).

    ``first_seen`` (``note_new_browser``) decides ``established``: whether the key is old enough
    (``BROWSER_KEY_AGE``) to claim on its own. Unknown counts as established (fails open).
    """
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
    fingerprint = keyed_id("fingerprint", json.dumps(stable, sort_keys=True, separators=(",", ":")))
    if fingerprint_is_common(fingerprint):
        indicators.append("fingerprint_common")
    tier = tier_of(indicators)
    return {
        "browserId": browser_id,
        "persistent": evidence.persistent,
        "established": first_seen is None or _now() - first_seen >= settings.BROWSER_KEY_AGE,
        "fingerprintId": fingerprint if tier == "high" else None,
        # Every low browser, whatever made it low: losing the fingerprint must never free it.
        "coarsePrint": _coarse_print(components, headers) if tier == "low" else None,
        "tier": tier,
        "continuity": previous_browser_id == browser_id,
        "similarity": similarity,
        "indicators": sorted(set(indicators)),
        # Keyed hashes, for the /system panel's prefixes: which probe moved between two checks.
        "components": components,
    }


def _coarse_print(components, headers):
    """The parts renderer noise leaves alone, plus the user agent; the user agent alone without them.

    Many devices share it, so it is never a claim on its own: ``device_keys`` binds it to the
    network and the day.
    """
    coarse = {name: components[name] for name in COARSE if name in components}
    coarse["userAgent"] = keyed_id("probe.userAgent", headers.get("user-agent", ""))
    return keyed_id("coarse", json.dumps(coarse, sort_keys=True, separators=(",", ":")))


def ip_bucket(ip, v6_prefix=64, v4_prefix=32):
    """IPv4 as it is; IPv6 by its /64, since privacy addresses rotate the host part."""
    try:
        address = ipaddress.ip_address(ip or "")
    except ValueError:
        return ip or "-"
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if isinstance(address, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{address}/{v6_prefix}", strict=False))
    if v4_prefix < 32:
        return str(ipaddress.ip_network(f"{address}/{v4_prefix}", strict=False))
    return str(address)


def ip_floor(ip):
    """What a per-IP limit counts: IPv4 as it is, IPv6 by its /56.

    Providers hand one customer a /56 or a /48, so a /64 would give anyone with an IPv6 line
    256 fresh addresses at no cost. IPv4 keys stay exactly as before.
    """
    return ip_bucket(ip, 56)


def _network(ip):
    """A coarser neighbourhood for counting how widely a fingerprint travels: IPv4 /24, IPv6 /48."""
    return ip_bucket(ip, 48, 24)


def is_class_e(ip):
    """240.0.0.0/4: Cloudflare's Pseudo IPv4 in "overwrite" mode, which hides every IPv6 prefix."""
    try:
        return ipaddress.ip_address(ip or "") in CLASS_E
    except ValueError:
        return False


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
    """The keys a claim can be made on. Unknown: none. A fingerprint only when high.

    A suspicious browser keeps its own key: the signature proves it holds that key whatever its
    probes claimed, and dropping it would let anyone shed their claims by lying on purpose. Its
    fingerprint and coarse print come from values that lied, so they never count, and it pays
    the IP's count besides (``is_trusted``).

    A low browser (``coarsePrint``) also claims on its coarse print within its network and UTC
    day: a new private tab mints a new key, but not a new ``p:`` key. Look-alike devices
    behind one address (CGNAT, iCloud Private Relay) share it for that day, which is never
    stricter than the per-IP count an unrecognised browser falls back to.
    """
    if not isinstance(assessment, dict) or assessment.get("tier") not in {"high", "low", "suspicious"}:
        return []
    keys = []
    if assessment.get("persistent"):
        keys.append(f"b:{assessment['browserId']}")
    if assessment.get("tier") == "suspicious":
        return keys
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


def is_established(assessment):
    """Whether the key is old enough to claim on its own (``BROWSER_KEY_AGE``); receipts from
    before the key age counted as established."""
    return isinstance(assessment, dict) and assessment.get("established", True) is not False


def log_assessment(assessment):
    # Tier and indicator names only; never a probe value, key or id.
    logger.info(f"Browser assessed: {assessment['tier']} {','.join(assessment['indicators']) or '-'}")


# --- Measured distinctiveness ----------------------------------------------------------------

# A fingerprint is one device's only while few keys share it. Counted, not assumed: a new key
# for it on this many networks within 30 days, or this many new keys within an hour (a cloned
# profile), and it is common for 30 days (``fingerprint_common``).
COMMON_NETWORKS = 3
COMMON_BURST = 5


def fingerprint_is_common(fingerprint_id):
    if not fingerprint_id:
        return False
    try:
        return bool(cache.get(f"browser:fp-common:{fingerprint_id}"))
    except CACHE_ERRORS as exc:
        logger.warning(f"Browser fingerprint counts unavailable: {exc}")
        return False


def note_fingerprint(fingerprint_id, browser_id, ip):
    """Count how widely a ``high`` fingerprint travels. Fails open.

    Only a (fingerprint, key) pair seen for the first time counts, so one device moving between
    networks adds nothing; its network is its IPv4 /24 or IPv6 /48 (``_network``).
    """
    if not fingerprint_id:
        return
    try:
        if not cache.add(f"browser:fp-key:{keyed_id('fp-key', f'{fingerprint_id}|{browser_id}')}", 1, RETENTION):
            return
        hour = f"browser:fp-new:{fingerprint_id}:{int(_now()) // 3600}"
        cache.add(hour, 0, timeout=3700)
        burst = cache.incr(hour)
        networks_key = f"browser:fp-nets:{fingerprint_id}"
        networks = cache.get(networks_key) or 0
        if cache.add(f"browser:fp-net:{keyed_id('fp-net', f'{fingerprint_id}|{_network(ip)}')}", 1, RETENTION):
            cache.add(networks_key, 0, timeout=RETENTION)
            networks = cache.incr(networks_key)
        if networks >= COMMON_NETWORKS or burst >= COMMON_BURST:
            cache.set(f"browser:fp-common:{fingerprint_id}", 1, timeout=RETENTION)
    except (ValueError, *CACHE_ERRORS) as exc:
        logger.warning(f"Browser fingerprint counts unavailable: {exc}")


# --- Aggregate stats (the /system panel) -----------------------------------------------------

STATS_TTL = 35 * 24 * 3600
STATS_DAYS = 14
REFUSALS = ("challenge", "context", "pow", "evidence", "signature", "replay", "echo")
TIERS = ("high", "low", "suspicious")
# Every indicator ``assess`` can name; a fixed vocabulary, so the stats never hold a value.
INDICATORS = frozenset(
    {
        *SUSPICIOUS,
        *LOW,
        "fingerprint_changed",
        "browser_key_changed",
        *(f"{name}_{status}" for name in SIGNALS for status in ("unavailable", "timeout", "unstable")),
    }
)


def _today():
    return datetime.fromtimestamp(_now(), tz=UTC).date()


def count_stat(name):
    """One more ``name`` today, for the admin's tuning. Names come from a fixed set. Fails open."""
    key = f"browser:stats:{_today().isoformat()}:{name}"
    try:
        cache.add(key, 0, timeout=STATS_TTL)
        cache.incr(key)
    except (ValueError, *CACHE_ERRORS) as exc:
        logger.warning(f"Browser stats unavailable: {exc}")


def record_stats(assessment):
    """Count the tier and every indicator, and each observed one that fired *solo*.

    Solo means no *enforced* lie and no automation beside it: the likely false positive, so an
    observed lie is enforced only once its solo count stayed at 0 (``BROWSER_OBSERVE_ONLY``).
    Other observed lies do not count against it, or two false positives that fire together (a
    header-rewriting proxy trips several) would hide each other.
    """
    indicators = set(assessment["indicators"]) & INDICATORS
    enforced_lies = indicators & SUSPICIOUS - settings.BROWSER_OBSERVE_ONLY
    count_stat(f"tier:{assessment['tier']}")
    for name in sorted(indicators):
        count_stat(f"ind:{name}")
        if name in settings.BROWSER_OBSERVE_ONLY and not enforced_lies and "automation" not in indicators:
            count_stat(f"solo:{name}")


def stat_names():
    """Every name ``count_stat`` may have written, so the reader needs no key scan."""
    return [
        *(f"path:{verdict}" for verdict in PATH_VERDICTS),
        *(f"excess:{transport}:{bucket}" for transport in TRANSPORTS for bucket in (*EXCESS_BUCKETS, "unknown")),
        *(f"tier:{tier}" for tier in TIERS),
        *(f"ind:{name}" for name in sorted(INDICATORS)),
        *(f"solo:{name}" for name in sorted(INDICATORS & settings.BROWSER_OBSERVE_ONLY)),
        *(f"refused:{reason}" for reason in REFUSALS),
        *(f"pow:{bits}" for bits in range(POW_MAX_BITS + 1)),
        "ip_class_e",
    ]


def read_stats(days=STATS_DAYS):
    """``{day: {name: count}}`` for the last ``days`` UTC days, zeros left out. Fails open (empty)."""
    today = _today()
    dates = [(today - timedelta(days=offset)).isoformat() for offset in range(days)]
    names = stat_names()
    try:
        found = cache.get_many([f"browser:stats:{day}:{name}" for day in dates for name in names])
    except CACHE_ERRORS as exc:
        logger.warning(f"Browser stats unavailable: {exc}")
        return {}
    stats = {}
    for day in dates:
        counts = {name: found[key] for name in names if (key := f"browser:stats:{day}:{name}") in found}
        if counts:
            stats[day] = counts
    return stats


# --- The relay meter (observe mode) ----------------------------------------------------------
#
# Cloudflare measures the TCP (or QUIC) round trip to whatever ended the connection at its
# edge. A residential proxy ends it at the proxy's exit, so the bot's own leg behind the exit
# never shows there, but it does show in the round trips the server times itself: the echoes.
# excess = the fastest echo - the edge's round trip - this Cloudflare site's own leg (the p10 of
# recent samples there). Only the verdict and a coarse bucket are kept; raw round trips live
# 130 s. It never changes a tier. Its allowances are enforced only after the PoC gates (G0, G1
# in docs/reference/browser-fingerprinting.md); until then it counts, for tuning.

ECHO_STEPS = 3
COLO_SAMPLES = 500
COLO_MIN_SAMPLES = 20
COLO_TTL = 7 * 24 * 3600
PATH_VERDICTS = ("direct", "relayed", "unclear", "unmeasured")
TRANSPORTS = ("tcp", "quic")
# Excess buckets for the stats, in ms: coarse enough to be no value, fine enough to set D and R.
EXCESS_BUCKETS = tuple(range(-50, 301, 10))


def meter_on():
    return settings.BROWSER_PATH_METER != "off"


def echo(challenge, context, step, token, headers):
    """One step of the echo chain; returns the next token. Each step needs the last reply's token.

    The server times the gap between its reply and the next request itself, so a client cannot
    report a round trip. ``headers`` carry the edge's own measurement (``BROWSER_PATH_HEADER``).
    """
    try:
        issued = signing.loads(challenge, salt=SALT, max_age=CHALLENGE_TTL)
    except signing.BadSignature as error:
        raise ProofRefusedError("challenge", "Invalid or expired challenge") from error
    if not constant_time_compare(issued["context"], keyed_id("context", context)):
        raise ProofRefusedError("context", "Challenge belongs to another browser context")
    key = f"browser:echo:{issued['nonce']}"
    arrived = int(_now() * 1000)
    record = cache.get(key) or {"step": 0, "token": "", "sent": None, "rtts": []}
    if step != record["step"] + 1 or step > ECHO_STEPS or not constant_time_compare(token, record["token"]):
        raise ProofRefusedError("echo", "Echo out of order")
    if record["sent"] is not None:
        record["rtts"].append(arrived - record["sent"])
    headers = {name.lower(): value for name, value in headers.items()}
    edge = _edge(headers.get(settings.BROWSER_PATH_HEADER.lower(), ""))
    if edge is not None:
        record["edge"] = edge
    ray = headers.get("cf-ray", "")
    if "-" in ray:
        record["colo"] = ray.rsplit("-", 1)[1][:8]
    record.update(step=step, token=secrets.token_urlsafe(12), sent=int(_now() * 1000))
    cache.set(key, record, timeout=CHALLENGE_TTL + 10)
    return record["token"]


def _edge(value):
    """``"tcp_rtt,quic_rtt,asn"`` as the Transform Rule writes it; None when unusable."""
    parts = value.split(",")
    if len(parts) != 3 or not all(part.strip().isdigit() for part in parts):
        return None
    tcp, quic, asn = (int(part) for part in parts)
    return (tcp, quic, asn) if (tcp or quic) else None


def take_echo(challenge):
    """The echo record of a verified challenge, removed from the cache; None without one."""
    try:
        nonce = signing.loads(challenge, salt=SALT, max_age=CHALLENGE_TTL)["nonce"]
        record = cache.get(f"browser:echo:{nonce}")
        cache.delete(f"browser:echo:{nonce}")
    except (signing.BadSignature, KeyError, *CACHE_ERRORS):
        return None
    return record if isinstance(record, dict) else None


def _colo_floor(colo, sample):
    """Add ``sample`` (app - edge, ms) to this Cloudflare site's recent ones; their p10, or None."""
    key = f"browser:colo-floor:{colo or '-'}"
    try:
        samples = [*(cache.get(key) or []), sample][-COLO_SAMPLES:]
        cache.set(key, samples, timeout=COLO_TTL)
    except CACHE_ERRORS as exc:
        logger.warning(f"Browser relay meter unavailable: {exc}")
        return None
    if len(samples) < COLO_MIN_SAMPLES:
        return None
    return sorted(samples)[len(samples) // 10]


def path_verdict(record):
    """``(verdict, transport, excess bucket)`` from an echo record. Counted in the stats."""
    verdict, transport, bucket = "unmeasured", None, None
    if meter_on() and isinstance(record, dict) and record.get("rtts") and record.get("edge"):
        tcp, quic, _ = record["edge"]
        transport = "quic" if quic else "tcp"
        sample = min(record["rtts"]) - max(tcp, quic)
        floor = _colo_floor(record.get("colo"), sample)
        verdict = "unclear"
        if floor is not None:
            excess = sample - floor
            bucket = min(max(excess // 10 * 10, EXCESS_BUCKETS[0]), EXCESS_BUCKETS[-1])
            direct, relayed = settings.BROWSER_PATH_DIRECT_MS, settings.BROWSER_PATH_RELAYED_MS
            if direct is not None and excess < direct:
                verdict = "direct"
            elif relayed is not None and excess > relayed:
                verdict = "relayed"
    count_stat(f"path:{verdict}")
    if transport is not None:
        count_stat(f"excess:{transport}:{'unknown' if bucket is None else bucket}")
    return verdict, transport, bucket


def path_fields(record, ip):
    """What the receipt keeps of the path: the verdict, a coarse excess bucket and keyed ids."""
    verdict, transport, bucket = path_verdict(record)
    asn = record["edge"][2] if isinstance(record, dict) and record.get("edge") else None
    return {
        "path": verdict,
        "pathTransport": transport,
        "pathExcess": bucket,
        # Claims later compare these: a proof from one exit does not vouch for another.
        "pathBucket": keyed_id("path", ip_bucket(ip)),
        "asnKey": keyed_id("asn", str(asn)) if asn else None,
    }
