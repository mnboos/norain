# Build and deploy the routing graph

MeteoLane builds **GraphHopper 12.0-SNAPSHOT** from commit
`d9506cd7d36d5d068d9118b19b86cf0609dbe773` with Java 25. Its native PMTiles
provider reads **Mapterhorn zoom 15**, using bilinear interpolation. Photon keeps
its own Java runtime. Both amd64 and arm64 images are built from the same Java source.

There are three separate artifacts:

| Artifact      | Container location                     | Purpose                                                                        |
|---------------|----------------------------------------|--------------------------------------------------------------------------------|
| Filtered OSM  | `/osm_data/bike-*.osm.pbf`             | Roads and paths, bike- and hiking-route relations                              |
| Terrain       | `/osm_data/elevation/<manifest-hash>/` | Zoom-15 and zoom-12 fallback PMTiles, attribution, and reusable decoded caches |
| Routing graph | `/graph-cache/releases/<id>/`          | Graph, configuration snapshot, models, and build identity                      |

`/graph-cache/current` selects the active graph. `candidate` selects the newest
successful import; `previous` retains the last activated managed graph. Graph
builds never stop the running service or delete its graph. Startup only loads an
existing graph; it never downloads OSM/terrain or starts an import.

## Choose where to build

**Road elevations are stored in the graph during import.** Production also needs the
matching terrain archives for `/elevation`, which looks up heights for coordinates
such as saved or imported paths. Changing the terrain alone does not update the
heights or slope-based speeds in an existing graph: rebuild it to apply those changes.
Saved routes keep their geometry until explicitly recalculated.

- **Build on production:** follow steps 1–5 on the VPS: select the image, prepare OSM
  and terrain, build a candidate, validate, then activate.
- **Build locally and serve on production:** follow steps 1–4 on the build machine,
  then [ship the candidate and its terrain](#build-elsewhere-serve-in-production).
  Validate and activate on the VPS. **No separate elevation download on the VPS is needed.**

Run commands from the repository root. On the VPS, use the production `.env` with
`COMPOSE_FILE=docker-compose.prod.yml`; the `just` recipes use that Compose configuration.

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

Preparation downloads terrain for the cells containing the filtered OSM file's
nodes, rather than its entire bounding box. It creates `terrain.pmtiles` (zoom 15)
and `fallback.pmtiles` (zoom 12 for gaps), plus metadata, under
`ROUTING_OSM_IMPORT_DIR/elevation/<hash>/`, then selects it with `elevation/current`.

Downloads are split into sequential, independently reusable pieces capped at
1 GB (1,000,000,000 bytes), for both elevation layers. The estimate reports the
piece sizes; preparation splits coverage until the estimated payload is below
900 MB, leaving room for archive metadata, and enforces the 1 GB file limit.
Pieces are merged locally into the final archives, which can exceed 1 GB.
Allow space for the retained pieces, per-source merges and final archives to coexist,
plus decoded caches, the new graph and the retained previous graph.

### What gets downloaded again?

- **Same coverage and source catalog:** the completed terrain is reused after
  checksum verification. The small source catalog is still fetched.
- **Interrupted preparation:** rerun the same command. Completed pieces and source
  extracts that pass verification are reused from `.prepare-<hash>/`. Only unfinished
  or invalid pieces are downloaded again; this is not byte-by-byte resume. Completed
  pieces survive merge and metadata failures. After publication, downloaded pieces
  and source extracts remain alongside the final archives in `elevation/<hash>/`;
  publication does not delete them.
  Completed source extracts from earlier versions remain reusable.
- **Changed coverage or catalog:** preparation uses a new hash and downloads new
  extracts, including areas that overlap older downloads. It does not fetch only
  missing tiles. An already completed set with that hash can still be reused.

A graph build can use the currently selected terrain when it covers all the new
file's cells, even if the filename changed or the region became smaller. In that
case, skip terrain preparation and build directly. If coverage expands beyond it,
prepare terrain for the new file first. Running preparation for a smaller region with different coverage cells creates a
separate set rather than automatically choosing a larger existing one.

Failed preparation leaves the previous `elevation/current` unchanged. Updating this
link does not change a running graph: each graph records its own terrain directory.

To reclaim the space used by retained pieces and regional source extracts after
successful publication, run `just cleanup-elevation-downloads` and accept its
confirmation prompt. It cleans every completed terrain release while preserving
the final `terrain.pmtiles` and `fallback.pmtiles`, metadata, decoded caches, and
unfinished `.prepare-*` directories.

## 4. Import without interrupting routing

```sh
just build-graphhopper-graph-from bike-switzerland-latest.osm.pbf
```

This checks terrain integrity and coverage of the OSM file first, then imports into a
new release directory. A failed import never changes `current` or `candidate`.
Only one import runs at a time. Failed release directories are retained for
inspection and can be removed once no import uses them.

`GRAPHHOPPER_BUILD_HEAP` defaults to `GRAPHHOPPER_HEAP` (6g).
`GRAPHHOPPER_BUILD_THREADS` defaults to 3 and controls CH, LM, urban-density
and subnetwork preparation; increasing it raises peak CPU and memory use.
`GRAPHHOPPER_BUILD_DATAACCESS=RAM_STORE` uses heap; `MMAP` trades speed for a
smaller heap. Serving defaults to `GRAPHHOPPER_DATAACCESS=MMAP`. Leave enough RAM
for the running graph plus the import, or build on another machine. Before Java
starts, the container rejects a cgroup memory limit smaller than the heap plus
native-memory headroom (at least 2 GiB or 10% of the heap), with an actionable
`GRAPHHOPPER_MEM_LIMIT` error instead of a later exit 137.

If the selected terrain does not cover the filtered file, the build stops with an
error. Run `download-elevation-for` for that file, then retry the build.

### Build elsewhere, serve in production

A graph can be built on a larger machine (steps 1–4 there) and copied over. The serving
machine accepts it when:

- the image has the same GraphHopper revision. Build with production's
  `GRAPHHOPPER_IMAGE` (`export GRAPHHOPPER_IMAGE=…; docker compose pull graphhopper`).
  The image is multi-arch, so an amd64 build serves on arm64. Use the same image tag
  for building and serving so both include the zoom-12 fallback support.
- the release directory is unchanged. Its `config.yaml` and `models/` are the snapshot the
  checksum covers.
- the terrain sits at the same container path. `artifact.json` records
  `/osm_data/elevation/<hash>`, and serving reads `terrain.pmtiles` and
  `fallback.pmtiles` from there. The decoded `cache/` and `cache-fallback/` are only
  used by an import and need not be copied. The internal `/elevation` endpoint uses it to enrich saved paths without rerouting them.
  Ordinary routing reads elevations from the graph. Neither downloads terrain.

On Windows, build from WSL with the data on the WSL filesystem. The symlinks and MMAP
files do not work well on an NTFS bind mount.

On the build machine, set `VPS_USER` and `VPS_HOST` in `.env`. The shipping commands
read production paths from `/srv/norain/.env` on the VPS (`VPS_NORAIN_DIR` overrides
that checkout location). Both machines need `rsync`, and SSH access must be configured.

```sh
# On the build machine, after the candidate build:
just routing-ship-candidate-dry-run
just routing-ship-candidate
```

The dry run compares the files to transfer with free space on the VPS. Shipping
copies the candidate, its matching terrain from `artifact.json`, and the matching
POI file if present. It sets the VPS's candidate and terrain links, but does not
validate or activate the graph. Existing matching files are skipped by rsync.
If the POI file is absent, shipping keeps the VPS's existing POIs and prints a notice.

Continue with [step 5](#5-validate-activate-and-check) **on the VPS**, even if the
candidate passed validation locally. Run `just poi-import-into-db` there after
shipping a new POI file. No terrain download is needed on the VPS.

### Copy manually (optional)

Prefer the shipping command above. For a manual transfer, read the local candidate
link (`data/graphhopper/cache/candidate`) to get `releases/<id>`. Read the `terrain`
field from that release's `artifact.json` to get `/osm_data/elevation/<hash>`.
Do not use `elevation/current`: it may have changed since the candidate was built.
Copy the release, that terrain directory (excluding `cache/` and `cache-fallback/`),
and the matching POI file to the corresponding production paths. Create destination
directories first. On the VPS, set:

```sh
ln -sfn releases/<id> "$APP_STORAGE_PATH/graphhopper/cache/candidate"
ln -sfn <hash> "$ROUTING_OSM_IMPORT_DIR/elevation/current"
```

These are placeholders; use the actual IDs and production paths from `.env`.
Then validate and activate as in step 5.

### Files to keep on production

| Files | Needed for |
|---|---|
| Graph release, including `artifact.json`, configuration and models | Serving and validation |
| Matching terrain directory, including both PMTiles archives and metadata | Coordinate elevation lookups and future builds |
| Decoded `cache/` and `cache-fallback/` directories | Builds only; no need to ship them |
| Filtered `.osm.pbf` | Rebuilding on that machine; no need to ship it for serving |

Keep the terrain referenced by every active, candidate or retained rollback graph,
using each release's `artifact.json` to identify it. Several graphs can share one
terrain directory. Do not remove it just because `elevation/current` points elsewhere.

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

## Terrain details

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

Preparation requires at least one intersecting zoom-15 source archive in the catalog;
it cannot currently prepare an area served only by the planet fallback.

Zoom 15 has gaps: it is missing in central and southern Italy, the Balkans, Greece,
Iceland and east of about 28°E, and some tiles contain nodata pixels. Wherever zoom 15
has no value, GraphHopper reads the zoom-12 fallback (`FallbackElevationProvider`,
patched into the jar), in the import and in `/elevation` alike. Without the fallback it
would store 0 m for such a node, and the slope against the real heights beside it
would become a cliff that the speed rules take seriously. At the edge of zoom-15
coverage the two sources can differ by a few metres. Only where the fallback has no
value either does a node get 0 m.

The cells are kept as `cells.json`. Before an import, the check runs the same pass
over the file and requires its cells to be a subset of the prepared ones, so terrain
for more countries also serves a file with fewer. That is one extra pass per build,
minutes for a large file. Terrain prepared for a bounding box, before the cells
existed, is still compared by its bounds.

Preparation verifies the PMTiles structure. For up to 250,000 tile positions per
archive it also decodes every tile, and it records missing tiles and tiles with
nodata in the manifest's `coverage`: counts and a few examples, never an error.
Larger areas skip that per-tile pass. Terrain prepared before the fallback existed
has no `fallback.pmtiles` and still builds and serves as before.

GraphHopper stores decoded tiles in `cache/` and `cache-fallback/`. Imports keep at
most 512 decoded tiles memory-mapped at once, so continent-sized builds do not exhaust
native mappings or file descriptors. An empty, interrupted, or legacy-format cache
file is deleted and regenerated from the retained PMTiles archive when it is read;
valid cached tiles remain reusable.

The manifest records the coverage-cell hash, source catalog version, source metadata,
zooms and archive checksums. Mapterhorn attribution is retained as `attribution.json`;
see [Mapterhorn attribution](https://mapterhorn.com/attribution/) and
[data access](https://mapterhorn.com/data-access/).

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
