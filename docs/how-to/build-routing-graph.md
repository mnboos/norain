# Build and deploy the routing graph

NoRain builds **GraphHopper 12.0-SNAPSHOT** from commit
`d9506cd7d36d5d068d9118b19b86cf0609dbe773` with Java 25. Its native PMTiles
provider reads **Mapterhorn zoom 15**, using bilinear interpolation. Photon keeps
its own Java runtime. Both amd64 and arm64 images are built from the same Java source.

There are three separate artifacts:

| Artifact | Container location | Purpose |
| --- | --- | --- |
| Filtered OSM | `/osm_data/bike-*.osm.pbf` | Roads and bike-route relations |
| Terrain | `/osm_data/elevation/<manifest-hash>/` | Verified PMTiles, attribution, and reusable decoded cache |
| Routing graph | `/graph-cache/releases/<id>/` | Graph, configuration snapshot, models, and build identity |

`/graph-cache/current` selects the active graph. `candidate` selects the newest
successful import; `previous` retains the last activated managed graph. Graph
builds never stop the running service or delete its graph. Startup only loads an
existing graph; it never downloads OSM/terrain or starts an import.

The elevation chart and height enrichment of previously saved routes are separate
application work. This upgrade does not rewrite saved routes. New routing results
may differ because the engine and terrain have changed.

## 1. Build or select the image

Locally:

```sh
docker compose build graphhopper
```

In production, set `GRAPHHOPPER_IMAGE` to the immutable image tag published by CI
and pull it. Use **that same image** for the import, validation and serving. Each
graph records the Java revision and jar checksum; a different source revision is rejected
before GraphHopper opens it. Config/model snapshots prevent a later checkout from
silently changing the configuration of a prepared graph.

A generic application release checks the active graph against the selected image
before replacing services. An engine upgrade therefore needs the graph workflow
below before the application release.

## 2. Prepare OSM

`ROUTING_OSM_IMPORT_DIR` is the host folder mounted at `/osm_data`; default:
`./data/graphhopper/osm`. Production normally uses a persistent folder such as
`/srv/norain-data/graphhopper/osm`.

For custom country combinations, download the raw extracts and run:

```sh
just osm-filter-many-raw-pbf-into-one /path/to/germany-latest.osm.pbf /path/to/austria-latest.osm.pbf
```

Set `ROUTING_OSM_FILE_FILTERED` before that command. It also produces the matching
POI file. Use the exact same filtered file for terrain preparation and graph import.
For the default `bike-switzerland-latest.osm.pbf`, the terrain command can download
and filter `OSM_DATA_URL` when the file is missing. The dry run does not download
terrain, but can still perform this OSM preparation.

## 3. Estimate and prepare terrain

```sh
just routing-terrain-estimate bike-switzerland-latest.osm.pbf
just routing-terrain-from bike-switzerland-latest.osm.pbf
```

Bounds come from the actual OSM nodes, expanded by one complete zoom-15 tile on
all sides. The helper reads Mapterhorn's archive catalog, extracts only zoom 15
from intersecting regional archives, then merges them. The planet archive alone
only contains zooms 0–12 and cannot provide this resolution.

The estimate prints transfer/archive sizes before downloading terrain. Allow
additional space for temporary extracts, the merged archive, decoded terrain
cache, the new graph and the retained previous graph. Zoom 15 over a large region
can require substantial disk space and import time; start with a small extract.

Preparation verifies PMTiles structure, WebP decoding and absence of terrain voids.
If Mapterhorn has no tile for part of the requested area, preparation continues and
prints a warning; GraphHopper returns a height gap for routes crossing that tile and
the charts leave that segment blank. There is no silent low-resolution fallback. A
higher zoom cannot improve the accuracy of the original survey. At Swiss latitudes
zoom 15 corresponds to roughly 1.6 m pixels.

Downloads are staged and only published when complete. The manifest records source
URLs and source checksums, bounds, zoom, and the completed extract's SHA-256. A
matching completed extract is reused after checksum verification. Interrupted
preparation leaves the prior `elevation/current` untouched and preserves completed
source extracts in `.prepare-<hash>/` for the next attempt, so they are not
downloaded again.

Mapterhorn attribution is retained as `attribution.json` alongside the archive;
see [Mapterhorn attribution](https://mapterhorn.com/attribution/) and
[data access](https://mapterhorn.com/data-access/).

## 4. Import without interrupting routing

```sh
just build-graphhopper-graph-from bike-switzerland-latest.osm.pbf
```

This checks terrain integrity and matching OSM bounds first, then imports into a
new release directory. A failed import never changes `current` or `candidate`.
Only one import runs at a time. Failed release directories are retained for
inspection and can be removed once no import uses them.

`GRAPHHOPPER_BUILD_HEAP` defaults to `GRAPHHOPPER_HEAP` (6g).
`GRAPHHOPPER_BUILD_DATAACCESS=RAM_STORE` uses heap; `MMAP` trades speed for a
smaller heap. Serving defaults to `GRAPHHOPPER_DATAACCESS=MMAP`. Leave enough RAM
for the running graph plus the import, or build on another machine.

To build elsewhere, use the same image and copy the **entire release directory**
to the serving machine under `graphhopper/cache/releases/`, then set `candidate`
to that relative release path. Keep the matching terrain release at the same path on the serving machine: the internal `/elevation` endpoint uses GraphHopper’s native provider to enrich existing saved paths without rerouting them. Ordinary routing loads elevations from the graph. Neither endpoint downloads terrain at request time.

## 5. Validate, activate, and check

Choose two non-sea-level points inside the imported graph, in `[longitude, latitude]`
order. For a Switzerland graph:

```sh
just routing-validate-candidate '[[9.5329,46.8499],[9.6800,46.7833]]'
just routing-activate
just routing-speeds
```

Validation starts an isolated container with no published ports. It checks bike,
ebike and fast_ebike, finite 3D elevation, 2D compatibility, time details, via
points, alternative routing and the custom-model expressions used for road and
wind preferences. Only a passing artifact can be activated. The container is
removed after testing.

Activation briefly stops GraphHopper, switches the current symlink, then recreates
its service. Check `docker compose logs graphhopper` and `/info` after activation.
Review representative flat, mountainous, bridge and tunnel routes as well as the
reference speed output; a successful HTTP response alone does not establish survey
accuracy. Startup should load the graph without terrain downloads or reimporting.

The commands never enqueue `routing-refresh-routes`: that command recalculates
saved geometry and is not part of this migration.

## Rollback

Before activating, record the old immutable image ID:

```sh
docker inspect --format '{{.Image}}' "$(docker compose ps -q graphhopper)"
```

For managed graphs created by this workflow:

```sh
just routing-rollback sha256:THE_PREVIOUS_IMAGE_ID
```

This verifies the previous artifact against that image before stopping the service,
then restores its graph/configuration and recreates the service. Persist the image
selection in the deployment environment so the next release uses the intended
engine. Keep both images and release directories until the new deployment is verified.

### First migration from the legacy 10.2 graph

Before updating the checkout, retain a copy of its configuration and models, record
the old image ID and keep the old checkout/release. The new workflow leaves the
legacy graph files directly under `/graph-cache` untouched. Rollback to 10.2 uses
the **old checkout/configuration and old image**, which still read those root files;
it does not use the new `routing-rollback` command. Do not prune the old graph or
image until validation is complete. Never load a 10.2 graph with the 12 image.

## Tests

The terrain tests require `pmtiles==3.8.1` and Pillow:

```sh
python -m unittest discover -s docker/tests -v
cd backend && python manage.py test core
```

The container already includes both terrain dependencies. To run the unit tests
there, mount `docker/` at a separate test path and run unittest discovery there.
A real small-area import and `routing-validate-candidate` additionally exercise the
native WebP decoder and the Java routing engine; run these for both supported
architectures when changing the source pin or runtime.
