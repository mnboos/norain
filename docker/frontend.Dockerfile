FROM node:22-bookworm-slim AS build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./frontend/
COPY packages/api/ ./packages/api/
WORKDIR /app/frontend
RUN npm ci

COPY frontend/ ./
RUN npm run build-only

FROM caddy:2.10-alpine

COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/frontend/dist /srv
