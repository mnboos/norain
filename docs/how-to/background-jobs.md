# Run background jobs and pre-warm forecasts

Use this guide after completing the [local setup](../tutorials/first-forecast.md).
Commands run from `backend/` with the same environment and database as the API.

From the repository root, use these local shortcuts (replace `UUID` with the saved
route's ID from its URL or the API):

```bash
just worker                        # all queues; keep running in a separate terminal
just forecast-refresh UUID          # pre-warm one route's upcoming departures
just forecast-refresh-all           # pre-warm all eligible routes
just routing-refresh-route UUID     # fetch geometry again, then rebuild its thumbnail
just thumbnail-refresh UUID         # rebuild the thumbnail from cached weather
just routing-backfill --route-id UUID           # preview missing/invalid timings
just routing-backfill --route-id UUID --enqueue # queue a repair
just manage showmigrations          # arbitrary Django management command
```

These recipes use `uv run` in `backend/` and your local environment. Refresh recipes
print the queued task ID and return; `just worker` processes the tasks. Use
`just worker cells` (or `default`, `compute`, `forecasts`) to serve only one queue.
The route-specific forecast scan requires existing geometry and examines the next
three departures, reusing fresh cached cells. It explicitly targets the supplied route,
including inactive routes; the all-routes pass applies the usual eligibility rules.
Pre-warming does not force fresh provider downloads or regenerate completed forecast
jobs. Open the route's forecast to request the assembled result.

## Process queued jobs

Every heavy operation runs on a worker, so **nothing works without one**: forecasts stay
`pending` forever if no worker consumes the queue. Run one process per queue:

```bash
uv run python manage.py db_worker --queue-name cells       # provider fetches, one per grid cell
uv run python manage.py db_worker --queue-name compute     # weather computation from warm cells
uv run python manage.py db_worker --queue-name forecasts   # job planning and assembly
uv run python manage.py db_worker --queue-name default     # geometry, thumbnails, scans
```

For local development one process can serve all four with `--queue-name '*'`. Without
`--queue-name` a worker only serves `default`, and every forecast stays `pending`.

`db_worker` has no concurrency flag — one process runs one task at a time — so throughput
comes from running several. In production `docker-compose.prod.yml` runs four `cells`
replicas, two `compute`, two `forecasts` and one `default`. Scale `worker-compute`
independently to control weather-computation throughput. Raising the `cells` count speeds up a cold
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

## Separate computation and assembly

Once all cells settle, `compute_route_weather_job` runs on `compute`. It reads stored
geometry and warm weather cells, applies the owner's entitlements, and saves an
intermediate forecast. Saving that result and queueing `assemble_forecast_job` is one
database transaction. Assembly runs on `forecasts`, adding charts and sections before
publishing the finished result. Neither stage waits inside a worker for another task.

Both stages appear as `assembling` to the browser. Their task records and queues distinguish
them for operations. Failed stages mark the forecast failed; the next request can retry.
The intermediate is cleared after successful assembly or when restarting a job.

For deployment of migration 0008, stop new forecast requests and the scheduler, drain
existing work with the old workers, then stop those workers. Apply migrations and deploy
the API and all four worker queues together. Old queued assembly tasks do not contain the
new intermediate result and must finish before the switch.

Pro/free queue routing is not enabled yet. A future routing policy can select a queue at
the enqueue boundary with `task.using(queue_name=...).enqueue(...)`; register that queue
and run workers consuming it.

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

## Recompute every route after a new routing graph

A route's polyline, sample points, vertex times, duration and distance are stored, so a new
graph — a new coverage area, or a changed ride speed — does not reach saved routes by
itself. With the new graph serving, queue them all:

```bash
just routing-refresh-routes
```

which is this, from `backend/`:

```bash
uv run python manage.py shell -c '
from core.models import RecurringRoute
from core.tasks import refresh_route_geometry
for rid in RecurringRoute.objects.values_list("id", flat=True):
    refresh_route_geometry.enqueue(str(rid))
'
```

Pass no `backfill_only` here: it defaults to false, which is what re-routes a route that
already has valid times. Each refresh queues the route's thumbnail itself, so the list
glyphs follow. Stored forecast jobs keep the old arrival times until they expire.

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
