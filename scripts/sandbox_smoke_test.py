#!/usr/bin/env python3
"""End-to-end smoke test for the production Bubblewrap command builder."""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "orchestrator" / "claude_runner.py"
SPEC = importlib.util.spec_from_file_location("ai_prof_smoke_claude_runner", RUNNER)
cr = importlib.util.module_from_spec(SPEC)
if SPEC.loader is None:
    raise RuntimeError("cannot load production sandbox builder")
sys.modules[SPEC.name] = cr
SPEC.loader.exec_module(cr)

STAGE_TIMEOUT = 30
ONLINE_TIMEOUT = 120
EXPECTED = "AI_PROF_SANDBOX_OK"
ONLINE_REQUEST = "Reply with exactly AI_PROF_SANDBOX_OK and nothing else."


def fail(stage: str, detail: str) -> None:
    safe = cr.sanitize_sandbox_stderr(detail)
    print(f"FAIL stage={stage}: {safe}", file=sys.stderr)
    raise SystemExit(1)


def git_state(project: Path) -> tuple[str, str]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=project, text=True,
        capture_output=True, check=True, timeout=STAGE_TIMEOUT,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain=v1"], cwd=project, text=True,
        capture_output=True, check=True, timeout=STAGE_TIMEOUT,
    ).stdout
    return head, status


def run_stage(
    name: str,
    command: list[str],
    *,
    workspace: Path,
    scratch: Path,
    private_home: Path,
    toolchain: cr.NodeToolchain,
    online: bool = False,
    stdin: str | None = None,
    cli: Path | None = None,
) -> subprocess.CompletedProcess:
    argv = cr.build_bwrap_argv(
        cr.check_bwrap_available(), workspace, scratch, command,
        node_toolchain=toolchain, private_home_dir=private_home, allow_network=online,
        readonly_binds=[(cli.resolve(), cr.SANDBOX_CLAUDE)] if cli else None,
        credentials_home=Path.home() if online else None,
        environment=cr._claude_auth_env_pairs() if online else None,
    )
    if name == "true":
        assert argv[-1] == "/bin/true", (
            f"stage true must end in /bin/true, got {argv[-1]!r}"
        )
        assert cr.SANDBOX_CLAUDE not in argv, "stage true must never mount or execute Claude"
    try:
        result = subprocess.run(
            argv, input=stdin, text=True, capture_output=True,
            timeout=ONLINE_TIMEOUT if online else STAGE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        fail(name, str(exc))
    if (result.stderr or "").lstrip().startswith("bwrap:"):
        fail(name, str(cr.classify_sandbox_setup_error(result.stderr)))
    if result.returncode:
        fail(name, result.stderr or result.stdout or f"exit {result.returncode}")
    print(f"PASS stage={name}")
    return result


def contains_expected(payload: str) -> bool:
    if payload.strip() == EXPECTED:
        return True
    try:
        value = json.loads(payload)
    except ValueError:
        return False
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str) and item.strip() == EXPECTED:
            return True
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--online", action="store_true")
    args = parser.parse_args()

    project = args.project.resolve(strict=True)
    scope = (project / args.scope).resolve(strict=True)
    if project not in scope.parents or not scope.is_dir():
        parser.error("--scope must be an existing directory inside --project")
    before = git_state(project)
    if before[1]:
        fail("repository-clean-before", "target repository is not clean")

    toolchain = cr.locate_nvm_node_toolchain()
    cli = cr.check_claude_available().resolve()
    with tempfile.TemporaryDirectory(prefix="ai-prof-sandbox-smoke-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        scratch = root / "scratch"
        private_home = root / "home"
        shutil.copytree(scope, workspace, symlinks=True)
        scratch.mkdir()
        for relative in (".cache", ".config", ".local/state"):
            (private_home / relative).mkdir(parents=True, exist_ok=True)

        stages = [
            ("true", ["/bin/true"]),
            ("git-version", ["/usr/bin/git", "--version"]),
            ("python-version", ["/usr/bin/python3", "--version"]),
            ("node-version", [f"{cr.SANDBOX_NODE_ROOT}/bin/node", "--version"]),
            ("npm-version", [f"{cr.SANDBOX_NODE_ROOT}/bin/npm", "--version"]),
            ("npx-version", [f"{cr.SANDBOX_NODE_ROOT}/bin/npx", "--version"]),
        ]
        for name, command in stages:
            run_stage(
                name, command, workspace=workspace, scratch=scratch,
                private_home=private_home, toolchain=toolchain,
            )

        probe = (
            "from pathlib import Path\n"
            "p=Path('/workspace/.ai-prof-smoke.tmp');p.write_text('ok');p.unlink()\n"
            "assert not Path('/workspace/.ai-prof-smoke.tmp').exists()\n"
        )
        run_stage(
            "scope-write-remove", ["/usr/bin/python3", "-c", probe],
            workspace=workspace, scratch=scratch, private_home=private_home,
            toolchain=toolchain,
        )
        isolation = (
            "from pathlib import Path\n"
            f"assert not Path({str(ROOT)!r}).exists()\n"
            f"assert not Path({str(project / '.git')!r}).exists()\n"
            "assert not Path('/home/agent/.ssh').exists()\n"
            "try:\n"
            f" Path({str(ROOT / '.smoke-write')!r}).write_text('bad')\n"
            "except OSError: pass\n"
            "else: raise SystemExit('control-center write unexpectedly succeeded')\n"
        )
        run_stage(
            "host-isolation", ["/usr/bin/python3", "-c", isolation],
            workspace=workspace, scratch=scratch, private_home=private_home,
            toolchain=toolchain,
        )

        if args.online:
            mcp = cr.build_claude_mcp_config(scratch)
            policy = cr.build_claude_argv(mcp)
            cr.validate_claude_argv(policy, mcp)
            command = [cr.SANDBOX_CLAUDE, *policy[1:]]
            command[command.index("--mcp-config") + 1] = f"{cr.SANDBOX_SCRATCH}/{mcp.name}"
            result = run_stage(
                "online-agent", command, workspace=workspace, scratch=scratch,
                private_home=private_home, cli=cli, toolchain=toolchain,
                online=True,
                stdin=ONLINE_REQUEST,
            )
            if not contains_expected(result.stdout):
                fail("online-exact-response", result.stdout or result.stderr)
            print("PASS stage=online-exact-response")

    after = git_state(project)
    if after != before:
        fail("repository-clean-after", "target repository state changed")
    print("AI_PROF_SANDBOX_SMOKE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
