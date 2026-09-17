# Deploy NoRain to a Docker VPS

This deployment uses Caddy for automatic HTTPS, PostgreSQL/PostGIS for application data,
Docker Compose for the application processes, GitHub Container Registry (GHCR)
for immutable images, and Restic for encrypted database backups.

## Requirements

Use a domain whose A/AAAA records point to the VPS. Open only SSH, HTTP (80), and
HTTPS (443) in the VPS firewall. Install Docker Engine, the Docker Compose plugin,
Git, and Restic. Create a non-root `norain` deployment user in the `docker` group,
then clone this repository at `/srv/norain`.

GraphHopper builds its routing graph on the VPS the first time it starts with an empty
`graphhopper/cache`, from `OSM_DATA_URL`. Building needs more memory than serving:
Switzerland needs a build heap (`GRAPHHOPPER_BUILD_HEAP`) of about 6 GB and a serving heap
of about 3 GB; DACH a 16–24 GB build heap and a 10–14 GB serving heap, or a 3 GB serving
heap with `GRAPHHOPPER_DATAACCESS=MMAP`. `GRAPHHOPPER_MEM_LIMIT` must fit the build heap.
If the VPS cannot hold the build, [build the graph on another
machine](build-routing-graph.md) and copy it in. Prepare Photon with a manual import before first startup (see below), using
`PHOTON_IMPORT_HEAP` (4 GB by default). The published images are built for
both amd64 and arm64, so ARM hosts such as Oracle's Ampere A1 work. Do not expose
GraphHopper, Photon, PostgreSQL, or Django directly.

### ARM hosts: enable database emulation

The application images support arm64, but
[`postgis/postgis:18-3.6`](https://github.com/postgis/docker-postgis) is amd64-only.
Production therefore sets `platform: linux/amd64` for `db`. On an ARM VPS
(`uname -m` prints `aarch64`), register amd64 emulation before starting the database:

```bash
docker run --privileged --rm tonistiigi/binfmt --install amd64
docker compose -f docker-compose.prod.yml run --rm --no-deps db postgres --version
docker compose -f docker-compose.prod.yml up -d db
```

The first command registers QEMU with the host kernel and needs privileged access,
as described in [Docker's emulation setup](https://docs.docker.com/build/building/multi-platform/#install-qemu-manually).
If `exec /usr/local/bin/docker-entrypoint.sh: exec format error` returns after a
host reboot, repeat registration and the version check. Selecting a platform alone
does not install an emulator. Emulation adds database CPU overhead.

## Configure the server

Copy `deploy/production.env.template` to `/srv/norain/.env`, set its ownership to
the deployment user and mode `0600`, and replace every placeholder. Set
`APP_STORAGE_PATH` to an absolute host path outside the Git checkout. It contains
all persistent bind mounts:

```text
APP_STORAGE_PATH/
  caddy/{data,config}/
  django/static/
  postgres/
  graphhopper/{osm,cache}/
  photon/
```

Create the directory tree before the first release, owned by the deployment user:

```bash
sudo install -d -o norain -g norain -m 0750 \
  /srv/norain-data/caddy/data /srv/norain-data/caddy/config \
  /srv/norain-data/django/static /srv/norain-data/postgres \
  /srv/norain-data/graphhopper/osm /srv/norain-data/graphhopper/cache \
  /srv/norain-data/photon
```

`graphhopper/osm` keeps the downloaded OSM extract and elevation tiles, so a rebuild does
not download them again; allow disk space for them next to the graph in `graphhopper/cache`.

The supplied Compose file expects these internal endpoints:

```dotenv
GEOCODER_API_URL=http://photon:2322/api
GRAPHHOPPER_API_URL=http://graphhopper:8989
```

Set `DOMAIN`, `ACME_EMAIL`, all Django/PostgreSQL/SMTP secrets,
and the desired map-data URLs. Use a transactional SMTP provider with a domain
sender: sign-up is deliberately blocked until email verification is complete.
The template derives `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, and
`FRONTEND_URL` from `DOMAIN`.

Set `DJANGO_ADMIN_PATH` to a random slug (for example `openssl rand -hex 8`). The Django
admin is served at `https://YOUR_DOMAIN/<DJANGO_ADMIN_PATH>/`, not `/admin/`. The backend
refuses to start without it, so on an existing server add it to `.env` **before**
deploying a release that needs it.

Create `/etc/norain/restic-password`, restrict it to root and the `norain` user,
and set `RESTIC_REPOSITORY` plus `RESTIC_PASSWORD_FILE` in the server
environment. Initialize the encrypted Restic repository once with `restic init`.
The repository should be in independent offsite storage, not only on the VPS.

Log in to GHCR on the VPS with a fine-grained token that has read-only permission
to the image packages:

```bash
echo "$GHCR_READ_TOKEN" | docker login ghcr.io -u YOUR_GITHUB_USER --password-stdin
```

## Import Photon manually

Photon runs on an isolated Docker network in production. It cannot download an
index there; changing DNS servers does not provide Internet access. Normal startup
reuses the index at `${APP_STORAGE_PATH}/photon/photon_data` and fails with instructions
if it is missing.

Download the Photon **1.0** artifact matching your region on a machine with Internet
access and copy it to the VPS. For the default region:

```bash
wget -O photon-dump.jsonl.zst https://download1.graphhopper.com/public/europe/switzerland-liechtenstein/photon-dump-switzerland-liechtenstein-1.0-latest.jsonl.zst
scp photon-dump.jsonl.zst norain@YOUR_VPS:/srv/norain-data/photon/dump.jsonl.zst
```

Use your actual `APP_STORAGE_PATH` if it differs from `/srv/norain-data`. On the VPS,
pull a Photon image containing the local-import entrypoint, or build it from this
checkout (`docker compose -f docker-compose.prod.yml build photon`), then import:

```bash
cd /srv/norain
docker compose -f docker-compose.prod.yml stop photon
docker compose -f docker-compose.prod.yml run --rm --no-deps \
  -e PHOTON_INDEX_FILE=/photon_data/dump.jsonl.zst \
  -e PHOTON_IMPORT_ONLY=true photon
docker compose -f docker-compose.prod.yml up -d photon
```

The import runs without network access and exits when complete. The source dump is
kept; you can remove it after success. Plain `.jsonl` dumps and prebuilt `.tar.bz2`
indexes are also supported. Failed imports clean up their temporary index so a
retry can start cleanly. Do not run multiple imports against the same data directory.

An existing `photon_data` directory is always reused. To replace an index, stop
Photon and move that directory to a backup location before importing. You can also
copy a completed `photon_data` directory from another machine using the same Photon
version; stop Photon on both machines during the copy.

## First deployment and updates

This release uses a fresh PostGIS schema and does not import SQLite data. Before its
first deployment, confirm that the old database is disposable and initialize an empty
PostgreSQL directory. Never remove a populated database directory without explicit
approval and a verified backup.

The GitHub Actions workflow publishes SHA-tagged images after backend and
frontend checks pass. Configure the GitHub `production` environment with
`VPS_DEPLOY_SSH_KEY`, `VPS_HOST`, `VPS_USER`, `VPS_KNOWN_HOSTS`, and
`VPS_PUBLIC_HEALTH_URL` (for example, `https://norain.example.com/healthz`);
protect that environment with the desired reviewer rule. `VPS_KNOWN_HOSTS` must
contain the VPS's pinned SSH host key, obtained through an independently trusted
channel.

For an initial manual release, substitute a published commit SHA:

```bash
cd /srv/norain
BACKEND_IMAGE=ghcr.io/mnboos/norain-backend:COMMIT_SHA \
FRONTEND_IMAGE=ghcr.io/mnboos/norain-frontend:COMMIT_SHA \
GRAPHHOPPER_IMAGE=ghcr.io/mnboos/norain-graphhopper:COMMIT_SHA \
PHOTON_IMAGE=ghcr.io/mnboos/norain-photon:COMMIT_SHA \
./deploy/release.sh
```

From a workstation, `just deploy [COMMIT_SHA]` does the same over SSH (the SHA defaults
to local `HEAD`, which CI must already have published) and then checks the health
endpoint. It reads `VPS_USER`, `VPS_HOST`, and `VPS_PUBLIC_HEALTH_URL` from the local
`.env`, and your SSH key must be accepted by the deployment user.

Photon loads the index prepared above. GraphHopper loads its graph, or builds it first
when `graphhopper/cache` is empty. The backend, `worker-forecasts` and `worker-default` wait
until GraphHopper is healthy (and Caddy waits for the backend), so the first release waits
for the whole build and the site stays down until it is done. Restarting only
`graphhopper` to rebuild leaves the running backend up, but routing fails until the build
ends. Follow both with
`docker compose --env-file .env -f docker-compose.prod.yml logs -f graphhopper photon`.
After they are ready, verify `https://YOUR_DOMAIN/healthz`, sign up, verify the
email, create a route, and confirm the worker computes its geometry.

### Admin access

The admin is on the public internet, so it asks for a code from an authenticator app as
well as the password, and ten failed sign-ins from one address lock that address out for
30 minutes (this applies to the app's sign-in too). Create the admin account and its
authenticator device in the backend container:

```bash
docker compose --env-file .env -f docker-compose.prod.yml exec backend python manage.py createsuperuser
docker compose --env-file .env -f docker-compose.prod.yml exec backend python manage.py add_totp_device --identifier YOU
```

Enter the printed key in an authenticator app and store the backup codes somewhere safe;
they are shown once. Then sign in at `https://YOUR_DOMAIN/<DJANGO_ADMIN_PATH>/`: submit
the username and password first, and the form then lists your devices. Pick `default` and
enter the code from the app (or `backup` and a backup code).

### Build images on the VPS

The production services inherit their build definitions from `docker-compose.base.yml`.
To build from the checked-out source, set `COMPOSE_FILE=docker-compose.prod.yml` in
the VPS `.env` (included in the production template). Set the image variables to
local tags such as `BACKEND_IMAGE=norain-backend:local`,
`FRONTEND_IMAGE=norain-frontend:local`, `GRAPHHOPPER_IMAGE=norain-graphhopper:local`,
and `PHOTON_IMAGE=norain-photon:local`, then run:

```bash
cd /srv/norain
just deploy-local
```

This recipe uses `COMPOSE_FILE` from the production `.env`, builds the application
images, pulls PostGIS and Redis, runs migrations, collects static files, and starts
the services. If Just is not installed, the equivalent commands are:

```bash
cd /srv/norain
docker compose build
docker compose pull db redis
docker compose run --rm --pull never backend python manage.py migrate --noinput
docker compose run --rm --pull never --user root backend python manage.py collectstatic --noinput
docker compose up -d --pull never --remove-orphans
```

The build takes the Sentry settings from `.env`: `SENTRY_DSN_FRONTEND` is baked into the
frontend image (so changing it means building again), and with `SENTRY_AUTH_TOKEN`,
`SENTRY_ORG` and `SENTRY_PROJECT_FRONTEND` set the source maps are uploaded to Sentry and
left out of the image. `just deploy-local` sets `SENTRY_RELEASE` to the checked-out commit;
without Just, run `SENTRY_RELEASE="$(git rev-parse HEAD)" docker compose build`.

`deploy/release.sh` pulls published images; use `just deploy-local` for local
builds.

To roll back a published-image deployment, run the release command with the prior known-good immutable SHA. The
configured bind-mount directories persist PostgreSQL, Caddy certificates, and
imported geographic data across releases.

## Shared hostname reverse proxy

On a server hosting several apps, run one independent Caddy stack that owns ports
80/443 and TLS certificates. Each app's frontend joins the external `server-proxy`
network under a unique alias; databases and backend services remain private.
See [the proxy setup and hostname activation guide](../../deploy/proxy/README.md).

After creating the shared network, set this in NoRain's production `.env`:

```dotenv
COMPOSE_FILE=docker-compose.prod.yml:docker-compose.proxy.yml
```

Then `docker compose up -d`, `just deploy-local`, and `deploy/release.sh` use both
files. Commands that explicitly supply `-f` must include both files too. The NoRain
frontend listens internally at `norain-web:80`, with no host ports, and preserves
the central proxy's forwarded HTTPS headers. Keep the NoRain hostname template
inactive until you choose a domain and configure its DNS and application origins.

## Backups and recovery

Install the included systemd units, then enable the daily backup:

```bash
sudo cp deploy/norain-backup.service deploy/norain-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now norain-backup.timer
```

Check `systemctl status norain-backup.timer` and `journalctl -u norain-backup.service`.
The job stores PostgreSQL custom dumps in Restic and retains 14 daily, 8 weekly,
and 12 monthly backups. Perform a restore drill regularly: restore a dump to a
clean PostgreSQL database, deploy a known image SHA, and verify login, a private
route, and a forecast. GraphHopper and Photon data are reproducible but can make
recovery slower; back up their bind-mounted directories too if that recovery-time
cost is not acceptable.
