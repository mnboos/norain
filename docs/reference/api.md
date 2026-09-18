# HTTP API

The base path is `/api`. With the backend running, use
<http://127.0.0.1:8000/api/docs> for interactive documentation and
<http://127.0.0.1:8000/api/openapi.json> for the live schema.
[Export and regenerate](../how-to/development.md) after schema changes.

## Naming and time conventions

Query parameters use the Python names shown below, such as `departure_time`.
Body schemas define camelCase aliases and accept Python snake_case field names as
well. Tables below use Python field names to match the source. Generated TypeScript
models use camelCase (`start_lat` becomes `startLat`).

`CamelSchema` defines aliases, but the current routers do not explicitly enable
response `by_alias` serialization. Check actual response JSON against the generated
client when integrating; schema aliases alone do not establish response spelling.

Coordinates are decimal degrees. Coordinate arrays use **longitude, latitude**.
Use local ISO departure times without an offset, such as `YYYY-MM-DDT08:00:00`,
with a date in the current forecast window. See [time handling](../explanation/forecasts.md)
for current restrictions.

Departure comparisons return ISO times with an explicit Europe/Zurich UTC offset.
Preserve that offset when requesting the selected forecast, including the `time`
parameter on saved-route forecasts, so repeated daylight-saving hours stay distinct.

## Flexible departure times

Both forecast endpoints accept `departure_flex_before_minutes` and
`departure_flex_after_minutes`: integers from 0 to 120 in steps of 15. For example,
`departure_time=2026-09-17T08:00&departure_flex_before_minutes=30&departure_flex_after_minutes=60`
compares departures from 07:30 to 09:00, including 08:00. Invalid windows return 422.
One-off requests default to zero. Saved-route forecasts use the route's stored
settings when omitted; explicit zeros request only the exact departure.

Recurring-route create/update bodies and responses contain the same two fields,
defaulting to zero for existing routes. They configure flexibility without changing
the cron schedule. Clients may override them for an individual forecast.

When flexibility is enabled, the finished job result includes `departure_comparison`:

- `requested_time`, `window_start`, and `window_end` describe the original window.
- `candidates` contains departure and arrival times, `available`, `ride_score`
  (0 is best; null when unavailable), and `ride_label` for each evaluated departure.
- `recommended_time` is the selected departure or null when none can be compared;
  `explanation` describes the recommendation in German.

The comparison evaluates weather along the whole ride, combining typical and worst
conditions. Similar results favour the requested departure. Missing weather, past
departures, and rides outside provider coverage are not ranked. Rankings are derived
when serving the job; HTTP polling and WebSocket results share the same shape.

The ordinary forecast remains for the requested departure. To apply a candidate,
request its exact time with both flexibility values set to zero and keep the original
comparison in the UI. Route-list thumbnails and recurring schedules continue to use
the requested time. Suggestions are available to every tier; existing station and
uncertainty entitlements still apply. Candidate spacing is 15 minutes, even when the
underlying weather data is hourly.

## Endpoints

The session endpoint (`GET /api/auth/session`) and successful login responses
contain `user: {id, email, username}` for authenticated accounts. `id` is a stable
string account identifier used to correlate Sentry product metrics. The frontend
also accepts older responses without `id` during deployment. Signed-out sessions
return `user: null`.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/search` | Search Photon for places |
| GET | `/api/route_weather` | Start (or join) an ad-hoc route forecast; returns a job |
| GET | `/api/routes` | List active saved routes, ordered by next departure |
| POST | `/api/routes` | Create a saved route and enqueue geometry |
| GET | `/api/routes/{route_id}` | Retrieve one route by UUID, including an inactive route |
| PUT | `/api/routes/{route_id}` | Replace editable fields; enqueue geometry if coordinates/profile change |
| DELETE | `/api/routes/{route_id}` | Delete route; return 204 without a body |
| GET | `/api/routes/{route_id}/forecast` | Start (or join) a saved-route forecast; returns a job |
| GET | `/api/forecast_jobs/{job_id}` | Poll one forecast job (WebSocket fallback) |
| GET | `/api/forecast_jobs/{job_id}/map_detail?detail=medium\|full` | Route line and wind arrows at more detail than the job result's |
| GET | `/api/forecast_jobs/{job_id}/samples/{index}/uncertainty` | One sample's full ensemble spread, with the per-model breakdown |
| GET | `/api/billing/entitlements` | Current tier, its limits, and how much of them is used |
| POST | `/api/billing/checkout` | Start a Stripe Checkout session; returns a hosted URL |
| POST | `/api/billing/portal` | Open the Stripe billing portal; returns a hosted URL |
| POST | `/api/billing/webhook` | Stripe subscription events (see Billing below) |

The `/api/auth/*` and `/api/billing/*` paths are plain Django views mounted in
`backend/urls.py`, not part of the django-ninja API, so they do not appear in the OpenAPI
schema or the generated client. `frontend/src/services/` calls them directly.

Create and update return a route object using the default success status 200.
PUT uses the full input schema, not partial-update semantics. Omitted optional
fields receive their defaults. No per-user filtering or API authentication is
configured.

## Place search

All query parameters are required:

| Parameter | Type | Meaning |
| --- | --- | --- |
| `query` | string | Nonempty search text |
| `zoom` | number | Map zoom; rounded before forwarding |
| `lat`, `lon` | number | Location bias |

Returns a list of GeoJSON-like Point features with `geometry.coordinates` and
properties including `name`, `city`, `state`, `countrycode`, and `show_canton`.
The ambiguity flag distinguishes same-name places across cantons. Search requests
ask Photon for up to five city/locality features.

## Ad-hoc forecast

| Query parameter | Type | Required / default |
| --- | --- | --- |
| `start_lat`, `start_lon` | number | Required |
| `dest_lat`, `dest_lon` | number | Required |
| `profile` | string | Required; `bike`, `ebike` or `fast_ebike`, otherwise 422 |
| `departure_time` | string | Required; local ISO datetime |
| `interval_seconds` | integer | Optional, 300; effective minimum 60 |

Example from Bash; substitute a local date within the forecast window:

```bash
curl --fail --get http://127.0.0.1:8000/api/route_weather   --data-urlencode 'start_lat=47.5200'   --data-urlencode 'start_lon=9.2600'   --data-urlencode 'dest_lat=47.5570'   --data-urlencode 'dest_lon=8.8980'   --data-urlencode 'profile=bike'   --data-urlencode 'departure_time=YYYY-MM-DDT08:00:00'
```

This endpoint returns a `ForecastJobOut`, not weather. Forecasts are computed by background
workers, so the response is a job envelope:

```json
{"job_id": "…", "status": "pending", "cells_settled": 0, "cells_total": 0,
 "error": "", "result": null, "ws_url": "/ws/forecast/…/"}
```

`status` moves `pending` → `planning` → `fetching` → `assembling` → `done`, or `failed`.
Once it is `done`, `result` holds a `RouteForecastOut` — the same payload the saved-route
endpoint produces. Ad-hoc jobs do not save the route, so `route_id` is null.

`result` is a slim view of what the job stores, so that each page downloads only what it
draws. It leaves out three parts, each served by its own endpoint once the job is `done`
(404 before that, and 404 for another account's job):

| Left out of `result` | Fetch from |
| --- | --- |
| The full route line (`result.line` is simplified to ~50 m) | `GET /api/forecast_jobs/{job_id}/map_detail?detail=medium` (~10 m) or `detail=full` |
| The wind segments (`result.wind_arrows` holds one arrow per ~2 km) | the same `map_detail` call: `wind_arrows` ~500 m apart for `medium`, every drawable segment for `full` |
| Each sample's `uncertainty.models` and `requested_models` | `GET /api/forecast_jobs/{job_id}/samples/{index}/uncertainty` (`null` when the sample has no spread, which includes every sample of a free account) |

Every line level keeps each sample's vertex exactly, so a sample's `lon`/`lat` can be found
on any of them. `result` adds `job_id`, `version` (changes when the job is recomputed under
the same id — include it in any cache key for the parts) and `uncertainty_partial` (true
when some sample lacks the spread, a requested model or a metric median).

A request whose identical forecast is already computed and still fresh returns **200** with
`result` already populated; otherwise **202**.

To follow a job, connect a WebSocket to `ws_url` — it emits the same object on every change
and closes nothing else — or poll `GET /api/forecast_jobs/{job_id}`. A job owned by an
account is only readable by that account; an ad-hoc job is guarded by its unguessable id.

## Saved-route input

[RecurringRouteIn](../../backend/core/api/recurring_route.py) is shared by POST and PUT.

| Field | Type | Required / default |
| --- | --- | --- |
| `name` | string | Required |
| `description` | string | Empty string |
| `start_lat`, `start_lon`, `dest_lat`, `dest_lon` | number | Required |
| `start_name`, `dest_name` | string | Required display names |
| `profile` | string | `bike` |
| `schedule_cron` | string | Required five-field cron expression |
| `schedule_description` | string | Required display text; not parsed as a schedule |
| `active` | boolean | `true` |

Example create request using aliases:

```bash
curl --fail http://127.0.0.1:8000/api/routes   -H 'Content-Type: application/json'   --data '{"name":"Weekday ride","startLat":47.5200,"startLon":9.2600,"startName":"Zihlschlacht","destLat":47.5570,"destLon":8.8980,"destName":"Frauenfeld","profile":"bike","scheduleCron":"0 8 * * 1-5","scheduleDescription":"Weekdays at 08:00"}'
```

Route output adds `id`, `total_seconds`, `total_distance_m`, `has_geometry`,
`next_departure`, `forecast_available`, `created_at`, and `updated_at`. Geometry
readiness indicates stored sample points; it does not guarantee available weather.

The saved forecast endpoint requires `date=YYYY-MM-DD` and `time=HH:MM` query
parameters. The requested departure need not match the saved cron schedule.
It returns a job like the ad-hoc endpoint; its `result` adds `route_id`, `departure_time`
and `sections`. There are no chart figures: the frontend draws the charts from `samples`.

## Forecast response fields

[Weather schemas](../../backend/core/api/route_weather.py) define the complete types.

| Field | Meaning |
| --- | --- |
| `line` | Route coordinates simplified to ~50 m (see above for finer levels); each is `[longitude, latitude]` |
| `total_seconds`, `total_distance_m` | Estimated trip duration and distance |
| `samples` | Weather at sampled locations and their arrival times; can be empty |
| `summary` | Route-level rain and headwind summary |

Each sample contains `lat`, `lon`, `elapsed_s`, `eta`, `rain_mm`, nullable `pop`
and `rain_if_wet`, `temp`, `wind_speed`, nullable `wind_gust`, `wind_dir`,
`headwind`, `crosswind`, nullable `weather_code`, and German `weather_desc`.
Temperatures are °C, wind speeds are km/h, probabilities are 0–1, and wind direction
is degrees clockwise from north, indicating where the wind comes from.
Precipitation retains its provider time interval; it is not a journey total.

Samples additionally expose optional `precipitation_interval_s` (seconds),
`rain_rate_mm_h` (normalized intensity), `probability_source`, and `uncertainty`.
The generated TypeScript client presents these as camelCase properties.

In the job `result`, `uncertainty` contains `metrics`, `forecast_time`, `fetched_at`,
`source`, `precipitation_interval_s`, `pop`, and `rain_if_wet`; the sample uncertainty
endpoint adds per-model `models` and `requested_models`. Metric dictionary keys are `precipitation`, `temperature`,
`windSpeed`, `windGust`, `headwind`, and `crosswind`. Each metric has
`member_count` and nullable `p10`, `median`, and `p90`; fewer than two members
produces null percentiles. Model entries carry `model`, `metrics`, `pop`, and
`rain_if_wet`. Precipitation statistics use the preceding hour's mm, equivalent
to its average mm/h intensity. Retrieval time is not model initialization time.

Summary fields are `will_rain`, nullable `first_rain_eta` and `first_rain_place`
(the latter is a `lat,lon` string), `max_rain_mm`, nullable `rain_probability`,
`rain_amount`, `max_headwind`, and `source`. The source records the last successfully
extracted deterministic sample, so it does not describe every cell in a mixed-source trip.

Section fields are `start_km`, `end_km`, `start_time`, `end_time`, `condition`,
`max_rain_mm`, `max_headwind`, `temp_min`, and `temp_max`. Section times are elapsed
ride times, not departure-clock times; distances are estimated from elapsed-time
fractions. See [forecast interpretation](../explanation/forecasts.md).

## Error behavior

- Missing or mistyped schema inputs normally produce framework validation errors (422).
- A saved route without sample points returns 409 on its forecast endpoint.
- Unknown route UUIDs on read/update/forecast use unhandled ORM lookups; a structured
  404 is not implemented. Delete uses a filter and returns 204 even if no row exists.
- Cron strings and departure strings are not fully validated by the input schemas;
  parsing failures can surface as server errors. Invalid cron parsing logs and raises.
- Weather fetch failures can produce missing samples or an empty forecast. Saved-route
  charts then contain **Keine Wetterdaten** placeholders rather than failing on empty data.

[Documentation index](../README.md)


## Tiers

Limits live in `backend/core/entitlements.py` and are enforced server-side at three
places — route creation, the forecast endpoints, and the pre-warm task. The frontend only
uses `/api/billing/entitlements` to decide what to *show*.

| | Free | Pro |
| --- | --- | --- |
| Active saved routes | 2 | unlimited |
| Forecast, rain probability (`pop`), `rainIfWet` | yes | yes |
| Ensemble spread (`uncertainty`: p10/median/p90, per-model breakdown) | no | yes |

`uncertainty` is stripped from the response for free accounts; `pop` and `rainIfWet` are
not, because ensemble cells are shared between all accounts and pre-warmed anyway, so
serving them costs nothing extra.

## Billing

`POST /api/billing/webhook` is the only thing that changes a tier. It is mounted outside
the ninja API because `NinjaAPI(auth=session_auth)` CSRF-checks every route it owns and
would reject Stripe's POST with 403; the `Stripe-Signature` check is what authenticates
it instead. Each `event.id` is recorded in `ProcessedStripeEvent` and applied at most
once, since Stripe retries on any non-2xx. The Checkout success redirect grants nothing —
a browser may never load it.

Handled events: `checkout.session.completed`, `customer.subscription.created`,
`customer.subscription.updated`, `customer.subscription.deleted`,
`invoice.payment_failed`. Anything else gets a 200 and is ignored.

## Route list thumbnails

`RecurringRouteOut.thumbnail` carries a simplified route path (at most 64 vertices) plus
the six weather fields per sample that `frontend/src/utils/rideQuality.ts` scores. It is
precomputed by the `refresh_route_thumbnail` background task and only read from the
database by the list endpoint, which never parses a forecast cell or fetches from an
upstream API.

Scoring stays in TypeScript so the glyph and the full route map cannot disagree about the
same route. A sample point with no warm forecast cell is `null` — never an invented
value — and the frontend paints it neutral grey.

## Route-relative wind, felt wind and wind effort

Finished jobs store `wind_segments` (at most 500 chunks) and `summary.wind_distribution`.
The job `result` and `map_detail` serve the segments as `wind_arrows`, which show the
**real (ground) wind**, not the felt wind: only segments with full wind coverage, known
`wind_speed`, `wind_dir` and bearing (calm air has no direction and gets no arrow), reduced
to `lat`, `lon` (5 decimals), `bearing`, `wind_speed`, `wind_dir` (1 decimal each) and
`wind_power_w` (whole watts, null without timing), and spaced along the route by detail
level (see the table above). The full segment fields below
are what the job stores and what the wind chart is drawn from. They use the original cached weather anchors; finer geometry
does not trigger extra provider requests. These fields are available on both tiers.
Older stored results may omit them: clients should treat arrows as `[]` and distribution
as `null`. HTTP and WebSocket JSON keep snake_case; the generated client exposes camelCase.

Each sample's `headwind` is now the distance-weighted mean over its section, bounded by
distance midpoints to adjacent original samples. Positive is headwind, negative tailwind.
`crosswind` is the mean of absolute local crosswind. `wind_coverage` is the section's known
fraction (0..1); `sample_index` identifies the original anchor even when cells are missing.
An isolated valid anchor may have a local point value with coverage 0. `max_headwind` is
the maximum available section average, not instantaneous maximum exposure.

Each sample's `wind_power_w` (whole watts) is the **wind effort**: the extra power needed to
hold the planned speed against the wind, compared with calm air, distance-weighted over the
same section. Negative means the wind helps. It is
`k·v·(hypot(v+h, c)·(v+h) − v²)` with `k = ½·1.2 kg/m³·0.5 m²` (upright rider) and `v` the
section's average routing-model speed. The per-edge speed is not used, so a fast descent does
not read as hundreds of watts. The same headwind costs more the faster the profile rides.
It is null without timing. The ride score reads it in preference to `headwind`.
`summary.max_wind_power_w` is the largest sample value.

Wind speed, direction, head/crosswind, thumbnail headwind and summary/section maxima may
be null. North (0°), calm (0 km/h) and unavailable data are distinct. Missing wind does
not discard otherwise usable rain/temperature data, but prevents a complete ride score.

Segment fields:

| Fields | Meaning |
| --- | --- |
| `start_m`, `end_m` | Bounds along reported route distance |
| `lat`, `lon`, `elapsed_s` | Midpoint location and elapsed seconds; time can be null |
| `bearing`, `rider_speed` | Local compass heading and routing-model speed in km/h |
| `wind_speed`, `wind_dir` | Interpolated ground wind; direction is where wind comes from |
| `headwind`, `crosswind` | Signed midpoint components; segment crosswind is positive from the right |
| `felt_speed`, `felt_angle` | Apparent speed in km/h and angle relative to travel, positive right |
| `wind_power_w` | Wind effort in W at the section's average speed; negative when the wind helps |
| `wind_coverage`, `felt_coverage` | Fractions known within the whole chunk, independently of its midpoint |

Midpoint wind/speed/angle values may be null. A zero apparent vector has no angle. Apparent
wind uses forecast wind plus routing-model speed; it does not model local shelter or actual
rider speed. Wind arrows and the felt chart conservatively omit partially covered chunks.

Distribution fields `headwind_m`, `crosswind_m`, `tailwind_m`, `calm_m`, `unknown_m` sum to
the reported distance. Classification happens locally using ground angle: head <45°,
cross 45–135°, tail >135°. Ground speed below 0.1 km/h is calm. Missing original anchors
create unknown intervals; the next available samples are never joined across that gap.
Curves and out-and-back routes remain part of the distance totals.

`mean_felt_speed` averages only locally available apparent speeds over `felt_covered_m`;
`max_felt_speed` is the largest evaluated local value. Both are null without apparent data.
`mean_wind_power_w` averages the wind effort with tailwind counted as 0, and
`max_wind_power_w` is its largest local value (at least 0). Both are null without timing.
`timing_source` is `routing`, `sample-interpolation` for legacy geometry, or `unavailable`.
Display-chunk count and map zoom do not affect these route totals.

## Free / Plus and ride briefings

These session-authenticated JSON endpoints use camelCase and require CSRF for mutations:

- `GET /api/billing/entitlements`: effective plan (`pro` means Plus), route and briefing
  limits, comparison access, `trialEligible`, `trialEndsAt`, `complimentaryUntil`,
  `paidSubscription`, and `billingConfigured`. Routes count pairs once.
- `POST /api/billing/trial {}`: activate the account's one 14-day no-card trial; 409 if
  used already or currently Plus. No Stripe objects are created.
- `POST /api/billing/checkout {"interval":"annual"|"monthly"}`: server-selected Stripe
  price; 503 before checkout is enabled, 409 for an existing live subscription.
- `POST /api/billing/free-routes {"routeIds":[...]}`: select up to two owned active
  parent rides to retain on Free; excess rides are preserved and paused.
- `GET /api/briefings/preferences`: available channels, public push key, route
  preferences, effective activation, device count and the ten most recent messages.
- `POST /api/briefings/preferences {"routeId":"...","channel":"email"|"push"|""}`:
  set the channel for the parent ride and its return journey. Five rides on Plus;
  disabling is always permitted. A registered device is required for push.
- `POST /api/briefings/push`: browser `PushSubscription.toJSON()` containing `endpoint`
  and `keys`. Only supported HTTPS push-provider endpoints are accepted.
- `DELETE /api/briefings/push {"endpoint":"..."}`: remove this account's registration.

Recurring-route creation accepts optional `returnScheduleCron` and
`returnScheduleDescription`. A return journey swaps start/destination and is routed
independently. List responses contain parent rides only, with `return_route_id`,
`return_schedule_cron`, `return_schedule_description` and `return_next_departure`. Detail
responses also expose `parent_route_id` for a return journey (the generated TypeScript client maps these to camelCase). Use either leg's ID at
`/routes/{id}/forecast`. Both legs count as one slot and share a briefing setting.
Updates preserve the return schedule when omitted; an explicit empty return cron
removes the return journey. Deleting a parent deletes both journeys.

Explicit departure-comparison requests require Plus (402 otherwise). Saved comparison
windows revert to ordinary forecasts after expiry. Forecasts of paused saved rides
return 402. Basic weather and rain probability remain on Free.
