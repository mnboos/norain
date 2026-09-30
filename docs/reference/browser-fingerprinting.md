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
  `Function.prototype.toString`. It checks the canvas and the audio output for injected noise
  against known answers (every pixel its own colour, drawn and put; a buffer of exact samples
  played straight through). It reads the engine
  from its own error messages and compares that with the user agent. It also compares the
  user agent with the `Sec-CH-UA*` headers the server receives itself.
- **Keeps its conclusions to itself.** The `verify` reply carries only a lifetime. The
  assessment is kept in the cache, and the HttpOnly receipt cookie holds only a random id
  that points to it. A forger learns nothing about which check it failed.
- **Never trusts a look-alike.** Two devices of one model (iPhones, Tor, Firefox's
  resistFingerprinting, software rendering) report identical values. Such browsers are
  `low` and never deduplicate on their fingerprint, only on their own key.

- **Never frees a browser for lying.** Fewer claim keys mean looser limits, so nothing a
  browser can trigger at will leaves it with fewer: a `suspicious` browser keeps its own key
  and pays its IP's shared count on top, every `low` browser gets a network key, and a key
  younger than 20 hours pays its IP's count too.
- **Gives no feedback to tune against.** Votes are recorded blind and counted once a day, and
  the sign-up limit is the same for recognised and unrecognised browsers, so no reply says
  which check a request passed.

A scripted forger is therefore bounded by cost, time and the per-IP limits, which all remain
(an IPv6 address counts by its /56). An extension that patches every frame and every worker
could also pass the lie checks. That is the ceiling of any check that runs inside the browser
it is checking.

## Protocol

1. `POST /api/fingerprint/challenge` returns `{challenge, expiresIn, difficulty, echo}`.
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
   - While it runs, and only when `echo` is true (the relay meter is on), the page makes three
     chained `POST /api/fingerprint/echo {challenge, step, token}` calls, each with the token of
     the last reply, within 2 s at most. See "The relay meter".
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
`canvasIntegrity`, `engine` and `automation`. A check that did not run cannot catch a lie, so
any of them missing is `checks_unavailable`: skipping one costs the browser its `high` tier.

The `integrity` check also calls every watched getter and method on the wrong object, through
the fresh frame's own `Reflect.apply`, and compares that with the frame's clean copy: a native
refuses with a TypeError, a JavaScript replacement usually answers (`receiver:` entries). Only
whether it threw and the error's kind count; V8 words some refusals with the receiver's realm.
The same call runs inside a marker method, and the stack between the throw and the marker must
hold nothing but natives: a wrapper that forwards to the real getter still shows as a frame of
its own (`stack:` entries; the marker is a method, because Safari's proper tail calls drop an
arrow in tail position and JavaScriptCore leaves arrows under computed keys unnamed). The frame
must be one of `window[i]`, which a page cannot redefine (`frame:identity`: a hooked
`contentWindow` that hands back another window, such as the page itself, shows here; a
prepared realm that is a real child frame passes), and its own WebAssembly must give the
page's NaN bits (`frame:nan`). A promise-returning method (`getHighEntropyValues`) rejects
rather than throws on the wrong object; that rejection is handled, never left to the console.
The user-agent data targets report as `realm_tampered` until they are enforced.

## Tiers

- `suspicious`: any lie. That is one of `user_agent_mismatch`, `worker_mismatch`,
  `iframe_mismatch`, `native_tampered`, `engine_mismatch`, `client_hints_mismatch`,
  `automation`, malformed check values, and the observed ones below once enforced:
  - `client_hints_grease_mismatch`: Chromium derives the made-up brand in `Sec-CH-UA` from its
    own major version (`"Not" + c[M % 11] + "A" + c[(M + 1) % 11] + "Brand"`, version
    `8`/`99`/`24` by `M % 3`), every fork keeps that code, and a header template edited to claim
    another version keeps the old brand. Checked from Chromium 115.
  - `fetch_metadata_mismatch`: the verify request is a JSON `fetch` POST, so its Sec-Fetch-*
    can only be `same-origin`/`same-site`, `cors`/`same-origin`, `empty`, without
    `Sec-Fetch-User`.
  - `graphics_platform_mismatch`: hardware that cannot exist. A Direct3D 9/11 renderer on a
    non-Windows UA, a Metal renderer on a non-macOS UA, or a macOS UA with an Intel, AMD or
    NVIDIA renderer and ARM's NaN bits (Apple silicon runs no PC GPU; an Intel Mac cannot make
    ARM's NaN). Masked renderers, D3D12 (WSL), phones and Windows on ARM never trip it.
  - `realm_tampered` (`receiver:` and `frame:` entries) and `native_stack_tampered` (`stack:`).
- `low`: honest but not distinctive, or degraded. That is `canvas_noise`,
  `iframe_canvas_mismatch`, `software_renderer`, `ios`, `client_hints_missing`,
  `ephemeral_key`, `low_signal_coverage`, `checks_unavailable`, and the observed
  `fetch_metadata_missing` (a version that sends Sec-Fetch-* sent none) and
  `fingerprint_common` (see "Measured distinctiveness"). A canvas that differs only between
  page and iframe counts as noise, not a lie: a script that patches the canvas already shows
  as `native_tampered`. Audio that fails its known answer reads as `audio_unstable`, like audio
  that changes between two reads.
- `high`: everything else.

### Observe mode

A new lie check never starts by changing tiers. `BROWSER_OBSERVE_ONLY` (a comma-separated
setting) lists indicators that are named in the assessment and counted in the stats but earn
no tier. Every check added after the first release starts there. One leaves the list when
both hold:

- its **solo** count stayed at 0 for 14 days. Solo means it fired with no *enforced* lie and no
  `automation` beside it: the likely false positive. Other observed lies do not count against
  it, or two false positives that fire together would hide each other. The `/system` panel
  shows both counts;
- a manual matrix of real devices shows no hit on the `/system` panel: an Apple-silicon Mac
  (Chrome, Firefox, Safari), an Intel Mac, Windows on ARM, an Android phone and an iPhone.
  Playwright on an x86 box cannot exercise the ARM rules.

That gate is for lies. The observed `low` indicators (`fetch_metadata_missing`,
`fingerprint_common`) fire on honest browsers by design, so they are nearly always solo; they
are enforced from their counts by judgement instead, which is safe because enforcing them only
trades a browser's `f:` key for a `p:` key.

### Claim keys

`core.fingerprinting.device_keys` turns an assessment into claim keys. Nothing a browser can
trigger at will leaves it with fewer, because fewer keys mean looser limits:

| Tier | Claim keys | Also pays its IP's shared count |
|---|---|---|
| `high` | its key and its fingerprint | while its key is younger than 20 h (votes) |
| `low` | its key (when persistent), and `p:` = its coarse print on this network, today | while its key is younger than 20 h (votes) |
| `suspicious` | its key only: the signature still proves it; its values lied | always |
| unknown, or no key at all | none | always |

The coarse print is a keyed hash of what renderer noise leaves alone (`graphics`, `fonts`,
`hardware`, `math`, `media`) and the user agent, or of the user agent alone when none of them
was readable. `math` carries the WebAssembly NaN bits of 0/0, which the hardware sets (x86 and
ARM differ). Many devices share a coarse print, so it is never a claim on its own: it is bound
to the network (IPv4 as is, IPv6 by its /64) and the UTC day.

So a new Safari private tab (its own storage, noise salted per tab) mints a new key, but not a
new `p:` key. Look-alike devices behind one address (CGNAT, iCloud Private Relay) share a `p:`
key for that day. That is never stricter than the per-IP count they would otherwise fall back
to, which the whole address shares.

**Key age.** The server remembers when it first saw each key (`browser:seen:…`, 30 days from
the key's last proof). A key younger than `BROWSER_KEY_AGE` (20 h) is not `established`: it
still claims its own keys, and pays its IP's shared count on top. Time is the one resource a
GPU cannot compress; minting keys buys no votes that day. `BROWSER_KEY_AGE_SIGNUPS` applies the
same to sign-ups and sign-in codes (off by default). Unknown (a cache error) counts as
established.

**Measured distinctiveness.** A fingerprint is one device's only while few keys share it. The
server counts, per `high` fingerprint, the networks (IPv4 /24, IPv6 /48) on which a key new to
it appeared within 30 days, and the new keys per hour. Three networks, or five new keys within
an hour (a cloned anti-detect profile), make it `fingerprint_common` for 30 days: once
enforced, such a browser is `low`, and its `p:` key takes the fingerprint's place. One device
moving between networks adds nothing: only a (fingerprint, key) pair seen for the first time
counts.

**IP floors.** Every per-IP count (votes, the shared count, churn, the sign-up fallback, the
endpoints' own limit) counts an IPv6 address by its /56, which one customer usually holds
whole: by its full address, a /64 would give anyone with an IPv6 line unlimited fresh counts.
IPv4 addresses count as they are, so their counters are the same as before. This needs
Cloudflare's Pseudo IPv4 **not** set to "Overwrite headers": then every IPv6 client arrives as
a class E IPv4 (240.0.0.0/4) and no prefix can be recovered. The stats count such addresses
(`ip_class_e`).

## The relay meter

Observe mode only (`BROWSER_PATH_METER=observe`; `off` by default): it measures and counts, it
never limits and never changes a tier.

Cloudflare measures the TCP (or QUIC) round trip to whatever ended the connection at its edge.
A residential proxy, the usual way to rotate IPs, ends it at the proxy's exit device, so the
bot's own leg behind the exit never shows there. It does show in round trips the server times
itself: the three chained echoes (each needs the last reply's token). The verdict compares

`excess = fastest echo − edge round trip − this Cloudflare site's own leg`

where the last term is the 10th percentile of the recent samples at that Cloudflare site
(`CF-Ray` suffix). Below `BROWSER_PATH_DIRECT_MS` a path is `direct`, above
`BROWSER_PATH_RELAYED_MS` `relayed`, otherwise `unclear`; without the edge measurement or the
echoes, `unmeasured`. Both thresholds stay unset until gate G1 measured them.

Only the verdict, the transport, a 10 ms excess bucket and keyed ids of the IP bucket and the
ASN go into the receipt; raw round trips live 130 s. The stats count verdicts and excess
buckets per transport, which is what sets the thresholds.

It needs two things outside this repository, both the site owner's:

1. A Cloudflare request-header Transform Rule on `/api/fingerprint/*` and on the claim
   requests (`/api/coverage/*/vote`, `/api/allauth/browser/v1/auth/signup`,
   `/api/allauth/browser/v1/auth/code/request`), setting `X-Ml-Edge`
   (`BROWSER_PATH_HEADER`, which Caddy reads from the same variable) to
   `concat(to_string(cf.timings.client_tcp_rtt_msec), ",", to_string(cf.timings.client_quic_rtt_msec), ",", to_string(ip.src.asnum))`.
   Caddy removes that header from any request not from Cloudflare's ranges.
2. Authenticated Origin Pulls, with `client_auth` in the Caddyfile's `(origin_cert)`, so no
   client can reach the origin without Cloudflare and write the header itself.

**Gates before anything is enforced** (on the production path through Cloudflare, in observe
mode; the loopback Playwright fixture has no Cloudflare and proves nothing here):

- G0, infrastructure: the header arrives on at least 99 % of echoes over HTTP/2 and HTTP/3; a
  client's own copy is overwritten; a direct TLS connection to the origin is refused; the ASN
  is filled in.
- G1, separation: at least 12 honest setups (fibre and Wi-Fi desktops; iPhone and Android on
  two carriers; Private Relay on and off; Mozilla VPN; Opera VPN; a corporate proxy) against
  three residential proxy providers (CONNECT and SOCKS5; exits in the same city, the same
  country and far away) driving headless Chrome and Camoufox, plus a scripted HTTP/3 client
  over a UDP relay. Passed when at least 97 % of honest direct sessions stay below D, at least
  90 % of same-country proxy sessions exceed R, and after 2 weeks no per-ASN ceiling would
  refuse more than 1 % of trusted sessions (established keys, signed-in users).
- Enforcing (a later change) must fail open: an unmeasured request keeps today's per-IP
  allowances; a relayed one keeps them too and meets a per-ASN ceiling on top, sized from the
  observed counts.

What it would move, honestly: rented residential proxies plus a script or an anti-detect
browser would have to run on each residential device itself, or tunnel to it below TCP. It
does not stop owned device farms, botnets, modem farms or HTTP/3 over a UDP relay.

## Where it is used

**Anonymous coverage votes.** These stack on the cookie and the per-IP limits, never
replacing them.

- A browser with claim keys votes once per area for 30 days, whatever its cookie or network.
- Unknown, keyless, suspicious and young-key browsers share 3 votes per IP a day.
- The device claim is made before the IP claim, and released again if the IP claim fails.
- Withdrawing a vote releases exactly what was claimed, recorded per voter.
- **The ledger is blind** (`COVERAGE_BLIND_LEDGER`, on by default). A vote the limits refused
  is stored anyway, not counted, and shown to its voter as cast; a withdrawal waits for the
  daily settlement, which also publishes the tallies (`COVERAGE_TALLY_STEP` rounds them down).
  So no reply says whether a vote counted: whoever probes the limits learns it a day later, as
  the day's change in a tally. With the step at 1 (the default, because most areas here have
  fewer than five votes), a quiet area's change still shows a single vote; a larger step blurs
  it at the cost of small tallies. A refused vote cast again is not tried again, since a flip
  would give it away; signing in is the way to a vote that surely counts. The page says the same to every voter: votes are counted once a day,
  anonymous ones checked then. Only the per-IP flood limit still answers 429.

**Sign-up and sign-in codes.** This is a middleware in front of allauth.

| Browser | Sign-ups | Code requests |
|---|---|---|
| Recognised | 3 a day per claim key | 10 an hour per claim key |
| Its `p:` key | 3 a day, the IP's allowance | 10 an hour |
| Unknown, keyless or suspicious | 3 a day per IP (a suspicious one per key too) | 10 an hour per IP |

- The per-key and per-IP limits are equal on purpose: after how many requests the refusals
  start must not say whether the browser was recognised.
- Only requests allauth accepted (200 or 401) are counted, so a mistyped address costs nothing.
- A refusal is answered as JSON `{detail}` with status 429.
- Password sign-in and code confirmation are not affected.

## Data and operation

| What | Where | How long |
|---|---|---|
| Receipts | Redis | 15 minutes |
| Baselines (keyed probe hashes) | Redis | 30 days |
| "Seen" markers with the first sight, and context-to-key links | Redis | 30 days |
| Fingerprint spread counters (keyed) | Redis | 30 days |
| Used nonces | Redis | 130 s |
| Echo records (raw round trips) | Redis | 130 s |
| Round-trip samples per Cloudflare site | Redis | 7 days |
| Churn and rate counters | Redis | about an hour |
| Vote claims | Redis | 30 days |
| Daily stats (fixed names, counts only) | Redis | 35 days |
| Published vote tallies | Redis | 48 hours |

- Raw probe values are never stored or logged. The log carries tier and indicator names.
- Nothing is stored in Postgres beyond the votes themselves (`CoverageVote.accepted`,
  `withdrawn_at`).
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
- `BROWSER_OBSERVE_ONLY`, `BROWSER_KEY_AGE`, `BROWSER_KEY_AGE_SIGNUPS`,
  `BROWSER_PATH_METER`, `BROWSER_PATH_HEADER`, `BROWSER_PATH_DIRECT_MS`,
  `BROWSER_PATH_RELAYED_MS`, `COVERAGE_BLIND_LEDGER` and `COVERAGE_TALLY_STEP`: see above.
- Caddy's `fingerprint` and `fingerprint_echo` zones (`../../deploy/auth-ratelimit.caddy`)
  stop floods of the endpoints.
- A `SECRET_KEY` rotation changes every HMAC identifier.

## Verification

On a running site, an admin can open `/system` and use the "Browser-Erkennung" panel.
"Diesen Browser prüfen" runs a fresh proof, and the panel then shows what the server
concluded about that browser: tier, indicators, continuity, similarity, whether the key is
established, the current proof-of-work bits, the first 8 characters of the key and
fingerprint ids, the claim keys the browser holds on this network, the relay meter's verdict
for its own path, and a prefix per probe value (which probe moved between two checks).
`GET /api/system/browser` is the only reply the app serves that carries an assessment. It
requires system access (staff plus the admin's OTP) and only ever shows the requesting
browser's own receipt, so only an admin account can use it as an oracle. An honest desktop
browser should read `high` with no indicators; iOS reads `low` (`ios`), and a browser that
cannot keep its key (some private windows) reads `low` with `ephemeral_key`. Several Safari
private tabs read `low` with `canvas_noise` or `audio_unstable`, a different key each, and the
same `p:` key. Check that last point on a real Safari (three private tabs, with and without
iCloud Private Relay) whenever a probe that feeds the coarse print changes: Playwright's
WebKit has none of Safari's fingerprinting protection. Each check is also logged as
`Browser assessed: <tier> <indicators>`.

The panel "Browser-Erkennung, 14 Tage" (`GET /api/system/browser/stats`, the same access)
shows counts only: tiers, each observed check with its solo count, refused proofs by reason,
the proof-of-work levels issued, the relay meter's verdicts and excess buckets, and class E
addresses.

`cd backend && uv run python manage.py test core.test_fingerprinting core.test_coverage --noinput`

`cd frontend && npx vitest run src/lib/browser-fingerprint src/services/__tests__/browserRecognition.spec.ts`

`cd frontend && npx playwright test --config playwright.fingerprint.config.ts`

The Playwright run starts an isolated Vite server and a loopback Django fixture
(`../../scripts/fingerprint-test-server.py`). The fixture alone serves
`/api/fingerprint/test-assessment`, so the tests can read what the server concluded, and it
swaps in the headers a page cannot set (`X-Test-Headers`), so the tests can play a forger's
Sec-Fetch-* and Sec-CH-UA. It exercises real Web Crypto, IndexedDB, workers, iframes, client
hints, the echo chain and the verification endpoint in Chromium, Firefox and WebKit. It checks
that an honest browser shows no lie, and that patched natives (even behind a patched
`toString`), forwarding wrappers, a foreign frame, navigator overrides, impossible headers and
replayed or altered proofs are caught. The only indicator expected from a Playwright-driven
browser is `automation`.

Set `BROWSER_FINGERPRINT_REDIS_TEST_URL` to a local test Redis URL to also run the atomic
nonce test against independent Redis clients. It never flushes the cache.

Under `manage.py test`, `BROWSER_FINGERPRINT_ENABLED` defaults to off
(`settings/development.py`), so other suites never meet the per-browser limits. The
recognition tests switch it on per class.
