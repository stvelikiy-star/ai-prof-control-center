#!/usr/bin/env python3
"""Fail-closed release preparation inside AI PROF Control Center."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping

DEFAULT_ROOT = Path("/home/agent/projects/ai-prof-control-center")
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class ReleaseError(RuntimeError):
    """Invalid release configuration or execution contract."""


def run(
    command: list[str],
    *,
    cwd: Path,
    timeout: int = 900,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
        env={**os.environ, "PAGER": "cat", "GIT_PAGER": "cat"},
    )


def load_project(root: Path, project_id: str) -> dict:
    path = root / "orchestrator/projects.json"

    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError("project registry is unavailable or invalid") from exc

    projects = registry.get("projects")

    if not isinstance(projects, list):
        raise ReleaseError("project registry has no projects list")

    for project in projects:
        if isinstance(project, dict) and project.get("project_id") == project_id:
            return project

    raise ReleaseError(f"unknown project: {project_id}")


def secret_file_keys(path: Path) -> set[str]:
    if not path.is_file():
        return set()

    keys: set[str] = set()

    for raw in path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines():
        line = raw.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key = line.split("=", 1)[0].strip()

        if KEY_RE.fullmatch(key):
            keys.add(key)

    return keys


def prepare(
    root: Path,
    project_id: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict:
    environ = os.environ if environ is None else environ
    project = load_project(root, project_id)
    release = project.get("release")

    blockers: list[str] = []
    checks: list[dict] = []

    if not isinstance(release, dict):
        return {
            "project": project_id,
            "state": "OWNER_ACTION_REQUIRED",
            "checks": [],
            "blockers": ["RELEASE_PROFILE_MISSING"],
            "production_changed": False,
        }

    project_path = Path(str(project.get("path", ""))).expanduser()

    if not project_path.is_dir():
        blockers.append("PROJECT_PATH_UNAVAILABLE")
    elif not (project_path / ".git").exists():
        blockers.append("PROJECT_IS_NOT_GIT_REPOSITORY")

    expected_branch = release.get("branch")

    if project_path.is_dir() and isinstance(expected_branch, str):
        result = run(
            ["git", "branch", "--show-current"],
            cwd=project_path,
            timeout=30,
        )
        branch = result.stdout.strip()

        checks.append({
            "name": "git_branch",
            "status": "PASS" if result.returncode == 0 and branch == expected_branch else "FAIL",
        })

        if result.returncode != 0 or branch != expected_branch:
            blockers.append("RELEASE_BRANCH_MISMATCH")

        status = run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=project_path,
            timeout=30,
        )

        clean = status.returncode == 0 and not status.stdout.strip()

        checks.append({
            "name": "git_worktree",
            "status": "PASS" if clean else "FAIL",
        })

        if not clean:
            blockers.append("PROJECT_WORKTREE_NOT_CLEAN")

    required_commands = release.get("required_commands", [])

    if not isinstance(required_commands, list):
        raise ReleaseError("release required_commands must be a list")

    missing_commands = [
        str(command)
        for command in required_commands
        if not isinstance(command, str) or shutil.which(command) is None
    ]

    checks.append({
        "name": "required_commands",
        "status": "PASS" if not missing_commands else "FAIL",
    })

    blockers.extend(
        f"MISSING_COMMAND:{command}"
        for command in missing_commands
    )

    required_environment = release.get("required_environment", [])
    secret_path_value = release.get("secret_file")
    secret_keys: set[str] = set()

    if isinstance(secret_path_value, str) and secret_path_value:
        secret_keys = secret_file_keys(
            Path(secret_path_value).expanduser()
        )

    missing_environment = [
        key
        for key in required_environment
        if (
            not isinstance(key, str)
            or not KEY_RE.fullmatch(key)
            or (not environ.get(key) and key not in secret_keys)
        )
    ]

    checks.append({
        "name": "required_environment",
        "status": "PASS" if not missing_environment else "FAIL",
    })

    blockers.extend(
        f"MISSING_ENVIRONMENT:{key}"
        for key in missing_environment
    )

    backup_script_value = release.get("backup_script")
    backup_markers = release.get("backup_markers", [])

    if not isinstance(backup_script_value, str) or not backup_script_value:
        blockers.append("BACKUP_SCRIPT_NOT_CONFIGURED")
        checks.append({"name": "backup_contract", "status": "FAIL"})
    else:
        backup_script = Path(backup_script_value).expanduser()

        if not backup_script.is_file():
            blockers.append("BACKUP_SCRIPT_UNAVAILABLE")
            checks.append({"name": "backup_contract", "status": "FAIL"})
        else:
            source = backup_script.read_text(
                encoding="utf-8",
                errors="replace",
            ).casefold()

            missing_markers = [
                str(marker)
                for marker in backup_markers
                if str(marker).casefold() not in source
            ]

            checks.append({
                "name": "backup_contract",
                "status": "PASS" if not missing_markers else "FAIL",
            })

            blockers.extend(
                f"BACKUP_MARKER_MISSING:{marker}"
                for marker in missing_markers
            )

    configured_checks = release.get("checks", [])

    if not isinstance(configured_checks, list):
        raise ReleaseError("release checks must be a list")

    # Fail fast: expensive tests and builds are pointless until the
    # release environment, tools and backup contract are available.
    if blockers:
        for index, command in enumerate(configured_checks, start=1):
            checks.append({
                "name": f"release_check_{index}",
                "status": "SKIPPED",
                "command": command,
            })

        return {
            "project": project_id,
            "state": "OWNER_ACTION_REQUIRED",
            "checks": checks,
            "blockers": blockers,
            "production_changed": False,
        }

    for index, command in enumerate(configured_checks, start=1):
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(value, str) and value for value in command)
        ):
            blockers.append(f"INVALID_RELEASE_CHECK:{index}")
            continue

        if not project_path.is_dir():
            blockers.append(f"RELEASE_CHECK_NOT_RUN:{index}")
            continue

        result = run(command, cwd=project_path)
        passed = result.returncode == 0

        checks.append({
            "name": f"release_check_{index}",
            "status": "PASS" if passed else "FAIL",
            "command": command,
            "exit_code": result.returncode,
        })

        if not passed:
            blockers.append(f"RELEASE_CHECK_FAILED:{index}")

    return {
        "project": project_id,
        "state": "RELEASE_READY" if not blockers else "OWNER_ACTION_REQUIRED",
        "checks": checks,
        "blockers": blockers,
        "production_changed": False,
    }


def render(report: dict) -> str:
    lines = [
        "AI PROF release preparation",
        f"Project: {report['project']}",
        f"State: {report['state']}",
        "Checks:",
    ]

    for check in report["checks"]:
        lines.append(f"- {check['name']}: {check['status']}")

    if report["blockers"]:
        lines.append("Blockers:")
        lines.extend(f"- {blocker}" for blocker in report["blockers"])

    lines.extend([
        "Production changed: no",
        "Migration applied: no",
        "Deploy performed: no",
    ])

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare"])
    parser.add_argument("--project", required=True)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()

    try:
        report = prepare(args.root.resolve(), args.project)
    except ReleaseError as exc:
        print(f"RELEASE_ERROR: {exc}", file=sys.stderr)
        return 1

    print(render(report))
    return 0 if report["state"] == "RELEASE_READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
