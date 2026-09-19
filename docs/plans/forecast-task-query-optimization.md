# Forecast task query optimization

## Objective and evidence

Remove repeated database reads and warm-cell progress writes from forecast planning,
route scans, and scheduled/sign-in refresh eligibility. Code inspection found:

- `plan_forecast_job` and `scan_route_forecasts` check deterministic and ensemble
  availability separately for each cell/window: two reads on a primary-source hit,
  three when the deterministic lookup needs to check the fallback.
- Planning calls `_settle_cell` for every warm cell, performing two updates, a read,
  and a progress publication each time.
- `_prewarm_routes` calls `briefing_route_ids` per owner, repeating subscription and
  route selection queries. This was not matched to a specific Sentry trace.

## Bulk cache availability

Add an internal async grid helper taking distinct `(latitude, longitude)` cells and
`(day_key, forecast_days)` windows. Return two sets of `(latitude, longitude, date)`
keys: usable deterministic cells and usable ensembles. Normalize ISO dates to dates.
For repeated keys, require the largest requested horizon. Empty inputs issue no SQL.

Process at most 500 exact coordinate/date requirements per batch. Build exact-key OR
predicates including each required horizon; do not query a latitude/longitude Cartesian
product. Execute one deterministic and one ensemble SELECT per batch. Restrict rows to
`fetched_at >= now - MAX_CELL_AGE`; capture the time once per helper invocation.
The deterministic query accepts only `open-meteo` and `openweathermap`. Prefer the
primary source when recording hit metrics. Ensembles additionally require the current
`data._norain_request_version` in SQL. Select coordinates, dates, and deterministic
source only; do not transfer raw weather JSON. Preserve logical lookup hit/miss metrics.

Use this helper in planning and scans, replacing per-cell reads with set membership.
Single-cell fetching and forecast extraction remain unchanged. Scans skip held claims;
planning still enqueues cold cells with its job ID even if another task holds a claim.
Retain separate deterministic and ensemble tasks, window handling, and thumbnail work.

## Warm-cell progress and concurrency

Resolve cache availability before initializing the fetching state. Persist geometry,
total work, warm-cell count, zero failures, and fetching status together before any
worker is enqueued. Total work still includes both cell kinds and optional station work.
Publish the initialized progress once, instead of settling warm cells individually.

When the warm count equals total work (including the zero-work case), perform the
existing guarded fetching-to-assembling update, reload/publish, and enqueue computation
only for the winner. Otherwise enqueue only missing cells and any station task. The
existing atomic settlement path handles worker completion/failure and the final handoff.
Workers may finish while tasks are still being enqueued: all counters must already be
correct. Do not add an unconditional final job save that could overwrite worker progress.

## Bulk refresh eligibility

Add a synchronous bulk helper returning owner ID to briefing-route IDs. Deduplicate and
batch owner IDs in groups of 500. Read subscriptions once and all active route metadata
once per batch; do not load route geometry or thumbnail JSON for quota calculation.
Use the existing subscription-to-entitlements function, with no persistent entitlement
cache. Share pure selection logic with single-owner entitlement helpers.

Select active root routes by `-free_selected, created_at, id`, limited by `max_routes`.
Allowed return routes must be active and belong to the same owner and selected root.
From allowed roots, choose email/push briefing roots by `created_at, id`, limited by
`max_briefing_routes`. Include active return routes for those roots when their briefing
channel is nonempty. Roots without briefing opt-in still consume the route quota.
Preserve missing/expired subscriptions, trial and complimentary access, and free-tier
behavior. Do not filter routes by departure before applying quotas.

`_prewarm_routes` loads its candidate routes once, obtains bulk eligibility for their
owners, then applies the existing next-departure/four-hour rule. Both global refresh and
single-user refresh use this path. Keep inactive/ownerless exclusion and prebuild rules.
Single-owner helper signatures and ordering of selected roots remain compatible.

## Regression tests and acceptance criteria

Use database-backed Django tests and capture SQL around `async_to_sync` task execution.
Mock external providers, queue submissions, and publication when isolating query cost;
keep cache reads and progress writes real. Legitimate queue inserts remain per task.

- Compare small and large cell sets within one batch: exactly two availability reads,
  with no raw JSON selected. Test empty input and 501-key batching.
- Test fresh, stale, missing, insufficient-horizon, OWM-only, primary-plus-fallback,
  outdated/missing ensemble versions, duplicate keys, and multiple departure dates.
- Check exact coordinate/date matching and maximum horizon for duplicate requests.
- Compare warm planning progress writes for small/large routes: constant write count.
  Test fully warm, empty, mixed, cold, multi-window, and station-enabled plans.
- Simulate workers settling inside enqueue calls; assert initialized counters, accurate
  failure counts, no early computation, and exactly one handoff including late settlement.
- Keep held-claim behavior distinct between scans and planning. Ensure scan fixtures
  are entitled and departing soon so tests exercise the cache checks.
- Compare refresh eligibility query counts for one and many owners, plus batch boundaries.
  Verify parity for quota ordering, return pairs, invalid/empty channels, inactive and
  ownerless routes, subscription expiry, trial/gift access, user filtering, and departure
  boundaries. Include roots lacking briefing preferences but consuming quota.
- Update tests that mock the replaced per-cell helpers to exercise the bulk interface.

Run focused regression tests first, then `cd backend && .venv/bin/python manage.py test core`.
Run Ruff without automatic fixes on changed Python files. Record any environment
limitations and actual validation results in the implementation report.

## Compatibility and rollout

No public HTTP/WebSocket schema, task arguments, queue names, model migrations, or
provider-request changes. Progress reports warm work in one update. Weather computation
and thumbnail cache reads are outside this change. Preserve unrelated workspace edits.
After normal deployment, compare Sentry planning/refresh traces for lower repeated read
counts and warm-cell writes; queue inserts and real cell-worker writes remain expected.

## Implementation and validation — 2026-09-18

Implemented all three changes. The bulk cache helper is `get_cached_cell_keys`, returning
two sets of `CellKey` tuples. The bulk eligibility helper is `briefing_route_ids_by_owner`;
single-owner helpers share the same route selection rules. Planning initializes the
warm count before worker enqueueing and uses the existing guarded computation handoff.

- Added 18 database-backed regressions in `backend/core/test_task_queries.py`.
- Availability reads: two queries for both 1 and 100 keys; four for 501 keys; zero for
  empty requests. Tests check the SQL projection excludes weather JSON.
- Warm planning: three job UPDATEs for both 1 and 50 cells, with one computation enqueue.
- Warm route scanning: one query per cell table for both 1 and 50 cells.
- Eligibility: two queries for both 1 and 20 owners; four for 501 owners. Scheduler
  selection adds one candidate-route query, including when restricted to one owner.
- Mixed warm/cold work with workers settling during enqueueing preserves initial warm
  progress, waits for station work, records failures, and hands off exactly once.
- `.venv/bin/python manage.py test core.test_task_queries --noinput`: all 18 passed.
- `.venv/bin/python manage.py test core --noinput`: 360 tests run, OK, 4 skipped.
- Ruff `check --no-fix` passed for all five changed Python files; `git diff --check` passed.

Database tests ran with local PostgreSQL access outside the network-restricted sandbox.
One full-suite launch encountered an automatic approval-review timeout; its retry
succeeded. No test or implementation work remains blocked. Production Sentry comparison
remains a post-deployment check, not a measurement performed during this implementation.
