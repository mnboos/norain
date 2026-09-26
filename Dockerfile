# syntax=docker/dockerfile:1
# One Dockerfile, one stage per image. Pick the image with `--target` (compose: `target:`,
# CI: the `publish` matrix in .github/workflows/release.yml).

# ---------------------------------------------------------------------------------------------
# frontend
# ---------------------------------------------------------------------------------------------

# The build output is static files, so build natively instead of under QEMU for arm64.
FROM --platform=$BUILDPLATFORM docker.io/library/node:22-bookworm-slim AS frontend-build

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


FROM docker.io/library/caddy:2.10-alpine AS frontend

COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=frontend-build /app/frontend/dist /srv


# ---------------------------------------------------------------------------------------------
# graphhopper + photon
# ---------------------------------------------------------------------------------------------

FROM docker.io/library/eclipse-temurin:24-jre AS java-base

RUN apt-get update -y && \
    apt-get install -y --no-install-recommends ca-certificates wget && \
    rm -rf /var/lib/apt/lists/*


# Build Java once on the build host; the shaded jar includes the platform-specific WebP libraries.
FROM --platform=$BUILDPLATFORM docker.io/library/eclipse-temurin:25-jdk AS graphhopper-build
ARG GRAPHHOPPER_COMMIT=d9506cd7d36d5d068d9118b19b86cf0609dbe773
RUN apt-get update && apt-get install -y --no-install-recommends git maven ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY docker/certs/* /usr/local/share/ca-certificates/
RUN update-ca-certificates && keytool -import -noprompt -alias zscaler-corp-cert -trustcacerts \
    -keystore ${JAVA_HOME}/lib/security/cacerts -storepass changeit \
    -file /usr/local/share/ca-certificates/zscaler-root.pem.crt
WORKDIR /source
RUN git init && git remote add origin https://github.com/graphhopper/graphhopper.git \
    && git fetch --depth 1 origin "$GRAPHHOPPER_COMMIT" && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "$GRAPHHOPPER_COMMIT" \
    && printf '%s\n' "$GRAPHHOPPER_COMMIT" > /graphhopper-revision
# Expose coordinate heights for saved paths using GraphHopper's native provider.
COPY docker/graphhopper/ElevationResource.java /source/web/src/main/java/com/graphhopper/application/resources/ElevationResource.java
RUN sed -i '/environment.jersey().register(new RootResource());/a\        environment.jersey().register(com.graphhopper.application.resources.ElevationResource.class);' \
    web/src/main/java/com/graphhopper/application/GraphHopperApplication.java
# Where the zoom-15 terrain has no value, read the zoom-12 archive instead of storing 0 m.
# The grep fails the build if the line moved and the sed matched nothing.
COPY docker/graphhopper/FallbackElevationProvider.java /source/core/src/main/java/com/graphhopper/reader/dem/FallbackElevationProvider.java
RUN sed -i 's/ElevationProvider elevationProvider = createElevationProvider(ghConfig);/ElevationProvider elevationProvider = com.graphhopper.reader.dem.FallbackElevationProvider.withFallback(createElevationProvider(ghConfig), ghConfig, ghConfig.getString("graph.elevation.pmtiles.fallback.cache_dir", ""));/' \
    core/src/main/java/com/graphhopper/GraphHopper.java \
    && grep -q 'FallbackElevationProvider.withFallback' core/src/main/java/com/graphhopper/GraphHopper.java
RUN --mount=type=cache,target=/root/.m2 mvn -B -ntp -pl web -am package -DskipTests

FROM docker.io/library/eclipse-temurin:25-jre AS graphhopper
ARG TARGETARCH
WORKDIR /graphhopper
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates wget osmium-tool python3 python3-venv python3-pil \
    && rm -rf /var/lib/apt/lists/*
COPY docker/certs/* /usr/local/share/ca-certificates/
RUN update-ca-certificates && keytool -import -noprompt -alias zscaler-corp-cert -trustcacerts \
    -keystore ${JAVA_HOME}/lib/security/cacerts -storepass changeit \
    -file /usr/local/share/ca-certificates/zscaler-root.pem.crt
COPY --from=graphhopper-build /source/web/target/graphhopper-web-12.0-SNAPSHOT.jar graphhopper.jar
COPY --from=graphhopper-build /graphhopper-revision /graphhopper/revision
# Pin both native CLI builds and verify downloads before extracting them.
RUN case "$TARGETARCH" in \
      arm64) arch=arm64; checksum=f8bd47e7ea866863489cad588fbaf2f31f42e5821f7a03f009b3769f05801cb1 ;; \
      amd64) arch=x86_64; checksum=3ed7dbf4ec2e6dfe5e25b6f70d1ffc932729f93c86db353bf514dd71010a312f ;; \
      *) exit 1 ;; esac \
    && wget -q "https://github.com/protomaps/go-pmtiles/releases/download/v1.31.2/go-pmtiles_1.31.2_Linux_${arch}.tar.gz" -O /tmp/pmtiles.tar.gz \
    && echo "$checksum  /tmp/pmtiles.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/pmtiles.tar.gz -C /usr/local/bin pmtiles && rm /tmp/pmtiles.tar.gz
RUN python3 -m venv --system-site-packages /opt/terrain \
    && /opt/terrain/bin/pip install --no-cache-dir pmtiles==3.8.1
ENV PATH="/opt/terrain/bin:$PATH"
COPY docker/graphhopper-entrypoint.sh entrypoint.sh
COPY docker/graphhopper-terrain.py terrain.py
COPY docker/graphhopper-artifact.py artifact.py
COPY docker/graphhopper-smoke.py smoke.py
COPY docker/graphhopper-filter-osm.sh filter-osm.sh
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
FROM docker.io/library/python:3.14-slim-bookworm AS python-base

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
