# Build the routing graph

NoRain plans bike routes with GraphHopper. GraphHopper needs a **routing graph**: a
prepared copy of the map that it can search quickly. This guide explains how to make one,
step by step.

**The graph is never built automatically.** When GraphHopper starts without a graph, it
stops at once with this message:

```
No graph in /graph-cache. The container never builds one by itself. Build it with:
  just build-graphhopper-graph-from <bike-filtered .osm.pbf in ROUTING_OSM_IMPORT_DIR>
```

You always build it yourself, with one command. That is true on your own computer and on
the server (the VPS).

## Contents

- [The words used in this guide](#the-words-used-in-this-guide)
- [Before you start: `.env`](#before-you-start-env)
- [Path A: one country, downloaded for you](#path-a-one-country-downloaded-for-you)
- [Path B: several countries merged into one](#path-b-several-countries-merged-into-one)
- [Path C: build on the VPS](#path-c-build-on-the-vps)
- [Path D: build on another computer and copy it to the VPS](#path-d-build-on-another-computer-and-copy-it-to-the-vps)
- [After every build](#after-every-build)
- [Build again after changing the config or a speed](#build-again-after-changing-the-config-or-a-speed)
- [When something goes wrong](#when-something-goes-wrong)
- [What happens inside the build command](#what-happens-inside-the-build-command)

## The words used in this guide

| Word | What it is | Example |
| --- | --- | --- |
| **Raw OSM file** | A map file as it comes from [Geofabrik](https://download.geofabrik.de/), with everything in it: roads, buildings, shops, rivers. | `switzerland-latest.osm.pbf` |
| **Filtered OSM file** | The same map with only what bikes need (roads, paths, ferries, cycle routes). About a third of the size. Its name starts with `bike-`. **The graph is always built from a filtered file.** | `bike-europe-cycling.osm.pbf` |
| **Import folder** | The folder on your computer (or the VPS) where the OSM files live. Set by `ROUTING_OSM_IMPORT_DIR` in `.env`. Inside the container it is called `/osm_data`. | `./data/graphhopper/osm` |
| **Graph folder** | Where the finished graph is stored. GraphHopper serves from here. | `data/graphhopper/cache` (dev), `/srv/norain-data/graphhopper/cache` (VPS) |

There are only two commands:

| Command | What it does | Makes a graph? |
| --- | --- | --- |
| `just osm-filter-many-raw-pbf-into-one FILE…` | Takes one or more **raw** files, filters them and merges them into **one filtered file** in the import folder. It also writes the POI file for the journey planner. | No |
| `just build-graphhopper-graph-from FILE` | Takes **one filtered file** from the import folder and builds the graph from it. Deletes the old graph first. | Yes |

## Before you start: `.env`

All commands read `.env` in the repository folder. Check these lines:

```bash
# Which docker-compose file to use: docker-compose.dev.yml on your computer,
# docker-compose.prod.yml on the VPS.
COMPOSE_FILE=docker-compose.dev.yml

# The import folder: where the OSM files are. Required.
ROUTING_OSM_IMPORT_DIR=./data/graphhopper/osm

# A raw file to download (only used by path A).
OSM_DATA_URL=https://download.geofabrik.de/europe/switzerland-latest.osm.pbf

# The name of the filtered file that osm-filter-many-raw-pbf-into-one writes (only path B).
ROUTING_OSM_FILE_FILTERED=bike-europe-cycling.osm.pbf

# Memory for the build. See "How much memory" below.
GRAPHHOPPER_BUILD_HEAP=6g
GRAPHHOPPER_MEM_LIMIT=8g
GRAPHHOPPER_BUILD_DATAACCESS=RAM_STORE
```

### How much memory

The build needs much more memory than running the server. Two settings matter:

- `GRAPHHOPPER_BUILD_HEAP` is the memory Java may use for the build.
- `GRAPHHOPPER_MEM_LIMIT` is the most the whole container may use. It must be **bigger**
  than the heap: Java needs some memory outside the heap too. If the container reaches this
  limit, the system kills the build (you see `Killed` and `exit code 137`).

`GRAPHHOPPER_BUILD_DATAACCESS` says where the graph is kept while it is built:

- `RAM_STORE` (the default): all in memory. Fast, but needs a lot of memory.
- `MMAP`: in files on disk. Slower (hours for a big area), but needs far less memory.
  Use it when the computer does not have enough memory for `RAM_STORE`.

Starting points (not measured exactly; raise them if the build runs out of memory):

| Area | `DATAACCESS` | `BUILD_HEAP` | `MEM_LIMIT` |
| --- | --- | --- | --- |
| Switzerland | `RAM_STORE` | `6g` | `8g` |
| Germany + Austria + Switzerland | `RAM_STORE` | `16g`–`24g` | `20g`–`28g` |
| Many European countries, on a 64 GB computer | `RAM_STORE` | `40g` | `48g` |
| Many European countries, on a 24 GB VPS | `MMAP` | `12g` | `20g` |

## Path A: one country, downloaded for you

Use this for a quick start on your own computer. The build downloads the raw file from
`OSM_DATA_URL` and filters it for you.

1. Set `OSM_DATA_URL` in `.env` to the Geofabrik file you want. The default is Switzerland.
2. Build the GraphHopper image, so it has the newest scripts:

   ```bash
   docker compose build graphhopper
   ```

3. Build the graph. The file name is `bike-` plus the file name at the end of
   `OSM_DATA_URL`:

   ```bash
   just build-graphhopper-graph-from bike-switzerland-latest.osm.pbf
   ```

   Type `y` when it asks. You see the download, then the filter, then the build. For
   Switzerland this takes a few minutes. It ends with `Build finished`, and GraphHopper
   starts again.
4. Do the checks in [after every build](#after-every-build).

The raw and the filtered file stay in the import folder. The next build uses the filtered
file again and downloads nothing. To get fresh map data, delete both files from the
import folder and build again.

## Path B: several countries merged into one

Use this to cover more than one country (for example Germany, Austria, Switzerland, the
Netherlands, Belgium and Denmark).

1. Download the raw files:

   ```bash
   just download-pbf
   ```

   They go into `data/downloads/osm/`. The list of countries is at the top of
   `scripts/download-pbf.sh`. Running it again later only downloads what has changed.
2. In `.env`, choose the name of the merged filtered file, and set the memory for your
   computer (see [how much memory](#how-much-memory)):

   ```bash
   ROUTING_OSM_FILE_FILTERED=bike-europe-cycling.osm.pbf
   ```

3. Build the GraphHopper image:

   ```bash
   docker compose build graphhopper
   ```

4. Filter and merge the raw files into one filtered file:

   ```bash
   just osm-filter-many-raw-pbf-into-one data/downloads/osm/*.osm.pbf
   ```

   Type `y` when it asks. When it is done, the import folder has
   `bike-europe-cycling.osm.pbf` and `pois-europe-cycling.geojsonseq`. **No graph is built
   yet**, and GraphHopper keeps running with the old graph.
5. Build the graph from that file:

   ```bash
   just build-graphhopper-graph-from bike-europe-cycling.osm.pbf
   ```

   Type `y` when it asks. Routing does not work until the build is done. It ends with
   `Build finished`, and GraphHopper starts again.
6. Load the journey planner's POIs, which step 4 extracted from the same files:

   ```bash
   just poi-import
   ```

   On another machine, such as the VPS, copy `pois-europe-cycling.geojsonseq` into its import
   folder too, or remake it there from the raw files:
   `just poi-extract data/downloads/osm/*.osm.pbf`.

7. Do the checks in [after every build](#after-every-build).

Search (Photon) is separate. See [build routing and search from downloaded
files](import-geodata.md) for the search index.

## Path C: build on the VPS

Use this when the filtered file is already on the VPS, or when the VPS should download it
(path A's file name). Run everything on the VPS, in the repository folder.

1. **Get the newest code.**

   ```bash
   cd ~/src/norain        # or /srv/norain: wherever the repository is
   git pull
   ```

2. **Check `.env`.** It must say:

   ```bash
   COMPOSE_FILE=docker-compose.prod.yml
   ROUTING_OSM_IMPORT_DIR=/srv/norain-data/graphhopper/osm   # the folder with your .osm.pbf files
   ```

   For a big area on a VPS with 24 GB, also set:

   ```bash
   GRAPHHOPPER_BUILD_DATAACCESS=MMAP
   GRAPHHOPPER_BUILD_HEAP=12g
   GRAPHHOPPER_MEM_LIMIT=20g
   ```

3. **Check that the filtered file is in the import folder.** Use the folder from
   `ROUTING_OSM_IMPORT_DIR`:

   ```bash
   ls -lh /srv/norain-data/graphhopper/osm/
   ```

   You should see your `bike-….osm.pbf`. If it is not there, copy it from the computer that
   made it (path B, step 4):

   ```bash
   # run this on the computer that has the file
   rsync -a --partial --progress data/graphhopper/osm/bike-europe-cycling.osm.pbf \
     USER@VPS:/srv/norain-data/graphhopper/osm/
   ```

   If you see a file that starts with `bike-bike-`, delete it: it was filtered twice by
   mistake.
4. **Build the GraphHopper image**, so it has the newest scripts:

   ```bash
   docker compose build graphhopper
   ```

5. **Stop everything**, so the build gets all the memory. The site is offline from here
   until step 8.

   ```bash
   docker compose stop
   ```

6. **Start a `tmux` session.** A big build takes hours. Inside `tmux` it keeps running if
   your SSH connection drops.

   ```bash
   tmux new -s graph
   ```

   To leave it running and log out: press `Ctrl-b`, then `d`. To come back later:
   `tmux attach -t graph`.
7. **Build the graph:**

   ```bash
   just build-graphhopper-graph-from bike-europe-cycling.osm.pbf
   ```

   Type `y` when it asks. The log shows:

   ```
   Importing from /osm_data (ROUTING_OSM_IMPORT_DIR on the host: /srv/norain-data/graphhopper/osm)
   Building the graph from /osm_data/bike-europe-cycling.osm.pbf with a 12g heap (MMAP)
   ```

   Check that the file name and `MMAP` are what you expect. Then wait until you see
   `Build finished`. If it stops with an error, see [when something goes
   wrong](#when-something-goes-wrong).
8. **Start everything again:**

   ```bash
   docker compose up -d
   ```

9. Do the checks in [after every build](#after-every-build).

## Path D: build on another computer and copy it to the VPS

Use this when the VPS does not have enough memory, or to keep the site online during a
long build. A computer with 64 GB builds much faster than a VPS with `MMAP`.

**The graph must be built with the same setup as the VPS**, or GraphHopper refuses to load
it. Use the same commit (the same `data/graphhopper/graphhopper-config.yaml`, the same files
in `data/graphhopper/models/` and the same GraphHopper version in `Dockerfile`). The type of
processor does not matter: a graph built on a normal PC works on the ARM VPS.

1. **On your computer**, check out the commit that runs on the VPS, then build the graph
   with path A or path B. With a lot of memory, set it in `.env` first, for example:

   ```bash
   GRAPHHOPPER_BUILD_DATAACCESS=RAM_STORE
   GRAPHHOPPER_BUILD_HEAP=40g
   GRAPHHOPPER_MEM_LIMIT=48g
   ```

2. **Check the size** of the graph:

   ```bash
   du -sh data/graphhopper/cache
   ```

3. **Copy it to the VPS**, into a new folder next to the old one:

   ```bash
   rsync -a --partial --delete --progress data/graphhopper/cache/ \
     USER@VPS:/srv/norain-data/graphhopper/cache.new/
   ```

4. **On the VPS**, swap the new graph in:

   ```bash
   cd ~/src/norain
   docker compose stop graphhopper
   cd /srv/norain-data/graphhopper
   [ -d cache ] && mv cache cache.old
   mv cache.new cache
   cd ~/src/norain
   docker compose up -d graphhopper
   docker compose logs -f graphhopper      # wait for "Serving /graph-cache", then Ctrl-C
   ```

5. Do the checks in [after every build](#after-every-build). When everything works, delete
   the old graph: `rm -rf /srv/norain-data/graphhopper/cache.old`.

## After every build

1. **Check that GraphHopper runs.**

   ```bash
   docker compose ps graphhopper
   ```

   After a few minutes the status should say `healthy` (on the VPS) or `running`. On your
   own computer you can also open <http://localhost:8989/info>.
2. **Restart the backend and the workers**, so they forget old routes they kept in memory:

   ```bash
   docker compose restart
   ```

   (On your own computer, restart `just server` and your workers instead.)
3. **Recalculate all saved routes.** Saved routes keep their old line and times until this
   runs. It needs a worker on the `default` queue.

   ```bash
   just routing-refresh-routes
   ```

   On the VPS, if `uv` is not installed there, run it in a container instead:

   ```bash
   docker compose run --rm --no-deps worker-default python manage.py shell -c "from core.models import RecurringRoute; from core.tasks import refresh_route_geometry; print(sum(refresh_route_geometry.enqueue(str(i)) is not None for i in RecurringRoute.objects.values_list('id', flat=True)), 'routes queued')"
   ```

4. **Try it.** Plan a short route in each country you built, and one that crosses a border.
   Routes outside the new area no longer work.

## Build again after changing the config or a speed

The graph contains the rules from `data/graphhopper/graphhopper-config.yaml` and
`data/graphhopper/models/` (the ride speeds live there, see
[configuration](../reference/configuration.md#ride-speed)). A change to them only works
after a new build. Changing the files alone does nothing, and after a restart GraphHopper
refuses the old graph.

Build again from the filtered file that is already there:

```bash
just build-graphhopper-graph-from bike-europe-cycling.osm.pbf
just routing-speeds          # shows the new speeds of each profile
```

Then do [after every build](#after-every-build).

## When something goes wrong

| What you see | What it means | What to do |
| --- | --- | --- |
| `No graph in /graph-cache. The container never builds one by itself.` | GraphHopper has no graph yet, or the graph folder was emptied. It restarts again and again with this message. | Build one ([path A](#path-a-one-country-downloaded-for-you), [B](#path-b-several-countries-merged-into-one), [C](#path-c-build-on-the-vps)) or copy one ([path D](#path-d-build-on-another-computer-and-copy-it-to-the-vps)). |
| `error: … is not in ROUTING_OSM_IMPORT_DIR (…)` | The file you named is not in the import folder. Nothing was changed. | Check the name with `ls`, and check `ROUTING_OSM_IMPORT_DIR` in `.env`. |
| `Killed`, and `exit code 137` | The container used more memory than `GRAPHHOPPER_MEM_LIMIT`, and the system stopped it. | Raise `GRAPHHOPPER_MEM_LIMIT`. If the computer has no more memory, use `GRAPHHOPPER_BUILD_DATAACCESS=MMAP`, or build on a bigger computer (path D). |
| `java.lang.OutOfMemoryError` | Java used all of `GRAPHHOPPER_BUILD_HEAP`. | Raise `GRAPHHOPPER_BUILD_HEAP`. Keep `GRAPHHOPPER_MEM_LIMIT` a few GB above it. |
| `ROUTING_OSM_IMPORT_DIR must be set` | `.env` has no import folder. | Add `ROUTING_OSM_IMPORT_DIR=…` to `.env`. |
| `no configuration file provided` | `.env` has no `COMPOSE_FILE`. | Add `COMPOSE_FILE=docker-compose.dev.yml` (or `docker-compose.prod.yml` on the VPS). |
| A file called `bike-bike-….osm.pbf` appears | A file that was already filtered was filtered again. | Delete the `bike-bike-…` file. Build from the `bike-…` file, and don't put a filtered file name into `OSM_DATA_URL`. |
| GraphHopper refuses to load a copied graph | It was built with a different config or GraphHopper version. | Build again with the same commit as the VPS (path D). |

## What happens inside the build command

`just build-graphhopper-graph-from FILE` does four things, in this order:

1. It checks that `FILE` is in the import folder, and stops if not. (One exception: for
   `bike-<file name of OSM_DATA_URL>` the file may be missing, because it will be
   downloaded.)
2. It stops GraphHopper and empties the graph folder.
3. It runs the GraphHopper container once with the command `build`. The container
   downloads and filters the raw file if needed (path A only), then builds the graph from
   `/osm_data/FILE` and exits.
4. It starts GraphHopper again, which now serves the new graph.

The filter rules are in `docker/graphhopper-filter-osm.sh`. See [what the bike filter
keeps](import-geodata.md#what-the-bike-filter-keeps).

[Documentation index](../README.md)
