from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "orchestrator"
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))

import approved_task_publisher_gate as gate


class KolPublisherTargetIdentityTests(unittest.TestCase):
    def _repo(self, **overrides):
        payload = {
            "full_name": "stvelikiy-star/kol-travel-platform",
            "private": False,
            "visibility": "public",
            "default_branch": "main",
            "owner": {"login": "stvelikiy-star"},
        }
        payload.update(overrides)
        return payload

    def test_current_public_kol_identity_is_accepted(self):
        project = Path("/tmp/kol")
        result = subprocess.CompletedProcess([], 0, json.dumps(self._repo()), "")
        with mock.patch.object(
            gate.publisher,
            "git_text",
            side_effect=[
                "https://github.com/stvelikiy-star/kol-travel-platform.git",
                "https://github.com/stvelikiy-star/kol-travel-platform.git",
            ],
        ), mock.patch.object(gate.publisher, "run", return_value=result) as run:
            gate._validate_publish_target(project)
        run.assert_called_once_with(
            ["gh", "api", "repos/stvelikiy-star/kol-travel-platform"]
        )

    def test_target_identity_fails_closed_on_visibility_owner_or_branch_drift(self):
        cases = [
            self._repo(private=True, visibility="private"),
            self._repo(visibility="internal"),
            self._repo(default_branch="develop"),
            self._repo(owner={"login": "someone-else"}),
            self._repo(full_name="someone-else/kol-travel-platform"),
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                result = subprocess.CompletedProcess([], 0, json.dumps(payload), "")
                with mock.patch.object(
                    gate.publisher,
                    "git_text",
                    side_effect=[
                        "https://github.com/stvelikiy-star/kol-travel-platform.git",
                        "https://github.com/stvelikiy-star/kol-travel-platform.git",
                    ],
                ), mock.patch.object(gate.publisher, "run", return_value=result):
                    with self.assertRaises(gate.publisher.PublisherError):
                        gate._validate_publish_target(Path("/tmp/kol"))

    def test_unexpected_fetch_or_push_origin_fails_before_github_api(self):
        with mock.patch.object(
            gate.publisher,
            "git_text",
            side_effect=[
                "https://github.com/stvelikiy-star/kol-travel-platform.git",
                "https://github.com/attacker/kol-travel-platform.git",
            ],
        ), mock.patch.object(gate.publisher, "run") as run:
            with self.assertRaises(gate.publisher.PublisherError):
                gate._validate_publish_target(Path("/tmp/kol"))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
