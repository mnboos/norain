# Change the geographic coverage

Use this guide to route and search in a different region. You need the existing
local setup and enough storage and memory to build the new graph.

This covers one region that GraphHopper and Photon download themselves. To cover
several countries at once, or to build from files you already have, follow
[build routing and search from downloaded files](import-geodata.md) instead.

1. Keep the current routing service running while preparing its replacement. Ensure
   there is disk and memory for both graphs and the zoom-15 terrain data.

2. Set `OSM_DATA_URL` in `.env` to the new region's Geofabrik `.osm.pbf` URL.
   Set `PHOTON_INDEX_URL` to a matching Photon regional dump or prebuilt index.
   The checked-in Photon image expects the `1.0` artifact version token and the
   startup script supports `.jsonl.zst` dumps and `.tar.bz2` indexes. Select URLs
   for the desired coverage from the providers; changing one service does not
   change the other.
3. Prepare Mapterhorn terrain and import a candidate graph following the
   [routing-graph guide](build-routing-graph.md). Run `routing-terrain-estimate`,
   `routing-terrain-from` and `build-graphhopper-graph-from` with the same filtered
   OSM filename. Validate endpoints inside the new region, then run
   `routing-activate`. Zoom-15 terrain must cover the entire OSM extent.
4. For search, move `data/photon` to a backup location and recreate the empty directory:
   Photon only imports when its index directory is absent. Adjust `PHOTON_IMPORT_HEAP`
   as needed. Then recreate Photon and follow its import:

   ```bash
   docker compose up -d --force-recreate photon
   docker compose logs -f photon
   ```

5. On production, do the same on the VPS
   ([path C](build-routing-graph.md#4-import-without-interrupting-routing)), or build the graph on another computer and copy
   it over ([path D](build-routing-graph.md#4-import-without-interrupting-routing)).

6. Search for a town within the new area and create a short route between covered
   locations. Confirm geometry is computed and a weather forecast loads.

Saved routes retain their old geometry. Re-enqueue geometry for routes you want to
recompute using the [background-job guide](background-jobs.md); routes outside the
new area may no longer be routable. Restart the backend and worker to clear their
in-memory route caches.

Geographic coverage and weather interpretation are separate: weather requests still
use `Europe/Zurich` in the current code. A region in another time zone needs application
changes as described in [forecast interpretation](../explanation/forecasts.md).

[Documentation index](../README.md)
