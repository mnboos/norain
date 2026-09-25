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

## 2. Build the routing graph

Follow [path B of the routing-graph guide](build-routing-graph.md#2-prepare-osm).
In short:

```bash
# in .env: ROUTING_OSM_FILE_FILTERED=bike-europe-cycling.osm.pbf, and enough memory
just osm-filter-many-raw-pbf-into-one data/downloads/osm/*.osm.pbf
just routing-terrain-estimate bike-europe-cycling.osm.pbf
just download-elevation-for bike-europe-cycling.osm.pbf
just build-graphhopper-graph-from bike-europe-cycling.osm.pbf
just poi-import-into-db
```

The commands filter and merge the OSM files, prepare matching terrain, import a candidate
graph and load POIs. Validate and activate the candidate using the routing-graph guide
before serving it.

## 3. Build the search index

In `.env`, raise the import memory if the Photon import stops with an `OutOfMemoryError`:

```bash
PHOTON_IMPORT_HEAP=8g
```

```bash
just photon-import data/downloads/photon/*.jsonl.zst
```

This asks for confirmation, stops Photon, imports all dumps into **one** index and
starts Photon again. Photon's own import reads only one file and wipes the index
each time. So the dumps are joined into one stream. They all start with the same header
and country list, which are kept once.

The new index is built next to the old one (`data/photon/`). The old one is replaced
only when the import worked. Search is down while Photon is stopped.

Steps 2 and 3 don't depend on each other. Run them in either order.

## 4. Check the result

- Search for a town in each country, and plan a short route in each one, including one
  that crosses a border.
- `just routing-speeds` should show the same speeds as before for the Swiss reference
  routes. The filter doesn't change how routes are chosen.
- GraphHopper serves the graph with MMAP, so its size doesn't need to fit the heap. The
  first queries after a start are slower while the OS reads it in. If routing stays slow,
  give the container more memory (`GRAPHHOPPER_MEM_LIMIT`): what the heap doesn't use
  caches the graph file.

Then do [after every build](build-routing-graph.md#5-validate-activate-and-check).

## Update the data later

```bash
just download-pbf
just download-photon-dumps
just osm-filter-many-raw-pbf-into-one data/downloads/osm/*.osm.pbf
just routing-terrain-estimate bike-europe-cycling.osm.pbf
just download-elevation-for bike-europe-cycling.osm.pbf
just build-graphhopper-graph-from bike-europe-cycling.osm.pbf
just photon-import data/downloads/photon/*.jsonl.zst
```

After a change to `data/graphhopper/graphhopper-config.yaml` or the models, only the build
is needed: see [build again after changing the config](build-routing-graph.md#4-import-without-interrupting-routing).

## Use a single file

Both recipes also take a single file. `osm-filter-many-raw-pbf-into-one` then filters that
one file into `ROUTING_OSM_FILE_FILTERED`, for example `ROUTING_OSM_FILE_FILTERED=bike-switzerland.osm.pbf`
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

- **Routing graph:** build it here and copy the graph over
  ([path D](build-routing-graph.md#4-import-without-interrupting-routing)), or copy the
  filtered file to the VPS and build it there ([path C](build-routing-graph.md#4-import-without-interrupting-routing)).
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
