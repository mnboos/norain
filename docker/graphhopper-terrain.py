#!/usr/bin/env python3
"""Prepare immutable, verified Mapterhorn extracts. Never modifies a routing graph."""

import argparse
from contextlib import contextmanager
from functools import lru_cache
import hashlib
import io
import json
import math
import mmap
import os
from pathlib import Path
import subprocess
import urllib.request

ZOOM = 15
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


def select_sources(catalog, bounds):
    w, s, e, n = bounds
    sources = [
        item
        for item in catalog["items"]
        if item["min_zoom"] <= ZOOM <= item["max_zoom"]
        and item["min_lon"] < e
        and item["max_lon"] > w
        and item["min_lat"] < n
        and item["max_lat"] > s
    ]
    if not sources:
        raise ValueError(
            "No Mapterhorn zoom-15 archive covers this area. Choose an area with high-resolution coverage."
        )
    for item in sources:
        if not item["url"].startswith("https://download.mapterhorn.com/"):
            raise ValueError("Unexpected Mapterhorn download host.")
    return sorted(sources, key=lambda item: item["name"])


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


def verify_coverage(path, rect):
    from PIL import Image
    from pmtiles.tile import Compression, TileType

    missing = []
    with archive_reader(path) as reader:
        header = reader.header()
        if (
            header["tile_type"] != TileType.WEBP
            or header["tile_compression"] != Compression.NONE
        ):
            raise ValueError("Expected uncompressed Terrarium WebP tiles.")
        x0, y0, x1, y1 = rect
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                data = reader.get(ZOOM, x, y)
                if not data:
                    missing.append(f"{ZOOM}/{x}/{y}")
                    continue
                with Image.open(io.BytesIO(data)) as image:
                    image.load()
                    if image.size != (512, 512):
                        raise ValueError(
                            f"Unexpected terrain tile dimensions: {image.size}"
                        )
                    # Terrarium no-data is black (-32768 m). Reject voids rather than letting
                    # GraphHopper silently fill them or produce zero-height route segments.
                    rgb = image.convert("RGB")
                    if rgb.getextrema()[0][0] < 124:
                        raise ValueError(
                            f"Terrain void in {ZOOM}/{x}/{y}; supply complete terrain before importing."
                        )
    checked = (x1 - x0 + 1) * (y1 - y0 + 1)
    print(f"Verified {checked} zoom-{ZOOM} tile positions.", flush=True)
    if missing:
        print(
            f"WARNING: {len(missing)} Mapterhorn terrain tile(s) are missing; "
            "routes crossing them will have elevation gaps. "
            f"Examples: {', '.join(missing[:5])}",
            flush=True,
        )
    return missing


def check(directory, pbf=None):
    directory = Path(directory).resolve(strict=True)
    manifest = json.loads((directory / "manifest.json").read_text())
    if (
        manifest["zoom"] != ZOOM
        or digest(directory / "terrain.pmtiles") != manifest["sha256"]
    ):
        raise ValueError(
            "Terrain checksum or zoom mismatch. Run terrain preparation again."
        )
    if pbf is not None:
        bounds, rect = padded_bounds(osm_bounds(pbf))
        if bounds != manifest["bounds"] or list(rect) != manifest["tile_rect"]:
            raise ValueError(
                "Prepared terrain does not match this OSM extent. Prepare terrain for this file first."
            )
    return directory, manifest


def prepare(pbf, root, dry_run=False):
    bounds, rect = padded_bounds(osm_bounds(pbf))
    catalog = download_json(CATALOG_URL)
    sources = select_sources(catalog, bounds)
    spec = {
        "format": 1,
        "zoom": ZOOM,
        "bounds": bounds,
        "tile_rect": list(rect),
        "catalog_version": catalog["version"],
        "sources": sources,
    }
    key = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:24]
    target = root / key
    bbox = ",".join(str(v) for v in bounds)
    # Move bounds very slightly inside the outer tile edges so extraction does not
    # include an extra row due to inclusive bounds or floating-point roundoff.
    extract_bounds = [
        bounds[0] + 1e-9,
        bounds[1] + 1e-9,
        bounds[2] - 1e-9,
        bounds[3] - 1e-9,
    ]
    extract_bbox = ",".join(str(v) for v in extract_bounds)
    print(f"Zoom {ZOOM}; bounds {bbox}; {len(sources)} regional archives", flush=True)
    if dry_run:
        for source in sources:
            run(
                "pmtiles",
                "extract",
                source["url"],
                "/tmp/terrain-estimate.pmtiles",
                f"--bbox={extract_bbox}",
                f"--minzoom={ZOOM}",
                f"--maxzoom={ZOOM}",
                "--dry-run",
            )
        return
    root.mkdir(parents=True, exist_ok=True)
    # A failed download never replaces the previous current directory.
    if target.exists():
        check(target, pbf)
        print(f"Reusing verified terrain: {target}")
    else:
        # Keep completed source extracts across interruptions and verification failures.
        # They are content-addressed by the catalog/bounds spec and never become current
        # until the final merged archive has passed verification.
        stage = root / f".prepare-{key}"
        stage.mkdir(parents=True, exist_ok=True)
        parts = []
        for i, source in enumerate(sources):
            part = stage / f"part-{i}.pmtiles"
            if part.exists():
                try:
                    run("pmtiles", "verify", part)
                    print(f"Reusing completed terrain extract: {part}", flush=True)
                except (OSError, subprocess.CalledProcessError):
                    part.unlink(missing_ok=True)
            if not part.exists():
                run(
                    "pmtiles",
                    "extract",
                    source["url"],
                    part,
                    f"--bbox={extract_bbox}",
                    f"--minzoom={ZOOM}",
                    f"--maxzoom={ZOOM}",
                )
            parts.append(part)
        terrain = stage / "terrain.pmtiles"
        terrain.unlink(missing_ok=True)
        if len(parts) == 1:
            parts[0].rename(terrain)
        else:
            run("pmtiles", "merge", *parts, terrain)
        run("pmtiles", "verify", terrain)
        missing = verify_coverage(terrain, rect)
        for part in parts:
            part.unlink(missing_ok=True)
        spec["sha256"] = digest(terrain)
        spec["missing_tiles"] = missing
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
