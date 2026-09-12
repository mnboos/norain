# Deploy NoRain to a Docker VPS

This deployment uses Caddy for automatic HTTPS, PostgreSQL/PostGIS for application data,
Docker Compose for the application processes, GitHub Container Registry (GHCR)
for immutable images, and Restic for encrypted database backups.

## Requirements

Use a domain whose A/AAAA records point to the VPS. Open only SSH, HTTP (80), and
HTTPS (443) in the VPS firewall. Install Docker Engine, the Docker Compose plugin,
Git, and Restic. Create a non-root `norain` deployment user in the `docker` group,
then clone this repository at `/srv/norain`.

The default Swiss map import may use 6 GB for GraphHopper and 4 GB while Photon
imports. Select RAM, disk, and the `GRAPHHOPPER_HEAP`/`PHOTON_IMPORT_HEAP`
values before the first start; larger extracts need more of both. Do not expose
GraphHopper, Photon, PostgreSQL, or Django directly.

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

The supplied Compose file expects these internal endpoints:

```dotenv
GEOCODER_API_URL=http://photon:2322/api
GRAPHHOPPER_API_URL=http://graphhopper:8989
```

Set `DOMAIN`, `ACME_EMAIL`, `FRONTEND_URL`, all Django/PostgreSQL/SMTP secrets,
and the desired map-data URLs. Use a transactional SMTP provider with a domain
sender: sign-up is deliberately blocked until email verification is complete.

Create `/etc/norain/restic-password`, restrict it to root and the `norain` user,
and set `RESTIC_REPOSITORY` plus `RESTIC_PASSWORD_FILE` in the server
environment. Initialize the encrypted Restic repository once with `restic init`.
The repository should be in independent offsite storage, not only on the VPS.

Log in to GHCR on the VPS with a fine-grained token that has read-only permission
to the image packages:

```bash
echo "$GHCR_READ_TOKEN" | docker login ghcr.io -u YOUR_GITHUB_USER --password-stdin
```

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

The first geographic imports can take a long time. Follow them with
`docker compose --env-file .env -f docker-compose.prod.yml logs -f graphhopper photon`.
After they are ready, verify `https://YOUR_DOMAIN/healthz`, sign up, verify the
email, create a route, and confirm the worker computes its geometry.

To roll back, run the same command with the prior known-good immutable SHA. The
configured bind-mount directories persist PostgreSQL, Caddy certificates, and
imported geographic data across releases.

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
