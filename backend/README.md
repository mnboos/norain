# NoRain backend

Django and django-ninja provide the ASGI API. SQLite stores recurring routes,
forecast cells, and database-backed tasks; `db_worker` processes queued geometry
and weather jobs.

- [Local setup and first forecast](../docs/tutorials/first-forecast.md)
- [Background jobs](../docs/how-to/background-jobs.md)
- [Development and tests](../docs/how-to/development.md)
- [HTTP API reference](../docs/reference/api.md)
- [Configuration](../docs/reference/configuration.md)
- [Architecture](../docs/explanation/architecture.md)

With dependencies and root `.env` configured, run tests from this directory:

```bash
uv run python manage.py test core
```

[All documentation](../docs/README.md)
