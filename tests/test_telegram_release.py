from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from orchestrator import telegram_bridge


class TelegramReleaseTests(unittest.TestCase):
    def test_release_prepare_command_is_parsed(self):
        command = telegram_bridge.parse_command(
            "/ai release ak-bermet prepare"
        )

        self.assertIsNotNone(command)
        self.assertEqual(command.name, "release")
        self.assertEqual(command.project, "ak-bermet")

    def test_incomplete_release_command_is_invalid(self):
        command = telegram_bridge.parse_command(
            "/ai release ak-bermet"
        )

        self.assertIsNotNone(command)
        self.assertEqual(command.name, "invalid")

    def test_owner_action_required_report_is_returned(self):
        command = telegram_bridge.parse_command(
            "/ai release ak-bermet prepare"
        )

        completed = subprocess.CompletedProcess(
            args=[],
            returncode=2,
            stdout=(
                "AI PROF release preparation\n"
                "Project: ak-bermet\n"
                "State: OWNER_ACTION_REQUIRED\n"
                "Production changed: no\n"
            ),
        )

        with (
            patch.object(
                telegram_bridge,
                "load_projects",
                return_value={},
            ),
            patch.object(
                telegram_bridge,
                "resolve_project",
                return_value=("ak-bermet", {}),
            ),
            patch.object(
                telegram_bridge.subprocess,
                "run",
                return_value=completed,
            ),
        ):
            message = (
                telegram_bridge.release_prepare_message(
                    command
                )
            )

        self.assertIn(
            "OWNER_ACTION_REQUIRED",
            message,
        )
        self.assertIn(
            "Production changed: no",
            message,
        )

    def test_unexpected_release_failure_is_rejected(self):
        command = telegram_bridge.parse_command(
            "/ai release ak-bermet prepare"
        )

        completed = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="internal failure\n",
        )

        with (
            patch.object(
                telegram_bridge,
                "load_projects",
                return_value={},
            ),
            patch.object(
                telegram_bridge,
                "resolve_project",
                return_value=("ak-bermet", {}),
            ),
            patch.object(
                telegram_bridge.subprocess,
                "run",
                return_value=completed,
            ),
        ):
            with self.assertRaises(
                telegram_bridge.BridgeError
            ):
                telegram_bridge.release_prepare_message(
                    command
                )


if __name__ == "__main__":
    unittest.main()
