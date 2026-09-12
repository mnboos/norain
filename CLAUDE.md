# NoRain — Bike-route weather forecaster

Self-hosted routing (GraphHopper) + geocoding (Photon), weather from Open-Meteo
(primary, free) with OpenWeatherMap One Call 3.0 as fallback. Multi-user with
email/username sign-in, and a free/Pro subscription tier backed by Stripe.

## Project layout

```
backend/          Django 6 + django-ninja (async ASGI via daphne)
  core/
    weather.py       route_weather endpoint, routing + sampling + wind logic
    grid.py          forecast grid cache (ForecastCell, EnsembleCell) + API fetch + extraction
    plotting.py      Plotly figure generation (temp, precip, wind charts)
    models.py        User (custom, AUTH_USER_MODEL), Subscription, ProcessedStripeEvent,
                     RecurringRoute, ForecastCell, EnsembleCell
    authentication.py  session_auth (CSRF + session) and IdentityBackend (email OR username)
    auth_views.py    signup / verify / login / logout / password reset (plain Django views)
    billing_views.py Stripe checkout, portal, webhook, entitlements (plain Django views)
    entitlements.py  every tier limit, in one place
    thumbnails.py    route-list glyph: path simplification + cache-only weather
    schedule.py      croniter-based next_departure / forecast_available_at
    tasks.py         django-tasks background: route geometry, forecast pre-warming
    routes_api.py    CRUD endpoints for recurring routes + per-route forecast
    sections.py      route sectioning by weather condition
    tests.py         django tests (SimpleTestCase + TestCase)
    schemas.py       shared Pydantic (CamelSchema)
    weather_schemas.py   RouteWeatherOut, WeatherSample, RouteWeatherSummary
    routes_schemas.py    RecurringRouteIn/Out, RouteForecastOut
    management/commands/  verify_user, claim_routes, refresh_forecasts, run_forecast_scheduler
frontend/         Vue 3 + Quasar + @tanstack/vue-query
  src/
    services/        http.ts (shared fetch+CSRF), auth.ts, billing.ts — the plain-Django
                     endpoints; the ninja API goes through the generated @norain/api client
    composables/     useSession, useEntitlements
    utils/rideQuality.ts   ride-quality scoring + Spectral colour ramp (the single source)
    utils/routeThumbnail.ts  geographic path -> square viewBox projection
    components/RouteThumbnail.vue  the tiny route glyph in the list
packages/api/     generated TypeScript client (see "Regenerating the client")
.env             OSM_DATA_URL, PHOTON_INDEX_URL, GRAPHHOPPER_HEAP, OPENWEATHERMAP_API_KEY
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

Every limit lives in `core/entitlements.py`: free = 2 active routes and no ensemble
spread. Enforced at **three** places, and a limit is only real if all three hold:

1. `create_route` / `update_route` (`routes_api.py`) — the route count, 402 when full.
2. `route_forecast` and `route_weather` — `strip_uncertainty()` for free accounts.
   `pop` and `rain_if_wet` are deliberately *not* gated: ensemble cells are shared and
   pre-warmed, so serving them costs nothing extra.
3. `_refresh_upcoming_forecasts_async` (`tasks.py`) — the pre-warm fan-out, which is what
   actually spends the Open-Meteo budget. It is nowhere near the HTTP layer, so it is the
   easy one to forget.

Entitlements read the local `Subscription` row, never Stripe, so a tier set by hand in the
admin behaves exactly like a paid one and the whole layer is testable without API keys.

### Stripe

`/api/billing/*` is mounted in `backend/urls.py`, **not** on the ninja router:
`NinjaAPI(auth=session_auth)` CSRF-checks every route it owns and would 403 Stripe's
webhook POST. The signature check authenticates it instead. Every `event.id` goes into
`ProcessedStripeEvent` and is applied once — Stripe retries on any non-2xx. Tier changes
come only from webhook events; the Checkout success redirect proves nothing.

### Route-list thumbnails

`RecurringRoute.thumbnail` is a precomputed blob (simplified path ≤ 64 vertices + the five
weather fields per sample that the frontend scorer reads), written by the
`refresh_route_thumbnail` task and only *read* by `list_routes`.

Two rules hold this together:

- **Never fetch from the list path.** `compute_route_thumbnail` calls
  `compute_route_weather(cache_only=True)`, which uses `get_cached_forecast_cell` instead
  of `get_or_fetch_*`. The list polls every 60 s; the fetching accessor would hammer
  Open-Meteo once per cold cell per poll.
- **Never port the scoring to Python.** `frontend/src/utils/rideQuality.ts` owns the
  curves and the colour ramp and the full map already uses them. A second implementation
  would drift and the glyph would disagree with the map about the same route.

A sample point with no warm cell stays `null` and is painted neutral grey; a thumbnail
whose `departure` no longer matches the route's live `nextDeparture` is greyed out
entirely, rather than showing yesterday's weather as today's.

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

`generate_forecast_figures()` returns 3 Plotly figure JSONs. When `forecast.samples` is
empty, it returns placeholder figures with a "Keine Wetterdaten" annotation instead of
crashing.

### Recurring routes

Users configure routes with cron schedules. `next_departure()` computes the next departure,
`forecast_available_at()` checks if it's within the 16-day Open-Meteo window. Background
tasks (`refresh_upcoming_forecasts`) pre-warm forecast cells for upcoming departures.

## Testing

- `SimpleTestCase` for pure functions (no DB)
- `TestCase` for DB-dependent tests (CellCacheTests, EntitlementTests, StripeWebhookTests)
- Run: `cd backend && python manage.py test core`
- Frontend: `cd frontend && npm run test:unit` and `npm run type-check`

When asserting that the thumbnail path spends no API request, patch
`core.weather.get_or_fetch_forecast_cell` — `weather.py` imports the name into its own
namespace, so patching `core.grid.*` does not intercept and the test passes while the code
still fetches.

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
