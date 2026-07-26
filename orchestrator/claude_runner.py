#!/usr/bin/env python3
"""AI PROF Orchestrator Stage 01B: restricted Claude execution runner.

Processes only tasks already validated by Stage 01A (queue/review). Reuses
Stage 01A's atomic no-replace queue movement, lock file, redaction and
context-escape checks by importing orchestrator.py directly. This module
never overloads or modifies Stage 01A behavior.

Claude is never launched against the real target project. It runs inside an
ephemeral, isolated workspace that contains only the task text, the five
fixed context files (via stdin) and an explicit, pre-validated allowlist of
project paths ("Scope-Files"). The real target project is only touched after
Claude succeeds and the isolated workspace diff has been proven to stay
inside the approved scope.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


_ORCHESTRATOR_PATH = Path(__file__).resolve().parent / "orchestrator.py"
_SPEC = importlib.util.spec_from_file_location("ai_prof_orchestrator_core", _ORCHESTRATOR_PATH)
orch = importlib.util.module_from_spec(_SPEC)
if _SPEC.loader is None:
    raise RuntimeError("Cannot load orchestrator core module")
sys.modules[_SPEC.name] = orch
_SPEC.loader.exec_module(orch)


CLAUDE_CONTEXT_FILES = [
    "SYSTEM_INSTRUCTIONS.md",
    "SOURCE_POLICY.md",
    "STATE.md",
    "APPROVAL_MATRIX.md",
    "DECISIONS.md",
]

CLAUDE_CLI = "claude"
CLAUDE_TIMEOUT_SECONDS = 1800
CHECK_TIMEOUT_SECONDS = 900


# ---------------------------------------------------------------------------
# Exception classification (Blocker 5)
#
# AccessFailure and its subclasses are infrastructure/access problems: the
# task moves to queue/blocked. Every other exception is a genuine execution,
# generated-code, or required-check failure: the task moves to queue/failed.
# ---------------------------------------------------------------------------


class AccessFailure(RuntimeError):
    """Base class for infrastructure/access failures. Always routes to blocked."""

    status_code = "BLOCKED_MISSING_ACCESS"


class ClaudeCliMissingError(AccessFailure):
    status_code = "BLOCKED_CLI_MISSING"


class ClaudeAuthError(AccessFailure):
    status_code = "BLOCKED_CLAUDE_AUTH"


class ProjectAccessError(AccessFailure):
    status_code = "BLOCKED_PROJECT_ACCESS"


class PermissionAccessError(AccessFailure):
    status_code = "BLOCKED_PERMISSION_DENIED"


class ContextAccessError(AccessFailure):
    status_code = "BLOCKED_CONTEXT_ACCESS"


class EnvironmentAccessError(AccessFailure):
    status_code = "BLOCKED_ENVIRONMENT_ACCESS"


class GitAccessError(AccessFailure):
    status_code = "BLOCKED_GIT_ACCESS"


class BranchAccessError(AccessFailure):
    status_code = "BLOCKED_BRANCH_ACCESS"


class PatchAccessError(AccessFailure):
    status_code = "BLOCKED_PATCH_ACCESS"


class ScopeAccessError(AccessFailure):
    status_code = "BLOCKED_SCOPE_ACCESS"


class DirtyProjectError(AccessFailure):
    status_code = "BLOCKED_DIRTY_PROJECT"


class InvalidBranchNameError(AccessFailure):
    status_code = "BLOCKED_INVALID_BRANCH"


class ClaudePolicyError(AccessFailure):
    """Raised when the Claude tool/security policy cannot be proven safe."""

    status_code = "BLOCKED_CLAUDE_POLICY"


class ClaudeExecutionError(RuntimeError):
    """Genuine Claude execution failure. Always routes to failed."""

    status_code = "CLAUDE_FAILED"


def classify_failure(exc: Exception) -> tuple[str, str]:
    """Map an exception to (queue_name, status_code).

    Structural classification by exception class, not by string sniffing:
    any AccessFailure subclass is an infrastructure/access problem and moves
    to blocked; everything else is a genuine failure and moves to failed.
    """
    if isinstance(exc, AccessFailure):
        return "blocked", exc.status_code
    return "failed", ClaudeExecutionError.status_code


# ---------------------------------------------------------------------------
# Local post-Claude command allowlist (Blocker 2)
#
# This allowlist is a *separate*, purely local trust boundary used only to
# run Required-Checks (npm test, tsc, etc.) against the real project after
# Claude has already finished and its changes have been validated. It is
# NEVER the Claude security boundary: Claude itself is never granted Bash or
# any shell/exec capability (see build_claude_argv / validate_claude_argv
# below), so nothing in this allowlist is reachable from Claude's own tools.
# Stage 01B never executes shell text taken from the task file itself;
# Required-Checks may only *describe* checks in prose, and execution is
# limited to these exact fixed argv lists.
# ---------------------------------------------------------------------------

ALLOWED_COMMANDS: dict[str, list[str]] = {
    "npm ci": ["npm", "ci"],
    "npm run lint": ["npm", "run", "lint"],
    "npm run build": ["npm", "run", "build"],
    "npm test": ["npm", "test"],
    "npx tsc --noEmit": ["npx", "tsc", "--noEmit"],
    "python3 -m pytest": ["python3", "-m", "pytest"],
    "python3 -m unittest": ["python3", "-m", "unittest"],
}


@dataclass
class ClaudePaths:
    root: Path
    review: Path
    active: Path
    blocked: Path
    failed: Path
    pending_codex: Path
    logs: Path
    lock: Path


def build_claude_paths(root: Path) -> ClaudePaths:
    paths = ClaudePaths(
        root=root,
        review=root / "queue/review",
        active=root / "queue/active",
        blocked=root / "queue/blocked",
        failed=root / "queue/failed",
        pending_codex=root / "queue/pending_codex",
        logs=root / "logs/orchestrator",
        lock=root / "orchestrator/orchestrator.lock",
    )
    for directory in [
        paths.review, paths.active, paths.blocked,
        paths.failed, paths.pending_codex, paths.logs,
    ]:
        directory.mkdir(parents=True, exist_ok=True)
    return paths


def load_claude_context(root: Path, relative: str) -> dict[str, str]:
    """Load the fixed 5-file AK BERMET context bundle for Claude.

    Reuses the same containment rule as Stage 01A: the context directory
    must resolve inside the Control Center root.
    """
    context = (root / relative).resolve()
    if root not in context.parents:
        raise ContextAccessError("BLOCKED_CONTEXT_ACCESS: agent context must remain inside Control Center")
    loaded: dict[str, str] = {}
    for name in CLAUDE_CONTEXT_FILES:
        path = context / name
        try:
            if not path.is_file() or not path.stat().st_size:
                raise ContextAccessError(f"BLOCKED_CONTEXT_ACCESS: required context file missing or empty: {path}")
            loaded[name] = path.read_text(encoding="utf-8")
        except PermissionError as exc:
            raise PermissionAccessError(f"BLOCKED_PERMISSION_DENIED: cannot read context file {path}: {exc}") from exc
        except OSError as exc:
            raise ContextAccessError(f"BLOCKED_CONTEXT_ACCESS: cannot read context file {path}: {exc}") from exc
    return loaded


def resolve_allowed_checks(required_checks: str) -> list[list[str]]:
    """Match Required-Checks prose against the local allowlist only.

    The task file's Required-Checks text is never executed directly. Only
    exact allowlisted command keys that appear in that text are resolved to
    their fixed argv, in stable allowlist order.
    """
    lowered = required_checks.lower()
    return [argv for key, argv in ALLOWED_COMMANDS.items() if key.lower() in lowered]


def run_allowed_checks(argvs: list[list[str]], project: Path) -> list[str]:
    executed: list[str] = []
    for argv in argvs:
        result = subprocess.run(
            argv,
            cwd=str(project),
            text=True,
            capture_output=True,
            timeout=CHECK_TIMEOUT_SECONDS,
        )
        executed.append(" ".join(argv))
        if result.returncode != 0:
            raise ClaudeExecutionError(f"CLAUDE_FAILED: check failed: {' '.join(argv)}")
    return executed


# ---------------------------------------------------------------------------
# Target project access helpers (real project only; never mutating)
# ---------------------------------------------------------------------------


def check_project_accessible(project: Path) -> None:
    try:
        mode = project.stat().st_mode
    except FileNotFoundError as exc:
        raise ProjectAccessError(f"BLOCKED_PROJECT_ACCESS: project not found: {project}") from exc
    except PermissionError as exc:
        raise PermissionAccessError(f"BLOCKED_PERMISSION_DENIED: {exc}") from exc
    except OSError as exc:
        raise ProjectAccessError(f"BLOCKED_PROJECT_ACCESS: {exc}") from exc
    if not stat.S_ISDIR(mode):
        raise ProjectAccessError(f"BLOCKED_PROJECT_ACCESS: not a directory: {project}")
    try:
        git_present = (project / ".git").is_dir()
    except PermissionError as exc:
        raise PermissionAccessError(f"BLOCKED_PERMISSION_DENIED: {exc}") from exc
    if not git_present:
        raise ProjectAccessError(f"BLOCKED_PROJECT_ACCESS: project not found: {project}")


def current_branch(project: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(project), text=True, capture_output=True, check=True,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        raise GitAccessError(f"BLOCKED_GIT_ACCESS: unable to read current branch: {exc}") from exc
    return result.stdout.strip()


def branch_exists(project: Path, branch: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=str(project), text=True, capture_output=True,
        )
    except OSError as exc:
        raise GitAccessError(f"BLOCKED_GIT_ACCESS: unable to query branches: {exc}") from exc
    return result.returncode == 0


def ensure_work_branch(project: Path, base_branch: str, work_branch: str) -> None:
    """Create or switch to work_branch, checking out base_branch first.

    Never invoked with unvalidated branch names: callers must have already
    confirmed work_branch matches orch.is_valid_work_branch and base_branch
    is in {main, develop}. Only ref switches happen here, no file writes.

    Must only be called AFTER Claude has succeeded and the isolated
    workspace diff has been validated (Blocker 4): the real target project
    stays completely untouched before and during Claude's execution.
    """
    try:
        if current_branch(project) != base_branch:
            subprocess.run(
                ["git", "checkout", base_branch],
                cwd=str(project), text=True, capture_output=True, check=True,
            )
        if branch_exists(project, work_branch):
            subprocess.run(
                ["git", "checkout", work_branch],
                cwd=str(project), text=True, capture_output=True, check=True,
            )
        else:
            subprocess.run(
                ["git", "checkout", "-b", work_branch],
                cwd=str(project), text=True, capture_output=True, check=True,
            )
    except (subprocess.CalledProcessError, OSError) as exc:
        raise BranchAccessError(f"BLOCKED_BRANCH_ACCESS: unable to switch branch: {exc}") from exc


# ---------------------------------------------------------------------------
# Claude capability policy (Blocker 1 + Blocker 2)
#
# The subprocess argv is built and validated by dedicated functions before
# subprocess.run is ever called. Validation fails closed: if the policy
# cannot be proven safe, invoke_claude raises ClaudePolicyError and the
# subprocess is never spawned.
# ---------------------------------------------------------------------------

# The only tools Claude may use at all: local file read/edit/search inside
# the isolated workspace. No Bash, no network, no browser, no MCP tools, no
# subagents, no deployment tools, no Git push/merge, no database access.
CLAUDE_SAFE_TOOLS = ("Read", "Edit", "Write", "Glob", "Grep")
CLAUDE_ALLOWED_TOOLS = CLAUDE_SAFE_TOOLS

# Explicit deny list as defence in depth, enforced even though these tools
# are already absent from CLAUDE_SAFE_TOOLS / CLAUDE_ALLOWED_TOOLS.
CLAUDE_DISALLOWED_TOOLS = (
    "Bash", "BashOutput", "KillShell",
    "WebFetch", "WebSearch",
    "Task", "NotebookEdit", "SlashCommand",
    "mcp__*",
)

_DANGEROUS_TOOL_MARKERS = (
    "bash", "shell", "exec", "terminal", "web", "fetch", "search",
    "browser", "chrome", "mcp", "task", "agent", "subagent",
    "deploy", "push", "merge", "sql", "database", "kill",
)

CLAUDE_DANGEROUS_FLAGS = (
    "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions",
)

CLAUDE_DANGEROUS_PERMISSION_MODES = ("bypassPermissions",)

EMPTY_MCP_CONFIG = {"mcpServers": {}}


def _flag_value(argv: list[str], flag: str) -> str | None:
    for index, item in enumerate(argv):
        if item == flag and index + 1 < len(argv):
            return argv[index + 1]
    return None


def build_claude_mcp_config(scratch_dir: Path) -> Path:
    """Write an explicitly empty MCP configuration file for --strict-mcp-config."""
    config_path = scratch_dir / "mcp-empty.json"
    config_path.write_text(json.dumps(EMPTY_MCP_CONFIG), encoding="utf-8")
    return config_path


def build_claude_argv(mcp_config_path: Path) -> list[str]:
    """Construct the exact, restricted Claude CLI argv.

    Never uses --bare (the installed CLI may authenticate via OAuth/keychain
    and --bare would break that). Never emits any dangerous bypass flag.
    """
    return [
        CLAUDE_CLI,
        "-p",
        "--output-format", "json",
        "--safe-mode",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--no-chrome",
        "--strict-mcp-config",
        "--mcp-config", str(mcp_config_path),
        "--permission-mode", "dontAsk",
        "--tools", ",".join(CLAUDE_SAFE_TOOLS),
        "--allowed-tools", ",".join(CLAUDE_ALLOWED_TOOLS),
        "--disallowed-tools", ",".join(CLAUDE_DISALLOWED_TOOLS),
    ]


def validate_claude_argv(argv: list[str], mcp_config_path: Path) -> None:
    """Fail closed unless argv is provably the restricted Claude policy.

    Rejects missing, broad, default, bypass, dangerous, or unsupported
    configurations. Raises ClaudePolicyError on any doubt.
    """
    joined = " ".join(argv)
    for dangerous in CLAUDE_DANGEROUS_FLAGS:
        if dangerous in argv or dangerous in joined:
            raise ClaudePolicyError(f"BLOCKED_CLAUDE_POLICY: dangerous flag present: {dangerous}")

    if not argv or argv[0] != CLAUDE_CLI:
        raise ClaudePolicyError("BLOCKED_CLAUDE_POLICY: unexpected executable")

    for flag in (
        "--safe-mode", "--no-session-persistence", "--disable-slash-commands",
        "--no-chrome", "--strict-mcp-config",
    ):
        if flag not in argv:
            raise ClaudePolicyError(f"BLOCKED_CLAUDE_POLICY: missing required flag: {flag}")

    permission_mode = _flag_value(argv, "--permission-mode")
    if permission_mode != "dontAsk":
        raise ClaudePolicyError(f"BLOCKED_CLAUDE_POLICY: unsupported permission mode: {permission_mode!r}")
    for mode in CLAUDE_DANGEROUS_PERMISSION_MODES:
        if mode in argv:
            raise ClaudePolicyError(f"BLOCKED_CLAUDE_POLICY: dangerous permission mode present: {mode}")

    mcp_flag_value = _flag_value(argv, "--mcp-config")
    if not mcp_flag_value:
        raise ClaudePolicyError("BLOCKED_CLAUDE_POLICY: missing --mcp-config")
    config_path = Path(mcp_flag_value)
    if config_path != mcp_config_path:
        raise ClaudePolicyError("BLOCKED_CLAUDE_POLICY: --mcp-config path mismatch")
    try:
        config_content = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ClaudePolicyError(f"BLOCKED_CLAUDE_POLICY: unreadable MCP config: {exc}") from exc
    if config_content != EMPTY_MCP_CONFIG:
        raise ClaudePolicyError("BLOCKED_CLAUDE_POLICY: MCP config is not explicitly empty")

    tools_value = _flag_value(argv, "--tools")
    allowed_value = _flag_value(argv, "--allowed-tools")
    disallowed_value = _flag_value(argv, "--disallowed-tools")
    if not tools_value or not allowed_value or not disallowed_value:
        raise ClaudePolicyError("BLOCKED_CLAUDE_POLICY: missing explicit tool policy")

    tools = [t for t in tools_value.split(",") if t]
    allowed = [t for t in allowed_value.split(",") if t]
    disallowed = {t for t in disallowed_value.split(",") if t}

    if not tools or not allowed:
        raise ClaudePolicyError("BLOCKED_CLAUDE_POLICY: empty tool policy")

    safe_registry = set(CLAUDE_SAFE_TOOLS)
    for name in (*tools, *allowed):
        if name not in safe_registry:
            raise ClaudePolicyError(f"BLOCKED_CLAUDE_POLICY: tool not in safe registry: {name}")
        if any(marker in name.lower() for marker in _DANGEROUS_TOOL_MARKERS):
            raise ClaudePolicyError(f"BLOCKED_CLAUDE_POLICY: dangerous tool granted: {name}")

    if set(allowed) & disallowed:
        raise ClaudePolicyError("BLOCKED_CLAUDE_POLICY: overlap between allowed and disallowed tools")

    missing_denies = set(CLAUDE_DISALLOWED_TOOLS) - disallowed
    if missing_denies:
        raise ClaudePolicyError(
            f"BLOCKED_CLAUDE_POLICY: disallowed-tools missing required denies: {sorted(missing_denies)}"
        )


def invoke_claude(bundle: str, workspace: Path, mcp_config_path: Path) -> subprocess.CompletedProcess:
    """Run Claude inside the isolated workspace with a validated, restricted argv.

    The argv is built and validated before subprocess.run is ever called; if
    validation fails, subprocess.run is never invoked (fail closed).
    """
    if shutil.which(CLAUDE_CLI) is None:
        raise ClaudeCliMissingError("BLOCKED_CLI_MISSING: claude CLI not found on PATH")

    argv = build_claude_argv(mcp_config_path)
    validate_claude_argv(argv, mcp_config_path)

    try:
        return subprocess.run(
            argv,
            cwd=str(workspace),
            input=bundle,
            text=True,
            capture_output=True,
            timeout=CLAUDE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeExecutionError(f"CLAUDE_FAILED: claude timed out: {exc}") from exc
    except FileNotFoundError as exc:
        raise ClaudeCliMissingError(f"BLOCKED_CLI_MISSING: {exc}") from exc
    except PermissionError as exc:
        raise PermissionAccessError(f"BLOCKED_PERMISSION_DENIED: {exc}") from exc
    except OSError as exc:
        raise ClaudeCliMissingError(f"BLOCKED_CLI_MISSING: claude invocation error: {exc}") from exc


_AUTH_FAILURE_MARKERS = (
    "not authenticated", "not logged in", "please log in", "claude login",
    "invalid api key", "unauthorized", "authentication failed", "oauth",
    "credential", "401",
)


def is_claude_auth_failure(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _AUTH_FAILURE_MARKERS)


def build_claude_bundle(task_text: str, context: dict[str, str]) -> str:
    """Assemble exactly the restricted content Claude may receive."""
    parts = ["# TASK", task_text.strip(), ""]
    for name in CLAUDE_CONTEXT_FILES:
        parts.append(f"# {name}")
        parts.append(context[name].strip())
        parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Isolated workspace and approved-scope enforcement (Blocker 3)
# ---------------------------------------------------------------------------

SCOPE_FILES_PATTERN = re.compile(r"(?mi)^\s*Scope-Files:\s*(.+?)\s*$")


def parse_scope_files(task_text: str) -> list[str]:
    """Extract the task's declared, explicit project-file scope, if any.

    This is Stage 01B's own declared file/scope restriction field. Stage
    01A's task schema has no structured file-scope field (only free-text
    Scope/Out-of-Scope prose), so Stage 01B requires a dedicated
    Scope-Files: line of comma-separated project-relative paths. A task
    without it has no safe explicit scope and must be blocked rather than
    exposing the whole project.
    """
    match = SCOPE_FILES_PATTERN.search(task_text)
    if not match:
        return []
    return orch.split_csv(match.group(1))


@dataclass
class ScopeEntry:
    relative: str
    absolute: Path
    is_dir: bool


def resolve_scope_entries(project: Path, entries: list[str]) -> list[ScopeEntry]:
    """Validate declared scope entries with resolved-path containment checks.

    Rejects missing scope, path traversal, absolute paths, and any entry
    whose resolved (symlink-following) location escapes the project.
    """
    if not entries:
        raise ScopeAccessError(
            "BLOCKED_SCOPE_ACCESS: task does not declare an explicit Scope-Files allowlist"
        )
    project = project.resolve()
    resolved: list[ScopeEntry] = []
    for raw in entries:
        rel = raw.strip().strip("/")
        parts = PurePosixPath(rel).parts if rel else ()
        if not rel or ".." in parts or PurePosixPath(rel).is_absolute():
            raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: unsafe scope entry: {raw}")
        try:
            candidate = (project / rel).resolve()
        except OSError as exc:
            raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: unable to resolve scope entry {raw}: {exc}") from exc
        if candidate != project and project not in candidate.parents:
            raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: scope entry escapes project: {raw}")
        if not candidate.exists():
            raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: scope entry does not exist: {raw}")
        resolved.append(ScopeEntry(relative=rel, absolute=candidate, is_dir=candidate.is_dir()))
    return resolved


def _copy_scope_safe(src: Path, dst: Path, project: Path) -> None:
    if src.is_symlink():
        target = src.resolve()
        if target != project and project not in target.parents:
            raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: symlink escapes approved project: {src}")
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for child in sorted(src.iterdir()):
            _copy_scope_safe(child, dst / child.name, project)
    elif src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def snapshot_workspace(workspace_dir: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(workspace_dir.rglob("*")):
        if path.is_file():
            snapshot[str(path.relative_to(workspace_dir))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def build_isolated_workspace(workspace_dir: Path, project: Path, entries: list[ScopeEntry]) -> dict[str, str]:
    """Copy only approved scope paths into workspace_dir, validating containment.

    The Control Center root and the real target project are never exposed:
    workspace_dir contains nothing but these explicitly approved paths.
    """
    workspace_root = workspace_dir.resolve()
    for entry in entries:
        destination = (workspace_dir / entry.relative).resolve()
        if destination != workspace_root and workspace_root not in destination.parents:
            raise ScopeAccessError("BLOCKED_SCOPE_ACCESS: workspace containment violated")
        _copy_scope_safe(entry.absolute, workspace_dir / entry.relative, project)
    return snapshot_workspace(workspace_dir)


def compute_workspace_diff(workspace_dir: Path, before: dict[str, str]) -> dict[str, str]:
    after = snapshot_workspace(workspace_dir)
    changes: dict[str, str] = {}
    for relative, digest in after.items():
        if relative not in before:
            changes[relative] = "added"
        elif before[relative] != digest:
            changes[relative] = "modified"
    for relative in before:
        if relative not in after:
            changes[relative] = "deleted"
    return changes


def _is_within_scope(relative_path: str, entries: list[ScopeEntry]) -> bool:
    candidate = PurePosixPath(relative_path)
    for entry in entries:
        entry_path = PurePosixPath(entry.relative)
        if entry.is_dir:
            if candidate == entry_path or entry_path in candidate.parents:
                return True
        elif candidate == entry_path:
            return True
    return False


def validate_workspace_changes(changes: dict[str, str], entries: list[ScopeEntry]) -> None:
    for relative in changes:
        if not _is_within_scope(relative, entries):
            raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: change outside approved scope: {relative}")


def build_change_set(workspace_dir: Path, changes: dict[str, str]) -> list[tuple[str, str, bytes | None]]:
    """Deterministic, validated change set: sorted (relative_path, action, content)."""
    change_set: list[tuple[str, str, bytes | None]] = []
    for relative in sorted(changes):
        action = changes[relative]
        if action == "deleted":
            change_set.append((relative, action, None))
        else:
            content = (workspace_dir / relative).read_bytes()
            change_set.append((relative, action, content))
    return change_set


def apply_validated_changes(project: Path, change_set: list[tuple[str, str, bytes | None]]) -> list[str]:
    """Apply an already-validated change set to the real project.

    Only called after Claude succeeded, the workspace diff was validated
    against the approved scope, and the work branch has been prepared.
    """
    project = project.resolve()
    applied: list[str] = []
    for relative, action, content in change_set:
        target = (project / relative).resolve()
        if target != project and project not in target.parents:
            raise PatchAccessError(f"BLOCKED_PATCH_ACCESS: change escapes project: {relative}")
        try:
            if action == "deleted":
                if target.exists():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        except OSError as exc:
            raise PatchAccessError(f"BLOCKED_PATCH_ACCESS: unable to apply change {relative}: {exc}") from exc
        applied.append(f"{action}:{relative}")
    return applied


def run_self_test(root: Path) -> int:
    context = load_claude_context(root, "agents/ak-bermet")
    if set(context) != set(CLAUDE_CONTEXT_FILES) or not all(context.values()):
        raise RuntimeError("SELF_TEST_CONTEXT_LOAD_FAILED")
    if orch.redact("TOKEN=abc") != "[REDACTED]":
        raise RuntimeError("SELF_TEST_REDACTION_FAILED")
    if resolve_allowed_checks("Run npm test and npm run lint") != [
        ALLOWED_COMMANDS["npm run lint"],
        ALLOWED_COMMANDS["npm test"],
    ]:
        raise RuntimeError("SELF_TEST_ALLOWLIST_FAILED")
    if resolve_allowed_checks("rm -rf / && curl evil.example"):
        raise RuntimeError("SELF_TEST_ALLOWLIST_LEAK")
    argv = build_claude_argv(Path("/tmp/nonexistent-self-test-mcp.json"))
    for dangerous in CLAUDE_DANGEROUS_FLAGS:
        if dangerous in argv:
            raise RuntimeError("SELF_TEST_CLAUDE_POLICY_LEAK")
    print("AI PROF Claude runner self-test: PASS")
    return 0


def process_one(paths: ClaudePaths) -> int:
    tasks = sorted(paths.review.glob("*.md"))
    if not tasks:
        print("QUEUE_EMPTY")
        return 0

    task = tasks[0]
    try:
        active_task = orch.safe_move(task, paths.active)
    except orch.AtomicMoveUnavailable:
        print("BLOCKED_ATOMIC_NOREPLACE_UNAVAILABLE", file=sys.stderr)
        return 3

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = paths.logs / f"{active_task.stem}-01B-{timestamp}.log"

    try:
        data, task_text = orch.parse_task(active_task)
        try:
            project = Path(data["Project-Path"]).expanduser().resolve()
        except OSError as exc:
            raise ProjectAccessError(f"BLOCKED_PROJECT_ACCESS: unable to resolve project path: {exc}") from exc

        check_project_accessible(project)

        try:
            orch.validate_access(data)
        except RuntimeError as exc:
            raise EnvironmentAccessError(f"BLOCKED_ENVIRONMENT_ACCESS: {exc}") from exc

        try:
            clean = orch.git_clean(project)
        except (subprocess.CalledProcessError, OSError) as exc:
            raise GitAccessError(f"BLOCKED_GIT_ACCESS: unable to read project status: {exc}") from exc
        if not clean:
            raise DirtyProjectError(f"BLOCKED_DIRTY_PROJECT: {project}")

        if not orch.is_valid_work_branch(data["Work-Branch"]):
            raise InvalidBranchNameError("BLOCKED_INVALID_BRANCH: invalid work branch")
        if data["Base-Branch"] not in {"main", "develop"}:
            raise InvalidBranchNameError("BLOCKED_INVALID_BRANCH: invalid base branch")

        context = load_claude_context(paths.root, data["Agent-Context"])

        if shutil.which(CLAUDE_CLI) is None:
            raise ClaudeCliMissingError("BLOCKED_CLI_MISSING: claude CLI not found on PATH")

        scope_entries = resolve_scope_entries(project, parse_scope_files(task_text))

        # From here on the real project is READ-ONLY until Claude succeeds and
        # the isolated workspace diff has been validated (Blocker 4).
        with tempfile.TemporaryDirectory(prefix="ai-prof-claude-") as isolation_root_str:
            isolation_root = Path(isolation_root_str)
            workspace_dir = isolation_root / "workspace"
            scratch_dir = isolation_root / "scratch"
            workspace_dir.mkdir()
            scratch_dir.mkdir()

            before_snapshot = build_isolated_workspace(workspace_dir, project, scope_entries)
            mcp_config_path = build_claude_mcp_config(scratch_dir)

            bundle = build_claude_bundle(task_text, context)
            result = invoke_claude(bundle, workspace_dir, mcp_config_path)
            if result.returncode != 0:
                detail = f"{result.stdout or ''}\n{result.stderr or ''}".strip()[:500]
                if is_claude_auth_failure(detail):
                    raise ClaudeAuthError(f"BLOCKED_CLAUDE_AUTH: claude authentication failed: {detail}")
                raise ClaudeExecutionError(f"CLAUDE_FAILED: claude exited with {result.returncode}: {detail}")

            changes = compute_workspace_diff(workspace_dir, before_snapshot)
            validate_workspace_changes(changes, scope_entries)
            change_set = build_change_set(workspace_dir, changes)

        # Only now, after success + validated scope, may the target branch be
        # prepared and the validated change set applied.
        ensure_work_branch(project, data["Base-Branch"], data["Work-Branch"])
        applied_changes = apply_validated_changes(project, change_set)

        executed_checks = run_allowed_checks(
            resolve_allowed_checks(data["Required-Checks"]), project,
        )

        summary = "\n".join([
            "STAGE_01B_CLAUDE_PASS",
            f"task_id={data['Task-ID']}",
            f"project={project}",
            f"base_branch={data['Base-Branch']}",
            f"work_branch={data['Work-Branch']}",
            f"context_files={','.join(context.keys())}",
            f"scope_files={','.join(entry.relative for entry in scope_entries)}",
            f"applied_changes={','.join(applied_changes) if applied_changes else 'none'}",
            f"checks_executed={','.join(executed_checks) if executed_checks else 'none'}",
            "codex_launched=false",
            "merge_capability=false",
            "push_capability=false",
            "production_deploy_capability=false",
            "destructive_sql_capability=false",
        ])
        log_path.write_text(orch.redact(summary) + "\n", encoding="utf-8")
        orch.safe_move(active_task, paths.pending_codex)
        print("STAGE_01B_CLAUDE_PASS")
        return 0

    except orch.AtomicMoveUnavailable:
        print("BLOCKED_ATOMIC_NOREPLACE_UNAVAILABLE", file=sys.stderr)
        return 3
    except Exception as exc:
        queue_name, status_code = classify_failure(exc)
        destination = paths.blocked if queue_name == "blocked" else paths.failed
        try:
            if active_task.exists():
                orch.safe_move(active_task, destination)
        except orch.AtomicMoveUnavailable:
            print("BLOCKED_ATOMIC_NOREPLACE_UNAVAILABLE", file=sys.stderr)
            return 3
        log_path.write_text(
            orch.redact(f"{status_code}\n{type(exc).__name__}: {exc}\n"), encoding="utf-8",
        )
        print(status_code, file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/home/agent/projects/ai-prof-control-center")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    paths = build_claude_paths(root)

    try:
        lock_handle = orch.acquire_lock(paths.lock)
    except BlockingIOError:
        print("ORCHESTRATOR_ALREADY_RUNNING", file=sys.stderr)
        return 2

    with lock_handle:
        if args.self_test:
            return run_self_test(root)
        return process_one(paths)


if __name__ == "__main__":
    raise SystemExit(main())
