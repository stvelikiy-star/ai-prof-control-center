#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_FIELDS = [
    "Task-ID",
    "Project-Path",
    "Base-Branch",
    "Work-Branch",
    "Agent-Context",
    "Goal",
    "Scope",
    "Out-of-Scope",
    "Pass-Criteria",
    "Required-Checks",
    "Owner-Approval-Required",
]


@dataclass
class Paths:
    root: Path
    pending: Path
    active: Path
    review: Path
    failed: Path
    completed: Path
    blocked: Path
    logs: Path


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=check,
    )


def load_config(root: Path) -> dict:
    path = root / "orchestrator" / "config.json"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def parse_task(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    values: dict[str, str] = {}
    for field in REQUIRED_FIELDS:
        match = re.search(rf"(?mi)^\s*{re.escape(field)}:\s*(.+?)\s*$", text)
        if match:
            values[field] = match.group(1).strip()
    missing = [field for field in REQUIRED_FIELDS if not values.get(field)]
    if missing:
        raise ValueError("Missing required fields: " + ", ".join(missing))
    if values["Owner-Approval-Required"].lower() not in {"yes", "no"}:
        raise ValueError("Owner-Approval-Required must be yes or no")
    return values


def git_clean(project: Path) -> bool:
    result = run(["git", "status", "--porcelain"], cwd=project)
    return not result.stdout.strip()


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def next_task(paths: Paths) -> Path | None:
    tasks = sorted(paths.pending.glob("*.md"))
    return tasks[0] if tasks else None


def move_task(task: Path, target_dir: Path) -> Path:
    target = target_dir / task.name
    os.replace(task, target)
    return target


def validate_task(task: Path, config: dict) -> tuple[dict[str, str], Path]:
    data = parse_task(task)
    project = Path(data["Project-Path"]).expanduser().resolve()
    if not (project / ".git").is_dir():
        raise ValueError(f"Project repository not found: {project}")
    if config.get("require_clean_worktree", True) and not git_clean(project):
        raise ValueError(f"Project working tree is not clean: {project}")
    if data["Work-Branch"] in {"main", "develop"}:
        raise ValueError("Work-Branch must be a dedicated feature/fix branch")
    return data, project


def build_prompt(task_path: Path, data: dict[str, str], root: Path) -> str:
    context_path = root / data["Agent-Context"]
    if not context_path.exists():
        raise ValueError(f"Agent context not found: {context_path}")

    system_path = context_path / "SYSTEM_INSTRUCTIONS.md"
    source_path = context_path / "SOURCE_POLICY.md"
    state_path = context_path / "STATE.md"
    for required in (system_path, source_path, state_path):
        if not required.exists():
            raise ValueError(f"Required context file missing: {required}")

    return f"""You are Claude Code, the implementation agent.

Read and obey:
- {system_path}
- {source_path}
- {state_path}
- Task: {task_path}

Repository: {data['Project-Path']}
Base branch: {data['Base-Branch']}
Work branch: {data['Work-Branch']}

Rules:
- Do not work directly on main or develop.
- Do not merge.
- Do not deploy production.
- Do not print secrets.
- Stop with BLOCKED_MISSING_ACCESS when required access is absent.
- Complete only the approved scope.
- Run the required checks.
- Commit changes and produce a concise report with commit SHA, changed files, checks, residual risks.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/home/agent/projects/ai-prof-control-center")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    paths = Paths(
        root=root,
        pending=root / "queue" / "pending",
        active=root / "queue" / "active",
        review=root / "queue" / "review",
        failed=root / "queue" / "failed",
        completed=root / "queue" / "completed",
        blocked=root / "queue" / "blocked",
        logs=root / "logs" / "orchestrator",
    )
    config = load_config(root)

    for directory in vars(paths).values():
        if isinstance(directory, Path):
            directory.mkdir(parents=True, exist_ok=True)

    if args.self_test:
        assert config["allow_merge"] is False
        assert config["allow_production_deploy"] is False
        assert (root / "agents" / "ak-bermet" / "SYSTEM_INSTRUCTIONS.md").exists()
        print("AI PROF orchestrator self-test: PASS")
        return 0

    task = next_task(paths)
    if task is None:
        print("QUEUE_EMPTY")
        return 0

    active_task = move_task(task, paths.active)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = paths.logs / f"{active_task.stem}-{timestamp}.log"

    try:
        data, project = validate_task(active_task, config)
        prompt = build_prompt(active_task, data, root)

        if args.dry_run:
            log_path.write_text(
                "DRY_RUN_PASS\n"
                f"task={active_task}\n"
                f"project={project}\n"
                f"base_branch={data['Base-Branch']}\n"
                f"work_branch={data['Work-Branch']}\n",
                encoding="utf-8",
            )
            move_task(active_task, paths.review)
            print("DRY_RUN_PASS")
            return 0

        if not command_exists("claude"):
            raise RuntimeError("BLOCKED_MISSING_CLAUDE")
        if not command_exists("codex"):
            raise RuntimeError("BLOCKED_MISSING_CODEX")

        claude = run(["claude", "-p", prompt], cwd=project, check=False)
        log_path.write_text(
            "CLAUDE_STDOUT\n" + claude.stdout +
            "\nCLAUDE_STDERR\n" + claude.stderr,
            encoding="utf-8",
        )

        if claude.returncode != 0:
            raise RuntimeError(f"CLAUDE_FAILED_{claude.returncode}")

        move_task(active_task, paths.review)
        print("CLAUDE_COMPLETE_REVIEW_REQUIRED")
        return 0

    except Exception as exc:
        destination = paths.blocked if "BLOCKED_" in str(exc) or "not found" in str(exc) else paths.failed
        if active_task.exists():
            move_task(active_task, destination)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"\nERROR\n{type(exc).__name__}: {exc}\n")
        print(f"ORCHESTRATOR_STOPPED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
