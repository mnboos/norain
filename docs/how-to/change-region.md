# Change the geographic coverage

Use this guide to route and search in a different region. You need the existing
local setup and enough storage and memory to build the new graph.

1. Stop the geographic services from the repository root:

   ```bash
   docker compose -f docker-compose.dev.yml stop graphhopper photon
   ```

2. Set `OSM_DATA_URL` in `.env` to the new region's Geofabrik `.osm.pbf` URL.
   Set `PHOTON_INDEX_URL` to a matching Photon regional dump or prebuilt index.
   The checked-in Photon image expects the `1.0` artifact version token and the
   startup script supports `.jsonl.zst` dumps and `.tar.bz2` indexes. Select URLs
   for the desired coverage from the providers; changing one service does not
   change the other.
3. Preserve the existing data by moving `data/graphhopper/cache` and `data/photon`
   to backup locations outside their mounted paths. Recreate the empty directories.
   GraphHopper must rebuild its graph; Photon only imports when its index directory
   is absent. Changing URLs alone does not replace these indexes. Keep the old OSM
   file if desired, but move it aside too if the new URL has the same filename.
4. Adjust `GRAPHHOPPER_HEAP` (or `GRAPHHOPPER_BUILD_HEAP` for the build alone) and
   `PHOTON_IMPORT_HEAP` as needed. If increasing GraphHopper's heap beyond the current
   budget, also raise `GRAPHHOPPER_MEM_LIMIT` (default 8 GB), leaving room for non-heap
   memory. Production works the same way: empty `graphhopper/cache` and GraphHopper
   builds the new graph on start. If the VPS lacks the memory, build it as described in
   [build the routing graph elsewhere](build-routing-graph.md) and copy it over.
5. Recreate the services and monitor the import:

   ```bash
   docker compose -f docker-compose.dev.yml up -d --force-recreate graphhopper photon
   docker compose -f docker-compose.dev.yml logs -f graphhopper photon
   ```

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
