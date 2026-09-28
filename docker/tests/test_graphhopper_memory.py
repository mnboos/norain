import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location(
    "memory", Path(__file__).resolve().parents[1] / "graphhopper-memory.py"
)
memory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory)


class MemoryTests(unittest.TestCase):
    def test_java_heap_sizes(self):
        self.assertEqual(memory.parse_java_size("36g"), 36 * memory.GIB)
        self.assertEqual(memory.parse_java_size("512M"), 512 * 1024**2)
        self.assertEqual(memory.parse_java_size("1024"), 1024)
        for value in ("", "0g", "1.5g", "12gb", "nope"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                memory.parse_java_size(value)

    def test_expected_heap_and_limit_pairs(self):
        memory.check_memory("6g", 8 * memory.GIB)
        memory.check_memory("36g", 44 * memory.GIB)

    def test_mismatched_heap_and_limit_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            r"JVM heap 36 GiB.*effective cgroup limit is 8 GiB.*at least 40g",
        ):
            memory.check_memory("36g", 8 * memory.GIB)

    def test_reads_v2_and_v1_cgroup_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing"
            v1 = root / "memory.limit_in_bytes"
            v1.write_text(str(8 * memory.GIB))
            self.assertEqual(memory.cgroup_memory_limit((missing, v1)), 8 * memory.GIB)

            v2 = root / "memory.max"
            v2.write_text("max")
            self.assertIsNone(memory.cgroup_memory_limit((v2, v1)))
            v2.write_text(str(44 * memory.GIB))
            self.assertEqual(memory.cgroup_memory_limit((v2, v1)), 44 * memory.GIB)

    def test_missing_or_v1_unlimited_limit_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing"
            self.assertIsNone(memory.cgroup_memory_limit((missing,)))
            unlimited = root / "memory.limit_in_bytes"
            unlimited.write_text(str(2**63 - 4096))
            self.assertIsNone(memory.cgroup_memory_limit((unlimited,)))
