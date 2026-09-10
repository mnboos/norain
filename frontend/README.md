# NoRain frontend

Vue 3, Quasar, and TanStack Query provide the route interface. MapLibre renders the
map and Plotly renders backend-generated forecast charts. The application imports
the shared `@norain/api` client from `../packages/api/`.

- [Local setup and first forecast](../docs/tutorials/first-forecast.md)
- [Development, tests, and API generation](../docs/how-to/development.md)
- [Troubleshooting](../docs/how-to/troubleshooting.md)
- [Forecast interpretation](../docs/explanation/forecasts.md)

From this directory:

```bash
npm ci
npm run dev
```

Vite serves the application at <http://127.0.0.1:3000>. Run the backend and worker
as described in the setup tutorial to use saved routes and forecasts.

[All documentation](../docs/README.md)
