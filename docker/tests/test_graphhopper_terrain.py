"""Run with a Python environment containing pmtiles==3.8.1 and Pillow."""

import importlib.util
import io
import json
import shutil
import subprocess
import sys
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
        for x, y, color in sorted(
            tiles, key=lambda tile: zxy_to_tileid(zoom, tile[0], tile[1])
        ):
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

    def test_cell_coverage_counts_present_and_void_tiles_per_cell(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "terrain.pmtiles"
            # Cell (6, 6): three tiles, one with nodata. Cell (7, 6): every tile.
            # Cell (6, 7): none, so it reads the fallback only.
            tiles = [
                (100, 100, (132, 0, 0)),
                (101, 100, (100, 0, 0)),
                (102, 100, (130, 0, 0)),
            ] + [(x, y, (132, 0, 0)) for x in range(112, 128) for y in range(96, 112)]
            archive(path, tiles)
            cells = {(6, 6), (7, 6), (6, 7)}
            void_tiles = set()
            terrain.verify_coverage(path, cells, void_out=void_tiles)
            self.assertEqual(void_tiles, {(102, 100)})
            result = terrain.cell_coverage(path, cells, void_tiles=void_tiles)
            self.assertEqual(result["per_cell"], 256)
            self.assertEqual(
                result["cells"], [[6, 6, 3, 1], [6, 7, 0, 0], [7, 6, 256, 0]]
            )
            # Without decoding, the counts come from the directories alone.
            self.assertEqual(
                [row[3] for row in terrain.cell_coverage(path, cells)["cells"]],
                [None, None, None],
            )

    def test_write_cell_coverage_reads_the_prepared_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive(root / "terrain.pmtiles", [(100, 100, (132, 0, 0))])
            (root / "cells.json").write_text(terrain.cells_json({(6, 6)}))
            terrain.write_cell_coverage(root)
            stored = json.loads((root / "cell_coverage.json").read_text())
            self.assertEqual(stored["cells"], [[6, 6, 1, None]])
            self.assertEqual(list(root.glob(".cell_coverage-*")), [])

    def test_bounding_box_terrain_uses_every_cell_in_its_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive(root / "terrain.pmtiles", [(100, 100, (132, 0, 0))])
            west, north = terrain.tile_lonlat(6, 6, terrain.MASK_ZOOM)
            east, south = terrain.tile_lonlat(8, 7, terrain.MASK_ZOOM)
            # Slightly inside: cells (6, 6) and (7, 6), one row.
            bounds = [west + 0.01, south + 0.01, east - 0.01, north - 0.01]
            (root / "manifest.json").write_text(json.dumps({"bounds": bounds}))
            self.assertEqual(terrain.bounds_cells(bounds), {(6, 6), (7, 6)})
            data = terrain.write_cell_coverage(root)
            self.assertEqual(data["cells"], [[6, 6, 1, None], [7, 6, 0, None]])

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
                patch.object(
                    terrain, "extract_piece", side_effect=OSError("interrupted")
                ),
            ):
                with self.assertRaises(OSError):
                    terrain.prepare(Path("test.osm.pbf"), root)
            self.assertEqual((root / "current").resolve(), (root / "old").resolve())
            self.assertTrue(list(root.glob(".prepare-*")))


class DownloadPieceTests(unittest.TestCase):
    def setUp(self):
        self.source = {
            "name": "test",
            "url": "https://download.mapterhorn.com/test.pmtiles",
        }

    def test_partition_is_deterministic_and_preserves_cells(self):
        cells = [(4, 7), (2, 7), (3, 7)]
        children = terrain.split_cells(cells, 11, 15)
        self.assertEqual(children, terrain.split_cells(list(reversed(cells)), 11, 15))
        self.assertEqual(
            sorted(c for group, _ in children for c in group), sorted(cells)
        )
        children = terrain.split_cells([(2, 7)], 11, 15)
        self.assertEqual(
            {c for group, _ in children for c in group},
            {(4, 14), (4, 15), (5, 14), (5, 15)},
        )
        self.assertTrue(all(z == 12 for _, z in children))
        with self.assertRaisesRegex(ValueError, "single terrain tile"):
            terrain.split_cells([(2, 7)], 12, 12)

    def test_cleanup_removes_only_published_download_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            published = root / "published"
            published.mkdir()
            (published / "manifest.json").write_text("{}")
            (published / "terrain.pmtiles").write_bytes(b"terrain")
            (published / "fallback.pmtiles").write_bytes(b"fallback")
            (published / "part-0.pmtiles").write_bytes(b"source")
            (published / "cache").mkdir()
            (published / "cache" / "tile").write_bytes(b"cache")
            (published / "pieces").mkdir()
            (published / "pieces" / "a.pmtiles").write_bytes(b"piece")
            (root / "current").symlink_to("published")
            staging = root / ".prepare-next"
            (staging / "pieces").mkdir(parents=True)
            (staging / "pieces" / "resume.pmtiles").write_bytes(b"resume")

            self.assertEqual(terrain.cleanup_downloads(root), (2, 11))
            self.assertFalse((published / "pieces").exists())
            self.assertFalse((published / "part-0.pmtiles").exists())
            self.assertEqual((published / "terrain.pmtiles").read_bytes(), b"terrain")
            self.assertEqual((published / "fallback.pmtiles").read_bytes(), b"fallback")
            self.assertEqual((published / "cache" / "tile").read_bytes(), b"cache")
            self.assertEqual(
                (staging / "pieces" / "resume.pmtiles").read_bytes(), b"resume"
            )
            self.assertTrue((root / "current").is_symlink())

    def test_cleanup_missing_root_is_a_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                terrain.cleanup_downloads(Path(directory) / "missing"), (0, 0)
            )

    def test_estimate_rounding_and_unknown_output(self):
        for message, expected in [
            ("Region tiles 1311488, result tile entries 0\narchive size of 0 B", 0),
            ("result tile entries 1\narchive size of 0 B", 1),
            ("archive size of 899 MB", 900_000_000),
            ("archive size of 1.1 GB", 1_200_000_000),
        ]:
            with patch.object(
                terrain,
                "extract_output",
                return_value=subprocess.CompletedProcess([], 0, message),
            ):
                result = terrain.extract_piece(
                    self.source, 15, Path("out"), Path("region"), True
                )
                self.assertAlmostEqual(result, expected)
        with (
            patch.object(
                terrain,
                "extract_output",
                return_value=subprocess.CompletedProcess([], 0, "unknown"),
            ),
            self.assertRaisesRegex(ValueError, "unbounded"),
        ):
            terrain.extract_piece(self.source, 15, Path("out"), Path("region"), True)

    def test_extract_passes_overfetch_and_threads(self):
        with (
            patch.object(terrain, "OVERFETCH", 0.4),
            patch.object(terrain, "DOWNLOAD_THREADS", 3),
            patch.object(
                terrain,
                "extract_output",
                return_value=subprocess.CompletedProcess([], 0, "archive size of 1 MB"),
            ) as extract,
        ):
            terrain.extract_piece(self.source, 15, Path("out"), Path("region"), True)
        args = extract.call_args.args[0]
        self.assertIn("--overfetch=0.4", args)
        self.assertIn("--download-threads=3", args)

    def test_stalled_extract_is_killed_and_retried(self):
        stalls = [
            sys.executable,
            "-c",
            "import sys, time\n"
            "sys.stdout.write('fetching chunks (5/9 MB, 1 MB/s)\\r'); sys.stdout.flush()\n"
            "sys.stdout.write('fetching chunks (5/9 MB, 9 kB/s)\\r'); sys.stdout.flush()\n"
            "time.sleep(30)",
        ]
        with patch.object(terrain, "STALL_SECONDS", 0.5):
            with self.assertRaises(terrain.DownloadStalled):
                terrain.extract_output(stalls)
            with (
                patch.object(
                    terrain,
                    "extract_output",
                    side_effect=[
                        terrain.DownloadStalled("stalled"),
                        subprocess.CompletedProcess([], 0, ""),
                    ],
                ) as extract,
                tempfile.TemporaryDirectory() as directory,
            ):
                output = Path(directory) / "out.pmtiles"
                output.write_bytes(b"x")
                terrain.extract_piece(self.source, 15, output, Path("region"))
            self.assertEqual(extract.call_count, 2)

    def test_failed_extract_is_retried_but_size_limit_is_not(self):
        reset = subprocess.CompletedProcess(
            [], 1, "stream error: stream ID 33; INTERNAL_ERROR; received from peer"
        )
        done = subprocess.CompletedProcess([], 0, "")
        too_large = subprocess.CompletedProcess([], 1, "write: file too large")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.pmtiles"
            output.write_bytes(b"x")
            with patch.object(
                terrain, "extract_output", side_effect=[reset, done]
            ) as extract:
                terrain.extract_piece(self.source, 15, output, Path("region"))
            self.assertEqual(extract.call_count, 2)
            with (
                patch.object(
                    terrain, "extract_output", side_effect=[reset] * 3
                ) as extract,
                self.assertRaises(subprocess.CalledProcessError),
            ):
                terrain.extract_piece(self.source, 15, output, Path("region"))
            self.assertEqual(extract.call_count, terrain.DOWNLOAD_ATTEMPTS)
            with (
                patch.object(
                    terrain, "extract_output", side_effect=[too_large, done]
                ) as extract,
                self.assertRaises(terrain.PieceTooLarge),
            ):
                terrain.extract_piece(self.source, 15, output, Path("region"))
            self.assertEqual(extract.call_count, 1)

    def test_extract_output_keeps_lines_and_return_code(self):
        result = terrain.extract_output(
            [sys.executable, "-c", "print('a'); print('b', end='\\r'); print('c', end='')"]
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "a\nb\nc")

    def test_empty_piece_skips_download_and_verification(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(terrain, "extract_piece", return_value=0) as extract,
            patch.object(terrain, "run") as run,
        ):
            root = Path(directory)
            self.assertEqual(terrain.download_pieces(self.source, 15, [(0, 0)], root), [])
            self.assertEqual(extract.call_count, 1)
            self.assertTrue(extract.call_args.kwargs["dry_run"])
            run.assert_not_called()
            self.assertFalse(list(root.glob("*.pmtiles")))

    def test_empty_merge_preserves_previous_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "terrain.pmtiles"
            output.write_bytes(b"previous")
            with self.assertRaisesRegex(ValueError, "No terrain tiles"):
                terrain.merge_pieces([], output)
            self.assertEqual(output.read_bytes(), b"previous")

    @unittest.skipUnless(shutil.which("pmtiles"), "requires pinned pmtiles CLI")
    def test_local_empty_region_is_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pmtiles"
            archive(source, [(1078 * 16, 719 * 16, (131, 10, 20))])
            parts = terrain.download_pieces(
                dict(self.source, url=str(source)), 15, [(1080, 719)], root / "pieces"
            )
            self.assertEqual(parts, [])

    def test_restart_reuses_completed_piece_after_failure(self):
        for zoom in (12, 15):
            with self.subTest(zoom=zoom), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                downloads = []

                def extract(
                    source, z, output, region, dry_run=False, downloads=downloads
                ):
                    polygons = json.loads(region.read_text())["coordinates"]
                    if dry_run:
                        return 1000 if len(polygons) > 1 else 10
                    downloads.append(output.name)
                    output.write_bytes(b"data")
                    if len(downloads) == 2:
                        raise OSError("connection lost")

                with (
                    patch.object(terrain, "ESTIMATE_PIECE_BYTES", 100),
                    patch.object(terrain, "extract_piece", side_effect=extract),
                    patch.object(terrain, "run"),
                ):
                    with self.assertRaises(OSError):
                        terrain.download_pieces(
                            self.source, zoom, [(0, 0), (2, 0)], root
                        )
                    first = downloads[0]
                    parts = terrain.download_pieces(
                        self.source, zoom, [(2, 0), (0, 0)], root
                    )
                    self.assertEqual(len(parts), 2)
                    self.assertEqual(downloads.count(first), 1)
                    self.assertFalse(list(root.glob("*.partial.pmtiles")))

    def test_hard_limit_splits_and_corrupt_cache_is_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            downloads = []

            def extract(source, zoom, output, region, dry_run=False):
                if dry_run:
                    return 1
                downloads.append(output)
                output.write_bytes(b"data")
                if len(downloads) == 1:
                    raise terrain.PieceTooLarge("too large")

            with (
                patch.object(terrain, "extract_piece", side_effect=extract),
                patch.object(terrain, "run"),
            ):
                parts = terrain.download_pieces(
                    self.source, 12, [(0, 0)], Path(directory)
                )
                self.assertEqual(len(parts), 4)
                self.assertFalse(list(Path(directory).glob("*.partial.pmtiles")))
            with (
                patch.object(terrain, "extract_piece", side_effect=extract),
                patch.object(
                    terrain,
                    "run",
                    side_effect=[
                        subprocess.CalledProcessError(1, "verify"),
                        None,
                        None,
                        None,
                        None,
                    ],
                ),
            ):
                terrain.download_pieces(self.source, 12, [(0, 0)], Path(directory))
            self.assertEqual(len(downloads), 6)

    def test_merge_failure_keeps_pieces_and_previous_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parts = [root / "a.pmtiles", root / "b.pmtiles"]
            for part in parts:
                part.write_bytes(b"piece")
            output = root / "terrain.pmtiles"
            output.write_bytes(b"previous")
            with (
                patch.object(terrain, "run", side_effect=OSError("merge failed")),
                self.assertRaises(OSError),
            ):
                terrain.merge_pieces(parts, output)
            self.assertEqual(output.read_bytes(), b"previous")
            self.assertTrue(all(part.exists() for part in parts))

    def test_metadata_failure_keeps_inputs_and_retry_reuses_legacy_extracts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "old").mkdir()
            (root / "current").symlink_to("old")
            source = dict(
                self.source,
                min_lon=5,
                max_lon=12,
                min_lat=45,
                max_lat=50,
                min_zoom=13,
                max_zoom=17,
            )
            catalog = {
                "version": "test",
                "items": [
                    source,
                    dict(source, name="empty"),
                    dict(source, name="planet", min_zoom=0, max_zoom=12),
                ],
            }

            def download(source, zoom, cells, directory):
                if source["name"] == "empty":
                    return []
                directory.mkdir(exist_ok=True)
                part = directory / f"{zoom}.pmtiles"
                part.write_bytes(b"piece")
                return [part]

            with (
                patch.object(
                    terrain, "osm_bounds", return_value=[9.51, 47.12, 9.54, 47.145]
                ),
                patch.object(terrain, "node_cells", return_value={(1078, 719)}),
                patch.object(terrain, "run"),
                patch.object(terrain, "verify_coverage", return_value={}),
                patch.object(terrain, "write_cell_coverage"),
                patch.object(
                    terrain, "download_pieces", side_effect=download
                ) as downloads,
                patch.object(
                    terrain,
                    "download_json",
                    side_effect=[catalog, OSError("attribution offline"), catalog, {}],
                ),
            ):
                with self.assertRaisesRegex(OSError, "attribution offline"):
                    terrain.prepare(Path("test.osm.pbf"), root)
                stage = next(root.glob(".prepare-*"))
                self.assertEqual((root / "current").resolve(), (root / "old").resolve())
                self.assertFalse((stage / "part-0.pmtiles").exists())
                self.assertTrue((stage / "part-1.pmtiles").exists())
                self.assertEqual(len(list((stage / "pieces").glob("*.pmtiles"))), 2)
                terrain.prepare(Path("test.osm.pbf"), root)
                self.assertEqual(downloads.call_count, 4)
                self.assertNotEqual(
                    (root / "current").resolve(), (root / "old").resolve()
                )
                published = root / "current"
                self.assertEqual(len(list((published / "pieces").glob("*.pmtiles"))), 2)
                self.assertEqual((published / "part-1.pmtiles").read_bytes(), b"piece")
                self.assertEqual(
                    (published / "fallback.pmtiles").read_bytes(), b"piece"
                )

    def test_dry_run_never_downloads_payloads(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(terrain, "extract_piece", return_value=10) as extract,
        ):
            terrain.download_pieces(
                self.source, 15, [(0, 0)], Path(directory), dry_run=True
            )
            self.assertTrue(
                all(call.kwargs.get("dry_run") for call in extract.call_args_list)
            )
            self.assertFalse(list(Path(directory).glob("*.pmtiles")))

    def test_whole_mode_uses_no_pieces_and_publishes_the_same_key(self):
        source = dict(
            self.source,
            min_lon=5,
            max_lon=12,
            min_lat=45,
            max_lat=50,
            min_zoom=13,
            max_zoom=17,
        )
        catalog = {
            "version": "test",
            "items": [source, dict(source, name="planet", min_zoom=0, max_zoom=12)],
        }

        def whole(source, zoom, output, region_file):
            output.write_bytes(b"whole")
            return True

        def pieces(source, zoom, cells, directory):
            directory.mkdir(exist_ok=True)
            part = directory / f"{zoom}.pmtiles"
            part.write_bytes(b"piece")
            return [part]

        published = []
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                terrain, "osm_bounds", return_value=[9.51, 47.12, 9.54, 47.145]
            ),
            patch.object(terrain, "node_cells", return_value={(1078, 719)}),
            patch.object(terrain, "run"),
            patch.object(terrain, "verify_coverage", return_value={}),
            patch.object(terrain, "write_cell_coverage"),
            patch.object(terrain, "download_json", side_effect=lambda url: catalog),
            patch.object(terrain, "extract_whole", side_effect=whole) as extract,
            patch.object(terrain, "download_pieces", side_effect=pieces) as download,
        ):
            for mode, is_whole in (("whole", True), ("pieces", False)):
                root = Path(directory) / mode
                terrain.prepare(Path("test.osm.pbf"), root, whole=is_whole)
                published.append((root / "current").resolve().name)
                if is_whole:
                    self.assertEqual(extract.call_count, 2)
                    download.assert_not_called()
                    self.assertEqual(
                        (root / "current" / "fallback.pmtiles").read_bytes(), b"whole"
                    )
                    self.assertFalse((root / "current" / "pieces").exists())
        self.assertEqual(published[0], published[1])

    def test_whole_mode_skips_an_empty_source(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(terrain, "extract_piece", return_value=0) as estimate,
            patch.object(terrain, "run") as run,
        ):
            output = Path(directory) / "part-0.pmtiles"
            self.assertFalse(
                terrain.extract_whole(self.source, 15, output, Path("region.geojson"))
            )
            self.assertTrue(estimate.call_args.kwargs["dry_run"])
            run.assert_not_called()
            self.assertFalse(output.exists())

    @unittest.skipUnless(shutil.which("pmtiles"), "requires pinned pmtiles CLI")
    def test_whole_mode_extracts_a_real_archive_in_one_piece(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pmtiles"
            archive(source, [(1078 * 16, 719 * 16, (131, 10, 20))])
            region = root / "region.geojson"
            region.write_text(
                json.dumps(terrain.region_geojson(terrain.cell_boxes([(1078, 719)])))
            )
            output = root / "part-0.pmtiles"
            # A 64-byte piece limit must not apply here.
            with patch.object(terrain, "MAX_PIECE_BYTES", 64):
                self.assertTrue(
                    terrain.extract_whole(
                        dict(self.source, url=str(source)), 15, output, region
                    )
                )
            self.assertGreater(output.stat().st_size, 64)
            self.assertFalse(list(root.glob("*.partial.pmtiles")))
            with terrain.archive_reader(output) as reader:
                self.assertTrue(reader.get(15, 1078 * 16, 719 * 16))

    @unittest.skipUnless(shutil.which("pmtiles"), "requires pinned pmtiles CLI")
    def test_cli_enforces_hard_limit_before_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pmtiles"
            archive(source, [(1078 * 16, 719 * 16, (131, 10, 20))])
            region = root / "region.geojson"
            region.write_text(
                json.dumps(terrain.region_geojson(terrain.cell_boxes([(1078, 719)])))
            )
            output = root / "partial.pmtiles"
            with (
                patch.object(terrain, "MAX_PIECE_BYTES", 64),
                self.assertRaises(terrain.PieceTooLarge),
            ):
                terrain.extract_piece(
                    dict(self.source, url=str(source)), 15, output, region
                )
            self.assertLessEqual(output.stat().st_size, 64)

    @unittest.skipUnless(shutil.which("pmtiles"), "requires pinned pmtiles CLI")
    def test_local_extraction_splits_and_merges_with_small_limit(self):
        for zoom in (12, 15):
            with self.subTest(zoom=zoom), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "source.pmtiles"
                # Nonadjacent cells exercise coverage boundaries; distinct payloads avoid deduplication.
                cells = [(1078, 719), (1080, 719)]
                k = 2 ** (zoom - terrain.MASK_ZOOM)
                tiles = [
                    (x * k, y * k, color)
                    for (x, y), color in zip(cells, [(131, 10, 20), (132, 30, 40)])
                ]
                archive(source, tiles, zoom)
                item = dict(self.source, url=str(source))
                with terrain.archive_reader(source) as reader:
                    payload = max(len(reader.get(zoom, x, y)) for x, y, _ in tiles)
                with (
                    patch.object(terrain, "ESTIMATE_PIECE_BYTES", payload + 1),
                    patch.object(terrain, "MAX_PIECE_BYTES", 4096),
                ):
                    parts = terrain.download_pieces(item, zoom, cells, root / "pieces")
                self.assertEqual(len(parts), 2)
                self.assertTrue(all(p.stat().st_size <= 4096 for p in parts))
                merged = root / "merged.pmtiles"
                terrain.merge_pieces(parts, merged)
                with (
                    terrain.archive_reader(source) as original,
                    terrain.archive_reader(merged) as result,
                ):
                    for x, y, _ in tiles:
                        self.assertEqual(
                            result.get(zoom, x, y), original.get(zoom, x, y)
                        )
                    self.assertIsNone(
                        result.get(zoom, (cells[0][0] + 1) * k, cells[0][1] * k)
                    )


if __name__ == "__main__":
    unittest.main()
