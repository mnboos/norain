import base64
import hashlib
import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from django.conf import settings
from django.core.cache import cache
from django.core.cache.backends.redis import RedisCache
from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from redis.exceptions import RedisError

from . import coverage
from . import fingerprinting as fp
from .auth import device_throttle
from .models import CoverageVote

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.0 Mobile/15E148 Safari/604.1"
)
SAFARI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/26.0 Safari/605.1.15"
)
CHROME_HEADERS = {
    "User-Agent": CHROME_UA,
    "Sec-CH-UA": '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
    # What a JSON fetch POST from the page carries.
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}
# Each engine's real wording, read from Playwright's Chromium, Firefox and WebKit.
V8 = ["Invalid array length", "toFixed() digits argument must be between 0 and 100", "Invalid count value: -1", "v8"]
SPIDERMONKEY = ["invalid array length", "precision 101 out of range", "repeat count must be non-negative", "other"]
JAVASCRIPTCORE = [
    "Array length must be a positive integer of safe magnitude.",
    "toFixed() argument must be between 0 and 100",
    "String.prototype.repeat argument must be greater than or equal to 0 and not be Infinity",
    "other",
]
POW_VECTOR = Path(settings.BASE_DIR).parent / "frontend/src/lib/browser-fingerprint/__tests__/pow-vector.json"


def honest_signals(ua=CHROME_UA, platform="Win32", engine=V8):
    """What a real, unmodified desktop Chrome reports; each test changes one thing."""
    values = {name: name for name in fp.WEIGHTS}
    values["graphics"] = json.dumps({"vendor": "Google Inc.", "renderer": "ANGLE (NVIDIA GeForce RTX 3060)"})
    values["locale"] = json.dumps(["Europe/Zurich", "latn"])
    values["hardware"] = json.dumps([8, 0, 8])
    values["navigator"] = json.dumps([ua, 8, "de-CH", platform])
    values["worker"] = json.dumps([ua, 8, "de-CH", platform])
    values["iframe"] = json.dumps([ua, platform, 8, "ANGLE (NVIDIA GeForce RTX 3060)", "Europe/Zurich", "canvas"])
    values["integrity"] = "[]"
    values["canvasIntegrity"] = "clean"
    values["engine"] = json.dumps(engine)
    values["automation"] = json.dumps({"webdriver": False, "cdc": False, "chrome": True})
    return {name: {"status": "ok", "value": value} for name, value in values.items()}


def solve(challenge, payload, bits):
    seed = fp.pow_seed(challenge, payload)
    nonce = 0
    while not fp.pow_valid(seed, str(nonce), bits):
        nonce += 1
    return str(nonce)


def evidence(signals, persistent=True):
    return fp.parse_evidence(json.dumps({"version": 2, "persistent": persistent, "signals": signals}))


def recognise(
    client, tier, browser="browser", fingerprint="fingerprint", persistent=True, coarse=None, established=True
):
    """Give a test client a stored receipt, as a verified browser of ``tier`` would have."""
    response = HttpResponse()
    context = f"context-{browser}"
    fp.set_cookie(response, fp.CONTEXT_COOKIE, context, fp.RETENTION)
    assessment = {
        "browserId": browser,
        "persistent": persistent,
        "established": established,
        "fingerprintId": fingerprint if tier == "high" else None,
        "coarsePrint": coarse,
        "tier": tier,
        "continuity": False,
        "similarity": None,
        "indicators": [],
        "components": {},
    }
    fp.store_receipt(response, context, assessment)
    for name, morsel in response.cookies.items():
        client.cookies[name] = morsel.value


@override_settings(CACHES=LOCMEM, BROWSER_FINGERPRINT_ENABLED=True, BROWSER_POW_BITS=4)
class FingerprintTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.public = base64.b64encode(
            self.key.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        ).decode()
        self.signals = honest_signals()

    def payload(self):
        return json.dumps({"version": 2, "persistent": True, "signals": self.signals})

    def submission(self, challenge, bits=4):
        payload = self.payload()
        nonce = solve(challenge, payload, bits)
        message = f"{challenge}\n{payload}\n{nonce}".encode()
        r, s = decode_dss_signature(self.key.sign(message, ec.ECDSA(hashes.SHA256())))
        return {
            "challenge": challenge,
            "payload": payload,
            "publicKey": self.public,
            "signature": base64.b64encode(r.to_bytes(32) + s.to_bytes(32)).decode(),
            "pow": nonce,
        }

    def verify(self, data, context="context"):
        return fp.verify_proof(
            data["challenge"], context, data["payload"], data["publicKey"], data["signature"], data["pow"]
        )

    def assess(self, headers=None, persistent=True, previous=None):
        return fp.assess("browser", evidence(self.signals, persistent), headers or CHROME_HEADERS, previous)

    def test_proof_rejects_tampering_replay_and_context_copy(self):
        data = self.submission(fp.issue_challenge("context", 4))
        with self.assertRaisesRegex(ValueError, "another browser"):
            self.verify(data, "other")
        tampered = data["payload"].replace("Europe/Zurich", "Europe/Paris")
        with self.assertRaisesRegex(ValueError, "Invalid browser proof"):
            self.verify({**data, "payload": tampered, "pow": solve(data["challenge"], tampered, 4)})
        browser_id, _ = self.verify(data)
        self.assertEqual(len(browser_id), 64)
        with self.assertRaisesRegex(ValueError, "already used"):
            self.verify(data)

    def test_proof_of_work_is_checked_before_the_nonce_is_spent(self):
        data = self.submission(fp.issue_challenge("context", 12), bits=12)
        seed = fp.pow_seed(data["challenge"], data["payload"])
        wrong = next(str(n) for n in range(10_000) if not fp.pow_valid(seed, str(n), 12))
        for nonce in (wrong, "", "-1", "1" * 17, "１２"):
            with self.subTest(nonce=nonce), self.assertRaisesRegex(ValueError, "proof-of-work"):
                self.verify({**data, "pow": nonce})
        # The signed difficulty holds: a proof solved for fewer bits is refused.
        easy = self.submission(fp.issue_challenge("context", 12), bits=0)
        if not fp.pow_valid(fp.pow_seed(easy["challenge"], easy["payload"]), easy["pow"], 12):
            with self.assertRaisesRegex(ValueError, "proof-of-work"):
                self.verify(easy)
        self.verify(data)

    def test_proof_of_work_vector_matches_the_client(self):
        vector = json.loads(POW_VECTOR.read_text(encoding="utf-8"))
        self.assertEqual(hashlib.sha256(vector["payload"].encode()).hexdigest(), vector["payloadDigest"])
        self.assertEqual(fp.pow_seed(vector["challenge"], vector["payload"]), vector["seed"])
        self.assertEqual(hashlib.sha256(f"{vector['seed']}:{vector['nonce']}".encode()).hexdigest(), vector["digest"])
        self.assertTrue(fp.pow_valid(vector["seed"], vector["nonce"], vector["bits"]))
        self.assertFalse(fp.pow_valid(vector["seed"], str(int(vector["nonce"]) + 1), 24))

    def test_churn_makes_proofs_dearer_but_never_changes_the_tier(self):
        self.assertEqual(fp.pow_bits("10.0.0.1"), 4)
        for n in range(fp.CHURN_FREE):
            fp.note_new_browser(f"browser-{n}", "10.0.0.1")
        self.assertEqual(fp.pow_bits("10.0.0.1"), 6)
        fp.note_new_browser("browser-0", "10.0.0.1")  # a returning key is not churn
        self.assertEqual(fp.pow_bits("10.0.0.1"), 6)
        for n in range(100):
            fp.note_new_browser(f"more-{n}", "10.0.0.1")
        with self.settings(BROWSER_POW_BITS=18):
            self.assertEqual(fp.pow_bits("10.0.0.1"), fp.POW_MAX_BITS)
        self.assertEqual(fp.pow_bits("10.0.0.2"), 4)
        self.assertEqual(self.assess()["tier"], "high")

    def test_challenge_expiration(self):
        data = self.submission(fp.issue_challenge("context", 4))
        with (
            patch("django.core.signing.time.time", return_value=fp.time.time() + 130),
            self.assertRaisesRegex(ValueError, "expired"),
        ):
            self.verify(data)

    def test_concurrent_replay_has_one_winner(self):
        data = self.submission(fp.issue_challenge("context", 4))

        def attempt(_):
            try:
                self.verify(data)
            except ValueError:
                return False
            return True

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(attempt, range(16))), 1)

    def test_missing_unknown_and_malformed_probes_rejected(self):
        for signals in [
            {},
            {**self.signals, "extra": {"status": "ok", "value": "x"}},
            {**self.signals, "canvas": {"status": "ok"}},
            {**self.signals, "canvas": {"status": "timeout", "value": "x"}},
            {name: probe for name, probe in self.signals.items() if name != "iframe"},
        ]:
            with self.subTest(signals=signals), self.assertRaises(ValueError):
                evidence(signals)
        with self.assertRaises(ValueError):
            fp.parse_evidence(json.dumps({"version": 1, "persistent": True, "signals": self.signals}))

    def test_honest_chromium_is_high_and_volatile_parts_keep_the_fingerprint(self):
        first = self.assess()
        self.assertEqual(first["tier"], "high", first["indicators"])
        self.assertRegex(first["fingerprintId"], r"^[0-9a-f]{64}$")
        self.signals["display"]["value"] = "[1080,2560,24]"
        self.signals["locale"]["value"] = json.dumps(["America/New_York", "latn"])
        self.signals["iframe"]["value"] = json.dumps(
            [CHROME_UA, "Win32", 8, "ANGLE (NVIDIA GeForce RTX 3060)", "America/New_York", "canvas"]
        )
        moved = self.assess(previous="browser")
        self.assertEqual(moved["tier"], "high", moved["indicators"])
        self.assertEqual(moved["fingerprintId"], first["fingerprintId"])
        self.assertTrue(moved["continuity"])

    def test_every_lie_is_suspicious(self):
        def worker(values):
            return {"status": "ok", "value": json.dumps(values)}

        cases = {
            "user_agent_mismatch": ({}, {**CHROME_HEADERS, "User-Agent": CHROME_UA.replace("130", "131")}),
            "worker_mismatch": ({"worker": worker([CHROME_UA, 4, "de-CH", "Win32"])}, None),
            "iframe_mismatch": (
                {
                    "iframe": worker(
                        [CHROME_UA, "MacIntel", 8, "ANGLE (NVIDIA GeForce RTX 3060)", "Europe/Zurich", "canvas"]
                    )
                },
                None,
            ),
            "native_tampered": ({"integrity": {"status": "ok", "value": '["Navigator.hardwareConcurrency"]'}}, None),
            "engine_mismatch": ({"engine": worker(SPIDERMONKEY)}, None),
            "client_hints_mismatch": ({}, {**CHROME_HEADERS, "Sec-CH-UA-Platform": '"macOS"'}),
            "automation": ({"automation": worker({"webdriver": True, "cdc": False, "chrome": True})}, None),
            "invalid_navigator": ({"navigator": {"status": "ok", "value": "not json"}}, None),
        }
        for flag, (changes, headers) in cases.items():
            with self.subTest(flag=flag):
                self.signals = {**honest_signals(), **changes}
                result = self.assess(headers)
                self.assertIn(flag, result["indicators"])
                self.assertEqual(result["tier"], "suspicious")
                self.assertIsNone(result["fingerprintId"])

    def test_a_canvas_only_iframe_difference_is_noise_not_a_lie(self):
        self.signals["iframe"]["value"] = json.dumps(
            [CHROME_UA, "Win32", 8, "ANGLE (NVIDIA GeForce RTX 3060)", "Europe/Zurich", "other canvas"]
        )
        result = self.assess()
        self.assertIn("iframe_canvas_mismatch", result["indicators"])
        self.assertNotIn("iframe_mismatch", result["indicators"])
        self.assertEqual(result["tier"], "low")
        # Read-back noise already explains it: not compared at all.
        self.signals["canvasIntegrity"]["value"] = "noise:12"
        self.assertNotIn("iframe_canvas_mismatch", self.assess()["indicators"])
        # Anything else the frame disagrees on is still a lie.
        self.signals["iframe"]["value"] = json.dumps(
            [CHROME_UA, "Win32", 4, "ANGLE (NVIDIA GeForce RTX 3060)", "Europe/Zurich", "other canvas"]
        )
        self.assertEqual(self.assess()["tier"], "suspicious")

    def test_client_hints_follow_the_engine(self):
        brand_mismatch = {**CHROME_HEADERS, "Sec-CH-UA": '"Chromium";v="120", "Not?A_Brand";v="99"'}
        self.assertIn("client_hints_mismatch", self.assess(brand_mismatch)["indicators"])
        mobile_mismatch = {**CHROME_HEADERS, "Sec-CH-UA-Mobile": "?1"}
        self.assertIn("client_hints_mismatch", self.assess(mobile_mismatch)["indicators"])
        stripped = {"User-Agent": CHROME_UA}
        result = self.assess(stripped)
        self.assertIn("client_hints_missing", result["indicators"])
        self.assertEqual(result["tier"], "low")
        # A Firefox UA that sends Chromium's hints is not Firefox.
        firefox = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0"
        self.signals = honest_signals(ua=firefox, engine=SPIDERMONKEY)
        honest = self.assess({"User-Agent": firefox})
        self.assertEqual(honest["tier"], "high", honest["indicators"])
        self.assertIn("client_hints_mismatch", self.assess({**CHROME_HEADERS, "User-Agent": firefox})["indicators"])

    def test_engine_is_read_from_error_messages(self):
        self.assertEqual(fp.js_engine(V8), "blink")
        self.assertEqual(fp.js_engine(SPIDERMONKEY), "gecko")
        self.assertEqual(fp.js_engine(JAVASCRIPTCORE), "webkit")
        # Unknown wording or a mixed signature is no evidence either way.
        self.assertIsNone(fp.js_engine(["a", "b", "c", "other"]))
        self.assertIsNone(fp.js_engine([V8[0], SPIDERMONKEY[1], JAVASCRIPTCORE[2], "other"]))
        self.assertIsNone(fp.js_engine("not a list"))

    def test_honest_but_common_browsers_are_low(self):
        def iphone():
            signals = honest_signals(ua=IPHONE_UA, platform="iPhone", engine=JAVASCRIPTCORE)
            signals["hardware"]["value"] = "[4,5,null]"
            return signals

        cases = {
            "canvas_noise": ({"canvasIntegrity": {"status": "ok", "value": "noise:3"}}, None, True),
            "software_renderer": (
                {
                    "graphics": {"status": "ok", "value": '{"renderer": "Google SwiftShader"}'},
                    "iframe": {
                        "status": "ok",
                        "value": json.dumps([CHROME_UA, "Win32", 8, "Google SwiftShader", "Europe/Zurich", "canvas"]),
                    },
                },
                None,
                True,
            ),
            "ios": (iphone(), {"User-Agent": IPHONE_UA}, True),
            "ephemeral_key": ({}, None, False),
            "checks_unavailable": ({"iframe": {"status": "unavailable"}}, None, True),
            "low_signal_coverage": ({"audio": {"status": "timeout"}}, None, True),
        }
        for flag, (changes, headers, persistent) in cases.items():
            with self.subTest(flag=flag):
                self.signals = {**honest_signals(), **changes}
                result = self.assess(headers, persistent)
                self.assertIn(flag, result["indicators"])
                self.assertEqual(result["tier"], "low", result["indicators"])
                self.assertIsNone(result["fingerprintId"])

    def safari_private_tab(self):
        """Safari's fingerprinting protection: canvas and audio salted per tab, so both differ."""
        signals = honest_signals(ua=SAFARI_UA, platform="MacIntel", engine=JAVASCRIPTCORE)
        signals["graphics"]["value"] = json.dumps({"vendor": "Apple Inc.", "renderer": "Apple GPU"})
        signals["iframe"]["value"] = json.dumps([SAFARI_UA, "MacIntel", 8, "Apple GPU", "Europe/Zurich", None])
        signals["canvas"]["value"] = secrets.token_hex(8)
        signals["audio"] = {"status": "unstable"}
        signals["canvasIntegrity"]["value"] = "noise:12"
        return signals

    def test_every_safari_private_tab_shares_one_coarse_print(self):
        tabs = []
        for n in range(3):
            self.signals = self.safari_private_tab()
            tabs.append(fp.assess(f"tab-{n}", evidence(self.signals), {"User-Agent": SAFARI_UA}))
        for tab in tabs:
            self.assertEqual(tab["tier"], "low", tab["indicators"])
            self.assertIn("canvas_noise", tab["indicators"])
            self.assertIsNone(tab["fingerprintId"])
        self.assertEqual({tab["coarsePrint"] for tab in tabs} - {None}, {tabs[0]["coarsePrint"]})
        keys = [fp.device_keys(tab, "203.0.113.7") for tab in tabs]
        # A new key per tab, one network key for all of them.
        self.assertEqual(len({k[0] for k in keys}), 3)
        self.assertEqual(len({k[-1] for k in keys}), 1)
        self.assertTrue(keys[0][-1].startswith("p:"))

    def test_the_network_key_is_bound_to_the_address_and_the_day(self):
        tab = {"tier": "low", "persistent": True, "browserId": "b", "fingerprintId": None, "coarsePrint": "c"}
        here = fp.device_keys(tab, "203.0.113.7")[-1]
        self.assertNotEqual(fp.device_keys(tab, "203.0.113.8")[-1], here)
        self.assertNotEqual(fp.device_keys({**tab, "coarsePrint": "d"}, "203.0.113.7")[-1], here)
        # Privacy addresses rotate the host part of an IPv6 address; the /64 stays.
        self.assertEqual(
            fp.device_keys(tab, "2001:db8:1:2::1")[-1], fp.device_keys(tab, "2001:db8:1:2:aaaa:bbbb:cccc:dddd")[-1]
        )
        self.assertNotEqual(fp.device_keys(tab, "2001:db8:1:2::1")[-1], fp.device_keys(tab, "2001:db8:1:3::1")[-1])
        self.assertEqual(fp.device_keys(tab), ["b:b"])
        with patch("core.fingerprinting.datetime") as clock:
            clock.now.return_value = datetime(2030, 1, 2, tzinfo=UTC)
            self.assertNotEqual(fp.device_keys(tab, "203.0.113.7")[-1], here)

    def test_every_low_browser_gets_a_coarse_print(self):
        self.assertIsNone(self.assess()["coarsePrint"], "high claims on its fingerprint")
        # Whatever made it low: dropping the fingerprint must never leave it without a network key.
        self.signals["audio"] = {"status": "timeout"}
        self.assertIsNotNone(self.assess()["coarsePrint"])
        self.signals = {**honest_signals(), "canvasIntegrity": {"status": "ok", "value": "noise:3"}}
        self.assertIsNotNone(self.assess()["coarsePrint"])
        self.assertIsNotNone(fp.assess("b", evidence(honest_signals(), False), CHROME_HEADERS)["coarsePrint"])
        # Nothing coarse left to read: the user agent alone, still bound to network and day.
        self.signals = {name: {"status": "unavailable"} for name in fp.SIGNALS}
        keyless = fp.assess("b", evidence(self.signals, False), CHROME_HEADERS)
        self.assertEqual(keyless["tier"], "low")
        self.assertTrue(fp.device_keys(keyless, "203.0.113.7")[0].startswith("p:"))
        self.signals = {**honest_signals(), "integrity": {"status": "ok", "value": '["Navigator.userAgent"]'}}
        self.assertEqual(self.assess()["tier"], "suspicious")
        self.assertIsNone(self.assess()["coarsePrint"])

    def test_device_keys_by_tier(self):
        high = {"tier": "high", "persistent": True, "browserId": "b", "fingerprintId": "f"}
        self.assertEqual(fp.device_keys(high), ["b:b", "f:f"])
        self.assertEqual(fp.device_keys({**high, "tier": "low", "fingerprintId": None}), ["b:b"])
        self.assertEqual(fp.device_keys({**high, "tier": "low", "persistent": False}), [])
        # A lie never frees a browser: it keeps its own key, never the fingerprint.
        self.assertEqual(fp.device_keys({**high, "tier": "suspicious"}), ["b:b"])
        self.assertEqual(fp.device_keys({**high, "tier": "suspicious", "coarsePrint": "c"}, "10.0.0.1"), ["b:b"])
        self.assertEqual(fp.device_keys(None), [])

    def test_weighted_similarity_and_frozen_baseline(self):
        first = self.assess()
        self.assertEqual(first["similarity"], 1)
        self.signals["canvas"] = {"status": "unstable"}
        self.signals["audio"] = {"status": "unavailable"}
        self.signals["graphics"]["value"] = '{"renderer": "different"}'
        second = self.assess(previous="browser")
        self.assertLess(second["similarity"], 0.7)
        self.assertIn("fingerprint_changed", second["indicators"])
        self.assertIn("canvas_unstable", second["indicators"])
        self.assertEqual(self.assess(previous="browser")["similarity"], second["similarity"])

    def test_unavailable_does_not_mean_automation_or_known_browser(self):
        self.signals = {name: {"status": "unavailable"} for name in fp.SIGNALS}
        result = self.assess()
        self.assertIsNone(result["similarity"])
        self.assertFalse(result["continuity"])
        self.assertNotIn("automation", result["indicators"])
        self.assertEqual(result["tier"], "low")
        self.assertIsNone(result["fingerprintId"])
        self.assertIsNone(cache.get("browser:baseline:browser"))

    def test_worker_locale_can_differ_but_ua_cpu_and_platform_are_compared(self):
        self.signals["worker"]["value"] = json.dumps([CHROME_UA, 8, "en-US", "Win32"])
        self.assertNotIn("worker_mismatch", self.assess()["indicators"])
        self.signals["worker"]["value"] = json.dumps([CHROME_UA, 8, "en-US", "Linux x86_64"])
        self.assertIn("worker_mismatch", self.assess()["indicators"])

    def test_full_http_flow_csrf_cookies_and_receipt(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post("/api/fingerprint/challenge").status_code, 403)
        request = RequestFactory().get("/")
        token = get_token(request)
        client.cookies["csrftoken"] = request.META["CSRF_COOKIE"]
        headers = {f"HTTP_{name.upper().replace('-', '_')}": value for name, value in CHROME_HEADERS.items()}

        def post(path, data):
            return client.post(
                f"/api/fingerprint/{path}",
                data=json.dumps(data),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=token,
                **headers,
            )

        challenge = post("challenge", {})
        self.assertEqual(challenge.status_code, 200)
        self.assertEqual(challenge.json()["difficulty"], 4)
        self.assertTrue(challenge.cookies[fp.CONTEXT_COOKIE]["httponly"])
        verified = post("verify", self.submission(challenge.json()["challenge"]))
        self.assertEqual(verified.status_code, 200, verified.content)
        # Nothing about the checks goes back: a forger learns only that a receipt exists.
        self.assertEqual(verified.json(), {"expiresIn": fp.RECEIPT_TTL})
        self.assertEqual(verified["Cache-Control"], "no-store")
        self.assertNotIn(b"tier", verified.cookies[fp.RECEIPT_COOKIE].value.encode())
        request.COOKIES = {name: cookie.value for name, cookie in client.cookies.items()}
        assessment = fp.get_browser_assessment(request)
        self.assertEqual(assessment["tier"], "high", assessment["indicators"])
        self.assertFalse(assessment["continuity"])
        post("verify", self.submission(post("challenge", {}).json()["challenge"]))
        request.COOKIES = {name: cookie.value for name, cookie in client.cookies.items()}
        self.assertTrue(fp.get_browser_assessment(request)["continuity"])
        with patch("django.core.signing.time.time", return_value=fp.time.time() + fp.RECEIPT_TTL + 1):
            self.assertIsNone(fp.get_browser_assessment(request))
        request.COOKIES[fp.CONTEXT_COOKIE] = "forged"
        self.assertIsNone(fp.get_browser_assessment(request))
        refused = post("verify", self.submission(post("challenge", {}).json()["challenge"]) | {"pow": "x"})
        self.assertEqual(refused.status_code, 400)
        self.assertNotIn("pow", refused.json()["detail"].lower())

    def test_no_receipt_without_the_replay_guard(self):
        client = Client()
        challenge = client.post("/api/fingerprint/challenge").json()["challenge"]
        data = self.submission(challenge)
        with patch("core.fingerprinting.cache") as down:
            down.add.side_effect = RedisError("down")
            response = client.post("/api/fingerprint/verify", data=json.dumps(data), content_type="application/json")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(fp.RECEIPT_COOKIE, response.cookies)

    def test_rate_limit_and_disable(self):
        client = Client()
        with self.settings(BROWSER_FINGERPRINT_ENABLED=False):
            self.assertEqual(client.post("/api/fingerprint/challenge").status_code, 404)
        for _ in range(30):
            self.assertEqual(client.post("/api/fingerprint/challenge").status_code, 200)
        self.assertEqual(client.post("/api/fingerprint/challenge").status_code, 429)

    def test_rate_limit_fails_open_when_the_cache_is_down(self):
        with patch("core.fingerprinting.cache") as down:
            down.add.side_effect = RedisError("down")
            self.assertTrue(fp.rate_limit("ip", "10.0.0.1", 1))

    def test_receipt_is_context_bound(self):
        response = HttpResponse()
        fp.set_cookie(response, fp.CONTEXT_COOKIE, "original", fp.RETENTION)
        fp.store_receipt(response, "original", {"browserId": "b", "tier": "high"})
        fp.set_cookie(response, fp.CONTEXT_COOKIE, "other", fp.RETENTION)
        request = RequestFactory().get("/")
        request.COOKIES = {name: value.value for name, value in response.cookies.items()}
        self.assertIsNone(fp.get_browser_assessment(request))
        with self.settings(BROWSER_FINGERPRINT_ENABLED=False):
            self.assertIsNone(fp.get_browser_assessment(request))

    def test_oversized_and_invalid_proof_are_rejected(self):
        client = Client()
        self.assertEqual(
            client.post("/api/fingerprint/verify", data="x" * 24001, content_type="application/json").status_code, 413
        )
        data = self.submission(fp.issue_challenge("context", 4))
        with self.assertRaisesRegex(ValueError, "Invalid browser proof"):
            self.verify({**data, "publicKey": "not a key"})
        with self.assertRaisesRegex(ValueError, "Invalid browser proof"):
            self.verify({**data, "signature": base64.b64encode(bytes(64)).decode()})


@override_settings(CACHES=LOCMEM, BROWSER_FINGERPRINT_ENABLED=True, BROWSER_OBSERVE_ONLY=frozenset())
class HardeningTests(SimpleTestCase):
    """The floors, the checks that start in observe mode (enforced here), and the stats."""

    def setUp(self):
        cache.clear()

    def assess(self, signals=None, headers=None, browser="browser"):
        return fp.assess(browser, evidence(signals or honest_signals()), headers or CHROME_HEADERS)

    def test_semantic_diagnostics_never_change_identity_or_tier(self):
        baseline = self.assess()
        for value, indicator in (
            (dict.fromkeys(fp.SEMANTIC_CASES, "pass"), None),
            (dict.fromkeys(fp.SEMANTIC_CASES, "mismatch"), "semantic_alteration"),
            (dict.fromkeys(fp.SEMANTIC_CASES, "unavailable"), None),
            ({"font": ["pass"]}, "invalid_semantics"),
        ):
            with self.subTest(value=value):
                signals = honest_signals()
                signals["semantics"] = {"status": "ok", "value": json.dumps(value)}
                result = self.assess(signals)
                self.assertEqual(result["tier"], baseline["tier"])
                self.assertEqual(result["fingerprintId"], baseline["fingerprintId"])
                self.assertEqual(result["components"], baseline["components"])
                if indicator:
                    self.assertIn(indicator, result["indicators"])
                    fp.record_stats(result)

    def test_optional_semantic_status_and_unknown_signal_validation(self):
        signals = honest_signals()
        signals["semantics"] = {"status": "unavailable"}
        result = self.assess(signals)
        self.assertEqual(result["tier"], "high")
        self.assertNotIn("checks_unavailable", result["indicators"])
        signals["unknown"] = {"status": "unavailable"}
        with self.assertRaises(ValueError):
            evidence(signals)

    def test_common_assessment_keeps_candidate_only_for_debits(self):
        high = self.assess()
        with patch.object(fp, "fingerprint_is_common", return_value=True):
            low = self.assess()
        self.assertEqual(low["tier"], "low")
        self.assertIsNone(low["fingerprintId"])
        self.assertEqual(low["debitFingerprint"], high["fingerprintId"])
        self.assertFalse(any(key.startswith("f:") for key in fp.device_keys(low, "203.0.113.1")))

    def test_ipv6_floors_count_a_56_and_leave_ipv4_alone(self):
        self.assertEqual(fp.ip_floor("203.0.113.7"), "203.0.113.7")
        self.assertEqual(fp.ip_floor("::ffff:203.0.113.7"), "203.0.113.7")
        self.assertEqual(fp.ip_floor("2001:db8:1:2::1"), fp.ip_floor("2001:db8:1:ff:aaaa::1"))
        self.assertNotEqual(fp.ip_floor("2001:db8:1:2::1"), fp.ip_floor("2001:db8:1:100::1"))
        self.assertEqual(fp.ip_floor(None), "-")
        # The network key of a private tab stays per /64.
        self.assertEqual(fp.ip_bucket("2001:db8:1:2::1"), "2001:db8:1:2::/64")

    def test_churn_counts_the_whole_56(self):
        for n in range(fp.CHURN_FREE):
            fp.note_new_browser(f"browser-{n}", f"2001:db8:1:{n:x}::1")
        self.assertGreater(fp.pow_bits("2001:db8:1:ff::9"), settings.BROWSER_POW_BITS)
        self.assertEqual(fp.pow_bits("2001:db8:2::1"), settings.BROWSER_POW_BITS)

    def test_grease_follows_the_chromium_major(self):
        for brands in (
            CHROME_HEADERS["Sec-CH-UA"],
            '"Opera";v="114", "Chromium";v="128", "Not;A=Brand";v="24"',
            '"Samsung Internet";v="27.0", "Chromium";v="125", "Not.A/Brand";v="24"',
            '"Chromium";v="153", "Not_A Brand";v="8", "Google Chrome";v="153"',
        ):
            with self.subTest(brands=brands):
                self.assertTrue(fp._grease_matches(int(fp._brands(brands)["Chromium"]), fp._brands(brands)))
        stale = '"Chromium";v="153", "Not?A_Brand";v="99", "Google Chrome";v="153"'
        self.assertFalse(fp._grease_matches(153, fp._brands(stale)))
        self.assertTrue(fp._grease_matches(110, {}), "before the current GREASE there is nothing to check")
        ua = CHROME_UA.replace("130", "153")
        signals = honest_signals(ua=ua)
        result = self.assess(signals, {**CHROME_HEADERS, "User-Agent": ua, "Sec-CH-UA": stale})
        self.assertIn("client_hints_grease_mismatch", result["indicators"])
        self.assertEqual(result["tier"], "suspicious")

    def test_fetch_metadata_of_a_json_post(self):
        self.assertEqual(self.assess()["indicators"], [])
        for changes in (
            {"Sec-Fetch-Mode": "navigate"},
            {"Sec-Fetch-Dest": "document"},
            {"Sec-Fetch-Site": "cross-site"},
            {"Sec-Fetch-Site": "none"},
            {"Sec-Fetch-User": "?1"},
        ):
            with self.subTest(changes=changes):
                result = self.assess(headers={**CHROME_HEADERS, **changes})
                self.assertIn("fetch_metadata_mismatch", result["indicators"])
                self.assertEqual(result["tier"], "suspicious")
        stripped = {k: v for k, v in CHROME_HEADERS.items() if not k.startswith("Sec-Fetch")}
        result = self.assess(headers=stripped)
        self.assertIn("fetch_metadata_missing", result["indicators"])
        self.assertEqual(result["tier"], "low")
        # Safari before 16.4 sends none.
        old_safari = SAFARI_UA.replace("Version/26.0", "Version/16.3")
        signals = honest_signals(ua=old_safari, platform="MacIntel", engine=JAVASCRIPTCORE)
        self.assertNotIn("fetch_metadata_missing", self.assess(signals, {"User-Agent": old_safari})["indicators"])

    def test_hardware_that_cannot_exist(self):
        mac_ua = CHROME_UA.replace("Windows NT 10.0; Win64; x64", "Macintosh; Intel Mac OS X 10_15_7")
        linux_ua = CHROME_UA.replace("Windows NT 10.0; Win64; x64", "X11; Linux x86_64")
        arm, x86 = ["7fc00000", "7ff80000"], ["ffc00000", "fff80000"]

        def check(ua, renderer, nan, os_name):
            profile = fp.user_agent_profile(ua)
            self.assertEqual(profile["platform"], os_name)
            return fp.graphics_flags(profile, {"renderer": renderer}, ["a", "b", "c", "d", nan])

        d3d = "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"
        metal = "ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)"
        lies = [
            (mac_ua, d3d, x86, "macOS"),
            (linux_ua, d3d, x86, "Linux"),
            (CHROME_UA, metal, arm, "Windows"),
            (mac_ua, "ANGLE (Intel, Intel(R) Iris(TM) Plus Graphics OpenGL Engine)", arm, "macOS"),
        ]
        for ua, renderer, nan, os_name in lies:
            with self.subTest(renderer=renderer, os=os_name):
                self.assertEqual(check(ua, renderer, nan, os_name), ["graphics_platform_mismatch"])
        honest = [
            (CHROME_UA, d3d, x86, "Windows"),
            (mac_ua, metal, arm, "macOS"),
            # An Intel Mac with its own GPU, and Rosetta: x86 maths on an Apple GPU.
            (mac_ua, "ANGLE (Intel, Intel(R) Iris(TM) Plus Graphics OpenGL Engine)", x86, "macOS"),
            (mac_ua, metal, x86, "macOS"),
            # WSL passes D3D12 to a Linux browser; Windows on ARM runs NVIDIA N1X.
            (linux_ua, "D3D12 (NVIDIA GeForce RTX 3060)", x86, "Linux"),
            (CHROME_UA, d3d, arm, "Windows"),
            # Masked strings name no backend.
            (mac_ua, "Apple GPU", arm, "macOS"),
            (CHROME_UA, "Mozilla", x86, "Windows"),
        ]
        for ua, renderer, nan, os_name in honest:
            with self.subTest(renderer=renderer, os=os_name):
                self.assertEqual(check(ua, renderer, nan, os_name), [])
        android = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile"
        self.assertEqual(fp.graphics_flags(fp.user_agent_profile(android), {"renderer": metal}, None), [])

    def test_realm_checks_are_named_by_what_they_caught(self):
        signals = honest_signals()
        signals["integrity"]["value"] = json.dumps(["receiver:Navigator.userAgent", "stack:Screen.width", "frame:nan"])
        indicators = self.assess(signals)["indicators"]
        self.assertIn("realm_tampered", indicators)
        self.assertIn("native_stack_tampered", indicators)
        self.assertNotIn("native_tampered", indicators)

    def test_a_check_that_did_not_run_costs_something(self):
        for name in fp.CHECKS:
            with self.subTest(check=name):
                result = self.assess({**honest_signals(), name: {"status": "unavailable"}})
                self.assertIn("checks_unavailable", result["indicators"])
                self.assertNotEqual(result["tier"], "high")

    def test_observed_indicators_do_not_change_the_tier(self):
        stale = {**CHROME_HEADERS, "Sec-Fetch-Mode": "navigate"}
        with self.settings(BROWSER_OBSERVE_ONLY=frozenset({"fetch_metadata_mismatch"})):
            result = self.assess(headers=stale)
            self.assertIn("fetch_metadata_mismatch", result["indicators"])
            self.assertEqual(result["tier"], "high")
            self.assertIsNotNone(result["fingerprintId"])
        self.assertEqual(self.assess(headers=stale)["tier"], "suspicious")

    def test_a_fingerprint_on_many_networks_or_keys_is_common(self):
        fp.note_fingerprint("fid", "one", "203.0.113.7")
        for n in range(5):
            fp.note_fingerprint("fid", "one", f"198.51.{n}.1")  # one device travelling adds nothing
        self.assertFalse(fp.fingerprint_is_common("fid"))
        fp.note_fingerprint("fid", "two", "198.51.100.1")
        self.assertFalse(fp.fingerprint_is_common("fid"))
        fp.note_fingerprint("fid", "three", "192.0.2.1")
        self.assertTrue(fp.fingerprint_is_common("fid"))
        # Five new keys within an hour on one network: a cloned profile.
        for n in range(fp.COMMON_BURST):
            self.assertFalse(fp.fingerprint_is_common("clone"))
            fp.note_fingerprint("clone", f"key-{n}", "203.0.113.9")
        self.assertTrue(fp.fingerprint_is_common("clone"))
        result = self.assess()
        fp.note_fingerprint(result["fingerprintId"], "x", "10.0.0.1")
        cache.set(f"browser:fp-common:{result['fingerprintId']}", 1)
        common = self.assess()
        self.assertIn("fingerprint_common", common["indicators"])
        self.assertEqual(common["tier"], "low")
        # The fingerprint's claim goes, a network key takes its place.
        self.assertTrue(fp.device_keys(common, "203.0.113.7")[-1].startswith("p:"))

    def test_stats_count_tiers_indicators_and_solo_lies(self):
        observed = frozenset({"fetch_metadata_mismatch", "client_hints_grease_mismatch"})
        with self.settings(BROWSER_OBSERVE_ONLY=observed):
            fp.record_stats({"tier": "high", "indicators": ["fetch_metadata_mismatch"]})
            fp.record_stats({"tier": "suspicious", "indicators": ["fetch_metadata_mismatch", "automation"]})
            fp.record_stats({"tier": "suspicious", "indicators": ["fetch_metadata_mismatch", "native_tampered"]})
            # Two observed lies together (a header-rewriting proxy): neither hides the other.
            fp.record_stats({"tier": "high", "indicators": ["fetch_metadata_mismatch", "client_hints_grease_mismatch"]})
            fp.record_stats({"tier": "high", "indicators": ["not-a-known-name"]})
            fp.count_stat("refused:pow")
            day = fp._today().isoformat()
            counts = fp.read_stats()[day]
        self.assertEqual(counts["tier:high"], 3)
        self.assertEqual(counts["ind:fetch_metadata_mismatch"], 4)
        self.assertEqual(counts["solo:fetch_metadata_mismatch"], 2)
        self.assertEqual(counts["solo:client_hints_grease_mismatch"], 1)
        self.assertEqual(counts["refused:pow"], 1)
        self.assertFalse(any("not-a-known-name" in name for name in counts))

    def test_stats_fail_open(self):
        with patch("core.fingerprinting.cache") as down:
            down.add.side_effect = RedisError("down")
            down.get_many.side_effect = RedisError("down")
            fp.count_stat("tier:high")
            self.assertEqual(fp.read_stats(), {})

    def test_a_key_is_established_once_it_is_old_enough(self):
        with patch("core.fingerprinting._now", return_value=1_000_000.0):
            first = fp.note_new_browser("young", "10.0.0.1")
            self.assertEqual(first, 1_000_000)
            self.assertFalse(
                fp.assess("young", evidence(honest_signals()), CHROME_HEADERS, first_seen=first)["established"]
            )
        later = 1_000_000.0 + settings.BROWSER_KEY_AGE
        with patch("core.fingerprinting._now", return_value=later):
            self.assertEqual(fp.note_new_browser("young", "10.0.0.1"), first, "the first sight stays")
            self.assertTrue(
                fp.assess("young", evidence(honest_signals()), CHROME_HEADERS, first_seen=first)["established"]
            )
        # A marker from before the key age held 1: long established.
        cache.set("browser:seen:legacy", 1)
        self.assertTrue(
            fp.assess(
                "legacy",
                evidence(honest_signals()),
                CHROME_HEADERS,
                first_seen=fp.note_new_browser("legacy", "10.0.0.1"),
            )["established"]
        )
        self.assertTrue(fp.is_established({"tier": "high"}), "receipts from before the key age")
        self.assertFalse(fp.is_established(None))

    def test_the_key_age_fails_open(self):
        with patch("core.fingerprinting.cache") as down:
            down.add.side_effect = RedisError("down")
            self.assertIsNone(fp.note_new_browser("any", "10.0.0.1"))
        self.assertTrue(fp.assess("any", evidence(honest_signals()), CHROME_HEADERS, first_seen=None)["established"])

    def held_on_the_ip(self, address="2001:db8:1:2::1", **recognised):
        """The IP counters a sign-up from ``address`` is held to, for a browser recognised as given."""
        client = Client()
        if recognised:
            recognise(client, **recognised)
        request = RequestFactory().post("/api/allauth/browser/v1/auth/signup", REMOTE_ADDR=address)
        request.COOKIES.update({name: morsel.value for name, morsel in client.cookies.items()})
        return [key for key, _, _ in device_throttle.counters(request) if ":ip:" in key]

    def test_the_signup_throttle_never_frees_a_liar_a_keyless_or_a_young_key(self):
        self.assertEqual(self.held_on_the_ip(tier="high"), [])
        self.assertEqual(len(self.held_on_the_ip(tier="suspicious")), 1, "a lie pays the IP's count")
        self.assertEqual(len(self.held_on_the_ip(tier="low", persistent=False)), 1, "no key: the IP's count")
        self.assertEqual(len(self.held_on_the_ip()), 1, "unknown")
        self.assertEqual(self.held_on_the_ip(tier="high", established=False), [], "votes only, by default")
        with self.settings(BROWSER_KEY_AGE_SIGNUPS=True):
            self.assertEqual(len(self.held_on_the_ip(tier="high", established=False)), 1)
        # One /56 is one count; IPv4 keys are as they were.
        self.assertEqual(self.held_on_the_ip(), self.held_on_the_ip("2001:db8:1:ff::7"))
        self.assertNotEqual(self.held_on_the_ip(), self.held_on_the_ip("2001:db8:1:100::7"))
        self.assertIn(fp.keyed_id("auth", "10.0.0.1"), self.held_on_the_ip("10.0.0.1")[0])

    def test_refusals_carry_a_reason_code(self):
        with self.assertRaises(fp.ProofRefusedError) as refused:
            fp.verify_proof("not-a-challenge", "context", "{}", "", "", "0")
        self.assertEqual(refused.exception.reason, "challenge")
        self.assertIn(refused.exception.reason, fp.REFUSALS)
        self.assertTrue(fp.is_class_e("240.1.2.3"))
        self.assertFalse(fp.is_class_e("203.0.113.7"))


@override_settings(
    CACHES=LOCMEM,
    BROWSER_FINGERPRINT_ENABLED=True,
    BROWSER_POW_BITS=4,
    BROWSER_PATH_METER="observe",
    BROWSER_PATH_DIRECT_MS=25,
    BROWSER_PATH_RELAYED_MS=60,
)
class RelayMeterTests(SimpleTestCase):
    """The echo chain and the path verdict; observed only, never a tier."""

    setUp = FingerprintTests.setUp
    payload = FingerprintTests.payload
    submission = FingerprintTests.submission

    def seed_floor(self, colo="ZRH", sample=40):
        cache.set(f"browser:colo-floor:{colo}", [sample] * fp.COLO_MIN_SAMPLES)

    def test_the_edge_header_is_read_strictly(self):
        self.assertEqual(fp._edge("12,0,3303"), (12, 0, 3303))
        self.assertEqual(fp._edge("0,9,3303"), (0, 9, 3303))
        for value in ("0,0,3303", "12,0", "a,b,c", "", "12,0,3303,1"):
            self.assertIsNone(fp._edge(value), value)

    def test_the_chain_needs_every_token_in_order(self):
        challenge = fp.issue_challenge("context", 4)
        with patch("core.fingerprinting._now", return_value=1000.0):
            first = fp.echo(challenge, "context", 1, "", {"X-Ml-Edge": "20,0,3303", "CF-Ray": "abc-ZRH"})
        with self.assertRaises(fp.ProofRefusedError):
            fp.echo(challenge, "context", 2, "wrong", {})
        with self.assertRaises(fp.ProofRefusedError):
            fp.echo(challenge, "context", 3, first, {})
        with self.assertRaises(fp.ProofRefusedError):
            fp.echo(challenge, "other", 2, first, {})
        with patch("core.fingerprinting._now", return_value=1000.07):
            second = fp.echo(challenge, "context", 2, first, {})
        with patch("core.fingerprinting._now", return_value=1000.13):
            fp.echo(challenge, "context", 3, second, {})
        record = fp.take_echo(challenge)
        self.assertEqual(record["rtts"], [70, 60])
        self.assertEqual((record["edge"], record["colo"]), ((20, 0, 3303), "ZRH"))
        self.assertIsNone(fp.take_echo(challenge), "taken once")

    def test_the_verdict_follows_the_excess_over_the_edge(self):
        self.seed_floor()
        record = {"rtts": [80, 60], "edge": (20, 0, 3303), "colo": "ZRH"}
        self.assertEqual(fp.path_verdict(record), ("direct", "tcp", 0))
        # The bot's own leg behind a proxy exit: 90 ms the edge never saw.
        self.assertEqual(fp.path_verdict({**record, "rtts": [150]})[0], "relayed")
        self.assertEqual(fp.path_verdict({**record, "rtts": [100]})[0], "unclear")
        self.assertEqual(fp.path_verdict({**record, "edge": (0, 20, 3303)})[1], "quic")
        with self.settings(BROWSER_PATH_DIRECT_MS=None, BROWSER_PATH_RELAYED_MS=None):
            self.assertEqual(fp.path_verdict(record)[0], "unclear", "no thresholds before gate G1")
        cache.clear()
        self.assertEqual(fp.path_verdict(record), ("unclear", "tcp", None), "too few samples at this site")
        self.assertEqual(fp.path_verdict(None)[0], "unmeasured")
        self.assertEqual(fp.path_verdict({"rtts": [60]})[0], "unmeasured", "no edge header")
        with self.settings(BROWSER_PATH_METER="off"):
            self.assertEqual(fp.path_verdict(record)[0], "unmeasured")

    def test_the_receipt_keeps_no_round_trip(self):
        self.seed_floor()
        fields = fp.path_fields({"rtts": [83, 61], "edge": (20, 0, 3303), "colo": "ZRH"}, "203.0.113.7")
        self.assertEqual(fields["path"], "direct")
        self.assertEqual(fields["pathExcess"], 0)
        self.assertEqual(fields["pathBucket"], fp.keyed_id("path", "203.0.113.7"))
        self.assertEqual(fields["asnKey"], fp.keyed_id("asn", "3303"))
        self.assertFalse({61, 83, 3303} & {value for value in fields.values() if isinstance(value, int)})

    def test_the_http_flow_measures_without_saying_so(self):
        self.seed_floor()
        client = Client(enforce_csrf_checks=True)
        request = RequestFactory().get("/")
        token = get_token(request)
        client.cookies["csrftoken"] = request.META["CSRF_COOKIE"]
        headers = {f"HTTP_{name.upper().replace('-', '_')}": value for name, value in CHROME_HEADERS.items()}
        headers |= {"HTTP_X_ML_EDGE": "20,0,3303", "HTTP_CF_RAY": "abc-ZRH"}

        def post(path, data):
            return client.post(
                f"/api/fingerprint/{path}",
                data=json.dumps(data),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=token,
                **headers,
            )

        issued = post("challenge", {}).json()
        self.assertTrue(issued["echo"])
        step_token = ""
        for step in range(1, fp.ECHO_STEPS + 1):
            reply = post("echo", {"challenge": issued["challenge"], "step": step, "token": step_token})
            self.assertEqual(reply.status_code, 200, reply.content)
            self.assertEqual(set(reply.json()), {"token"})
            step_token = reply.json()["token"]
        self.assertEqual(post("echo", {"challenge": issued["challenge"], "step": 1, "token": ""}).status_code, 400)
        submission = self.submission(issued["challenge"])
        self.assertEqual(post("verify", submission).json(), {"expiresIn": fp.RECEIPT_TTL})
        request.COOKIES = {name: cookie.value for name, cookie in client.cookies.items()}
        assessment = fp.get_browser_assessment(request)
        self.assertIn(assessment["path"], {"direct", "unclear"})
        self.assertEqual(assessment["tier"], "high", "the path never changes a tier")
        with self.settings(BROWSER_PATH_METER="off"):
            self.assertFalse(post("challenge", {}).json()["echo"])
            self.assertEqual(post("echo", {"challenge": issued["challenge"], "step": 1}).status_code, 400)


# The claims themselves; with the blind ledger off, a refused claim still answers 429.
@override_settings(CACHES=LOCMEM, BROWSER_FINGERPRINT_ENABLED=True, COVERAGE_BLIND_LEDGER=False)
class DeviceVoteTests(TestCase):
    def setUp(self):
        cache.clear()

    def vote(self, client, code, ip):
        return client.put(f"/api/coverage/{code}/vote", REMOTE_ADDR=ip)

    def test_a_recognised_browser_votes_once_whatever_its_cookie_or_network(self):
        first, second = Client(), Client()
        recognise(first, "high", browser="one", fingerprint="device")
        self.assertEqual(self.vote(first, "DE", "10.0.0.1").status_code, 200)
        # Storage cleared (new key and cookie), other network, same device.
        recognise(second, "high", browser="two", fingerprint="device")
        refused = self.vote(second, "DE", "10.0.0.2")
        self.assertEqual(refused.status_code, 429)
        self.assertIn("Browser", refused.json()["detail"])
        self.assertEqual(self.vote(second, "FR", "10.0.0.2").status_code, 200)

    def test_look_alike_devices_both_vote(self):
        # Two iPhones of one model share every probe value; only their keys differ.
        for n in range(2):
            client = Client()
            recognise(client, "low", browser=f"iphone-{n}", fingerprint="same-model")
            self.assertEqual(self.vote(client, "DE", f"10.0.1.{n}").status_code, 200)

    def test_private_tabs_of_one_browser_vote_once_per_network(self):
        for n in range(2):
            client = Client()
            recognise(client, "low", browser=f"tab-{n}", coarse="same-safari")
            self.assertEqual(self.vote(client, "DE", "10.0.8.1").status_code, 200 if n == 0 else 429, n)
        # A look-alike Mac on another network is another device.
        other = Client()
        recognise(other, "low", browser="elsewhere", coarse="same-safari")
        self.assertEqual(self.vote(other, "DE", "10.0.8.2").status_code, 200)

    def test_unknown_browsers_share_a_small_daily_count_per_ip(self):
        for n, code in enumerate(["DE", "FR", "IT"]):
            self.assertEqual(self.vote(Client(), code, "10.0.2.1").status_code, 200, n)
        refused = self.vote(Client(), "AT", "10.0.2.1")
        self.assertEqual(refused.status_code, 429)
        self.assertEqual(self.vote(Client(), "AT", "10.0.2.2").status_code, 200)
        suspicious = Client()
        recognise(suspicious, "suspicious")
        self.assertEqual(self.vote(suspicious, "ES", "10.0.2.1").status_code, 429)

    def test_a_refused_ip_claim_leaves_no_device_claim(self):
        first, second = Client(), Client()
        recognise(first, "high", browser="one", fingerprint="first-device")
        recognise(second, "high", browser="two", fingerprint="second-device")
        self.assertEqual(self.vote(first, "DE", "10.0.3.1").status_code, 200)
        self.assertEqual(self.vote(second, "DE", "10.0.3.1").status_code, 429)
        self.assertEqual(self.vote(second, "DE", "10.0.3.2").status_code, 200)

    def test_withdrawing_releases_the_claim_after_the_receipt_expired(self):
        client = Client()
        recognise(client, "high", browser="one", fingerprint="device")
        self.assertEqual(self.vote(client, "DE", "10.0.4.1").status_code, 200)
        del client.cookies[fp.RECEIPT_COOKIE]
        self.assertEqual(client.delete("/api/coverage/DE/vote", REMOTE_ADDR="10.0.4.1").status_code, 200)
        other = Client()
        recognise(other, "high", browser="two", fingerprint="device")
        self.assertEqual(self.vote(other, "DE", "10.0.4.2").status_code, 200)

    def test_unknown_withdrawal_refunds_the_daily_count(self):
        client = Client()
        for _ in range(5):
            self.assertEqual(self.vote(client, "DE", "10.0.5.1").status_code, 200)
            self.assertEqual(client.delete("/api/coverage/DE/vote", REMOTE_ADDR="10.0.5.1").status_code, 200)

    def test_switched_off_votes_behave_as_before(self):
        with self.settings(BROWSER_FINGERPRINT_ENABLED=False):
            for code in ["DE", "FR", "IT", "AT"]:
                self.assertEqual(self.vote(Client(), code, "10.0.6.1").status_code, 200)

    def test_the_claim_fails_open(self):
        with patch("core.coverage.cache") as down:
            down.add.side_effect = RedisError("down")
            self.assertIsNone(coverage.claim_device_vote("anon:x", None, "10.0.7.1", "DE"))

    def test_a_young_key_also_pays_the_ip_count(self):
        # Fresh keys from one address share its small daily count, whatever they claim.
        for n, code in enumerate(["DE", "FR", "IT"]):
            client = Client()
            recognise(client, "high", browser=f"young-{n}", fingerprint=f"young-{n}", established=False)
            self.assertEqual(self.vote(client, code, "10.0.10.1").status_code, 200, n)
        fresh = Client()
        recognise(fresh, "high", browser="young-4", fingerprint="young-4", established=False)
        self.assertEqual(self.vote(fresh, "AT", "10.0.10.1").status_code, 429)
        # An established key on the same address is not held by that count.
        old = Client()
        recognise(old, "high", browser="old", fingerprint="old-device")
        self.assertEqual(self.vote(old, "AT", "10.0.10.1").status_code, 200)

    def test_the_blind_ledger_answers_a_refused_claim_like_a_counted_one(self):
        first, second = Client(), Client()
        recognise(first, "high", browser="one", fingerprint="device")
        recognise(second, "high", browser="two", fingerprint="device")
        with self.settings(COVERAGE_BLIND_LEDGER=True):
            counted, refused = self.vote(first, "DE", "10.0.9.1"), self.vote(second, "DE", "10.0.9.2")
        self.assertEqual((counted.status_code, counted.json()), (refused.status_code, refused.json()))
        self.assertEqual(
            sorted(CoverageVote.objects.filter(area_code="DE").values_list("accepted", flat=True)), [False, True]
        )


@override_settings(CACHES=LOCMEM, BROWSER_FINGERPRINT_ENABLED=True)
class DeviceThrottleTests(TestCase):
    def setUp(self):
        cache.clear()

    def signup(self, client, n, ip):
        return client.post(
            "/api/allauth/browser/v1/auth/signup",
            data=json.dumps({"email": f"rider{n}@example.com"}),
            content_type="application/json",
            REMOTE_ADDR=ip,
        )

    def test_a_recognised_browser_starts_three_signups_a_day_on_any_network(self):
        client = Client()
        recognise(client, "high", browser="one", fingerprint="device")
        for n in range(3):
            self.assertNotEqual(self.signup(client, n, f"10.1.0.{n}").status_code, 429)
        refused = self.signup(client, 3, "10.1.0.9")
        self.assertEqual(refused.status_code, 429)
        self.assertIn("detail", refused.json())
        # A new key in the same device still counts against its fingerprint.
        fresh = Client()
        recognise(fresh, "high", browser="two", fingerprint="device")
        self.assertEqual(self.signup(fresh, 4, "10.1.0.10").status_code, 429)

    def test_a_new_private_tab_does_not_start_afresh(self):
        # The network key gets the IP's allowance, not a device's.
        allowance = device_throttle.RULES["/api/allauth/browser/v1/auth/signup"][3]
        for n in range(allowance):
            client = Client()
            recognise(client, "low", browser=f"tab-{n}", coarse="same-safari")
            self.assertNotEqual(self.signup(client, n, "10.1.3.1").status_code, 429, n)
        fresh = Client()
        recognise(fresh, "low", browser="tab-new", coarse="same-safari")
        self.assertEqual(self.signup(fresh, allowance, "10.1.3.1").status_code, 429)

    def test_the_refusal_count_does_not_reveal_the_tier(self):
        _, _, per_key, per_ip = device_throttle.RULES["/api/allauth/browser/v1/auth/signup"]
        self.assertEqual(per_key, per_ip)

    def test_unknown_browsers_share_a_count_per_ip(self):
        allowance = device_throttle.RULES["/api/allauth/browser/v1/auth/signup"][3]
        for n in range(allowance):
            self.assertNotEqual(self.signup(Client(), n, "10.1.1.1").status_code, 429)
        self.assertEqual(self.signup(Client(), allowance, "10.1.1.1").status_code, 429)
        self.assertNotEqual(self.signup(Client(), allowance + 1, "10.1.1.2").status_code, 429)

    def test_a_refused_request_costs_nothing(self):
        client = Client()
        recognise(client, "high", browser="one", fingerprint="device")
        for n in range(5):
            response = client.post(
                "/api/allauth/browser/v1/auth/signup",
                data=json.dumps({"email": f"not an address {n}"}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 400)
        for n in range(3):
            self.assertNotEqual(self.signup(client, n, "10.1.2.1").status_code, 429)
        self.assertEqual(self.signup(client, 3, "10.1.2.1").status_code, 429)

    def test_only_the_mail_sending_posts_are_held(self):
        factory = RequestFactory()
        middleware = device_throttle.DeviceThrottleMiddleware(lambda request: HttpResponse(status=401))
        with patch.dict(device_throttle.RULES, {"/api/allauth/browser/v1/auth/code/request": ("code", 3600, 1, 1)}):
            request = factory.post("/api/allauth/browser/v1/auth/code/request")
            self.assertEqual(middleware(request).status_code, 401)
            self.assertEqual(middleware(request).status_code, 429)
            for other in (
                factory.get("/api/allauth/browser/v1/auth/code/request"),
                factory.post("/api/allauth/browser/v1/auth/code/confirm"),
                factory.post("/api/allauth/browser/v1/auth/login"),
            ):
                self.assertEqual(device_throttle.counters(other), [])
            with self.settings(BROWSER_FINGERPRINT_ENABLED=False):
                self.assertEqual(middleware(request).status_code, 401)

    def test_it_fails_open(self):
        request = RequestFactory().post("/api/allauth/browser/v1/auth/signup")
        middleware = device_throttle.DeviceThrottleMiddleware(lambda request: HttpResponse(status=401))
        with patch("core.auth.device_throttle.cache") as down:
            down.get.side_effect = RedisError("down")
            down.add.side_effect = RedisError("down")
            self.assertEqual(middleware(request).status_code, 401)


@override_settings(CACHES=LOCMEM, BROWSER_FINGERPRINT_ENABLED=True, BROWSER_KEY_AGE_SIGNUPS=False)
class DebitLeaseTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def held(self, assessment, ip="203.0.113.1", path="signup", now=100000):
        request = self.factory.post(f"/api/allauth/browser/v1/auth/{path}", REMOTE_ADDR=ip)
        with (
            patch.object(fp, "get_browser_assessment", return_value=assessment),
            patch("core.auth.device_throttle.time.time", return_value=now),
        ):
            return device_throttle.counters(request)

    def assessment(self, browser, common=False):
        return {
            "browserId": browser,
            "persistent": True,
            "established": True,
            "tier": "low" if common else "high",
            "fingerprintId": None if common else "device",
            "debitFingerprint": "device" if common else None,
            "coarsePrint": "coarse" if common else None,
            "indicators": ["fingerprint_common"] if common else [],
        }

    def test_already_spent_debit_survives_new_keys_and_networks(self):
        high = self.held(self.assessment("old"))
        device_throttle.count(high)
        device_throttle.count(high)
        low = self.assessment("new", common=True)
        held = self.held(low, "198.51.100.1")
        self.assertEqual(len(held), 3)
        self.assertFalse(device_throttle.refused(held))
        device_throttle.count(held)
        fresh = self.held(self.assessment("fresh", common=True), "192.0.2.1")
        self.assertTrue(device_throttle.refused(fresh))
        self.assertEqual(len(self.held(low, now=200000)), 2)
        self.assertFalse(device_throttle.refused(self.held(low, now=200000)))
        self.assertEqual(len(self.held(low, path="code/request")), 2)

    def test_initially_common_print_cannot_create_a_lease(self):
        low = self.assessment("low", common=True)
        for n in range(4):
            held = self.held({**low, "browserId": f"low-{n}"}, f"192.0.2.{n}")
            self.assertEqual(len(held), 2)
            device_throttle.count(held)
        self.assertEqual(len(self.held(low)), 2)

    def test_code_lease_is_rule_and_slot_scoped(self):
        high = self.held(self.assessment("old"), path="code/request")
        for _ in range(10):
            device_throttle.count(high)
        low = self.assessment("low", common=True)
        self.assertTrue(device_throttle.refused(self.held(low, path="code/request")))
        self.assertEqual(len(self.held(low)), 2)
        self.assertEqual(len(self.held(low, path="code/request", now=104000)), 2)

    def test_failed_cache_lookup_does_not_hold_mail(self):
        with patch("core.auth.device_throttle.cache.get", side_effect=RedisError("down")):
            self.assertEqual(len(self.held(self.assessment("low", common=True))), 2)


@skipUnless(os.environ.get("BROWSER_FINGERPRINT_REDIS_TEST_URL"), "Opt-in shared Redis integration test")
class RedisFingerprintTests(SimpleTestCase):
    def test_nonce_claim_is_atomic_between_independent_cache_clients(self):
        location = os.environ["BROWSER_FINGERPRINT_REDIS_TEST_URL"]
        first = RedisCache(location, {})
        second = RedisCache(location, {})
        # Never flush the user's cache: this test owns exactly one random, short-lived key.
        key = f"browser:test:{secrets.token_hex(24)}"
        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                winners = list(pool.map(lambda i: (first if i % 2 else second).add(key, True, timeout=10), range(32)))
            self.assertEqual(sum(winners), 1)
        finally:
            first.delete(key)
