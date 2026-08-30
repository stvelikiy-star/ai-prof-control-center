from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bootstrap_self_maintenance.py"
SPEC = importlib.util.spec_from_file_location(
    "bootstrap_self_maintenance_test",
    SCRIPT,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load bootstrap_self_maintenance.py")
bootstrap = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bootstrap
SPEC.loader.exec_module(bootstrap)


class BootstrapSelfMaintenanceDivergenceTests(unittest.TestCase):
    def test_normal_ancestor_path_stays_ff_only(self):
        target = Path("/tmp/maintenance")
        with mock.patch.object(bootstrap, "run_status", return_value=0), \
             mock.patch.object(bootstrap, "run") as run:
            archive = bootstrap.reconcile_clean_standalone(target)
        self.assertIsNone(archive)
        run.assert_called_once_with(
            ["git", "merge", "--ff-only", bootstrap.REMOTE_REF],
            cwd=target,
        )

    def test_diverged_clean_clone_archives_old_head_before_realign(self):
        target = Path("/tmp/maintenance")
        local_sha = "a" * 40
        remote_sha = "b" * 40
        expected_archive = f"{bootstrap.ARCHIVE_PREFIX}{local_sha[:12]}"

        def fake_head(_path: Path, ref: str = "HEAD") -> str:
            if ref == "HEAD":
                return remote_sha if fake_head.realigned else local_sha
            if ref == bootstrap.REMOTE_REF:
                return remote_sha
            raise AssertionError(ref)

        fake_head.realigned = False

        def fake_run(argv: list[str], *, cwd=None, capture=False):
            if argv[:3] == ["git", "switch", "--detach"]:
                fake_head.realigned = True
            return ""

        with mock.patch.object(bootstrap, "run_status", return_value=1), \
             mock.patch.object(bootstrap, "head", side_effect=fake_head), \
             mock.patch.object(bootstrap, "ref_sha", return_value=None), \
             mock.patch.object(bootstrap, "status", return_value=""), \
             mock.patch.object(bootstrap, "run", side_effect=fake_run) as run:
            archive = bootstrap.reconcile_clean_standalone(target)

        self.assertEqual(archive, expected_archive)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(
            commands,
            [
                ["git", "branch", expected_archive, local_sha],
                ["git", "switch", "--detach", remote_sha],
                ["git", "branch", "-f", bootstrap.BASE_BRANCH, remote_sha],
                ["git", "switch", bootstrap.BASE_BRANCH],
            ],
        )

    def test_existing_archive_for_same_sha_is_reused(self):
        target = Path("/tmp/maintenance")
        local_sha = "c" * 40
        remote_sha = "d" * 40
        realigned = {"value": False}

        def fake_head(_path: Path, ref: str = "HEAD") -> str:
            if ref == bootstrap.REMOTE_REF:
                return remote_sha
            return remote_sha if realigned["value"] else local_sha

        def fake_run(argv: list[str], *, cwd=None, capture=False):
            if argv[:3] == ["git", "switch", "--detach"]:
                realigned["value"] = True
            return ""

        with mock.patch.object(bootstrap, "run_status", return_value=1), \
             mock.patch.object(bootstrap, "head", side_effect=fake_head), \
             mock.patch.object(bootstrap, "ref_sha", return_value=local_sha), \
             mock.patch.object(bootstrap, "status", return_value=""), \
             mock.patch.object(bootstrap, "run", side_effect=fake_run) as run:
            bootstrap.reconcile_clean_standalone(target)

        commands = [call.args[0] for call in run.call_args_list]
        self.assertFalse(any(cmd[:2] == ["git", "branch"] and "archive/" in " ".join(cmd) for cmd in commands))
        self.assertIn(["git", "branch", "-f", bootstrap.BASE_BRANCH, remote_sha], commands)

    def test_conflicting_archive_fails_closed_before_branch_move(self):
        target = Path("/tmp/maintenance")
        local_sha = "e" * 40
        remote_sha = "f" * 40
        wrong_sha = "1" * 40

        with mock.patch.object(bootstrap, "run_status", return_value=1), \
             mock.patch.object(
                 bootstrap,
                 "head",
                 side_effect=lambda _path, ref="HEAD": (
                     local_sha if ref == "HEAD" else remote_sha
                 ),
             ), \
             mock.patch.object(bootstrap, "ref_sha", return_value=wrong_sha), \
             mock.patch.object(bootstrap, "run") as run:
            with self.assertRaisesRegex(
                bootstrap.BootstrapError,
                "archive branch exists with unexpected SHA",
            ):
                bootstrap.reconcile_clean_standalone(target)

        run.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
