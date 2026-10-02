# MeteoLane — Bike-route weather forecaster

Self-hosted routing (GraphHopper) + geocoding (Photon), weather from Open-Meteo (primary, free) with MET Norway
(yr.no, free) and then OpenWeatherMap One Call 3.0 as fallbacks. Multi-user with
email/username sign-in, and a free/Pro subscription tier backed by Stripe.

## Project layout

```
backend/          Django 6 + Channels (async ASGI via daphne)
  backend/settings/  base.py + development.py / production.py (a package, not settings.py)
  backend/asgi.py    ProtocolTypeRouter: the Django app for http, consumers for websocket
  core/
    middleware.py    UserLanguageMiddleware: the account's language over Accept-Language
    locale/en/       django.po + compiled .mo (msgids are German); `just messages`
    weather.py       routing + sampling + wind logic, compute_route_weather, build_geometry
    grid.py          forecast grid cache (ForecastCell, EnsembleCell) + API fetch + extraction
    stations.py      Weather Underground stations: budgeted fetch, cache, near-now correction
    models.py        User (custom, AUTH_USER_MODEL), Subscription, ProcessedStripeEvent,
                     RecurringRoute, ForecastCell, EnsembleCell, StationLookup,
                     StationObservation, ForecastJob, RoutePhoto, RouteComment, RouteLike
    jobs.py          forecast-job identity, lifecycle and channel-layer publishing
    claims.py        cache-backed in-flight claim for grid-cell fetches
    consumers.py     ForecastJobConsumer, SystemEventsConsumer (websocket); routing.py maps URLs
    system_events.py notify_system: change notices for the admin system dashboard
    forecast_schemas.py  the forecast payload (RouteWeatherOut, WeatherSample, ForecastJobOut, …)
    api/             ninja routers: route_weather.py, recurring_route.py (route CRUD),
                     billing.py, places.py, community.py (sharing, photos, comments, likes),
                     coverage.py (the public coverage page)
    public_routes.py the public view of a route: privacy zones, public_geometry
    coverage.py      the coverage page: votes (anonymous too), double opt-in "tell me when" mails
    fingerprinting.py  browser recognition: challenge, proof-of-work, lie checks, tiers, receipts
    countries.py     votable ISO country codes + de/en names for the mails (generated from ICU)
    photos.py        upload re-encoding (no EXIF/GPS); signals.py deletes the files with the row
    auth/            backend.py (session_auth), adapter.py (allauth rules), signals.py,
                     views.py (session, sign-up step 2, profile), lockout.py,
                     device_throttle.py (sign-up / code requests per recognised browser)
    entitlements.py  every tier limit, in one place
    thumbnails.py    route-list glyph: path simplification + cache-only weather
    ride_quality.py  ride-quality curves + RIDE_QUALITY config (secret; scored on read)
    journeys.py      journey constants, lodging filter, ranking on read
    journey_planner.py  routed insertions: gap fixes, breaks, lodging (RoutingBudget)
    journey_geometry.py measured lines: Limits, LineMeasure, check_limits, gaps
    random_rides.py  random rides: candidate generation (loop / long way round) and sizing
    pois.py          POI categories (POI_RULES) + corridor query pois_along_sync
    road_prefs.py    road preferences -> penalty-only GraphHopper custom model
    weather_routing.py  corridor cells by the hour -> the `weather` field GraphHopper routes around
    schedule.py      croniter-based next_departure / forecast_available_at
    tasks.py         every heavy operation: geometry, cells, job planning/assembly, scans
    sections.py      route sectioning by weather condition
    tests.py         django tests (SimpleTestCase + TestCase + TransactionTestCase)
    schemas.py       shared Pydantic (CamelSchema)
    management/commands/  verify_user, claim_routes, refresh_forecasts, run_forecast_scheduler,
                          import_pois
frontend/         Vue 3 + Quasar + @tanstack/vue-query
  src/
    services/        http.ts (shared fetch+CSRF, allauthRequest), auth.ts, billing.ts — the
                     plain-Django and allauth endpoints; the ninja API goes through the
                     generated @norain/api client; browserRecognition.ts (on-demand proof)
    lib/browser-fingerprint/  our own recognition library: probes, lie checks, SHA-256 + PoW
    composables/     useSession, useEntitlements, useLocale (detect, switch and save the language)
    i18n/index.ts    vue-i18n instance, t/te for .ts modules, intlLocale(), dateFnsLocale()
    locales/         de.json (source) + en.json; __tests__ checks both have the same keys
    utils/levels.ts  the server's band/level/weather codes -> words
    utils/serverErrors.ts  stored job/plan error codes -> words
    utils/rideQuality.ts   score -> YlOrRd colour + its casing, line placement; no scoring
                           (that is core/ride_quality.py, server-only)
    utils/routeThumbnail.ts  geographic path -> square viewBox projection
    components/RouteThumbnail.vue  the tiny route glyph in the list
packages/api/     generated TypeScript client (see "Regenerating the client")
.env             OSM_DATA_URL, ROUTING_OSM_FILE_FILTERED, ROUTING_OSM_IMPORT_DIR,
                 PHOTON_INDEX_URL, GRAPHHOPPER_HEAP, OPENWEATHERMAP_API_KEY,
                 WEATHERUNDERGROUND_API_KEY, OPEN_METEO_API_KEY (commercial, optional),
                 OPEN_METEO_LIMIT_{MINUTE,HOUR,DAY,MONTH}, OPENWEATHERMAP_DAILY_CAP,
                 REDIS_URL (claim cache, provider budgets, channel layer)
```

## Key architecture

### Accounts and sign-in

`core.User` (`AUTH_USER_MODEL = "core.User"`) subclasses `AbstractUser`. It exists as a
custom model for one main reason: the two constraints in its `Meta`. Django's default
user permits duplicate and blank emails (`unique=False, blank=True`) and its `username`
index is case-sensitive, so "one account per email address, however capitalised" cannot be
expressed there — and you cannot add constraints to a model the project does not own. The
model also overrides `email` to `blank=False` (an account with no email could never verify
itself or reset its password) and adds `signup_completed`, `default_profile` (the bike
profile the route, journey and map forms start with; `useSession().defaultProfile`) and
`language` (see "Internationalisation").

**Sign-up, sign-in, verification and password reset are django-allauth, headless.** allauth
serves JSON under `/api/allauth/browser/v1/`; the Vue app draws every form (`components/account/SignInForms.vue`). Our
own endpoints are only `/api/auth/session`
(the session as the app needs it, plus the CSRF cookie), `/api/auth/complete-signup`,
`/api/auth/username-available` (step 2's live check, signed-in only, limited per account)
and `/api/auth/profile` (changes `default_profile` and/or `language`, each optional). Sign-up has
two steps:

1. The form takes only the email. allauth creates the user with a placeholder username (`fahrer-<hex>`,
   `AccountAdapter.populate_username`: never the email's local part,
   because usernames are public) and no usable password, and mails a **code**
   (`ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED`), never a link. The user types it into the
   same tab, which verifies the address and signs in, whichever device read the mail. The
   pending verification lives in the session: the code is useless in another browser (409), 3 wrong codes end it, and
   two resends are allowed at least 10 s apart (allauth
   answers 429 before that, and to a second sign-up of one address within 10 s). A known
   address gets the same reply (allauth mails its owner a pointer to sign-in by code
   instead), so the form reveals nothing. When the pending verification is gone, sign-in
   by emailed code (`ACCOUNT_LOGIN_BY_CODE_ENABLED`) is the way in; it verifies the address too.
2. The signed-in user picks a username (the form suggests the email's local part, which
   only the owner sees there), the default bike profile and, **optionally**, a password in
   `complete_signup_view`. Without a password the account signs in by emailed code, and
   "Passwort vergessen?" sets one later. `User.signup_completed` marks step 2 done, and
   the router keeps the user on `/account` until it is true. That guard is the UI's only:
   the API does not check `signup_completed` (the email is verified and every tier limit
   applies, so there is nothing to protect), so don't describe it as a server-side rule. It
   is a separate flag, not "has a usable password": a password is optional, and a password
   reset sets one without the user ever picking a username.

The mails of this flow (sign-up code, "account exists", sign-in code) are German templates
in `core/templates/account/email/`, which win over allauth's because `core` comes first in
`INSTALLED_APPS`; the others are still allauth's English ones. Mails get `frontend_url` in
their context (`AccountAdapter.send_mail`).

Every step 1 makes a `User` before the mailbox is proven, so the hourly pass (`refresh_upcoming_forecasts`) runs
`_purge_abandoned_signups`: accounts older than
`ABANDONED_SIGNUP_RETENTION` (7 days) with no verified address, no usable password, sign-up
not completed and not staff. The password condition is what spares an account made by hand
in the admin before `verify_user` ran — keep it.

Whether an email is verified lives only in allauth's `EmailAddress`. `create_superuser`
adds a verified one, so a superuser can sign in to the app at once; an account made by hand
in the admin or the shell has none until `python manage.py verify_user` runs (which also
marks sign-up complete when the account already has a password). A sign-in by code marks
the address verified too.

Both the email and the username are sign-in identities. The SPA sends whatever was typed as
`username`; allauth's `AuthenticationBackend` tries it as an email first, then as a
username. Do **not** branch on whether the identifier contains `@` —
`UnicodeUsernameValidator` permits `@` in usernames. `core.auth.adapter.AccountAdapter`
refuses a username that matches any existing email (and vice versa), or one sign-in would
match two accounts. The adapter also names the site "MeteoLane" in allauth's mails and counts
allauth's rate limits by `core.auth.lockout.client_ip` (allauth's own
`TRUSTED_CLIENT_IP_HEADER` has no fallback, so without Caddy every request would get a
403). Links in the mails come from `HeadlessAdapter.get_frontend_url`, which puts
`FRONTEND_URL` in front of the paths in `HEADLESS_FRONTEND_URLS` when the mail is sent,
because production.py sets `FRONTEND_URL` after base.py is read.

Side effects hang off allauth's signals (`core/auth/signals.py`): the forecast refresh on
sign-in and the `account.action` milestones. They are allauth's signals, not Django's, so
`force_login` and the admin sign-in don't send them.

**The admin is public, so it has three guards.** It is served at `DJANGO_ADMIN_PATH`
(production refuses a missing value or `admin`; Caddy routes the same variable). It is an
`OTPAdminSite` (django-otp): password plus an authenticator code, and the first device comes
from `manage.py add_totp_device` (`DJANGO_ADMIN_OTP=false` turns the code off, under the
development settings only). And django-axes counts failed sign-ins — app and admin — **per IP**, not per account+IP: a
pair lockout never trips when one address tries a new
account each time. It is the only limit on wrong *passwords*: allauth's own `login_failed`
limit is off (`ACCOUNT_RATE_LIMITS`), or it would block one identity after 5 tries, before
axes counts to 10, with a different reply. Axes does **not** cover sign-in by code (no
password is checked, so a locked-out address can still get in by code). That is on
purpose: a code proves the mailbox, and guessing one is capped by allauth (3 tries per
code, `request_login_code` 3 a minute per address). A test pins this. The IP comes only from `X-Real-IP`, which Caddy
sets from `{client_ip}`
(`core/auth/lockout.py`); daphne has no proxy headers, and behind Cloudflare
`X-Forwarded-For` is a chain, not the client. The lockout reply is JSON because the SPA's `request()` parses every body.

**Caddy rate-limits the auth endpoints in front of all that** (`deploy/auth-ratelimit.caddy`,
imported by `deploy/Caddyfile`; the image builds Caddy with the `caddy-ratelimit` plugin, which
needs `order rate_limit before basic_auth`). Per `{client_ip}`: password sign-in (app and
admin), sign-up, the mail-sending calls and code/key checks. Per user (the `sessionid` cookie)
*and* per IP: the username check and the step-2/profile saves. These are floods stopped before
Django, not a replacement for axes or allauth's per-address limits, which still decide the
normal cases; counters live in Caddy's memory. A 429 is answered as JSON with `detail`, like
the axes lockout. A new auth endpoint that sends mail, checks a secret or answers "is this
taken" belongs in that file. The development server has no Caddy, so none of this applies there.

**Migration ordering.** `core.User` is created in `0002`, not `0001`, because `0001` was
already released. Django resolves `swappable_dependency(AUTH_USER_MODEL)` to
`("core", "__first__")`, which would schedule `admin.0001_initial` before the user model
exists and fail with "Related model 'core.user' cannot be resolved" — so `0002` declares
`run_before = [("admin", "0001_initial")]`. Keep that if you ever regenerate it.

### Tiers and entitlements

Every limit lives in `core/entitlements.py`: free = 2 active routes, no ensemble
spread and no station correction; journeys: free = 1 journey, 1 alternative per day and no
weather-aware routing (enforced in `create_journey` and `plan_journey`).

**Riding around bad weather is the rider's choice and a Plus feature** (`weather_routing`), for
journeys and random rides alike. `weather_prefs.avoid_rain` / `avoid_headwind` / `avoid_shade` are off unless
the rider switches them on (`WeatherPrefsIn` defaults, and `prefs.get(..., False)` for rows
without them). `_values` in `api/journey.py` stores them off for an account without Plus, so an
upgrade never starts routing around weather nobody chose. `_wants_weather_routing` checks the
tier and the choice again at planning time, so a downgrade stops it on the next plan. The
entitlements payload carries `weatherRouting`; the SPA's `WeatherRoutingChoice` shows the
switches locked for free accounts. The route and forecast
limits are enforced at **three** places, and a limit is only real if all three hold:

1. `create_route` / `update_route` (`api/recurring_route.py`) — the route count, 402 when full.
2. `assemble_forecast_job` (`tasks.py`) — `strip_uncertainty()` for free accounts. This is **not** in the endpoints any
   more: the job's stored `result` is what the WebSocket pushes
   and what the job endpoint returns, so it has to be stripped *before* storage or a free
   account reads Pro data straight out of the row. The owner is part of the job key, so
   results never cross accounts — but the tier is not, so assembly records
   `result["entitlements"]` and `get_or_start_job` never reuses a result built for another
   tier (upgrade or downgrade; a result without the marker is rebuilt too).
   `pop` and `rain_if_wet` are deliberately *not* gated: ensemble cells are shared and
   pre-warmed, so serving them costs nothing extra.
3. `_prewarm_routes` (`tasks.py`) — decides which routes get a `scan_route_forecasts` task,
   and the scans are what actually spend the Open-Meteo budget. Nowhere near the HTTP layer,
   so it is the easy one to forget.

The station correction (`station_correction`) is gated differently, because a corrected
number cannot be stripped afterwards: `_wants_stations` in `plan_forecast_job` decides
whether the Weather Underground task runs (that is where the budget is spent), and
`assemble_forecast_job` and `compute_route_thumbnail` pass `station_correction_enabled` from
the owner's tier. The pre-warm scan never fetches stations.

Entitlements read the local `Subscription` row, never Stripe, so a tier set by hand in the
admin behaves exactly like a paid one and the whole layer is testable without API keys.

### Stripe

`/api/billing/*` is mounted in `backend/urls.py`, **not** on the ninja router:
`NinjaAPI(auth=session_auth)` CSRF-checks every route it owns and would 403 Stripe's
webhook POST. The signature check authenticates it instead. Every `event.id` goes into
`ProcessedStripeEvent` and is applied once — Stripe retries on any non-2xx. Tier changes
come only from webhook events; the Checkout success redirect proves nothing.

**`current_period_end` is not on the subscription any more.** Stripe moved it onto the
subscription *items* in API version 2025-03-31.basil, and the SDK pins a version well past
that, so a live payload has no top-level field. `_period_end` reads the field off
`items.data[]` and falls back to the old place for payloads from an older endpoint — do not
simplify it back to one lookup. Leaving it null costs the renewal date in the UI and
the expiry guard in `_entitlements_for_subscription`. Tests cover both shapes.

Use `client.v1.*` for every Stripe call (`v1.customers`, `v1.checkout`, `v1.billing_portal`):
the accessors without `v1` are deprecated. `stripe.Webhook.construct_event` is deliberately *not* the client method —
the webhook needs only `STRIPE_WEBHOOK_SECRET`, and
`client.construct_event` would make it need a secret key too.

### Planning needs an account

Every endpoint that plans a ride is `session_auth` (401 without a session): the ad-hoc forecast (`GET`/
`POST /api/route_weather`), a public route's forecast, `POST /api/routes/preview`,
`POST /api/elevation`, place search and the whole GPX router. Each spends provider or
GraphHopper budget. The SPA's `/map` planner is `requiresAuth`. What stays open to anyone is
reading: a public route, its photos and comments, and a forecast job by its unguessable id.

### Every heavy operation is a task

No HTTP request performs a provider fetch or a GraphHopper call — with two deliberate
exceptions: `POST /api/routes/preview` (see "Route editing") and the admin-only
`GET /api/system/data-coverage` (see "System dashboard"). The forecast endpoints create a
`ForecastJob`, enqueue `plan_forecast_job` and return **202** with a job id; a finished job that is still fresh returns
**200** with its stored payload.

```
POST-ish GET  ->  ForecastJob (202)
                     plan_forecast_job     queue: forecasts   geometry + fan-out
                       refresh_forecast_cells  \ queue: cells   one task (one Open-Meteo
                       refresh_ensemble_cells  /               request) per batch of cells
                         assemble_forecast_job  queue: forecasts  cache_only + sections
                           -> job.result, pushed over ws/forecast/<job_id>/
```

The stored `job.result` is always complete. `job_snapshot` serves a slim view of it (`core.jobs.forecast_view`: a ~50 m
line, wind arrows ~2 km apart instead of the wind
segments, no per-model breakdown), and pages fetch those parts from
`/api/forecast_jobs/{id}/map_detail?detail=` (line + arrows) and
`/samples/{i}/uncertainty` only when they draw them. Shape on read only — never let page
shape into the job key, or two pages would compute two jobs for one forecast. Every line
level must keep each sample's vertex exactly: the map finds samples on the line by equality.

**Stale while it refreshes.** A restarted job (expired, failed or stalled) keeps its last
result as `stale_result` (`jobs.carry_stale`): only one built for the owner's current tier and
at most `STALE_RESULT_MAX_AGE` (24 h) old, measured from the payload's `computed_at`, which
assembly stamps (not `updated_at`, which every restart bumps). A restart after a failed
refresh carries the kept one forward, so a throttled provider never empties the page. Only the
HTTP envelope (`job_out`) serves it, as `result` with `stale: true`, never the WebSocket
frames. The page shows it with its "Stand" and swaps in the fresh result. It is cleared when
assembly finishes, dropped on a tier change mid-refresh, and dropped by an entitlement failure
in planning (`_fail_not_allowed`). `result` itself is still only ever written by assembly.

Four rules hold this together:

- **Assembly reads `cache_only=True`.** Every cell it needs was fetched by a `cells` task.
  Reaching for `get_or_fetch_*` there would put provider calls back on the path this whole
  design exists to keep them off.
- **`cells_total` is committed before the first cell task is enqueued.** A `cells` worker
  can settle a cell while `plan_forecast_job` is still running, and a settle against
  `cells_total=0` would hand the job to assembly with no data.
- **The handoff to assembly is one guarded UPDATE, not a read-back.** `F()` makes the
  increment atomic but not the read after it, so with several `cells` workers two tasks can
  both see a complete job. Only the row still in `fetching` flips to `assembling`, and only
  that caller enqueues.
- **A job with no samples fails, it does not finish.** An empty forecast served as `done`
  would sit in front of the user as though it were the weather, for the full `MAX_CELL_AGE`.

Queue split, because `db_worker` has no concurrency flag (one process, one task at a time —
parallelism is replicas): `cells` for the provider fan-out, `forecasts` for planning, assembly and a route's geometry
(someone is waiting), `default` for thumbnails, scans, journey planning and maintenance (a bulk
geometry backfill passes `.using(queue_name="default")`).

**A route forecast waits for its geometry in `pending`, and storing the geometry plans it.**
`_refresh_route_geometry_async` enqueues `plan_forecast_job` for every pending `ROUTE` job of the
route; the delayed retry (`PLAN_RETRY_DELAY`, `MAX_PLAN_ATTEMPTS`) is only the fallback for a
geometry task that never stores. Because both can arrive, planning starts with one guarded UPDATE
from `pending` to `planning`, and only its winner plans; never start planning without it, or a job
is counted and fanned out twice.
Queue position no longer implies completion order, so anything that used to rely on FIFO —
the thumbnail rebuild — now uses `.using(run_after=…)`.

`core/claims.py` keeps one cell from being enqueued by every pass that wants it (`cache.add` is
atomic). It fails **open** — a Redis outage costs deduplication, never forecasts — and the
claim is released on failure too, or one dead cell would block retries for the whole TTL.
A job still enqueues a cell whose claim is held elsewhere: the holder is usually the
pre-warm scan, whose task carries no `job_id` and would never report back.

The claim only deduplicates *enqueuing*. The guarantee that one cell is fetched **once** is
the fetch lease (`core/cell_lease.py`, `CellFetchLease` rows), taken inside
`grid.get_or_fetch_*`, so it covers every caller. It lives in Postgres and fails **closed**.
The holder re-checks the cache under the lease before it fetches. A caller that finds the
lease held waits for it, then only reads what the holder stored. If the holder stored
nothing, the waiter returns `None` and does not fetch. It fetches only when the stored cell
covers fewer `forecast_days` than it needs. Never make a waiter wait on the enqueue claim
instead: a task still in the queue holds that, possibly behind the waiter itself.
`LEASE_TTL` must stay above the Open-Meteo + MET Norway + OWM timeouts combined.

**Imports go at module scope — keep the layering that allows it.** The forecast payload
schemas live in `core/forecast_schemas.py`, outside the `core.api` package, because the
domain modules (`weather`, `uncertainty`, `sections`) need them and importing
anything under `core.api` runs its `__init__`, which loads the routers, which import
`core.tasks`. The direction is one way: `core.api.*` → `core.tasks` → domain modules →
`forecast_schemas`. Never make a domain module import from `core.api`; that recreates the
cycle, and any process reaching `core.tasks` or `backend.asgi` first — a worker, daphne —
dies on import. Don't paper over a new cycle with a function-level import; fix the layering.

### Route-list thumbnails

`RecurringRoute.thumbnail` is a precomputed blob (simplified path ≤ 64 vertices + the ten
weather fields per sample that `core.ride_quality` reads, `weather_code` and `felt_temp` among
them — frost needs the code and the temperature factor the felt value, and without them the list
would score from the thermometer alone and disagree with the map), written by the `refresh_route_thumbnail` task and
only *read* by `list_routes`,
which serves the path plus the worst sample's `ride_score` / `ride_label` and the ride's worst
rain and frost (`rain_level`, `frost_level`, `rain_probability`, `max_rain_rate_mm_h`,
`temp_min`) — never the raw samples. The rain and frost readings are the worst *point* of the
ride, not the worst-scoring sample: "will it rain on my ride" is a different question from
"what spoils it".

It is rebuilt after a geometry change, after each background scan (only briefing routes
departing within 4 h get one) and after every finished `ROUTE` forecast job, whose cells are
warm then. Without that last one, most routes would say "Noch keine Prognose" in the list
right after their forecast was shown.

Two rules hold this together:

- **Never fetch from the list path.** `compute_route_thumbnail` calls
  `compute_route_weather(cache_only=True)`, which uses `get_cached_forecast_cell` instead
  of `get_or_fetch_*`. The list polls every 60 s; the fetching accessor would hammer
  Open-Meteo once per cold cell per poll.
- **Ride-quality scoring is server-only.** See "Ride quality" below.

**The glyph is one colour: the worst sample's.** The whole line is painted
`scoreColor(thumbnail.rideScore)` — the same YlOrRd ramp as the map route line, for the
sample whose `rideLabel` `RouteListItem.qualityLabel` shows, so glyph and caption always
agree. Where along the route it changes is the map's job. At 40 px the ramp's pale good end (`#ffeda0`) all but
disappears, so the line sits on the theme-flipping casing the map uses (`CASING_*` in `rideQuality.ts`, shared by both —
change them there). The glyph has no legend
or hover, so the colour is never the only channel: the caption and the `aria-label` say the
quality in words. Do not remove that caption.

Grey is *data presence*, which is a fact about the data rather than a reading of the
weather, and it is deliberately off the warm ramp: a thumbnail with no sample the server
could score, or whose `departure` no longer matches the route's live `nextDeparture`, is
greyed out, rather than showing yesterday's weather as today's.

### Ride quality

`core/ride_quality.py` is the **only** implementation of the ride-quality score: the rain,
wind, temperature and frost curves, and `RIDE_QUALITY` (`weights` per factor, `sensitivity` —
how fast the score climbs the colour ramp). It is the app's own judgement and stays secret, so:

- **Nothing derived from the curves ships to the browser.** The API serves results only:
  per sample `ride_score` (0..1), `ride_label`, `wind_effort_level` and `frost_level`; per wind
  arrow `wind_effort_level` and `wind_effort` (0..1, arrow size); `summary.max_wind_effort_level`
  and `summary.max_frost_level`; per section `frost_level`; the thumbnail's worst `ride_score` /
  `ride_label` plus `rain_level`, `frost_level`, `rain_probability`, `max_rain_rate_mm_h` and
  `temp_min`. `frontend/src/utils/rideQuality.ts` only maps a score to a colour and places
  samples along the line. Never add a curve, weight or breakpoint to the frontend — a threshold
  in the bundle gives the curve away. A *level* is a word, never a number the curve can be read
  back out of; that is how `wind_effort_level` has always worked. The words are **codes**
  (`RideBand`, `WindEffortLevel`, `ImpactLevel` in `ride_quality.py`: `very_good`…`very_poor`,
  `tailwind`…`very_high`, `light`/`moderate`/`heavy`), and `ride_cause` names the factor that
  dominates, if one does. The SPA words them (`utils/levels.ts`) and branches on them; see
  "Internationalisation".
- **One config per profile.** `ride_quality.config_for(profile)`: every bike profile scores with
  `RIDE_QUALITY`, `hike` with `HIKE_RIDE_QUALITY` (`wind_source="gust"`: the wind factor reads gusts,
  not the cyclist's wind effort, and hike samples, summaries and arrows carry no wind-effort level).
  Assembly records the job's `profile` in `job.result` and in `departure_inputs` (from the geometry
  `plan_forecast_job` resolved); a result without it is a bike ride. Every scorer on read takes the
  config from there, or from the route or journey row. A hike never routes around headwind (`tasks._weather_prefs`), and
  "off the network" is `foot_network` for it (`road_prefs_model`).
- **Score on read, never store.** `core.jobs.forecast_view` (samples, summary *and* sections),
  `wind_arrows_at_detail` and `recurring_route._thumbnail_out` score the stored raw weather when
  serving, so a change to `RIDE_QUALITY` shows on the next request without rebuilding jobs or
  thumbnails. It is plain arithmetic, cheap enough for the 60 s list poll. Sections carry
  `start_index` / `end_index` so `forecast_view` can score each one's frost from the samples it
  covers; both are **optional**, because jobs stored before they existed must still validate on
  the way out.
- **Rain combines chance and amount** (`rain_impact`): the worse of the main run's rate through
  `RAIN_CURVE` and `curve(rain_if_wet) × pop ** (1 / rain_risk_aversion)`. The main run counts
  at face value; the ensemble adds the risk where it is dry. That is why thumbnails store `pop`
  / `rain_if_wet` and `compute_route_thumbnail` passes `include_uncertainty=True` (still
  `cache_only`) — without them the list would score the main run alone and disagree with the map.
- **Temperature is felt, frost is air.** The temperature factor reads `felt_temp`: the wind chill
  at riding speed (`wind.felt_temperature`, airspeed = the sample's support-average riding speed
  plus head- and crosswind). It falls back to `temp` for samples stored without it, and
  `departures.SAMPLE_FIELDS` / `thumbnails._SAMPLE_FIELDS` carry it, or the departure ranking
  and the list would score the thermometer. Frost stays on `temp`: ice is a property of the
  road, and wind chill does not cool a surface below the air. The key-ride tile shows the
  ride's felt temperature averaged over riding time (`meanFeltTemp`, and `weather.mean_felt_temp`
  for the briefing).
- **Frost is a safety factor, not a comfort one** (`frost_impact`): `FROST_CURVE` is steep around
  freezing, scaled by how wet the road is (`FROST_DRY_SHARE` on a dry one, the whole curve where
  the rain penalty is worst), and `FROST_CODES` puts a floor under it for freezing drizzle,
  freezing rain, snow and ice fog — that is the +2 °C freezing rain the thermometer would call
  harmless. It weighs slightly more than rain, so ice names itself as the cause. It overlaps
  `TEMP_CURVE`'s cold arm on purpose: cold counts once as discomfort and once as danger. Don't
  truncate `TEMP_CURVE` to "fix" that — it would recolour every cold ride that already reads
  correctly. Like the temperature factor, frost returns 0 and never `None`, so every job result
  and thumbnail written before it existed stays scorable.
- The weights need not sum to 1 (the score is clamped), so the rain weight is also how far rain
  alone can reach; a config may leave a factor out and `ride_score` reads weights with `.get`.
  Tests that check curve *shape* derive from or pin the config, so tuning `RIDE_QUALITY` does
  not break them. The frontend's `NiceChart` comfort band (14–22 °C) mirrors `TEMP_CURVE`'s
  flat part, and it applies to the chart's felt line.

### Ensemble central estimate

Past about two days the ensemble's central value verifies better than the single run, so
`compute_route_weather` moves each sample's temperature and wind toward it: weight 0 up to 48 h
lead, linear to 1 at 72 h (`uncertainty.ensemble_weight`, lead measured from the ensemble cell's
`fetched_at`). Rules that hold this together:

- **Blend before `compute_wind_profile`**, in the first loop beside the station correction, so
  headwind, wind power, felt temperature, frost, arrows and the summary all read the blended value.
- **Temperature is the pooled member median** (at weight 1 the chart's dotted line sits on the
  median line). **Wind is the median member speed along the mean vector's direction.** A median
  of directions is not defined. The mean vector's own length is no good as a speed: members that
  disagree on direction would cancel into a calm none of them forecast. When the members agree on
  no direction (`WIND_DIRECTION_AGREEMENT`), the single run's wind stays.
- **Temperature and wind only.** Rain and `weather_code` stay the single run's. The ride score
  already weighs the ensemble's rain through `pop` / `rain_if_wet`.
- **Every tier.** Ensemble cells are fetched for free accounts anyway. Only the spread is Pro.
- **Mark it.** Blended samples carry `ensemble_weight`. None means the plain single run.
- Journey weather routing (corridor cells) still reads the single run, and so does every sample
  past the ensemble's last hour (~7 days): `_members` returns None there and nothing is extrapolated.

### Weather-station correction

`core/stations.py`. Pro rides that overlap the next 2 h get temperature and rain probability
nudged toward nearby Weather Underground stations: station-now minus model-now, added at
each eta with a weight fading to 0 at 2 h (temp) / 1 h (rain). Wind is never corrected.
Rules that hold this together:

- **Only the `refresh_station_observations` task fetches.** `compute_route_weather` reads
  `get_cached_readings` only. It is one extra unit in `cells_total` and always settles
  as not failed — no stations nearby is a normal answer, and `cells_failed` would shorten
  the job's lifetime as if forecast data were missing.
- **Every call goes through `_spend_call`**, which fails *closed* (unlike `claims.py`):
  the free key allows 1500/day and 30/min, and going over can get it switched off.
- **Temperature is corrected in `forecasts[i]` before `compute_wind_profile`**; the pop
  correction happens where `pop` is resolved, so `_summarize` sees it.
- **Mark it.** Corrected samples carry `station_count`, the summary `station_corrected`,
  and the UI says so. Don't present a corrected value as the plain model.
- A done job whose ride is near now is reused for `STATION_JOB_LIFETIME` (10 min) only.

In tests, patch `core.stations._fetch_nearby` / `_fetch_observation`, and set
`WEATHERUNDERGROUND_API_KEY` with `patch.dict(os.environ, ...)` — `.env` may hold a real key.

### Provider rate limits

`core/ratelimit.py` budgets every external weather call in shared cache counters (the four
`cells` replicas fetch side by side, so a per-process limit would mean nothing). A `Limit` is a
set of fixed windows in the provider's own *weighted* calls. Open-Meteo counts a request as
variables × members of every model / 10, times days / 14, at least 1 (`grid.open_meteo_weight`,
its own `calculateQueryWeight`): one forecast cell costs ~2.3, one **ensemble cell 36**
(5 variables × 72 members, `ENSEMBLE_MEMBERS`; keep the counts in step with `ENSEMBLE_MODELS`).
`acquire` spends against every window or refuses and returns the wait.
Rules that hold this together:

- **The gate sits in `grid._fetch_and_store_*` and `grid._fetch_cell_batch`, inside the fetch lease and outside `@provider`.**
  That covers every caller, and a skipped call is `provider.throttled`, never a
  `provider.request`.
- **Adaptive.** A 429 goes through `record_throttle`. That starts one cooldown shared by every
  worker: `Retry-After`, else the end of the minute/hour/day the reply's `reason` names, else
  doubling from 60 s. "Too many concurrent requests" (the free API's per-IP limit on requests in
  flight) only pauses `CONCURRENCY_COOLDOWN` and adapts nothing: it says nothing about the budget.
  It also halves a factor on the *shortest* window, which climbs back 0.1 a quiet minute (halving
  the hour would lock a half-spent hour and send every cell to OWM over a minute-level 429). The first 429
  of a burst adapts, the rest don't. This is what corrects a weight we guessed too low.
- **Fail open for Open-Meteo and MET Norway, closed for OWM and Weather Underground**, for the same reasons as
  `claims.py` and `_spend_call` (which is now a thin wrapper over `ratelimit`). Open-Meteo's
  forecast and ensemble APIs share one budget.
- **`ProviderThrottled` must not subclass `httpx.HTTPError`/`ValueError`/`KeyError`**, or the
  fetch paths swallow it and fall back to OWM. That fallback on every 429 was the old bug:
  a 429 storm became a paid OWM storm.
- **A throttled cell task defers, it does not settle.** `refresh_*_cell` asks for
  `allow_fallback=False` / `raise_throttled=True` and re-enqueues itself with `run_after`
  (`attempt` + 1, at most `MAX_CELL_DEFERS`). It touches the job's `updated_at` so the stall
  check doesn't restart it and fan every cell out again. Waits of 1 s or less are slept in the
  worker. Over `MAX_DEFER_WAIT` (the hourly or daily limit), or on the last attempt, the forecast
  falls back to OWM within `OPENWEATHERMAP_DAILY_CAP` and the ensemble settles as failed.
- **The free Open-Meteo API is non-commercial.** `OPEN_METEO_API_KEY` switches to the
  `customer-*` hosts; set `OPEN_METEO_LIMIT_*` to the plan's limits too. Never log an httpx
  status error as is, because its message holds the URL and the key: use `ratelimit.describe_failure`.

In tests, use locmem `CACHES` (or the dev Redis's budget and cooldowns leak in), patch
`core.grid._fetch_*`, and patch `core.ratelimit._now` rather than `time.time`, which would move
the locmem cache's own expiry clock too.

### Forecast grid caching

Weather data is cached per ~1 km² cell (`lat_r`/`lon_r` rounded to 2 decimals) as raw
API responses in `ForecastCell.data` (JSONField). Multiple routes through the same cell
share one fetch. Cells expire after `MAX_CELL_AGE` (2 h) and are also rejected when their
stored `forecast_days` is less than what the caller needs.

- `get_or_fetch_forecast_cell()` — returns fresh cell (DB cache or live fetch)
- `extract_sample(cell_data, eta, source)` — source-aware dispatcher: "open-meteo" or "openweathermap"
- `get_or_fetch_ensemble_cell()` — same pattern for ensemble POP data
- `fetch_forecast_cells()` / `fetch_ensemble_cells()` — the same for many cells, with one
  Open-Meteo request for the missing ones (`latitude=a,b,…`; it answers a list, or a bare
  object for one location)

**Planning and the scan fetch cells in batches** (`tasks.CELL_BATCH`: 25 forecast, 8 ensemble
cells per `refresh_*_cells` task). The free API serves one request per IP at a time, so one
request per cell kept the `cells` workers waiting on each other. A batch only shares the round
trip: each cell is still claimed, counted in `cells_total`, settled (`_settle_cell(…, settled=n)`)
and fetched under its own lease (`cell_lease.try_lease`, never waiting — a cell someone else holds
goes through the single-cell path, which waits for the holder). The budget is spent cell by cell,
so a batch shrinks to what the window allows and the rest is deferred (`CellBatch.throttled`) under
the single-cell deferral rules. A failed batch request falls back cell by cell. A reply with a
different number of results than locations is a failure, never matched up by position. The single
`refresh_*_cell` tasks stay for journey corridors and random-ride areas. In tests, patch
`core.grid._fetch_open_meteo_batch` / `_fetch_ensemble_batch` for this path, and patch
`core.tasks.refresh_*_cells` (count cells with `core.tests.enqueued_cells`) where planning is tested.

**Ensemble cells sit on a 0.05° lattice** (`grid.ensemble_cell`, `ENSEMBLE_CELL_DEG`), not the
forecast cells' 0.01°: at 36 weighted calls each, one per forecast cell spent the hourly budget on
a few routes. The ensemble accessors take a forecast cell's coordinates and snap them themselves;
`get_cached_cell_keys` / `get_cached_cells` key their ensemble result by the forecast cells asked
for, so readers look up both kinds by the sample's `lat_r`/`lon_r`. Only planning and the scan
work in ensemble cells (`tasks._cell_kinds`): one task and one `cells_total` unit per ensemble
cell, however many forecast cells it covers. Anything else that reads `EnsembleCell` rows directly
(the system dashboard) must snap too.

### Data format difference (critical)

**Open-Meteo**: `hourly` = `{"time": [...], "temperature_2m": [...], ...}` — dict of parallel arrays **OpenWeatherMap**:
`hourly` = `[{"dt": 123, "temp": 15, ...}, ...]` — list of objects. **MET Norway**: `properties.timeseries` =
`[{"time": "…Z", "data": {"instant": {...}, "next_1_hours": {...}}}, ...]` — no `hourly` at all

`_from_open_meteo()` expects dict blocks; `_from_owm()` expects a list; `_from_met()` expects
`properties.timeseries`. All have `isinstance` guards rejecting the wrong format. `extract_sample()`
routes directly based on `cell.source` to avoid confusing them, and `departures.cell_covers` has
one branch per source.

### MET Norway fallback

`grid._met_fallback`, tried after Open-Meteo fails and before the paid OWM (`FORECAST_SOURCES`
is the order, for fetching and for a cache read that finds several sources). Free, commercial use
allowed, CC BY 4.0. Rules that hold this together:

- **MET's terms.** Every request sends `MET_USER_AGENT` (app and contact; without one MET
  answers 403). A place's last response is reused for the conditional request: its
  `_met_last_modified` goes out as `If-Modified-Since`, and a 304 (`MET_NOT_MODIFIED`) stores that
  response again under the new day. MET answers the same for any day, so any earlier row of the
  place will do. Budget `met_limit()`: far below the 20 requests a second MET asks to be told about.
- **Coarser than Open-Meteo.** Outside the Nordics it is a global model; steps are hourly for
  about 2.5 days, then six-hourly. `_from_met` gives a six-hourly step its own
  `precipitation_interval_s` (21600), so the rain rate stays right. Don't make it primary.
- **Symbol codes become WMO codes** (`MET_SYMBOLS`, `met_weather_code`), so the frost floor and
  the SPA's weather words work. WMO has no sleet: it counts as snow; snow with thunder stays snow.
- **A throttled Open-Meteo still defers first.** MET is only asked when the fallback is allowed
  (a failure, a long wait, the last attempt), like OWM before it.
- **Credit it.** `summary.sources` lists every provider a forecast used, and
  `ForecastSummaryCard` names and links them (`utils/weatherProviders.ts`). Open-Meteo's and
  MET's licences both ask for that.

In tests, patch `core.grid._fetch_met` on every path where Open-Meteo fails, or the test calls
api.met.no.

### `_from_open_meteo` clamping

If the eta falls outside the `minutely_15` block range (>2 h from nearest match), the
function falls through to the `hourly` block. The hourly block *always clamps* to the
nearest data point rather than returning `None` — slightly-off data is better than a
blank chart.

### OWM `rain` field

OWM One Call 3.0 returns `rain` as a float (mm); legacy 2.5 returns `{"1h": value}`.
`_from_owm()` handles both.

### Wind on the map

`NiceMap.vue` shows wind either as animated particles (`map/windParticles.ts`, after
mapbox/webgl-wind) or as the clickable arrows. A toggle in the wind legend switches between them,
and the choice is kept in localStorage. Under `prefers-reduced-motion` the default is arrows,
and the arrows remain the accessible mode (aria-label, effort popup). Rules that hold this
together:

- **The field is the route's own arrows, nothing else.** `utils/windField.ts` interpolates the
  job's `windArrows` (IDW on u/v) over a corridor along the line. Each arrow carries the wind at
  its own ETA. There is no API change and no extra provider call. Do not fill the whole
  viewport: off the route, the forecast has no wind to show. On a journey day the field also
  covers the alternatives, each from its **own** stage forecast's arrows (`JourneyDayPanel`
  fetches a variant's forecast only once its `forecastStatus` is `done`). `buildWindField`
  takes the routes as tracks, the selected one first so it keeps the wind where variants share
  road. A cell reads only its nearest track's arrows, no segment joins two tracks, and a track
  without arrows gets no corridor.
- **The corridor width follows the zoom, the field does not.** The field is built once out to
  `MAX_CORRIDOR_M` and stores each cell's distance to the route. `corridorHalfWidthM` picks the
  width for the current zoom (about `CORRIDOR_HALF_WIDTH_PX` on screen, at least `MIN_CORRIDOR_M`)
  and `corridorMask` fades it out every frame (smoothstep over `CORRIDOR_FADE_SHARE` of the
  width, particle density thinning with it), so zooming never rebuilds anything. A cell blends only
  the arrows around its nearest route segment, which relies on `windArrows` being in route
  order (they are: `wind_arrows_at_detail` walks the segments). That keeps the build near
  100 ms however wide the corridor is.
- **Particles move on the CPU, trails are WebGL.** Offscreen drawing (fade and particles into
  the trail texture) goes in `prerender`. `render` only composites, as MapLibre's custom-layer
  contract requires. The field-to-clip matrix is composed in float64, or particles jitter at
  street zoom.
- **The layer keeps the map from going idle.** Nothing may wait on `idle` while it runs: chip
  thinning follows `moveend` (and `renderRoute` thins at once when `fitBounds` does not move).
  The loop keeps running across a route or alternative switch; `setField` wipes the trails
  only for a new field object.
- `setStyle()` (theme switch) drops custom layers, so `renderWindParticles` runs again after
  `style.load`. The layer sits directly above `route-line`, below the basemap labels and
  the invisible `route-hit` layer.

### Basemap

Every map (`NiceMap`, the route editor, the system map, the GPX preview, the variants map) loads
its style through `map/basemap.ts`: CARTO Positron / Dark Matter by theme, or swisstopo
SWISSIMAGE aerial photos with Dark Matter's labels on top (`satelliteStyle`, a `transformStyle`
that keeps only the symbol layers). One choice per browser (`norain.basemap`), shared by every
open map and switched by `BasemapControl`. Rules that hold this together:

- **SWISSIMAGE is Switzerland only.** Its source's `bounds` keep MapLibre from asking outside
  CH. It is open government data and needs the "© swisstopo" attribution the source carries.
  Don't swap in a worldwide source without checking its terms: the app is commercial.
- **Imagery counts as a dark map** (`isDarkMap`): the casing, alternatives and wind particles
  take their dark colours. Use it instead of `$q.dark.isActive` wherever a map colour depends on the theme.
- **A style change goes through `applyBasemap`**, and the page re-adds its layers in `onReady`.

### Charts

The backend draws no charts. `frontend/src/utils/forecastCharts.ts` builds the temperature,
precipitation and headwind charts from the samples the job result already carries (`temp`, `rainRateMmH`, `pop`,
`headwind` and the ensemble p10/median/p90), so they need no
request of their own; `NiceChart.vue` adds the theme. Each metric is **one line**: the sample's
own value (main run near now, the ensemble centre from 72 h, see "Ensemble central estimate").
The band around it is the ensemble spread **recentred on that line**:
`value + (p10 − median)` … `value + (p90 − median)`. It shows spread width, not the ensemble's
absolute range, which the details panel still shows. Don't add a median line back. The chart design lives only there:
a chart that needs a new value needs it on the sample, not a figures endpoint. Results
stored before this still carry a `figures` key; `forecast_view` drops it.

**The selection is a route position, not a sample.** `composables/useRoutePosition.ts` holds one
share (0..1) of the route's distance per page; the map, the elevation profile and the forecast
charts each convert it to their own axis (line point, km or minutes) and write it back on hover,
anywhere along the route, not only at samples. `selectedSample` is just the sample nearest to it,
for the details panel. Sample places are measured on the forecast's own `line`; the map pins a
finer detail line to the same samples (`remapProgress`), so don't measure positions on whichever
line happens to be drawn.

### Routing graph

GraphHopper is bike-only (`bike`, `ebike`, `fast_ebike`, each with CH); `ROUTING_PROFILES`
in `api/route_weather.py` must list the same names. The container **never builds a graph by itself**:
with an empty `graph-cache` it exits with an error. `just build-graphhopper-graph-from FILE` (a bike-filtered file
in `ROUTING_OSM_IMPORT_DIR`) empties the cache, runs the entrypoint's `build` command and starts it
again; production too. A graph can also be built on another machine and copied over
(`docs/how-to/build-routing-graph.md`). `just build-graphhopper-graph-host FILE` runs the same
import without a container (`scripts/graphhopper-host-build.sh`, for macOS), with the jar copied
out of `GRAPHHOPPER_IMAGE`. It repeats the entrypoint's `build()`, so change both. `artifact.py`
takes its paths from env overrides, and `begin --terrain-as` records the terrain as
`/osm_data/elevation/<hash>` so the release serves in the container.
GraphHopper calls the build "import". It refuses a graph built with a different config or jar,
so any change to `graphhopper-config.yaml` or `data/graphhopper/models/` means rebuilding.

Every build reads the bike-filtered file it is given, in `ROUTING_OSM_IMPORT_DIR` (the host folder
mounted at `/osm_data`, required in `.env`), never an extract itself:
`docker/graphhopper-filter-osm.sh` (osmium) keeps only the ways, nodes and cycle-route relations
the bike profiles use (a third of the Swiss file, same speeds). A build from
`bike-<OSM_DATA_URL's file name>` downloads `OSM_DATA_URL` (only ever an unfiltered extract) and
filters it, and redoes that when the extract is newer; any other file is used as it is.
`just osm-filter-many-raw-pbf-into-one FILE…` runs the same script on
local files (several are filtered one by one, then merged) and writes `ROUTING_OSM_FILE_FILTERED`,
which it requires to be set, plus the matching `pois-<name without bike->.geojsonseq`; it builds
no graph, `just build-graphhopper-graph-from FILE` does.
If a profile ever needs a tag the filter drops, add it to that script and rebuild.

**Terrain is zoom 15 with a zoom-12 fallback.** `download-elevation-for` writes `terrain.pmtiles`
(Mapterhorn zoom 15, which has gaps: Italy, the Balkans, the east) and `fallback.pmtiles`
(the planet archive's zoom 12). `FallbackElevationProvider` (patched into the jar in the
Dockerfile, with a `grep` guard) reads the fallback wherever zoom 15 is NaN. Without it
GraphHopper stores 0 m there (`OSMReader` default elevation), and the slope next to real
heights becomes a cliff that `average_slope` punishes. The import and `/elevation` must both
go through `withFallback`, or saved paths read gaps the graph filled. Gaps are counted in the
manifest's `coverage`, never an error, and per road cell in `cell_coverage.json` (directory lookups
only; the system dashboard's elevation layer). A release keeps its OSM file's road cells as
`cells.json` (terrain may cover more). Heights are baked in at import, so new terrain means a
new graph. Terrain covers only the zoom-11 cells holding a node of the file (`node_cells` →
`region.geojson` → `pmtiles extract --region`), never its bounding box. GraphHopper reads just the
zoom-15 tile under each node (interpolation stays inside the tile, long-edge sampling is off).
Turning on `long_edge_sampling_distance` would read points between nodes. `check` requires the
file's cells to be a subset of `cells.json`.
An import looks up every node's height once, in tile order, before it reads the ways
(`PrefetchedElevationProvider`, wrapped around `withFallback` when `datareader.file` is set). OSMReader
asks way by way, in way-ID order, and a continent's decoded tiles dwarf RAM (Europe: 187 GB), so
without it nearly every lookup is a random disk read (4 h). The table costs ~30 bytes of heap per node.
On a cold tile cache (fresh terrain) the read-ahead threads also decode the missing tiles, one core each,
each with a `PMTilesElevationProvider` of its own (it is not thread-safe). The `.tile` file is the
hand-off: the patch writes it to a temp file and renames it, and the lookups wait for their tile's task.
A single thread decodes ~90 tiles/s, which is hours for Europe.

A Europe graph is ~12 GB; the heap needs that plus the OSM reader and the height table (~5 GB).
On btrfs, never build a large area with `GRAPHHOPPER_BUILD_DATAACCESS=MMAP`: LM rewrites every
page of its memory-mapped landmark files, btrfs reserves space for each write, and LM stalls
for many hours (`handle_reserve_ticket`), with `chattr +C` and with `vm.dirty_*` raised alike.
Use `RAM_STORE` (Europe: 20 GB heap, 24 GB limit). `chattr +C` on the releases folder still stops
the fragmentation (`docs/how-to/build-routing-graph.md`, "Large areas").

Numeric GraphHopper settings never go through `-Ddw.`: Dropwizard passes them as strings and
`PMap.getInt` ignores a string, so the default applies silently (0 urban-density threads fails the
import after pass 2). The entrypoint writes the build thread counts into a copy of the config instead.
A string key may go through `-Ddw.` only if `graphhopper-config.yaml` already declares it (empty is
fine): Dropwizard replaces an existing dotted key and nests any other, which GraphHopper never reads.
The undeclared `pmtiles.fallback.location` left every zoom-15 gap (all of Tuscany) at 0 m.
`docker/tests/test_graphhopper_config.py` checks every override; the smoke test probes heights in
cells without zoom 15.

The whole download-and-import workflow is in `docs/how-to/import-geodata.md`.
`just photon-import FILE…` imports several Photon dumps into **one** index (one dump per country).
Photon's import takes one file and drops what the index already holds, so the entrypoint
streams them as one, keeping the header and country-list lines from the first dump only.
`PHOTON_REPLACE_INDEX=true` builds the new index beside the old one and swaps it in only when
the import worked.

**Ride speed lives in those model files**, nowhere in Python: every eta and every
`rider_speed` comes from the travel times GraphHopper returns. Each profile's `speed` block
starts from `bike_average_speed` (road type and surface), scales it, applies the
`average_slope` rules and caps it — about 18 / 22 / 32 km/h on mixed roads. `bike` keeps the
jar's `bike.json` + `bike_elevation.json` and adds our factor in `bike_speed.json`, which
must stay **last** in `custom_model_files` or the slope limits cut it. After a change, saved
routes keep their old times until `refresh_route_geometry` runs for each one.

### Route editing (via points)

`RecurringRoute.via_points` is `[[lon, lat], ...]`, in riding order. GraphHopper routes
`start → via… → dest` (`RecurringRoute.routing_points`, `weather.routing_points`), so speed
and every eta still come from GraphHopper. Changing the via points in `update_route` clears the
geometry and enqueues `refresh_route_geometry`, like a changed start or profile. The new
`geometry_fetched_at` is in the job params (`start_forecast_job`), so no old forecast is reused.
The return journey gets the via points reversed, set in `_save_return` like its swapped
endpoints. It always rides on the outbound days (a restriction on purpose): only minute and hour
come from its own schedule (`_on_outbound_days`), in `_save_return` and in `update_route` on a
return route alike, so a change to the outbound days carries over. To edit it, the user reshapes the outbound route; the
UI offers no editor on a
return route.

The editor (`components/RouteEditorDialog.vue`) draws its line from `POST /api/routes/preview`,
the only user-facing HTTP request that calls GraphHopper itself. An editor cannot wait on a queue.
It returns the line, distance and time only, with no sampling and no weather. The editor
calls it once per finished drag (debounced, and a new call cancels the previous one), and the
server limits it per account (`PREVIEW_LIMIT_PER_MINUTE`, failing open like the claims). A saved
route's geometry still comes only from the task. Both go through `weather._route_body`, so a
request-level change, such as the planned weather-aware custom model (which needs CH off, so
LM/hybrid), reaches the preview and the saved line alike. Don't build a second GraphHopper
request body elsewhere.

### Journeys

A journey is a one-off ride over one or more days: start, end, date, a limit per day and per
leg (time or distance), the POIs wanted on every leg, lodging kinds, road and weather
preferences. `plan_journey` (queue `default`) picks the day ends one day at a time (`tasks._plan_day_ends`: route the
remainder, choose lodging, start the next day there) and
hands over to `plan_journey_routes`, which gets each day's GraphHopper alternatives and, per
alternative, fills POI gaps and chooses breaks (`journey_planner.JourneyPlanner.stage`). Rows:
`Journey` → `JourneyDay` → `JourneyStage` (one per alternative, geometry like a
`RecurringRoute`). Rules that hold this together:

- **POIs are not in the graph.** The bike filter drops standalone amenity nodes, so
  `docker/osm-extract-pois.sh` (`just poi-extract-from-unfiltered-osm-pbf`) extracts them from the *raw* extract and
  `manage.py import_pois` (`just poi-import-into-db`, which runs in `worker-default` under a prod
  `COMPOSE_FILE` because the VPS host has no GDAL) replaces the `Poi` table in one transaction.
  It is a bulk load: drop the secondary indexes (read from the catalog, so their names survive),
  `TRUNCATE`, `COPY`, rebuild. POI readers wait on its lock until it commits. Don't add
  `db_index` to `Poi` fields: the unique (osm_ref, category) and the location GIST cover every query.
  `POI_RULES` in `core/pois.py` is the one tag map; a test checks the script filters every tag
  in it. Journeys store the POIs they use as JSON, never as FKs, so a re-import is free.
  An object gets one `Poi` row per matching category (unique on `osm_ref` + `category`): a
  vending machine selling drinks and sweets is both, so wanted categories stay ANDed per leg
  and one machine satisfies several. Extra-tag conditions match list items (`vending=a;b`).
  Postgres jsonb arrives as text on a raw cursor (Django's loader): parse it.
- **Request custom models only penalise** (`multiply_by` ≤ 1). GraphHopper runs LM without CH,
  and LM is only correct for a model that makes edges more expensive. "Prefer the cycle
  network" is therefore `avoid_off_network`, and "prefer it hilly" (`climbing="hilly"`, the
  random-ride form's "Gelände") makes the flat dearer in both directions instead of climbs cheaper. Every GraphHopper
  request still goes through
  `weather._route_body`; `_route` keeps a request without a model at `(profile, points)`.
- **POIs steer the route by via points, not by the custom model.** "Water once per leg" is a
  rule about the whole path and GraphHopper weighs edges. Every chosen POI (lodging, gap fix,
  break) is a via on the final line, so the way there and back is ridden, timed and checked.
  Each wanted category gets a visit attempt even on a day shorter than the leg limit (or
  without a leg limit). Successful visits appear as stops, not just detour markers.
  Search 60–85% into the available leg first, then 40–95%, then the whole leg if no evaluated
  candidate works. Apply the six-candidate cap separately within each band; cheap early POIs
  must not crowd out useful later stops. Within a band, prefer facilities within 400 m along
  the line of another committed stop, routing through each. Missing categories get a visible reason.
  A POI near the line is not a visit until the line is routed through it. The actual routed
  waypoint must be within `MAX_POI_SNAP_M` (25 m) of the POI; a successful GraphHopper reply
  that snaps it to a distant road is rejected, including previously committed POIs in an insertion.
  POI searches and projections use the current search window, so the same place can be visited
  again on the return leg. Segment projections are converted to cumulative geodesic metres.
- **Detours are routed, never guessed.** Self-hosted GraphHopper has **no `/matrix`** (only the
  hosted commercial API does). A candidate's cost is a local insertion: route ~2 km before it →
  POI → ~2 km after it (`journey_planner.INSERT_SPAN_M`) against the path's own time between
  those points, so an alternative keeps its road. `offset_m` only orders the prefilter (top
  `MAX_ROUTED_CANDIDATES`); only candidates that routed are eligible — a failed or unchecked
  one never wins, whatever its offset. Pair requests go through `weather.route_legs`: uncached (they would evict the
  planning pass's geometries from the LRU), through `_route_body` with no
  points, with the same model as the path, and with a limiter and memo created **per planning
  invocation** (`RoutingBudget`): `async_to_sync` may give each task a new event loop, and a
  module-level semaphore would stay bound to a dead one. `MAX_PLAN_ROUTE_REQUESTS` caps a plan.
- **Limits are time and distance, on the final line.** `journey_geometry.LineMeasure.boundary`
  takes the tighter of both from each break, never one converted distance for the day.
  `check_limits` measures the final geometry; `LAST_DAY_SLACK` applies to the last day's day
  limit only. An alternative over the day limit is dropped while another keeps it; a sole one is
  kept with `limit_overruns`, and `rank_day` says which limit, in minutes or km. Gaps are
  stored as `{category: {s, m}}`; old rows hold metres only and must still rank.
- **Via points are tracked by visit, not by position.** Waypoint indices come from GraphHopper's
  `leg_time` details of the route in hand (`waypoint_indices`); a loop that passes a via twice
  must not consume it early. Every remainder, day corridor and final line routes through the
  vias still ahead. A day with a via gets one path: alternatives take two points only.
- **Alternatives are per day.** `alternative_route` takes two points only and exceeds the
  2 M node cap beyond ~130 km, so a failure falls back to one path.
- **Weather-aware routing** (Pro, days within `WEATHER_ROUTING_DAYS`) is GraphHopper's own,
  see "Weather routing in GraphHopper" below: the day's corridor cells by the hour go with the
  request as a `weather` field (`weather_routing.weather_field`). Corridor cells are ordinary
  `ForecastCell`s on a 0.05° lattice fetched by `refresh_forecast_cell`; `plan_journey_routes`
  re-defers until they are warm (bounded), then reads `cache_only`. A weather day's first stage
  is the way around the weather, then GraphHopper's plain alternatives to compare it with (`alternative_route` cannot
  route by time); a refused weather request routes plainly.
- **Stage weather is a normal forecast job** (`ForecastJob.Kind.JOURNEY_STAGE`, geometry from
  the stage row, ownership checked in `plan_forecast_job`, never station calls). Reading a
  journey starts or joins its stage jobs; the departure window reuses the departure
  comparison (Plus). `rank_day` ranks a day's alternatives **on read** and serves results
  and reasons only, never the weights.
- **Revisions.** Every edit or re-plan bumps `plan_revision`; a planning task writes only
  while its revision is current, and replaces the days in one transaction.

### Weather routing in GraphHopper

The Dockerfile patches GraphHopper (`docker/graphhopper/weather/`, installed by a `grep`-guarded
`sed` in `GraphHopper.doCreateRouter`, like the elevation patch): `WeatherRouter` solves a request
that carries a `weather` hint with `WeatherAStar`, a time-dependent forward A*, and every other
request exactly as before. An edge costs its weight times the weather where the rider is halfway
along it, *when* they are there: rain by the cell and headwind along the edge's own bearing,
interpolated in space and time, and, when the rider asked for sun, the shade there. Rules that
hold this together:

- **GraphHopper never fetches weather.** The backend builds the field from the cache (`weather_routing.weather_field`)
  and sends it with each request, so the provider budget and
  the fetch lease stay in one place. A null is a cell without data and counts as no weather.
- **The judgement stays in Python.** The field carries weight multipliers (`rain_curve`,
  `headwind_table`, both from `ride_quality.ROUTING_*`), never the raw curves; Java only
  interpolates. Every multiplier is ≥ 1, which keeps the landmark lower bound valid, so the A*
  still uses LM. CH can never serve it: weather changes by the hour.
- **Time runs forward, leg by leg.** A bidirectional search cannot know when it arrives, so a
  weather request with `alternative_route` is refused (400). Via and round-trip legs start at the
  departure plus the riding time of the legs before (`LegClockPathCalculator`). The riding times
  in the reply stay the profile's: weather changes the choice of road, never the eta.
- **Weather requests are uncached** (`weather._route`): the field is large and asked for once.
  `JourneyPlanner.route` sends it only for a whole-day request; local POI insertions and the
  separate-leg fallback route without it, because the field's clock starts at the departure.
- **Shade is split: clouds are ours, the line of sight is GraphHopper's** ("avoid shade",
  `avoid_shade`). The field carries `sun` (Open-Meteo's hourly `sunshine_duration` as a share of
  the hour, OWM's clear sky) and `shade` (`ride_quality.ROUTING_SHADE_PRIORITY` as a multiplier).
  `Shade.java` works out the sun's position (`SunPosition`) at the edge's midpoint and time, and
  marches towards it over the terrain archive the graph was built with, plus, within 300 m, the
  tree heights of `canopy.pmtiles` beside it (`docker/graphhopper-canopy.py`, from OSM woods,
  `just canopy-from-unfiltered-osm-pbf`). `Shade` finds that file next to `terrain.pmtiles`,
  never through a config key: a served graph keeps its build's config, and `-Ddw.` nests an
  undeclared key where GraphHopper never reads it. An edge costs `1 + (shade − 1) × (1 − lit)`, lit being
  the sunshine where the line of sight is clear and 0 where it is blocked. After sunset every
  edge is 1: nothing to choose. Horizons are kept across requests in a fixed table (terrain does
  not change); the archives are opened once and shared under a lock. `sunshine_duration` is
  fetched **hourly only**: one more variable in both blocks would cost Open-Meteo weight twice.
  Cells stored before it carry no sunshine, which counts as no weather. Without a canopy
  archive, trees cast no shadow; without terrain (`graph.elevation.provider` not pmtiles), only
  the clouds count.
- `WeatherAStarTest` and `ShadeTest` run in the Docker build before packaging, and the smoke
  test checks that a dry field changes nothing and that `alternative_route` is refused.

### Random rides

The third mode, next to commute routes and journeys: the user gives a start, loop or not (then a
destination), a length (riding time or distance), a profile, an optional direction and a date,
and MeteoLane generates the ride (`core/random_rides.py`). A random ride is a `Journey` with
`kind="random"` and `random_prefs` (`round_trip`, `heading`, `seed`, `consider_weather`): one
day whose stages are the generated candidates. The SPA lists them at `/random` and opens them on
the journey page. It has two modes:

- **Picking (every tier, the default).** `PICK_VARIANTS` (3) variants, no forecast at all:
  `get_journey` starts no stage job and the stage forecast endpoint answers 409. The page shows
  `RandomVariantPicker`: the variants on a map (`VariantsMap`) with the stops the planner routed
  them through for the wanted POI categories (and which each one misses), their elevation
  profiles in one chart (`ElevationChart` with the stages as alternatives, stored heights, no
  routing) and each one's climb (`JourneyStageOut.ascent_m`). Each variant the rider ticks is saved with
  `POST /journeys/{id}/stages/{stage_id}/route` as an imported route on the variant's exact line (heights included, the
  stage's riding time as its duration) with a weekly schedule prefilled
  from the ride's day and departure. That goes through `create_route`, so the route quota (402)
  and the geometry task apply, and the forecast is the route's own from then on.
  The saved route keeps the variant's stops (`journeys.stage_stops`: break POIs and gap-fill
  detours) in `RecurringRoute.stops`, set by the server, never by the client, and cleared when
  the line changes. Every GPX export writes stops as `<wpt>` before the `<trk>`: a saved route's
  `/routes/{id}/gpx`, and `/journeys/{id}/stages/{stage_id}/gpx` (journey day or variant, plus
  the night's lodging). A public copy never gets them.
- **Considering the weather (Plus, `weather_routing`).** The tier's alternatives, each forecast (`JOURNEY_STAGE` jobs)
  and ranked on read (`rank_day`), optionally routed around the weather (`weather_prefs`). `_random_prefs` stores
  `consider_weather` off without Plus, and
  `RandomPrefs.weather_mode(limits)` checks it again when planning and reading, so a downgraded
  ride falls back to picking.

Rules that hold this together:

- **Planned in `plan_journey`.** `_plan_random_ride` branches off before the day cutting, with
  no `plan_journey_routes`. When the rider chose weather routing (Plus, within
  `WEATHER_ROUTING_DAYS`), it first warms the ride's area (`random_rides.area_cells`: the box
  around both ends widened by a quarter of the length) and re-enqueues `plan_journey` until the
  cells are warm, bounded by `MAX_CORRIDOR_ATTEMPTS`; then every candidate routes through the
  field, and one GraphHopper refuses is routed plainly instead.
- **A loop is a GraphHopper round trip** (`weather.build_round_trip`, through `_route_body`,
  uncached): GraphHopper picks the waypoints and avoids riding a road twice. It needs LM or
  flexible mode, never CH. **Point to point** routes start → one via → destination, the via on an
  ellipse around both ends sized to the missing length (`detour_via`).
- **Time means the profile's pace.** The length is the day limit (`max_day_seconds` /
  `max_day_distance_m`), and a time target is compared with GraphHopper's riding time, the same
  one every eta uses. `NOMINAL_SPEED_KMH` is only the first guess (and the form's "≈" hint);
  `size` routes, measures and rescales, at most `MAX_SIZING_ATTEMPTS` times.
- **Candidates differ by seed and heading** (`headings`, `candidate_seed`), deterministic in the
  seed: an edit re-plans with the same dice, `POST /journeys/{id}/plan` throws new ones. The seed
  is the server's; the client never sends it.
- **Tiers:** picking gives everyone `PICK_VARIANTS`; the weather mode gives as many candidates
  as `max_journey_alternatives`. Random rides have a count of their own, `max_random_rides`,
  separate from `max_journeys`. The kind is fixed at creation.

### Public routes, photos and comments

A route can be published (`RecurringRoute.visibility`, `public_slug`, `privacy_zone_m`) and is
then readable by anyone at `/r/<slug>` and listed under "Entdecken" (`pages/explore.vue`). The
owner's side is `core/api/community.py` `owner_router` (session auth), the visitor's side is
`public_router` (optional session auth for reading; commenting, liking, copying and the
weather need a session). Rules that hold this together:

- **Nothing public reads `polyline`.** A commute starts at someone's door and its schedule says
  when they leave. Every public answer (detail, list, card path, elevation, a visitor's
  forecast, a copy into the visitor's routes) is built from `public_routes.public_geometry`: the
  line between two circles of `privacy_zone_m` round start and destination (circles, so a round
  trip or a route that doubles back past home is trimmed past its last pass). The start and
  destination, their names, the via points, the schedule and the route's UUID never leave in a
  public reply; a test greps for each. Less than `MIN_PUBLIC_DISTANCE_M` left is a 422 on publish
  and a 404 on read. The list's `bbox` filter is checked against the public line too.
- **A visitor's weather is an ordinary forecast job** (`ForecastJob.Kind.PUBLIC_ROUTE`): owner =
  the signed-in visitor, so it is shaped for the visitor's tier; geometry from `public_geometry`,
  with `privacy_zone_m` and the geometry revision in the params; planning fails once the route is
  private again. Never station calls: any number of visitors can open one route.
- **Photos are re-encoded from pixels** (`core/photos.py`): no EXIF, no GPS, at most 2048 px, and
  only JPEG/PNG/WebP. A photo's position is only what the uploader sends (the SPA reads it from
  EXIF in the browser, `utils/exifGps.ts`, when "Aufnahmeort übernehmen" is on), and the public
  page drops it inside a privacy zone. Files are never under a static URL: `/api/photos/{id}/…`
  checks that the route is public or the viewer owns it. `core/signals.py` removes the files after
  the row is deleted (photo, route or account). `max_route_photos` is in `entitlements.py`.
- **Comments** are the author's to edit and the author's or the route owner's to delete; staff
  moderate in the admin. Posting is limited per account per minute (fails open).
- **Copy** ("In meine Routen") saves the public line as an *imported* route of the visitor's, so it
  goes through `create_route` and its quota and never contains the hidden ends.

### Coverage page and votes

`/coverage` (`pages/coverage.vue`, `core/api/coverage.py`, rules in `core/coverage.py`) lists the
areas Meteolane covers and lets **anyone**, signed in or not, vote for one it does not cover yet
and leave an address to be told when it is. Rows are the admin's (`CoverageArea`: covered,
planned, or a region below a country listed for votes); a country needs no row to be voted for,
its ISO code is enough (`core/countries.py`). The seed migration marks Switzerland covered.
Rules that hold this together:

- **Votes are one per voter and area, and store no IP.** An account votes as `user:<id>`, a
  visitor as `anon:` + a hash of the random token in the httpOnly `meteolane_voter` cookie.
  Against cookie clearing, the IP counts in the cache only (30 votes an hour, one anonymous vote
  per area a day, released when a withdrawal is settled). Both fail open, like the other per-minute limits.
  On top of that, a recognised browser votes once per area for 30 days, and unrecognised,
  keyless, suspicious or young-key ones share 3 votes per IP a day (see "Browser recognition").
  An IPv6 address counts by its /56.
- **The ledger is blind** (`COVERAGE_BLIND_LEDGER`). A vote the limits refused is stored
  anyway (`CoverageVote.accepted=False`) and shown to its voter as cast; a withdrawal only
  sets `withdrawn_at`. The daily `coverage.settle_votes` (in the hourly pass, once per UTC day)
  deletes withdrawn rows, releases their claims and publishes the tallies; without them in the
  cache, only votes from before the day began show. So no reply says whether a vote counted:
  don't add a count, a reason or a status that does. Only the per-IP flood limit answers 429.
  The page tells every voter the same: votes are counted once a day. A refused vote cast again
  is not tried again (a flip would give it away); signing in is the way to a vote that counts.
  With `COVERAGE_TALLY_STEP` at 1, a quiet area's next-day change still shows a single vote.
- **Double opt-in.** Anyone can type anyone's address, so nothing but the confirmation goes to
  it until its link was used; the reply is `pending` whether the address was new, pending or
  confirmed, and a confirmation is sent at most once per `RESEND_AFTER`. Only a signed-in
  account's own verified address skips the confirmation (`confirmed`). The links go to the SPA
  (`/coverage?confirm=` / `?unsubscribe=`), which POSTs the token, so a mail scanner fetching
  the link confirms nothing. Unconfirmed rows are purged by the hourly pass after 7 days.
- **Saving an area as covered mails the waiting addresses once**, whoever saved it:
  `core.signals.notify_coverage_subscribers` enqueues `notify_area_covered` after commit, and
  `coverage.notify_covered` deletes each row it mailed (rows locked, skipped when locked).
- **Names are the reader's.** The API serves codes; the SPA names a country with
  `Intl.DisplayNames` (`utils/coverage.ts`), a region by the admin's `name` / `name_en`. The
  mails use `core/countries.py`, generated from the same ICU data, in the language of the page
  the visitor asked from. Don't hand-edit that table; regenerate it.
- The mail-sending, token and vote endpoints have Caddy flood limits in
  `deploy/auth-ratelimit.caddy`, like the auth endpoints.

### Browser recognition

Our own library (`frontend/src/lib/browser-fingerprint`, `core/fingerprinting.py`; no
fingerprinting package, ever), used for anonymous coverage votes and for the sign-up and
sign-in-code throttle (`core/auth/device_throttle.py`, middleware in front of allauth). The
protocol and tables are in `docs/reference/browser-fingerprinting.md`. Rules that hold this together:

- **The server is the arbiter; every probe value is the client's word.** Robustness comes
  from what the server can check: a signed single-use challenge, a SHA-256 proof-of-work
  (`BROWSER_POW_BITS`, dearer while one IP churns new keys, capped at 20 bits: a phone's
  ~4 s, the SPA's wait), consistency
  against a worker, a fresh iframe (whose clean `Function.prototype.toString` inspects the
  page's getters), the engine's own error wording and the request's `Sec-CH-UA*` headers.
  Scripted forgery can only be made expensive and stays bounded by the per-IP limits, which
  all remain. Say so; do not claim more.
- **Never shed keys.** Fewer claim keys mean looser limits, so nothing a browser can trigger
  at will may leave it with fewer: a `suspicious` browser keeps its own `b:` key and pays the
  IP's shared count on top (never its `f:` or `p:`: those come from values that lied); every
  `low` browser gets a `p:` key; a browser without keys pays the IP's count. A new rule that
  demotes a browser must keep this true.
- **New lie checks start in observe mode** (`BROWSER_OBSERVE_ONLY`): named and counted, but
  tier-neutral until their *solo* count (fired with no enforced lie or automation) stayed at 0
  for 14 days and a real-device matrix was clean. That gate is for lies; the observed `low`
  ones (`fetch_metadata_missing`, `fingerprint_common`) fire on honest browsers by design and
  are enforced from their counts by judgement, since enforcing them only trades `f:` for `p:`. Add every new `suspicious` indicator there first,
  with a Playwright case showing honest Chromium, Firefox, WebKit and Firefox-RFP don't trip it.
- **Time is a cost too.** A key younger than `BROWSER_KEY_AGE` (20 h, from `browser:seen:…`) is
  not `established` and pays the IP's count for votes on top of its own keys (sign-ups too with
  `BROWSER_KEY_AGE_SIGNUPS`). Per-IP counts take an IPv6 address by its /56 (`ip_floor`), which
  needs Cloudflare's Pseudo IPv4 not to overwrite headers (the stats count class E addresses).
- **The relay meter observes only** (`BROWSER_PATH_METER`): three server-timed echo round
  trips against Cloudflare's own edge round trip (a Transform Rule's `X-Ml-Edge`, which Caddy
  strips from anything not from Cloudflare). It never changes a tier or a limit; enforcing it
  is a separate change behind the PoC gates in the reference doc, and must fail open.
- **Nothing diagnostic goes to the client.** `verify` answers `{expiresIn}` only; the
  assessment sits in the cache behind a random receipt id in an HttpOnly cookie. Read it
  with `get_browser_assessment(request)`. Never add tier or indicators to a reply or a
  readable cookie: they tell a forger which check to fix. The one exception is
  `GET /api/system/browser` (the "Browser-Erkennung" panel on `/system`): behind
  `has_system_access`, and only the requesting browser's own result, with id prefixes only.
  `GET /api/system/browser/stats` (same access) serves daily counts under fixed names, never
  a browser, key or value. The sign-up limit is the same per key and per IP, so the number of
  accepted requests does not reveal the tier either.
- **Tiers.** `suspicious`: a lie (or automation). `low`: honest but shared by many devices
  or degraded (iOS, canvas noise, a canvas-only iframe difference, software rendering,
  missing client hints, ephemeral key, unavailable checks). `high`: the rest. **A `low` browser never deduplicates on its
  fingerprint**, only on its own key (`device_keys`): look-alike devices would block each
  other. Churn raises the proof-of-work, never the tier (CGNAT and campus networks).
- **The stable set excludes what changes by itself.** `fingerprintId` hashes canvas (unless
  noisy), audio, graphics, fonts, hardware, math and media. `display` (no DPR) and `locale`
  (no languages) count towards similarity only. Don't add viewport, zoom, battery or network.
- **Claims stack on the IP limits and are recorded per voter**, so a withdrawal releases
  exactly what was claimed. The device claim runs before the IP claim and is released when
  the IP claim refuses.
- **Every `low` browser claims per network too.** It gets a `coarsePrint` (what renderer
  noise leaves alone, plus the user agent; the user agent alone when nothing else was read),
  and `device_keys(assessment, ip)` adds a `p:` key = coarse print + IP (IPv6 /64) + UTC day.
  A new Safari private tab (own storage, per-tab noise) is not a new device; look-alikes
  behind one CGNAT share it for a day, never stricter than the per-IP fallback. A `high`
  fingerprint shared too widely (`note_fingerprint`: 3 networks, or 5 new keys an hour) turns
  `fingerprint_common`; once that is enforced, it trades `f:` for this `p:`. Pass the IP wherever `device_keys` is
  called. Canvas and audio noise are found by known answers (`canvasIntegrity`,
  `audioIsExact`); `repeated` cannot see a noise salted per tab.
- **Tamper checks compare with the clean frame, not with wording.** A native called on the
  wrong object throws; only whether it threw and the error's kind count (V8 words Intl's
  refusal per realm). The stack check needs a *method* as marker that does not call in tail
  position: Safari's proper tail calls drop such frames, and JavaScriptCore leaves arrows under
  computed keys unnamed. Only Playwright in all three engines shows whether a check is honest.
- **Collect on demand only.** The SPA calls `prewarmRecognition()` where a vote or sign-up
  may follow and `ensureRecognized()` (waits ≤ 6 s, never rejects) before the request;
  there is no background collection on other pages.
- **Raw probe values are never logged or stored**; log tier and indicator names at most.
  Everything fails open on a Redis error, except the nonce claim, which is the replay guard.
- Under `manage.py test`, `BROWSER_FINGERPRINT_ENABLED` is off (`settings/development.py`);
  recognition tests switch it on per class with locmem `CACHES`. The proof-of-work encoding
  is pinned for Python and TypeScript alike by `__tests__/pow-vector.json`; Playwright
  (`playwright.fingerprint.config.ts`) runs the real library in Chromium, Firefox and WebKit.

### Recurring routes

Users configure routes with cron schedules. `next_departure()` computes the next departure,
`forecast_available_at()` checks if it's within the 16-day Open-Meteo window.

**A ride under way stays the route's departure.** `nextDeparture` / `returnNextDeparture`,
the thumbnail and the pre-build all use `schedule.current_departure(cron, ride_seconds(route))`:
the departure stays put until the riding time has passed, so the list says "jetzt" and the page
keeps that ride's forecast. Keep these three on the same function, or the thumbnail greys out and
the pre-built job misses the page's key. The route page is the outbound route's: the tab is
`?direction=outbound|return` (`useRouteQuery`), and without one it is the direction riding now or
next (`nextRideId`); a link to a return route redirects to the outbound one's return tab.
`run_forecast_scheduler` runs `refresh_forecasts` hourly, which now only *queues*
`refresh_upcoming_forecasts`; that fans out to one `scan_route_forecasts` task per eligible
route, so one slow route no longer holds up the pass, and it purges the Stripe ledger and
expired `ForecastJob` rows. A successful SPA sign-in (allauth's `user_logged_in`, `core/auth/signals.py`) also enqueues
`refresh_user_forecasts`, which scans just that account's routes through the same
`_prewarm_routes` quota; a failure to enqueue never fails the sign-in.

Both passes also **pre-build the finished forecast** (`prebuild_route_forecast`), so opening a
route returns 200 at once. It is built only for routes inside that same quota, opened in the last
14 days (`RecurringRoute.last_viewed_at`, set by `route_forecast` at most hourly), whose next
departure is within 48 h. Rules that hold this together:

- **The params must match the page exactly.** The job key hashes them. The page sends
  `nextDeparture` split as a string (`nextDepartureParts` in `queries/recurringRoutes.ts`:
  `slice(0, 10)` and `slice(11)`, offset kept), the endpoint joins them back, and the task uses
  `next_departure(...).isoformat()` directly. Both build the dict with
  `departures.route_job_params`. Never parse and reformat the time on either side.
- **`min_remaining`.** The task asks `get_or_start_job` to rebuild a job with less than 75 min
  left, or a job built early would expire between hourly passes.
- **No station calls.** A Pro ride near now is skipped; that job would spend Weather
  Underground calls and only live 10 min.
- **Staggered 30 s apart**, because `compute` and `forecasts` each have one worker and a user's
  own forecast would otherwise queue behind every build.
- The dashboard's own prefetch sends `X-NoRain-Prefetch: 1`, which does not count as a view.

### System dashboard (admin)

`/system` (`pages/system.vue`, `core/api/system.py`) reads everything over REST and does not
poll. `ws/system/` (`SystemEventsConsumer`) sends change notices only, never data:
`{"type": "hello" | "changed", "topics": [...]}`, and the page invalidates the queries that read
those topics (`systemQueryAffected` in `utils/systemOverview.ts`). Rules that hold this together:

- **Every write the dashboard shows calls `core.system_events.notify_system`** with its topic (`cells`, `jobs`,
  `routes`, `journeys`), after the write is committed. Today that is the grid
  cell stores, `jobs.publish` plus job creation, restart and purge, route geometry and route
  CRUD, and journey CRUD plus the stored plan. A new writer that skips it leaves the dashboard
  stale without any error.
- **The consumer throttles per topic** (`THROTTLE`: jobs 1 s, the rest 5 s, and the last change
  is always delivered). A job's fan-out stores a cell on every settle, and the cell layer
  refetches every page. The client invalidates with `cancelRefetch: false` for the same reason.
- **Access is `core.auth.admin_access.has_system_access`**, for the REST auth, the socket and
  the session's `system.allowed` alike. It applies `ADMIN_OTP` itself and never goes through
  `admin.site.has_permission`: the `OTPAdminSite` swap happens when `backend/urls.py` is first
  imported, and a socket can reach a fresh daphne process before any HTTP request does.
  A refused socket is accepted and then closed with 4003: if it were closed before `accept()`,
  the browser would see only 1006 and could not tell a refusal from a dropped connection.
- **Values that change only with time get no notice.** A stalled job writes nothing, so the page
  works out "possibly stalled" from `updatedAt` and the jobs page's `stall_timeout_seconds`.
  Cache freshness in the summary and coverage updates on the next notice or on "Aktualisieren".
- **Map data coverage** ("Kartendaten", the "Routing-Netz" and "Höhendaten" layers) is
  `GET /api/system/data-coverage` (`core/data_coverage.py`). It reads GraphHopper's
  `GET /coverage` (`docker/graphhopper/CoverageResource.java`: the running release's
  `artifact.json` and `cells.json`, plus its terrain's `manifest.json` and `cell_coverage.json`,
  passed through as they are), Photon's `/status`, and `meteolane-coverage.json` from Photon's
  data directory (mounted read-only into `backend` in production, `PHOTON_COVERAGE_FILE` to
  override). It is fetched in the request, cached 5 min, and cached only when every part answered.
  Each part fails on its own (`None`). It gets no change notice: it changes only when a graph or
  index is swapped in. The elevation levels are computed in Python from per-cell tile counts.
  Java only passes the files through. Older releases and terrain lack the files; the
  `*-backfill` recipes write them.

## Internationalisation

The step-by-step workflow (adding a text on either side, adding a code, adding a language,
the pre-commit checklist) is `docs/how-to/translations.md`; keep it in step with this section.

German is the source language, English the second one. The frontend is vue-i18n (`src/i18n/index.ts`, catalogs in
`src/locales/`), the backend Django's gettext with **German
msgids** (`gettext("Route nicht gefunden.")`), so German needs no catalog and wrapping a string
never changes what German users see. Rules that hold this together:

- **One rule for server text.** Anything the SPA branches on, or that is stored, cached, reused
  across readers or written by a worker, is a **code** the SPA words: the ride band and cause,
  the rain/frost/wind-effort levels, the departure `explanation`, journey `reasons`
  (`{kind, …}` from `rank_day`), `ForecastJob.error` and `Journey.plan_error` (rows from before
  hold German prose, which `utils/serverErrors.ts` shows as it is), and the weather description (the SPA words
  `weather_code`; `WMO_DE` is gone). **Language never enters a job key,
  `job.result` or `forecast_view`**: one job serves readers in both languages, and the WebSocket
  needs no locale. `gettext` is only for prose built inside a request (`HttpError`, `detail`,
  validator messages, default names written on create such as "– Rückfahrt") and for mails and
  briefings.
- **Which language.** Signed in: `User.language` (`core.middleware.UserLanguageMiddleware`, after
  `AuthenticationMiddleware`). Before that: `Accept-Language` through `LocaleMiddleware`; the SPA
  sends its own locale in that header on every request (`services/http.ts` and the generated
  client's middleware in `main.ts`). The SPA's order is the account's language, then the last
  choice in this browser (localStorage), then the browser, then German (`useLocale.detectLocale`).
  The switcher (`LanguageSwitcher.vue`, header and `/account`) saves to the account when signed
  in. allauth creates the account in the request's language (`AccountAdapter.save_user`).
- **Mails and briefings have no request.** `AccountAdapter.send_mail` renders in the
  recipient's language when the address has an account ("account exists" goes to its owner,
  whoever asked). Briefings and the trial mail use `translation.override(user.language)`. The
  briefing body is stored, so it is worded once, in the owner's language.
- **Never `t()` at import time.** A label built at module scope keeps the language the page
  loaded with. Constants that carry labels are getters (`POI_CATEGORIES`, `LODGING_KINDS`,
  `BIKE_PROFILE_OPTIONS`) or functions (`headingOptions()`, `weekdayLabels()`,
  `metricLabels()`, `coverageLabels()`). `Intl`/`toLocale*` take `intlLocale()` (de-CH, en-GB),
  date-fns takes `dateFnsLocale()`, both read inside a computed so a switch reformats. Charts
  are keyed on the locale; NiceMap rebuilds its chips, arrows and stops on a switch.
- **Catalogs.** `src/locales/de.json` is the source; `en.json` must have exactly the same keys,
  placeholders and plural forms (`src/locales/__tests__/catalogs.spec.ts`). The Vite plugin
  (`@intlify/unplugin-vue-i18n`) precompiles them, so a malformed message fails the build; its
  `include` must stay `src/locales/*.json` (a broader glob swallows the spec). Escape literal
  `{ } @ $ |` as `{'@'}`; plurals are `|`-separated (`t(key, n)`). ESLint's
  `@intlify/vue-i18n/no-missing-keys` is an error, `no-raw-text` a warning. `welcome.vue` keeps
  its own de/en copy object, switched by the app locale.
- **Hiking wording.** A text about one route, journey or forecast that says "Fahrt" has a
  `<key>Hike` sibling, and the code calls `tp(profile, key)` (`src/i18n`), which falls back to
  the plain key for every other profile and for keys without a variant. The profile comes from
  the row or from `job.result.profile`; the charts take it as an argument. Texts with no single
  profile in reach (list pages, the account) are worded for both. `keyData.note` and
  `summaryCard.note` differ in content for a hike (gusts, no wind effort), not only in words.
  The stored return-route name follows the profile too ("– Rückweg").
- **No figures in texts.** Prices, tier limits, the trial length and the briefing lead and cap
  are placeholders. The figures come from the server only: `offer` (and `prices`) in the
  entitlements payload (`core/api/billing.py` `_offer`, from `entitlements.py` `FREE`/`PRO`,
  `PLUS_PRICES`, `TRIAL_DAYS` and `briefings.LEAD`/`MAX_PER_DAY`). The endpoint answers anonymous
  visitors, so `welcome.vue` reads it through `usePlanOffer` and leaves out any line still
  holding a placeholder. Money goes through `formatPrice` (`Intl`, so de-CH reads "EUR 29").
- **Backend catalog.** `backend/core/locale/en/LC_MESSAGES/django.po` and the compiled `.mo`
  are both committed, so neither the image nor a Windows dev box needs GNU gettext at runtime.
  After adding or changing a `gettext` string or a `{% translate %}`, run `just messages`
  (needs gettext) and translate the new `msgstr`s. `core/test_i18n.py` fails when the `.mo` is
  missing or stale. Mail templates in `core/templates/account/email/` use `{% translate %}` /
  `{% blocktranslate with site_name=current_site.name %}` (blocktranslate takes no attribute
  lookups).

## Testing

- `SimpleTestCase` for pure functions (no DB)
- `TestCase` for DB-dependent tests (CellCacheTests, EntitlementTests, StripeWebhookTests,
  ForecastJobTests), `TransactionTestCase` for the consumer (`ForecastJobConsumerTests`)
- Sign in with `self.client.force_login(user)`, never `self.client.login()`: `login()` calls
  `authenticate()` without a request, and django-axes' backend refuses that
- Tests call the private `_..._async` twins directly, never the `@task()` wrappers — no
  test needs a live worker
- Run: `cd backend && python manage.py test core`
- Frontend: `cd frontend && npm run test:unit` and `npm run type-check`
- Unit tests render in German: `src/test/setup.ts` registers i18n for every mount; Playwright's
  default locale is `de-CH`. Backend tests run in German unless they set a language.

**Patch at the binding site, which differs by module.** When asserting that a path spends no
API request, patch `core.weather.get_or_fetch_forecast_cell` *and*
`core.weather.get_or_fetch_ensemble_cell` — `weather.py` imports both names into its own
namespace, so patching `core.grid.*` does not intercept and the test passes while the code
still fetches. The same rule everywhere: the route handlers use
`core.api.recurring_route.refresh_route_geometry`, assembly uses `core.tasks.compute_route_weather`
/ `core.tasks.compute_sections`, the cell tasks `core.tasks.get_or_fetch_*`, sign-in
`core.auth.signals.refresh_user_forecasts`. A patch on the defining module is silently ignored —
an `AssertionError` side effect then passes vacuously.

Override both `CACHES` (locmem) and `CHANNEL_LAYERS` (`InMemoryChannelLayer`) for anything
touching claims or job progress, so tests need neither Redis nor a worker.

## Regenerating the client

Easiest: `just export-openapi-schema && just update-api--build-only` (uses
`backend/api-generator.typescript-fetch.additionalProperties.json`, which sets
`prefixParameterInterfaces`; the manual command below without it renames every request type).

`npm run update:api:generate_client` is broken: `openapi-generator-cli` is a *Python* dev
dependency, and npm's package of that name is a dependency-confusion placeholder. Use the
backend venv:

```bash
cd backend
.venv/bin/python manage.py export_openapi_schema --indent 4 --sorted --output api_schema.json
.venv/bin/openapi-generator-cli generate -g typescript-fetch -i api_schema.json -o ../packages/api/ \
  --additional-properties=useSingleRequestParameter=true,supportsES6=true,disallowAdditionalPropertiesIfNotPresent=false
.venv/bin/python add_ts_nocheck.py
```

Do not pass `--remove-operation-id-prefix`: it renames every method (`coreRoutesApiListRoutes` -> `routesApiListRoutes`)
and breaks every call site.
