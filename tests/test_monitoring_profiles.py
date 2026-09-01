from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ORCHESTRATOR = Path(__file__).resolve().parents[1] / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

import monitoring_profiles as profiles


class MonitoringProfileTests(unittest.TestCase):
    def test_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "orchestrator").mkdir()
            self.assertEqual(profiles.load_monitoring_profiles(root, {"demo"}), {})

    def test_registered_profile_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "orchestrator").mkdir()
            (root / "orchestrator" / "monitoring_profiles.json").write_text(
                json.dumps({
                    "version": 1,
                    "projects": {
                        "demo": {"enabled": True, "probes": [{"id": "x", "kind": "path_exists", "path": "/tmp"}]}
                    },
                }),
                encoding="utf-8",
            )
            loaded = profiles.load_monitoring_profiles(root, {"demo"})
            self.assertIn("demo", loaded)

    def test_unregistered_project_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "orchestrator").mkdir()
            (root / "orchestrator" / "monitoring_profiles.json").write_text(
                json.dumps({
                    "version": 1,
                    "projects": {"ghost": {"enabled": True, "probes": []}},
                }),
                encoding="utf-8",
            )
            with self.assertRaises(profiles.MonitoringProfileError):
                profiles.load_monitoring_profiles(root, {"demo"})

    def test_bad_version_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "orchestrator").mkdir()
            (root / "orchestrator" / "monitoring_profiles.json").write_text(
                json.dumps({"version": 2, "projects": {}}), encoding="utf-8"
            )
            with self.assertRaises(profiles.MonitoringProfileError):
                profiles.load_monitoring_profiles(root, set())


if __name__ == "__main__":
    unittest.main()
