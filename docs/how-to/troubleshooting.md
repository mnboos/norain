# Troubleshoot setup and missing forecasts

Use the symptoms below with the backend and worker terminals visible. Run Compose
commands from the repository root and Django commands from `backend/`.

| Symptom | Action |
| --- | --- |
| `No .env file was found` or `Dotenv file not found` | Create the root `.env` using the [tutorial](../tutorials/first-forecast.md). ASGI reads that path even when management commands use `ENV_FILE`. |
| Compose cannot find a configuration file | Include `-f docker-compose.dev.yml`; the repository has no default `compose.yml`. |
| Compose reports missing database/storage variables | Supply `DB_NAME`, `DB_USER`, `DB_PASSWORD`, and `APP_STORAGE_PATH` for interpolation, even when starting only geographic services. |
| Missing database table | Run `uv run python manage.py migrate` against the same environment as the server and worker. |
| Search fails | Set `GEOCODER_API_URL=http://localhost:2322/api`, restart the backend, and inspect Photon logs and import completion. |
| Routing fails or times out | Inspect GraphHopper logs; confirm import is complete, both points are covered, and the profile is enabled. |
| **Fuss** routing fails | The UI offers `foot`, but that profile is commented out in the checked-in GraphHopper configuration. Use an enabled profile or configure and rebuild routing data for `foot`. |
| **Route wird berechnet…** persists, or forecast returns HTTP 409 | Run `db_worker`; inspect routing failures and [retry geometry](background-jobs.md). |
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
