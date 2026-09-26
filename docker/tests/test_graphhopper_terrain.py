"""Run with a Python environment containing pmtiles==3.8.1 and Pillow."""

import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

MODULE = Path(__file__).resolve().parents[1] / "graphhopper-terrain.py"
spec = importlib.util.spec_from_file_location("terrain", MODULE)
terrain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(terrain)


def archive(path, tiles, zoom=15):
    with path.open("wb") as stream:
        writer = Writer(stream)
        for x, y, color in tiles:
            image = Image.new("RGB", (512, 512), color)
            if color == (130, 0, 0):
                image.putpixel((7, 9), (0, 0, 0))  # a single nodata pixel
            buffer = io.BytesIO()
            image.save(buffer, format="WEBP", lossless=True)
            writer.write_tile(zxy_to_tileid(zoom, x, y), buffer.getvalue())
        writer.finalize(
            {"tile_type": TileType.WEBP, "tile_compression": Compression.NONE}, {}
        )


class TerrainTests(unittest.TestCase):
    def test_bounds_add_a_full_tile_margin(self):
        bounds, rect = terrain.padded_bounds([9.51, 47.12, 9.54, 47.145])
        self.assertLess(bounds[0], 9.51)
        self.assertGreater(bounds[3], 47.145)
        x, y = terrain.tile_xy(9.51, 47.145)
        self.assertEqual(rect[:2], (int(x) - 1, int(y) - 1))

    def test_invalid_geographic_bounds(self):
        for bounds in ([170, 1, -170, 2], [0, 86, 1, 87], [1, 2, 1, 3]):
            with self.assertRaises(ValueError):
                terrain.padded_bounds(bounds)

    def test_only_intersecting_zoom15_archives_are_selected(self):
        regional = {
            "name": "region",
            "url": "https://download.mapterhorn.com/region.pmtiles",
            "min_zoom": 13,
            "max_zoom": 17,
            "min_lon": 5,
            "max_lon": 12,
            "min_lat": 45,
            "max_lat": 50,
        }
        planet = dict(regional, name="planet", min_zoom=0, max_zoom=12)
        other = dict(regional, name="other", min_lon=15, max_lon=20)
        result = terrain.select_sources(
            {"items": [planet, other, regional]}, [[8, 46, 9, 47], [30, 60, 31, 61]]
        )
        self.assertEqual(result, [regional])
        with self.assertRaisesRegex(ValueError, "No Mapterhorn"):
            terrain.select_sources({"items": [planet]}, [[8, 46, 9, 47]])
        self.assertEqual(
            terrain.select_fallback({"items": [planet, other, regional]}), planet
        )
        with self.assertRaisesRegex(ValueError, "zoom 12"):
            terrain.select_fallback({"items": [regional]})

    def test_cells_come_from_node_locations(self):
        lines = [
            "n1 T x9.5210000 y47.1300000",  # Liechtenstein
            "n2 T x9.5220000 y47.1310000",  # same cell
            "n3 T x1.5200000 y42.5100000",  # Andorra
            "n4 T x y",  # no location
        ]
        cells = terrain.cells_from_opl(lines)
        self.assertEqual(len(cells), 2)
        x, y = terrain.tile_xy(9.521, 47.13, terrain.MASK_ZOOM)
        self.assertIn((int(x), int(y)), cells)

    def test_adjacent_cells_in_a_row_become_one_box(self):
        boxes = terrain.cell_boxes({(10, 5), (11, 5), (12, 5), (20, 5), (10, 6)})
        self.assertEqual(len(boxes), 3)
        west, _, east, _ = boxes[0]
        self.assertAlmostEqual(west, terrain.tile_lonlat(10, 5, terrain.MASK_ZOOM)[0])
        self.assertAlmostEqual(east, terrain.tile_lonlat(13, 5, terrain.MASK_ZOOM)[0])
        region = terrain.region_geojson(boxes)
        self.assertEqual(region["type"], "MultiPolygon")
        self.assertEqual(len(region["coordinates"]), 3)

    def test_cell_tiles_at_each_zoom(self):
        self.assertEqual(len(terrain.cell_tiles({(6, 6)}, 15)), 256)
        self.assertEqual(
            terrain.cell_tiles({(6, 6)}, 12), [(12, 12), (12, 13), (13, 12), (13, 13)]
        )

    def test_real_archive_missing_tile_and_void_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "terrain.pmtiles"
            # 512 m, deep sea (-7168 m, not nodata), one nodata pixel in a 512 m tile.
            archive(
                path,
                [
                    (100, 100, (132, 0, 0)),
                    (101, 100, (100, 0, 0)),
                    (102, 100, (130, 0, 0)),
                ],
            )
            # Cell (6, 6) at zoom 11 holds zoom-15 tiles 96..111 in each direction.
            result = terrain.verify_coverage(path, {(6, 6)})
            self.assertEqual(
                (result["positions"], result["missing"], result["void"]), (256, 253, 1)
            )
            self.assertIn("15/102/100", result["examples"])

    def test_fallback_is_checked_at_its_own_zoom(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fallback.pmtiles"
            archive(path, [(12, 12, (132, 0, 0))], zoom=12)
            result = terrain.verify_coverage(path, {(6, 6)}, 12)
            self.assertEqual((result["positions"], result["missing"]), (4, 3))

    def test_large_areas_are_not_decoded_tile_by_tile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "terrain.pmtiles"
            archive(path, [(100, 100, (132, 0, 0))])
            with patch.object(terrain, "VERIFY_MAX_TILES", 1):
                result = terrain.verify_coverage(path, {(6, 6)})
            self.assertFalse(result["decoded"])

    def test_checksum_and_extent_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "terrain.pmtiles"
            path.write_bytes(b"terrain")
            bounds, rect = terrain.padded_bounds([9.51, 47.12, 9.54, 47.145])
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "zoom": 15,
                        "sha256": terrain.digest(path),
                        "bounds": bounds,
                        "tile_rect": list(rect),
                    }
                )
            )
            terrain.check(root)
            with patch.object(terrain, "osm_bounds", return_value=[8, 46, 9, 47]):
                with self.assertRaisesRegex(ValueError, "does not match"):
                    terrain.check(root, "different.osm.pbf")
            fallback = root / "fallback.pmtiles"
            fallback.write_bytes(b"fallback")
            manifest = json.loads((root / "manifest.json").read_text())
            manifest["fallback_sha256"] = terrain.digest(fallback)
            (root / "manifest.json").write_text(json.dumps(manifest))
            terrain.check(root)
            fallback.write_bytes(b"corrupted")
            with self.assertRaisesRegex(ValueError, "Fallback terrain checksum"):
                terrain.check(root)
            path.write_bytes(b"corrupted")
            with self.assertRaisesRegex(ValueError, "checksum"):
                terrain.check(root)

    def test_node_cells_must_be_a_subset_of_the_prepared_ones(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "terrain.pmtiles").write_bytes(b"terrain")
            content = terrain.cells_json({(1, 2), (3, 4)})
            (root / "cells.json").write_text(content)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "zoom": 15,
                        "sha256": terrain.digest(root / "terrain.pmtiles"),
                        "mask_zoom": terrain.MASK_ZOOM,
                        "cells_sha256": terrain.digest(root / "cells.json"),
                    }
                )
            )
            terrain.check(root, "fewer.osm.pbf", {(1, 2)})
            with patch.object(terrain, "node_cells", return_value={(3, 4)}):
                terrain.check(root, "fewer.osm.pbf")
            with self.assertRaisesRegex(ValueError, "does not match"):
                terrain.check(root, "more.osm.pbf", {(1, 2), (5, 6)})

    def test_failed_download_keeps_current_and_preserves_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ((root / "old").resolve()).mkdir()
            (root / "current").symlink_to("old")
            source = {
                "name": "region",
                "url": "https://download.mapterhorn.com/region.pmtiles",
                "min_zoom": 13,
                "max_zoom": 17,
                "min_lon": 5,
                "max_lon": 12,
                "min_lat": 45,
                "max_lat": 50,
            }
            with (
                patch.object(
                    terrain, "osm_bounds", return_value=[9.51, 47.12, 9.54, 47.145]
                ),
                patch.object(terrain, "node_cells", return_value={(1078, 719)}),
                patch.object(
                    terrain,
                    "download_json",
                    return_value={
                        "version": "test",
                        "items": [
                            source,
                            dict(source, name="planet", min_zoom=0, max_zoom=12),
                        ],
                    },
                ),
                patch.object(terrain, "run", side_effect=OSError("interrupted")),
            ):
                with self.assertRaises(OSError):
                    terrain.prepare(Path("test.osm.pbf"), root)
            self.assertEqual((root / "current").resolve(), (root / "old").resolve())
            self.assertTrue(list(root.glob(".prepare-*")))


if __name__ == "__main__":
    unittest.main()
