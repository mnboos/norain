"""What our own map data covers, for the system dashboard: the running graph's road cells, how
much of them has zoom-15 terrain, and the countries in the Photon index.

GraphHopper reports the files its build left beside the graph and the terrain (`GET /coverage`,
docker/graphhopper/CoverageResource.java); Photon cannot, so its import writes
`meteolane-coverage.json` into its data directory, which the backend mounts read-only. Both are
read on request (admin only, cached), never by a task: they change only when a graph or an index
is swapped in.
"""

import json
import logging
import math
import os
from contextlib import suppress
from datetime import datetime
from pathlib import Path

import httpx
from django.conf import settings
from django.core.cache import cache
from redis.exceptions import RedisError

from .weather import GRAPHHOPPER_URL

logger = logging.getLogger(__name__)

CACHE_KEY = "system:data-coverage"
CACHE_TTL = 300
TIMEOUT = 10
# The zoom-11 mask cells of docker/graphhopper-terrain.py: about 13 km at 47°N.
MASK_ZOOM = 11
ELEVATION_LEVELS = ("full", "partial", "fallback")


def photon_coverage_file() -> Path:
    # Production mounts the Photon data directory here, read-only; in development it is the
    # same folder the dev compose mounts into Photon.
    return Path(
        os.environ.get("PHOTON_COVERAGE_FILE") or settings.BASE_DIR.parent / "data/photon/meteolane-coverage.json"
    )


def _fetch_graphhopper() -> dict:
    response = httpx.get(f"{GRAPHHOPPER_URL}/coverage", timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def _fetch_photon_status() -> dict:
    url = os.environ.get("GEOCODER_API_URL")
    if not url:
        raise RuntimeError("GEOCODER_API_URL is not set")
    response = httpx.get(url.rstrip("/").removesuffix("/api") + "/status", timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def _read_photon_manifest() -> dict | None:
    path = photon_coverage_file()
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def tile_lonlat(x: float, y: float, zoom: int = MASK_ZOOM) -> tuple[float, float]:
    n = 2**zoom
    return x / n * 360 - 180, math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))


def merge_cells(cells, zoom: int = MASK_ZOOM) -> list[list[float]]:
    """[west, south, east, north] per run of adjacent cells in a row (as the terrain script's
    cell_boxes), rounded to ~1 m."""
    boxes = []
    first = previous = None
    for x, y in sorted({(int(x), int(y)) for x, y in cells}, key=lambda cell: (cell[1], cell[0])):
        if previous is not None and (x, y) == (previous[0] + 1, previous[1]):
            previous = (x, y)
            continue
        if previous is not None:
            boxes.append(_box(first, previous, zoom))
        first = previous = (x, y)
    if previous is not None:
        boxes.append(_box(first, previous, zoom))
    return boxes


def _box(first, last, zoom):
    west, north = tile_lonlat(first[0], first[1], zoom)
    east, south = tile_lonlat(last[0] + 1, last[1] + 1, zoom)
    return [round(west, 5), round(south, 5), round(east, 5), round(north, 5)]


def elevation_level(present: int, void: int | None, per_cell: int) -> str:
    """full: every zoom-15 tile there and none with nodata; fallback: no zoom-15 tile, so the
    zoom-12 archive serves the whole cell; partial: the rest."""
    if present >= per_cell and not void:
        return "full"
    if present == 0:
        return "fallback"
    return "partial"


def _datetime(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except TypeError, ValueError:
        return None


def bounds_cells(bounds, zoom: int = MASK_ZOOM) -> set[tuple[int, int]]:
    """Every cell in [west, south, east, north], as the terrain script's bounds_cells."""
    west, south, east, north = bounds
    n = 2**zoom

    def tile(lon, lat):
        return (
            int((lon + 180) / 360 * n),
            int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n),
        )

    (x0, y0), (x1, y1) = tile(west, north), tile(east, south)
    return {(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)}


def _graph_part(report: dict) -> tuple[dict, set]:
    artifact = report.get("artifact") or {}
    cells = {tuple(cell) for cell in report.get("cells") or []}
    cells_source = report.get("cells_source", "graph")
    manifest = report.get("manifest") or {}
    if not cells and isinstance(manifest.get("bounds"), list):
        # Terrain prepared for a bounding box, before cells.json: the box is all there is.
        cells, cells_source = bounds_cells(manifest["bounds"]), "bounds"
    osm = artifact.get("osm_file")
    graph = {
        "release": report.get("release", ""),
        "revision": artifact.get("revision", ""),
        "status": artifact.get("status", ""),
        "built_at": _datetime(artifact.get("built_at")),
        "osm_file": osm.get("name") if isinstance(osm, dict) else None,
        "osm_file_modified": _datetime(osm.get("modified")) if isinstance(osm, dict) else None,
        "cells_source": cells_source,
        "cells": len(cells),
    }
    return graph, cells


def _terrain_part(report: dict, road_cells: set) -> tuple[dict | None, dict[str, list]]:
    manifest = report.get("manifest")
    if not isinstance(manifest, dict):
        return None, {}
    checks = [
        {
            "zoom": check.get("zoom"),
            "positions": check.get("positions", 0),
            "decoded": bool(check.get("decoded")),
            "missing": check.get("missing"),
            "nodata": check.get("void"),
        }
        for check in manifest.get("coverage") or []
        if isinstance(check, dict)
    ]
    terrain = {
        "key": report.get("terrain", ""),
        "catalog_version": str(manifest.get("catalog_version", "")),
        "zoom": manifest.get("zoom"),
        "fallback_zoom": manifest.get("fallback_zoom"),
        "sources": [
            {
                "name": source.get("name", ""),
                "bbox": [source.get(k) for k in ("min_lon", "min_lat", "max_lon", "max_lat")],
            }
            for source in manifest.get("sources") or []
        ],
        "fallback_source": (manifest.get("fallback_source") or {}).get("name"),
        "checks": checks,
        "cell_counts": None,
    }
    per_cell_report = report.get("cell_coverage")
    if not isinstance(per_cell_report, dict):
        return terrain, {}
    per_cell = per_cell_report.get("per_cell", 256)
    levels = {level: [] for level in ELEVATION_LEVELS}
    for x, y, present, void in per_cell_report.get("cells") or []:
        # Only where the graph has roads: terrain may cover more than the graph.
        if road_cells and (x, y) not in road_cells:
            continue
        levels[elevation_level(present, void, per_cell)].append((x, y))
    terrain["cell_counts"] = {level: len(cells) for level, cells in levels.items()}
    return terrain, {level: merge_cells(cells) for level, cells in levels.items()}


def _photon_part() -> dict | None:
    status = manifest = None
    try:
        status = _fetch_photon_status()
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        logger.warning("Photon status unavailable: %s", type(exc).__name__)
    try:
        manifest = _read_photon_manifest()
    except (OSError, ValueError) as exc:
        logger.warning("Photon coverage file unreadable: %s", type(exc).__name__)
    if status is None and manifest is None:
        return None
    status, manifest_data = status or {}, manifest or {}
    countries = manifest_data.get("countries")
    return {
        "reachable": bool(status),
        "import_date": _datetime(status.get("import_date")),
        "version": status.get("version"),
        "manifest": manifest is not None,
        "imported_at": _datetime(manifest_data.get("imported_at")),
        "sources": manifest_data.get("sources"),
        "countries": (
            sorted(
                ({"code": code.upper(), "places": int(places)} for code, places in countries.items()),
                key=lambda country: -country["places"],
            )
            if isinstance(countries, dict)
            else None
        ),
    }


def build_data_coverage() -> dict:
    """Each part fails on its own: a graph without Photon still draws, and vice versa."""
    graph = terrain = None
    road_boxes, elevation_boxes = [], {}
    try:
        report = _fetch_graphhopper()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("GraphHopper coverage unavailable: %s", type(exc).__name__)
    else:
        graph, road_cells = _graph_part(report)
        road_boxes = merge_cells(road_cells)
        terrain, elevation_boxes = _terrain_part(report, road_cells)
    bounds = (
        [
            min(b[0] for b in road_boxes),
            min(b[1] for b in road_boxes),
            max(b[2] for b in road_boxes),
            max(b[3] for b in road_boxes),
        ]
        if road_boxes
        else None
    )
    return {
        "graph": graph,
        "terrain": terrain,
        "photon": _photon_part(),
        "road_boxes": road_boxes,
        "elevation_boxes": {level: elevation_boxes.get(level, []) for level in ELEVATION_LEVELS},
        "bounds": bounds,
    }


def data_coverage() -> dict:
    """Cached for CACHE_TTL. A Redis outage costs the cache, never the answer."""
    try:
        cached = cache.get(CACHE_KEY)
    except RedisError, OSError:  # the cache is an optimisation, never a gate
        cached = None
    if cached is not None:
        return cached
    result = build_data_coverage()
    # Don't keep a failure for five minutes: the next request may find GraphHopper back.
    if result["graph"] is not None and result["photon"] is not None:
        with suppress(RedisError, OSError):
            cache.set(CACHE_KEY, result, CACHE_TTL)
    return result
