# NoRain documentation

NoRain connects the time and location of a journey to weather forecasts. These
pages cover using the local application, operating its services, and contributing
changes.

The documentation follows [Diátaxis](https://diataxis.fr/): tutorials teach through
a guided experience, how-to guides solve a particular task, reference describes
the implementation, and explanation develops understanding.

## Tutorials — learn by doing

- [Your first route forecast](tutorials/first-forecast.md): set up the application,
  save a weekday bike route, and read its forecast.

## How-to guides — accomplish a task

- [Run background jobs and pre-warm forecasts](how-to/background-jobs.md).
- [Monitor product usage, forecasts, and workers with Sentry](how-to/sentry-metrics.md).
- [Change the geographic coverage](how-to/change-region.md).
- [Build routing and search from downloaded files, for several countries](how-to/import-geodata.md).
- [Rebuild the routing graph, or build it elsewhere and ship it to the VPS](how-to/build-routing-graph.md).
- [Develop, test, and regenerate the API client](how-to/development.md).
- [Deploy and operate a production VPS](how-to/deploy-vps.md).
- [Troubleshoot setup and missing forecasts](how-to/troubleshooting.md).

## Reference — look up details

- [Configuration and services](reference/configuration.md): environment variables,
  ports, persistent storage, and application constants.
- [HTTP API](reference/api.md): endpoints, request fields, and response meanings.

## Explanation — understand the design

- [Architecture and data lifecycle](explanation/architecture.md).
- [How to interpret route forecasts](explanation/forecasts.md).

## Maintaining these pages

Choose a page according to the reader's need. Keep the tutorial on one working
path; put alternative procedures in how-to guides, exhaustive facts in reference,
and design reasoning in explanation. Link between them instead of repeating full
instructions. When changing an endpoint, setting, or workflow, update its related
page and verify commands against the implementation.

[Project overview](../README.md)

- [Run the Free / Plus beta and invite colleagues](how-to/freemium-beta.md)
