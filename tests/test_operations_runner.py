from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "orchestrator" / "operations_runner.py"
SPEC = importlib.util.spec_from_file_location("ai_prof_operations_runner", MODULE_PATH)
operations = importlib.util.module_from_spec(SPEC)
if SPEC.loader is None:
    raise RuntimeError("Cannot load operations runner")
sys.modules[SPEC.name] = operations
SPEC.loader.exec_module(operations)


def completed(argv: list[str], stdout: str = "", stderr: str = "", code: int = 0):
    return subprocess.CompletedProcess(argv, code, stdout, stderr)


class OperationsRunnerTests(unittest.TestCase):
    def make_repo(self, parent: Path) -> tuple[Path, operations.OperationProfile]:
        repo = parent / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "develop"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        migration = "supabase/migrations/20260727000100_manager_inspection_blocking_problem.sql"
        (repo / migration).parent.mkdir(parents=True)
        (repo / migration).write_text("-- test\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
        profile = operations.OperationProfile(
            "test-profile", repo, "develop", migration,
        )
        return repo, profile

    def tools(self, repo: Path) -> operations.Toolchain:
        return operations.Toolchain(
            Path("/node"), Path("/npm"), Path("/npx"),
            repo / "node_modules/.bin/supabase",
        )

    def test_profile_resolution_and_unknown_rejection(self):
        profile = operations.resolve_profile("ak-bermet-supabase-rpc-deploy")
        self.assertEqual(profile.repository, Path("/home/agent/projects/ak-bermet"))
        with self.assertRaises(ValueError):
            operations.resolve_profile("anything-else")

    def test_exact_repository_restriction_and_dirty_tree_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, profile = self.make_repo(Path(tmp))
            with self.assertRaises(operations.OperationBlocked):
                operations.validate_repository(profile, str(repo) + "/.")
            (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            with self.assertRaises(operations.OperationBlocked):
                operations.validate_repository(profile, str(repo))

    def test_subprocess_boundary_always_uses_shell_false(self):
        with (
            mock.patch.dict(
                operations.os.environ,
                {
                    "NODE_OPTIONS": "--require=/tmp/injected.js",
                    "npm_config_script_shell": "/tmp/evil",
                    "SUPABASE_ACCESS_TOKEN": "required-token",
                },
                clear=True,
            ),
            mock.patch.object(
                operations.subprocess, "run", return_value=completed(["git"]),
            ) as run,
        ):
            operations.run_argv(["git", "status"], Path("/tmp"))
        self.assertIs(run.call_args.kwargs["shell"], False)
        self.assertEqual(run.call_args.args[0], ["git", "status"])
        child_env = run.call_args.kwargs["env"]
        self.assertEqual(child_env["SUPABASE_ACCESS_TOKEN"], "required-token")
        self.assertNotIn("NODE_OPTIONS", child_env)
        self.assertNotIn("npm_config_script_shell", child_env)

    def test_secret_redaction(self):
        raw = (
            "Authorization: Bearer abc123 token=hidden password=hunter2 "
            "postgresql://user:pass@example/db secret=topsecret"
        )
        safe = operations.redact(raw)
        for secret in ("abc123", "hidden", "hunter2", "user:pass", "topsecret"):
            self.assertNotIn(secret, safe)

    def test_pending_migration_mismatch_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, profile = self.make_repo(Path(tmp))
            outputs = [
                completed([], "Local | Remote\n20260727000100 | \n"),
                completed([], "20260727000100_x.sql\n20260727000200_other.sql\n"),
            ]
            with (
                mock.patch.object(operations, "validate_repository", return_value=repo),
                mock.patch.object(operations, "locate_toolchain", return_value=self.tools(repo)),
                mock.patch.object(operations, "run_argv", side_effect=outputs),
            ):
                with self.assertRaises(operations.OperationBlocked):
                    operations.execute_profile(profile, str(repo))

    def test_already_applied_skips_push_and_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, profile = self.make_repo(Path(tmp))
            listing = "Local | Remote\n20260727000100 | 20260727000100\n"
            calls: list[list[str]] = []

            def fake_run(argv, _repository, **_kwargs):
                calls.append(argv)
                if argv[1].endswith("supabase"):
                    return completed(argv, listing)
                return completed(argv)

            with (
                mock.patch.object(operations, "validate_repository", return_value=repo),
                mock.patch.object(operations, "locate_toolchain", return_value=self.tools(repo)),
                mock.patch.object(operations, "run_argv", side_effect=fake_run),
                mock.patch.object(operations, "git_output", return_value=""),
            ):
                self.assertEqual(operations.execute_profile(profile, str(repo)), "already_applied")
            self.assertFalse(any("push" in call for call in calls))
            self.assertEqual(len([call for call in calls if call[:2] == ["/node", str(self.tools(repo).supabase)]]), 2)

    def test_successful_pending_operation_applies_only_expected_and_runs_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, profile = self.make_repo(Path(tmp))
            calls: list[list[str]] = []
            listings = iter([
                "Local | Remote\n20260727000100 | \n",
                "Local | Remote\n20260727000100 | 20260727000100\n",
            ])

            def fake_run(argv, _repository, **_kwargs):
                calls.append(argv)
                if "migration" in argv and "list" in argv:
                    return completed(argv, next(listings))
                if "--dry-run" in argv:
                    return completed(argv, "20260727000100_manager_inspection_blocking_problem.sql\n")
                return completed(argv)

            with (
                mock.patch.object(operations, "validate_repository", return_value=repo),
                mock.patch.object(operations, "locate_toolchain", return_value=self.tools(repo)),
                mock.patch.object(operations, "run_argv", side_effect=fake_run),
                mock.patch.object(operations, "git_output", return_value=""),
            ):
                self.assertEqual(operations.execute_profile(profile, str(repo)), "applied")
            self.assertIn(
                ["/node", str(self.tools(repo).supabase), "db", "push", "--linked"],
                calls,
            )
            self.assertIn(["/npm", "run", "lint"], calls)
            self.assertIn(["/npx", "tsc", "--noEmit"], calls)
            self.assertIn(
                ["/node", "--test", "--experimental-strip-types", "src/lib/inspection-rules.test.ts"],
                calls,
            )
            self.assertIn(["/npm", "run", "build"], calls)

    def test_operations_task_does_not_require_bubblewrap_and_code_task_still_does(self):
        source = (MODULE_PATH.parent / "claude_runner.py").read_text(encoding="utf-8")
        self.assertNotIn("bwrap", operations.execute_profile.__code__.co_names)
        self.assertIn("check_bwrap_available()", source[source.index("def process_one"):])


if __name__ == "__main__":
    unittest.main()
