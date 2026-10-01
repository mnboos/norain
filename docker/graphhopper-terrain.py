#!/usr/bin/env python3
"""Prepare immutable, verified Mapterhorn extracts. Never modifies a routing graph.

Two archives per area: zoom 15 where Mapterhorn has it (terrain.pmtiles), and zoom 12 from
its planet archive (fallback.pmtiles), which covers everything. GraphHopper reads the fallback
wherever zoom 15 has no value (FallbackElevationProvider), so a country without zoom-15 data
gets coarser heights instead of 0 m.

Both cover only the zoom-11 cells that hold a node of the OSM file (region.geojson), not its
bounding box: GraphHopper reads the one zoom-15 tile under each node, nothing else, so two
distant countries do not pull in everything between them.

Each source is downloaded in pieces of at most 1 GB by default. With --whole it is one
extract per source, of any size (the old way). Both give the same archives and the same key."""

import argparse
import hashlib
import io
import json
import math
import mmap
import os
import re
import resource
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.request
from bisect import bisect_right
from collections import Counter
from contextlib import contextmanager
from decimal import Decimal
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
MAX_PIECE_BYTES = 1_000_000_000
ESTIMATE_PIECE_BYTES = 900_000_000
PARTITION_VERSION = 1
# go-pmtiles sends each piece as a few large range requests and has no timeout or retry. Now
# and then one connection stalls at a few kB/s, which leaves the piece crawling near its end
# while a fresh connection gets tens of MB/s. So a download whose byte count has not moved for
# STALL_SECONDS is killed and the piece started again, at most DOWNLOAD_ATTEMPTS times. A piece
# whose download fails (the host resets a stream now and then) is started again the same way.
STALL_SECONDS = 90
DOWNLOAD_ATTEMPTS = 3
# Requests at once. Mapterhorn is a free host, so keep this modest.
DOWNLOAD_THREADS = int(os.environ.get("TERRAIN_DOWNLOAD_THREADS") or 8)
# Share of extra bytes go-pmtiles may fetch (and throw away) to merge nearby chunks. Merging
# makes requests longer, and a long request is what stalls, so it is off by default.
OVERFETCH = float(os.environ.get("TERRAIN_OVERFETCH") or 0)
# The byte count in the progress bar: "(528/601 MB, 20 kB/s)".
PROGRESS = re.compile(r"\(([\d.]+)/[\d.]+ \w*B, ")


class PieceTooLarge(ValueError):
    pass


class DownloadStalled(RuntimeError):
    pass


def extract_output(args, limit_size=None):
    """Keep CLI progress visible while retaining diagnostics for size-limit failures.

    Kills the process when its output shows no progress for STALL_SECONDS: any line other
    than the progress bar counts as progress, and so does a bar whose byte count moved."""
    with subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        preexec_fn=limit_size,  # noqa: PLW1509 -- preparation is single-threaded
    ) as process:
        lines = []
        pending = b""
        last_count = None
        last_progress = time.monotonic()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                if time.monotonic() - last_progress > STALL_SECONDS:
                    process.kill()
                    process.wait()
                    raise DownloadStalled(
                        f"pmtiles made no progress for {STALL_SECONDS} s"
                    )
                if not selector.select(timeout=5):
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                *complete, pending = re.split(rb"\r\n|\r|\n", pending + chunk)
                for raw in complete:
                    line = raw.decode(errors="replace") + "\n"
                    print(line, end="", flush=True)
                    lines.append(line)
                    match = PROGRESS.search(line)
                    count = match.group(1) if match else None
                    if count is None or count != last_count:
                        last_count = count
                        last_progress = time.monotonic()
        if pending:
            line = pending.decode(errors="replace")
            print(line, flush=True)
            lines.append(line)
        return subprocess.CompletedProcess(args, process.wait(), "".join(lines))


def extract_piece(source, zoom, output, region_file, dry_run=False):
    """Limit only downloads, never local merges. The preparation process is single-threaded."""

    def limit_size():
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_PIECE_BYTES, MAX_PIECE_BYTES))

    args = [
        "pmtiles",
        "extract",
        source["url"],
        str(output),
        f"--region={region_file}",
        f"--minzoom={zoom}",
        f"--maxzoom={zoom}",
        f"--overfetch={OVERFETCH}",
        f"--download-threads={DOWNLOAD_THREADS}",
    ]
    if dry_run:
        args.append("--dry-run")
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            result = extract_output(args, None if dry_run else limit_size)
            if not result.returncode:
                break
            if not dry_run and (
                result.returncode == -signal.SIGXFSZ
                or "file too large" in result.stdout.lower()
            ):
                raise PieceTooLarge("Terrain piece reached the 1 GB file limit")
            # Any other failure, such as the host resetting an HTTP/2 stream, is retried
            # like a stall: a fresh start almost always works.
            error = subprocess.CalledProcessError(
                result.returncode, args, result.stdout
            )
        except DownloadStalled as stalled:
            error = stalled
        if attempt == DOWNLOAD_ATTEMPTS:
            raise error
        print(
            f"{error}; starting the piece again ({attempt + 1}/{DOWNLOAD_ATTEMPTS})",
            flush=True,
        )
    if not dry_run:
        if output.stat().st_size > MAX_PIECE_BYTES:
            raise PieceTooLarge("Terrain piece exceeded the 1 GB file limit")
        return
    # go-pmtiles v1.31.2 reports humanized decimal tile payload bytes, not metadata.
    # Empty extracts cannot pass CLI verification (its minimum tile zoom stays 31).
    # Use the explicit entry count, not a rounded payload size, to detect them.
    if re.search(r"\bresult tile entries 0\b", result.stdout):
        return 0
    match = re.search(
        r"archive size of ([\d.]+) (B|kB|MB|GB|TB|PB|EB)\b", result.stdout
    )
    if not match:
        raise ValueError(
            "Cannot read pmtiles dry-run archive size; refusing an unbounded download"
        )
    value, unit = match.groups()
    scale = 1000 ** ("B", "kB", "MB", "GB", "TB", "PB", "EB").index(unit)
    # Add one unit of displayed precision to bound rounding upwards.
    precision = len(value.split(".")[1]) if "." in value else 0
    return int((Decimal(value) + Decimal(10) ** -precision) * scale)


def split_cells(cells, cell_zoom, zoom):
    ordered = sorted(cells)
    if len(ordered) > 1:
        middle = len(ordered) // 2
        return [(ordered[:middle], cell_zoom), (ordered[middle:], cell_zoom)]
    if cell_zoom >= zoom:
        raise ValueError(
            "A single terrain tile cannot fit within the 1 GB download limit"
        )
    x, y = ordered[0]
    return [
        ([(2 * x + dx, 2 * y + dy)], cell_zoom + 1)
        for dx in range(2)
        for dy in range(2)
    ]


def download_pieces(source, zoom, cells, directory, dry_run=False, cell_zoom=MASK_ZOOM):
    """Stable subdivision tree: completed leaves survive changed estimates and retries."""
    directory.mkdir(parents=True, exist_ok=True)
    identity = [PARTITION_VERSION, source, zoom, cell_zoom, sorted(cells)]
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    part = directory / f"{key}.pmtiles"
    split = directory / f"{key}.split"
    if part.exists() and not dry_run:
        try:
            if part.stat().st_size > MAX_PIECE_BYTES:
                raise PieceTooLarge("Oversized cached piece")
            run("pmtiles", "verify", part)
            print(f"Reusing completed terrain piece: {part.name}", flush=True)
            return [part]
        except (ValueError, subprocess.CalledProcessError):
            part.unlink()
    region_file = directory / f"{key}.geojson"
    temporary = directory / f"{key}.partial.pmtiles"
    temporary.unlink(missing_ok=True)
    if not split.exists():
        region_file.write_text(json.dumps(region_geojson(cell_boxes(cells, cell_zoom))))
        size = extract_piece(source, zoom, temporary, region_file, dry_run=True)
        if size == 0:
            print(
                f"Skipping empty terrain piece {source['name']} zoom {zoom}",
                flush=True,
            )
            return []
        if size <= ESTIMATE_PIECE_BYTES:
            print(
                f"Terrain piece {source['name']} zoom {zoom}: at most ~{size / 1e6:.1f} MB payload",
                flush=True,
            )
            if dry_run:
                return [part]
            try:
                extract_piece(source, zoom, temporary, region_file)
                run("pmtiles", "verify", temporary)
                temporary.replace(part)
                return [part]
            except PieceTooLarge:
                temporary.unlink(missing_ok=True)
        # Persist subdivision before downloading children, including size-limit splits.
        children = split_cells(cells, cell_zoom, zoom)
        split.touch()
    else:
        children = split_cells(cells, cell_zoom, zoom)
    parts = []
    for child_cells, child_zoom in children:
        parts.extend(
            download_pieces(source, zoom, child_cells, directory, dry_run, child_zoom)
        )
    return parts


def extract_whole(source, zoom, output, region_file):
    """The old way (--whole): one extract per source over the whole region, with no size
    limit and no pieces. Fewer, longer requests; an interrupted download starts over.
    Returns False when the source has no tiles in the region."""
    size = extract_piece(source, zoom, output, region_file, dry_run=True)
    if size == 0:
        print(f"Skipping empty terrain source {source['name']} zoom {zoom}", flush=True)
        return False
    print(
        f"Terrain source {source['name']} zoom {zoom}: at most ~{size / 1e6:.1f} MB "
        "payload, in one piece",
        flush=True,
    )
    temporary = output.with_suffix(".partial.pmtiles")
    temporary.unlink(missing_ok=True)
    run(
        "pmtiles",
        "extract",
        source["url"],
        temporary,
        f"--region={region_file}",
        f"--minzoom={zoom}",
        f"--maxzoom={zoom}",
        f"--overfetch={OVERFETCH}",
        f"--download-threads={DOWNLOAD_THREADS}",
    )
    run("pmtiles", "verify", temporary)
    temporary.replace(output)
    return True


def merge_pieces(parts, output):
    if not parts:
        raise ValueError(f"No terrain tiles available for {output.name}")
    temporary = output.with_suffix(".partial.pmtiles")
    temporary.unlink(missing_ok=True)
    if len(parts) == 1:
        shutil.copyfile(parts[0], temporary)
    else:
        run("pmtiles", "merge", *parts, temporary)
    run("pmtiles", "verify", temporary)
    temporary.replace(output)


def cleanup_downloads(root):
    """Remove retained inputs from published terrain sets, preserving resumable staging."""
    root = Path(root)
    removed_files = 0
    removed_bytes = 0
    if not root.exists():
        print(f"No elevation directory exists at {root}.")
        return removed_files, removed_bytes
    for directory in sorted(root.iterdir()):
        # `current` is a symlink to one of these directories. Process each release once.
        if not directory.is_dir() or directory.is_symlink():
            continue
        # A manifest exists only after successful publication. Incomplete preparations
        # retain their pieces so the next preparation can resume.
        if not (directory / "manifest.json").is_file():
            continue
        pieces = directory / "pieces"
        if pieces.is_symlink():
            raise ValueError(f"Refusing to clean symlinked pieces directory: {pieces}")
        if pieces.is_dir():
            files = [path for path in pieces.rglob("*") if path.is_file()]
            removed_files += len(files)
            removed_bytes += sum(path.stat().st_size for path in files)
            shutil.rmtree(pieces)
        for part in directory.glob("part-*.pmtiles"):
            if not part.is_file() or part.is_symlink():
                continue
            removed_files += 1
            removed_bytes += part.stat().st_size
            part.unlink()
    print(
        f"Removed {removed_files} retained elevation download file(s) "
        f"({removed_bytes / 1_000_000_000:.2f} GB). Final terrain archives were preserved."
    )
    return removed_files, removed_bytes


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
        url, headers={"User-Agent": "MeteoLane/1.0 (+https://meteolane.com)"}
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


def verify_coverage(path, cells, zoom=ZOOM, void_out=None):
    """Counts missing tiles and tiles with nodata inside the mask cells, at zoom.

    Neither is an error. GraphHopper reads the fallback archive where zoom 15 has no value;
    where the fallback has none either, it stores 0 m, so those are the counts to watch.
    void_out: a set that receives every nodata tile as (x, y), for cell_coverage.
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
                    if void_out is not None:
                        void_out.add((x, y))
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


def tile_ranges(path):
    """The archive's tile entries as sorted (first tile id, run length): directories only,
    never a tile payload, so it is cheap even for a continent."""
    from pmtiles.tile import deserialize_directory, deserialize_header

    with Path(path).open("rb") as stream, mmap.mmap(
        stream.fileno(), 0, access=mmap.ACCESS_READ
    ) as mapping:
        header = deserialize_header(mapping[0:127])
        ranges = []
        pending = [(header["root_offset"], header["root_length"])]
        while pending:
            offset, length = pending.pop()
            for entry in deserialize_directory(mapping[offset : offset + length]):
                if entry.run_length:
                    ranges.append((entry.tile_id, entry.run_length))
                else:
                    pending.append(
                        (header["leaf_directory_offset"] + entry.offset, entry.length)
                    )
    return sorted(ranges)


def _present(ranges, starts, first, count):
    """How many tile ids in [first, first + count) the sorted ranges hold."""
    end = first + count
    i = max(0, bisect_right(starts, first) - 1)
    present = 0
    while i < len(ranges) and ranges[i][0] < end:
        start, length = ranges[i]
        present += max(0, min(end, start + length) - max(first, start))
        i += 1
    return present


def cell_coverage(path, cells, zoom=ZOOM, void_tiles=None):
    """Per mask cell: how many of its zoom tiles the archive holds, and how many of those
    have nodata (None when the tiles were not decoded). For the system dashboard's map.

    A mask cell's zoom tiles are one subtree of the Hilbert curve, so their tile ids are one
    contiguous run: counting needs the directories only."""
    from pmtiles.tile import zxy_to_tileid

    ranges = tile_ranges(path)
    starts = [start for start, _ in ranges]
    shift = zoom - MASK_ZOOM
    per_cell = 4**shift
    base = zxy_to_tileid(zoom, 0, 0)
    mask_base = zxy_to_tileid(MASK_ZOOM, 0, 0)
    voids = None
    if void_tiles is not None:
        voids = Counter((vx >> shift, vy >> shift) for vx, vy in void_tiles)
    rows = []
    for x, y in sorted(cells):
        first = base + (zxy_to_tileid(MASK_ZOOM, x, y) - mask_base) * per_cell
        void = None if voids is None else voids[(x, y)]
        rows.append([x, y, _present(ranges, starts, first, per_cell), void])
    return {"zoom": zoom, "mask_zoom": MASK_ZOOM, "per_cell": per_cell, "cells": rows}


def bounds_cells(bounds, zoom=MASK_ZOOM):
    """Every mask cell in [west, south, east, north]: for terrain prepared for a bounding box,
    before cells.json existed."""
    west, south, east, north = bounds
    x0, y0 = (int(v) for v in tile_xy(west, north, zoom))
    x1, y1 = (int(v) for v in tile_xy(east, south, zoom))
    return {(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)}


def write_cell_coverage(directory, cells=None, void_tiles=None):
    """cell_coverage.json beside the archives. Not part of the terrain key."""
    directory = Path(directory)
    if cells is None and (directory / "cells.json").is_file():
        cells = {tuple(cell) for cell in json.loads((directory / "cells.json").read_text())}
    elif cells is None:
        cells = bounds_cells(json.loads((directory / "manifest.json").read_text())["bounds"])
    data = cell_coverage(directory / "terrain.pmtiles", cells, void_tiles=void_tiles)
    temporary = directory / f".cell_coverage-{os.getpid()}.json"
    temporary.write_text(json.dumps(data, separators=(",", ":")))
    temporary.replace(directory / "cell_coverage.json")
    return data


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


def prepare(pbf, root, dry_run=False, whole=False):
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

    if dry_run:
        with tempfile.TemporaryDirectory() as directory:
            for i, (source, zoom) in enumerate(extracts):
                print(
                    f"Terrain estimate [{i + 1}/{len(extracts)}]: "
                    f"{source['name']} zoom {zoom}",
                    flush=True,
                )
                if whole:
                    region_file = Path(directory) / "region.geojson"
                    region_file.write_text(region)
                    size = extract_piece(
                        source,
                        zoom,
                        Path(directory) / "estimate.pmtiles",
                        region_file,
                        dry_run=True,
                    )
                    print(
                        f"Terrain source {source['name']} zoom {zoom}: at most "
                        f"~{size / 1e6:.1f} MB payload, in one piece",
                        flush=True,
                    )
                else:
                    download_pieces(source, zoom, cells, Path(directory), dry_run=True)
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
            print(
                f"Terrain downloads: {i}/{len(extracts)} sources complete "
                f"({i / len(extracts):.0%}); "
                f"processing [{i + 1}/{len(extracts)}] {source['name']} zoom {zoom}",
                flush=True,
            )
            part = stage / (
                "fallback.pmtiles" if zoom == FALLBACK_ZOOM else f"part-{i}.pmtiles"
            )
            if part.exists():
                try:
                    run("pmtiles", "verify", part)
                    print(f"Reusing completed terrain extract: {part}", flush=True)
                except (OSError, subprocess.CalledProcessError):
                    part.unlink(missing_ok=True)
            if not part.exists() and whole:
                if not extract_whole(source, zoom, part, region_file):
                    if zoom == ZOOM:
                        continue
                    raise ValueError(f"No terrain tiles available for {part.name}")
            elif not part.exists():
                pieces = download_pieces(source, zoom, cells, stage / "pieces")
                if not pieces and zoom == ZOOM:
                    continue
                merge_pieces(pieces, part)
            parts.append(part)
        print(
            f"Terrain downloads: {len(extracts)}/{len(extracts)} sources complete (100%). "
            "Merging final terrain archive...",
            flush=True,
        )
        *parts, fallback_path = parts
        terrain = stage / "terrain.pmtiles"
        merge_pieces(parts, terrain)
        print("Terrain preparation: checking coverage and checksums...", flush=True)
        void_tiles = set()
        coverage = [
            verify_coverage(terrain, cells, void_out=void_tiles),
            verify_coverage(fallback_path, cells, FALLBACK_ZOOM),
        ]
        # Per cell, for the system dashboard's elevation layer.
        write_cell_coverage(
            stage, cells, void_tiles if coverage[0].get("decoded") else None
        )
        spec["sha256"] = digest(terrain)
        spec["fallback_sha256"] = digest(fallback_path)
        spec["coverage"] = coverage
        print("Terrain preparation: saving attribution and publishing...", flush=True)
        spec["attribution_url"] = "https://mapterhorn.com/attribution/"
        (stage / "attribution.json").write_text(
            json.dumps(download_json(ATTRIBUTION_URL), indent=2) + "\n"
        )
        (stage / "manifest.json").write_text(json.dumps(spec, indent=2) + "\n")
        # Rename the completed directory. Never overwrite another process's archive.
        stage.rename(target)
        # Retain downloaded pieces and source extracts alongside the published archives.
    temporary = root / f".current-{os.getpid()}"
    try:
        temporary.symlink_to(target.name, target_is_directory=True)
        temporary.replace(root / "current")
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Terrain ready: {target}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("prepare", "check", "cleanup", "coverage", "cells")
    )
    parser.add_argument("pbf", type=Path, nargs="?")
    parser.add_argument("--root", type=Path, default=Path("/osm_data/elevation"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--terrain", type=Path, help="coverage: the terrain directory (default: current)"
    )
    parser.add_argument(
        "--cells", type=Path, help="coverage: the road cells (default: the terrain's)"
    )
    parser.add_argument(
        "--cells-out",
        type=Path,
        help="check: also write the file's road cells there (the graph release keeps them)",
    )
    parser.add_argument(
        "--whole",
        action="store_true",
        help="one extract per source, no 1 GB pieces (the old way)",
    )
    args = parser.parse_args()
    if args.command == "cleanup":
        cleanup_downloads(args.root)
    elif args.command == "coverage":
        # Backfill for terrain prepared before cell_coverage.json existed. Terrain from before
        # the cell mask has no cells.json: pass the graph release's (--cells).
        directory = (args.terrain or args.root / "current").resolve(strict=True)
        cells = None
        if args.cells:
            cells = {tuple(cell) for cell in json.loads(args.cells.read_text())}
        data = write_cell_coverage(directory, cells)
        full = sum(1 for row in data["cells"] if row[2] == data["per_cell"])
        none = sum(1 for row in data["cells"] if row[2] == 0)
        print(
            f"Cell coverage written: {len(data['cells'])} cells, {full} complete at zoom "
            f"{ZOOM}, {none} fallback only: {directory / 'cell_coverage.json'}"
        )
    elif args.pbf is None:
        parser.error(f"the {args.command} command requires pbf")
    elif args.command == "cells":
        # Backfill for a graph release built before it kept its road cells.
        if not args.cells_out:
            parser.error("the cells command requires --cells-out")
        args.cells_out.write_text(cells_json(node_cells(args.pbf)))
        print(f"Road cells written: {args.cells_out}")
    elif args.command == "prepare":
        # Serialize preparation, including current-symlink changes, across containers.
        if args.dry_run:
            prepare(args.pbf, args.root, True, args.whole)
        else:
            import fcntl

            args.root.mkdir(parents=True, exist_ok=True)
            with (args.root / ".prepare.lock").open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                prepare(args.pbf, args.root, whole=args.whole)
    else:
        cells = node_cells(args.pbf) if args.cells_out else None
        check(args.root / "current", args.pbf, cells)
        if args.cells_out:
            args.cells_out.write_text(cells_json(cells))
        print("Terrain checksum and OSM extent match.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"Terrain preparation failed: {exc}") from exc
