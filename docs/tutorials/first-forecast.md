# Your first route forecast

In this tutorial you will run NoRain locally and save a weekday bike ride from
Zihlschlacht to Frauenfeld. At the end, you will have a route with a scheduled
forecast, a map, and weather charts.

## Before you start

Use a local checkout of this repository, Docker with Compose, Python 3.13 or
newer with `uv`, and Node.js/npm compatible with the frontend lockfile. The shell
commands below use Bash. Allow disk space for geographic downloads and enough
memory for GraphHopper's 6 GB import and serving heap, Photon import's 4 GB heap, and the host system.
Initial imports need internet access and can take substantially longer than later starts.

## 1. Configure the local services

From the repository root, create `.env` if it does not exist. If it already exists,
merge these settings without overwriting your values. The checked-in `.env.template`
contains the complete development example.

```dotenv
GRAPHHOPPER_API_URL=http://localhost:8989
GEOCODER_API_URL=http://localhost:2322/api
OPENWEATHERMAP_API_KEY=
TZ=Europe/Zurich
DB_NAME=norain
DB_HOST=localhost
DB_PORT=5432
DB_USER=norain
DB_PASSWORD=local-development-only
APP_STORAGE_PATH=./data
```

The `DB_*` settings are required by Django in every environment and initialize the
development PostGIS container. Open-Meteo needs no API key. If another program already uses
one of the default ports (5432, 6379, 8989, 2322, 8000, 3000), set the matching `*_PORT`
variable from `.env.template` in `.env`; the commands and URLs below then use that port instead.

Start PostGIS and the two geographic services:

```bash
docker compose -f docker-compose.dev.yml up -d --build db graphhopper photon
docker compose -f docker-compose.dev.yml logs -f db graphhopper photon
```

Wait for both services to finish importing and start serving requests. Press
Ctrl-C to leave the log viewer; the containers continue running. Confirm routing
and search respond:

```bash
curl --fail http://localhost:8989/info
curl --fail --get http://localhost:2322/api --data-urlencode 'q=Frauenfeld'
```

The first response describes GraphHopper; the second contains Photon features.

## 2. Start the backend

In a new terminal, starting at the repository root:

```bash
cd backend
uv sync
uv run python manage.py migrate
uv run python manage.py runserver   # 127.0.0.1:$BACKEND_PORT (8000); or: just backend
```

Keep this terminal running. Open <http://127.0.0.1:8000/api/docs> to see the
interactive API documentation. Migrations create the local database tables.

## 3. Start the background workers

Every forecast is computed by workers, so without them the app shows progress that never
finishes. In another terminal, starting at the repository root:

```bash
cd backend
uv run python manage.py db_worker --queue-name cells &
uv run python manage.py db_worker --queue-name forecasts &
uv run python manage.py db_worker --queue-name default
```

Keep this terminal running too. They also need Redis; if you are not running the Compose
stack, start it with `docker compose -f docker-compose.dev.yml up -d redis` (or `just services`);
it is published on `REDIS_PORT`, which `REDIS_URL` in `.env.template` already follows.

## 4. Start the frontend

In another terminal, starting at the repository root:

```bash
cd frontend
npm ci
npm run dev
```

Open <http://127.0.0.1:3000>.

## 5. Save a ride

1. Choose **Route erstellen** to open **Neue Route**.
2. Enter `Weekday ride` as the name.
3. In **Start**, type `Zihlschlacht` and select a search result.
4. In **Ziel**, type `Frauenfeld` and select a search result.
5. Keep Monday through Friday selected, set **Abfahrtszeit** to `08:00`,
   and select **Velo** as the profile.
6. Choose **Speichern** and open the saved route.

While the worker fetches routing data, the page shows **Route wird berechnet…**.
Once geometry is ready, NoRain requests weather for the next departure. A successful
forecast displays a route map, weather summary, sections, and three charts.

Check the departure time, temperature, precipitation, and wind. Positive headwind
means wind against your travel direction. If the page stays pending or shows no
weather, follow [troubleshooting](../how-to/troubleshooting.md).

## 6. Stop the local application

Press Ctrl-C in the frontend, backend, and worker terminals. From the repository
root, stop the supporting services:

```bash
docker compose -f docker-compose.dev.yml stop db graphhopper photon
```

Your routes remain in PostgreSQL, and the imported geographic data remains under
`data/`. Repeat the startup commands to return to your saved route.

Next, learn [how forecasts are interpreted](../explanation/forecasts.md) or
[pre-warm upcoming forecasts](../how-to/background-jobs.md).

[Documentation index](../README.md)
