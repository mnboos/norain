# Build routing and search from downloaded files

Use this guide to cover several countries at once (by default Germany, Austria,
Switzerland, the Netherlands, Belgium and Denmark), or to build from files you have
already downloaded. You end up with one routing graph and one search index that
cover all of them.

For a single region that GraphHopper and Photon can download themselves, the shorter
[change the geographic coverage](change-region.md) is enough.

You need the local setup from [development](development.md), a few tens of GB of free
disk space, and enough memory for the graph build (see step 2).

## 1. Download the files

From the repository root:

```bash
just download-pbf            # OSM extracts from Geofabrik -> data/downloads/osm/
just download-photon-dumps   # Photon dumps from GraphHopper -> data/downloads/photon/
```

The countries are listed at the top of `scripts/download-pbf.sh` and
`scripts/download-photon-dumps.sh`. Keep the two lists the same, or routing and search
cover different areas.

The files are kept. Running a download again only fetches files the server has updated
since, so it is also how you get fresh data later. `data/downloads/` is not committed.

## 2. Set `.env`

```bash
# The extracts are filtered for bikes and merged into this one file; every graph build reads it.
ROUTING_OSM_FILE_FILTERED=bike-europe-cycling.osm.pbf

# Building needs far more memory than serving. DACH alone needs a 16–24 GB heap;
# more countries need more. Leave room for the JVM outside the heap.
GRAPHHOPPER_BUILD_HEAP=24g
GRAPHHOPPER_MEM_LIMIT=28g

# Serving uses MMAP (the default): the graph is paged in from disk, so the default
# GRAPHHOPPER_HEAP is enough however many countries it covers.

# Raise it if the Photon import stops with an OutOfMemoryError.
PHOTON_IMPORT_HEAP=8g
```

The numbers are a starting point, not measured values. Raise them if a step runs out of
memory.

Why `ROUTING_OSM_FILE_FILTERED`: every graph build reads
`<ROUTING_OSM_IMPORT_DIR>/<ROUTING_OSM_FILE_FILTERED>`. That includes a rebuild
after a speed change and a fresh start with an empty graph cache. `osm-filter-many-raw-pbf-into-one` writes that
file, so later rebuilds use the same data. `OSM_DATA_URL` only names an extract to download,
which no merged set of countries has; a filtered file with any other name than
`bike-<its file name>` is never made from it. `osm-filter-many-raw-pbf-into-one` refuses to run without
`ROUTING_OSM_FILE_FILTERED`. The journey planner's POIs go into the matching
`pois-europe-cycling.geojsonseq`.

## 3. Build the routing graph

```bash
just osm-filter-many-raw-pbf-into-one data/downloads/osm/*.osm.pbf
just build-graphhopper-graph-from bike-europe-cycling.osm.pbf
```

The first filters each extract down to what the bike profiles use (see
[what the filter keeps](#what-the-bike-filter-keeps)) and merges the results into
`bike-europe-cycling.osm.pbf` in `ROUTING_OSM_IMPORT_DIR` (`data/graphhopper/osm`), plus the
POIs into `pois-europe-cycling.geojsonseq`. It builds no graph. The second deletes the current
graph (the elevation tiles are kept), builds the new one from that file and starts GraphHopper
again.

Routing is down until the build is done. That takes a long time for this many countries.
Follow it with:

```bash
podman compose -f docker-compose.dev.yml logs -f graphhopper   # or docker compose
```

The filter runs before anything is deleted, so if a file is broken, the old graph stays.

## 4. Build the search index

```bash
just photon-import data/downloads/photon/*.jsonl.zst
```

This asks for confirmation, stops Photon, imports all dumps into **one** index and
starts Photon again. Photon's own import reads only one file and wipes the index
each time. So the dumps are joined into one stream. They all start with the same header
and country list, which are kept once.

The new index is built next to the old one (`data/photon/`). The old one is replaced
only when the import worked. Search is down while Photon is stopped.

Steps 3 and 4 don't depend on each other. Run them in either order.

## 5. Check the result

- Search for a town in each country, and plan a short route in each one, including one
  that crosses a border.
- `just routing-speeds` should show the same speeds as before for the Swiss reference
  routes. The filter doesn't change how routes are chosen.
- GraphHopper serves the graph with MMAP, so its size doesn't need to fit the heap. The
  first queries after a start are slower while the OS reads it in. If routing stays slow,
  give the container more memory (`GRAPHHOPPER_MEM_LIMIT`): what the heap doesn't use
  caches the graph file.

Then refresh what depended on the old graph:

- `just routing-refresh-routes` re-routes every saved route (needs a worker on the
  `default` queue). Otherwise saved routes keep their old geometry and arrival times.
- Restart the backend and the workers to clear their in-memory route caches.

## Update the data later

```bash
just download-pbf
just download-photon-dumps
just osm-filter-many-raw-pbf-into-one data/downloads/osm/*.osm.pbf
just build-graphhopper-graph-from bike-europe-cycling.osm.pbf
just photon-import data/downloads/photon/*.jsonl.zst
```

After a change to `data/graphhopper/graphhopper-config.yaml` or the models,
`just build-graphhopper-graph-from bike-europe-cycling.osm.pbf` is enough. It rebuilds from the filtered file that
is already there.

## Use a single file

Both recipes also take a single file. `osm-filter-many-raw-pbf-into-one` writes it, filtered, to
`ROUTING_OSM_FILE_FILTERED` as well, for example `ROUTING_OSM_FILE_FILTERED=bike-switzerland.osm.pbf`
for `just osm-filter-many-raw-pbf-into-one ~/Downloads/switzerland-latest.osm.pbf`.

`photon-import` also takes a prebuilt `.tar.bz2` index, but only on its own: an index
can't be combined with other files.

## What the bike filter keeps

`docker/graphhopper-filter-osm.sh` is the only place the filter rules live. The graph
build runs it every time, and so does `osm-filter-many-raw-pbf-into-one`. It keeps:

- every highway except motorways and roads that are only planned or are gone;
- ferries and piers;
- cycle-route relations, which the bike profile uses to prefer marked routes;
- every node those ways use, with its tags (so barriers stay), and the members of the
  kept relations.

Buildings, land use, addresses, points of interest, rail and turn restrictions are
dropped. For Switzerland this cuts the file from 546 MB to 178 MB, and the reference
routes of `just routing-speeds` came out at the same speeds as with the full file. If a
profile ever needs a tag the filter drops, add it to that script and rebuild.

## Production

These recipes work on the development services. For the VPS:

- **Routing graph:** build it here as above, then copy `data/graphhopper/cache` over as in
  [build the routing graph elsewhere](build-routing-graph.md#2-copy-it-to-the-vps).
  Or copy the filtered file into the VPS's `ROUTING_OSM_IMPORT_DIR` and run
  `just build-graphhopper-graph-from` with it there.
- **Search index:** copy the dumps into `${APP_STORAGE_PATH}/photon/` on the VPS and
  import them as in [deploy on a VPS](deploy-vps.md), listing all of them in
  `PHOTON_INDEX_FILE` separated by spaces and adding `-e PHOTON_REPLACE_INDEX=true`.
  You can also copy a finished `data/photon/photon_data` directory, with Photon stopped
  on both machines.

## Notes

- The recipes use `docker`, or `podman` when there is no `docker` program. A shell alias
  doesn't count; set `CONTAINER_ENGINE` to choose.
- The import recipes are bash scripts. They are not tested on Windows.
- Weather requests use `Europe/Zurich`. All the default countries share that time zone.
  A region in another one needs code changes, see
  [forecast interpretation](../explanation/forecasts.md).

[Documentation index](../README.md)
