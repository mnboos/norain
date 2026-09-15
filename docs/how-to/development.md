# Develop, test, and regenerate the API client

Use the [tutorial](../tutorials/first-forecast.md) to install dependencies and create
`.env` first. Run each command group from its stated directory.

GeoDjango requires GEOS, PROJ, and GDAL on the host. On Debian/Ubuntu install
`binutils libproj-dev gdal-bin`; on macOS, `brew install gdal` (or `just setup`, which
also creates the backend virtual environment) — the settings find Homebrew's libraries on
their own; on Windows, set `GDAL_LIBRARY_PATH` and `GEOS_LIBRARY_PATH` to the
corresponding OSGeo4W DLLs. The geodata recipes in the justfile use `docker` (Windows:
`podman`); set `CONTAINER_ENGINE` to override. Start PostGIS before Django:

```bash
docker compose -f docker-compose.dev.yml up -d db
```

## Check backend changes

From `backend/`:

```bash
uv run python manage.py check
uv run python manage.py test core
```

Pure-function tests use `SimpleTestCase`; database-dependent tests use `TestCase`
and Django's test database. When changing models, create and apply migrations:

```bash
uv run python manage.py makemigrations core
uv run python manage.py migrate
```

## Check frontend changes

From `frontend/`:

```bash
npm run test:unit -- --run
npm run build
```

The build includes TypeScript checking and a Vite production build. `npm run lint`
runs ESLint with automatic fixes; `npm run format` rewrites formatting under `src/`.
Review their diffs before committing.

Playwright is configured in `frontend/playwright.config.ts`; locally it uses Vite on
`FRONTEND_PORT` from the root `.env`. Install browsers with `npx playwright install`.
The CI branch uses preview on 4173 and requires `npm run build` first. Start the
backend, worker, and geographic services for tests that exercise real forecasts.

## Regenerate the client used by the frontend

The application imports `@norain/api` from `packages/api/`. The existing
`npm run update:api` script activates a Windows virtual environment and generates
into `frontend/src/api/`; it does not update that shared package.

For the shared package, export the schema from `backend/`:

```bash
uv run python manage.py export_openapi_schema --indent 4 --sorted --output openapi.json
```

Then, still in `backend/`, use the generator supplied by the development dependencies
(with a Java runtime available):

```bash
uv run openapi-generator-cli generate   -g typescript-fetch   -i openapi.json   -o ../packages/api   -c api-generator.typescript-fetch.additionalProperties.json
```

Review the generated diff, preserving the package's `@norain/api` name and exports.
Run the frontend checks above. Verify an actual API response as well as its schema,
especially when changing field aliases. Keep generated types and their consumers
consistent; avoid manually patching generated files as the source of an API change.

## Update documentation

Update [HTTP reference](../reference/api.md) for endpoint changes,
[configuration reference](../reference/configuration.md) for settings, and the
relevant tutorial or how-to guide for workflow changes. Follow the page-type guidance
in the [documentation index](../README.md).


## Create an account you can sign in with

`IdentityBackend` refuses any account whose `email_verified` is false, and
`createsuperuser` cannot set that flag — so a fresh superuser reaches `/admin` but not the
app itself until you verify it:

```bash
cd backend
python manage.py createsuperuser          # asks for username, email and password
python manage.py verify_user --identifier you@example.test
```

Signing in accepts either the email address or the username, case-insensitively.

In development, verification and password-reset emails are printed to the console by
`EMAIL_BACKEND = console`; copy the link out of the runserver output.

Routes created before the ownership migration have no owner and are invisible in the UI.
Assign them with:

```bash
python manage.py claim_routes --identifier you@example.test --dry-run   # then without --dry-run
```

## Set a billing tier without Stripe

Entitlements read the local `Subscription` row, so a tier set by hand behaves exactly like
a paid one. In the Django admin, open **Subscriptions**, pick the user, set *plan* to
`pro` and *status* to `active`. With `STRIPE_SECRET_KEY` unset the upgrade buttons stay
hidden and the billing endpoints answer 503.

To exercise the real flow, run Stripe in test mode:

```bash
stripe listen --forward-to localhost:${BACKEND_PORT:-8000}/api/billing/webhook   # prints STRIPE_WEBHOOK_SECRET
stripe trigger customer.subscription.deleted                     # confirm the downgrade
```
