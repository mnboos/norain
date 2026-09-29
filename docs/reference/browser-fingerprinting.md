# Browser recognition

MeteoLane owns the implementation in `../../frontend/src/lib/browser-fingerprint` and
`backend/core/fingerprinting.py`. It uses no fingerprinting library. Web Crypto and
Python `cryptography` provide the signatures. SHA-256 for the proof-of-work is written
out in `sha256.ts`.

It serves two purposes:

- deduplicating anonymous coverage votes (`core/coverage.py`);
- throttling sign-ups and sign-in code requests per browser (`core/auth/device_throttle.py`).

It never signs anyone in or grants an entitlement. When it fails, the browser counts as
unrecognised; it is never locked out.

The SPA only collects signals where one of those two actions may follow: the coverage page
for anonymous visitors, and the sign-in/sign-up forms. `services/browserRecognition.ts`
starts in the background when those mount. It waits at most 6 s before the vote or sign-up
request, and reuses a receipt for 12 minutes.

## What the server can and cannot know

Every probe value is written by the client, so the server cannot tell a script that invents
a consistent set of values from a real browser. No browser-side technique can. What this
library does instead:

- **Makes each identity cost something.** Every challenge needs a SHA-256 proof-of-work
  (`BROWSER_POW_BITS`, 16 bits by default). It costs more, up to 20 bits, once one IP has
  presented more than 10 keys the server had not seen that hour. Measured with the JS solver
  (~1.25 M hashes/s on a laptop, a phone about 5× slower), 16 bits takes ~0.05 s / ~0.3 s and
  20 bits ~0.8 s / ~4 s on average. Against native code on a GPU no proof-of-work a phone can
  afford is expensive; its value is against browser-driven farms and in the churn escalation.
- **Catches in-browser spoofing.** Extensions, devtools overrides and automation frameworks
  change values in the page's own JavaScript realm. The library compares that realm with a
  worker and with a fresh same-origin iframe. It inspects getters through the iframe's clean
  `Function.prototype.toString`. It checks the canvas for injected noise. It reads the engine
  from its own error messages and compares that with the user agent. It also compares the
  user agent with the `Sec-CH-UA*` headers the server receives itself.
- **Keeps its conclusions to itself.** The `verify` reply carries only a lifetime. The
  assessment is kept in the cache, and the HttpOnly receipt cookie holds only a random id
  that points to it. A forger learns nothing about which check it failed.
- **Never trusts a look-alike.** Two devices of one model (iPhones, Tor, Firefox's
  resistFingerprinting, software rendering) report identical values. Such browsers are
  `low` and never deduplicate on their fingerprint, only on their own key.

A scripted forger is therefore bounded by cost and by the per-IP limits, which all remain.
An extension that patches every frame and every worker could also pass the lie checks. That
is the ceiling of any check that runs inside the browser it is checking.

## Protocol

1. `POST /api/fingerprint/challenge` returns `{challenge, expiresIn, difficulty}`.
   - The challenge is signed, single-use and valid for 120 s.
   - It is bound to an HttpOnly browser-context cookie.
   - The difficulty is signed into it.
2. The client collects its signals and builds `payload = JSON.stringify({version: 2, persistent, signals})`.
3. The client solves the proof-of-work:
   - `seed = SHA-256(challenge + "\n" + SHA-256(payload))`, as hex;
   - `pow` is the smallest decimal `n` for which `SHA-256(seed + ":" + n)` has `difficulty`
     leading zero bits.
   - The seed is exactly one block, so each attempt costs one compression.
   - The solve runs in a module worker, falling back to the main thread in chunks.
   - `__tests__/pow-vector.json` pins the encoding for both sides.
4. The client signs `challenge + "\n" + payload + "\n" + pow` with its P-256 key (ECDSA/SHA-256).
   The key is non-exportable and kept in IndexedDB.
5. `POST /api/fingerprint/verify` with `{challenge, payload, publicKey, signature, pow}`.
   - The server checks, in order: the challenge's signature and age, the context binding,
     the proof-of-work, the evidence schema, and the signature.
   - Only then does it spend the nonce (`cache.add`, atomic across workers).
   - It stores the assessment and answers `{expiresIn}`.
   - On any failure it answers a generic 400; the reason is logged only.

`browserId` is an HMAC of the public key. `fingerprintId` is an HMAC of the stable probe
hashes, and exists only for `high` browsers.

## Signals

| Group | Weighted | In `fingerprintId` | What |
|---|---|---|---|
| canvas | 3 | unless noisy | text, emoji and blending, drawn twice |
| audio | 2 | yes | an OfflineAudioContext oscillator through a compressor |
| graphics | 3 | yes | WebGL vendor/renderer, limits, extensions |
| fonts | 2 | yes | widths of a fixed font list |
| hardware | 2 | yes | CPUs, touch points, device memory |
| math | 1 | yes | results of a few `Math` functions |
| media | 1 | yes | `canPlayType` for three codecs |
| display | 1 | no | screen size and colour depth (no DPR: it follows the zoom) |
| locale | 1 | no | time zone, numbering system (no languages: a setting) |

The checks feed indicators only: `navigator`, `worker`, `iframe`, `integrity`,
`canvasIntegrity`, `engine` and `automation`.

## Tiers

- `suspicious`: any lie. That is one of `user_agent_mismatch`, `worker_mismatch`,
  `iframe_mismatch`, `native_tampered`, `engine_mismatch`, `client_hints_mismatch`,
  `automation`, or malformed check values.
- `low`: honest but not distinctive, or degraded. That is `canvas_noise`,
  `iframe_canvas_mismatch`, `software_renderer`, `ios`, `client_hints_missing`,
  `ephemeral_key`, `low_signal_coverage` or `checks_unavailable`. A canvas that differs
  only between page and iframe counts as noise, not a lie: a script that patches the canvas
  already shows as `native_tampered`.
- `high`: everything else.

`core.fingerprinting.device_keys` turns an assessment into claim keys:

| Tier | Claim keys |
|---|---|
| `high` | its key and its fingerprint |
| `low` | its key, when that key is persistent |
| `suspicious` or unknown | none; these share a small per-IP count instead |

## Where it is used

**Anonymous coverage votes.** These stack on the cookie and the per-IP limits, never
replacing them.

- A browser with claim keys votes once per area for 30 days, whatever its cookie or network.
- Unknown or suspicious browsers share 3 votes per IP a day.
- The device claim is made before the IP claim, and released again if the IP claim fails.
- Withdrawing a vote releases exactly what was claimed, recorded per voter.

**Sign-up and sign-in codes.** This is a middleware in front of allauth.

| Browser | Sign-ups | Code requests |
|---|---|---|
| Recognised | 3 a day per claim key | 10 an hour per claim key |
| Unknown or suspicious | 5 a day per IP | 10 an hour per IP |

- Only requests allauth accepted (200 or 401) are counted, so a mistyped address costs nothing.
- A refusal is answered as JSON `{detail}` with status 429.
- Password sign-in and code confirmation are not affected.

## Data and operation

| What | Where | How long |
|---|---|---|
| Receipts | Redis | 15 minutes |
| Baselines (keyed probe hashes) | Redis | 30 days |
| "Seen" markers and context-to-key links | Redis | 30 days |
| Used nonces | Redis | 130 s |
| Churn and rate counters | Redis | about an hour |
| Vote claims | Redis | 30 days |

- Raw probe values are never stored or logged. The log carries tier and indicator names.
- Nothing is stored in Postgres, and no migration is needed.
- No GPS, contacts, history, permissions, installed-font enumeration or third-party service
  is used.
- All limits fail open when Redis is unavailable.
- Deleting site data removes the cookies and the key.

Configuration:

- `BROWSER_FINGERPRINT_ENABLED=false` (backend) turns the feature off. Votes and sign-ups
  then behave exactly as before.
- `VITE_BROWSER_FINGERPRINT_ENABLED=false` (frontend build) stops collection. Rebuild the
  frontend after changing it.
- `BROWSER_POW_BITS` sets the base difficulty.
- Caddy's `fingerprint` zone (`../../deploy/auth-ratelimit.caddy`) stops floods of the two
  endpoints.
- A `SECRET_KEY` rotation changes every HMAC identifier.

## Verification

On a running site, an admin can open `/system` and use the "Browser-Erkennung" panel.
"Diesen Browser prüfen" runs a fresh proof, and the panel then shows what the server
concluded about that browser: tier, indicators, continuity, similarity, the current
proof-of-work bits and the first 8 characters of the key and fingerprint ids.
`GET /api/system/browser` is the only reply the app serves that carries an assessment. It requires system
access (staff plus the admin's OTP) and only ever shows the requesting browser's own receipt,
so only an admin account can use it as an oracle. An honest desktop browser should read
`high` with no indicators; iOS reads `low` (`ios`), and a browser that cannot keep its key
(some private windows) reads `low` with `ephemeral_key`. Each check is also logged as
`Browser assessed: <tier> <indicators>`.

`cd backend && uv run python manage.py test core.test_fingerprinting --noinput`

`cd frontend && npx vitest run src/lib/browser-fingerprint src/services/__tests__/browserRecognition.spec.ts`

`cd frontend && npx playwright test --config playwright.fingerprint.config.ts`

The Playwright run starts an isolated Vite server and a loopback Django fixture
(`../../scripts/fingerprint-test-server.py`). The fixture alone serves
`/api/fingerprint/test-assessment`, so the tests can read what the server concluded. It
exercises real Web Crypto, IndexedDB, workers, iframes, client hints and the verification
endpoint in Chromium, Firefox and WebKit. It checks that an honest browser shows no lie,
and that patched natives (even behind a patched `toString`), navigator overrides and
replayed or altered proofs are caught. The only indicator expected from a Playwright-driven
browser is `automation`.

Set `BROWSER_FINGERPRINT_REDIS_TEST_URL` to a local test Redis URL to also run the atomic
nonce test against independent Redis clients. It never flushes the cache.

Under `manage.py test`, `BROWSER_FINGERPRINT_ENABLED` defaults to off
(`settings/development.py`), so other suites never meet the per-browser limits. The
recognition tests switch it on per class.
