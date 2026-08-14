from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "orchestrator/ak_bermet_sheets_mirror_dry_run.py"
RUNNER_PATH = ROOT / "orchestrator/operations_runner.py"

HELPER_SPEC = importlib.util.spec_from_file_location("ai_prof_sheets_runtime", HELPER_PATH)
helper = importlib.util.module_from_spec(HELPER_SPEC)
if HELPER_SPEC.loader is None:
    raise RuntimeError("Cannot load Sheets runtime helper")
sys.modules[HELPER_SPEC.name] = helper
HELPER_SPEC.loader.exec_module(helper)

RUNNER_SPEC = importlib.util.spec_from_file_location("ai_prof_operations_runtime", RUNNER_PATH)
runner = importlib.util.module_from_spec(RUNNER_SPEC)
if RUNNER_SPEC.loader is None:
    raise RuntimeError("Cannot load operations runner")
sys.modules[RUNNER_SPEC.name] = runner
RUNNER_SPEC.loader.exec_module(runner)


class SheetsMirrorRuntimeOperationTests(unittest.TestCase):
    def valid_values(self) -> dict[str, str]:
        return {
            "NEXT_PUBLIC_SUPABASE_URL": f"https://{helper.EXPECTED_SUPABASE_HOST}",
            "SUPABASE_SERVICE_ROLE_KEY": "service-role-sentinel",
            "GOOGLE_SHEETS_SPREADSHEET_ID": helper.EXPECTED_SPREADSHEET_ID,
            "GOOGLE_SERVICE_ACCOUNT_EMAIL": "mirror@example.invalid",
            "GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY": "private-key-sentinel",
        }

    def test_operation_profile_is_pinned_to_ak_bermet_main(self):
        profile = runner.resolve_profile("ak-bermet-sheets-mirror-dry-run")
        self.assertEqual(profile.repository, Path("/home/agent/projects/ak-bermet"))
        self.assertEqual(profile.base_branch, "main")
        self.assertEqual(profile.kind, "sheets-mirror-dry-run")
        self.assertEqual(profile.expected_migration, "")

    def test_env_parser_reads_only_allowlisted_keys_without_shell_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.env"
            path.write_text(
                "# comment\n"
                "export NEXT_PUBLIC_SUPABASE_URL=\"https://ednqgzgjhnalsiiuekmw.supabase.co\"\n"
                "SUPABASE_SERVICE_ROLE_KEY=role-value\n"
                "UNTRUSTED_COMMAND=$(touch /tmp/never)\n"
                "GOOGLE_SHEETS_SPREADSHEET_ID=sheet-id\n",
                encoding="utf-8",
            )
            parsed = helper.parse_env_file(path)
        self.assertEqual(
            parsed,
            {
                "NEXT_PUBLIC_SUPABASE_URL": "https://ednqgzgjhnalsiiuekmw.supabase.co",
                "SUPABASE_SERVICE_ROLE_KEY": "role-value",
                "GOOGLE_SHEETS_SPREADSHEET_ID": "sheet-id",
            },
        )
        self.assertNotIn("UNTRUSTED_COMMAND", parsed)

    def test_runtime_environment_fails_closed_and_never_enables_execute_gate(self):
        values = self.valid_values()
        with (
            mock.patch.object(helper, "ENV_FILES", ()),
        ):
            environment = helper.build_runtime_environment(
                {
                    **values,
                    "PATH": "/usr/bin:/bin",
                    "NODE_OPTIONS": "--require=/tmp/injected.js",
                    "AK_BERMET_SHEETS_MIRROR_ENABLED": "YES",
                }
            )
        self.assertEqual(environment["GOOGLE_SHEETS_ENABLED"], "true")
        self.assertNotIn("AK_BERMET_SHEETS_MIRROR_ENABLED", environment)
        self.assertNotIn("NODE_OPTIONS", environment)

        with mock.patch.object(helper, "ENV_FILES", ()):
            with self.assertRaises(helper.RuntimeDryRunBlocked) as context:
                helper.build_runtime_environment({"PATH": "/usr/bin"})
        message = str(context.exception)
        self.assertIn("MISSING_ENVIRONMENT:NEXT_PUBLIC_SUPABASE_URL", message)
        self.assertNotIn("service-role-sentinel", message)
        self.assertNotIn("private-key-sentinel", message)

    def test_dev_and_workbook_identities_are_pinned(self):
        values = self.valid_values()
        with mock.patch.object(helper, "ENV_FILES", ()):
            with self.assertRaisesRegex(
                helper.RuntimeDryRunBlocked, "SUPABASE_DEV_IDENTITY_MISMATCH",
            ):
                helper.build_runtime_environment(
                    {**values, "NEXT_PUBLIC_SUPABASE_URL": "https://prod.supabase.co"}
                )
            with self.assertRaisesRegex(
                helper.RuntimeDryRunBlocked, "GOOGLE_SHEETS_IDENTITY_MISMATCH",
            ):
                helper.build_runtime_environment(
                    {**values, "GOOGLE_SHEETS_SPREADSHEET_ID": "wrong-sheet"}
                )

    def test_worker_result_parser_returns_only_safe_plan_summary(self):
        sha = "a" * 40
        result = helper._parse_worker_result(
            "DRY: room_units -> update\nDRY: leads -> append\nRESULT: PASS queue=2\n",
            "",
            0,
            sha,
        )
        self.assertEqual(result.git_sha, sha)
        self.assertEqual(result.queue_count, 2)
        self.assertEqual(result.planned_actions, ("room_units:update", "leads:append"))
        self.assertEqual(
            result.summary(),
            f"dry_run_pass sha={sha} queue=2 actions=room_units:update,leads:append",
        )
        with self.assertRaisesRegex(helper.RuntimeDryRunBlocked, "SYNC_MAPPING_REQUIRED"):
            helper._parse_worker_result(
                "",
                "DRY_BLOCKED: room_units -> SYNC_MAPPING_REQUIRED\n",
                2,
                sha,
            )

    def test_legacy_telegram_goal_is_promoted_only_by_exact_project_and_goal(self):
        exact = {
            "Execution-Mode": "code",
            "Project-Path": "/home/agent/projects/ak-bermet",
            "Goal": "Sheets mirror runtime dry-run",
            "Operation-Profile": "none",
        }
        self.assertEqual(
            runner.legacy_runtime_profile(exact),
            "ak-bermet-sheets-mirror-dry-run",
        )
        for changed in (
            {**exact, "Goal": "Sheets mirror runtime dry-run please"},
            {**exact, "Project-Path": "/tmp/ak-bermet"},
            {**exact, "Execution-Mode": "operations"},
        ):
            self.assertIsNone(runner.legacy_runtime_profile(changed))

    def test_runner_dispatches_profile_without_executing_task_text(self):
        profile = runner.resolve_profile("ak-bermet-sheets-mirror-dry-run")
        result = helper.RuntimeDryRunResult("b" * 40, 0, ())
        with (
            mock.patch.object(runner, "locate_node_bin", return_value=Path("/nvm/bin")),
            mock.patch.object(runner, "operation_environment", return_value={"PATH": "/nvm/bin"}),
            mock.patch.object(runner.sheets_mirror, "execute", return_value=result) as execute,
        ):
            outcome = runner.execute_profile(profile, str(profile.repository))
        self.assertIn("dry_run_pass", outcome)
        execute.assert_called_once_with(
            Path("/nvm/bin/node"), str(profile.repository), {"PATH": "/nvm/bin"}
        )

    def test_terminal_reason_is_redacted_for_telegram_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task.md"
            task.write_text("Task-ID: TEST_TASK\n", encoding="utf-8")
            runner._terminal_reason(
                task,
                "Blocked-Reason",
                "token=do-not-show MISSING_ENVIRONMENT:GOOGLE_SERVICE_ACCOUNT_EMAIL",
            )
            text = task.read_text(encoding="utf-8")
        self.assertIn("Blocked-Reason:", text)
        self.assertIn("MISSING_ENVIRONMENT:GOOGLE_SERVICE_ACCOUNT_EMAIL", text)
        self.assertNotIn("do-not-show", text)

    def test_source_pins_dry_run_and_never_constructs_execute_argv(self):
        source = HELPER_PATH.read_text(encoding="utf-8")
        self.assertIn('"--dry-run", "--limit=25"', source)
        self.assertNotIn('"--execute"', source)
        self.assertIn('environment.pop("AK_BERMET_SHEETS_MIRROR_ENABLED", None)', source)
        self.assertIn('"worktree", "add", "--detach"', source)


if __name__ == "__main__":
    unittest.main()
