#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "orchestrator" / "codex_stage01b_runner.py"
SPEC = importlib.util.spec_from_file_location("ai_prof_codex_stage01b_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load codex_stage01b_runner")
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


class CodexStage01BRunnerTests(unittest.TestCase):
    def workspace(self):
        temp = tempfile.TemporaryDirectory(prefix="ai-prof-claude-")
        path = Path(temp.name) / "workspace"
        path.mkdir()
        return temp, path

    def test_exact_workspace_write_argv(self):
        temp, workspace = self.workspace()
        self.addCleanup(temp.cleanup)
        cli = Path("/opt/codex")
        argv = runner.build_codex_stage01b_argv(cli, workspace)
        self.assertEqual(
            argv,
            [
                "/opt/codex",
                "-a",
                "never",
                "--disable",
                "plugins",
                "exec",
                "-s",
                "workspace-write",
                "--skip-git-repo-check",
                "--ephemeral",
                "-C",
                str(workspace.resolve()),
                "-",
            ],
        )
        runner.validate_codex_stage01b_argv(argv, cli, workspace)

    def test_rejects_non_isolated_workspace(self):
        with tempfile.TemporaryDirectory(prefix="wrong-prefix-") as temp:
            workspace = Path(temp) / "workspace"
            workspace.mkdir()
            with self.assertRaises(runner.core.SandboxExposureError):
                runner.validate_isolated_workspace(workspace)

    def test_rejects_git_metadata(self):
        temp, workspace = self.workspace()
        self.addCleanup(temp.cleanup)
        (workspace / ".git").mkdir()
        with self.assertRaises(runner.core.SandboxExposureError):
            runner.validate_isolated_workspace(workspace)

    def test_rejects_dangerous_or_changed_argv(self):
        temp, workspace = self.workspace()
        self.addCleanup(temp.cleanup)
        cli = Path("/opt/codex")
        argv = runner.build_codex_stage01b_argv(cli, workspace)
        for mutation in (
            argv + ["--full-auto"],
            [value for value in argv if value not in ("-a", "never")],
            [*argv[:3], "danger-full-access", *argv[4:]],
            [value for value in argv if value != "--skip-git-repo-check"],
            [value for value in argv if value not in ("--disable", "plugins")],
        ):
            with self.assertRaises(runner.CodexPolicyError):
                runner.validate_codex_stage01b_argv(mutation, cli, workspace)

    def test_invoke_uses_exact_workspace_and_trusted_header(self):
        temp, workspace = self.workspace()
        self.addCleanup(temp.cleanup)
        fake_cli = Path(temp.name) / "codex"
        fake_cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_cli.chmod(0o755)
        completed = subprocess.CompletedProcess([], 0, "done", "")
        with mock.patch.object(
            runner, "check_codex_available", return_value=fake_cli
        ), mock.patch.object(
            runner.subprocess, "run", return_value=completed
        ) as run:
            result = runner.invoke_codex(
                "TASK DATA", workspace, Path(temp.name) / "unused.json"
            )
        self.assertEqual(result.returncode, 0)
        argv = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertEqual(Path(kwargs["cwd"]).resolve(), workspace.resolve())
        self.assertEqual(Path(argv[argv.index("-C") + 1]).resolve(), workspace.resolve())
        self.assertTrue(kwargs["input"].startswith(runner._TRUSTED_HEADER))
        self.assertIn("TASK DATA", kwargs["input"])
        self.assertIn("do not run git init", kwargs["input"].lower())
        self.assertEqual(kwargs["env"]["GIT_DIR"], "/dev/null")
        self.assertEqual(
            Path(kwargs["env"]["GIT_WORK_TREE"]).resolve(),
            workspace.resolve(),
        )
        self.assertFalse(kwargs.get("shell", False))

    def test_core_model_seams_are_replaced(self):
        self.assertIs(runner.core.check_claude_available, runner.check_codex_available)
        self.assertIs(runner.core.invoke_claude, runner.invoke_codex)
        self.assertIs(
            runner.core.invoke_claude_with_retries,
            runner.invoke_codex_once,
        )
        self.assertIs(runner.core.ClaudeExecutionError, runner.CodexExecutionError)

    def test_log_rewrite_is_truthful(self):
        source = "\n".join(
            [
                "STAGE_01B_CLAUDE_PASS",
                "claude_attempts=[]",
                "sandbox=bubblewrap",
                "codex_launched=false",
            ]
        )
        rewritten = runner._rewrite_text(source)
        self.assertIn(runner.SUCCESS_MARKER, rewritten)
        self.assertIn("codex_attempts=[]", rewritten)
        self.assertIn("sandbox=codex-workspace-write+isolated-copy", rewritten)
        self.assertIn("codex_launched=true", rewritten)
        self.assertNotIn("STAGE_01B_CLAUDE_PASS", rewritten)

    def test_empty_agents_tree_is_normalized_before_scope_audit(self):
        temp, workspace = self.workspace()
        self.addCleanup(temp.cleanup)
        (workspace / ".agents" / "skills" / "nested").mkdir(parents=True)
        runner.audit_workspace_integrity_with_codex_normalization(workspace, [])
        self.assertFalse((workspace / ".agents").exists())

    def test_nonempty_agents_tree_remains_blocked(self):
        temp, workspace = self.workspace()
        self.addCleanup(temp.cleanup)
        agents = workspace / ".agents"
        agents.mkdir()
        (agents / "metadata.json").write_text("{}\n", encoding="utf-8")
        with self.assertRaises(runner.core.ScopeAccessError):
            runner.audit_workspace_integrity_with_codex_normalization(workspace, [])
        self.assertTrue((agents / "metadata.json").is_file())

    def test_root_codex_metadata_is_discarded_before_scope_audit(self):
        temp, workspace = self.workspace()
        self.addCleanup(temp.cleanup)
        codex_root = workspace / ".codex"
        (codex_root / "runtime").mkdir(parents=True)
        (codex_root / "runtime" / "session.json").write_text(
            "{}\n", encoding="utf-8"
        )
        runner.audit_workspace_integrity_with_codex_normalization(workspace, [])
        self.assertFalse(codex_root.exists())

    def test_nested_codex_metadata_remains_blocked(self):
        temp, workspace = self.workspace()
        self.addCleanup(temp.cleanup)
        source = workspace / "src"
        (source / ".codex").mkdir(parents=True)
        entry = runner.core.ScopeEntry(
            relative="src", absolute=source, is_dir=True, exists=True
        )
        with self.assertRaises(runner.core.ScopeAccessError):
            runner.audit_workspace_integrity_with_codex_normalization(
                workspace, [entry]
            )
        self.assertTrue((source / ".codex").is_dir())

    def test_symlink_in_root_codex_metadata_remains_blocked(self):
        temp, workspace = self.workspace()
        self.addCleanup(temp.cleanup)
        codex_root = workspace / ".codex"
        codex_root.mkdir()
        target = Path(temp.name) / "outside.txt"
        target.write_text("outside\n", encoding="utf-8")
        (codex_root / "link").symlink_to(target)
        with self.assertRaises(runner.core.ScopeAccessError):
            runner.audit_workspace_integrity_with_codex_normalization(
                workspace, []
            )
        self.assertTrue((codex_root / "link").is_symlink())

    def test_root_git_metadata_is_discarded_before_scope_audit(self):
        temp, workspace = self.workspace()
        self.addCleanup(temp.cleanup)
        git_root = workspace / ".git"
        (git_root / "objects").mkdir(parents=True)
        (git_root / "config").write_text("[core]\n", encoding="utf-8")
        runner.audit_workspace_integrity_with_codex_normalization(workspace, [])
        self.assertFalse(git_root.exists())

    def test_nested_git_metadata_remains_blocked(self):
        temp, workspace = self.workspace()
        self.addCleanup(temp.cleanup)
        source = workspace / "src"
        (source / ".git").mkdir(parents=True)
        entry = runner.core.ScopeEntry(
            relative="src", absolute=source, is_dir=True, exists=True
        )
        with self.assertRaises(runner.core.ScopeAccessError):
            runner.audit_workspace_integrity_with_codex_normalization(
                workspace, [entry]
            )
        self.assertTrue((source / ".git").is_dir())

    def test_git_init_is_blocked_without_creating_metadata(self):
        temp, workspace = self.workspace()
        self.addCleanup(temp.cleanup)
        env = runner.build_codex_environment(workspace)
        result = subprocess.run(
            ["git", "init"],
            cwd=str(workspace),
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((workspace / ".git").exists())

    def test_codex_retry_policy_is_single_attempt(self):
        temp, workspace = self.workspace()
        self.addCleanup(temp.cleanup)
        completed = subprocess.CompletedProcess([], 9, "", "failure")
        with mock.patch.object(
            runner, "invoke_codex", return_value=completed
        ) as invoke:
            result, evidence = runner.invoke_codex_once(
                "TASK", workspace, Path(temp.name) / "unused.json"
            )
        self.assertEqual(result.returncode, 9)
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual(len(evidence), 1)
        self.assertFalse(evidence[0]["retried"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
