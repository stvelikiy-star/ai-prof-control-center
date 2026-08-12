from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "orchestrator" / "operations_runner.py"
SPEC = importlib.util.spec_from_file_location("ak_bermet_release_v6_operations_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load operations runner")
operations = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = operations
SPEC.loader.exec_module(operations)


class AkBermetReleaseV6OperationTests(unittest.TestCase):
    def profile(self):
        return operations.resolve_profile("ak-bermet-production-prepare-v6")

    def report(self, blockers=None, *, changed=False):
        return operations.release_v6.PrepareReport(
            frozen_sha=operations.release_v6.FROZEN_SHA,
            repository="/home/agent/projects/ak-bermet",
            production_changed=changed,
            blockers=list(blockers or []),
        )

    def test_profile_is_fixed_to_ak_bermet_main_and_read_only_kind(self):
        profile = self.profile()
        self.assertEqual(profile.repository, Path("/home/agent/projects/ak-bermet"))
        self.assertEqual(profile.base_branch, "main")
        self.assertEqual(profile.expected_migration, "")
        self.assertEqual(profile.kind, "release-v6-prepare")

    def test_release_prepare_dispatch_never_enters_migration_toolchain(self):
        profile = self.profile()
        with (
            mock.patch.object(operations.release_v6, "prepare", return_value=self.report()) as prepare,
            mock.patch.object(operations, "locate_node_bin", side_effect=AssertionError("migration path entered")),
            mock.patch.object(operations, "locate_toolchain", side_effect=AssertionError("migration path entered")),
        ):
            outcome = operations.execute_profile(profile, str(profile.repository))
        self.assertEqual(outcome, "release_ready")
        prepare.assert_called_once_with()

    def test_expected_v6_readiness_blockers_move_operation_to_blocked_contract(self):
        profile = self.profile()
        blockers = [
            "RESTORE_SMOKE_REQUIRED_BEFORE_PRODUCTION_CHANGE",
            "DEPLOYMENT_TARGET_UNVERIFIED",
            "ROLLBACK_SAFE_CUTOVER_UNVERIFIED",
        ]
        with mock.patch.object(operations.release_v6, "prepare", return_value=self.report(blockers)):
            with self.assertRaisesRegex(
                operations.OperationBlocked,
                "AK_BERMET_V6_PREPARE:.*RESTORE_SMOKE_REQUIRED",
            ):
                operations.execute_profile(profile, str(profile.repository))

    def test_repository_path_cannot_be_overridden_by_task_text(self):
        profile = self.profile()
        with mock.patch.object(operations.release_v6, "prepare") as prepare:
            with self.assertRaisesRegex(operations.OperationBlocked, "exactly match registered path"):
                operations.execute_profile(profile, "/tmp/ak-bermet")
        prepare.assert_not_called()

    def test_any_reported_production_mutation_fails_closed(self):
        profile = self.profile()
        with mock.patch.object(
            operations.release_v6,
            "prepare",
            return_value=self.report(changed=True),
        ):
            with self.assertRaisesRegex(operations.OperationFailed, "production mutation"):
                operations.execute_profile(profile, str(profile.repository))

    def test_unknown_profile_kind_cannot_fall_through_to_migration(self):
        profile = operations.OperationProfile(
            key="unknown-kind",
            repository=Path("/home/agent/projects/ak-bermet"),
            base_branch="main",
            expected_migration="",
            kind="unknown",
        )
        with mock.patch.object(operations, "locate_node_bin", side_effect=AssertionError("must not run")):
            with self.assertRaisesRegex(operations.OperationBlocked, "unsupported operation profile kind"):
                operations.execute_profile(profile, str(profile.repository))


if __name__ == "__main__":
    unittest.main(verbosity=2)
