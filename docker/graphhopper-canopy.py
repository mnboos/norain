"""Tree heights for "avoid shade": an archive GraphHopper reads beside the terrain.

GraphHopper's shade check (docker/graphhopper/weather/Shade.java) looks from the road towards
the sun over the terrain, and within a few hundred metres over the trees on top of it. This
script makes the trees: the woods, forests and tree rows of raw OSM extracts, rasterised as
heights above the ground into zoom-13 Terrarium PNG tiles (about 13 m a pixel in Switzerland),
the same encoding the terrain uses, so GraphHopper reads both with one provider. Only tiles
with trees in them are stored; a missing tile is open land.

OSM rarely knows how tall a wood is, so ``height`` is read where it is a plain number of
metres and the kind's typical height (``HEIGHTS``) is used everywhere else. That is good
enough to tell a road in a wood or beside its sunny edge from one in the open, which is all
the routing asks.

The bike-filtered file has no woods: run it on the raw extracts, like the POI extraction.

    graphhopper-canopy.py OUT.pmtiles IN.osm.pbf [IN.osm.pbf ...]
"""

import io
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

ZOOM = 13
# Features are sorted into buckets of this zoom first, so only one bucket's tiles are ever in memory.
BUCKET_ZOOM = 7
TILE = 256
MAX_LAT = 85.05112878
HEIGHTS = {"forest": 20, "wood": 20, "tree_row": 12}
TREE_ROW_WIDTH_M = 8
MAX_HEIGHT = 80
FILTERS = ("nwr/landuse=forest", "nwr/natural=wood", "w/natural=tree_row")


def tile_xy(lon, lat, zoom=ZOOM):
    """Fractional tile coordinates (x east, y south) in Web Mercator."""
    lat = max(-MAX_LAT, min(MAX_LAT, lat))
    n = 2**zoom
    return (lon + 180) / 360 * n, (
        1 - math.asinh(math.tan(math.radians(lat))) / math.pi
    ) / 2 * n


def kind(properties):
    if properties.get("natural") == "tree_row":
        return "tree_row"
    return "forest" if properties.get("landuse") == "forest" else "wood"


def height(properties):
    """The tagged height when it is a plain number of metres, else the kind's typical one."""
    value = str(properties.get("height", "")).strip().removesuffix("m").strip()
    try:
        metres = float(value)
    except ValueError:
        return HEIGHTS[kind(properties)]
    return HEIGHTS[kind(properties)] if not 2 <= metres <= MAX_HEIGHT else round(metres)


def rings(geometry):
    """(outer, holes) per polygon, or the line itself as an outer with no holes."""
    kind_, coordinates = geometry["type"], geometry["coordinates"]
    if kind_ == "Polygon":
        return [(coordinates[0], coordinates[1:])]
    if kind_ == "MultiPolygon":
        return [(polygon[0], polygon[1:]) for polygon in coordinates]
    if kind_ == "LineString":
        return [(coordinates, [])]
    return []


def tile_range(points, zoom):
    xs, ys = zip(*(tile_xy(lon, lat, zoom) for lon, lat in points), strict=True)
    return int(min(xs)), int(max(xs)), int(min(ys)), int(max(ys))


def bucket_features(features, directory):
    """Sort features into one file per bucket tile, so each bucket rasterises on its own."""
    handles = {}
    try:
        for feature in features:
            geometry = feature.get("geometry") or {}
            properties = feature.get("properties") or {}
            parts = rings(geometry)
            # A wood drawn as an unclosed way is broken data, not a row of trees.
            if not parts or (geometry["type"] == "LineString") != (
                kind(properties) == "tree_row"
            ):
                continue
            record = json.dumps(
                {
                    "h": height(properties),
                    "line": geometry["type"] == "LineString",
                    "parts": parts,
                }
            )
            x0, x1, y0, y1 = tile_range(
                [p for outer, _ in parts for p in outer], BUCKET_ZOOM
            )
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    if (x, y) not in handles:
                        handles[(x, y)] = (directory / f"{x}-{y}.jsonl").open("w")
                    handles[(x, y)].write(record + "\n")
    finally:
        for handle in handles.values():
            handle.close()
    return sorted(handles)


def rasterise(records, bucket):
    """{(x, y): heights image} for the zoom-13 tiles of one bucket that have trees in them.

    Pixel i of a tile sits at fraction i / (TILE - 1), as GraphHopper samples it. Lower trees
    are drawn first, so where two woods overlap the taller one wins.
    """
    from PIL import Image, ImageDraw

    scale = 2 ** (ZOOM - BUCKET_ZOOM)
    bx, by = bucket
    images = {}
    for record in sorted(records, key=lambda r: r["h"]):
        points = [p for outer, _ in record["parts"] for p in outer]
        x0, x1, y0, y1 = tile_range(points, ZOOM)
        for tx in range(max(x0, bx * scale), min(x1, bx * scale + scale - 1) + 1):
            for ty in range(max(y0, by * scale), min(y1, by * scale + scale - 1) + 1):
                image = images.get((tx, ty))
                if image is None:
                    image = images[(tx, ty)] = Image.new("L", (TILE, TILE), 0)
                draw = ImageDraw.Draw(image)

                def pixels(ring, tx=tx, ty=ty):
                    return [
                        ((x - tx) * (TILE - 1), (y - ty) * (TILE - 1))
                        for x, y in (tile_xy(lon, lat) for lon, lat in ring)
                    ]

                for outer, holes in record["parts"]:
                    if record["line"]:
                        lat = outer[0][1]
                        metres_per_pixel = (
                            40_075_016
                            * math.cos(math.radians(lat))
                            / (2**ZOOM * (TILE - 1))
                        )
                        width = max(1, round(TREE_ROW_WIDTH_M / metres_per_pixel))
                        draw.line(pixels(outer), fill=record["h"], width=width)
                        continue
                    draw.polygon(pixels(outer), fill=record["h"])
                    for hole in holes:
                        draw.polygon(pixels(hole), fill=0)
    return {key: image for key, image in images.items() if image.getbbox() is not None}


def terrarium(image):
    """A heights image as a Terrarium PNG: height + 32768 = red × 256 + green."""
    from PIL import Image

    zero = Image.new("L", image.size, 0)
    red = image.point(lambda h: (h + 32768) // 256)
    green = image.point(lambda h: (h + 32768) % 256)
    buffer = io.BytesIO()
    Image.merge("RGB", (red, green, zero)).save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def write_archive(output, buckets, directory):
    from pmtiles.tile import Compression, TileType, zxy_to_tileid
    from pmtiles.writer import Writer

    temporary = output.with_suffix(".partial.pmtiles")
    count = 0
    with temporary.open("wb") as stream:
        writer = Writer(stream)
        for bucket in buckets:
            with (directory / f"{bucket[0]}-{bucket[1]}.jsonl").open() as handle:
                tiles = rasterise((json.loads(line) for line in handle), bucket)
            for (x, y), image in sorted(
                tiles.items(), key=lambda item: zxy_to_tileid(ZOOM, *item[0])
            ):
                writer.write_tile(zxy_to_tileid(ZOOM, x, y), terrarium(image))
                count += 1
        if not count:
            raise SystemExit("No woods in the input: nothing to write.")
        writer.finalize(
            {"tile_type": TileType.PNG, "tile_compression": Compression.NONE},
            {
                "name": "MeteoLane canopy",
                "attribution": "© OpenStreetMap contributors",
                "zoom": ZOOM,
            },
        )
    temporary.rename(output)
    return count


def extract(inputs, directory):
    """The woods of every input as one GeoJSON-sequence stream (osmium, like extract-pois.sh)."""
    parts = []
    for i, source in enumerate(inputs):
        part = directory / f"{i}.osm.pbf"
        subprocess.run(
            ["osmium", "tags-filter", source, *FILTERS, "--overwrite", "-o", part],
            check=True,
        )
        parts.append(part)
    merged = parts[0]
    if len(parts) > 1:
        # Border objects can come in different versions from extracts made at different times.
        merged = directory / "all.osm.pbf"
        history = subprocess.Popen(
            ["osmium", "merge", "-H", *parts, "--output-format", "pbf"],
            stdout=subprocess.PIPE,
        )
        subprocess.run(
            ["osmium", "time-filter", "-F", "pbf", "--overwrite", "-o", merged, "-"],
            stdin=history.stdout,
            check=True,
        )
        if history.wait():
            raise subprocess.CalledProcessError(history.returncode, "osmium merge")
    features = directory / "woods.geojsonseq"
    subprocess.run(
        [
            "osmium",
            "export",
            merged,
            "-f",
            "geojsonseq",
            "--geometry-types=polygon,linestring",
            "-x",
            "print_record_separator=false",
            "--overwrite",
            "-o",
            features,
        ],
        check=True,
    )
    return features


def main(argv):
    if len(argv) < 2:
        raise SystemExit(
            "Usage: graphhopper-canopy.py OUT.pmtiles IN.osm.pbf [IN.osm.pbf ...]"
        )
    output, inputs = Path(argv[0]), argv[1:]
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".canopy-") as temporary:
        directory = Path(temporary)
        features = extract(inputs, directory)
        buckets_dir = directory / "buckets"
        buckets_dir.mkdir()
        with features.open() as handle:
            buckets = bucket_features(
                (json.loads(line) for line in handle if line.strip()), buckets_dir
            )
        count = write_archive(output, buckets, buckets_dir)
    print(f"Wrote {count} tiles with trees to {output}")


if __name__ == "__main__":
    main(sys.argv[1:])
