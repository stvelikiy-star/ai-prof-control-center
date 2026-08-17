#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import ak_bermet_approved_task_publisher_gate as gate
import approved_task_publisher as publisher
import control_loop_service


class AkBermetApprovedPublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        gate.configure_ak_bermet_profile()

    def sample(self, branch: str, instructions: str = "owner task") -> str:
        return "\n".join(
            [
                "Task-ID: AK_BERMET_20260817T000000Z_ABCDEF",
                "Project-Path: /home/agent/projects/ak-bermet",
                "Base-Branch: main",
                f"Work-Branch: {branch}",
                "Goal: smoke",
                f"Instructions: {instructions}",
                "Scope-Files: docs/a.md, src/lib/a.ts",
            ]
        )

    def test_profile_is_exact_and_non_production(self) -> None:
        self.assertEqual(gate.AK_BERMET_PROJECT, Path("/home/agent/projects/ak-bermet"))
        self.assertEqual(gate.AK_BERMET_REPOSITORY, "stvelikiy-star/ak-bermet")
        self.assertEqual(gate.AK_BERMET_BASE_BRANCH, "main")
        self.assertEqual(publisher.KOL_PROJECT, gate.AK_BERMET_PROJECT)
        self.assertEqual(publisher.KOL_REPOSITORY, gate.AK_BERMET_REPOSITORY)
        self.assertEqual(publisher.KOL_BASE_BRANCH, "main")

    def test_owner_telegram_branch_is_accepted(self) -> None:
        task_id, branch, source, scopes = publisher.validate_supported_task(
            self.sample("feature/telegram-0123abcd-012345abcdef")
        )
        self.assertTrue(task_id.startswith("AK_BERMET_"))
        self.assertEqual(branch, "feature/telegram-0123abcd-012345abcdef")
        self.assertEqual(source, gate.TELEGRAM_SOURCE_SENTINEL)
        self.assertEqual(scopes, ["docs/a.md", "src/lib/a.ts"])

    def test_github_gateway_contract_remains_accepted(self) -> None:
        task_id, branch, issue, scopes = publisher.validate_supported_task(
            self.sample(
                "feature/chatgpt-issue-90",
                "Source: authorized private GitHub task issue #90.",
            )
        )
        self.assertTrue(task_id.startswith("AK_BERMET_"))
        self.assertEqual(branch, "feature/chatgpt-issue-90")
        self.assertEqual(issue, 90)
        self.assertEqual(scopes, ["docs/a.md", "src/lib/a.ts"])

    def test_arbitrary_branch_is_rejected(self) -> None:
        for branch in (
            "feature/arbitrary",
            "fix/telegram-0123abcd-012345abcdef",
            "feature/telegram-short",
            "feature/telegram-0123ABCD-012345abcdef",
        ):
            with self.subTest(branch=branch):
                with self.assertRaises(publisher.PublisherError):
                    publisher.validate_supported_task(self.sample(branch))

    def test_wrong_project_is_rejected(self) -> None:
        text = self.sample("feature/telegram-0123abcd-012345abcdef").replace(
            "/home/agent/projects/ak-bermet",
            "/tmp/not-ak-bermet",
        )
        with self.assertRaises(publisher.PublisherError):
            publisher.validate_supported_task(text)

    def test_target_gate_rejects_wrong_origin_before_github(self) -> None:
        project = gate.AK_BERMET_PROJECT
        with patch.object(
            publisher,
            "git_text",
            side_effect=["https://example.invalid/wrong.git", "https://example.invalid/wrong.git"],
        ), patch.object(publisher, "run") as run_mock:
            with self.assertRaises(publisher.PublisherError):
                gate._validate_publish_target(project)
            run_mock.assert_not_called()

    def test_target_gate_rejects_public_or_wrong_repository_identity(self) -> None:
        project = gate.AK_BERMET_PROJECT
        repo_payload = {
            "full_name": gate.AK_BERMET_REPOSITORY,
            "private": False,
            "default_branch": "main",
            "owner": {"login": gate.OWNER},
        }
        with patch.object(
            publisher,
            "git_text",
            side_effect=[
                "https://github.com/stvelikiy-star/ak-bermet.git",
                "https://github.com/stvelikiy-star/ak-bermet.git",
            ],
        ), patch.object(
            publisher,
            "run",
            return_value=SimpleNamespace(stdout=json.dumps(repo_payload)),
        ):
            with self.assertRaises(publisher.PublisherError):
                gate._validate_publish_target(project)

    def test_telegram_source_does_not_post_fake_github_issue(self) -> None:
        with patch.object(gate, "_ORIGINAL_POST_SOURCE_COMMENT") as original:
            gate._post_source_result(
                gate.TELEGRAM_SOURCE_SENTINEL,
                "AK_BERMET_20260817T000000Z_ABCDEF",
                "a" * 40,
                "https://github.com/stvelikiy-star/ak-bermet/pull/1",
            )
            original.assert_not_called()

    def test_control_loop_service_wraps_normal_pipeline_with_ak_publisher(self) -> None:
        with patch.object(
            control_loop_service,
            "_ORIGINAL_CHILD_COMMANDS",
            return_value=[("normal", ["normal-command"])],
        ):
            commands = control_loop_service._commands_with_publishers(
                Path("/control"), Path("/state")
            )
        stages = [stage for stage, _argv in commands]
        self.assertEqual(
            stages,
            [
                "kol_approved_publisher_pre",
                "ak_bermet_approved_publisher_pre",
                "normal",
                "kol_approved_publisher_post",
                "ak_bermet_approved_publisher_post",
            ],
        )
        ak_argv = commands[1][1]
        self.assertIn("ak_bermet_approved_task_publisher_gate.py", ak_argv[1])
        self.assertIn("--once", ak_argv)


if __name__ == "__main__":
    unittest.main()
