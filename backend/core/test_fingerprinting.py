import base64
import hashlib
import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
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

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.0 Mobile/15E148 Safari/604.1"
)
CHROME_HEADERS = {
    "User-Agent": CHROME_UA,
    "Sec-CH-UA": '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
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


def recognise(client, tier, browser="browser", fingerprint="fingerprint", persistent=True):
    """Give a test client a stored receipt, as a verified browser of ``tier`` would have."""
    response = HttpResponse()
    context = f"context-{browser}"
    fp.set_cookie(response, fp.CONTEXT_COOKIE, context, fp.RETENTION)
    assessment = {
        "browserId": browser,
        "persistent": persistent,
        "fingerprintId": fingerprint if tier == "high" else None,
        "tier": tier,
        "continuity": False,
        "similarity": None,
        "indicators": [],
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

    def test_device_keys_by_tier(self):
        high = {"tier": "high", "persistent": True, "browserId": "b", "fingerprintId": "f"}
        self.assertEqual(fp.device_keys(high), ["b:b", "f:f"])
        self.assertEqual(fp.device_keys({**high, "tier": "low", "fingerprintId": None}), ["b:b"])
        self.assertEqual(fp.device_keys({**high, "tier": "low", "persistent": False}), [])
        self.assertEqual(fp.device_keys({**high, "tier": "suspicious"}), [])
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


@override_settings(CACHES=LOCMEM, BROWSER_FINGERPRINT_ENABLED=True)
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

    def test_unknown_browsers_share_a_count_per_ip(self):
        for n in range(5):
            self.assertNotEqual(self.signup(Client(), n, "10.1.1.1").status_code, 429)
        self.assertEqual(self.signup(Client(), 5, "10.1.1.1").status_code, 429)
        self.assertNotEqual(self.signup(Client(), 6, "10.1.1.2").status_code, 429)

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
