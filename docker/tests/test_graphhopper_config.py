import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ConfigOverrideTests(unittest.TestCase):
    def test_every_override_is_declared_in_the_yaml(self):
        """Dropwizard's -Ddw. replaces a dotted key only if the yaml has it; any other is nested
        and GraphHopper never reads it (the zoom-12 fallback was dropped that way)."""
        # The host build (scripts/graphhopper-host-build.sh) repeats the import's overrides.
        scripts = ("docker/graphhopper-entrypoint.sh", "scripts/graphhopper-host-build.sh")
        overrides = {
            key
            for script in scripts
            for key in re.findall(r"-Ddw\.graphhopper\.([\w.]+)=", (ROOT / script).read_text())
        }
        config = (ROOT / "data" / "graphhopper" / "graphhopper-config.yaml").read_text()
        declared = set(re.findall(r"^  ([\w.]+):", config, re.MULTILINE))
        self.assertIn("graph.elevation.pmtiles.fallback.location", overrides)
        self.assertEqual(overrides - declared, set())


if __name__ == "__main__":
    unittest.main()
