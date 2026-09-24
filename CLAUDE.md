# NoRain — Bike-route weather forecaster

Self-hosted routing (GraphHopper) + geocoding (Photon), weather from Open-Meteo
(primary, free) with OpenWeatherMap One Call 3.0 as fallback. Multi-user with
email/username sign-in, and a free/Pro subscription tier backed by Stripe.

## Project layout

```
backend/          Django 6 + Channels (async ASGI via daphne)
  backend/settings/  base.py + development.py / production.py (a package, not settings.py)
  backend/asgi.py    ProtocolTypeRouter: the Django app for http, consumers for websocket
  core/
    weather.py       routing + sampling + wind logic, compute_route_weather, build_geometry
    grid.py          forecast grid cache (ForecastCell, EnsembleCell) + API fetch + extraction
    stations.py      Weather Underground stations: budgeted fetch, cache, near-now correction
    models.py        User (custom, AUTH_USER_MODEL), Subscription, ProcessedStripeEvent,
                     RecurringRoute, ForecastCell, EnsembleCell, StationLookup,
                     StationObservation, ForecastJob
    jobs.py          forecast-job identity, lifecycle and channel-layer publishing
    claims.py        cache-backed in-flight claim for grid-cell fetches
    consumers.py     ForecastJobConsumer (websocket), routing.py maps it to a URL
    forecast_schemas.py  the forecast payload (RouteWeatherOut, WeatherSample, ForecastJobOut, …)
    api/             ninja routers: route_weather.py, recurring_route.py (route CRUD),
                     billing.py, places.py
    auth/            backend.py (session_auth), adapter.py (allauth rules), signals.py,
                     views.py (session + sign-up step 2), lockout.py
    entitlements.py  every tier limit, in one place
    thumbnails.py    route-list glyph: path simplification + cache-only weather
    ride_quality.py  ride-quality curves + RIDE_QUALITY config (secret; scored on read)
    journeys.py      journey planning: day cuts, breaks, POI gaps, ranking on read
    pois.py          POI categories (POI_RULES) + corridor query pois_along_sync
    road_prefs.py    road preferences -> penalty-only GraphHopper custom model
    weather_routing.py  rain/headwind zones -> custom-model areas
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
                     generated @norain/api client
    composables/     useSession, useEntitlements
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
itself or reset its password) and adds `signup_completed`.

**Sign-up, sign-in, verification and password reset are django-allauth, headless.** allauth
serves JSON under `/api/allauth/browser/v1/`; the Vue app draws every form
(`components/account/SignInForms.vue`). Our own endpoints are only `/api/auth/session`
(the session as the app needs it, plus the CSRF cookie) and `/api/auth/complete-signup`.
Sign-up has two steps:

1. The form takes only the email. allauth creates the user with a generated username and
   no usable password, and mails `/account?verify_key=…`. A known address gets the same
   reply (allauth mails its owner instead), so the form reveals nothing.
2. Opened in the same browser, the link signs the user in. Opened anywhere else, allauth
   only verifies the address and does **not** sign in (on purpose, see
   `login_on_verification`), so sign-in by emailed code (`ACCOUNT_LOGIN_BY_CODE_ENABLED`)
   is the way back in. The signed-in user then picks username and password in
   `complete_signup_view`. `User.signup_completed` marks that, and the router keeps the
   user on `/account` until it is true. That guard is the UI's only: the API does not
   check `signup_completed` (the email is verified and every tier limit applies, so there is
   nothing to protect), so don't describe it as a server-side rule. It is a separate flag, not "has a usable password":
   a password reset sets a password without the user ever picking a username.

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
match two accounts. The adapter also names the site "NoRain" in allauth's mails and counts
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
development settings only). And django-axes counts failed sign-ins — app and admin —
**per IP**, not per account+IP: a pair lockout never trips when one address tries a new
account each time. It is the only limit on wrong *passwords*: allauth's own `login_failed`
limit is off (`ACCOUNT_RATE_LIMITS`), or it would block one identity after 5 tries, before
axes counts to 10, with a different reply. Axes does **not** cover sign-in by code (no
password is checked, so a locked-out address can still get in by code). That is on
purpose: a code proves the mailbox, and guessing one is capped by allauth (3 tries per
code, `request_login_code` 3 a minute per address). A test pins this. The IP comes only from `X-Real-IP`, which Caddy sets from `{client_ip}`
(`core/auth/lockout.py`); daphne has no proxy headers and `X-Forwarded-For` differs between
the two Caddyfiles. The lockout reply is JSON because the SPA's `request()` parses every body.

**Migration ordering.** `core.User` is created in `0002`, not `0001`, because `0001` was
already released. Django resolves `swappable_dependency(AUTH_USER_MODEL)` to
`("core", "__first__")`, which would schedule `admin.0001_initial` before the user model
exists and fail with "Related model 'core.user' cannot be resolved" — so `0002` declares
`run_before = [("admin", "0001_initial")]`. Keep that if you ever regenerate it.

### Tiers and entitlements

Every limit lives in `core/entitlements.py`: free = 2 active routes, no ensemble
spread and no station correction; journeys: free = 1 journey, 1 alternative per day and no
weather-aware routing (enforced in `create_journey` and `plan_journey`). The route and forecast
limits are enforced at **three** places, and a limit is only real if all three hold:

1. `create_route` / `update_route` (`api/recurring_route.py`) — the route count, 402 when full.
2. `assemble_forecast_job` (`tasks.py`) — `strip_uncertainty()` for free accounts. This is
   **not** in the endpoints any more: the job's stored `result` is what the WebSocket pushes
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
the accessors without `v1` are deprecated. `stripe.Webhook.construct_event` is deliberately
*not* the client method — the webhook needs only `STRIPE_WEBHOOK_SECRET`, and
`client.construct_event` would make it need a secret key too.

### Every heavy operation is a task

No HTTP request performs a provider fetch or a GraphHopper call — with one deliberate
exception, `POST /api/routes/preview` (see "Route editing"). The forecast endpoints create a
`ForecastJob`, enqueue `plan_forecast_job` and return **202** with a job id; a finished job that is still fresh returns **200** with its stored payload.

```
POST-ish GET  ->  ForecastJob (202)
                     plan_forecast_job     queue: forecasts   geometry + fan-out
                       refresh_forecast_cell  \ queue: cells   one task per ~1 km² cell
                       refresh_ensemble_cell  /
                         assemble_forecast_job  queue: forecasts  cache_only + sections
                           -> job.result, pushed over ws/forecast/<job_id>/
```

The stored `job.result` is always complete. `job_snapshot` serves a slim view of it
(`core.jobs.forecast_view`: a ~50 m line, wind arrows ~2 km apart instead of the wind
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
parallelism is replicas): `cells` for the provider fan-out, `forecasts` for planning and
assembly (someone is waiting), `default` for geometry, thumbnails, scans and maintenance.
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
`LEASE_TTL` must stay above the Open-Meteo + OWM timeouts combined.

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
would score from the thermometer alone and disagree with the map), written by the `refresh_route_thumbnail` task and only *read* by `list_routes`,
which serves the path plus the worst sample's `ride_score` / `ride_label` and the ride's worst
rain and frost (`rain_level`, `frost_level`, `rain_probability`, `max_rain_rate_mm_h`,
`temp_min`) — never the raw samples. The rain and frost readings are the worst *point* of the
ride, not the worst-scoring sample: "will it rain on my ride" is a different question from
"what spoils it".

Two rules hold this together:

- **Never fetch from the list path.** `compute_route_thumbnail` calls
  `compute_route_weather(cache_only=True)`, which uses `get_cached_forecast_cell` instead
  of `get_or_fetch_*`. The list polls every 60 s; the fetching accessor would hammer
  Open-Meteo once per cold cell per poll.
- **Ride-quality scoring is server-only.** See "Ride quality" below.

**The glyph is one colour: the worst sample's.** The whole line is painted
`scoreColor(thumbnail.rideScore)` — the same YlOrRd ramp as the map route line, for the
sample whose `rideLabel` `RouteListPanel.qualityLabel` shows, so glyph and caption always
agree. Where along the route it changes is the map's job. At 40 px the ramp's pale good end
(`#ffeda0`) all but disappears, so the line sits on the theme-flipping casing the map uses
(`CASING_*` in `rideQuality.ts`, shared by both — change them there). The glyph has no legend
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
  back out of; that is how `wind_effort_level` has always worked.
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
set of fixed windows in the provider's own *weighted* calls. Open-Meteo counts a request with
more than 10 variables or 14 days as several (`grid.open_meteo_weight`), so one forecast cell
costs ~2.3. `acquire` spends against every window or refuses and returns the wait.
Rules that hold this together:

- **The gate sits in `grid._fetch_and_store_*`, inside the fetch lease and outside `@provider`.**
  That covers every caller, and a skipped call is `provider.throttled`, never a
  `provider.request`.
- **Adaptive.** A 429 goes through `record_throttle`. That starts one cooldown shared by every
  worker: `Retry-After`, else the hour/day the reply's `reason` names, else doubling from 60 s.
  It also halves a factor on the *shortest* window, which climbs back 0.1 a quiet minute (halving
  the hour would lock a half-spent hour and send every cell to OWM over a minute-level 429). The first 429
  of a burst adapts, the rest don't. This is what corrects a weight we guessed too low
  (the ensemble's weighting is undocumented).
- **Fail open for Open-Meteo, closed for OWM and Weather Underground**, for the same reasons as
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

### Data format difference (critical)

**Open-Meteo**: `hourly` = `{"time": [...], "temperature_2m": [...], ...}` — dict of parallel arrays
**OpenWeatherMap**: `hourly` = `[{"dt": 123, "temp": 15, ...}, ...]` — list of objects

`_from_open_meteo()` expects dict blocks; `_from_owm()` expects a list. Both have
`isinstance` guards rejecting the wrong format. `extract_sample()` routes directly based
on `cell.source` to avoid confusing the two.

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

### Charts

The backend draws no charts. `frontend/src/utils/forecastCharts.ts` builds the temperature,
precipitation and headwind charts from the samples the job result already carries
(`temp`, `rainRateMmH`, `pop`, `headwind` and the ensemble p10/median/p90), so they need no
request of their own; `NiceChart.vue` adds the theme. Each metric is **one line**: the sample's
own value (main run near now, the ensemble centre from 72 h, see "Ensemble central estimate").
The band around it is the ensemble spread **recentred on that line**:
`value + (p10 − median)` … `value + (p90 − median)`. It shows spread width, not the ensemble's
absolute range, which the details panel still shows. Don't add a median line back. The chart design lives only there:
a chart that needs a new value needs it on the sample, not a figures endpoint. Results
stored before this still carry a `figures` key; `forecast_view` drops it.

### Routing graph

GraphHopper is bike-only (`bike`, `ebike`, `fast_ebike`, each with CH); `ROUTING_PROFILES`
in `api/route_weather.py` must list the same names. The container **never builds a graph by itself**:
with an empty `graph-cache` it exits with an error. `just build-graphhopper-graph-from FILE` (a bike-filtered file
in `ROUTING_OSM_IMPORT_DIR`) empties the cache, runs the entrypoint's `build` command and starts it
again; production too. A graph can also be built on another machine and copied over
(`docs/how-to/build-routing-graph.md`).
GraphHopper calls the build "import". It refuses a graph built with a different config or jar,
so any change to `graphhopper-config.yaml` or `data/graphhopper/models/` means rebuilding.

Every build reads the bike-filtered file it is given, in `ROUTING_OSM_IMPORT_DIR` (the host folder
mounted at `/osm_data`, required in `.env`), never an extract itself:
`docker/graphhopper-filter-osm.sh` (osmium) keeps only the ways, nodes and cycle-route relations
the bike profiles use (a third of the Swiss file, same speeds). A build from
`bike-<OSM_DATA_URL's file name>` downloads `OSM_DATA_URL` (only ever an unfiltered extract) and
filters it, and redoes that when the extract is newer; any other file is used as it is. `just osm-filter-many-raw-pbf-into-one FILE…` runs the same script on
local files (several are filtered one by one, then merged) and writes `ROUTING_OSM_FILE_FILTERED`,
which it requires to be set, plus the matching `pois-<name without bike->.geojsonseq`; it builds
no graph, `just build-graphhopper-graph-from FILE` does.
If a profile ever needs a tag the filter drops, add it to that script and rebuild.

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
endpoints. To edit it, the user reshapes the outbound route; the UI offers no editor on a
return route.

The editor (`components/RouteEditorDialog.vue`) draws its line from `POST /api/routes/preview`,
the **only** HTTP request that calls GraphHopper itself. An editor cannot wait on a queue.
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
preferences. `plan_journey` (queue `default`) routes the whole journey once with LM, cuts it
into days at lodging (`journeys.split_days`), and hands over to `plan_journey_routes`, which
gets each day's GraphHopper alternatives, fills POI gaps and places breaks. Rows:
`Journey` → `JourneyDay` → `JourneyStage` (one per alternative, geometry like a
`RecurringRoute`). Rules that hold this together:

- **POIs are not in the graph.** The bike filter drops standalone amenity nodes, so
  `docker/osm-extract-pois.sh` (`just poi-extract-from-unfiltered-osm-pbf`) extracts them from the *raw* extract and
  `manage.py import_pois` (`just poi-import-into-db`, which runs in `worker-default` under a prod
  `COMPOSE_FILE` because the VPS host has no GDAL) replaces the `Poi` table in one transaction.
  `POI_RULES` in `core/pois.py` is the one tag map; a test checks the script filters every tag
  in it. Journeys store the POIs they use as JSON, never as FKs, so a re-import is free.
  An object gets one `Poi` row per matching category (unique on `osm_ref` + `category`): a
  vending machine selling drinks and sweets is both, so wanted categories stay ANDed per leg
  and one machine satisfies several. Extra-tag conditions match list items (`vending=a;b`).
  Postgres jsonb arrives as text on a raw cursor (Django's loader): parse it.
- **Request custom models only penalise** (`multiply_by` ≤ 1). GraphHopper runs LM without CH,
  and LM is only correct for a model that makes edges more expensive. "Prefer the cycle
  network" is therefore `avoid_off_network`. Every GraphHopper request still goes through
  `weather._route_body`; `_route` keeps a request without a model at `(profile, points)`.
- **POIs steer the route by via points, not by the custom model.** "Water once per leg" is a
  rule about the whole path and GraphHopper weighs edges; `journeys.gap_fixes` picks the POI
  with the smallest detour and the planner routes through it.
- **Alternatives are per day.** `alternative_route` takes two points only and exceeds the
  2 M node cap beyond ~130 km, so a failure falls back to one path.
- **Weather-aware routing** (Pro, days within `WEATHER_ROUTING_DAYS`): rain zones as request
  `areas`, headwind as `in_<zone> && orientation …` (`urban_density` and `orientation` were
  added to `graph.encoded_values`). Corridor cells are ordinary `ForecastCell`s on a 0.05°
  lattice fetched by `refresh_forecast_cell`; `plan_journey_routes` re-defers until they are
  warm (bounded), then reads `cache_only`. The multipliers are `ride_quality.ROUTING_*`.
- **Stage weather is a normal forecast job** (`ForecastJob.Kind.JOURNEY_STAGE`, geometry from
  the stage row, ownership checked in `plan_forecast_job`, never station calls). Reading a
  journey starts or joins its stage jobs; the departure window reuses the departure
  comparison (Plus). `rank_day` ranks a day's alternatives **on read** and serves results
  and reasons only, never the weights.
- **Revisions.** Every edit or re-plan bumps `plan_revision`; a planning task writes only
  while its revision is current, and replaces the days in one transaction.

### Recurring routes

Users configure routes with cron schedules. `next_departure()` computes the next departure,
`forecast_available_at()` checks if it's within the 16-day Open-Meteo window.
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

Do not pass `--remove-operation-id-prefix`: it renames every method
(`coreRoutesApiListRoutes` -> `routesApiListRoutes`) and breaks every call site.
