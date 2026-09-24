# Configuration and services

This page describes the checked-in configuration. Sources:
[settings.py](../../backend/backend/settings.py),
[Compose base](../../docker-compose.base.yml), and
[Compose development](../../docker-compose.dev.yml).

## Environment variables

`manage.py` and the settings package load the repository-root `.env`, or the file
selected by `ENV_FILE`. The root `.env` is optional: CI and the production containers
have none and pass every variable through the process environment. An `ENV_FILE` that
is set but points to a missing file is an error. Already exported process variables
take precedence over values loaded by `python-dotenv`.

| Variable | Default / requirement | Consumer |
| --- | --- | --- |
| `ENV_FILE` | Root `.env` if unset and present | Management-command environment loader |
| `DB_NAME`, `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD` | Required | Django PostgreSQL connection and Compose database initialization; in development `DB_PORT` is also the host port Compose publishes PostGIS on |
| `REDIS_PORT`, `GRAPHHOPPER_PORT`, `PHOTON_PORT` | `6379`, `8989`, `2322` | Development only: host ports Compose publishes Redis, GraphHopper and Photon on. `.env.template` builds `REDIS_URL`, `GRAPHHOPPER_API_URL` and `GEOCODER_API_URL` from them |
| `BACKEND_PORT` | `8000` | Development only: `manage.py runserver` without an address listens on it, Vite proxies to it, and the localhost SPA calls it directly |
| `FRONTEND_PORT` | `3000` | Development only: Vite's port (strict — a taken port is an error), the allowed CORS/CSRF origin, the default `FRONTEND_URL`, and Playwright's local server |
| `GRAPHHOPPER_API_URL` | `http://localhost:8989` | Backend; base URL without `/route` |
| `GEOCODER_API_URL` | Required for search; no default | Backend; full Photon endpoint, e.g. `http://localhost:2322/api` |
| `OPENWEATHERMAP_API_KEY` | Optional | Enables OWM fallback when primary fetching fails |
| `WEATHERUNDERGROUND_API_KEY` | Optional | Pro only: corrects temperature and rain risk near now with nearby personal weather stations. Budgeted for the free PWS owner key (1500 calls/day, 30/min) |
| `REDIS_URL` | `redis://localhost:6379` | In-flight grid-cell claims (DB 1) and the forecast-progress channel layer (DB 2); needed by the web process and every worker |
| `OSM_DATA_URL` | `https://download.geofabrik.de/europe/switzerland-latest.osm.pbf` | The unfiltered OSM extract `just build-graphhopper-graph-from bike-<its file name>` downloads and filters for bikes when that file is missing |
| `ROUTING_OSM_FILE_FILTERED` | `bike-<file name of OSM_DATA_URL>` | The bike-filtered file in `ROUTING_OSM_IMPORT_DIR` that `just osm-filter-many-raw-pbf-into-one` writes, with its POIs, and `just poi-import-into-db` reads the POIs of. The graph itself is built from the file you pass to `just build-graphhopper-graph-from`; for `bike-<file name of OSM_DATA_URL>` that build downloads and filters the extract first. Set it for imported files, e.g. `bike-europe-cycling.osm.pbf` for several countries merged, see [downloaded files](../how-to/import-geodata.md) |
| `ROUTING_OSM_IMPORT_DIR` | Required; `./data/graphhopper/osm` in `.env.template`, `/srv/norain-data/graphhopper/osm` in production | The host folder GraphHopper imports from, mounted at `/osm_data`: `ROUTING_OSM_FILE_FILTERED`, a downloaded extract and the elevation tiles. `just osm-filter-many-raw-pbf-into-one` and `just poi-extract-from-unfiltered-osm-pbf` write their files here |
| `GRAPHHOPPER_IMAGE` | `norain-graphhopper:local` locally; required in production | Same immutable image for import, validation and serving; contains GraphHopper 12.0-SNAPSHOT built from the source commit in `Dockerfile` |
| `GRAPHHOPPER_HEAP` | `6g` | GraphHopper serving JVM maximum heap. With `MMAP` a few GB are enough; with `RAM_STORE` it must hold the whole graph |
| `GRAPHHOPPER_BUILD_HEAP` | `GRAPHHOPPER_HEAP` | JVM maximum heap while building the graph |
| `GRAPHHOPPER_DATAACCESS` | `MMAP` | How the server holds the graph. `MMAP` lets the OS page it in from disk, so the heap stays small and the first queries after a start are slower; `RAM_STORE` keeps all of it in the heap. Serving only: the build uses `GRAPHHOPPER_BUILD_DATAACCESS`. Both write the same files, so switching needs no rebuild |
| `GRAPHHOPPER_BUILD_DATAACCESS` | `RAM_STORE` | How the build holds the graph. `RAM_STORE` builds it in the heap; `MMAP` builds it in files on `/graph-cache`, so a large area (all of Europe, say) builds on a machine with far less memory, more slowly. The heap still holds the OSM reader's node map and the CH/LM bookkeeping |
| `GRAPHHOPPER_MEM_LIMIT` | `8g` | GraphHopper container memory and swap limit, during the build too: it must fit `GRAPHHOPPER_BUILD_HEAP` plus JVM overhead, or the kernel kills the build (exit 137, `Killed`) |
| `PHOTON_INDEX_URL` | `https://download1.graphhopper.com/public/europe/switzerland-liechtenstein/photon-dump-switzerland-liechtenstein-1.0-latest.jsonl.zst` | Photon import |
| `PHOTON_INDEX_FILE` | Empty | Local artifact path inside the container; takes precedence over the URL. Several `.jsonl.zst` / `.jsonl` dumps, separated by spaces, become one index |
| `PHOTON_REPLACE_INDEX` | `false` | `true` imports even when an index exists, and swaps the old one out only once the new one is ready (`just photon-import` sets it) |
| `PHOTON_ALLOW_DOWNLOAD` | `true`; production forces `false` | Permit downloading a missing index |
| `PHOTON_IMPORT_ONLY` | `false` | Prepare/reuse the index and exit without serving |
| `PHOTON_IMPORT_HEAP` | `4g` | Photon import JVM heap |
| `APP_STORAGE_PATH` | Required by Compose interpolation | PostgreSQL bind-mount root |
| `TZ` | No Compose default | Passed to the PostgreSQL service; does not configure every service |
| `DJANGO_ADMIN_PATH` | `admin` in development; required in production, one segment of letters, digits, `-` or `_`, and not `admin` | URL path of the Django admin, in Django and in the Caddy route to the backend |
| `FRONTEND_URL` | `http://localhost:$FRONTEND_PORT` in development; required in production | Base URL of the app. allauth's mails (email verification, password reset) and Stripe's return links point here |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL` | Required in production | Outgoing mail. Development prints messages to the console instead |
| `STRIPE_SECRET_KEY` | Empty; optional in all environments | Stripe API key. When empty, the billing endpoints answer 503 and tiers are set in the Django admin |
| `STRIPE_WEBHOOK_SECRET` | Empty; optional in all environments | Verifies the `Stripe-Signature` on `/api/billing/webhook`. Without it the webhook is refused |
| `STRIPE_PRICE_ID_PRO` | Empty; optional in all environments | Price the Pro Checkout session subscribes to |
| `SENTRY_DSN_BACKEND` | Empty; optional | Sentry DSN for Django, the workers and the scheduler. Read by production and development settings; the Django test command skips live initialization. See the [metrics and dashboard guide](../how-to/sentry-metrics.md) |
| `SENTRY_DSN_FRONTEND` | Empty; optional | Sentry DSN for the SPA, read from `.env` at build time: Vite bakes it into the bundle (`npm run dev`/`build` read the root `.env`, the production compose build passes it as a build arg), so a change needs a rebuild |
| `SENTRY_RELEASE` | Empty | Release reported by both halves, baked into both images. `just deploy-local` sets it to `git rev-parse HEAD` |
| `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT_FRONTEND` | Empty | Build time only, from `.env`: with a token, `vite build` writes hidden source maps, uploads them to Sentry and deletes them. The frontend image never contains a map, and Caddy answers `*.map` with 404. The production compose build passes the token as a BuildKit secret |

Weather provider URLs, ensemble model selection, and weather timezone are constants
in [grid.py](../../backend/core/grid.py), not environment settings.

## Services and ports

| Service | Local endpoint | Started by |
| --- | --- | --- |
| Frontend | `http://127.0.0.1:3000` (`FRONTEND_PORT`) | `just frontend`, or `npm run dev` in `frontend/` |
| Django API | `http://127.0.0.1:8000/api/` (`BACKEND_PORT`) | `just backend`, or `python manage.py runserver` in `backend/` (listens on `BACKEND_PORT`) |
| Task workers | No HTTP port | `python manage.py db_worker --queue-name {cells,compute,forecasts,default}` in `backend/` |
| Redis | Port 6379 (`REDIS_PORT`) | Cell claims and the WebSocket channel layer (`REDIS_URL`) |
| GraphHopper | `http://localhost:8989` (`GRAPHHOPPER_PORT`) | Development Compose `graphhopper` |
| Photon | `http://localhost:2322/api` (`PHOTON_PORT`) | Development Compose `photon` |
| PostgreSQL/PostGIS | Port 5432 (`DB_PORT`) | Development Compose `db` |
| MOTIS | Port 8080 | Optional Compose `motis`; not used by the weather flow |

If a default port is already taken on your machine, change its variable in the root `.env`
and restart; `just services` starts the Compose services on those ports.

The localhost frontend client calls `BACKEND_PORT` directly. Vite also defines backend
proxies. For a non-localhost hostname, the client uses the current page origin.
CORS allows `http://localhost:$FRONTEND_PORT` and `http://127.0.0.1:$FRONTEND_PORT` with credentials.

## Storage

| Path | Contents |
| --- | --- |
| `${APP_STORAGE_PATH}/db/app/data/` | Development PostgreSQL/PostGIS data |
| `ROUTING_OSM_IMPORT_DIR` (`data/graphhopper/osm/`) | Downloaded OSM extract, its bike-filtered copy (`bike-*.osm.pbf`, the file the graph is built from) and elevation archive (read during graph builds and saved-coordinate elevation lookups) |
| `data/graphhopper/cache/` | Immutable graph releases with current/candidate/previous symlinks; retain previous releases for rollback |
| `data/downloads/{osm,photon}/` | OSM extracts and Photon dumps from `just download-pbf` / `just download-photon-dumps`; not committed, only read by `osm-filter-many-raw-pbf-into-one` / `photon-import` |
| `data/graphhopper/graphhopper-config.yaml` | Mounted routing configuration |
| `data/graphhopper/models/` | Custom e-bike routing models |
| `data/photon/` | Photon search data; inner `photon_data/` indicates an existing index |
| `${APP_STORAGE_PATH}/postgres/` | Production PostgreSQL/PostGIS data bind mount |
| `${APP_STORAGE_PATH}/caddy/{data,config}/` | Production Caddy certificates and configuration |
| `${APP_STORAGE_PATH}/django/static/` | Collected Django static files |
| `${APP_STORAGE_PATH}/graphhopper/cache/` | Production routing graph, built elsewhere and copied in |
| `${APP_STORAGE_PATH}/photon/` | Production Photon index bind mount |

Stopping processes preserves these paths. Django routes and caches are persistent,
despite older comments in Compose referring to a model-free application.

## Application constants

| Constant | Value | Meaning |
| --- | --- | --- |
| `SAMPLE_INTERVAL_DEFAULT_S` | 300 s | Default route sampling interval; ad-hoc input is clamped to at least 60 s |
| `COORD_ROUND` | 2 decimal places | Spatial cache key precision |
| `MAX_CELL_AGE` | 2 hours | Database cell freshness limit |
| `MAX_THUMBNAIL_VERTICES` | 64 | Vertex budget for the route-list glyph (`core/thumbnails.py`) |
| `FREE.max_routes` | 2 | Active routes allowed on the free tier (`core/entitlements.py`) |
| `STRIPE_EVENT_RETENTION` | 30 days | How long processed webhook ids are kept for replay rejection |
| `RAIN_THRESHOLD_MM` | 0.1 mm | Deterministic rain verdict threshold |
| `POP_MEMBER_MM` | 0.1 mm | Minimum precipitation for a wet ensemble member |
| `POP_VERDICT` | 0.25 | Probability threshold for the route rain verdict |
| Forecast availability | Today through today + 15 days | Calendar window used for scheduled departures |
| `ENSEMBLE_MODELS` | `icon_seamless_eps,meteoswiss_icon_ch1_ensemble,meteoswiss_icon_ch2_ensemble` | Models requested by the application |

Enabled routing profiles are `bike`, `ebike`, and `fast_ebike`, each with a CH
preparation. `ROUTING_PROFILES` in `core/api/route_weather.py` must list the same
names; the API rejects any other profile with 422.

### Ride speed

Every arrival time in the app — each sample's clock time, and the `rider_speed` the wind
effort is computed at — comes from the travel times GraphHopper returns. There is no speed
setting in the backend. The speed is the `speed` block of each profile's custom model files
in `data/graphhopper/models/`:

| Profile | Files | Speed rule | Mixed-road average |
|---|---|---|---|
| `bike` | `bike.json` + `bike_elevation.json` (both from the jar) + `bike_speed.json` | road speed, slope, then ×1.15 capped at 30 km/h | ~18 km/h |
| `ebike` | `ebike.json` | road speed ×1.35, soft slope rules, capped at 25 km/h | ~22 km/h |
| `fast_ebike` | `fast_ebike.json` | road speed ×2.0, soft slope rules, capped at 35 km/h | ~32 km/h |

"Road speed" is GraphHopper's `bike_average_speed`, which comes from the OSM road type and
surface, so a forest track is slower than a cycleway for all three. Each profile's block
reads the same way, here `ebike.json`:

```json
  "speed": [
    { "if": "true", "limit_to": "bike_average_speed" },   // road type and surface
    { "if": "true", "multiply_by": "1.35" },              // how fast this rider is
    { "if": "average_slope >= 15", "limit_to": "8" },     // climbs
    { "else_if": "average_slope >= 12", "limit_to": "12" },
    { "else_if": "average_slope >= 8", "multiply_by": "0.9" },
    { "else_if": "average_slope <= -4", "multiply_by": "1.05" },
    { "if": "true", "limit_to": "25" }                    // motor cap, last
  ]
```

The statements run in order, so the cap stays last and the factor stays above it. For the
plain bike the first four lines come from the jar's `bike.json` and `bike_elevation.json`;
only the factor is ours, in `bike_speed.json`, which must stay **last** in
`custom_model_files` or the slope limits cut it.

### Changing a speed

1. Edit the factor (or the cap, or a slope rule) in the profile's file.
2. Prepare terrain if the OSM extent changed, then run
   `just build-graphhopper-graph-from <filtered .osm.pbf>`. This builds a separate
   candidate and retains the active graph. Run `just routing-validate-candidate`
   with endpoints inside it, then `just routing-activate` to use the new speeds.
3. `just routing-speeds` — prints what each profile now rides on four reference routes,
   next to the targets above. Repeat from 1 if a number is off.
4. `just routing-refresh-routes` — saved routes store their travel times, so they keep the
   old arrival times until they are routed again. Needs a worker on the `default` queue.

On production, deploy the changed files, then run `just build-graphhopper-graph-from` there
with the filtered file; the container never rebuilds by itself. On a VPS without the
memory for that, [build the graph elsewhere](../how-to/build-routing-graph.md#4-import-without-interrupting-routing). Step 4
applies there too.

## Deployment boundary

The checked-in default settings remain development settings: `DEBUG=True`, a
development secret, empty `ALLOWED_HOSTS`, and localhost CORS. The production
Compose workflow uses `backend.settings.production`, requires all secrets and
hosts through environment variables, terminates TLS at Caddy, keeps internal
services off public ports. Both configurations require the same `DB_*` PostGIS settings.

See [VPS deployment](../how-to/deploy-vps.md) for the full production procedure,
including image release, workers, backups, and recovery.

[Documentation index](../README.md)

## Free / Plus and briefings

| Variable | Default | Meaning |
| --- | --- | --- |
| `BILLING_ENABLED` | `false` | Enables paid checkout after commercial launch readiness; trials and admin grants work independently |
| `STRIPE_PRICE_ID_PLUS_ANNUAL` | Empty | Recurring €29/year EUR price; falls back to legacy `STRIPE_PRICE_ID_PRO` |
| `STRIPE_PRICE_ID_PLUS_MONTHLY` | Empty | Recurring €3.90/month EUR price |
| `BRIEFING_EMAIL_ENABLED` | `false` | Makes scheduled email briefings available, using the existing mail backend |
| `VAPID_PUBLIC_KEY` | Empty | Base64url application-server public key exposed to the browser |
| `VAPID_PRIVATE_KEY` | Empty | Private VAPID key or PEM path, available to delivery workers |
| `VAPID_SUBJECT` | Empty | VAPID contact URI, e.g. `mailto:admin@example.com` |

See [running the freemium beta](../how-to/freemium-beta.md) for activation, complimentary
colleague access, paired rides, notification delivery and commercial launch steps.
