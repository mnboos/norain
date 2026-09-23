# syntax=docker/dockerfile:1
# One Dockerfile, one stage per image. Pick the image with `--target` (compose: `target:`,
# CI: the `publish` matrix in .github/workflows/release.yml).

# ---------------------------------------------------------------------------------------------
# frontend
# ---------------------------------------------------------------------------------------------

# The build output is static files, so build natively instead of under QEMU for arm64.
FROM --platform=$BUILDPLATFORM node:22-bookworm-slim AS frontend-build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./frontend/
COPY packages/api/ ./packages/api/
WORKDIR /app/frontend
RUN npm ci

COPY frontend/ ./

# Sentry, read by vite.config.ts. The DSN is public (it ends up in the bundle), and org and
# project only name where the source maps go. The auth token is a BuildKit secret, so no
# layer or build arg keeps it; without it the build writes no source maps at all.
ARG SENTRY_DSN_FRONTEND=""
ARG SENTRY_RELEASE=""
ARG SENTRY_ORG=""
ARG SENTRY_PROJECT_FRONTEND=""
RUN --mount=type=secret,id=sentry_auth_token,env=SENTRY_AUTH_TOKEN npm run build-only
# Source maps belong to Sentry, never to the web server. The plugin already deletes them
# after the upload; this makes sure none reaches /srv even if that step did not run.
RUN find dist -name '*.map' -print -delete


FROM caddy:2.10-alpine AS frontend

COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=frontend-build /app/frontend/dist /srv


# ---------------------------------------------------------------------------------------------
# graphhopper + photon
# ---------------------------------------------------------------------------------------------

FROM eclipse-temurin:24-jre AS java-base

RUN apt-get update -y && \
    apt-get install -y --no-install-recommends ca-certificates wget && \
    rm -rf /var/lib/apt/lists/*


FROM java-base AS graphhopper

WORKDIR /graphhopper

# A graph only loads with the jar that built it: bumping this means rebuilding the graph
# (empty the graph cache and restart the service).
ADD https://github.com/graphhopper/graphhopper/releases/download/10.2/graphhopper-web-10.2.jar graphhopper.jar

COPY docker/certs/* /usr/local/share/ca-certificates/
RUN update-ca-certificates

RUN keytool -import -noprompt -alias zscaler-corp-cert -trustcacerts -keystore ${JAVA_HOME}/lib/security/cacerts -storepass changeit -file /usr/local/share/ca-certificates/zscaler-root.pem.crt

# osmium cuts an OSM extract down to what the bike profiles use before every build.
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends osmium-tool && \
    rm -rf /var/lib/apt/lists/*

COPY docker/graphhopper-entrypoint.sh entrypoint.sh
COPY docker/graphhopper-filter-osm.sh filter-osm.sh
# Also run on its own (`just poi-extract`): the journey planner's POIs, see core/pois.py.
COPY docker/osm-extract-pois.sh extract-pois.sh
RUN chmod +x /graphhopper/entrypoint.sh /graphhopper/filter-osm.sh /graphhopper/extract-pois.sh

ENTRYPOINT ["/graphhopper/entrypoint.sh"]


FROM java-base AS photon

RUN apt-get update -y && \
    apt-get install -y --no-install-recommends pbzip2 zstd && \
    rm -rf /var/lib/apt/lists/*

ADD https://github.com/komoot/photon/releases/download/1.0.1/photon-1.0.1.jar photon.jar

COPY docker/photon-entrypoint.sh /entrypoint.sh
COPY import-photon-dump.sh import-photon-dump.sh
RUN chmod +x /entrypoint.sh

# The entrypoint reuses a prepared index, or imports an artifact when configured.
ENTRYPOINT ["/entrypoint.sh"]


# ---------------------------------------------------------------------------------------------
# backend (last, so a plain `docker build .` produces the app image)
# ---------------------------------------------------------------------------------------------

# Interpreter, system libraries, uv and the locked dependencies — everything but the source.
FROM python:3.14-slim-bookworm AS python-base

ENV PATH="/app/backend/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_NO_CACHE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN apt-get update \
    && apt-get install --yes --no-install-recommends binutils gdal-bin libproj-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --link --from=ghcr.io/astral-sh/uv:0.10 /uv /uvx /usr/local/bin/

WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


FROM python-base AS backend

RUN useradd --create-home --uid 10001 app \
    && chown app:app /app /app/backend

COPY --chown=app:app backend/ ./

USER app
EXPOSE 8000

# The commit this image was built from; sentry-sdk reports it as the release.
ARG SENTRY_RELEASE=""
ENV SENTRY_RELEASE=$SENTRY_RELEASE

CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "backend.asgi:application"]
