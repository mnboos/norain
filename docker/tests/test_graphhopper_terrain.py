"""Run with a Python environment containing pmtiles==3.8.1 and Pillow."""

import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

MODULE = Path(__file__).resolve().parents[1] / "graphhopper-terrain.py"
spec = importlib.util.spec_from_file_location("terrain", MODULE)
terrain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(terrain)


def archive(path, tiles):
    with path.open("wb") as stream:
        writer = Writer(stream)
        for x, y, color in tiles:
            image = Image.new("RGB", (512, 512), color)
            buffer = io.BytesIO()
            image.save(buffer, format="WEBP", lossless=True)
            writer.write_tile(zxy_to_tileid(15, x, y), buffer.getvalue())
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
            {"items": [planet, other, regional]}, [8, 46, 9, 47]
        )
        self.assertEqual(result, [regional])
        with self.assertRaisesRegex(ValueError, "No Mapterhorn"):
            terrain.select_sources({"items": [planet]}, [8, 46, 9, 47])

    def test_real_archive_missing_tile_and_void_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "terrain.pmtiles"
            archive(path, [(100, 100, (130, 0, 0))])  # 512 m
            terrain.verify_coverage(path, (100, 100, 100, 100))
            self.assertEqual(
                terrain.verify_coverage(path, (100, 100, 101, 100)),
                ["15/101/100"],
            )
            archive(path, [(100, 100, (0, 0, 0))])
            with self.assertRaisesRegex(ValueError, "Terrain void"):
                terrain.verify_coverage(path, (100, 100, 100, 100))

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
            path.write_bytes(b"corrupted")
            with self.assertRaisesRegex(ValueError, "checksum"):
                terrain.check(root)

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
                patch.object(
                    terrain,
                    "download_json",
                    return_value={"version": "test", "items": [source]},
                ),
                patch.object(terrain, "run", side_effect=OSError("interrupted")),
            ):
                with self.assertRaises(OSError):
                    terrain.prepare(Path("test.osm.pbf"), root)
            self.assertEqual((root / "current").resolve(), (root / "old").resolve())
            self.assertTrue(list(root.glob(".prepare-*")))


if __name__ == "__main__":
    unittest.main()
