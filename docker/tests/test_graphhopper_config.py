import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ConfigOverrideTests(unittest.TestCase):
    def test_every_override_is_declared_in_the_yaml(self):
        """Dropwizard's -Ddw. replaces a dotted key only if the yaml has it; any other is nested
        and GraphHopper never reads it (the zoom-12 fallback was dropped that way)."""
        entrypoint = (ROOT / "docker" / "graphhopper-entrypoint.sh").read_text()
        overrides = set(re.findall(r"-Ddw\.graphhopper\.([\w.]+)=", entrypoint))
        config = (ROOT / "data" / "graphhopper" / "graphhopper-config.yaml").read_text()
        declared = set(re.findall(r"^  ([\w.]+):", config, re.MULTILINE))
        self.assertIn("graph.elevation.pmtiles.fallback.location", overrides)
        self.assertEqual(overrides - declared, set())


if __name__ == "__main__":
    unittest.main()
