"""Run with a Python environment containing pmtiles==3.8.1 and Pillow."""

import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from pmtiles.reader import MmapSource, Reader
from pmtiles.tile import TileType

MODULE = Path(__file__).resolve().parents[1] / "graphhopper-canopy.py"
spec = importlib.util.spec_from_file_location("canopy", MODULE)
canopy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canopy)

# A square wood of about 400 m in the middle of zoom-13 tile 4267/2883 (near Bern), and a tree row.
WOOD = {
    "type": "Feature",
    "properties": {"landuse": "forest"},
    "geometry": {
        "type": "Polygon",
        "coordinates": [
            [
                [7.530, 46.935],
                [7.536, 46.935],
                [7.536, 46.939],
                [7.530, 46.939],
                [7.530, 46.935],
            ]
        ],
    },
}
ROW = {
    "type": "Feature",
    "properties": {"natural": "tree_row", "height": "15 m"},
    "geometry": {
        "type": "LineString",
        "coordinates": [[7.520, 46.930], [7.525, 46.930]],
    },
}


def heights(png):
    with Image.open(io.BytesIO(png)) as image:
        red, green, _ = image.convert("RGB").split()
        return [
            r * 256 + g - 32768
            for r, g in zip(red.getdata(), green.getdata(), strict=True)
        ]


def pixel(lon, lat, tile):
    x, y = canopy.tile_xy(lon, lat)
    return round((x - tile[0]) * (canopy.TILE - 1)), round(
        (y - tile[1]) * (canopy.TILE - 1)
    )


class CanopyTests(unittest.TestCase):
    def test_height_reads_plain_metres_and_falls_back_to_the_kind(self):
        self.assertEqual(canopy.height({"landuse": "forest"}), 20)
        self.assertEqual(canopy.height({"natural": "wood", "height": "30"}), 30)
        self.assertEqual(canopy.height({"natural": "tree_row", "height": "15 m"}), 15)
        self.assertEqual(canopy.height({"natural": "tree_row", "height": "tall"}), 12)
        self.assertEqual(
            canopy.height({"natural": "wood", "height": "400"}), 20, "not a tree"
        )

    def test_woods_and_rows_become_heights_in_terrarium_tiles(self):
        with tempfile.TemporaryDirectory() as directory:
            broken = {
                **WOOD,
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[7.5, 46.9], [7.51, 46.9]],
                },
            }
            buckets = canopy.bucket_features([WOOD, ROW, broken], Path(directory))
            self.assertEqual(len(buckets), 1)
            with (Path(directory) / "{}-{}.jsonl".format(*buckets[0])).open() as handle:
                records = [json.loads(line) for line in handle]
            self.assertEqual(len(records), 2, "an unclosed wood is dropped")
            tiles = canopy.rasterise(records, buckets[0])
        tile = (
            int(canopy.tile_xy(7.533, 46.937)[0]),
            int(canopy.tile_xy(7.533, 46.937)[1]),
        )
        self.assertIn(tile, tiles)
        values = heights(canopy.terrarium(tiles[tile]))
        width = canopy.TILE

        def at(lon, lat):
            x, y = pixel(lon, lat, tile)
            return values[y * width + x]

        self.assertEqual(at(7.533, 46.937), 20, "inside the wood")
        self.assertEqual(at(7.540, 46.937), 0, "open land beside it")
        self.assertEqual(at(7.5225, 46.930), 15, "on the tree row")

    def test_archive_holds_only_tiles_with_trees(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            buckets = canopy.bucket_features([WOOD], root)
            output = root / "canopy.pmtiles"
            count = canopy.write_archive(output, buckets, root)
            with output.open("rb") as stream:
                reader = Reader(MmapSource(stream))
                header = reader.header()
                x, y = (int(v) for v in canopy.tile_xy(7.533, 46.937))
                self.assertEqual(header["tile_type"], TileType.PNG)
                self.assertEqual(
                    (header["min_zoom"], header["max_zoom"]), (canopy.ZOOM, canopy.ZOOM)
                )
                self.assertIsNotNone(reader.get(canopy.ZOOM, x, y))
                self.assertIsNone(reader.get(canopy.ZOOM, x + 3, y))
            self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
