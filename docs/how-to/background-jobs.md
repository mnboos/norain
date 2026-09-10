# Run background jobs and pre-warm forecasts

Use this guide after completing the [local setup](../tutorials/first-forecast.md).
Commands run from `backend/` with the same environment and database as the API.

## Process queued jobs

```bash
uv run python manage.py db_worker
```

Keep this process running alongside the web server. Creating a route queues
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

This scans active routes with geometry, examines their next three departures, and
queues deterministic and ensemble cells missing from the usable cache. Departures
outside today through today + 15 days are skipped. The printed counts describe
**enqueued work**, not completed weather downloads. Multiple samples or departures
can queue the same cell before a worker stores it.

To repeat this hourly, add a cron entry with absolute paths adapted to your checkout:

```cron
0 * * * * cd /absolute/path/to/norain/backend && /absolute/path/to/norain/backend/.venv/bin/python manage.py refresh_forecasts >> /absolute/path/to/norain/forecast-refresh.log 2>&1
```

No automatic recurring trigger is installed by the repository. The command performs
the scan in-process; the worker consumes the resulting jobs. Check its output and
worker logs after scheduling the first run. See [troubleshooting](troubleshooting.md)
if the scan raises a synchronous-database-access error.

## Retry geometry after fixing routing

A routing failure is logged and the geometry task returns without populating the
route. After resolving the service or coverage problem, enqueue a retry from the
Django shell, replacing the UUID:

```bash
uv run python manage.py shell -c 'from core.tasks import refresh_route_geometry; refresh_route_geometry.enqueue("REPLACE-WITH-ROUTE-UUID")'
```

Forecast requests also fetch missing weather cells on demand, so pre-warming is an
optimization rather than a prerequisite for reading weather.

[Documentation index](../README.md)
