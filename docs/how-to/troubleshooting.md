# Troubleshoot setup and missing forecasts

Use the symptoms below with the backend and worker terminals visible. Run Compose
commands from the repository root and Django commands from `backend/`.

| Symptom | Action |
| --- | --- |
| `No .env file was found` or `Dotenv file not found` | Create the root `.env` using the [tutorial](../tutorials/first-forecast.md). ASGI reads that path even when management commands use `ENV_FILE`. |
| Compose cannot find a configuration file | Include `-f docker-compose.dev.yml`; the repository has no default `compose.yml`. |
| Compose reports missing database/storage variables | Supply `DB_NAME`, `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, and `APP_STORAGE_PATH`. |
| Django reports that a `DB_*` setting is missing | Copy `.env.template` to `.env` and start the Compose `db` service. SQLite is no longer supported. |
| GeoDjango cannot find GEOS or GDAL | Install the host GIS libraries documented in the development guide, or correct `GEOS_LIBRARY_PATH` / `GDAL_LIBRARY_PATH`. |
| Missing database table | Run `uv run python manage.py migrate` against the same environment as the server and worker. |
| Search fails | Set `GEOCODER_API_URL=http://localhost:2322/api`, restart the backend, and inspect Photon logs and import completion. |
| Routing fails or times out | Inspect GraphHopper logs; confirm import is complete and both points are covered. Only `bike`, `ebike` and `fast_ebike` are configured; the API answers 422 for any other profile. |
| Production GraphHopper exits with `No imported graph` | The VPS does not import. [Build the graph elsewhere](build-routing-graph.md) and copy it into `graphhopper/cache`. |
| GraphHopper refuses to load a copied graph | It was built with a different configuration or jar version. Rebuild from the commit deployed on the VPS. |
| **Route wird berechnet…** persists, or forecast returns HTTP 409 | Run the `default` worker; inspect routing failures and [retry geometry](background-jobs.md). |
| A forecast stays `pending` or `fetching` forever | No worker is consuming that queue. All three of `cells`, `forecasts` and `default` must run — see [background jobs](background-jobs.md). |
| Forecast fails with *Noch keine Wetterdaten verfügbar* | Every cell was still cold at assembly. Check the `cells` worker log for `NOT stored` warnings — usually the Open-Meteo rate limit; retry, or run fewer `cells` replicas. |
| Ensemble ranges (e.g. the wind spread) are missing, with the *Teilweise Ensemble-Abdeckung* banner | Some ensemble cells failed to fetch, usually Open-Meteo answering `429 Too Many Requests` (`Ensemble cell NOT stored` in the `cells` worker log). A job with failed cells is only reused for 5 minutes (`INCOMPLETE_JOB_LIFETIME`), so reopening the forecast after that fetches the missing cells again. |
| Progress never updates but the forecast eventually appears | The WebSocket could not be established (a proxy that drops upgrades); the client fell back to polling. Check that `REDIS_URL` is reachable and that the proxy forwards `/ws/`. |
| Forecast refresh reports `SynchronousOnlyOperation` | The async pre-warm scan directly calls synchronous ORM cache helpers in `tasks.py`. This requires a code fix wrapping those lookups in `sync_to_async`; use on-demand forecasts meanwhile. |
| **Keine Wetterdaten** appears in charts | Inspect backend logs for failed providers or extraction. Check network access and optional OWM credentials. Empty samples mean unavailable data. |
| Forecast time is wrong or datetime comparison fails | Supply local date/time without a UTC offset; review [time-zone limitations](../explanation/forecasts.md). |
| UI route fields are missing despite a successful API response | Compare response JSON with the live OpenAPI schema and generated client; response alias serialization may differ from schema aliases. |
| Playwright waits for a server | Align its local 5173 configuration with Vite on 3000; see [development](development.md). |

To inspect geographic service startup:

```bash
docker compose -f docker-compose.dev.yml logs --tail=100 graphhopper photon
```

To distinguish backend connectivity from a frontend problem:

```bash
curl --fail http://127.0.0.1:8000/api/routes
curl --fail --get http://127.0.0.1:8000/api/search   --data-urlencode 'query=Frauenfeld'   --data-urlencode 'zoom=12'   --data-urlencode 'lat=47.5'   --data-urlencode 'lon=9.3'
```

Changing an API URL requires restarting processes that read it at import time.
Check the backend and worker together: they have separate in-memory caches and
must share the same persistent database.

[Documentation index](../README.md)
