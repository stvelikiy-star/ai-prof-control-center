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

    def test_control_center_health_profile_is_exact_and_read_only(self):
        profile = operations.resolve_profile(
            "ai-prof-control-center-health-check"
        )
        self.assertEqual(
            profile.repository,
            Path("/home/agent/projects/ai-prof-control-center-maintenance"),
        )
        self.assertEqual(profile.base_branch, "maintenance/base")
        self.assertEqual(profile.kind, "control-center-health-check")
        self.assertEqual(profile.expected_migration, "")

    def test_exact_repository_restriction_and_dirty_tree_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, profile = self.make_repo(Path(tmp))
            with self.assertRaises(operations.OperationBlocked):
                operations.validate_repository(profile, str(repo) + "/.", {})
            (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            with self.assertRaises(operations.OperationBlocked):
                operations.validate_repository(profile, str(repo), {})

    def test_subprocess_boundary_always_uses_shell_false(self):
        with (
            mock.patch.object(
                operations.subprocess, "run", return_value=completed(["git"]),
            ) as run,
        ):
            environment = {"PATH": "/nvm/bin:/usr/bin:/bin"}
            operations.run_argv(["git", "status"], Path("/tmp"), environment)
        self.assertIs(run.call_args.kwargs["shell"], False)
        self.assertEqual(run.call_args.args[0], ["git", "status"])
        self.assertIs(run.call_args.kwargs["env"], environment)

    def test_missing_path_supabase_receives_newest_nvm_node_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            versions = Path(tmp) / "versions"
            for version in ("v20.9.0", "v20.10.0", "v18.20.5"):
                node = versions / version / "bin/node"
                node.parent.mkdir(parents=True)
                node.write_text("#!/bin/sh\n", encoding="utf-8")
                node.chmod(0o755)
            with (
                mock.patch.object(operations, "NVM_NODE_VERSIONS", versions),
                mock.patch.dict(
                    operations.os.environ,
                    {
                        "PATH": "/usr/bin:/bin",
                        "SERVICE_MARKER": "copied",
                        "NODE_OPTIONS": "--require=/tmp/injected.js",
                    },
                    clear=True,
                ),
                mock.patch.object(
                    operations.subprocess, "run", return_value=completed(["supabase"]),
                ) as run,
            ):
                node_bin = operations.locate_node_bin()
                environment = operations.operation_environment(node_bin)
                operations.run_argv(
                    ["/repo/node_modules/.bin/supabase", "--version"],
                    Path("/repo"),
                    environment,
                )
            self.assertEqual(node_bin, versions / "v20.10.0/bin")
            self.assertEqual(
                run.call_args.kwargs["env"]["PATH"],
                f"{versions / 'v20.10.0/bin'}:/usr/bin:/bin",
            )
            self.assertEqual(run.call_args.kwargs["env"]["SERVICE_MARKER"], "copied")
            self.assertNotIn("NODE_OPTIONS", run.call_args.kwargs["env"])

    def test_missing_node_runtime_has_concise_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(operations, "NVM_NODE_VERSIONS", Path(tmp)):
                with self.assertRaisesRegex(
                    operations.OperationBlocked, "^NODE_RUNTIME_NOT_FOUND$",
                ):
                    operations.locate_node_bin()

    def test_secret_redaction(self):
        raw = (
            "Authorization: Bearer abc123 token=hidden password=hunter2 "
            "postgresql://user:pass@example/db secret=topsecret"
        )
        safe = operations.redact(raw)
        for secret in ("abc123", "hidden", "hunter2", "user:pass", "topsecret"):
            self.assertNotIn(secret, safe)

    def test_health_environment_removes_python_and_node_injection(self):
        with mock.patch.dict(
            operations.os.environ,
            {
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": "/tmp/injected",
                "PYTHONHOME": "/tmp/python",
                "PYTHONSTARTUP": "/tmp/startup.py",
                "NODE_OPTIONS": "--require=/tmp/injected.js",
                "SAFE_MARKER": "preserved",
            },
            clear=True,
        ):
            environment = operations.read_only_health_environment()
        self.assertEqual(environment["PATH"], "/usr/bin:/bin")
        self.assertEqual(environment["SAFE_MARKER"], "preserved")
        self.assertEqual(environment["PYTHONDONTWRITEBYTECODE"], "1")
        for name in (
            "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "NODE_OPTIONS"
        ):
            self.assertNotIn(name, environment)

    def test_read_only_health_runs_fixed_focused_and_full_checks(self):
        profile = operations.resolve_profile(
            "ai-prof-control-center-health-check"
        )
        repository = profile.repository
        calls: list[list[str]] = []

        def fake_run(argv, _repository, _environment, **_kwargs):
            calls.append(argv)
            return completed(argv)

        def fake_git(_repository, _environment, *args):
            if args == ("rev-parse", "--verify", "origin/main"):
                return "origin-main-sha"
            if args == ("rev-parse", "HEAD"):
                return "head-sha"
            if args == ("status", "--porcelain"):
                return ""
            self.fail(f"unexpected git query: {args}")

        with (
            mock.patch.object(
                operations, "validate_repository", return_value=repository
            ),
            mock.patch.object(operations, "git_output", side_effect=fake_git),
            mock.patch.object(operations, "run_argv", side_effect=fake_run),
        ):
            outcome = operations.execute_profile(profile, str(repository))
        self.assertEqual(
            calls[0],
            [
                str(operations.GIT_CLI), "merge-base", "--is-ancestor",
                "origin/main", "HEAD",
            ],
        )
        self.assertEqual(
            calls[1],
            [
                str(operations.PYTHON3_CLI), "-m", "unittest",
                *operations.HEALTH_TEST_MODULES,
            ],
        )
        self.assertEqual(
            calls[2],
            [str(operations.PYTHON3_CLI), "-m", "unittest"],
        )
        self.assertEqual(len(calls), 3)
        self.assertIn('"status":"PASS"', outcome)
        self.assertIn('"working_tree":"clean"', outcome)

    def test_read_only_health_blocks_stale_base_before_tests(self):
        profile = operations.resolve_profile(
            "ai-prof-control-center-health-check"
        )
        repository = profile.repository

        def fake_git(_repository, _environment, *args):
            if args == ("rev-parse", "--verify", "origin/main"):
                return "origin-main-sha"
            return "head-sha"

        with (
            mock.patch.object(
                operations, "validate_repository", return_value=repository
            ),
            mock.patch.object(operations, "git_output", side_effect=fake_git),
            mock.patch.object(
                operations,
                "run_argv",
                side_effect=operations.OperationFailed("not ancestor"),
            ) as run,
        ):
            with self.assertRaisesRegex(
                operations.OperationBlocked,
                "HEALTH_ORIGIN_MAIN_NOT_ANCESTOR",
            ):
                operations.execute_profile(profile, str(repository))
        self.assertEqual(run.call_count, 1)

    def test_read_only_health_fails_if_checks_mutate_head(self):
        profile = operations.resolve_profile(
            "ai-prof-control-center-health-check"
        )
        repository = profile.repository
        heads = iter(("head-before", "head-after"))

        def fake_git(_repository, _environment, *args):
            if args == ("rev-parse", "--verify", "origin/main"):
                return "origin-main-sha"
            if args == ("rev-parse", "HEAD"):
                return next(heads)
            return ""

        with (
            mock.patch.object(
                operations, "validate_repository", return_value=repository
            ),
            mock.patch.object(operations, "git_output", side_effect=fake_git),
            mock.patch.object(
                operations, "run_argv", return_value=completed([])
            ),
        ):
            with self.assertRaisesRegex(
                operations.OperationFailed, "changed repository HEAD"
            ):
                operations.execute_profile(profile, str(repository))

    def test_pending_migration_mismatch_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, profile = self.make_repo(Path(tmp))
            outputs = [
                completed([], "Local | Remote\n20260727000100 | \n"),
                completed([], "20260727000100_x.sql\n20260727000200_other.sql\n"),
            ]
            with (
                mock.patch.object(operations, "locate_node_bin", return_value=Path("/")),
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

            def fake_run(argv, _repository, _environment, **_kwargs):
                calls.append(argv)
                if argv[1].endswith("supabase"):
                    return completed(argv, listing)
                return completed(argv)

            with (
                mock.patch.object(operations, "locate_node_bin", return_value=Path("/")),
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

            def fake_run(argv, _repository, _environment, **_kwargs):
                calls.append(argv)
                if "migration" in argv and "list" in argv:
                    return completed(argv, next(listings))
                if "--dry-run" in argv:
                    return completed(argv, "20260727000100_manager_inspection_blocking_problem.sql\n")
                return completed(argv)

            with (
                mock.patch.object(operations, "locate_node_bin", return_value=Path("/")),
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
