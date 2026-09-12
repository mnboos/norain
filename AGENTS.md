# NoRain — Bike-route weather forecaster

Self-hosted routing (GraphHopper) + geocoding (Photon), weather from Open-Meteo
(primary, free) with OpenWeatherMap One Call 3.0 as fallback.

## Project layout

```
backend/          Django 6 + django-ninja (async ASGI via daphne)
  backend/settings/
    base.py          settings shared by all environments
    development.py   local development and test overrides
    production.py    required production secrets + security settings
  core/
    api/
      __init__.py     NinjaAPI assembly and router registration
      billing.py      Stripe and entitlement JSON endpoints
      places.py       Photon place-search endpoint and schemas
      recurring_route.py  RecurringRoute CRUD/forecast endpoints and schemas
      route_weather.py    ad-hoc route-weather endpoint and schemas
    auth/
      backend.py      Django identity backend + Ninja session/CSRF auth
      tokens.py       email-verification token generator
      views.py        account/session JSON endpoints
    weather.py       routing + weather sampling + wind logic
    grid.py          forecast grid cache (ForecastCell, EnsembleCell) + API fetch + extraction
    plotting.py      Plotly figure generation (temp, precip, wind charts)
    models.py        RecurringRoute, ForecastCell, EnsembleCell
    schedule.py      croniter-based next_departure / forecast_available_at
    tasks.py         django-tasks background: route geometry, forecast pre-warming
    sections.py      route sectioning by weather condition
    tests.py         django tests (SimpleTestCase + TestCase)
    schemas.py       shared Pydantic base (CamelSchema)
frontend/         Vue 3 + Quasar + @tanstack/vue-query
  src/queries/    server-state keys, query hooks, mutations, cache invalidation
.env             OSM_DATA_URL, PHOTON_INDEX_URL, GRAPHHOPPER_HEAP, OPENWEATHERMAP_API_KEY
```

## Key architecture

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
- `TestCase` for DB-dependent tests (CellCacheTests)
- Run: `cd backend && python manage.py test core`
