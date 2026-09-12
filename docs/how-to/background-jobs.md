# Run background jobs and pre-warm forecasts

Use this guide after completing the [local setup](../tutorials/first-forecast.md).
Commands run from `backend/` with the same environment and database as the API.

## Process queued jobs

Every heavy operation runs on a worker, so **nothing works without one**: forecasts stay
`pending` forever if no worker consumes the queue. Run one process per queue:

```bash
uv run python manage.py db_worker --queue-name cells       # provider fetches, one per grid cell
uv run python manage.py db_worker --queue-name forecasts   # job planning and assembly
uv run python manage.py db_worker --queue-name default     # geometry, thumbnails, scans
```

For local development one process can serve all three with `--queue-name '*'`. Without
`--queue-name` a worker only serves `default`, and every forecast stays `pending`.

`db_worker` has no concurrency flag — one process runs one task at a time — so throughput
comes from running several. In production `docker-compose.prod.yml` runs four `cells`
replicas, two `forecasts` and one `default`. Raising the `cells` count speeds up a cold
forecast but spends the Open-Meteo rate limit faster; lower it if you see
`Ensemble cell NOT stored` warnings in bulk.

Redis must also be reachable at `REDIS_URL`: it holds the in-flight cell claims and the
channel layer that carries progress to the browser.

Keep these processes running alongside the web server. Creating a route queues
`refresh_route_geometry`; changing its coordinates or routing profile clears its
stored geometry and queues another refresh. Weather requests for a saved route
return HTTP 409 until sample points exist.

Check the route again through `GET /api/routes/{route_id}`. Its geometry-ready field
should become true and its duration and distance should be populated.

## Pre-warm upcoming departures

With the worker running, execute:

```bash
uv run python manage.py refresh_forecasts
```

The command itself only queues `refresh_upcoming_forecasts`; that task fans out to one
`scan_route_forecasts` per eligible route, and each of those examines its route's next
three departures and queues the deterministic and ensemble cells missing from the usable
cache. Departures outside today through today + 15 days are skipped. Consecutive samples
inside the same ~1 km² cell collapse to one fetch, and an in-flight claim in Redis stops
two routes (or a live request) queueing the same cell twice.

To repeat this hourly, add a cron entry with absolute paths adapted to your checkout:

```cron
0 * * * * cd /absolute/path/to/norain/backend && /absolute/path/to/norain/backend/.venv/bin/python manage.py refresh_forecasts >> /absolute/path/to/norain/forecast-refresh.log 2>&1
```

`docker-compose.prod.yml` runs `run_forecast_scheduler` for this, which triggers the same
command hourly; the cron entry above is the equivalent for a checkout without Compose. The
command itself returns immediately — the scan runs on the `default` worker and the cell
fetches on the `cells` workers — so read the worker logs rather than the command's output to
see what actually happened.

## Retry geometry after fixing routing

A routing failure is logged and the geometry task returns without populating the
route. After resolving the service or coverage problem, enqueue a retry from the
Django shell, replacing the UUID:

```bash
uv run python manage.py shell -c 'from core.tasks import refresh_route_geometry; refresh_route_geometry.enqueue("REPLACE-WITH-ROUTE-UUID")'
```

## How a forecast request is served

Requests never fetch weather themselves. `GET /api/route_weather` and
`GET /api/routes/{id}/forecast` return **202** with a job:

```json
{"job_id": "…", "status": "pending", "cells_settled": 0, "cells_total": 0, "ws_url": "/ws/forecast/…/"}
```

Watch it over the WebSocket at `ws_url`, which streams the same shape as it progresses
(`planning` → `fetching n/m` → `assembling` → `done`, with `result` on the last frame), or
poll `GET /api/forecast_jobs/{job_id}` if the connection cannot upgrade. An identical
request for a forecast that is already computed and still fresh returns **200** straight
away instead.

Pre-warming therefore decides how *fast* a forecast appears, not whether it can be read:
a cold route still works, it just spends longer in `fetching`.

## Backfill per-vertex travel times

The wind update adds nullable `RecurringRoute.vertex_times`; migration 0004 performs no
network work. Drain/stop workers for a coordinated rollout, apply migrations, deploy the
compatible API/workers/client together and restart workers. Existing results remain readable;
new requests use the current wind algorithm version in their job identity.

From `backend/`, preview routes that need timing, then explicitly enqueue geometry refreshes:

```bash
.venv/bin/python manage.py backfill_route_vertex_times
.venv/bin/python manage.py backfill_route_vertex_times --enqueue --limit 100
```

Use `--route-id UUID` to restrict a run. The command checks missing and invalid arrays,
queues existing geometry tasks, and prints matched/enqueued counts. It does not call routing
or weather services itself. Workers recheck timing validity for backfill-only tasks; repeated
completed runs skip repaired routes. Concurrent runs may enqueue duplicate tasks. A geometry
refresh makes a routing request and queues a cache-only thumbnail rebuild, with no additional
weather fetch. A changed geometry revision makes later requests obtain a new forecast job.

Until backfilled, old geometry uses sample-time interpolation marked
`timing_source=sample-interpolation`; unsupported timing gives null apparent wind. No
live-provider fallback is allowed in forecast assembly or thumbnails. Refresh other existing
thumbnails through the existing `refresh_route_thumbnail` task to pick up corrected ground
wind values. Do not manually add wind segments to the compact thumbnail JSON.

[Documentation index](../README.md)
