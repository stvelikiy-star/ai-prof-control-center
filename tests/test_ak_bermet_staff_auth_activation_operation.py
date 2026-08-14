from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "orchestrator/ak_bermet_staff_auth_activate_dev.py"
RUNNER_PATH = ROOT / "orchestrator/operations_runner.py"

HELPER_SPEC = importlib.util.spec_from_file_location("ai_prof_staff_auth_runtime", HELPER_PATH)
helper = importlib.util.module_from_spec(HELPER_SPEC)
if HELPER_SPEC.loader is None:
    raise RuntimeError("Cannot load staff Auth runtime helper")
sys.modules[HELPER_SPEC.name] = helper
HELPER_SPEC.loader.exec_module(helper)

RUNNER_SPEC = importlib.util.spec_from_file_location("ai_prof_operations_staff_auth", RUNNER_PATH)
runner = importlib.util.module_from_spec(RUNNER_SPEC)
if RUNNER_SPEC.loader is None:
    raise RuntimeError("Cannot load operations runner")
sys.modules[RUNNER_SPEC.name] = runner
RUNNER_SPEC.loader.exec_module(runner)


class StaffAuthActivationOperationTests(unittest.TestCase):
    def valid_env(self) -> dict[str, str]:
        return {
            "NEXT_PUBLIC_SUPABASE_URL": f"https://{helper.EXPECTED_SUPABASE_HOST}",
            "SUPABASE_SERVICE_ROLE_KEY": "service-role-sentinel",
            "SUPABASE_PROJECT_REF": helper.EXPECTED_PROJECT_REF,
            "PATH": "/usr/bin:/bin",
        }

    def test_operation_profile_is_pinned_to_ak_bermet_main_dev_activation(self):
        profile = runner.resolve_profile("ak-bermet-staff-auth-activate-dev")
        self.assertEqual(profile.repository, Path("/home/agent/projects/ak-bermet"))
        self.assertEqual(profile.base_branch, "main")
        self.assertEqual(profile.kind, "staff-auth-activate-dev")
        self.assertEqual(profile.expected_migration, "")

    def test_fixed_staff_login_contract_is_exactly_17_numbered_slots(self):
        self.assertEqual(len(helper.STAFF_SLOTS), 17)
        slots = [slot for slot, _email in helper.STAFF_SLOTS]
        emails = [email for _slot, email in helper.STAFF_SLOTS]
        self.assertEqual(slots[0], "owner-1")
        self.assertEqual(slots[1], "administrator-1")
        self.assertEqual(slots[2:6], [f"manager-{index}" for index in range(1, 5)])
        self.assertEqual(slots[6:12], [f"housekeeping-{index}" for index in range(1, 7)])
        self.assertEqual(slots[12:17], [f"technician-{index}" for index in range(1, 6)])
        self.assertEqual(len(set(emails)), 17)
        self.assertTrue(all(email.endswith("@staff.akbermet.invalid") for email in emails))

    def test_env_parser_is_allowlisted_and_execution_gates_are_not_inherited(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.env"
            path.write_text(
                "NEXT_PUBLIC_SUPABASE_URL=https://ednqgzgjhnalsiiuekmw.supabase.co\n"
                "SUPABASE_SERVICE_ROLE_KEY=role-value\n"
                "SUPABASE_PROJECT_REF=ednqgzgjhnalsiiuekmw\n"
                "UNTRUSTED=$(touch /tmp/never)\n",
                encoding="utf-8",
            )
            parsed = helper.parse_env_file(path)
        self.assertEqual(
            parsed,
            {
                "NEXT_PUBLIC_SUPABASE_URL": "https://ednqgzgjhnalsiiuekmw.supabase.co",
                "SUPABASE_SERVICE_ROLE_KEY": "role-value",
                "SUPABASE_PROJECT_REF": "ednqgzgjhnalsiiuekmw",
            },
        )
        with mock.patch.object(helper, "ENV_FILES", ()):
            environment = helper.build_runtime_environment(
                {
                    **self.valid_env(),
                    "NODE_OPTIONS": "--require=/tmp/injected.js",
                    "AK_BERMET_AUTH_PROVISION_ENABLED": "YES",
                    "AK_BERMET_AUTH_TARGET": "PRODUCTION",
                }
            )
        self.assertNotIn("NODE_OPTIONS", environment)
        self.assertNotIn("AK_BERMET_AUTH_PROVISION_ENABLED", environment)
        self.assertNotIn("AK_BERMET_AUTH_TARGET", environment)

    def test_environment_fails_closed_on_missing_or_wrong_dev_identity(self):
        with mock.patch.object(helper, "ENV_FILES", ()):
            with self.assertRaisesRegex(helper.StaffAuthActivationBlocked, "MISSING_ENVIRONMENT"):
                helper.build_runtime_environment({"PATH": "/usr/bin"})
            with self.assertRaisesRegex(helper.StaffAuthActivationBlocked, "SUPABASE_DEV_IDENTITY_MISMATCH"):
                helper.build_runtime_environment(
                    {**self.valid_env(), "NEXT_PUBLIC_SUPABASE_URL": "https://wrong.supabase.co"}
                )
            with self.assertRaisesRegex(helper.StaffAuthActivationBlocked, "SUPABASE_PROJECT_REF_MISMATCH"):
                helper.build_runtime_environment(
                    {**self.valid_env(), "SUPABASE_PROJECT_REF": "wrong"}
                )

    def test_manifest_is_private_exact_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            secure = Path(tmp) / "secure"
            manifest = secure / "staff.json"
            with (
                mock.patch.object(helper, "SECURE_ROOT", secure),
                mock.patch.object(helper, "MANIFEST", manifest),
            ):
                created = helper.ensure_manifest()
                self.assertEqual(created, manifest)
                mode = stat.S_IMODE(manifest.stat().st_mode)
                self.assertEqual(mode, 0o600)
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                self.assertEqual(payload["version"], 1)
                self.assertEqual(payload["project"], "ak-bermet-dev")
                self.assertEqual(len(payload["slots"]), 17)
                passwords = [entry["password"] for entry in payload["slots"]]
                self.assertEqual(len(set(passwords)), 17)
                self.assertTrue(all(len(password) >= 20 for password in passwords))
                original = manifest.read_bytes()
                helper.ensure_manifest()
                self.assertEqual(manifest.read_bytes(), original)

    def test_result_parsers_expose_only_counts_and_safe_blocker_codes(self):
        dry = mock.Mock(returncode=0, stdout="RESULT: PASS mode=dry-run slots=17\n", stderr="")
        helper._parse_dry_result(dry)
        executed = mock.Mock(
            returncode=0,
            stdout="RESULT: PASS mode=execute created=17 existing=0 total=17\n",
            stderr="",
        )
        result = helper._parse_execute_result(executed, "a" * 40)
        self.assertEqual(result.created, 17)
        self.assertEqual(result.existing, 0)
        self.assertEqual(result.total, 17)
        self.assertNotIn("password", result.summary().lower())
        blocked = mock.Mock(
            returncode=2,
            stdout="",
            stderr="BLOCKED: AUTH_CREATE_FAILED:400 slot=manager-2\n",
        )
        with self.assertRaisesRegex(
            helper.StaffAuthActivationBlocked,
            "PROVISIONER_EXECUTE_BLOCKED:AUTH_CREATE_FAILED:400:slot=manager-2",
        ):
            helper._parse_execute_result(blocked, "b" * 40)

    def test_legacy_telegram_goal_is_promoted_only_by_exact_project_and_goal(self):
        exact = {
            "Execution-Mode": "code",
            "Project-Path": "/home/agent/projects/ak-bermet",
            "Goal": "Activate 17 DEV staff accounts",
            "Operation-Profile": "none",
        }
        self.assertEqual(
            runner.legacy_runtime_profile(exact),
            "ak-bermet-staff-auth-activate-dev",
        )
        for changed in (
            {**exact, "Goal": "Activate 17 DEV staff accounts please"},
            {**exact, "Project-Path": "/tmp/ak-bermet"},
            {**exact, "Execution-Mode": "operations"},
        ):
            self.assertIsNone(runner.legacy_runtime_profile(changed))

    def test_runner_dispatches_registered_helper_without_task_text(self):
        profile = runner.resolve_profile("ak-bermet-staff-auth-activate-dev")
        result = helper.StaffAuthActivationResult(
            "c" * 40, 17, 0, 17, "/state/secure/manifest.json"
        )
        with (
            mock.patch.object(runner, "locate_node_bin", return_value=Path("/nvm/bin")),
            mock.patch.object(runner, "operation_environment", return_value={"PATH": "/nvm/bin"}),
            mock.patch.object(runner.staff_auth, "execute", return_value=result) as execute,
        ):
            outcome = runner.execute_profile(profile, str(profile.repository))
        self.assertIn("staff_auth_dev_pass", outcome)
        execute.assert_called_once_with(
            Path("/nvm/bin/node"), str(profile.repository), {"PATH": "/nvm/bin"}
        )

    def test_source_executes_dry_run_before_dev_gated_execute_with_shell_false(self):
        source = HELPER_PATH.read_text(encoding="utf-8")
        dry_index = source.index('"--manifest", str(manifest)]')
        execute_index = source.index('"--manifest", str(manifest), "--execute"')
        self.assertLess(dry_index, execute_index)
        self.assertIn('execute_environment["AK_BERMET_AUTH_PROVISION_ENABLED"] = "YES"', source)
        self.assertIn('execute_environment["AK_BERMET_AUTH_TARGET"] = "DEV"', source)
        self.assertIn("shell=False", source)
        self.assertNotIn("shell=True", source)


if __name__ == "__main__":
    unittest.main()
