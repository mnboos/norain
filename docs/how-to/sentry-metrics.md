# Monitor NoRain with Sentry

NoRain emits application metrics using the SDK's `count`, `gauge`, and
`distribution` APIs. Business milestones also produce structured Sentry logs
with the same name and attributes. The instrumentation lives in
`backend/core/telemetry.py` and `frontend/src/services/telemetry.ts`.

## Configuration

Set `SENTRY_DSN_BACKEND` and `SENTRY_DSN_FRONTEND` in the root `.env`. They can
point to the same Sentry project; `component:backend` and `component:browser`
distinguish them. Separate projects also work: select both when investigating
a user journey. Keep the environment variables as deployment overrides rather
than embedding a DSN in application code.

The backend initializes Sentry in production and, when a DSN is set, in development.
The Django test command skips live initialization. The frontend DSN
is baked into its bundle, so rebuild after changing it. Existing release,
environment, tracing, and profiling settings continue to apply. A missing DSN
leaves the SDK unconfigured; instrumentation does not require a network connection
to perform application work. No instrumentation-specific database migration is needed.

See [configuration](../reference/configuration.md) and
[deployment](deploy-vps.md) for the existing environment/build setup.

## Metric reference

Every name below has the `norain.` prefix. All counters increment by one;
sum their values to count events. Durations and ages use `second`. Coverage
distributions are ratios from zero to one. Gauges describe a snapshot, so use
their latest or maximum value within a time bucket rather than summing them.

| Name | Type | Definition and useful attributes |
| --- | --- | --- |
| `forecast.request` | Counter | One job lookup: `outcome:new`, `cached`, `joined`, `restart_expired`, `restart_failed`, or `restart_stalled`. Split by `feature:adhoc/route`, `plan`, and `profile`. `trigger:prewarm` distinguishes proactive rebuild lookups from `trigger:request` (which also includes browser prefetch requests). Cached/joined requests do not increment completion counts. |
| `forecast.completed` | Counter | A winning terminal database transition, with `outcome:success/failed`. Duplicate callbacks and cached reads do not count again. |
| `forecast.stage.duration` | Distribution | Execution time for `stage:planning/computation/assembly`. `outcome:returned` means the function returned, including skips and handled failures; `error` means it raised. These timings exclude queue wait. Use terminal completion metrics for the success rate. |
| `forecast.sample_coverage` | Distribution | Returned baseline samples / expected geometry samples, once per successfully assembled job. An absent denominator produces no measurement. This detects partial forecasts even when the job succeeds. |
| `forecast.failed_cell_ratio` | Distribution | Failed cell tasks / total cell tasks, once per successful job with cell tasks. The existing total includes optional station tasks; sample coverage is the more direct measure of user-visible completeness. |
| `forecast.station_coverage` | Distribution | Samples carrying station observations / expected geometry samples. Zero can be normal outside the station horizon or for an ineligible plan. It measures observation coverage, not weather accuracy. |
| `forecast.candidate_coverage` | Distribution | Complete departure candidates / all evaluated candidates, once per completed comparison job. Unavailable or past departures remain in the denominator. |
| `cache.lookup` | Counter | `kind:forecast/ensemble`, `outcome:hit/miss`. A deterministic lookup checks both weather sources before recording one outcome. This is a lookup-weighted hit rate, not a count of unique cells or saved HTTP requests. |
| `provider.request` | Counter | Actual provider calls, including errors, with `provider` and `outcome`. Providers: `open-meteo`, `open-meteo-ensemble`, `openweathermap`, `graphhopper`, `photon`, `weather-underground`. Outcomes include `success`, `timeout`, `rate_limited`, `http_error`, `network_error`, `invalid_response`, `error`. |
| `provider.duration` | Distribution | Provider call duration using the same dimensions. Routing and geocoding cache hits do not emit provider calls. Station requests skipped for missing configuration or budget do not count. |
| `weather.fallback` | Counter | `outcome:needed` when primary weather is unavailable, `unconfigured` when OWM cannot be attempted, and `recovered` when OWM supplies data. These are stages of one fallback, not mutually exclusive outcomes. |
| `queue.depth` | Gauge | Eligible queued task count by `queue`. Excludes deliberately deferred tasks until they become eligible. |
| `queue.oldest_ready_age` | Gauge | Age of the oldest eligible task, measured from the later of enqueue time and scheduled time. Immediate tasks use enqueue time. Empty queues emit zero. |
| `forecast.stalled` | Gauge | Nonterminal jobs untouched for longer than the existing five-minute stall timeout. |
| `forecast.load` | Counter | One browser query execution, with `outcome:success/failed/cancelled` and `delivery:initial_request/immediate/websocket/polling`. Query retries and automatic refreshes are separate executions; Vue rerenders and reads from the query cache are not. |
| `forecast.load.duration` | Distribution | Browser elapsed time from before the initial API call to the resolved forecast or error. Includes queue wait and delivery. Background saved-route prefetches carry `prefetch:true`; exclude them when charting interactive wait. |
| `forecast.delivery_fallback` | Counter | One WebSocket-to-polling transition. Successful polling recovery does not count as a failed load. |
| `search.completed` | Counter | Server searches with `outcome:success/empty/error`, including server cache hits. Browser cache reads do not invoke this endpoint. |
| `search.duration`, `search.results` | Distribution | Server search time and result count. Result count is emitted only when retrieval succeeds, including zero results. |
| `account.action` | Counter | Request action: `complete_signup` (step 2 of sign-up) with outcomes `success/rejected/error`. The other account requests are allauth's and are not counted per request. Milestones from allauth's signals, all `outcome:success`: `created` (step 1 made an account), `verified`, `login`, `password_changed`. A sign-up with an address that already has an account creates nothing and counts nothing. |
| `route.action` | Counter | Persisted `created/updated/deleted` milestones with `outcome:success`, or `action:quota outcome:rejected`. Geometry work has its own provider outcomes; it does not undo a saved-route milestone. |
| `billing.action` | Counter | `checkout/portal` request outcomes `success/rejected/unavailable/error`. Verified milestones use actions `checkout.session.completed` and `invoice.payment_failed` with `outcome:applied`. A successful checkout API call only means a session was created. |
| `billing.webhook` | Counter | `outcome:applied/duplicate/unmatched/ignored/rejected/error`. Accepted event types are explicit; unsupported types are grouped as `other`. |
| `subscription.transition` | Counter | Actual changes to the local plan, status, or cancellation setting after a verified webhook commits. Attributes: `from_plan`, `to_plan`, `from_status`, `to_status`, `cancel_at_period_end`. Duplicate deliveries do not repeat transitions. |

Queue gauges come from the scheduler every minute, independent of worker progress.
The scheduler continues its hourly pre-warm enqueue. If sampling fails it emits
no snapshot, rather than a misleading zero. Monitor missing data separately from
an empty queue.

## User and route investigations

Authenticated product metrics use the stable database account ID as `user.id`.
The session endpoint exposes this ID as a string; older session responses without
it still parse. Browser identity is cleared when the session ends, and forecast
workers isolate their context per execution.

Relevant backend product metrics and milestone logs include `route.id`,
`route.name`, `job.id`, `profile`, `start_lat`, `start_lon`, `dest_lat`, and
`dest_lon` when available. Search events include the submitted `search.query`
and location bias `search.lat`, `search.lon`, `search.zoom`. Coordinates are
not rounded by instrumentation. Browser ad-hoc load metrics carry endpoint
coordinates; saved-route loads carry the route ID. Plan attributes are included
where available. These detailed attributes are attached directly to metrics as
well as milestone logs, as selected for this deployment.

Use the Sentry Metrics explorer for aggregate charts and filter by these
attributes for a specific ride or account. In Logs, filter `user.id` and the
`norain.*` milestone messages and sort chronologically to inspect signup,
verification, route usage, checkout, and subscription transitions. Use `job.id`
to inspect repeated requests and the eventual completion of the same job.

Keep operational charts grouped by compact dimensions such as plan, feature,
provider, and outcome. Counts measure events, not unique users or ordered funnel
conversion. Account-level journey analysis needs the ID and milestone sequence;
a ratio of signup and checkout event totals is not a cohort conversion rate.

Instrumentation selects explicit fields; it does not attach passwords, reset or
verification tokens, request bodies, API keys, or Stripe payloads. Existing SDK
PII configuration is unchanged. Metrics/log delivery is best effort, not an
accounting ledger: a process crash, transport failure, or Sentry quota can lose
events even though the application change committed.

## Dashboard recipes

Select the deployment's environment and release first. For counter formulas below,
`sum(metric, filter)` means summing the metric values after applying that filter;
use Sentry's metric selector and formula editor rather than pasting this notation
as a query.

| Chart | Calculation |
| --- | --- |
| Forecast completion rate | Successful `forecast.completed` / all `forecast.completed`, split by feature and plan. |
| Forecast completeness | Mean and p10 `forecast.sample_coverage`, alongside failed-cell ratio. |
| Reuse and deduplication | `forecast.request` stacked by outcome; cached fraction and joined fraction have all requests as their denominator. |
| Cache efficiency | Hits / all `cache.lookup`, separately for deterministic and ensemble data. |
| Provider health | Requests by provider and outcome; p95 successful request duration; isolate rate limits. |
| Fallback recovery | Recovered / needed `weather.fallback`; chart unconfigured separately. |
| Worker backlog | Latest queue depth and oldest-ready age by queue, plus stalled jobs. |
| Interactive wait | p50/p95 `forecast.load.duration`, success only, excluding `prefetch:true`; split by feature and delivery. |
| Delivery degradation | Polling fallbacks and successful polling loads beside failed loads; exclude cancellations from the failure denominator. |
| Search usefulness | Empty / all completed searches, result-count distribution, p95 duration. Filter detailed queries to investigate poor matches. |
| Account activation | `account.action` filtered to `created` and `verified`, displayed separately over time. |
| Route adoption | Created, updated, deleted, and quota-rejected `route.action`, by plan. |
| Billing | Checkout sessions created, verified checkout completions, subscription changes, and payment failures displayed separately. |

Starting alerts to tune against real traffic:

- Forecast failures above 5% over 15 minutes, with at least 20 terminal events.
- Mean successful forecast sample coverage below 0.95 over 15 minutes, with at
  least 20 samples of that metric.
- Oldest-ready queue age above 300 seconds for five minutes.
- No queue snapshots for three minutes while the scheduler should be running.

No live dashboards or alerts are created by the repository change.

## Verify a deployment

1. Deploy the backend and rebuilt frontend with the DSNs configured. Wait for a
   scheduler snapshot and check that all four queues report, including zeros.
2. Sign in, search, and request a forecast. In Sentry, find the corresponding
   search and forecast milestones by `user.id` and verify the detailed attributes.
3. Repeat the same forecast after completion: expect a cached request when a
   network request is made, with no new backend completion. A browser query-cache
   hit emits neither a server request nor a new load execution.
4. In a staging browser, block the forecast WebSocket and repeat a request that
   starts a pending job. Verify a delivery fallback followed by a successful
   polling load.
5. Use Stripe test mode for billing verification; redelivering an event should
   emit `billing.webhook outcome:duplicate` without another transition.

Automated tests mock SDK entry points and external providers. They verify event
semantics without sending telemetry. Live ingestion must be checked after deployment.

## Follow a backend error back to the browser

The browser sends `sentry-trace` and `baggage` on API requests, including the
separate local backend port. Django continues that trace automatically. CORS
already allows both headers; Caddy forwards them unchanged. Tracing starts
before the router's initial navigation and the session bootstrap request.

Queued forecast work preserves the same context in the task's existing JSON
envelope. Workers continue it in isolated `queue.process` transactions, including
fan-out and follow-up tasks. Unhandled task exceptions are captured before the
worker catches them. Old tasks without trace metadata start their own traces.
No database migration is needed.

In Sentry, open a backend error event and follow its **Trace / View trace** link.
The trace includes the initiating browser activity, API request, and queued work.
Select both projects if frontend and backend use separate projects; they must
belong to the same Sentry organization. Select `production` for a deployed build
or `development` for a local session. A recorded browser replay can also provide
visual context, but replay is not guaranteed for every backend error.

A cached, shared, or proactively built forecast keeps the trace of the request or
scheduler that created it. Later viewers do not become its parent retroactively;
use the existing `job.id` metric/log attribute to investigate those views.
WebSocket updates deliver job state; task errors remain on the initiating HTTP
trace rather than a new trace for each update.

After deploying, restart API and worker processes and rebuild the frontend.
Verify in browser Network tools that an API request carries both trace headers,
then compare its trace ID with a backend event's trace ID. Automated tests use the
real SDK with an in-memory transport to verify ASGI error continuation and task
fan-out, and check that local CORS preflights accept both headers.
