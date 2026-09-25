#!/usr/bin/env python3
"""Prepare immutable, verified Mapterhorn extracts. Never modifies a routing graph.

Two archives per area: zoom 15 where Mapterhorn has it (terrain.pmtiles), and zoom 12 from
its planet archive (fallback.pmtiles), which covers everything. GraphHopper reads the fallback
wherever zoom 15 has no value (FallbackElevationProvider), so a country without zoom-15 data
gets coarser heights instead of 0 m.

Both cover only the zoom-11 cells that hold a node of the OSM file (region.geojson), not its
bounding box: GraphHopper reads the one zoom-15 tile under each node, nothing else, so two
distant countries do not pull in everything between them."""

import argparse
import hashlib
import io
import json
import math
import mmap
import os
import subprocess
import tempfile
import urllib.request
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

ZOOM = 15
FALLBACK_ZOOM = 12
# The coverage mask: one cell is 16x16 zoom-15 tiles, about 13 km at 47°N. Coarse enough for a
# small region file, and it also covers points of saved paths that lie between nodes.
MASK_ZOOM = 11
# Decoding every tile of a large area takes hours, and a gap is no longer an error: past this
# many tile positions only the archive structure is checked.
VERIFY_MAX_TILES = 250_000
CATALOG_URL = "https://download.mapterhorn.com/download_urls.json"
ATTRIBUTION_URL = "https://download.mapterhorn.com/attribution.json"
MAX_LAT = 85.0511287798066


def run(*args, capture=False):
    return subprocess.run(
        [str(a) for a in args],
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    ).stdout


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download_json(url):
    request = urllib.request.Request(
        url, headers={"User-Agent": "NoRain/1.0 (+https://github.com/mnboos/norain)"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def tile_xy(lon, lat, zoom=ZOOM):
    n = 2**zoom
    return (
        (lon + 180) / 360 * n,
        (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n,
    )


def tile_lonlat(x, y, zoom=ZOOM):
    n = 2**zoom
    return x / n * 360 - 180, math.degrees(
        math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    )


def padded_bounds(bounds):
    west, south, east, north = bounds
    if not (-180 < west < east < 180 and -MAX_LAT < south < north < MAX_LAT):
        raise ValueError(
            "OSM bounds must be nonempty, within Web Mercator, and not cross the antimeridian."
        )
    x0, y0 = tile_xy(west, north)
    x1, y1 = tile_xy(east, south)
    # Whole tiles plus one tile of margin on every side.
    n = 2**ZOOM
    rect = (
        max(0, math.floor(x0) - 1),
        max(0, math.floor(y0) - 1),
        min(n - 1, math.floor(x1) + 1),
        min(n - 1, math.floor(y1) + 1),
    )
    w, top = tile_lonlat(rect[0], rect[1])
    e, bottom = tile_lonlat(rect[2] + 1, rect[3] + 1)
    return [w, bottom, e, top], rect


def osm_bounds(pbf):
    info = json.loads(run("osmium", "fileinfo", "-e", "-j", pbf, capture=True))
    # Computed from actual nodes, not an optional/stale PBF header bounding box.
    bounds = info["data"]["bbox"]
    if len(bounds) != 4 or not all(isinstance(v, (int, float)) for v in bounds):
        raise ValueError("OSM file has no usable node bounds.")
    return bounds


def cells_from_opl(lines, zoom=MASK_ZOOM):
    """The zoom cells holding a node, from `osmium cat -f opl` node lines (x<lon> y<lat>)."""
    n = 2**zoom
    cells = set()
    for line in lines:
        lon = lat = ""
        for field in line.split():
            if field[0] == "x":
                lon = field[1:]
            elif field[0] == "y":
                lat = field[1:]
        if not lon or not lat:  # a node without a location
            continue
        x, y = tile_xy(float(lon), float(lat), zoom)
        cells.add((min(n - 1, max(0, int(x))), min(n - 1, max(0, int(y)))))
    return cells


def node_cells(pbf):
    """One pass over the file: minutes for several countries."""
    with subprocess.Popen(
        ["osmium", "cat", "-t", "node", "-f", "opl,add_metadata=false", str(pbf)],
        stdout=subprocess.PIPE,
        text=True,
    ) as process:
        cells = cells_from_opl(process.stdout)
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, "osmium cat")
    if not cells:
        raise ValueError("OSM file has no nodes.")
    return cells


def cell_boxes(cells, zoom=MASK_ZOOM):
    """[west, south, east, north] per run of adjacent cells in a row."""
    boxes = []
    run_start = previous = None
    for x, y in sorted(cells, key=lambda cell: (cell[1], cell[0])):
        if previous is not None and (y, x) == (previous[1], previous[0] + 1):
            previous = (x, y)
            continue
        if previous is not None:
            boxes.append(_box(run_start, previous, zoom))
        run_start = previous = (x, y)
    if previous is not None:
        boxes.append(_box(run_start, previous, zoom))
    return boxes


def _box(first, last, zoom):
    west, north = tile_lonlat(first[0], first[1], zoom)
    east, south = tile_lonlat(last[0] + 1, last[1] + 1, zoom)
    # Very slightly inside the cell edges, so extraction does not add the neighbouring row
    # or column through inclusive bounds or floating-point roundoff.
    return [west + 1e-9, south + 1e-9, east - 1e-9, north - 1e-9]


def region_geojson(boxes):
    return {
        "type": "MultiPolygon",
        "coordinates": [
            [[[w, s], [e, s], [e, n], [w, n], [w, s]]] for w, s, e, n in boxes
        ],
    }


def cells_json(cells):
    """The canonical cells.json content; its sha256 is part of the terrain key."""
    return json.dumps(sorted(cells), separators=(",", ":"))


def _check_host(item):
    if not item["url"].startswith("https://download.mapterhorn.com/"):
        raise ValueError("Unexpected Mapterhorn download host.")
    return item


def select_sources(catalog, boxes):
    def intersects(item):
        return any(
            item["min_lon"] < e
            and item["max_lon"] > w
            and item["min_lat"] < n
            and item["max_lat"] > s
            for w, s, e, n in boxes
        )

    sources = [
        item
        for item in catalog["items"]
        if item["min_zoom"] <= ZOOM <= item["max_zoom"] and intersects(item)
    ]
    if not sources:
        raise ValueError(
            "No Mapterhorn zoom-15 archive covers this area. Choose an area with high-resolution coverage."
        )
    return sorted(map(_check_host, sources), key=lambda item: item["name"])


def select_fallback(catalog):
    """The planet archive: zooms 0-12, everywhere."""
    planets = [
        item
        for item in catalog["items"]
        if item["min_zoom"] <= FALLBACK_ZOOM <= item["max_zoom"]
    ]
    if len(planets) != 1:
        raise ValueError(
            f"Expected one Mapterhorn archive with zoom {FALLBACK_ZOOM}, found {len(planets)}."
        )
    return _check_host(planets[0])


def cell_tiles(cells, zoom):
    """The zoom tiles inside the mask cells, in (x, y) order."""
    if zoom >= MASK_ZOOM:
        k = 2 ** (zoom - MASK_ZOOM)
        tiles = {
            (x * k + i, y * k + j) for x, y in cells for i in range(k) for j in range(k)
        }
    else:
        shift = MASK_ZOOM - zoom
        tiles = {(x >> shift, y >> shift) for x, y in cells}
    return sorted(tiles)


@contextmanager
def archive_reader(path):
    from pmtiles.reader import Reader

    with (
        Path(path).open("rb") as stream,
        mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as mapping,
    ):
        # Cache directories, not multi-megabyte raster tile payloads.
        @lru_cache(maxsize=128)
        def small_read(offset, length):
            return mapping[offset : offset + length]

        yield Reader(
            lambda offset, length: (
                small_read(offset, length)
                if length < 32768
                else mapping[offset : offset + length]
            )
        )


def has_void(rgb):
    """Terrarium nodata as GraphHopper decodes it: red and green 0, about -32768 m.
    Deep sea is not nodata (-1024 m is red 124), and the zoom-12 archive has bathymetry."""
    from PIL import ImageChops

    red, green, _ = rgb.split()
    if red.getextrema()[0] != 0:
        return False
    return ImageChops.lighter(red, green).getextrema()[0] == 0


def verify_coverage(path, cells, zoom=ZOOM):
    """Counts missing tiles and tiles with nodata inside the mask cells, at zoom.

    Neither is an error. GraphHopper reads the fallback archive where zoom 15 has no value;
    where the fallback has none either, it stores 0 m, so those are the counts to watch.
    """
    from PIL import Image
    from pmtiles.tile import Compression, TileType

    positions = len(cell_tiles(cells, zoom))
    missing, void = [], []
    with archive_reader(path) as reader:
        header = reader.header()
        if (
            header["tile_type"] != TileType.WEBP
            or header["tile_compression"] != Compression.NONE
        ):
            raise ValueError("Expected uncompressed Terrarium WebP tiles.")
        if positions > VERIFY_MAX_TILES:
            print(
                f"Not decoding {positions} zoom-{zoom} tile positions one by one "
                f"(more than {VERIFY_MAX_TILES}); the archive structure is verified.",
                flush=True,
            )
            return {"zoom": zoom, "positions": positions, "decoded": False}
        for x, y in cell_tiles(cells, zoom):
            data = reader.get(zoom, x, y)
            if not data:
                missing.append(f"{zoom}/{x}/{y}")
                continue
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                if image.size != (512, 512):
                    raise ValueError(
                        f"Unexpected terrain tile dimensions: {image.size}"
                    )
                if has_void(image.convert("RGB")):
                    void.append(f"{zoom}/{x}/{y}")
    print(f"Verified {positions} zoom-{zoom} tile positions.", flush=True)
    for kind, tiles in (("are missing", missing), ("have nodata", void)):
        if tiles:
            print(
                f"WARNING: {len(tiles)} zoom-{zoom} terrain tile(s) {kind}. "
                f"Examples: {', '.join(tiles[:5])}",
                flush=True,
            )
    # Counts and a few examples: over a large area the full list runs to millions.
    return {
        "zoom": zoom,
        "positions": positions,
        "decoded": True,
        "missing": len(missing),
        "void": len(void),
        "examples": missing[:10] + void[:10],
    }


def check(directory, pbf=None, cells=None):
    """cells: the file's node cells, when the caller has them already."""
    directory = Path(directory).resolve(strict=True)
    manifest = json.loads((directory / "manifest.json").read_text())
    if (
        manifest["zoom"] != ZOOM
        or digest(directory / "terrain.pmtiles") != manifest["sha256"]
    ):
        raise ValueError(
            "Terrain checksum or zoom mismatch. Run terrain preparation again."
        )
    # Terrain prepared before the fallback existed has none.
    if "fallback_sha256" in manifest and (
        digest(directory / "fallback.pmtiles") != manifest["fallback_sha256"]
    ):
        raise ValueError(
            "Fallback terrain checksum mismatch. Run terrain preparation again."
        )
    if pbf is None:
        return directory, manifest
    if "mask_zoom" in manifest:
        stored = directory / "cells.json"
        if digest(stored) != manifest["cells_sha256"]:
            raise ValueError(
                "Terrain cells checksum mismatch. Run terrain preparation again."
            )
        # A subset is enough: terrain for more countries serves a file with fewer.
        prepared = {tuple(cell) for cell in json.loads(stored.read_text())}
        matches = (cells if cells is not None else node_cells(pbf)) <= prepared
    else:
        # Terrain prepared for the bounding box, before the mask existed.
        bounds, rect = padded_bounds(osm_bounds(pbf))
        matches = bounds == manifest["bounds"] and list(rect) == manifest["tile_rect"]
    if not matches:
        raise ValueError(
            "Prepared terrain does not match this OSM extent. Prepare terrain for this file first."
        )
    return directory, manifest


def prepare(pbf, root, dry_run=False):
    # Only a sanity check now: Web Mercator, no antimeridian crossing.
    padded_bounds(osm_bounds(pbf))
    cells = node_cells(pbf)
    boxes = cell_boxes(cells)
    region = json.dumps(region_geojson(boxes))
    cells_content = cells_json(cells)
    catalog = download_json(CATALOG_URL)
    sources = select_sources(catalog, boxes)
    fallback = select_fallback(catalog)
    spec = {
        "format": 3,
        "zoom": ZOOM,
        "mask_zoom": MASK_ZOOM,
        "cells_sha256": hashlib.sha256(cells_content.encode()).hexdigest(),
        "catalog_version": catalog["version"],
        "sources": sources,
        "fallback_zoom": FALLBACK_ZOOM,
        "fallback_source": fallback,
    }
    key = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:24]
    target = root / key
    print(
        f"Zoom {ZOOM} for {len(cells)} zoom-{MASK_ZOOM} cells with roads "
        f"({len(boxes)} rectangles); {len(sources)} regional archives, plus zoom "
        f"{FALLBACK_ZOOM} from {fallback['name']} where zoom {ZOOM} has no value",
        flush=True,
    )
    # The fallback comes last.
    extracts = [(source, ZOOM) for source in sources] + [(fallback, FALLBACK_ZOOM)]

    def extract(source, zoom, output, region_file, *extra):
        run(
            "pmtiles",
            "extract",
            source["url"],
            output,
            f"--region={region_file}",
            f"--minzoom={zoom}",
            f"--maxzoom={zoom}",
            *extra,
        )

    if dry_run:
        with tempfile.TemporaryDirectory() as directory:
            region_file = Path(directory) / "region.geojson"
            region_file.write_text(region)
            for source, zoom in extracts:
                extract(
                    source,
                    zoom,
                    Path(directory) / "estimate.pmtiles",
                    region_file,
                    "--dry-run",
                )
        return
    root.mkdir(parents=True, exist_ok=True)
    # A failed download never replaces the previous current directory.
    if target.exists():
        check(target, pbf, cells)
        print(f"Reusing verified terrain: {target}")
    else:
        # Keep completed source extracts across interruptions and verification failures.
        # They are content-addressed by the catalog/cells spec and never become current
        # until the final merged archive has passed verification.
        stage = root / f".prepare-{key}"
        stage.mkdir(parents=True, exist_ok=True)
        region_file = stage / "region.geojson"
        region_file.write_text(region)
        (stage / "cells.json").write_text(cells_content)
        parts = []
        for i, (source, zoom) in enumerate(extracts):
            part = stage / (
                "fallback.pmtiles" if zoom == FALLBACK_ZOOM else f"part-{i}.pmtiles"
            )
            if part.exists():
                try:
                    run("pmtiles", "verify", part)
                    print(f"Reusing completed terrain extract: {part}", flush=True)
                except (OSError, subprocess.CalledProcessError):
                    part.unlink(missing_ok=True)
            if not part.exists():
                extract(source, zoom, part, region_file)
            parts.append(part)
        *parts, fallback_path = parts
        terrain = stage / "terrain.pmtiles"
        terrain.unlink(missing_ok=True)
        if len(parts) == 1:
            parts[0].rename(terrain)
        else:
            run("pmtiles", "merge", *parts, terrain)
        run("pmtiles", "verify", terrain)
        coverage = [
            verify_coverage(terrain, cells),
            verify_coverage(fallback_path, cells, FALLBACK_ZOOM),
        ]
        for part in parts:
            part.unlink(missing_ok=True)
        spec["sha256"] = digest(terrain)
        spec["fallback_sha256"] = digest(fallback_path)
        spec["coverage"] = coverage
        spec["attribution_url"] = "https://mapterhorn.com/attribution/"
        (stage / "attribution.json").write_text(
            json.dumps(download_json(ATTRIBUTION_URL), indent=2) + "\n"
        )
        (stage / "manifest.json").write_text(json.dumps(spec, indent=2) + "\n")
        # Rename the completed directory. Never overwrite another process's archive.
        stage.rename(target)
    temporary = root / f".current-{os.getpid()}"
    try:
        temporary.symlink_to(target.name, target_is_directory=True)
        temporary.replace(root / "current")
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Terrain ready: {target}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "check"))
    parser.add_argument("pbf", type=Path)
    parser.add_argument("--root", type=Path, default=Path("/osm_data/elevation"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        # Serialize preparation, including current-symlink changes, across containers.
        if args.dry_run:
            prepare(args.pbf, args.root, True)
        else:
            import fcntl

            args.root.mkdir(parents=True, exist_ok=True)
            with (args.root / ".prepare.lock").open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                prepare(args.pbf, args.root)
    else:
        check(args.root / "current", args.pbf)
        print("Terrain checksum and OSM extent match.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"Terrain preparation failed: {exc}") from exc
