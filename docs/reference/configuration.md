# Configuration and services

This page describes the checked-in configuration. Sources:
[settings.py](../../backend/backend/settings.py),
[Compose base](../../docker-compose.base.yml), and
[Compose development](../../docker-compose.dev.yml).

## Environment variables

`manage.py` loads the repository-root `.env`, or the file selected by `ENV_FILE`.
The ASGI entry point independently requires the root `.env`. Already exported
process variables take precedence over values loaded by `python-dotenv`.

| Variable | Default / requirement | Consumer |
| --- | --- | --- |
| `ENV_FILE` | Root `.env` if unset | Management-command environment loader |
| `DB_NAME`, `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD` | Required | Django PostgreSQL connection and Compose database initialization |
| `GRAPHHOPPER_API_URL` | `http://localhost:8989` | Backend; base URL without `/route` |
| `GEOCODER_API_URL` | Required for search; no default | Backend; full Photon endpoint, e.g. `http://localhost:2322/api` |
| `OPENWEATHERMAP_API_KEY` | Optional | Enables OWM fallback when primary fetching fails |
| `WEATHERUNDERGROUND_API_KEY` | Optional | Pro only: corrects temperature and rain risk near now with nearby personal weather stations. Budgeted for the free PWS owner key (1500 calls/day, 30/min) |
| `REDIS_URL` | `redis://localhost:6379` | In-flight grid-cell claims (DB 1) and the forecast-progress channel layer (DB 2); needed by the web process and every worker |
| `OSM_DATA_URL` | `https://download.geofabrik.de/europe/switzerland-latest.osm.pbf` | GraphHopper import; not used by production, which does not import |
| `GRAPHHOPPER_HEAP` | `6g` | GraphHopper serving JVM maximum heap; with `RAM_STORE` it must hold the whole graph |
| `GRAPHHOPPER_IMPORT_HEAP` | `GRAPHHOPPER_HEAP` | JVM maximum heap for an import |
| `GRAPHHOPPER_DATAACCESS` | `RAM_STORE` | `RAM_STORE` keeps the graph in the heap; `MMAP` pages it in from disk with a small heap |
| `GRAPHHOPPER_ALLOW_IMPORT` | `true`; `false` in production Compose | `false` makes an empty `/graph-cache` an error instead of an import |
| `GRAPHHOPPER_IMPORT_ONLY` | `false` | `true` exits after importing, for [building a graph to ship](../how-to/build-routing-graph.md) |
| `GRAPHHOPPER_MEM_LIMIT` | `8g` | GraphHopper container memory and swap limit |
| `PHOTON_INDEX_URL` | `https://download1.graphhopper.com/public/europe/switzerland-liechtenstein/photon-dump-switzerland-liechtenstein-1.0-latest.jsonl.zst` | Photon import |
| `PHOTON_IMPORT_HEAP` | `4g` | Photon import JVM heap |
| `APP_STORAGE_PATH` | Required by Compose interpolation | PostgreSQL bind-mount root |
| `TZ` | No Compose default | Passed to the PostgreSQL service; does not configure every service |
| `FRONTEND_URL` | `http://localhost:3000` in development; required in production | Base URL used to build email verification, password-reset and Stripe return links |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL` | Required in production | Outgoing mail. Development prints messages to the console instead |
| `STRIPE_SECRET_KEY` | Empty in development; required in production | Stripe API key. When empty, the billing endpoints answer 503 and tiers are set in the Django admin |
| `STRIPE_WEBHOOK_SECRET` | Empty in development; required in production | Verifies the `Stripe-Signature` on `/api/billing/webhook`. Without it the webhook is refused |
| `STRIPE_PRICE_ID_PRO` | Empty in development; required in production | Price the Pro Checkout session subscribes to |

Weather provider URLs, ensemble model selection, and weather timezone are constants
in [grid.py](../../backend/core/grid.py), not environment settings.

## Services and ports

| Service | Local endpoint | Started by |
| --- | --- | --- |
| Frontend | `http://127.0.0.1:3000` | `npm run dev` in `frontend/` |
| Django API | `http://127.0.0.1:8000/api/` | `python manage.py runserver` in `backend/` |
| Task workers | No HTTP port | `python manage.py db_worker --queue-name {cells,forecasts,default}` in `backend/` |
| Redis | Port 6379 | Cell claims and the WebSocket channel layer (`REDIS_URL`) |
| GraphHopper | `http://localhost:8989` | Development Compose `graphhopper` |
| Photon | `http://localhost:2322/api` | Development Compose `photon` |
| PostgreSQL/PostGIS | Port 5432 | Development Compose `db` |
| MOTIS | Port 8080 | Optional Compose `motis`; not used by the weather flow |

The localhost frontend client calls port 8000 directly. Vite also defines backend
proxies. For a non-localhost hostname, the client uses the current page origin.
CORS allows `http://localhost:3000` and `http://127.0.0.1:3000` with credentials.

## Storage

| Path | Contents |
| --- | --- |
| `${APP_STORAGE_PATH}/db/app/data/` | Development PostgreSQL/PostGIS data |
| `data/graphhopper/osm/` | Downloaded OSM extract and elevation tiles (import only) |
| `data/graphhopper/cache/` | Imported routing graph; the directory copied to production |
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

## Deployment boundary

The checked-in default settings remain development settings: `DEBUG=True`, a
development secret, empty `ALLOWED_HOSTS`, and localhost CORS. The production
Compose workflow uses `backend.settings.production`, requires all secrets and
hosts through environment variables, terminates TLS at Caddy, keeps internal
services off public ports. Both configurations require the same `DB_*` PostGIS settings.

See [VPS deployment](../how-to/deploy-vps.md) for the full production procedure,
including image release, workers, backups, and recovery.

[Documentation index](../README.md)
