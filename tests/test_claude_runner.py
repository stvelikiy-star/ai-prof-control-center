from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

# Captured before any mock.patch.object(cr.shutil, "which", ...) call. cr.shutil
# IS this same shutil module object (modules are singletons), so patching one
# patches the other; fake_which must delegate to this pre-patch reference,
# never to shutil.which itself, to avoid infinite recursion.
_REAL_WHICH = shutil.which

MODULE_PATH = Path(__file__).resolve().parents[1] / "orchestrator" / "claude_runner.py"
SPEC = importlib.util.spec_from_file_location("ai_prof_claude_runner", MODULE_PATH)
cr = importlib.util.module_from_spec(SPEC)
if SPEC.loader is None:
    raise RuntimeError("Cannot load claude_runner module")
sys.modules[SPEC.name] = cr
SPEC.loader.exec_module(cr)

orch = cr.orch


def run_git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True,
    ).stdout


def init_git_project(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    (repo / "secret.txt").write_text("outside-scope-secret\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)


def snapshot_git_state(repo: Path) -> dict:
    head = run_git(repo, "rev-parse", "HEAD").strip()
    branch = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    status = run_git(repo, "status", "--porcelain").strip()
    file_hashes = {}
    for path in sorted(repo.rglob("*")):
        if ".git" in path.parts:
            continue
        if path.is_file():
            file_hashes[str(path.relative_to(repo))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"head": head, "branch": branch, "status": status, "file_hashes": file_hashes}


class ClaudeRunnerTests(unittest.TestCase):
    def make_context(self, root: Path) -> Path:
        context = root / "agents" / "ak-bermet"
        context.mkdir(parents=True)
        for name in cr.CLAUDE_CONTEXT_FILES:
            (context / name).write_text(f"{name}\ncontent\n", encoding="utf-8")
        return context

    def make_task(self, path: Path, **overrides: str) -> None:
        values = {
            "Task-ID": "TEST-01B-001",
            "Project-Path": "/tmp/project",
            "Base-Branch": "main",
            "Work-Branch": "feature/test",
            "Agent-Context": "agents/ak-bermet",
            "Goal": "Test",
            "Scope": "Implementation",
            "Out-of-Scope": "Merge, push, deploy",
            "Pass-Criteria": "PASS",
            "Required-Checks": "none",
            "Required-Commands": "git, python3",
            "Required-Environment": "none",
            "Owner-Approval-Required": "no",
            "Scope-Files": "tracked.txt",
        }
        values.update(overrides)
        path.write_text(
            "\n".join(f"{key}: {value}" for key, value in values.items()) + "\n",
            encoding="utf-8",
        )

    def fake_which(self, cmd):
        # sys.executable is guaranteed to exist and be executable on this
        # machine, so check_claude_available()'s real stat()/X_OK checks
        # succeed the same way they would for a real installed Claude CLI.
        if cmd == cr.CLAUDE_CLI:
            return sys.executable
        return _REAL_WHICH(cmd)

    def require_working_bwrap(self):
        probe = subprocess.run(
            [
                cr.BWRAP_CLI, "--unshare-all", "--share-net",
                "--die-with-parent", "--new-session", "--ro-bind", "/", "/",
                "--", "/bin/true",
            ],
            text=True, capture_output=True,
        )
        if probe.returncode != 0:
            self.skipTest(f"Bubblewrap unavailable on this host: {probe.stderr.strip()}")

    def run_fake_in_bwrap(self, script: str, workspace: Path, scratch: Path):
        self.require_working_bwrap()
        fake = scratch.parent / "fake-claude"
        fake.write_text("#!/usr/bin/python3\n" + script, encoding="utf-8")
        fake.chmod(0o755)
        argv = cr.build_bwrap_argv(
            Path(cr.BWRAP_CLI), fake, workspace, scratch,
            [str(fake)], home_dir=scratch.parent / "empty-home", auth_env=[],
        )
        return subprocess.run(argv, text=True, capture_output=True, timeout=15)

    # -- queue movement ------------------------------------------------------

    def test_success_moves_review_to_pending_codex(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(cr, "invoke_claude", return_value=SimpleNamespace(returncode=0, stdout="ok", stderr="")):
                result = cr.process_one(paths)

            self.assertEqual(result, 0)
            self.assertFalse((paths.review / "task.md").exists())
            self.assertFalse((paths.active / "task.md").exists())
            self.assertTrue((paths.pending_codex / "task.md").exists())

            logs = list(paths.logs.glob("*.log"))
            self.assertEqual(len(logs), 1)
            log_text = logs[0].read_text(encoding="utf-8")
            self.assertIn("STAGE_01B_CLAUDE_PASS", log_text)
            self.assertIn("codex_launched=false", log_text)
            self.assertIn("merge_capability=false", log_text)
            self.assertIn("scope_files=tracked.txt", log_text)

    def test_claude_failure_moves_review_to_failed(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(cr, "invoke_claude", return_value=SimpleNamespace(returncode=1, stdout="", stderr="token=supersecretvalue")):
                result = cr.process_one(paths)

            self.assertEqual(result, 1)
            self.assertFalse((paths.review / "task.md").exists())
            self.assertFalse((paths.pending_codex / "task.md").exists())
            self.assertTrue((paths.failed / "task.md").exists())

            logs = list(paths.logs.glob("*.log"))
            log_text = logs[0].read_text(encoding="utf-8")
            self.assertIn("CLAUDE_FAILED", log_text)
            # Secret must be redacted even in the failure log.
            self.assertNotIn("supersecretvalue", log_text)

    # -- branch validation -----------------------------------------------------

    def test_invalid_work_branch_moves_to_blocked(self):
        for bad_branch in ["main", "develop", "master", "release/1", "random-branch"]:
            with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
                cc_root = Path(cc_tmp)
                project = Path(proj_tmp) / "project"
                init_git_project(project)
                self.make_context(cc_root)
                paths = cr.build_claude_paths(cc_root)
                task_path = paths.review / "task.md"
                self.make_task(task_path, **{"Project-Path": str(project), "Work-Branch": bad_branch})

                with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which):
                    result = cr.process_one(paths)

                self.assertEqual(result, 1, bad_branch)
                self.assertTrue((paths.blocked / "task.md").exists(), bad_branch)
                log_text = list(paths.logs.glob("*.log"))[-1].read_text(encoding="utf-8")
                self.assertIn("BLOCKED_INVALID_BRANCH", log_text)

    def test_invalid_base_branch_moves_to_blocked(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project), "Base-Branch": "staging"})

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which):
                result = cr.process_one(paths)

            self.assertEqual(result, 1)
            self.assertTrue((paths.blocked / "task.md").exists())

    def test_codex_retry_accepts_only_scoped_dirty_work_branch(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            subprocess.run(["git", "checkout", "-qb", "feature/test"], cwd=project, check=True)
            (project / "tracked.txt").write_text("first attempt\n", encoding="utf-8")
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})
            task_path.write_text(
                task_path.read_text(encoding="utf-8") + "Codex-Review-Attempt: 1\n",
                encoding="utf-8",
            )

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(
                     cr, "invoke_claude",
                     return_value=SimpleNamespace(returncode=0, stdout="ok", stderr=""),
                 ):
                result = cr.process_one(paths)

            self.assertEqual(result, 0)
            self.assertEqual(run_git(project, "branch", "--show-current").strip(), "feature/test")
            self.assertTrue((paths.pending_codex / "task.md").exists())

    def test_codex_retry_rejects_outside_scope_dirty_file(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            subprocess.run(["git", "checkout", "-qb", "feature/test"], cwd=project, check=True)
            (project / "secret.txt").write_text("outside retry\n", encoding="utf-8")
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})
            task_path.write_text(
                task_path.read_text(encoding="utf-8") + "Codex-Review-Attempt: 1\n",
                encoding="utf-8",
            )

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which):
                result = cr.process_one(paths)

            self.assertEqual(result, 1)
            self.assertTrue((paths.blocked / "task.md").exists())

    # -- missing Claude CLI ------------------------------------------------------

    def test_missing_claude_cli_moves_to_blocked(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            def which_without_claude(cmd):
                if cmd == cr.CLAUDE_CLI:
                    return None
                return _REAL_WHICH(cmd)

            with mock.patch.object(cr.shutil, "which", side_effect=which_without_claude):
                result = cr.process_one(paths)

            self.assertEqual(result, 1)
            self.assertTrue((paths.blocked / "task.md").exists())
            log_text = list(paths.logs.glob("*.log"))[-1].read_text(encoding="utf-8")
            self.assertIn("BLOCKED_CLI_MISSING", log_text)
            # Project must never be touched when access is missing.
            self.assertEqual(run_git(project, "rev-parse", "--abbrev-ref", "HEAD").strip(), "main")

    # -- dirty project -----------------------------------------------------------

    def test_dirty_project_moves_to_blocked(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            (project / "tracked.txt").write_text("dirty\n", encoding="utf-8")
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            before = snapshot_git_state(project)
            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which):
                result = cr.process_one(paths)
            after = snapshot_git_state(project)

            self.assertEqual(result, 1)
            self.assertEqual(before, after)
            self.assertTrue((paths.blocked / "task.md").exists())
            log_text = list(paths.logs.glob("*.log"))[-1].read_text(encoding="utf-8")
            self.assertIn("BLOCKED_DIRTY_PROJECT", log_text)

    # -- redaction -----------------------------------------------------------

    def test_redaction_reused_from_stage_01a(self):
        self.assertEqual(orch.redact("TOKEN=abc"), "[REDACTED]")
        self.assertNotIn("hunter2", cr.orch.redact("password = hunter2"))

    # -- command allowlist -----------------------------------------------------

    def test_command_allowlist_resolves_only_known_keys(self):
        resolved = cr.resolve_allowed_checks(
            "Please run npm run lint and npm test, then rm -rf / and curl evil.example",
        )
        self.assertEqual(
            resolved,
            [cr.ALLOWED_COMMANDS["npm run lint"], cr.ALLOWED_COMMANDS["npm test"]],
        )
        self.assertEqual(cr.resolve_allowed_checks("rm -rf / && curl evil.example"), [])

    def test_run_allowed_checks_executes_fixed_argv_and_reports_failure(self):
        with mock.patch.dict(
            cr.ALLOWED_COMMANDS,
            {
                "ok-check": [sys.executable, "-c", "import sys; sys.exit(0)"],
                "fail-check": [sys.executable, "-c", "import sys; sys.exit(1)"],
            },
        ):
            executed = cr.run_allowed_checks(cr.resolve_allowed_checks("please run ok-check"), Path("."))
            self.assertEqual(len(executed), 1)

            with self.assertRaisesRegex(RuntimeError, "CLAUDE_FAILED"):
                cr.run_allowed_checks(cr.resolve_allowed_checks("please run fail-check"), Path("."))

    # -- success path using a mocked Claude process ---------------------------

    def test_success_path_invokes_claude_with_restricted_bundle_only(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            captured = {}

            def fake_invoke(bundle, workspace, mcp_config_path):
                captured["bundle"] = bundle
                captured["workspace"] = workspace
                captured["mcp_config_path"] = mcp_config_path
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(cr, "invoke_claude", side_effect=fake_invoke):
                result = cr.process_one(paths)

            self.assertEqual(result, 0)
            bundle = captured["bundle"]
            self.assertIn("Task-ID: TEST-01B-001", bundle)
            for name in cr.CLAUDE_CONTEXT_FILES:
                self.assertIn(name, bundle)
                self.assertIn("content", bundle)
            # Nothing outside the task text and the 5 context files is present.
            self.assertNotIn("KNOWLEDGE_BASE", bundle)
            self.assertNotIn("ROADMAP", bundle)

    # -- failure path ----------------------------------------------------------

    def test_failure_path_does_not_move_to_pending_codex(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(cr, "invoke_claude", return_value=SimpleNamespace(returncode=2, stdout="", stderr="build error")):
                result = cr.process_one(paths)

            self.assertEqual(result, 1)
            self.assertFalse((paths.pending_codex / "task.md").exists())
            self.assertTrue((paths.failed / "task.md").exists())

    # =========================================================================
    # Blocker 1 + 2: enforced Claude capability policy, built and validated
    # before subprocess.run is ever called.
    # =========================================================================

    def test_build_claude_argv_is_exact_and_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            mcp_config = cr.build_claude_mcp_config(Path(tmp))
            argv = cr.build_claude_argv(mcp_config)

            self.assertEqual(argv[0], cr.CLAUDE_CLI)
            for flag in [
                "-p", "--safe-mode", "--no-session-persistence",
                "--disable-slash-commands", "--no-chrome", "--strict-mcp-config",
            ]:
                self.assertIn(flag, argv)
            self.assertEqual(cr._flag_value(argv, "--output-format"), "json")
            self.assertEqual(cr._flag_value(argv, "--permission-mode"), "dontAsk")
            self.assertEqual(cr._flag_value(argv, "--mcp-config"), str(mcp_config))
            self.assertEqual(cr._flag_value(argv, "--tools"), ",".join(cr.CLAUDE_SAFE_TOOLS))
            self.assertEqual(cr._flag_value(argv, "--allowed-tools"), ",".join(cr.CLAUDE_ALLOWED_TOOLS))
            self.assertEqual(cr._flag_value(argv, "--disallowed-tools"), ",".join(cr.CLAUDE_DISALLOWED_TOOLS))

            # No Bash, shell, network, browser, MCP, subagent, deploy, or Git
            # push/merge tool is ever granted.
            granted = set(cr._flag_value(argv, "--tools").split(","))
            for banned in ("Bash", "WebFetch", "WebSearch", "Task", "mcp__*"):
                self.assertNotIn(banned, granted)

            # And --bare must never be used (breaks OAuth/keychain auth).
            self.assertNotIn("--bare", argv)

            cr.validate_claude_argv(argv, mcp_config)

    def test_build_claude_argv_never_contains_dangerous_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            mcp_config = cr.build_claude_mcp_config(Path(tmp))
            argv = cr.build_claude_argv(mcp_config)
            joined = " ".join(argv)
            for dangerous in cr.CLAUDE_DANGEROUS_FLAGS:
                self.assertNotIn(dangerous, argv)
                self.assertNotIn(dangerous, joined)
            self.assertNotIn("bypassPermissions", argv)

    def test_validate_claude_argv_fails_closed_on_bypass_permission_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            mcp_config = cr.build_claude_mcp_config(Path(tmp))
            argv = cr.build_claude_argv(mcp_config)
            index = argv.index("--permission-mode")
            argv[index + 1] = "bypassPermissions"
            with self.assertRaises(cr.ClaudePolicyError):
                cr.validate_claude_argv(argv, mcp_config)

    def test_validate_claude_argv_fails_closed_on_dangerous_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            mcp_config = cr.build_claude_mcp_config(Path(tmp))
            argv = cr.build_claude_argv(mcp_config) + ["--dangerously-skip-permissions"]
            with self.assertRaises(cr.ClaudePolicyError):
                cr.validate_claude_argv(argv, mcp_config)

    def test_validate_claude_argv_fails_closed_on_broad_tool_grant(self):
        with tempfile.TemporaryDirectory() as tmp:
            mcp_config = cr.build_claude_mcp_config(Path(tmp))
            argv = cr.build_claude_argv(mcp_config)
            for flag in ("--tools", "--allowed-tools"):
                index = argv.index(flag)
                argv[index + 1] = "Bash"
                with self.assertRaises(cr.ClaudePolicyError):
                    cr.validate_claude_argv(argv, mcp_config)
                argv[index + 1] = ",".join(cr.CLAUDE_SAFE_TOOLS)

    def test_validate_claude_argv_fails_closed_on_missing_required_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            mcp_config = cr.build_claude_mcp_config(Path(tmp))
            argv = cr.build_claude_argv(mcp_config)
            argv.remove("--strict-mcp-config")
            with self.assertRaises(cr.ClaudePolicyError):
                cr.validate_claude_argv(argv, mcp_config)

    def test_validate_claude_argv_fails_closed_on_non_empty_mcp_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            mcp_config = cr.build_claude_mcp_config(Path(tmp))
            mcp_config.write_text('{"mcpServers": {"evil": {}}}', encoding="utf-8")
            argv = cr.build_claude_argv(mcp_config)
            with self.assertRaises(cr.ClaudePolicyError):
                cr.validate_claude_argv(argv, mcp_config)

    def test_validate_claude_argv_fails_closed_on_weak_disallowed_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            mcp_config = cr.build_claude_mcp_config(Path(tmp))
            argv = cr.build_claude_argv(mcp_config)
            index = argv.index("--disallowed-tools")
            argv[index + 1] = "NotebookEdit"
            with self.assertRaises(cr.ClaudePolicyError):
                cr.validate_claude_argv(argv, mcp_config)

    def test_invoke_claude_never_calls_subprocess_when_policy_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()

            def broken_argv(_mcp_config_path):
                return [cr.CLAUDE_CLI, "-p", "--dangerously-skip-permissions"]

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(cr, "build_claude_argv", side_effect=broken_argv), \
                 mock.patch.object(cr.subprocess, "run") as run_mock:
                with self.assertRaises(cr.ClaudePolicyError):
                    cr.invoke_claude("bundle", workspace, workspace / "mcp.json")
                run_mock.assert_not_called()

    def test_invoke_claude_calls_subprocess_when_policy_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            scratch = Path(tmp) / "scratch"
            scratch.mkdir()
            mcp_config = cr.build_claude_mcp_config(scratch)

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(
                     cr.subprocess, "run",
                     return_value=SimpleNamespace(returncode=0, stdout="ok", stderr=""),
                 ) as run_mock:
                cr.invoke_claude("bundle", workspace, mcp_config)
                run_mock.assert_called_once()
                called_argv = run_mock.call_args.args[0]
                self.assertEqual(called_argv[0], cr.BWRAP_CLI)
                self.assertIn("--die-with-parent", called_argv)
                self.assertIn("--unshare-all", called_argv)
                self.assertIn("--share-net", called_argv)
                self.assertIn("--clearenv", called_argv)
                self.assertIn("--new-session", called_argv)
                bind_index = called_argv.index("--bind")
                self.assertEqual(called_argv[bind_index + 1], str(workspace.resolve()))
                self.assertEqual(called_argv[bind_index + 2], cr.SANDBOX_WORKSPACE)
                chdir_index = called_argv.index("--chdir")
                self.assertEqual(called_argv[chdir_index + 1], cr.SANDBOX_WORKSPACE)
                self.assertIn(sys.executable, called_argv)
                self.assertNotIn(str(workspace.resolve()), called_argv[chdir_index + 1:])
                self.assertNotIn(str(Path.home()), called_argv)
                self.assertNotIn("--dangerously-skip-permissions", called_argv)
                self.assertNotIn("cwd", run_mock.call_args.kwargs)

    # =========================================================================
    # Blocker 3: isolated workspace and approved-scope enforcement.
    # =========================================================================

    def test_scope_declaration_required_or_task_is_blocked(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project), "Scope-Files": "none"})

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which):
                result = cr.process_one(paths)

            self.assertEqual(result, 1)
            self.assertTrue((paths.blocked / "task.md").exists())
            log_text = list(paths.logs.glob("*.log"))[-1].read_text(encoding="utf-8")
            self.assertIn("BLOCKED_SCOPE_ACCESS", log_text)

    def test_resolve_scope_entries_blocks_path_traversal(self):
        with tempfile.TemporaryDirectory() as proj_tmp:
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            with self.assertRaises(cr.ScopeAccessError):
                cr.resolve_scope_entries(project, ["../outside.txt"])
            with self.assertRaises(cr.ScopeAccessError):
                cr.resolve_scope_entries(project, ["/etc/passwd"])

    def test_resolve_scope_entries_blocks_symlink_escape(self):
        with tempfile.TemporaryDirectory() as proj_tmp, tempfile.TemporaryDirectory() as outside_tmp:
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            outside_secret = Path(outside_tmp) / "outside_secret.txt"
            outside_secret.write_text("host-secret\n", encoding="utf-8")
            link = project / "escape_link"
            link.symlink_to(outside_secret)

            # A symlink whose resolved target escapes the project is rejected
            # up front, before it can ever be copied into the workspace.
            with self.assertRaises(cr.ScopeAccessError):
                cr.resolve_scope_entries(project, ["escape_link"])

    def test_isolated_workspace_exposes_only_approved_scope(self):
        with tempfile.TemporaryDirectory() as proj_tmp, tempfile.TemporaryDirectory() as ws_tmp:
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            entries = cr.resolve_scope_entries(project, ["tracked.txt"])
            workspace = Path(ws_tmp)
            cr.build_isolated_workspace(workspace, project, entries)

            visible = sorted(p.name for p in workspace.rglob("*") if p.is_file())
            self.assertEqual(visible, ["tracked.txt"])
            self.assertNotIn("secret.txt", visible)
            self.assertFalse((workspace / ".git").exists())

    def test_workspace_diff_outside_scope_is_rejected(self):
        with tempfile.TemporaryDirectory() as proj_tmp, tempfile.TemporaryDirectory() as ws_tmp:
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            entries = cr.resolve_scope_entries(project, ["tracked.txt"])
            workspace = Path(ws_tmp)
            before = cr.build_isolated_workspace(workspace, project, entries)

            # Claude "escapes" by writing a file outside the approved scope.
            (workspace / "unapproved.txt").write_text("sneaky\n", encoding="utf-8")

            changes = cr.compute_workspace_diff(workspace, before)
            with self.assertRaises(cr.ScopeAccessError):
                cr.validate_workspace_changes(changes, entries)

    def test_workspace_diff_within_scope_is_accepted_and_applied(self):
        with tempfile.TemporaryDirectory() as proj_tmp, tempfile.TemporaryDirectory() as ws_tmp:
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            entries = cr.resolve_scope_entries(project, ["tracked.txt"])
            workspace = Path(ws_tmp)
            before = cr.build_isolated_workspace(workspace, project, entries)

            (workspace / "tracked.txt").write_text("updated by claude\n", encoding="utf-8")

            changes = cr.compute_workspace_diff(workspace, before)
            cr.validate_workspace_changes(changes, entries)
            change_set = cr.build_change_set(workspace, changes)
            applied = cr.apply_validated_changes(project, change_set, entries)

            self.assertEqual(applied, ["modified:tracked.txt"])
            self.assertEqual((project / "tracked.txt").read_text(encoding="utf-8"), "updated by claude\n")
            # File outside scope must remain untouched.
            self.assertEqual((project / "secret.txt").read_text(encoding="utf-8"), "outside-scope-secret\n")

    def test_success_path_claude_cwd_is_isolated_not_target_project(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            captured = {}

            def fake_invoke(bundle, workspace, mcp_config_path):
                captured["workspace"] = workspace
                captured["visible"] = sorted(p.name for p in workspace.rglob("*") if p.is_file())
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(cr, "invoke_claude", side_effect=fake_invoke):
                result = cr.process_one(paths)

            self.assertEqual(result, 0)
            workspace = captured["workspace"]
            self.assertNotEqual(workspace.resolve(), project.resolve())
            self.assertNotIn(project.resolve(), [workspace.resolve()])
            self.assertEqual(captured["visible"], ["tracked.txt"])
            self.assertNotIn("secret.txt", captured["visible"])
            # The Control Center root itself must not be exposed inside the workspace.
            self.assertFalse((workspace / "agents").exists())

    # =========================================================================
    # Blocker 4: target immutability before and during Claude execution.
    # =========================================================================

    def test_target_project_untouched_before_and_during_claude_invocation(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            pre_snapshot = snapshot_git_state(project)
            captured = {}

            def fake_invoke(bundle, workspace, mcp_config_path):
                captured["during"] = snapshot_git_state(project)
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(cr, "invoke_claude", side_effect=fake_invoke):
                result = cr.process_one(paths)

            self.assertEqual(result, 0)
            during = captured["during"]
            # The real project must be byte-for-byte identical before and
            # during Claude's execution: same branch, same HEAD, same status,
            # same tracked file contents. No branch may be created early.
            self.assertEqual(during, pre_snapshot)
            self.assertEqual(pre_snapshot["branch"], "main")

            # Only after Claude succeeded may the work branch be created.
            after_snapshot = snapshot_git_state(project)
            self.assertEqual(after_snapshot["branch"], "feature/test")

    def test_ensure_work_branch_not_called_before_claude_succeeds(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            call_order = []
            real_ensure = cr.ensure_work_branch

            def tracking_ensure(*args, **kwargs):
                call_order.append("ensure_work_branch")
                return real_ensure(*args, **kwargs)

            def fake_invoke(bundle, workspace, mcp_config_path):
                call_order.append("invoke_claude")
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(cr, "invoke_claude", side_effect=fake_invoke), \
                 mock.patch.object(cr, "ensure_work_branch", side_effect=tracking_ensure):
                result = cr.process_one(paths)

            self.assertEqual(result, 0)
            self.assertEqual(call_order, ["invoke_claude", "ensure_work_branch"])

    def test_target_unchanged_when_claude_fails(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            before = snapshot_git_state(project)
            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(cr, "invoke_claude", return_value=SimpleNamespace(returncode=1, stdout="", stderr="build error")):
                result = cr.process_one(paths)
            after = snapshot_git_state(project)

            self.assertEqual(result, 1)
            self.assertEqual(before, after)

    # =========================================================================
    # Blocker 5: complete access-failure classification.
    # =========================================================================

    def test_missing_project_path_moves_to_blocked(self):
        with tempfile.TemporaryDirectory() as cc_tmp:
            cc_root = Path(cc_tmp)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": "/nonexistent/does/not/exist"})

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which):
                result = cr.process_one(paths)

            self.assertEqual(result, 1)
            self.assertTrue((paths.blocked / "task.md").exists())
            log_text = list(paths.logs.glob("*.log"))[-1].read_text(encoding="utf-8")
            self.assertIn("BLOCKED_PROJECT_ACCESS", log_text)

    def test_check_project_accessible_raises_on_permission_denied(self):
        with tempfile.TemporaryDirectory() as proj_tmp:
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            with mock.patch.object(cr.Path, "stat", side_effect=PermissionError("denied")):
                with self.assertRaises(cr.PermissionAccessError):
                    cr.check_project_accessible(project)
            queue_name, status_code = cr.classify_failure(cr.PermissionAccessError("x"))
            self.assertEqual(queue_name, "blocked")
            self.assertEqual(status_code, "BLOCKED_PERMISSION_DENIED")

    def test_unreadable_context_file_moves_to_blocked(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            real_read_text = cr.Path.read_text

            def denied_read_text(self_path, *args, **kwargs):
                if self_path.name in cr.CLAUDE_CONTEXT_FILES:
                    raise PermissionError("denied")
                return real_read_text(self_path, *args, **kwargs)

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(cr.Path, "read_text", denied_read_text):
                result = cr.process_one(paths)

            self.assertEqual(result, 1)
            self.assertTrue((paths.blocked / "task.md").exists())
            log_text = list(paths.logs.glob("*.log"))[-1].read_text(encoding="utf-8")
            self.assertIn("BLOCKED_PERMISSION_DENIED", log_text)

    def test_claude_auth_failure_moves_to_blocked(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(
                     cr, "invoke_claude",
                     return_value=SimpleNamespace(returncode=1, stdout="", stderr="Error: not authenticated. Please run `claude login`."),
                 ):
                result = cr.process_one(paths)

            self.assertEqual(result, 1)
            self.assertTrue((paths.blocked / "task.md").exists())
            log_text = list(paths.logs.glob("*.log"))[-1].read_text(encoding="utf-8")
            self.assertIn("BLOCKED_CLAUDE_AUTH", log_text)

    def test_genuine_claude_failure_stays_failed_not_blocked(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(
                     cr, "invoke_claude",
                     return_value=SimpleNamespace(returncode=1, stdout="", stderr="TypeError: generated code is broken"),
                 ):
                result = cr.process_one(paths)

            self.assertEqual(result, 1)
            self.assertTrue((paths.failed / "task.md").exists())
            log_text = list(paths.logs.glob("*.log"))[-1].read_text(encoding="utf-8")
            self.assertIn("CLAUDE_FAILED", log_text)

    def test_git_checkout_access_failure_moves_to_blocked(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            task_path = paths.review / "task.md"
            self.make_task(task_path, **{"Project-Path": str(project)})

            real_run = cr.subprocess.run

            def failing_checkout(argv, *args, **kwargs):
                if argv[:2] == ["git", "checkout"]:
                    raise subprocess.CalledProcessError(1, argv)
                return real_run(argv, *args, **kwargs)

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(cr, "invoke_claude", return_value=SimpleNamespace(returncode=0, stdout="ok", stderr="")), \
                 mock.patch.object(cr.subprocess, "run", side_effect=failing_checkout):
                result = cr.process_one(paths)

            self.assertEqual(result, 1)
            self.assertTrue((paths.blocked / "task.md").exists())
            log_text = list(paths.logs.glob("*.log"))[-1].read_text(encoding="utf-8")
            self.assertIn("BLOCKED_BRANCH_ACCESS", log_text)

    def test_git_repository_inaccessible_moves_to_blocked(self):
        with tempfile.TemporaryDirectory() as proj_tmp:
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            with mock.patch.object(
                cr.subprocess, "run",
                side_effect=FileNotFoundError("git binary not found"),
            ):
                with self.assertRaises(cr.GitAccessError):
                    cr.current_branch(project)

    def test_raw_absolute_windows_unc_backslash_and_traversal_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as proj_tmp:
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            for raw in (
                "/etc/passwd", "C:\\Windows\\win.ini", "\\\\server\\share",
                "dir\\file.txt", "../outside", "dir/../outside", "./tracked.txt",
            ):
                with self.subTest(raw=raw), self.assertRaises(cr.ScopeAccessError):
                    cr.resolve_scope_entries(project, [raw])

    def test_parent_and_final_component_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as proj_tmp:
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            real_dir = project / "real"
            real_dir.mkdir()
            (real_dir / "file.txt").write_text("x", encoding="utf-8")
            (project / "parent-link").symlink_to(real_dir, target_is_directory=True)
            (project / "file-link").symlink_to(real_dir / "file.txt")
            for raw in ("parent-link/file.txt", "file-link"):
                with self.subTest(raw=raw), self.assertRaises(cr.ScopeAccessError):
                    cr.resolve_scope_entries(project, [raw])

    def test_workspace_unexpected_files_symlinks_and_special_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as proj_tmp, tempfile.TemporaryDirectory() as ws_tmp:
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            entries = cr.resolve_scope_entries(project, ["tracked.txt"])
            workspace = Path(ws_tmp)
            cr.build_isolated_workspace(workspace, project, entries)
            (workspace / "unexpected.txt").write_text("no", encoding="utf-8")
            with self.assertRaises(cr.ScopeAccessError):
                cr.audit_workspace_integrity(workspace, entries)
            (workspace / "unexpected.txt").unlink()
            (workspace / "tracked-link").symlink_to("tracked.txt")
            with self.assertRaises(cr.ScopeAccessError):
                cr.audit_workspace_integrity(workspace, entries)
            (workspace / "tracked-link").unlink()
            fifo = workspace / "tracked.fifo"
            os.mkfifo(fifo)
            with self.assertRaises(cr.ScopeAccessError):
                cr.audit_workspace_integrity(workspace, entries)

    def test_apply_revalidates_destination_and_rolls_back_entire_batch(self):
        with tempfile.TemporaryDirectory() as proj_tmp:
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            (project / "second.txt").write_text("second-base\n", encoding="utf-8")
            entries = cr.resolve_scope_entries(project, ["tracked.txt", "second.txt"])
            change_set = [
                ("tracked.txt", "modified", b"first-new\n"),
                ("second.txt", "modified", b"second-new\n"),
            ]
            real_write = cr._write_atomic
            calls = 0

            def fail_second(directory, filename, content):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise cr.PatchAccessError("simulated atomic apply failure")
                return real_write(directory, filename, content)

            with mock.patch.object(cr, "_write_atomic", side_effect=fail_second):
                with self.assertRaises(cr.PatchAccessError):
                    cr.apply_validated_changes(project, change_set, entries)
            self.assertEqual((project / "tracked.txt").read_text(), "base\n")
            self.assertEqual((project / "second.txt").read_text(), "second-base\n")

            outside = project.parent / "outside.txt"
            outside.write_text("outside\n")
            (project / "tracked.txt").unlink()
            (project / "tracked.txt").symlink_to(outside)
            with self.assertRaises(cr.ScopeAccessError):
                cr.apply_validated_changes(project, [change_set[0]], entries)
            self.assertEqual(outside.read_text(), "outside\n")

    def test_context_only_content_is_never_copied_back(self):
        with tempfile.TemporaryDirectory() as proj_tmp, tempfile.TemporaryDirectory() as ws_tmp:
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            entries = cr.resolve_scope_entries(project, ["tracked.txt"])
            workspace = Path(ws_tmp)
            before = cr.build_isolated_workspace(workspace, project, entries)
            (workspace / "tracked.txt").write_text("approved\n")
            changes = cr.compute_workspace_diff(workspace, before)
            cr.apply_validated_changes(
                project, cr.build_change_set(workspace, changes), entries,
            )
            self.assertEqual((project / "tracked.txt").read_text(), "approved\n")
            self.assertFalse((project / "SYSTEM_INSTRUCTIONS.md").exists())
            self.assertEqual((project / "secret.txt").read_text(), "outside-scope-secret\n")

    def test_missing_and_non_executable_bwrap_and_claude_are_blocked(self):
        missing = Path("/definitely/missing/ai-prof-bwrap")
        with mock.patch.object(cr, "BWRAP_CLI", str(missing)):
            with self.assertRaises(cr.SandboxCliMissingError):
                cr.check_bwrap_available()
        with tempfile.TemporaryDirectory() as tmp:
            nonexec = Path(tmp) / "tool"
            nonexec.write_text("#!/bin/sh\n")
            nonexec.chmod(0o600)
            with mock.patch.object(cr, "BWRAP_CLI", str(nonexec)):
                with self.assertRaises(cr.SandboxNotExecutableError):
                    cr.check_bwrap_available()
            with mock.patch.object(cr.shutil, "which", return_value=None):
                with self.assertRaises(cr.ClaudeCliMissingError):
                    cr.check_claude_available()
            with mock.patch.object(cr.shutil, "which", return_value=str(nonexec)):
                with self.assertRaises(cr.ClaudeNotExecutableError):
                    cr.check_claude_available()

    def test_project_overlapping_runtime_or_credential_mount_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_runtime = Path(tmp) / "runtime"
            fake_runtime.mkdir()
            project = fake_runtime / "project"
            project.mkdir()
            with mock.patch.object(cr, "RUNTIME_READONLY_DIRS", [str(fake_runtime)]):
                with self.assertRaises(cr.SandboxExposureError):
                    cr.validate_project_not_exposed(project, Path(tmp) / "home")

    def test_launch_permission_oserror_and_timeout_are_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            scratch = root / "scratch"
            workspace.mkdir()
            scratch.mkdir()
            config = cr.build_claude_mcp_config(scratch)
            for raised, expected in (
                (PermissionError("denied"), cr.PermissionAccessError),
                (OSError("launch failed"), cr.ProcessLaunchError),
                (subprocess.TimeoutExpired(["bwrap"], 1), cr.InfrastructureTimeoutError),
            ):
                with self.subTest(expected=expected.__name__), \
                     mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                     mock.patch.object(cr.subprocess, "run", side_effect=raised):
                    with self.assertRaises(expected):
                        cr.invoke_claude("bundle", workspace, config)

    def test_temp_directory_auth_permission_git_and_apply_failures_route_blocked(self):
        blocked_types = (
            cr.TempDirectoryError, cr.ClaudeAuthError, cr.ClaudePermissionError,
            cr.GitAccessError, cr.PatchAccessError, cr.InfrastructureTimeoutError,
        )
        for failure in blocked_types:
            with self.subTest(failure=failure.__name__):
                self.assertEqual(cr.classify_failure(failure("x"))[0], "blocked")
        self.assertTrue(cr.is_claude_auth_failure("401 authentication failed"))
        self.assertTrue(cr.is_claude_permission_failure("403 account does not have access"))

    def test_temporary_directory_failure_moves_to_blocked_without_project_changes(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            self.make_task(paths.review / "task.md", **{"Project-Path": str(project)})
            before = snapshot_git_state(project)
            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(cr.tempfile, "TemporaryDirectory", side_effect=OSError("no temp access")):
                result = cr.process_one(paths)
            self.assertEqual(result, 1)
            self.assertTrue((paths.blocked / "task.md").exists())
            self.assertEqual(snapshot_git_state(project), before)
            log_text = list(paths.logs.glob("*.log"))[-1].read_text()
            self.assertIn("BLOCKED_TEMP_DIRECTORY", log_text)

    def test_atomic_apply_failure_moves_to_blocked_and_restores_all_files(self):
        with tempfile.TemporaryDirectory() as cc_tmp, tempfile.TemporaryDirectory() as proj_tmp:
            cc_root = Path(cc_tmp)
            project = Path(proj_tmp) / "project"
            init_git_project(project)
            (project / "second.txt").write_text("second-base\n")
            run_git(project, "add", "second.txt")
            run_git(project, "commit", "-m", "add second")
            self.make_context(cc_root)
            paths = cr.build_claude_paths(cc_root)
            self.make_task(
                paths.review / "task.md",
                **{"Project-Path": str(project), "Scope-Files": "tracked.txt, second.txt"},
            )

            def fake_invoke(_bundle, workspace, _config):
                (workspace / "tracked.txt").write_text("first-new\n")
                (workspace / "second.txt").write_text("second-new\n")
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            real_write = cr._write_atomic
            calls = 0

            def fail_second(directory, filename, content):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise cr.PatchAccessError("simulated apply failure")
                return real_write(directory, filename, content)

            with mock.patch.object(cr.shutil, "which", side_effect=self.fake_which), \
                 mock.patch.object(cr, "invoke_claude", side_effect=fake_invoke), \
                 mock.patch.object(cr, "_write_atomic", side_effect=fail_second):
                result = cr.process_one(paths)
            self.assertEqual(result, 1)
            self.assertTrue((paths.blocked / "task.md").exists())
            self.assertEqual((project / "tracked.txt").read_text(), "base\n")
            self.assertEqual((project / "second.txt").read_text(), "second-base\n")
            log_text = list(paths.logs.glob("*.log"))[-1].read_text()
            self.assertIn("BLOCKED_PATCH_ACCESS", log_text)

    def test_blocked_failed_and_pending_codex_remain_distinct(self):
        self.assertEqual(cr.classify_failure(cr.AccessFailure("x"))[0], "blocked")
        self.assertEqual(cr.classify_failure(cr.ClaudeExecutionError("x"))[0], "failed")
        with tempfile.TemporaryDirectory() as tmp:
            paths = cr.build_claude_paths(Path(tmp))
            self.assertNotEqual(paths.blocked, paths.failed)
            self.assertNotEqual(paths.blocked, paths.pending_codex)
            self.assertNotEqual(paths.failed, paths.pending_codex)

    def test_real_bwrap_allows_workspace_execution_and_blocks_host_reads_and_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            scratch = root / "scratch"
            workspace.mkdir()
            scratch.mkdir()
            host_secret = root / "host-secret"
            host_secret.write_text("secret")
            outside = root / "outside-write"
            script = (
                "from pathlib import Path\n"
                "Path('allowed.txt').write_text('ok')\n"
                f"assert not Path({str(host_secret)!r}).exists()\n"
                f"assert not Path({str(outside)!r}).parent.exists()\n"
                f"Path({str(outside)!r}).write_text('bad')\n"
            )
            result = self.run_fake_in_bwrap(script, workspace, scratch)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((workspace / "allowed.txt").read_text(), "ok")
            self.assertFalse(outside.exists())

    def test_real_bwrap_allowed_execution_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            scratch = root / "scratch"
            workspace.mkdir()
            scratch.mkdir()
            result = self.run_fake_in_bwrap(
                "from pathlib import Path\nPath('result.txt').write_text('success')\n",
                workspace, scratch,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((workspace / "result.txt").read_text(), "success")

    def test_real_bwrap_cannot_access_another_project_or_redirect_workspace_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            scratch = root / "scratch"
            other = root / "other-project"
            workspace.mkdir()
            scratch.mkdir()
            other.mkdir()
            (other / "secret").write_text("other-secret")
            (workspace / "redirect").symlink_to(other, target_is_directory=True)
            script = (
                "from pathlib import Path\n"
                f"assert not Path({str(other)!r}).exists()\n"
                "Path('redirect/escaped').write_text('bad')\n"
            )
            result = self.run_fake_in_bwrap(script, workspace, scratch)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((other / "escaped").exists())

    def test_classify_failure_uses_exception_classes(self):
        self.assertEqual(cr.classify_failure(cr.ClaudeCliMissingError("x")), ("blocked", "BLOCKED_CLI_MISSING"))
        self.assertEqual(cr.classify_failure(cr.ClaudeAuthError("x")), ("blocked", "BLOCKED_CLAUDE_AUTH"))
        self.assertEqual(cr.classify_failure(cr.ProjectAccessError("x")), ("blocked", "BLOCKED_PROJECT_ACCESS"))
        self.assertEqual(cr.classify_failure(cr.GitAccessError("x")), ("blocked", "BLOCKED_GIT_ACCESS"))
        self.assertEqual(cr.classify_failure(cr.BranchAccessError("x")), ("blocked", "BLOCKED_BRANCH_ACCESS"))
        self.assertEqual(cr.classify_failure(cr.PatchAccessError("x")), ("blocked", "BLOCKED_PATCH_ACCESS"))
        self.assertEqual(cr.classify_failure(cr.ScopeAccessError("x")), ("blocked", "BLOCKED_SCOPE_ACCESS"))
        self.assertEqual(cr.classify_failure(cr.ClaudePolicyError("x")), ("blocked", "BLOCKED_CLAUDE_POLICY"))
        # A generic, unclassified exception (not an AccessFailure) must not
        # be silently treated as an access problem: it stays failed.
        self.assertEqual(cr.classify_failure(ValueError("generated code is broken")), ("failed", "CLAUDE_FAILED"))
        self.assertEqual(cr.classify_failure(cr.ClaudeExecutionError("x")), ("failed", "CLAUDE_FAILED"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
