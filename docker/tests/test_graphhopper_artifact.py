import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "artifact", Path(__file__).resolve().parents[1] / "graphhopper-artifact.py"
)
artifact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifact)


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.revision = self.root / "revision"
        self.revision.write_text("test-revision\n")
        self.patches = [
            patch.object(artifact, "ROOT", self.root),
            patch.object(artifact, "REVISION", self.revision),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def release(self, name, status="validated"):
        path = self.root / "releases" / name
        (path / "models").mkdir(parents=True)
        (path / "graph").mkdir()
        (path / "graph" / "properties").write_text("test")
        (path / "config.yaml").write_text("graphhopper: {}")
        (path / "models" / "bike.json").write_text("{}")
        artifact.write_manifest(
            path,
            {
                "revision": "test-revision",
                "config_sha256": artifact.fingerprint(path),
                "status": status,
            },
        )
        return path

    def invoke(self, *args):
        with patch("sys.argv", ["artifact.py", *args]):
            artifact.main()

    def test_activation_retains_previous_and_rollback_restores_it(self):
        first, second = self.release("first"), self.release("second")
        artifact.link("current", first)
        artifact.link("candidate", second)
        self.invoke("activate", "candidate")
        self.assertEqual(artifact.selected("current"), second)
        self.assertEqual(artifact.selected("previous"), first)
        self.invoke("rollback", "previous")
        self.assertEqual(artifact.selected("current"), first)
        self.assertEqual(artifact.read(first)["status"], "validated")

    def test_unvalidated_or_wrong_revision_never_replaces_current(self):
        old = self.release("old")
        candidate = self.release("candidate", "built")
        artifact.link("current", old)
        artifact.link("candidate", candidate)
        with self.assertRaisesRegex(ValueError, "Validate"):
            self.invoke("activate", "candidate")
        self.revision.write_text("another-revision")
        with self.assertRaisesRegex(ValueError, "different GraphHopper"):
            self.invoke("ready", "candidate")
        self.assertEqual(artifact.selected("current"), old)

    def test_config_mutation_and_missing_graph_rejected(self):
        path = self.release("test")
        (path / "models" / "bike.json").write_text('{"changed": true}')
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            artifact.read(path)
        (path / "graph" / "properties").unlink()
        with self.assertRaisesRegex(ValueError, "files are missing"):
            artifact.read(path)

    def test_incomplete_import_does_not_publish_candidate(self):
        old = self.release("old")
        incomplete = self.release("incomplete", "building")
        artifact.link("candidate", old)
        (incomplete / "graph" / "properties").unlink()
        with self.assertRaises(ValueError):
            self.invoke("finish", "releases/incomplete")
        self.assertEqual(artifact.selected("candidate"), old)

    def test_finish_publishes_a_built_manifest(self):
        new = self.release("new", "building")
        self.invoke("finish", "releases/new")
        self.assertEqual(artifact.selected("candidate"), new)
        self.assertEqual(artifact.read(new)["status"], "built")

    def test_external_artifact_rejected(self):
        with self.assertRaises(ValueError):
            artifact.selected(str(self.root.parent))
