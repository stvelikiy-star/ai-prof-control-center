from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Legacy Control Center modules use top-level sibling imports (for example
# ``import control_loop``) when executed directly from orchestrator/. Keep the
# repository root ahead of orchestrator/ so ``orchestrator`` still resolves as
# the package/namespace directory, then append orchestrator/ only for legacy
# sibling imports. This avoids shadowing the package with orchestrator.py.
ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1] / "orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.append(str(ORCHESTRATOR_DIR))

from orchestrator import control_loop_service_night as night_service
from orchestrator import operations_runner_night as night_operations


class NightHealthDiscoveryTests(unittest.TestCase):
    def test_plain_unittest_is_upgraded_to_explicit_discovery(self):
        self.assertEqual(
            night_operations._upgrade_health_argv(
                [str(night_operations.base.PYTHON3_CLI), "-m", "unittest"]
            ),
            list(night_operations.STRICT_FULL_TEST_ARGV),
        )

    def test_focused_unittest_command_is_unchanged(self):
        argv = [
            str(night_operations.base.PYTHON3_CLI),
            "-m",
            "unittest",
            "tests.test_control_loop",
        ]
        self.assertEqual(night_operations._upgrade_health_argv(argv), argv)

    def test_non_test_operation_command_is_unchanged(self):
        argv = ["/usr/bin/git", "status", "--porcelain"]
        self.assertEqual(night_operations._upgrade_health_argv(argv), argv)

    def test_night_run_argv_delegates_with_explicit_discovery(self):
        repository = Path("/repo")
        environment = {"PATH": "/usr/bin:/bin"}
        with mock.patch.object(
            night_operations,
            "_ORIGINAL_RUN_ARGV",
            return_value=object(),
        ) as delegated:
            night_operations._night_run_argv(
                [str(night_operations.base.PYTHON3_CLI), "-m", "unittest"],
                repository,
                environment,
                timeout=123,
                retry_transient=False,
            )
        delegated.assert_called_once_with(
            list(night_operations.STRICT_FULL_TEST_ARGV),
            repository,
            environment,
            timeout=123,
            retry_transient=False,
        )

    def test_control_loop_replaces_only_operations_runner_binding(self):
        root = Path("/repo")
        legacy = str(root / "orchestrator/operations_runner.py")
        other = str(root / "orchestrator/orchestrator.py")
        commands = [
            ("operations", ["python", legacy, "--once"]),
            ("stage_01a", ["python", other]),
        ]
        upgraded = night_service._upgrade_operations_binding(root, commands)
        self.assertEqual(
            upgraded[0][1],
            ["python", str(root / "orchestrator/operations_runner_night.py"), "--once"],
        )
        self.assertEqual(upgraded[1], commands[1])

    def test_composed_night_commands_use_strict_operations_runner(self):
        root = Path("/repo")
        legacy = str(root / "orchestrator/operations_runner.py")
        established = [
            ("kol_approved_publisher_pre", ["kol-pre"]),
            ("ak_bermet_approved_publisher_pre", ["ak-pre"]),
            ("operations", ["python", legacy, "--root", str(root)]),
            ("stage_01a", ["stage-01a"]),
            ("kol_approved_publisher_post", ["kol-post"]),
            ("ak_bermet_approved_publisher_post", ["ak-post"]),
        ]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            night_service.base,
            "_commands_with_publishers",
            return_value=established,
        ), mock.patch.object(
            night_service.base,
            "_publisher_argv",
            return_value=["python", "ai_prof_approved_task_publisher_gate_v2.py"],
        ):
            commands = night_service._commands_with_night_safe_ai_prof_gate(
                root, Path(tmp)
            )
        operation = next(argv for stage, argv in commands if stage == "operations")
        self.assertIn(
            str(root / "orchestrator/operations_runner_night.py"),
            operation,
        )
        self.assertNotIn(legacy, operation)


if __name__ == "__main__":
    unittest.main(verbosity=2)
