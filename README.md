# NoRain — weather along your bike route

NoRain forecasts the weather where you will be during a ride. It combines a route
and departure time with precipitation, temperature, and wind forecasts, including
headwind and crosswind relative to your direction of travel.

Save recurring routes with departure schedules, then view their weather summaries,
route sections, maps, and charts. The interface uses Swiss German labels.

The application uses Django and django-ninja, Vue 3 and Quasar, self-hosted
GraphHopper routing and Photon geocoding, and Open-Meteo weather with an optional
OpenWeatherMap fallback. PostgreSQL/PostGIS stores routes, forecast caches, and background jobs.
The default geographic data covers Switzerland (Photon includes Liechtenstein).

## Documentation

Start at the [documentation index](docs/README.md), organized using Diátaxis:

| Your goal | Read |
| --- | --- |
| Run NoRain and create your first route | [First forecast tutorial](docs/tutorials/first-forecast.md) |
| Operate the worker and pre-warm forecasts | [Background jobs](docs/how-to/background-jobs.md) |
| Change routing and search coverage | [Change region](docs/how-to/change-region.md) |
| Run checks or regenerate the API client | [Development workflow](docs/how-to/development.md) |
| Diagnose a failed setup or forecast | [Troubleshooting](docs/how-to/troubleshooting.md) |
| Look up settings and API fields | [Configuration](docs/reference/configuration.md) · [HTTP API](docs/reference/api.md) |
| Understand the implementation | [Architecture](docs/explanation/architecture.md) · [Forecast interpretation](docs/explanation/forecasts.md) |

## Repository

- `backend/`: Django ASGI application, models, migrations, and tests.
- `frontend/`: Vue application with MapLibre maps and Plotly charts.
- `packages/api/`: TypeScript API client imported by the frontend.
- `Dockerfile`: every image (`backend`, `frontend`, `graphhopper`, `photon`) as a build stage.
- `docker/`: GraphHopper and Photon startup scripts and build certificates.
- `data/graphhopper/`: routing configuration, custom models, and local data mounts.
- `docs/`: project documentation in Markdown.

The checked-in settings and Compose files support local development. See the
[configuration reference](docs/reference/configuration.md) for their deployment limitations.
