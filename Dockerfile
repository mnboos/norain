# One Dockerfile, one stage per image. Pick the image with `--target` (compose: `target:`,
# CI: the `publish` matrix in .github/workflows/release.yml).

# ---------------------------------------------------------------------------------------------
# frontend
# ---------------------------------------------------------------------------------------------

# The build output is static files, so build natively instead of under QEMU for arm64.
FROM --platform=$BUILDPLATFORM node:22-bookworm-slim AS frontend-build

WORKDIR /app

ENV VITE_SENTRY_DSN_FRONTEND=$SENTRY_DSN_FRONTEND

COPY frontend/package.json frontend/package-lock.json ./frontend/
COPY packages/api/ ./packages/api/
WORKDIR /app/frontend
RUN npm ci

COPY frontend/ ./
RUN npm run build-only


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

COPY docker/graphhopper-entrypoint.sh entrypoint.sh
RUN chmod +x /graphhopper/entrypoint.sh

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

CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "backend.asgi:application"]
