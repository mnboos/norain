# MeteoLane for Forerunner 255

Connect IQ watch app. Opening it requests the chronologically next departure across
active, eligible recurring routes (including return rides) and upcoming days of planned
journeys. Journey alternatives use MeteoLane's existing recommendation ranking.
Unselected random route suggestions are not scheduled rides; save one as a route first.

The first page shows departure, distance, duration, and the air-temperature range.
UP/DOWN switches to maximum rain probability, maximum rain rate, and maximum headwind (positive means against the
rider). START refreshes. While open it refreshes every minute.
An offline response retains the current screen with an offline label; a departed ride
is no longer presented as the next ride. Internet requests use the paired phone and
Garmin Connect. The app does not record an activity or replace Garmin navigation.

## Backend and account

The current default is `http://localhost:8000` for development in Garmin's simulator.
HTTP is accepted only for localhost or 127.0.0.1 on port 8000; other addresses require
HTTPS. A physical watch makes requests through the paired phone, so localhost does
not refer to this development PC. Use a phone-reachable HTTPS address for this same
local backend when testing on the real watch.

Start the local PostgreSQL, Redis, Django backend and forecast workers before pairing.
Then create a watch token on the local account page and put it in the private settings
file below, using `http://localhost:8000` as the server for simulator testing.

Deploy the accompanying backend/frontend changes and run `python manage.py migrate`.
The normal forecast workers must be running. On the MeteoLane account page, create a
watch token. It grants access only to the next-ride feed. Creating another token or
disconnecting the watch immediately revokes the old one. The server retains only a hash.

The feed is `GET /api/garmin/next-ride` with `Authorization: Bearer <token>`.
Session-authenticated `GET`, `POST`, and `DELETE /api/garmin/token` report, replace,
and revoke credentials; mutations require CSRF. These are plain Django endpoints,
like billing and notification settings, outside the generated Ninja client.

## Build a personal sideload

1. Install Garmin's [Connect IQ SDK and Forerunner 255 device package](https://developer.garmin.com/connect-iq/sdk/).
2. Create `garmin/private/settings.json` containing your server URL and token:

   ```json
   {"server": "https://meteolane.com", "token": "YOUR_WATCH_TOKEN"}
   ```

3. Run with PowerShell 7 (replace the SDK path):

   ```powershell
   pwsh -File garmin/build.ps1 -SdkPath C:/path/to/connectiq-sdk -SettingsFile garmin/private/settings.json
   ```

The script creates a private signing key on the first build. Keep it for future updates.
The output is `garmin/bin/MeteoLane.prg`. This personal binary contains your token;
keep it private. The settings, signing key, and build output are git-ignored.

Connect the watch by USB and copy the PRG into its `GARMIN/APPS` folder, then disconnect
and open MeteoLane from the watch's apps list. See Garmin's
[sideload instructions](https://developer.garmin.com/connect-iq/connect-iq-basics/your-first-app/).

For a store package without credentials, omit `-SettingsFile` and add `-Package`.
Store publication is separate; no listing has been submitted. Store-installed copies
use the app settings in Garmin Connect IQ for the server and token.

## Verification status

The source is under development. Seven focused backend tests pass (departure ordering,
return rides, journey ordering and recommendation, feed selection and owner filtering,
missing authentication, weather summary). `just update-api` completed, including
frontend lint, type checking and build.
Database-backed token tests could not connect to the configured PostgreSQL database.
The Forerunner 255 device definition is installed. Garmin SDK 9.2.0 successfully
compiled and signed `garmin/bin/MeteoLane.prg` after the web-request callback types
were corrected. That binary has no account token configured.

The latest release build succeeds without warnings. Both watch-side tests in
`source/Tests.mc` pass in the Forerunner 255 simulator: valid/empty/malformed replies,
offline retention, number formatting, revoked tokens, and obsolete request callbacks.
These tests do not establish visual correctness or a live network connection.
The personal sideload build was also compiled successfully using an HTTPS test URL
and a dummy token, exercising the generated private properties and Jungle file.
The release PRG was then rebuilt without those test settings.
Database-backed tests, backend deployment, account pairing, visual checks, and physical
device installation remain unverified. The PRG has no account token configured yet.
