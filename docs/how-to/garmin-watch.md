# Install and test MeteoLane on a Forerunner 255

The Connect IQ app shows your chronologically next ride. It compares active,
eligible recurring routes, including return trips, with upcoming days of planned
journeys. For a journey it uses MeteoLane's recommended route alternative.
Random ride suggestions count after you save one as a recurring route.

This is a personal USB installation. There is no Connect IQ Store listing yet.
The app shows forecasts; it does not record an activity or provide navigation.

## What you need

- A Forerunner 255, a USB data cable, and a phone paired through Garmin Connect.
- A running MeteoLane backend, PostgreSQL/PostGIS, Redis, and
  [forecast workers](background-jobs.md).
- A MeteoLane account with a scheduled route or planned journey.
- PowerShell 7, Java, and Garmin's [Connect IQ SDK](https://developer.garmin.com/connect-iq/sdk/),
  including the **Forerunner 255** device package.

Run the commands below from the repository root in PowerShell 7.

## 1. Make your local backend reachable from the phone

| Where the app runs | Server address |
| --- | --- |
| Garmin simulator on this PC | `http://localhost:8000` |
| Physical watch, through its paired phone | An HTTPS address reachable from the phone |

**The watch cannot use this PC's localhost address.** Its web requests travel
through Garmin Connect on the phone. The current build defaults to localhost for
simulator development. HTTP is allowed only for `localhost:8000` and
`127.0.0.1:8000`; other addresses must use HTTPS.

For temporary physical-watch testing, you can use an HTTPS tunnel to your local
backend. One option is a [Cloudflare Quick Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).
Install `cloudflared` using Cloudflare's instructions, then run:

```powershell
cloudflared tunnel --url http://localhost:8000 --http-host-header localhost
```

Keep that terminal running. Copy the printed `https://…trycloudflare.com` address.
The host-header override lets Django receive the local host name it already accepts.
This temporarily makes your backend reachable over the internet; stop the tunnel
when testing is finished. Quick Tunnel addresses can change when restarted.

On your **phone**, open `https://YOUR-TUNNEL-HOST/healthz`. It must display `ok`
before you try the watch. Tunnel setup and physical-watch access have not yet been
verified for this repository; this is the procedure to perform that check.

## 2. Pair your MeteoLane account

Apply migrations if needed:

```powershell
cd backend
uv run python manage.py migrate
cd ..
```

Open the local web app at <http://localhost:3000/account> and sign in.
Under **Garmin watch** (German: **Garmin-Uhr**), choose **Create watch token**.
Copy the token while it is visible. It grants access to the next-ride feed,
not general access to your account.

Create `garmin/private/settings.json`:

```json
{
  "server": "https://YOUR-TUNNEL-HOST",
  "token": "YOUR_WATCH_TOKEN"
}
```

For simulator-only testing, use `http://localhost:8000` instead. If this file already
exists, keep its token and change only the server address. Do not include `/api`
in the server URL. Do not paste the token into source files or commit it.

**Replace watch token** invalidates the previous token. **Disconnect watch** revokes
it. After replacement, update the private settings and rebuild your USB-installed app.
The server stores only a hash; it cannot show an existing token again.

## 3. Build the paired app

Use the SDK selected in Garmin SDK Manager:

```powershell
$sdkPath = (Get-Content "$env:APPDATA\Garmin\ConnectIQ\current-sdk.cfg" -Raw).Trim()
pwsh -File garmin/build.ps1 -SdkPath $sdkPath -SettingsFile garmin/private/settings.json
```

Expected result: `BUILD SUCCESSFUL` and **`garmin/bin/MeteoLane.prg`**.
The script creates a signing key in `garmin/private/` on the first run. Keep that
key for updates. The settings, key, and binaries are git-ignored.
The paired PRG contains your watch token, so keep that binary private too.

A build without `-SettingsFile` uses the default localhost address and an empty token.
It will ask for a token and is not ready for personal USB testing.

## 4. Install on your watch

1. Connect the Forerunner 255 to your PC with a USB data cable.
2. Open the watch in File Explorer and navigate to **Internal Storage → GARMIN → APPS**
   (or **GARMIN → APPS**, depending on how Windows displays the device).
3. Copy **`garmin/bin/MeteoLane.prg`** into **APPS**. Copy the PRG, not the `.iq`
   package or the simulator test binary.
4. Disconnect the watch safely.
5. Press **START**, open the apps/activity list, and select **MeteoLane**.
6. Keep Garmin Connect connected to the watch on your phone. Keep the local backend,
   workers, and tunnel running on your PC.

Garmin documents PRG copying in its [first-app guide](https://developer.garmin.com/connect-iq/connect-iq-basics/your-first-app/).
To update, rebuild and replace the same PRG through USB.

## 5. Check the result

The opening page shows the next ride's name, departure, distance, duration, and
minimum/maximum air temperature. Departure labels use Europe/Zurich time.

| Button | Action |
| --- | --- |
| START | Refresh now |
| UP / DOWN | Switch between ride details and weather |
| BACK | Leave the app |

The weather page shows peak rain probability, maximum rain rate in mm/h, and
maximum headwind in km/h. Positive headwind means wind against the rider.
The app refreshes every minute while open. A departed ride is no longer displayed
as the next ride; refresh to fetch the next departure.

Compare the watch's ride and departure with the web app. Then check both pages and
press START. Missing values are `--`, not zero. Weather can take time to arrive
while the existing background jobs fetch forecast cells.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Watch does not appear in File Explorer | Use a data-capable USB cable; confirm Windows recognizes the device. |
| App absent after copying | Confirm the target is Forerunner 255 and the file is the signed PRG inside `GARMIN/APPS`; disconnect USB. |
| `Set watch token` | Rebuild with `-SettingsFile`; the default build has no token. |
| `Check watch token` | Token was replaced/revoked, or belongs to a different server. Update settings and rebuild. |
| `Use an HTTPS server` | A physical-watch server needs HTTPS. Only the explicit localhost simulator URLs allow HTTP. |
| `Offline - START retries` | Check phone pairing, Garmin Connect, tunnel availability, and `/healthz` from the phone. |
| HTTP 400 / `DisallowedHost` through the tunnel | Keep `--http-host-header localhost` in the tunnel command. |
| `No upcoming rides` | Check the account's active schedules, route entitlement, and future planned journey dates. |
| `Weather refreshing` or `Weather unavailable` | Check forecast workers and provider errors using the [forecast troubleshooting guide](troubleshooting.md). |
| Backend error `no_weather_yet` | The forecast job has no usable weather yet; this is separate from watch pairing or connectivity. |
| Build reports invalid device `fr255` | Install Forerunner 255 in Garmin SDK Manager. |

## Tested so far

During local development, the Forerunner 255 release build succeeded, both simulator
unit tests passed, and all nine Garmin backend tests passed, including token rotation
and revocation. The Garmin migration was applied locally and an authenticated HTTP
request returned the next local ride. The local forecast job reported `no_weather_yet`.

Those checks do **not** prove physical-watch installation, phone-to-backend access,
screen layout, or weather delivery. Follow the steps above to verify those parts.

[App source and developer notes](../../garmin/README.md) · [HTTP API](../reference/api.md#garmin-watch)
