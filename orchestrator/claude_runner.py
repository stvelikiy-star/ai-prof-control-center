#!/usr/bin/env python3
"""AI PROF Orchestrator Stage 01B: restricted Claude execution runner.

Processes only tasks already validated by Stage 01A (queue/review). Reuses
Stage 01A's atomic no-replace queue movement, lock file, redaction and
context-escape checks by importing orchestrator.py directly. This module
never overloads or modifies Stage 01A behavior.

Claude is never launched against the real target project and never launched
as a bare host process. It runs inside a real Bubblewrap (/usr/bin/bwrap)
sandbox that namespace-isolates the process from the host (pid, ipc, uts,
cgroup, mount, user), keeps only the network access needed for the Claude
API, and exposes nothing but: the minimum runtime directories required to
execute the Claude binary, a private tmpfs, and an ephemeral, isolated
workspace that contains only the task's approved files. The real target
project is only touched after Claude succeeds and the isolated workspace
diff has been proven to stay inside the approved scope; Claude's own
Read/Edit tool permission prompts are never relied upon as the security
boundary.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
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

# Fixed, absolute production path: never searched via PATH, so a PATH-based
# substitution cannot swap in an attacker-controlled "bwrap".
BWRAP_CLI = "/usr/bin/bwrap"
SANDBOX_WORKSPACE = "/workspace"
SANDBOX_SCRATCH = "/run/ai-prof/scratch"
SANDBOX_CLAUDE = "/run/ai-prof/claude"
SANDBOX_HOME = "/home/claude"

# The PATH visible *inside* the sandbox. Deliberately unrelated to whatever
# PATH this orchestrator process itself was started with.
SANDBOX_PATH_ENV = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# The minimum host directories/files required to execute a dynamically
# linked binary (the Claude CLI and its interpreter/runtime) and to resolve
# DNS/TLS for outbound HTTPS to the Claude API. Never the complete host
# root, never the user's home, never the target project.
RUNTIME_READONLY_DIRS = ["/usr", "/bin", "/sbin", "/lib", "/lib64", "/lib32"]

RUNTIME_READONLY_FILES = [
    "/etc/resolv.conf",
    "/etc/nsswitch.conf",
    "/etc/hosts",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/ca-certificates.conf",
    "/etc/passwd",
]

# Explicit allowlist of environment variables that may carry Claude
# authentication material through into the sandbox. Everything else is
# cleared (--clearenv); values are never logged.
CLAUDE_AUTH_ENV_PASSTHROUGH = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")


# ---------------------------------------------------------------------------
# Exception classification (Blocker 4)
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


class ClaudeNotExecutableError(AccessFailure):
    status_code = "BLOCKED_CLAUDE_NOT_EXECUTABLE"


class ClaudeAuthError(AccessFailure):
    status_code = "BLOCKED_CLAUDE_AUTH"


class ClaudePermissionError(AccessFailure):
    """Claude account/plan/entitlement failure, distinct from authentication."""

    status_code = "BLOCKED_CLAUDE_PERMISSION"


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


class ScopeReadError(AccessFailure):
    """An approved scope file could not be read from the target project."""

    status_code = "BLOCKED_SCOPE_READ"


class WorkspaceWriteError(AccessFailure):
    """The isolated workspace could not be prepared (copy/mkdir failure)."""

    status_code = "BLOCKED_WORKSPACE_WRITE"


class TempDirectoryError(AccessFailure):
    """The ephemeral isolation directory could not be created."""

    status_code = "BLOCKED_TEMP_DIRECTORY"


class DirtyProjectError(AccessFailure):
    status_code = "BLOCKED_DIRTY_PROJECT"


class InvalidBranchNameError(AccessFailure):
    status_code = "BLOCKED_INVALID_BRANCH"


class ConcurrentModificationError(AccessFailure):
    """An approved scope file changed in the target project during execution."""

    status_code = "BLOCKED_CONCURRENT_MODIFICATION"


class ClaudePolicyError(AccessFailure):
    """Raised when the Claude tool/security policy cannot be proven safe."""

    status_code = "BLOCKED_CLAUDE_POLICY"


class SandboxCliMissingError(AccessFailure):
    """Bubblewrap is not present at the fixed production path."""

    status_code = "BLOCKED_SANDBOX_MISSING"


class SandboxNotExecutableError(AccessFailure):
    """Bubblewrap exists but is not executable."""

    status_code = "BLOCKED_SANDBOX_NOT_EXECUTABLE"


class SandboxSetupError(AccessFailure):
    """Bubblewrap could not establish the sandbox (namespaces/binds/etc)."""

    status_code = "BLOCKED_SANDBOX_SETUP"


class SandboxExposureError(AccessFailure):
    """The target project overlaps a path that must be visible in the sandbox."""

    status_code = "BLOCKED_SANDBOX_EXPOSURE"


class ProcessLaunchError(AccessFailure):
    """The sandboxed subprocess could not be launched for an unclassified OS reason."""

    status_code = "BLOCKED_PROCESS_LAUNCH"


class InfrastructureTimeoutError(AccessFailure):
    """A subprocess boundary (sandbox setup or launch) hung past its timeout."""

    status_code = "BLOCKED_INFRA_TIMEOUT"


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
# Local post-Claude command allowlist (Blocker 2, Stage 01B original design)
#
# This allowlist is a *separate*, purely local trust boundary used only to
# run Required-Checks (npm test, tsc, etc.) against the real project after
# Claude has already finished and its changes have been validated. It is
# NEVER the Claude security boundary: Claude itself is never granted Bash or
# any shell/exec capability (see build_claude_argv / validate_claude_argv
# below) and runs fully confined inside Bubblewrap, so nothing in this
# allowlist is reachable from Claude's own tools. Stage 01B never executes
# shell text taken from the task file itself; Required-Checks may only
# *describe* checks in prose, and execution is limited to these exact fixed
# argv lists.
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
    workspace diff has been validated (Blocker 2): the real target project
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
# Claude capability policy (Blocker 1)
#
# The subprocess argv is built and validated by dedicated functions before
# subprocess.run is ever called. Validation fails closed: if the policy
# cannot be proven safe, invoke_claude raises ClaudePolicyError and no
# subprocess (sandboxed or otherwise) is ever spawned.
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
    """Construct the exact, restricted Claude CLI policy argv.

    This is the abstract policy representation validated by
    validate_claude_argv; invoke_claude substitutes argv[0] with the
    resolved absolute Claude binary path before actually launching it
    (see invoke_claude), since the sandboxed process cannot rely on PATH
    lookup finding an arbitrary host "claude" the way an unsandboxed
    process could. Never uses --bare (the installed CLI may authenticate
    via OAuth/keychain and --bare would break that). Never emits any
    dangerous bypass flag.
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


# ---------------------------------------------------------------------------
# Bubblewrap process confinement (Blocker 1)
# ---------------------------------------------------------------------------


def check_bwrap_available() -> Path:
    """Fail closed unless Bubblewrap is present at the fixed path and executable."""
    bwrap_path = Path(BWRAP_CLI)
    try:
        st = bwrap_path.stat()
    except FileNotFoundError as exc:
        raise SandboxCliMissingError(f"BLOCKED_SANDBOX_MISSING: bubblewrap not found at {BWRAP_CLI}") from exc
    except PermissionError as exc:
        raise PermissionAccessError(f"BLOCKED_PERMISSION_DENIED: {exc}") from exc
    except OSError as exc:
        raise SandboxCliMissingError(f"BLOCKED_SANDBOX_MISSING: unable to stat {BWRAP_CLI}: {exc}") from exc
    if not stat.S_ISREG(st.st_mode):
        raise SandboxCliMissingError(f"BLOCKED_SANDBOX_MISSING: {BWRAP_CLI} is not a regular file")
    if not os.access(str(bwrap_path), os.X_OK):
        raise SandboxNotExecutableError(f"BLOCKED_SANDBOX_NOT_EXECUTABLE: {BWRAP_CLI} is not executable")
    return bwrap_path


def check_claude_available() -> Path:
    """Fail closed unless a Claude CLI is on PATH, a regular file, and executable."""
    found = shutil.which(CLAUDE_CLI)
    if found is None:
        raise ClaudeCliMissingError("BLOCKED_CLI_MISSING: claude CLI not found on PATH")
    claude_path = Path(found)
    try:
        st = claude_path.stat()
    except FileNotFoundError as exc:
        raise ClaudeCliMissingError(f"BLOCKED_CLI_MISSING: {exc}") from exc
    except PermissionError as exc:
        raise PermissionAccessError(f"BLOCKED_PERMISSION_DENIED: {exc}") from exc
    except OSError as exc:
        raise ClaudeCliMissingError(f"BLOCKED_CLI_MISSING: unable to stat claude CLI: {exc}") from exc
    if not stat.S_ISREG(st.st_mode):
        raise ClaudeCliMissingError(f"BLOCKED_CLI_MISSING: {claude_path} is not a regular file")
    if not os.access(str(claude_path), os.X_OK):
        raise ClaudeNotExecutableError(f"BLOCKED_CLAUDE_NOT_EXECUTABLE: {claude_path} is not executable")
    return claude_path


def _is_within_any(path: Path, roots: list[str]) -> bool:
    for root in roots:
        root_path = Path(root)
        if path == root_path or root_path in path.parents:
            return True
    return False


def validate_project_not_exposed(project: Path, home_dir: Path | None = None) -> None:
    """Reject a target project that overlaps any path mounted into Bubblewrap."""
    project_root = project.resolve()
    mounted_roots = [Path(path).resolve() for path in RUNTIME_READONLY_DIRS if Path(path).exists()]
    host_home = home_dir if home_dir is not None else Path.home()
    for credential_path in (host_home / ".claude", host_home / ".claude.json"):
        if credential_path.exists():
            mounted_roots.append(credential_path.resolve())
    for mounted in mounted_roots:
        if (
            project_root == mounted
            or mounted in project_root.parents
            or project_root in mounted.parents
        ):
            raise SandboxExposureError(
                f"BLOCKED_SANDBOX_EXPOSURE: target project overlaps sandbox runtime mount: {mounted}"
            )


def _claude_auth_env_pairs() -> list[tuple[str, str]]:
    pairs = []
    for name in CLAUDE_AUTH_ENV_PASSTHROUGH:
        value = os.environ.get(name)
        if value:
            pairs.append((name, value))
    return pairs


def build_bwrap_argv(
    bwrap_path: Path,
    claude_real_path: Path,
    workspace_dir: Path,
    scratch_dir: Path,
    claude_argv: list[str],
    home_dir: Path | None = None,
    auth_env: list[tuple[str, str]] | None = None,
) -> list[str]:
    """Build the fixed Bubblewrap argv that confines the Claude subprocess.

    Every namespace is unshared (--unshare-all: user, pid, ipc, uts, cgroup,
    mount, net) except network, which is explicitly re-granted (--share-net)
    because Claude needs it to reach the Claude API. --die-with-parent and
    --new-session are always set. The sandbox root is a private tmpfs
    (never the host root); only the minimum runtime directories needed to
    execute the resolved Claude binary are bound, and always read-only.
    The isolated workspace is the only read-write bind anywhere in the
    sandbox; --remount-ro / is applied after every other bind so that any
    directory bwrap had to auto-create as a mount anchor (e.g. an
    otherwise-empty /etc) becomes non-writable too, while the independent
    workspace bind mounted before it remains writable. /tmp is its own
    private, ephemeral tmpfs distinct from the host's real /tmp: anything
    written there is isolated and discarded with the sandbox, and never
    reaches the host or the target project.
    """
    host_home = home_dir if home_dir is not None else Path.home()

    argv: list[str] = [str(bwrap_path)]
    argv += ["--unshare-all", "--share-net"]
    argv += ["--die-with-parent", "--new-session"]
    argv += ["--clearenv"]
    argv += ["--setenv", "HOME", SANDBOX_HOME]
    argv += ["--setenv", "PATH", SANDBOX_PATH_ENV]
    for name, value in auth_env or []:
        argv += ["--setenv", name, value]

    argv += ["--tmpfs", "/"]
    argv += ["--proc", "/proc"]
    argv += ["--dev", "/dev"]
    argv += ["--tmpfs", "/tmp"]
    argv += ["--dir", "/run"]
    argv += ["--dir", "/run/ai-prof"]
    argv += ["--dir", "/home"]
    argv += ["--dir", SANDBOX_HOME]

    for directory in RUNTIME_READONLY_DIRS:
        if Path(directory).is_dir():
            argv += ["--ro-bind", directory, directory]
    for file_path in RUNTIME_READONLY_FILES:
        if Path(file_path).is_file():
            argv += ["--ro-bind", file_path, file_path]
    if not _is_within_any(claude_real_path, RUNTIME_READONLY_DIRS):
        argv += ["--ro-bind", str(claude_real_path), SANDBOX_CLAUDE]
        claude_argv = [SANDBOX_CLAUDE, *claude_argv[1:]]

    home_claude_dir = host_home / ".claude"
    home_claude_json = host_home / ".claude.json"
    if home_claude_dir.is_dir():
        argv += ["--ro-bind", str(home_claude_dir), f"{SANDBOX_HOME}/.claude"]
    if home_claude_json.is_file():
        argv += ["--ro-bind", str(home_claude_json), f"{SANDBOX_HOME}/.claude.json"]

    argv += ["--ro-bind", str(scratch_dir), SANDBOX_SCRATCH]
    argv += ["--bind", str(workspace_dir), SANDBOX_WORKSPACE]
    argv += ["--remount-ro", "/"]
    argv += ["--chdir", SANDBOX_WORKSPACE]
    argv += claude_argv
    return argv


def invoke_claude(bundle: str, workspace: Path, mcp_config_path: Path) -> subprocess.CompletedProcess:
    """Run Claude fully confined inside a real Bubblewrap sandbox.

    Bubblewrap and Claude availability are checked, and the Claude policy
    argv is built and validated, before any subprocess is ever spawned
    (fail closed). The sandbox grants read-write access to nothing but
    `workspace`; Claude's own Read/Edit permission prompts are never relied
    upon as the boundary.
    """
    bwrap_path = check_bwrap_available()
    claude_path = check_claude_available()

    policy_argv = build_claude_argv(mcp_config_path)
    validate_claude_argv(policy_argv, mcp_config_path)

    exec_argv = [str(claude_path), *policy_argv[1:]]
    mcp_index = exec_argv.index("--mcp-config") + 1
    exec_argv[mcp_index] = f"{SANDBOX_SCRATCH}/{mcp_config_path.name}"
    bwrap_argv = build_bwrap_argv(
        bwrap_path=bwrap_path,
        claude_real_path=claude_path.resolve(),
        workspace_dir=workspace.resolve(),
        scratch_dir=mcp_config_path.resolve().parent,
        claude_argv=exec_argv,
        auth_env=_claude_auth_env_pairs(),
    )

    try:
        result = subprocess.run(
            bwrap_argv,
            input=bundle,
            text=True,
            capture_output=True,
            timeout=CLAUDE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise InfrastructureTimeoutError(
            f"BLOCKED_INFRA_TIMEOUT: sandboxed claude invocation timed out: {exc}"
        ) from exc
    except FileNotFoundError as exc:
        raise SandboxCliMissingError(f"BLOCKED_SANDBOX_MISSING: {exc}") from exc
    except PermissionError as exc:
        raise PermissionAccessError(f"BLOCKED_PERMISSION_DENIED: {exc}") from exc
    except OSError as exc:
        raise ProcessLaunchError(f"BLOCKED_PROCESS_LAUNCH: unable to launch sandboxed claude: {exc}") from exc

    if (result.stderr or "").lstrip().startswith("bwrap:"):
        # Bubblewrap's own diagnostics are always prefixed this way; this
        # means the sandbox itself never started, which is an
        # infrastructure failure, never a Claude execution failure.
        raise SandboxSetupError(
            "BLOCKED_SANDBOX_SETUP: bubblewrap failed to establish the sandbox: "
            f"{(result.stderr or '')[:300]}"
        )
    return result


_AUTH_FAILURE_MARKERS = (
    "not authenticated", "not logged in", "please log in", "claude login",
    "invalid api key", "unauthorized", "authentication failed", "oauth",
    "credential", "401",
)

_PERMISSION_FAILURE_MARKERS = (
    "permission denied", "not authorized to", "not entitled",
    "quota exceeded", "rate limit exceeded", "insufficient permissions",
    "account suspended", "billing", "does not have access",
    "forbidden", "403",
)


def is_claude_auth_failure(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _AUTH_FAILURE_MARKERS)


def is_claude_permission_failure(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _PERMISSION_FAILURE_MARKERS)


def build_claude_bundle(task_text: str, context: dict[str, str]) -> str:
    """Assemble exactly the restricted content Claude may receive."""
    parts = ["# TASK", task_text.strip(), ""]
    for name in CLAUDE_CONTEXT_FILES:
        parts.append(f"# {name}")
        parts.append(context[name].strip())
        parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Path and symlink security (Blocker 3)
# ---------------------------------------------------------------------------

SCOPE_FILES_PATTERN = re.compile(r"(?mi)^\s*Scope-Files:\s*(.+?)\s*$")

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


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
    exists: bool


def _validate_raw_scope_path(raw: str) -> None:
    """Validate the raw submitted Scope-File string before any normalization.

    This runs before Path()/PurePosixPath()/resolve() ever see the string.
    A prior implementation stripped a leading "/" before checking for an
    absolute path, which silently turned "/etc/passwd" into the relative
    "etc/passwd" and let it slip past the absolute-path check entirely;
    this function closes that bypass by rejecting on the untouched raw
    string first.
    """
    if raw is None or raw == "":
        raise ScopeAccessError("BLOCKED_SCOPE_ACCESS: empty scope entry")
    if raw.strip() == "":
        raise ScopeAccessError("BLOCKED_SCOPE_ACCESS: whitespace-only scope entry")
    if raw != raw.strip():
        raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: scope entry has surrounding whitespace: {raw!r}")
    if "\x00" in raw:
        raise ScopeAccessError("BLOCKED_SCOPE_ACCESS: NUL byte in scope entry")
    if "\\" in raw:
        raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: backslash path separator rejected: {raw!r}")
    if raw.startswith("/"):
        raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: absolute path rejected: {raw!r}")
    if _WINDOWS_DRIVE_RE.match(raw):
        raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: windows drive path rejected: {raw!r}")
    if raw in (".", ".."):
        raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: '{raw}' is not a valid file path")
    parts = raw.split("/")
    if any(part == "" for part in parts):
        raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: empty path component: {raw!r}")
    if any(part == ".." for part in parts):
        raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: path traversal rejected: {raw!r}")
    if any(part == "." for part in parts):
        raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: '.' path component rejected: {raw!r}")


def _lstat_component(path: Path) -> os.stat_result:
    try:
        return path.lstat()
    except PermissionError as exc:
        raise PermissionAccessError(f"BLOCKED_PERMISSION_DENIED: {exc}") from exc
    except OSError as exc:
        raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: unable to inspect path {path}: {exc}") from exc


def resolve_scope_entries(project: Path, entries: list[str]) -> list[ScopeEntry]:
    """Validate declared scope entries and resolve them safely.

    Every raw string is validated before any normalization (see
    _validate_raw_scope_path), then walked component-by-component from the
    trusted, resolved project root using lstat — never following a symlink
    at any level, including the final component, even one that would
    resolve back inside the project. A scope entry that does not yet exist
    is accepted only when every parent component is already a real,
    non-symlink directory: this is the one safe case where Claude may
    create a brand-new approved file. Anything that cannot be proven safe
    this way is rejected rather than guessed at.
    """
    if not entries:
        raise ScopeAccessError(
            "BLOCKED_SCOPE_ACCESS: task does not declare an explicit Scope-Files allowlist"
        )
    project_root = project.resolve()
    resolved: list[ScopeEntry] = []
    for raw in entries:
        _validate_raw_scope_path(raw)
        parts = raw.split("/")
        current = project_root
        for part in parts[:-1]:
            current = current / part
            st = _lstat_component(current)
            if stat.S_ISLNK(st.st_mode):
                raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: symlink in scope path rejected: {raw}")
            if not stat.S_ISDIR(st.st_mode):
                raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: expected directory component in {raw}")
        leaf = current / parts[-1]
        try:
            leaf_stat = leaf.lstat()
        except FileNotFoundError:
            resolved.append(ScopeEntry(relative=raw, absolute=leaf, is_dir=False, exists=False))
            continue
        except PermissionError as exc:
            raise PermissionAccessError(f"BLOCKED_PERMISSION_DENIED: {exc}") from exc
        except OSError as exc:
            raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: unable to inspect scope entry {raw}: {exc}") from exc
        if stat.S_ISLNK(leaf_stat.st_mode):
            raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: symlink as scope entry rejected: {raw}")
        if not (stat.S_ISREG(leaf_stat.st_mode) or stat.S_ISDIR(leaf_stat.st_mode)):
            raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: unsupported file type for scope entry: {raw}")
        resolved.append(ScopeEntry(
            relative=raw, absolute=leaf, is_dir=stat.S_ISDIR(leaf_stat.st_mode), exists=True,
        ))
    return resolved


# ---------------------------------------------------------------------------
# Isolated staging workspace (Blocker 2)
# ---------------------------------------------------------------------------


def _copy_scope_safe(src: Path, dst: Path) -> None:
    """Copy a single approved source into the workspace, no symlinks allowed.

    Every symlink is rejected outright, at any depth, even one whose target
    resolves back inside the project: Blocker 3 requires no internal
    symlink redirection to ever be trusted, so the isolated workspace must
    never receive one either.
    """
    try:
        st = src.lstat()
    except PermissionError as exc:
        raise PermissionAccessError(f"BLOCKED_PERMISSION_DENIED: unable to read {src}: {exc}") from exc
    except OSError as exc:
        raise ScopeReadError(f"BLOCKED_SCOPE_READ: unable to read {src}: {exc}") from exc

    if stat.S_ISLNK(st.st_mode):
        raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: symlink rejected inside approved scope: {src}")

    if stat.S_ISDIR(st.st_mode):
        try:
            dst.mkdir(parents=True, exist_ok=True)
            children = sorted(src.iterdir())
        except PermissionError as exc:
            raise PermissionAccessError(f"BLOCKED_PERMISSION_DENIED: {exc}") from exc
        except OSError as exc:
            raise WorkspaceWriteError(
                f"BLOCKED_WORKSPACE_WRITE: unable to prepare workspace directory {dst}: {exc}"
            ) from exc
        for child in children:
            _copy_scope_safe(child, dst / child.name)
    elif stat.S_ISREG(st.st_mode):
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        except PermissionError as exc:
            raise PermissionAccessError(f"BLOCKED_PERMISSION_DENIED: {exc}") from exc
        except OSError as exc:
            raise WorkspaceWriteError(
                f"BLOCKED_WORKSPACE_WRITE: unable to copy {src} into workspace: {exc}"
            ) from exc
    else:
        raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: unsupported file type in approved scope: {src}")


def snapshot_workspace(workspace_dir: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(workspace_dir.rglob("*")):
        if path.is_file():
            snapshot[str(path.relative_to(workspace_dir))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def build_isolated_workspace(workspace_dir: Path, project: Path, entries: list[ScopeEntry]) -> dict[str, str]:
    """Copy only approved, existing scope paths into workspace_dir.

    The Control Center root and the real target project are never exposed:
    workspace_dir contains nothing but these explicitly approved paths.
    Scope entries that do not yet exist (an approved brand-new file) are
    intentionally not copied; Claude creates that content directly.
    """
    workspace_root = workspace_dir.resolve()
    for entry in entries:
        if not entry.exists:
            continue
        destination = (workspace_dir / entry.relative).resolve()
        if destination != workspace_root and workspace_root not in destination.parents:
            raise ScopeAccessError("BLOCKED_SCOPE_ACCESS: workspace containment violated")
        _copy_scope_safe(entry.absolute, workspace_dir / entry.relative)
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


def _is_scope_ancestor(relative_path: str, entries: list[ScopeEntry]) -> bool:
    """True if relative_path is a directory Claude legitimately needed to
    create on the way to a nested approved (file or directory) scope entry.
    """
    candidate = PurePosixPath(relative_path)
    for entry in entries:
        entry_path = PurePosixPath(entry.relative)
        if candidate == entry_path or candidate in entry_path.parents:
            return True
    return False


def validate_workspace_changes(changes: dict[str, str], entries: list[ScopeEntry]) -> None:
    for relative in changes:
        if not _is_within_scope(relative, entries):
            raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: change outside approved scope: {relative}")


def audit_workspace_integrity(workspace_dir: Path, entries: list[ScopeEntry]) -> None:
    """Walk the entire sandboxed workspace and reject anything unsafe.

    Runs after Claude exits successfully, before any diff is trusted:
    rejects symlinks, devices, sockets, FIFOs, or any path that is not
    itself an approved scope entry or a directory on the way to one. A
    file-content diff alone (compute_workspace_diff) cannot see an empty,
    unexpected directory or a newly created symlink; this walk can.
    """
    workspace_root = workspace_dir.resolve()
    for path in sorted(workspace_root.rglob("*")):
        relative = str(path.relative_to(workspace_root))
        try:
            st = path.lstat()
        except OSError as exc:
            raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: unable to inspect sandbox output {relative}: {exc}") from exc
        if stat.S_ISLNK(st.st_mode):
            raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: symlink created in sandbox workspace: {relative}")
        if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
            raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: unexpected special file in sandbox workspace: {relative}")
        if not (_is_within_scope(relative, entries) or _is_scope_ancestor(relative, entries)):
            raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: unexpected path in sandbox workspace: {relative}")


def snapshot_scope_sources(project: Path, entries: list[ScopeEntry]) -> dict[str, str]:
    """Hash the current on-disk content of every existing approved scope path."""
    project_root = project.resolve()
    snapshot: dict[str, str] = {}
    for entry in entries:
        if not entry.exists:
            continue
        if entry.is_dir:
            for child in sorted(entry.absolute.rglob("*")):
                if child.is_symlink() or not child.is_file():
                    continue
                snapshot[str(child.relative_to(project_root))] = hashlib.sha256(child.read_bytes()).hexdigest()
        else:
            snapshot[entry.relative] = hashlib.sha256(entry.absolute.read_bytes()).hexdigest()
    return snapshot


def verify_scope_sources_unchanged(project: Path, entries: list[ScopeEntry], baseline: dict[str, str]) -> None:
    """Refuse to apply anything if the real project changed underneath us.

    Guards the window between when the isolated workspace was built (and
    the baseline was captured) and when the validated change set is about
    to be applied: if a concurrent process touched an approved scope file
    on the real project in the meantime, the diff Claude produced is no
    longer trustworthy against the current state.
    """
    current = snapshot_scope_sources(project, entries)
    if current != baseline:
        raise ConcurrentModificationError(
            "BLOCKED_CONCURRENT_MODIFICATION: approved scope files changed in the target project during execution"
        )


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


def _validate_destination_path(project_root: Path, relative: str) -> None:
    """Pure pre-flight check: raise if any existing component is unsafe.

    Never creates anything. Run once for every change in a batch before any
    write happens, so a failure anywhere in the batch never leaves the
    project partially patched.
    """
    parts = relative.split("/")
    current = project_root
    for index, part in enumerate(parts):
        current = current / part
        try:
            st = current.lstat()
        except FileNotFoundError:
            continue
        except PermissionError as exc:
            raise PermissionAccessError(f"BLOCKED_PERMISSION_DENIED: {exc}") from exc
        except OSError as exc:
            raise PatchAccessError(f"BLOCKED_PATCH_ACCESS: unable to inspect destination {current}: {exc}") from exc
        is_last = index == len(parts) - 1
        if stat.S_ISLNK(st.st_mode):
            raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: destination path component is a symlink: {current}")
        if not is_last and not stat.S_ISDIR(st.st_mode):
            raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: destination parent is not a directory: {current}")
        if is_last and not stat.S_ISREG(st.st_mode):
            raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: destination is not a regular file: {current}")


def _write_atomic(directory: Path, filename: str, content: bytes) -> None:
    """Write via a same-directory temp file plus os.replace.

    POSIX rename() replaces whatever is named at the destination without
    ever following it as a symlink, so even a symlink planted at `filename`
    between validation and this call cannot redirect the write.
    """
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=f".ai-prof-tmp-{filename}-", dir=str(directory))
    except OSError as exc:
        raise PatchAccessError(f"BLOCKED_PATCH_ACCESS: unable to create temp file in {directory}: {exc}") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.replace(tmp_path, str(directory / filename))
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise PatchAccessError(
            f"BLOCKED_PATCH_ACCESS: unable to atomically write {directory / filename}: {exc}"
        ) from exc


def _delete_safely(target: Path) -> None:
    try:
        st = target.lstat()
    except FileNotFoundError:
        return
    except PermissionError as exc:
        raise PermissionAccessError(f"BLOCKED_PERMISSION_DENIED: {exc}") from exc
    except OSError as exc:
        raise PatchAccessError(f"BLOCKED_PATCH_ACCESS: unable to inspect {target} for deletion: {exc}") from exc
    if stat.S_ISLNK(st.st_mode):
        raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: refusing to delete through symlink: {target}")
    if not stat.S_ISREG(st.st_mode):
        raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: refusing to delete non-regular file: {target}")
    try:
        target.unlink()
    except OSError as exc:
        raise PatchAccessError(f"BLOCKED_PATCH_ACCESS: unable to delete {target}: {exc}") from exc


def apply_validated_changes(
    project: Path,
    change_set: list[tuple[str, str, bytes | None]],
    entries: list[ScopeEntry],
) -> list[str]:
    """Apply an already-validated change set to the real project, atomically.

    Two phases: every change is re-validated against both the approved
    scope and a fresh, symlink-safe filesystem walk *before* anything is
    written, so a failure partway through never leaves the project
    partially patched. Each destination is re-checked again immediately
    before its own write to shrink the time-of-check/time-of-use window,
    and the write itself always goes through a same-directory temp file
    plus os.replace (see _write_atomic).
    """
    project_root = project.resolve()

    for relative, _action, _content in change_set:
        if not _is_within_scope(relative, entries):
            raise ScopeAccessError(f"BLOCKED_SCOPE_ACCESS: change outside approved scope: {relative}")
        _validate_destination_path(project_root, relative)

    backups: dict[str, bytes | None] = {}
    created_dirs: list[Path] = []
    for relative, _action, _content in change_set:
        target = project_root / relative
        try:
            backups[relative] = target.read_bytes()
        except FileNotFoundError:
            backups[relative] = None
        except OSError as exc:
            raise PatchAccessError(f"BLOCKED_PATCH_ACCESS: unable to back up {target}: {exc}") from exc

    applied: list[str] = []
    try:
        for relative, action, content in change_set:
            parts = relative.split("/")
            parent_dir = project_root
            for part in parts[:-1]:
                parent_dir = parent_dir / part
                try:
                    st = parent_dir.lstat()
                except FileNotFoundError:
                    st = None
                except PermissionError as exc:
                    raise PermissionAccessError(f"BLOCKED_PERMISSION_DENIED: {exc}") from exc
                except OSError as exc:
                    raise PatchAccessError(f"BLOCKED_PATCH_ACCESS: unable to inspect {parent_dir}: {exc}") from exc
                if st is None:
                    try:
                        parent_dir.mkdir()
                        created_dirs.append(parent_dir)
                    except OSError as exc:
                        raise PatchAccessError(f"BLOCKED_PATCH_ACCESS: unable to create {parent_dir}: {exc}") from exc
                    continue
                if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                    raise ScopeAccessError(
                        f"BLOCKED_SCOPE_ACCESS: destination parent unsafe at apply time: {parent_dir}"
                    )

            target = parent_dir / parts[-1]
            _validate_destination_path(project_root, relative)
            if action == "deleted":
                _delete_safely(target)
            else:
                _write_atomic(parent_dir, target.name, content)
            applied.append(f"{action}:{relative}")
    except Exception as apply_exc:
        rollback_error: OSError | None = None
        for relative, original in reversed(list(backups.items())):
            target = project_root / relative
            try:
                if original is None:
                    if target.exists():
                        target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _write_atomic(target.parent, target.name, original)
            except (OSError, AccessFailure) as exc:
                if rollback_error is None:
                    rollback_error = OSError(str(exc))
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        if rollback_error is not None:
            raise PatchAccessError(
                f"BLOCKED_PATCH_ACCESS: apply failed and rollback was incomplete: {rollback_error}"
            ) from apply_exc
        raise
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

        check_claude_available()
        check_bwrap_available()
        validate_project_not_exposed(project)

        scope_entries = resolve_scope_entries(project, parse_scope_files(task_text))
        source_baseline = snapshot_scope_sources(project, scope_entries)

        # From here on the real project is READ-ONLY until Claude succeeds and
        # the isolated workspace diff has been validated (Blocker 2).
        try:
            isolation_cm = tempfile.TemporaryDirectory(prefix="ai-prof-claude-")
        except OSError as exc:
            raise TempDirectoryError(f"BLOCKED_TEMP_DIRECTORY: unable to create isolated workspace: {exc}") from exc

        with isolation_cm as isolation_root_str:
            isolation_root = Path(isolation_root_str)
            workspace_dir = isolation_root / "workspace"
            scratch_dir = isolation_root / "scratch"
            try:
                workspace_dir.mkdir()
                scratch_dir.mkdir()
            except OSError as exc:
                raise TempDirectoryError(
                    f"BLOCKED_TEMP_DIRECTORY: unable to prepare isolated workspace: {exc}"
                ) from exc

            before_snapshot = build_isolated_workspace(workspace_dir, project, scope_entries)
            mcp_config_path = build_claude_mcp_config(scratch_dir)

            bundle = build_claude_bundle(task_text, context)
            result = invoke_claude(bundle, workspace_dir, mcp_config_path)
            if result.returncode != 0:
                detail = f"{result.stdout or ''}\n{result.stderr or ''}".strip()[:500]
                if is_claude_auth_failure(detail):
                    raise ClaudeAuthError(f"BLOCKED_CLAUDE_AUTH: claude authentication failed: {detail}")
                if is_claude_permission_failure(detail):
                    raise ClaudePermissionError(f"BLOCKED_CLAUDE_PERMISSION: claude account/permission failure: {detail}")
                raise ClaudeExecutionError(f"CLAUDE_FAILED: claude exited with {result.returncode}: {detail}")

            audit_workspace_integrity(workspace_dir, scope_entries)
            changes = compute_workspace_diff(workspace_dir, before_snapshot)
            validate_workspace_changes(changes, scope_entries)
            change_set = build_change_set(workspace_dir, changes)

        # Only now, after success + validated scope, may the real project be
        # touched: first re-confirm nothing changed underneath us, then
        # prepare the target branch and apply the validated change set.
        verify_scope_sources_unchanged(project, scope_entries, source_baseline)
        ensure_work_branch(project, data["Base-Branch"], data["Work-Branch"])
        applied_changes = apply_validated_changes(project, change_set, scope_entries)

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
            "sandbox=bubblewrap",
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
