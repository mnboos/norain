# MeteoLane for Forerunner 255

**[Install and test the app on your watch](../docs/how-to/garmin-watch.md)**

The guide covers local server access from the phone, pairing, building, USB
installation, controls, troubleshooting, and verification status. It is linked
from the project README and documentation index.

## Development

- `source/MeteolaneApp.mc`: watch UI and authenticated next-ride requests.
- `source/Tests.mc`: simulator tests.
- `resources/`: strings, settings, default properties, and launcher icon.
- `manifest.xml`: Forerunner 255 (`fr255`) and Communications permission.
- `build.ps1`: signed personal PRG or store package build.
- `private/`: git-ignored account settings and signing key.
- `bin/`: git-ignored output.

The default is `http://localhost:8000` with no token. A real watch needs a
phone-reachable HTTPS address and a paired build. See the
[API reference](../docs/reference/api.md#garmin-watch).

## Backend tests

From `backend/`:

```powershell
uv run python manage.py test core.test_garmin --keepdb
```

## Simulator tests

From the repository root, with Garmin's simulator running:

```powershell
$sdkPath = (Get-Content "$env:APPDATA\Garmin\ConnectIQ\current-sdk.cfg" -Raw).Trim()
pwsh -File garmin/build.ps1 -SdkPath $sdkPath
& "$sdkPath\bin\monkeyc.bat" -f garmin/monkey.jungle -o garmin/bin/MeteoLane-tests.prg -y garmin/private/developer_key.der -d fr255 -t -w
& "$sdkPath\bin\monkeydo.bat" garmin/bin/MeteoLane-tests.prg fr255 /t
```

Read the printed summary: the runner can return zero even when tests fail.
The initial build creates the signing key if needed, and replaces the release PRG
with an unpaired build. Rebuild with private settings before installing on a watch.

## Store package

```powershell
pwsh -File garmin/build.ps1 -SdkPath $sdkPath -Package
```

Output: `bin/MeteoLane.iq`, without credentials. The script refuses `-Package`
together with `-SettingsFile`. No listing has been submitted. Store-installed
copies would use Connect IQ app settings for server and token.

See [Tested so far](../docs/how-to/garmin-watch.md#tested-so-far) for verification limits.
