# Run the Free / Plus beta

Apply migrations with `cd backend && uv run python manage.py migrate`, then restart
the API, workers and `run_forecast_scheduler`. The scheduler now enqueues briefings
every minute as well as its hourly maintenance. No existing route is deleted.

## Plans and paired rides

Free includes two active saved rides and ordinary route forecasts. Plus includes
20, departure comparison, available uncertainty details, and briefings for five
saved rides. The database/API identifier remains `pro` for compatibility; its
customer-facing name is Plus.

A saved ride can include outward and return journeys with separate weekday/time
schedules. Both directions use independent GraphHopper geometry and forecasts,
count together as one slot, and share the briefing channel. Creating and deleting
the pair is atomic. Existing one-way routes retain their IDs and behaviour.

Users can voluntarily start a single 14-day trial from their account. It requires
no card and never creates a Stripe subscription. Expiry is enforced on access and
background work, rather than relying on a nightly job. Select the two Free rides
in the account page; without a selection, the oldest two are used. Extra rides
stay visible but their forecasts and automation pause. Individual ad-hoc forecasts
remain available. Plus is also capped at 20 if a legacy account has more routes.

## Invite colleagues without charging them

After colleagues create their own accounts, select them in Django admin's **Users**
page and run **Grant 90 days of complimentary Plus (no payment)**. The action changes
only access; it sends no invitation or email and makes no charge. It does not consume
their self-service trial. To change or revoke a grant, edit `complimentary_until`
in their Subscription. Reapplying the action never shortens an existing grant.

Keep complimentary testers separate from paying customers when evaluating conversion.
Initial validation: 15 commuters and 15 leisure riders over six weeks. Record actual
use, useful departure changes, delivery failures, notification opt-outs and support
time. Five real purchases across both groups is an early signal, not proof of retention.
Target €200/month surplus, assuming free Oracle hosting while available. At an
illustrative €20 annual contribution per subscriber, €50/month of other costs means
150 paying subscribers. Replace that assumption with measured costs before scaling.

## Briefings

Configure the existing email backend and set `BRIEFING_EMAIL_ENABLED=true` to make
email selectable. Development prints messages to the console; use a functioning
SMTP backend for real deliveries. The recipient is always the account's email.

For browser push, generate a persistent VAPID key pair with the installed `vapid`
CLI (`uv run vapid --help`, then `uv run vapid --gen` in a private directory).
Set `VAPID_PRIVATE_KEY` to the private PEM file path or supported encoded key,
`VAPID_PUBLIC_KEY` to its base64url uncompressed public point, and `VAPID_SUBJECT`
to a contact URI such as `mailto:admin@example.com`. Make the private key available
to workers, outside source control. Do not rotate keys without re-subscribing devices.
The public key must correspond to the private key. API and workers need the same
configuration. See the [pywebpush documentation](https://github.com/web-push-libs/pywebpush).

Users enable push through an explicit browser permission action, then choose email,
push or off for each ride in their account. Push needs HTTPS (localhost is allowed).
On iOS, add the app to the Home Screen first. Known Google, Mozilla and Apple push
hosts are allowed; arbitrary endpoints are rejected. Each account may register five
devices. Disabling a device removes its server registration and browser subscription.

The scheduler prepares the forecast ten minutes before the briefing is due, then
sends once, about 60 minutes before the earliest permitted departure. Outward and
return journeys each receive their own briefing. There is a ceiling of ten briefings
per account per local day. Enabling a channel inside the lead window may send the
next briefing immediately. The message is a scheduled forecast, not continuous
weather-change monitoring. Missing or incomplete data are explicitly labelled.

A persistent per-journey/departure claim prevents duplicate sends. Delivery uses
at-most-once attempts: ambiguous provider failures or process crashes are **not**
retried automatically. The account page retains the message even if delivery fails;
expired browser subscriptions are removed on HTTP 404/410. History is retained for
30 days. Opt-out, account expiry and paused routes are checked again before sending.
Changing a pending ride or its channel cancels that occurrence's pending briefing.

Free forecasts are demand-driven. Only Plus rides with briefings and departures
within four hours receive proactive cell warming; background work rechecks access.

## Enable paid subscriptions later

Leave `BILLING_ENABLED=false` during the beta. Before accepting payment, verify that
provider permissions cover commercial use; the current Open-Meteo hosted free API
is non-commercial. Provider replacement and DACH geodata expansion are deferred.
Routing coverage remains whatever the server's imported graph supports.

Create two recurring Stripe prices in EUR: **€29/year** and **€3.90/month**. Configure
`STRIPE_PRICE_ID_PLUS_ANNUAL`, `STRIPE_PRICE_ID_PLUS_MONTHLY`, `STRIPE_SECRET_KEY`,
`STRIPE_WEBHOOK_SECRET`, the customer portal and webhook, then set
`BILLING_ENABLED=true`. `STRIPE_PRICE_ID_PRO` remains a legacy annual-price fallback.
Configure tax handling so the checkout total agrees with advertised consumer prices.
The server selects the price ID; clients send only `annual` or `monthly`. Existing
subscribers use the billing portal; a success URL never grants access.

Track `billing.action` (including `trial_started`), `subscription.transition`,
`briefing.delivery`, provider requests and forecast job metrics. Distinguish paid,
trial and complimentary cohorts when analysing conversion; do not count invitations
as purchases. No real subscriptions, notifications or complimentary grants are
created merely by deploying these changes.
