# Build the routing graph elsewhere and ship it to the VPS

The production VPS only *serves* GraphHopper. Importing an OSM extract and preparing
the three bike profiles needs far more memory than serving the result, and on the VPS
it would compete with PostgreSQL, the workers and Photon. So the graph is built on
another machine and the finished `graph-cache` directory is copied over. The production
container refuses to import: with an empty cache it exits with an error pointing here.

## What must match

GraphHopper stores its profiles, encoded values and CH preparations with the graph and
refuses to load a graph built with a different configuration. Build with:

- the same image, or at least the same GraphHopper jar version (pinned in
  the `graphhopper` stage of `Dockerfile`), and
- the same `data/graphhopper/graphhopper-config.yaml` and `data/graphhopper/models/`
  as the commit deployed on the VPS.

CPU architecture does not matter: a graph built on an amd64 machine loads on the arm64 VPS.

That is also when you need this page: a change to a ride speed, or to any other rule in
`data/graphhopper/models/`, only takes effect through a new graph. Editing the files on the
VPS alone does nothing — the running server keeps the weights baked into its graph, and
after a restart it refuses to load it at all. The ride speeds themselves are described in
[configuration](../reference/configuration.md#ride-speed).

For a graph you only want locally — after a speed change, say — `just routing-import` does
step 1 and starts the server again, with the heaps from `.env`. The rest of this page is
the production route.

## 1. Import on the build machine

From the repository root, with `OSM_DATA_URL` set to the extract you want (the default
is Switzerland; Geofabrik's combined `europe/dach-latest.osm.pbf` covers DACH):

```bash
docker compose -f docker-compose.dev.yml stop graphhopper
mv data/graphhopper/cache data/graphhopper/cache.old && mkdir data/graphhopper/cache
GRAPHHOPPER_IMPORT_ONLY=true GRAPHHOPPER_IMPORT_HEAP=16g GRAPHHOPPER_MEM_LIMIT=20g \
  docker compose -f docker-compose.dev.yml run --rm graphhopper
```

Switzerland imports in a few GB of heap; DACH with three CH profiles needs roughly
16–24 GB. The container exits with `Import finished` when the graph is ready. The
downloaded extract and the elevation tiles stay in `data/graphhopper/osm/` and are not
needed on the VPS.

Check the size — with `GRAPHHOPPER_DATAACCESS=RAM_STORE` it is roughly the heap the
server needs:

```bash
du -sh data/graphhopper/cache
```

## 2. Copy it to the VPS

```bash
rsync -a --partial --delete data/graphhopper/cache/ \
  norain@VPS:/srv/norain-data/graphhopper/cache.new/
```

## 3. Swap it in

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

Size the server in `.env` first: `GRAPHHOPPER_HEAP` about 2 GB above the graph size and
`GRAPHHOPPER_MEM_LIMIT` about 1–2 GB above the heap. If the graph is larger than the
heap you can afford, set `GRAPHHOPPER_DATAACCESS=MMAP` and a 3 GB heap instead: the graph
is then paged in from disk, with slower first queries.

Once `/info` answers, delete `cache.old`.

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
