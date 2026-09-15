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
    plotting.py      Plotly figure generation (temp, precip, wind charts)
    models.py        User (custom, AUTH_USER_MODEL), Subscription, ProcessedStripeEvent,
                     RecurringRoute, ForecastCell, EnsembleCell, StationLookup,
                     StationObservation, ForecastJob
    jobs.py          forecast-job identity, lifecycle and channel-layer publishing
    claims.py        cache-backed in-flight claim for grid-cell fetches
    consumers.py     ForecastJobConsumer (websocket), routing.py maps it to a URL
    forecast_schemas.py  the forecast payload (RouteWeatherOut, WeatherSample, ForecastJobOut, …)
    api/             ninja routers: route_weather.py, recurring_route.py (route CRUD),
                     billing.py, places.py
    auth/            backend.py (session_auth, IdentityBackend), views.py, tokens.py
    entitlements.py  every tier limit, in one place
    thumbnails.py    route-list glyph: path simplification + cache-only weather
    ride_quality.py  ride-quality curves + RIDE_QUALITY config (secret; scored on read)
    schedule.py      croniter-based next_departure / forecast_available_at
    tasks.py         every heavy operation: geometry, cells, job planning/assembly, scans
    sections.py      route sectioning by weather condition
    tests.py         django tests (SimpleTestCase + TestCase + TransactionTestCase)
    schemas.py       shared Pydantic (CamelSchema)
    management/commands/  verify_user, claim_routes, refresh_forecasts, run_forecast_scheduler
frontend/         Vue 3 + Quasar + @tanstack/vue-query
  src/
    services/        http.ts (shared fetch+CSRF), auth.ts, billing.ts — the plain-Django
                     endpoints; the ninja API goes through the generated @norain/api client
    composables/     useSession, useEntitlements
    utils/rideQuality.ts   score -> YlOrRd colour + its casing, line placement; no scoring
                           (that is core/ride_quality.py, server-only)
    utils/routeThumbnail.ts  geographic path -> square viewBox projection
    components/RouteThumbnail.vue  the tiny route glyph in the list
packages/api/     generated TypeScript client (see "Regenerating the client")
.env             OSM_DATA_URL, PHOTON_INDEX_URL, GRAPHHOPPER_HEAP, OPENWEATHERMAP_API_KEY,
                 WEATHERUNDERGROUND_API_KEY,
                 REDIS_URL (claim cache + channel layer)
```

## Key architecture

### Accounts and sign-in

`core.User` (`AUTH_USER_MODEL = "core.User"`) subclasses `AbstractUser`. It exists as a
custom model for exactly one reason: the two constraints in its `Meta`. Django's default
user permits duplicate and blank emails (`unique=False, blank=True`) and its `username`
index is case-sensitive, so "one account per email address, however capitalised" cannot be
expressed there — and you cannot add constraints to a model the project does not own. The
model also overrides `email` to `blank=False` (an account with no email could never verify
itself or reset its password) and adds `email_verified`.

Both the email and the username are sign-in identities.
`IdentityBackend.authenticate()` resolves either with one
`Q(email__iexact=…) | Q(username__iexact=…)`. Do **not** branch on whether the identifier
contains `@` — `UnicodeUsernameValidator` permits `@` in usernames. Signup also refuses a
username that matches any existing email (and vice versa), or the lookup would match two
rows and lock both accounts out.

`email_verified` gates sign-in and `createsuperuser` cannot set it, so a fresh superuser
reaches `/admin` but not the SPA until `python manage.py verify_user` runs.

**Migration ordering.** `core.User` is created in `0002`, not `0001`, because `0001` was
already released. Django resolves `swappable_dependency(AUTH_USER_MODEL)` to
`("core", "__first__")`, which would schedule `admin.0001_initial` before the user model
exists and fail with "Related model 'core.user' cannot be resolved" — so `0002` declares
`run_before = [("admin", "0001_initial")]`. Keep that if you ever regenerate it.

### Tiers and entitlements

Every limit lives in `core/entitlements.py`: free = 2 active routes, no ensemble
spread and no station correction. Enforced at **three** places, and a limit is only real if all three hold:

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

### Every heavy operation is a task

No HTTP request performs a provider fetch, a GraphHopper call or a Plotly render. The
forecast endpoints create a `ForecastJob`, enqueue `plan_forecast_job` and return **202**
with a job id; a finished job that is still fresh returns **200** with its stored payload.

```
POST-ish GET  ->  ForecastJob (202)
                     plan_forecast_job     queue: forecasts   geometry + fan-out
                       refresh_forecast_cell  \ queue: cells   one task per ~1 km² cell
                       refresh_ensemble_cell  /
                         assemble_forecast_job  queue: forecasts  cache_only + figures
                           -> job.result, pushed over ws/forecast/<job_id>/
```

The stored `job.result` is always complete. `job_snapshot` serves a slim view of it
(`core.jobs.forecast_view`: a ~50 m line, wind arrows ~2 km apart instead of the wind
segments, no figures, no per-model breakdown), and pages fetch those parts from
`/api/forecast_jobs/{id}/figures`, `/map_detail?detail=` (line + arrows) and
`/samples/{i}/uncertainty` only when they draw them. Shape on read only — never let page
shape into the job key, or two pages would compute two jobs for one forecast. Every line
level must keep each sample's vertex exactly: the map finds samples on the line by equality.

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

`core/claims.py` keeps one cell from being fetched by several tasks at once (`cache.add` is
atomic). It fails **open** — a Redis outage costs deduplication, never forecasts — and the
claim is released on failure too, or one dead cell would block retries for the whole TTL.
A job still enqueues a cell whose claim is held elsewhere: the holder is usually the
pre-warm scan, whose task carries no `job_id` and would never report back.

**Imports go at module scope — keep the layering that allows it.** The forecast payload
schemas live in `core/forecast_schemas.py`, outside the `core.api` package, because the
domain modules (`weather`, `uncertainty`, `plotting`, `sections`) need them and importing
anything under `core.api` runs its `__init__`, which loads the routers, which import
`core.tasks`. The direction is one way: `core.api.*` → `core.tasks` → domain modules →
`forecast_schemas`. Never make a domain module import from `core.api`; that recreates the
cycle, and any process reaching `core.tasks` or `backend.asgi` first — a worker, daphne —
dies on import. Don't paper over a new cycle with a function-level import; fix the layering.

### Route-list thumbnails

`RecurringRoute.thumbnail` is a precomputed blob (simplified path ≤ 64 vertices + the six
weather fields per sample that `core.ride_quality` reads), written by the
`refresh_route_thumbnail` task and only *read* by `list_routes`, which serves the path plus
the worst sample's `ride_score` / `ride_label` — never the raw samples.

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
wind and temperature curves, and `RIDE_QUALITY` (`weights` per factor, `sensitivity` — how
fast the score climbs the colour ramp). It is the app's own judgement and stays secret, so:

- **Nothing derived from the curves ships to the browser.** The API serves results only:
  per sample `ride_score` (0..1), `ride_label` and `wind_effort_level`; per wind arrow
  `wind_effort_level` and `wind_effort` (0..1, arrow size); `summary.max_wind_effort_level`;
  the thumbnail's worst `ride_score` / `ride_label`. `frontend/src/utils/rideQuality.ts` only
  maps a score to a colour and places samples along the line. Never add a curve, weight or
  breakpoint to the frontend — a threshold in the bundle gives the curve away.
- **Score on read, never store.** `core.jobs.forecast_view`, `wind_arrows_at_detail` and
  `recurring_route._thumbnail_out` score the stored raw weather when serving, so a change to
  `RIDE_QUALITY` shows on the next request without rebuilding jobs or thumbnails. It is plain
  arithmetic, cheap enough for the 60 s list poll.
- **Rain combines chance and amount** (`rain_impact`): the worse of the main run's rate through
  `RAIN_CURVE` and `curve(rain_if_wet) × pop ** (1 / rain_risk_aversion)`. The main run counts
  at face value; the ensemble adds the risk where it is dry. That is why thumbnails store `pop`
  / `rain_if_wet` and `compute_route_thumbnail` passes `include_uncertainty=True` (still
  `cache_only`) — without them the list would score the main run alone and disagree with the map.
- The weights need not sum to 1 (the score is clamped), so the rain weight is also how far rain
  alone can reach. Tests that check curve *shape* derive from or pin the config, so tuning
  `RIDE_QUALITY` does not break them. The frontend's `NiceChart` comfort band (14–22 °C)
  mirrors `TEMP_CURVE`'s flat part.

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

### Plotting

`generate_forecast_figures()` returns 3 Plotly figure JSONs. It is plain synchronous CPU
and is called only from `assemble_forecast_job`, never from a request — on one daphne
process a Plotly render in a request coroutine stalls every other request. When
`forecast.samples` is empty it returns placeholder figures with a "Keine Wetterdaten"
annotation instead of crashing, though a job that assembles no samples fails before it
gets that far.

### Routing graph

GraphHopper is bike-only (`bike`, `ebike`, `fast_ebike`, each with CH); `ROUTING_PROFILES`
in `api/route_weather.py` must list the same names. Production never imports: the graph is
built on another machine (`GRAPHHOPPER_IMPORT_ONLY=true`) and `graph-cache` is copied over
(`docs/how-to/build-routing-graph.md`). GraphHopper refuses a graph built with a different
config or jar, so any change to `graphhopper-config.yaml` or `data/graphhopper/models/` means
re-importing and re-shipping.

### Recurring routes

Users configure routes with cron schedules. `next_departure()` computes the next departure,
`forecast_available_at()` checks if it's within the 16-day Open-Meteo window.
`run_forecast_scheduler` runs `refresh_forecasts` hourly, which now only *queues*
`refresh_upcoming_forecasts`; that fans out to one `scan_route_forecasts` task per eligible
route, so one slow route no longer holds up the pass, and it purges the Stripe ledger and
expired `ForecastJob` rows. A successful SPA sign-in (`login_view`) also enqueues
`refresh_user_forecasts`, which scans just that account's routes through the same
`_prewarm_routes` quota; a failure to enqueue never fails the sign-in.

## Testing

- `SimpleTestCase` for pure functions (no DB)
- `TestCase` for DB-dependent tests (CellCacheTests, EntitlementTests, StripeWebhookTests,
  ForecastJobTests), `TransactionTestCase` for the consumer (`ForecastJobConsumerTests`)
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
/ `core.tasks.generate_forecast_figures`, the cell tasks `core.tasks.get_or_fetch_*`, sign-in
`core.auth.views.refresh_user_forecasts`. A patch on the defining module is silently ignored —
an `AssertionError` side effect then passes vacuously.

Override both `CACHES` (locmem) and `CHANNEL_LAYERS` (`InMemoryChannelLayer`) for anything
touching claims or job progress, so tests need neither Redis nor a worker.

## Regenerating the client

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
