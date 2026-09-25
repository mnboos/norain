# Build and deploy the routing graph

NoRain builds **GraphHopper 12.0-SNAPSHOT** from commit
`d9506cd7d36d5d068d9118b19b86cf0609dbe773` with Java 25. Its native PMTiles
provider reads **Mapterhorn zoom 15**, using bilinear interpolation. Photon keeps
its own Java runtime. Both amd64 and arm64 images are built from the same Java source.

There are three separate artifacts:

| Artifact | Container location | Purpose |
| --- | --- | --- |
| Filtered OSM | `/osm_data/bike-*.osm.pbf` | Roads and bike-route relations |
| Terrain | `/osm_data/elevation/<manifest-hash>/` | Zoom-15 and zoom-12 fallback PMTiles, attribution, and reusable decoded caches |
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
just download-elevation-for bike-switzerland-latest.osm.pbf
```

The area is **where the file has roads**, not its bounding box. One pass over the
nodes (`osmium cat`) collects the zoom-11 cells that hold a node. A cell is 16×16
zoom-15 tiles, about 13 km at 47°N. Adjacent cells in a row are merged into
rectangles and written as `region.geojson`, and every extract uses
`pmtiles extract --region`. GraphHopper reads only the zoom-15 tile under each node,
so nothing outside the cells is ever used. Two distant countries fetch only
themselves (Liechtenstein + Andorra: 25 cells, about 780 MB), not the sea or the
countries between them. The helper reads Mapterhorn's archive catalog and writes two
archives:

- `terrain.pmtiles`: zoom 15 from the intersecting regional archives, merged.
  At Swiss latitudes that is about 1.6 m pixels.
- `fallback.pmtiles`: zoom 12 (about 13 m at 47°N) from the planet archive, which
  covers everywhere.

Zoom 15 has gaps: it is missing in central and southern Italy, the Balkans, Greece,
Iceland and east of about 28°E, and some tiles contain nodata pixels. Wherever zoom 15
has no value, GraphHopper reads the zoom-12 fallback (`FallbackElevationProvider`,
patched into the jar), in the import and in `/elevation` alike. Without the fallback it
would store 0 m for such a node, and the slope against the real heights beside it
would become a cliff that the speed rules take seriously. At the edge of zoom-15
coverage the two sources can differ by a few metres. Only where the fallback has no
value either does a node get 0 m.

The estimate prints transfer/archive sizes for both before downloading terrain.
Allow additional space for temporary extracts, the merged archive, decoded terrain
caches, the new graph and the retained previous graph.

The cells are kept as `cells.json`. Before an import, the check runs the same pass
over the file and requires its cells to be a subset of the prepared ones, so terrain
for more countries also serves a file with fewer. That is one extra pass per build,
minutes for a large file. Terrain prepared for a bounding box, before the cells
existed, is still compared by its bounds.

Preparation verifies the PMTiles structure. For up to 250,000 tile positions per
archive it also decodes every tile, and it records missing tiles and tiles with
nodata in the manifest's `coverage`: counts and a few examples, never an error.
Larger areas skip that per-tile pass. Terrain prepared before the fallback existed
has no `fallback.pmtiles` and still builds and serves as before. **Heights are baked
into the graph at import**, and slopes set speeds and arrival times. Better terrain
therefore takes a new terrain preparation and a new graph.

Downloads are staged and only published when complete. The manifest records source
URLs and source checksums, bounds, zoom, and the completed extracts' SHA-256. A
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

A different filtered file needs its own terrain: run `download-elevation-for` for it
before the import, or the check refuses the build.

### Build elsewhere, serve in production

A graph can be built on a larger machine (steps 2–4 there) and copied over. The serving
machine accepts it when:

- the image has the same GraphHopper revision. Build with production's
  `GRAPHHOPPER_IMAGE` (`export GRAPHHOPPER_IMAGE=…; docker compose pull graphhopper`).
  The image is multi-arch, so an amd64 build serves on arm64. Only an image with
  `FallbackElevationProvider` reads the zoom-12 fallback. Deploy that image to
  production first (its revision is unchanged, so the current graph keeps serving),
  then build with the same tag.
- the release directory is unchanged. Its `config.yaml` and `models/` are the snapshot the
  checksum covers.
- the terrain sits at the same container path. `artifact.json` records
  `/osm_data/elevation/<hash>`, and serving reads `terrain.pmtiles` and
  `fallback.pmtiles` from there. The decoded `cache/` and `cache-fallback/` are only
  used by an import and need not be copied. The
  internal `/elevation` endpoint uses it to enrich saved paths without rerouting them.
  Ordinary routing reads elevations from the graph. Neither downloads terrain.

On Windows, build from WSL with the data on the WSL filesystem. The symlinks and MMAP
files do not work well on an NTFS bind mount.

`just routing-ship-candidate` does the copy and the links below in one step. It reads
`VPS_USER`/`VPS_HOST` from the local `.env`, and the VPS paths from
`/srv/norain/.env` there (`VPS_NORAIN_DIR` overrides the checkout). It takes the terrain
recorded in the candidate's `artifact.json`, so it ships the terrain the graph was
actually built with. Validate and activate on the VPS afterwards. By hand: after the import, run
`readlink data/graphhopper/cache/candidate` (`releases/<id>`) and
`readlink "$ROUTING_OSM_IMPORT_DIR/elevation/current"` (`<hash>`), then copy both
directories and the POI file (the paths on the right are production's):

```sh
rsync -aP data/graphhopper/cache/releases/<id>/ vps:$APP_STORAGE_PATH/graphhopper/cache/releases/<id>/
rsync -aP --exclude /cache/ --exclude /cache-fallback/ "$ROUTING_OSM_IMPORT_DIR/elevation/<hash>/" vps:$ROUTING_OSM_IMPORT_DIR/elevation/<hash>/
rsync -aP "$ROUTING_OSM_IMPORT_DIR/pois-<name>.geojsonseq" vps:$ROUTING_OSM_IMPORT_DIR/
```

Symlinks are not copied. Set them on the serving machine, then continue with step 5 there:

```sh
ln -sfn releases/<id> "$APP_STORAGE_PATH/graphhopper/cache/candidate"
ln -sfn <hash> "$ROUTING_OSM_IMPORT_DIR/elevation/current"   # a later build there passes the check
```

Validate and activate on the serving machine even if you already validated the candidate
where it was built, then run `just poi-import-into-db` for the new area's POIs. The
filtered `.pbf` is needed only for a rebuild on that machine.

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
