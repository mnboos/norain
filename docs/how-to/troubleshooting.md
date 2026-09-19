# Troubleshoot setup and missing forecasts

Use the symptoms below with the backend and worker terminals visible. Run Compose commands from the repository root and
Django commands from `backend/`.

| Symptom                                                                                            | Action                                                                                                                                                                                                                                                                                                                                             |
|----------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `dotenv file not found` or `ENV_FILE does not point to a file`                                     | `ENV_FILE` is set to a missing path: fix or unset it. Without `ENV_FILE` the root `.env` is optional; create it using the [tutorial](../tutorials/first-forecast.md) or export the variables.                                                                                                                                                      |
| Compose cannot find a configuration file                                                           | Include `-f docker-compose.dev.yml`; the repository has no default `compose.yml`.                                                                                                                                                                                                                                                                  |
| Compose reports missing database/storage variables                                                 | Supply `DB_NAME`, `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, and `APP_STORAGE_PATH`.                                                                                                                                                                                                                                                          |
| Django reports that a `DB_*` setting is missing                                                    | Copy `.env.template` to `.env` and start the Compose `db` service. SQLite is no longer supported.                                                                                                                                                                                                                                                  |
| GeoDjango cannot find GEOS or GDAL                                                                 | Install the host GIS libraries documented in the development guide, or correct `GEOS_LIBRARY_PATH` / `GDAL_LIBRARY_PATH`.                                                                                                                                                                                                                          |
| Missing database table                                                                             | Run `uv run python manage.py migrate` against the same environment as the server and worker.                                                                                                                                                                                                                                                       |
| Search fails                                                                                       | Set `GEOCODER_API_URL=http://localhost:2322/api`, restart the backend, and inspect Photon logs and import completion.                                                                                                                                                                                                                              |
| Routing fails or times out                                                                         | Inspect GraphHopper logs; confirm the graph build is complete and both points are covered. Only `bike`, `ebike` and `fast_ebike` are configured; the API answers 422 for any other profile.                                                                                                                                                                 |
| Production GraphHopper exits with `No graph in /graph-cache`                                       | `GRAPHHOPPER_BUILD_GRAPH=false` is set and the cache is empty. Copy a graph [built elsewhere](build-routing-graph.md) into `graphhopper/cache`, or remove the setting so it builds on start.                                                                                                                                                       |
| GraphHopper refuses to load a copied graph                                                         | It was built with a different configuration or jar version. Rebuild from the commit deployed on the VPS.                                                                                                                                                                                                                                           |
| GraphHopper exits with `OSM_DATA_URL is a file name, but neither … nor bike-… exists` | `OSM_DATA_URL` names a merged set that was never imported here, or its filtered file was deleted. Run `just osm-import` with the extracts again ([downloaded files](import-geodata.md)), or set `OSM_DATA_URL` back to a download URL. |
| `just osm-import` stops with `OSM_DATA_URL in .env ends in …` or `Several files are merged into one…` | Every build reads `bike-<file name of OSM_DATA_URL>`, so the import must write that name. For one file, use a matching name; for several, set `OSM_DATA_URL` to a plain file name ([step 2](import-geodata.md#2-set-env)). Nothing was changed. |
| **Route wird berechnet…** persists, or forecast returns HTTP 409                                   | Run the `default` worker; inspect routing failures and [retry geometry](background-jobs.md).                                                                                                                                                                                                                                                       |
| A forecast stays `pending` or `fetching` forever                                                   | No worker is consuming that queue. All three of `cells`, `forecasts` and `default` must run — see [background jobs](background-jobs.md).                                                                                                                                                                                                           |
| Forecast fails with *Noch keine Wetterdaten verfügbar*                                             | Every cell was still cold at assembly. Check the `cells` worker log for `NOT stored` warnings — usually the Open-Meteo rate limit; retry, or run fewer `cells` replicas.                                                                                                                                                                           |
| Ensemble ranges (e.g. the wind spread) are missing, with the *Teilweise Ensemble-Abdeckung* banner | Some ensemble cells failed to fetch, usually Open-Meteo answering `429 Too Many Requests` (`Ensemble cell NOT stored` in the `cells` worker log). A job with failed cells is only reused for 5 minutes (`INCOMPLETE_JOB_LIFETIME`), so reopening the forecast after that fetches the missing cells again.                                          |
| Progress never updates but the forecast eventually appears                                         | The WebSocket could not be established (a proxy that drops upgrades); the client fell back to polling. Check that `REDIS_URL` is reachable and that the proxy forwards `/ws/`.                                                                                                                                                                     |
| Forecast refresh reports `SynchronousOnlyOperation`                                                | The async pre-warm scan directly calls synchronous ORM cache helpers in `tasks.py`. This requires a code fix wrapping those lookups in `sync_to_async`; use on-demand forecasts meanwhile.                                                                                                                                                         |
| **Keine Wetterdaten** appears in charts                                                            | Inspect backend logs for failed providers or extraction. Check network access and optional OWM credentials. Empty samples mean unavailable data.                                                                                                                                                                                                   |
| Forecast time is wrong or datetime comparison fails                                                | Supply local date/time without a UTC offset; review [time-zone limitations](../explanation/forecasts.md).                                                                                                                                                                                                                                          |
| UI route fields are missing despite a successful API response                                      | Compare response JSON with the live OpenAPI schema and generated client; response alias serialization may differ from schema aliases.                                                                                                                                                                                                              |
| `port is already allocated`, `address already in use`, or Vite's `Port 3000 is already in use`     | Another program holds that port. Set the matching `DB_PORT`, `REDIS_PORT`, `GRAPHHOPPER_PORT`, `PHOTON_PORT`, `BACKEND_PORT` or `FRONTEND_PORT` in the root `.env` and restart the services, backend and frontend. Vite does not fall back to the next port, because CORS only allows `FRONTEND_PORT`; Playwright's web server fails the same way. |

To inspect geographic service startup:

```bash
docker compose -f docker-compose.dev.yml logs --tail=100 graphhopper photon
```

To distinguish backend connectivity from a frontend problem:

```bash
curl --fail http://127.0.0.1:8000/api/routes
curl --fail --get http://127.0.0.1:8000/api/search   --data-urlencode 'query=Frauenfeld'   --data-urlencode 'zoom=12'   --data-urlencode 'lat=47.5'   --data-urlencode 'lon=9.3'
```

Changing an API URL requires restarting processes that read it at import time. Check the backend and worker together:
they have separate in-memory caches and must share the same persistent database.

[Documentation index](../README.md)

## Sign-in answers "Zu viele fehlgeschlagene Anmeldeversuche"

Ten failed sign-ins from one address (app or admin) lock that address for 30 minutes.
To lift it early, run `python manage.py axes_reset_ip IP_ADDRESS`, or `axes_reset` to clear
every lockout. In production run these with `docker compose ... exec backend`.

## Lost the authenticator app for the admin

Sign in with a backup code (pick the `backup` device on the login form), or create a new
device, which also replaces the backup codes:
`python manage.py add_totp_device --identifier YOU --replace`.

## Production database exits with `exec format error`

The database image is amd64-only and production selects `linux/amd64`. On an ARM VPS this error usually means amd64
emulation is missing. Follow the
[ARM host setup](deploy-vps.md#arm-hosts-enable-database-emulation), then restart
`db`. The version check there does not start PostgreSQL or initialize its data.

## Photon reports a missing index after a successful manual import

The serving process expects `photon_data/node_1` inside the mounted Photon folder. If the import log confirms success
but `node_1` is directly inside
`${APP_STORAGE_PATH}/photon`, stop Photon, create the missing `photon_data`
subdirectory, and move `node_1` into it. Preserve existing directories rather than overwriting them. Restart Photon and
test `/api?q=Zurich&limit=1`.

Use the documented local-file import command for future imports; it explicitly sets the data directory and publishes the
completed index in the expected layout.
