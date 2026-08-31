#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import approved_task_publisher as publisher
import control_loop_service
import resort_os_approved_task_publisher_gate as gate


class ResortOsApprovedPublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self._publisher_state = {
            "KOL_PROJECT": publisher.KOL_PROJECT,
            "KOL_REPOSITORY": publisher.KOL_REPOSITORY,
            "KOL_BASE_BRANCH": publisher.KOL_BASE_BRANCH,
            "OWNER": publisher.OWNER,
        }
        gate.configure_resort_os_profile()

    def tearDown(self) -> None:
        for name, value in self._publisher_state.items():
            setattr(publisher, name, value)

    @staticmethod
    def sample(branch: str = "feature/chatgpt-issue-140") -> str:
        issue = branch.rsplit("-", 1)[-1] if branch.startswith("feature/chatgpt-issue-") else "140"
        return "\n".join(
            [
                "Task-ID: RESORT_OS_20260827T000000Z_ABCDEF",
                "Project-Path: /home/agent/projects/resort-os",
                "Base-Branch: main",
                f"Work-Branch: {branch}",
                "Goal: smoke",
                f"Instructions: Source: authorized private GitHub task issue #{issue}.",
                "Scope-Files: apps/web/a.ts, knowledge/04_CURRENT_STATE.md",
            ]
        )

    def test_profile_is_exact_and_non_production(self) -> None:
        self.assertEqual(gate.RESORT_OS_PROJECT, Path("/home/agent/projects/resort-os"))
        self.assertEqual(gate.RESORT_OS_REPOSITORY, "stvelikiy-star/resort-os")
        self.assertEqual(gate.RESORT_OS_BASE_BRANCH, "main")
        self.assertEqual(publisher.KOL_PROJECT, gate.RESORT_OS_PROJECT)
        self.assertEqual(publisher.KOL_REPOSITORY, gate.RESORT_OS_REPOSITORY)
        self.assertEqual(publisher.KOL_BASE_BRANCH, "main")

    def test_github_gateway_contract_is_accepted(self) -> None:
        task_id, branch, issue, scopes = publisher.validate_supported_task(self.sample())
        self.assertTrue(task_id.startswith("RESORT_OS_"))
        self.assertEqual(branch, "feature/chatgpt-issue-140")
        self.assertEqual(issue, 140)
        self.assertEqual(scopes, ["apps/web/a.ts", "knowledge/04_CURRENT_STATE.md"])

    def test_arbitrary_branch_is_rejected(self) -> None:
        for branch in ("feature/arbitrary", "fix/chatgpt-issue-140", "main"):
            with self.subTest(branch=branch):
                with self.assertRaises(publisher.PublisherError):
                    publisher.validate_supported_task(self.sample(branch))

    def test_wrong_project_is_rejected(self) -> None:
        text = self.sample().replace(
            "/home/agent/projects/resort-os",
            "/tmp/not-resort-os",
        )
        with self.assertRaises(publisher.PublisherError):
            publisher.validate_supported_task(text)

    def test_target_gate_rejects_wrong_origin_before_github(self) -> None:
        with patch.object(
            publisher,
            "git_text",
            side_effect=["https://example.invalid/wrong.git", "https://example.invalid/wrong.git"],
        ), patch.object(publisher, "run") as run_mock:
            with self.assertRaises(publisher.PublisherError):
                gate._validate_publish_target(gate.RESORT_OS_PROJECT)
            run_mock.assert_not_called()

    def test_target_gate_rejects_public_repository(self) -> None:
        repo_payload = {
            "full_name": gate.RESORT_OS_REPOSITORY,
            "private": False,
            "default_branch": "main",
            "owner": {"login": gate.OWNER},
        }
        with patch.object(
            publisher,
            "git_text",
            side_effect=[
                "https://github.com/stvelikiy-star/resort-os.git",
                "https://github.com/stvelikiy-star/resort-os.git",
            ],
        ), patch.object(
            publisher,
            "run",
            return_value=SimpleNamespace(stdout=json.dumps(repo_payload)),
        ):
            with self.assertRaises(publisher.PublisherError):
                gate._validate_publish_target(gate.RESORT_OS_PROJECT)

    def test_control_loop_contains_resort_os_publisher_pre_and_post(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            orchestrator = root / "orchestrator"
            orchestrator.mkdir()
            (orchestrator / "resort_os_approved_task_publisher_gate.py").write_text(
                "# reviewed fixture\n", encoding="utf-8"
            )
            with patch.object(
                control_loop_service,
                "_ORIGINAL_CHILD_COMMANDS",
                return_value=[("normal", ["normal-command"])],
            ):
                commands = control_loop_service._commands_with_ai_prof_publisher_gate(
                    root, root / "state"
                )
        stages = [stage for stage, _argv in commands]
        self.assertIn("resort_os_approved_publisher_pre", stages)
        self.assertIn("resort_os_approved_publisher_post", stages)
        pre = stages.index("resort_os_approved_publisher_pre")
        normal = stages.index("normal")
        post = stages.index("resort_os_approved_publisher_post")
        self.assertLess(pre, normal)
        self.assertGreater(post, normal)
        resort_argv = dict(commands)["resort_os_approved_publisher_pre"]
        self.assertIn("resort_os_approved_task_publisher_gate.py", resort_argv[1])
        self.assertIn("--once", resort_argv)

    def test_control_loop_omits_resort_os_route_if_adapter_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            control_loop_service,
            "_ORIGINAL_CHILD_COMMANDS",
            return_value=[("normal", ["normal-command"])],
        ):
            commands = control_loop_service._commands_with_ai_prof_publisher_gate(
                Path(tmp), Path(tmp) / "state"
            )
        stages = [stage for stage, _argv in commands]
        self.assertNotIn("resort_os_approved_publisher_pre", stages)
        self.assertNotIn("resort_os_approved_publisher_post", stages)


if __name__ == "__main__":
    unittest.main()
