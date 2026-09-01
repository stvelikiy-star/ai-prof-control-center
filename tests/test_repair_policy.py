from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ORCHESTRATOR = Path(__file__).resolve().parents[1] / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

import repair_policy


class RepairPolicyTests(unittest.TestCase):
    def _root(self, policy_projects: dict) -> Path:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "orchestrator").mkdir()
        (root / "orchestrator" / "projects.json").write_text(
            json.dumps({"version": 1, "projects": [{
                "project_id": "demo",
                "path": "/tmp/demo",
                "base_branch": "main",
                "allowed_base_branches": ["main"],
                "enabled": True,
            }]}), encoding="utf-8"
        )
        (root / "orchestrator" / "monitoring_profiles.json").write_text(
            json.dumps({"version": 1, "projects": {"demo": {
                "enabled": True,
                "probes": [{"id": "heartbeat", "kind": "path_exists", "path": "/tmp/demo"}],
            }}}), encoding="utf-8"
        )
        (root / "orchestrator" / "repair_policies.json").write_text(
            json.dumps({"version": 1, "projects": policy_projects}), encoding="utf-8"
        )
        return root

    def test_explicit_yellow_policy(self):
        root = self._root({"demo": {"heartbeat": "YELLOW"}})
        self.assertEqual(repair_policy.classify(root, "demo", "heartbeat"), "YELLOW")

    def test_missing_policy_defaults_red(self):
        root = self._root({"demo": {}})
        self.assertEqual(repair_policy.classify(root, "demo", "heartbeat"), "RED")

    def test_unknown_probe_in_policy_is_rejected(self):
        root = self._root({"demo": {"ghost": "GREEN"}})
        with self.assertRaises(repair_policy.RepairPolicyError):
            repair_policy.load_repair_policies(root)

    def test_invalid_class_is_rejected(self):
        root = self._root({"demo": {"heartbeat": "AUTO"}})
        with self.assertRaises(repair_policy.RepairPolicyError):
            repair_policy.load_repair_policies(root)


if __name__ == "__main__":
    unittest.main()
