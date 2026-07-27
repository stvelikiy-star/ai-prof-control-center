#!/usr/bin/env python3
"""Validated, atomic local task intake for the AI PROF Control Center."""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_paths import DEFAULT_STATE_ROOT, initialize


DEFAULT_ROOT = Path("/home/agent/projects/ai-prof-control-center")
TITLE_LIMIT = 120
INSTRUCTION_LIMIT = 4000
SCOPE_COUNT_LIMIT = 20
SCOPE_ENTRY_LIMIT = 240
TASK_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{5,79}$")
PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
WORK_BRANCH_RE = re.compile(r"^(feature|fix)/[A-Za-z0-9._/-]+$")
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
QUEUE_NAMES = (
    "pending", "active", "review", "pending_codex", "approved",
    "blocked", "failed", "cancelled", "completed",
)
SELF_TEST_MARKER = "TASK_INTAKE_SELF_TEST_PASS"


class IntakeError(ValueError):
    pass


def read_registry(root: Path, *, validate_project: bool = True) -> dict:
    path = root / "orchestrator" / "projects.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IntakeError(f"invalid project registry: {exc}") from exc
    if data.get("version") != 1 or not isinstance(data.get("projects"), list):
        raise IntakeError("invalid project registry structure")
    projects = {}
    for item in data["projects"]:
        if not isinstance(item, dict):
            raise IntakeError("invalid project registry entry")
        project_id = item.get("project_id")
        if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
            raise IntakeError("invalid project_id")
        if project_id in projects:
            raise IntakeError(f"duplicate project_id: {project_id}")
        required = {
            "path", "base_branch", "work_prefixes", "allowed_scope", "agent_context",
            "allow_commits", "allow_push", "allow_merge", "allow_deployment",
        }
        if not required.issubset(item):
            raise IntakeError(f"incomplete project registry entry: {project_id}")
        if any(item[name] is not False for name in (
            "allow_commits", "allow_push", "allow_merge", "allow_deployment",
        )):
            raise IntakeError(f"write/deployment capability enabled for {project_id}")
        raw_path = item["path"]
        if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
            raise IntakeError(f"project path must be absolute: {project_id}")
        project = Path(raw_path)
        try:
            resolved = project.resolve(strict=True)
        except OSError as exc:
            raise IntakeError(f"project unavailable: {project_id}: {exc}") from exc
        if resolved != project:
            raise IntakeError(f"project path may not traverse a symlink: {project_id}")
        if not isinstance(item["work_prefixes"], list) or not item["work_prefixes"]:
            raise IntakeError(f"invalid work prefixes: {project_id}")
        if not isinstance(item["allowed_scope"], list) or not item["allowed_scope"]:
            raise IntakeError(f"invalid allowed scope: {project_id}")
        if item["base_branch"] not in {"main", "develop"}:
            raise IntakeError(f"invalid base branch: {project_id}")
        context = (root / item["agent_context"]).resolve()
        if root.resolve() not in context.parents or not context.is_dir():
            raise IntakeError(f"invalid agent context: {project_id}")
        if validate_project:
            if not (project / ".git").is_dir():
                raise IntakeError(f"project is not a Git repository: {project_id}")
            result = subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{item['base_branch']}"],
                cwd=project, capture_output=True,
            )
            if result.returncode != 0:
                raise IntakeError(f"base branch is unavailable: {project_id}")
        projects[project_id] = item
    if not projects:
        raise IntakeError("project registry is empty")
    return projects


def validate_text(label: str, value: str, limit: int) -> str:
    if "\x00" in value:
        raise IntakeError(f"{label} contains NUL")
    if not value.strip():
        raise IntakeError(f"{label} may not be empty")
    if len(value) > limit:
        raise IntakeError(f"{label} exceeds {limit} characters")
    if "\r" in value or "\n" in value:
        raise IntakeError(f"{label} must be one line")
    return value.strip()


def validate_scope_path(project: Path, raw: str, allowed_patterns: list[str]) -> str:
    if not raw or raw != raw.strip() or "\x00" in raw:
        raise IntakeError("invalid empty, padded, or NUL scope path")
    if len(raw) > SCOPE_ENTRY_LIMIT:
        raise IntakeError(f"scope path exceeds {SCOPE_ENTRY_LIMIT} characters")
    if "\\" in raw or raw.startswith("/") or WINDOWS_DRIVE_RE.match(raw):
        raise IntakeError(f"absolute or backslash scope path rejected: {raw!r}")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise IntakeError(f"path traversal or invalid component rejected: {raw!r}")
    candidate = PurePosixPath(raw)
    allowed = False
    for pattern in allowed_patterns:
        if pattern.endswith("/**"):
            prefix = PurePosixPath(pattern[:-3])
            if candidate == prefix or prefix in candidate.parents:
                allowed = True
        elif raw == pattern:
            allowed = True
    if not allowed:
        raise IntakeError(f"scope path is outside the project allowlist: {raw}")

    current = project
    for index, part in enumerate(parts):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if index != len(parts) - 1:
                raise IntakeError(f"missing parent directory in scope path: {raw}")
            break
        except OSError as exc:
            raise IntakeError(f"unable to inspect scope path {raw}: {exc}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise IntakeError(f"symlink scope path rejected: {raw}")
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise IntakeError(f"non-directory scope parent rejected: {raw}")
        if index == len(parts) - 1 and not (
            stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)
        ):
            raise IntakeError(f"special-file scope path rejected: {raw}")
    return raw


def validate_scope(project: Path, entries: list[str], allowed: list[str]) -> list[str]:
    if not entries:
        raise IntakeError("at least one scope path is required")
    if len(entries) > SCOPE_COUNT_LIMIT:
        raise IntakeError(f"scope exceeds {SCOPE_COUNT_LIMIT} entries")
    result = []
    seen = set()
    for raw in entries:
        value = validate_scope_path(project, raw, allowed)
        if value in seen:
            raise IntakeError(f"duplicate scope path: {value}")
        seen.add(value)
        result.append(value)
    return result


def make_task_id(project_id: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{project_id.upper().replace('-', '_')}_{timestamp}_{secrets.token_hex(3).upper()}"


def render_task(
    project: dict, task_id: str, title: str, instructions: str,
    work_branch: str, scope: list[str],
) -> str:
    values = [
        ("Task-ID", task_id),
        ("Project-Path", project["path"]),
        ("Base-Branch", project["base_branch"]),
        ("Work-Branch", work_branch),
        ("Agent-Context", project["agent_context"]),
        ("Goal", title),
        ("Instructions", instructions),
        ("Scope", "Only the approved Scope-Files listed below"),
        ("Out-of-Scope", "All files outside Scope-Files; commit, push, merge, deployment"),
        ("Pass-Criteria", "Requested change is complete and all required checks pass"),
        ("Required-Checks", "none"),
        ("Required-Commands", "git, python3"),
        ("Required-Environment", "none"),
        ("Owner-Approval-Required", "yes"),
        ("Scope-Files", ", ".join(scope)),
    ]
    return "\n".join(f"{key}: {value}" for key, value in values) + "\n"


def atomic_create(destination: Path, content: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.parent.is_symlink():
        raise IntakeError("pending queue may not be a symlink")
    fd, temporary = tempfile.mkstemp(prefix=".intake-", dir=destination.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_path, destination)
        except FileExistsError as exc:
            raise IntakeError(f"task already exists: {destination.stem}") from exc
        directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def locate_task(root: Path, task_id: str) -> tuple[str, Path]:
    if not TASK_ID_RE.fullmatch(task_id):
        raise IntakeError("invalid task id")
    matches = []
    for queue in QUEUE_NAMES:
        path = root / "queue" / queue / f"{task_id}.md"
        if path.is_file() and not path.is_symlink():
            matches.append((queue, path))
    if not matches:
        raise IntakeError(f"task not found: {task_id}")
    if len(matches) != 1:
        raise IntakeError(f"task exists in multiple queues: {task_id}")
    return matches[0]


def list_tasks(root: Path) -> list[dict]:
    tasks = []
    for queue in QUEUE_NAMES:
        directory = root / "queue" / queue
        if not directory.is_dir() or directory.is_symlink():
            continue
        for path in sorted(directory.glob("*.md")):
            if path.is_symlink() or not TASK_ID_RE.fullmatch(path.stem):
                continue
            tasks.append({"task_id": path.stem, "queue": queue})
    return tasks


def move_cancel(root: Path, task_id: str) -> Path:
    queue, source = locate_task(root, task_id)
    if queue != "pending":
        raise IntakeError("only a pending task may be cancelled")
    target_dir = root / "queue" / "cancelled"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    try:
        os.link(source, target)
    except FileExistsError as exc:
        raise IntakeError(f"cancelled task already exists: {task_id}") from exc
    try:
        source.unlink()
    except OSError:
        target.unlink()
        raise
    return target


def emit(payload, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, sort_keys=True))
    elif isinstance(payload, list):
        for item in payload:
            print(" ".join(f"{key}={value}" for key, value in item.items()))
    elif isinstance(payload, dict):
        print("\n".join(f"{key}={value}" for key, value in payload.items()))
    else:
        print(payload)


def run_self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="ai-prof-intake-test-") as tmp:
        project = Path(tmp) / "project"
        project.mkdir()
        (project / "README.md").write_text("test\n", encoding="utf-8")
        self_scope = validate_scope(project, ["README.md"], ["README.md", "docs/**"])
        if self_scope != ["README.md"]:
            raise RuntimeError("SELF_TEST_SCOPE_FAILED")
        for bad in ("../x", "/etc/passwd", r"docs\x", "tests/../../x"):
            try:
                validate_scope_path(project, bad, ["README.md", "docs/**", "tests/**"])
            except IntakeError:
                pass
            else:
                raise RuntimeError(f"SELF_TEST_UNSAFE_PATH_ACCEPTED: {bad}")
        destination = Path(tmp) / "pending" / "TASK_001.md"
        atomic_create(destination, "Task-ID: TASK_001\n")
        if destination.read_text(encoding="utf-8") != "Task-ID: TASK_001\n":
            raise RuntimeError("SELF_TEST_ATOMIC_CREATE_FAILED")
    print(SELF_TEST_MARKER)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--state-root", default=os.environ.get("AI_PROF_STATE_DIR", str(DEFAULT_STATE_ROOT)))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    commands = parser.add_subparsers(dest="command")
    create = commands.add_parser("create")
    create.add_argument("--project", required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--instructions", required=True)
    create.add_argument("--work-branch", required=True)
    create.add_argument("--scope", action="append", required=True)
    create.add_argument("--dry-run", action="store_true")
    commands.add_parser("list")
    show = commands.add_parser("show")
    show.add_argument("task_id")
    cancel = commands.add_parser("cancel")
    cancel.add_argument("task_id")
    commands.add_parser("projects")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.self_test:
        return run_self_test()
    if not args.command:
        parser.error("a command or --self-test is required")
    root = Path(args.root).resolve()
    runtime = initialize(args.state_root)
    try:
        projects = read_registry(root)
        if args.command == "projects":
            emit([
                {
                    "project_id": item["project_id"],
                    "path": item["path"],
                    "base_branch": item["base_branch"],
                }
                for item in projects.values()
            ], args.json)
        elif args.command == "list":
            emit(list_tasks(runtime), args.json)
        elif args.command == "show":
            queue, path = locate_task(runtime, args.task_id)
            emit(
                {"task_id": args.task_id, "queue": queue, "content": path.read_text(encoding="utf-8")},
                args.json,
            )
        elif args.command == "cancel":
            target = move_cancel(runtime, args.task_id)
            emit({"task_id": args.task_id, "queue": "cancelled", "path": str(target)}, args.json)
        elif args.command == "create":
            if args.project not in projects:
                raise IntakeError(f"unknown project: {args.project}")
            project = projects[args.project]
            title = validate_text("title", args.title, TITLE_LIMIT)
            instructions = validate_text("instructions", args.instructions, INSTRUCTION_LIMIT)
            if not WORK_BRANCH_RE.fullmatch(args.work_branch) or not any(
                args.work_branch.startswith(prefix) for prefix in project["work_prefixes"]
            ):
                raise IntakeError("work branch is outside the project prefix allowlist")
            scope = validate_scope(Path(project["path"]), args.scope, project["allowed_scope"])
            task_id = make_task_id(args.project)
            content = render_task(
                project, task_id, title, instructions, args.work_branch, scope,
            )
            result = {
                "task_id": task_id,
                "queue": "dry-run" if args.dry_run else "pending",
                "content": content,
            }
            if not args.dry_run:
                destination = runtime / "queue" / "pending" / f"{task_id}.md"
                atomic_create(destination, content)
                result["path"] = str(destination)
            emit(result, args.json)
        return 0
    except (IntakeError, OSError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, sort_keys=True))
        else:
            print(f"INTAKE_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
