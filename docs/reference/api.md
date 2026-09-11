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

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/search` | Search Photon for places |
| GET | `/api/route_weather` | Compute an ad-hoc route forecast |
| GET | `/api/routes` | List active saved routes, ordered by next departure |
| POST | `/api/routes` | Create a saved route and enqueue geometry |
| GET | `/api/routes/{route_id}` | Retrieve one route by UUID, including an inactive route |
| PUT | `/api/routes/{route_id}` | Replace editable fields; enqueue geometry if coordinates/profile change |
| DELETE | `/api/routes/{route_id}` | Delete route; return 204 without a body |
| GET | `/api/routes/{route_id}/forecast` | Forecast a saved route for the supplied departure |

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
| `profile` | string | Required; must be enabled in GraphHopper |
| `departure_time` | string | Required; local ISO datetime |
| `interval_seconds` | integer | Optional, 300; effective minimum 60 |

Example from Bash; substitute a local date within the forecast window:

```bash
curl --fail --get http://127.0.0.1:8000/api/route_weather   --data-urlencode 'start_lat=47.5200'   --data-urlencode 'start_lon=9.2600'   --data-urlencode 'dest_lat=47.5570'   --data-urlencode 'dest_lon=8.8980'   --data-urlencode 'profile=bike'   --data-urlencode 'departure_time=YYYY-MM-DDT08:00:00'
```

This endpoint returns `RouteWeatherOut`; it does not save the route or include figures.

## Saved-route input

[RecurringRouteIn](../../backend/core/routes_schemas.py) is shared by POST and PUT.

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
It returns the weather response plus `route_id`, `departure_time`, `figures`
(temperature, precipitation, wind Plotly figures), and `sections`.

## Forecast response fields

[Weather schemas](../../backend/core/weather_schemas.py) define the complete types.

| Field | Meaning |
| --- | --- |
| `line` | Full route coordinate arrays; first two components are longitude and latitude |
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

`uncertainty` contains `metrics`, per-model `models`, `requested_models`,
`forecast_time`, `fetched_at`, `source`, `precipitation_interval_s`, `pop`, and
`rain_if_wet`. Metric dictionary keys are `precipitation`, `temperature`,
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
