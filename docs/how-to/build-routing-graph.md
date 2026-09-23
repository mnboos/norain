# Rebuild the routing graph, or build it elsewhere

GraphHopper builds its routing graph when `/graph-cache` is empty and then serves it. That
is true in development and on the production VPS alike (`GRAPHHOPPER_BUILD_GRAPH`, default
`true`). GraphHopper's own name for this step is "import".

## Rebuild on the VPS

On the VPS, from `/srv/norain`:

```bash
docker compose --env-file .env -f docker-compose.prod.yml stop graphhopper
rm -rf /srv/norain-data/graphhopper/cache
mkdir /srv/norain-data/graphhopper/cache
docker compose --env-file .env -f docker-compose.prod.yml up -d graphhopper
docker compose --env-file .env -f docker-compose.prod.yml logs -f graphhopper
```

The extract and elevation tiles in `graphhopper/osm` are reused. Delete the extract too if
you want fresh OSM data. The build heap is `GRAPHHOPPER_BUILD_HEAP`, and
`GRAPHHOPPER_MEM_LIMIT` must fit it plus JVM overhead: a build that stops with `Killed` and
exit code 137 hit that limit, not the heap. Routing fails until the build is done. Then do
[step 4](#4-refresh-what-depended-on-the-old-graph).

Don't use `just routing-build` on the VPS: it runs `docker-compose.dev.yml`, which builds into
the repository's `data/graphhopper/cache`, not `APP_STORAGE_PATH`.

### A large area on a small VPS

By default the build holds the whole graph in the heap (`RAM_STORE`), which for all of Europe
is far more than a 24 GB VPS has. `GRAPHHOPPER_BUILD_DATAACCESS=MMAP` builds it in files on
`graph-cache` instead; the heap then only holds the OSM reader's node map and the CH/LM
bookkeeping, and the page cache does the rest. It is slower, hours for Europe. In `.env`:

```bash
GRAPHHOPPER_BUILD_DATAACCESS=MMAP
GRAPHHOPPER_BUILD_HEAP=12g
GRAPHHOPPER_MEM_LIMIT=20g
```

Free the memory the other services hold while it runs, build once, then start everything:

```bash
docker compose --env-file .env -f docker-compose.prod.yml stop
rm -rf /srv/norain-data/graphhopper/cache/*
docker compose --env-file .env -f docker-compose.prod.yml run --rm -e GRAPHHOPPER_BUILD_ONLY=true graphhopper
docker compose --env-file .env -f docker-compose.prod.yml up -d
```

These numbers are a starting point, not measured values. A Java `OutOfMemoryError` means the
heap is too small; `Killed` means `GRAPHHOPPER_MEM_LIMIT` is. If the VPS cannot manage it,
[build elsewhere](#build-elsewhere-and-ship-it).

## Build elsewhere and ship it

Building needs far more memory than serving: DACH with three CH profiles needs a 16–24 GB
heap. If the VPS cannot spare that next to PostgreSQL, the workers and Photon, build the
graph on another machine and copy the finished `graph-cache` directory over.

### What must match

GraphHopper stores its profiles, encoded values and CH preparations with the graph and
refuses to load a graph built with a different configuration. Build with:

- the same image, or at least the same GraphHopper jar version (pinned in
  the `graphhopper` stage of `Dockerfile`), and
- the same `data/graphhopper/graphhopper-config.yaml` and `data/graphhopper/models/`
  as the commit deployed on the VPS.

CPU architecture does not matter: a graph built on an amd64 machine loads on the arm64 VPS.

The same goes for a rebuild on the VPS: a change to a ride speed, or to any other rule in
`data/graphhopper/models/`, only takes effect through a new graph. Editing the files on the
VPS alone does nothing — the running server keeps the weights baked into its graph, and
after a restart it refuses to load it at all. The ride speeds themselves are described in
[configuration](../reference/configuration.md#ride-speed).

For a graph you only want locally — after a speed change, say — `just routing-build` does
step 1 and starts the server again, with the heaps from `.env`.

### 1. Build on the build machine

From the repository root, with `OSM_DATA_URL` set to the extract you want (the default
is Switzerland; Geofabrik's combined `europe/dach-latest.osm.pbf` covers DACH):

```bash
docker compose -f docker-compose.dev.yml stop graphhopper
mv data/graphhopper/cache data/graphhopper/cache.old && mkdir data/graphhopper/cache
GRAPHHOPPER_BUILD_ONLY=true GRAPHHOPPER_BUILD_HEAP=16g GRAPHHOPPER_MEM_LIMIT=20g \
  docker compose -f docker-compose.dev.yml run --rm graphhopper
```

Switzerland builds in a few GB of heap; DACH with three CH profiles needs roughly
16–24 GB. The container exits with `Build finished` when the graph is ready. It builds
from a copy of the extract that osmium has cut down to what the bike profiles use
(`bike-<extract>`). The extract, that copy and the elevation tiles stay in
`data/graphhopper/osm/` and are not needed on the VPS. To build from files you already
have, or from several countries merged into one, use `just osm-import FILE…` as described in
[build routing and search from downloaded files](import-geodata.md).

Check the size. With the default `GRAPHHOPPER_DATAACCESS=MMAP` the server pages it in from
disk and a 3 GB heap is enough; with `RAM_STORE` the size is roughly the heap it needs:

```bash
du -sh data/graphhopper/cache
```

### 2. Copy it to the VPS

```bash
rsync -a --partial --delete data/graphhopper/cache/ \
  norain@VPS:/srv/norain-data/graphhopper/cache.new/
```

### 3. Swap it in

On the VPS, from `/srv/norain`:

```bash
docker compose --env-file .env -f docker-compose.prod.yml stop graphhopper
cd /srv/norain-data/graphhopper
[ -d cache ] && mv cache cache.old
mv cache.new cache
cd /srv/norain
docker compose --env-file .env -f docker-compose.prod.yml up -d graphhopper
docker compose --env-file .env -f docker-compose.prod.yml logs -f graphhopper
```

Size the server in `.env` first. With the default `GRAPHHOPPER_DATAACCESS=MMAP`, a 3 GB
`GRAPHHOPPER_HEAP` is enough: the graph is paged in from disk, so the first queries after a
start are slower. Memory above `GRAPHHOPPER_MEM_LIMIT`'s heap share goes to caching the graph
file, and more of it means fewer disk reads. With `RAM_STORE` instead, set `GRAPHHOPPER_HEAP`
about 2 GB above the graph size and `GRAPHHOPPER_MEM_LIMIT` about 1–2 GB above the heap.

Once `/info` answers, delete `cache.old`. To keep the VPS from ever building its own graph
(for example after someone empties the cache), set `GRAPHHOPPER_BUILD_GRAPH=false` in its
`.env`: an empty cache then stops the container with an error instead.

## 4. Refresh what depended on the old graph

Restart the backend and workers so their in-memory route caches are dropped, then
recompute saved route geometry — `just routing-refresh-routes`, or the loop it runs in the
[background-job guide](background-jobs.md). Routes outside a new coverage area can no
longer be routed.

After a speed change, `just routing-speeds` prints what each profile now rides, so you can
see the new graph is the one you meant to build.

Photon is separate: set `PHOTON_INDEX_URL` to a matching region, as described in
[changing the geographic coverage](change-region.md).

[Documentation index](../README.md)
