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
| `GRAPHHOPPER_API_URL` | `http://localhost:8989` | Backend; base URL without `/route` |
| `GEOCODER_API_URL` | Required for search; no default | Backend; full Photon endpoint, e.g. `http://localhost:2322/api` |
| `OPENWEATHERMAP_API_KEY` | Optional | Enables OWM fallback when primary fetching fails |
| `OSM_DATA_URL` | `https://download.geofabrik.de/europe/switzerland-latest.osm.pbf` | GraphHopper import |
| `GRAPHHOPPER_HEAP` | `6g` | GraphHopper JVM initial and maximum heap |
| `PHOTON_INDEX_URL` | `https://download1.graphhopper.com/public/europe/switzerland-liechtenstein/photon-dump-switzerland-liechtenstein-1.0-latest.jsonl.zst` | Photon import |
| `PHOTON_IMPORT_HEAP` | `4g` | Photon import JVM heap |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Required by Compose interpolation | Additional PostgreSQL service; not Django's SQLite settings |
| `APP_STORAGE_PATH` | Required by Compose interpolation | PostgreSQL bind-mount root |
| `TZ` | No Compose default | Passed to the PostgreSQL service; does not configure every service |

Weather provider URLs, ensemble model selection, and weather timezone are constants
in [grid.py](../../backend/core/grid.py), not environment settings.

## Services and ports

| Service | Local endpoint | Started by |
| --- | --- | --- |
| Frontend | `http://127.0.0.1:3000` | `npm run dev` in `frontend/` |
| Django API | `http://127.0.0.1:8000/api/` | `python manage.py runserver` in `backend/` |
| Task worker | No HTTP port | `python manage.py db_worker` in `backend/` |
| GraphHopper | `http://localhost:8989` | Development Compose `graphhopper` |
| Photon | `http://localhost:2322/api` | Development Compose `photon` |
| PostgreSQL/PostGIS | Port 5432 | Optional Compose `db` |
| MOTIS | Port 8080 | Optional Compose `motis`; not used by the weather flow |

The localhost frontend client calls port 8000 directly. Vite also defines backend
proxies. For a non-localhost hostname, the client uses the current page origin.
CORS allows `http://localhost:3000` and `http://127.0.0.1:3000` with credentials.

## Storage

| Path | Contents |
| --- | --- |
| `backend/db.sqlite3` | Routes, forecast and ensemble cells, task queue, Django built-in tables |
| `data/graphhopper/osm/` | Downloaded OSM extract |
| `data/graphhopper/cache/` | Imported routing graph and elevation cache |
| `data/graphhopper/graphhopper-config.yaml` | Mounted routing configuration |
| `data/graphhopper/models/` | Custom e-bike routing models |
| `data/photon/` | Photon search data; inner `photon_data/` indicates an existing index |
| `${APP_STORAGE_PATH}/db/app/data/` | Optional PostgreSQL data |

Stopping processes preserves these paths. Django routes and caches are persistent,
despite older comments in Compose referring to a model-free application.

## Application constants

| Constant | Value | Meaning |
| --- | --- | --- |
| `SAMPLE_INTERVAL_DEFAULT_S` | 300 s | Default route sampling interval; ad-hoc input is clamped to at least 60 s |
| `COORD_ROUND` | 2 decimal places | Spatial cache key precision |
| `MAX_CELL_AGE` | 2 hours | Database cell freshness limit |
| `RAIN_THRESHOLD_MM` | 0.1 mm | Deterministic rain verdict threshold |
| `POP_MEMBER_MM` | 0.1 mm | Minimum precipitation for a wet ensemble member |
| `POP_VERDICT` | 0.25 | Probability threshold for the route rain verdict |
| Forecast availability | Today through today + 15 days | Calendar window used for scheduled departures |
| `ENSEMBLE_MODELS` | `icon_seamless_eps,meteoswiss_icon_ch1_ensemble,meteoswiss_icon_ch2_ensemble` | Models requested by the application |

Enabled routing profiles are `bike`, `ebike`, `fast_ebike`, and `car`. The UI also
lists `foot`, which is commented out in the routing configuration.

## Deployment boundary

The repository supplies development settings: `DEBUG=True`, a fixed development
secret, empty `ALLOWED_HOSTS`, and no API authentication configured on the Ninja
routers. Routes are shared rather than scoped to accounts. Production settings,
access control, TLS/reverse-proxy configuration, and process supervision are not
provided as a complete deployment workflow.

[Documentation index](../README.md)
