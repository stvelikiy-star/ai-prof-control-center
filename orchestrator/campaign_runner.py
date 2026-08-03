#!/usr/bin/env python3
"""Owner-approved, local-only integration campaign controller."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import submit_task
from project_registry import (
    allowed_base_branches, load_projects, local_integration_branches,
)
from runtime_paths import DEFAULT_STATE_ROOT, initialize


CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,79}$")
MAX_LOG_BYTES = 131072
FINAL_STATES = {"completed", "deadline_reached", "blocked"}
FORBIDDEN_CAMPAIGN_SCOPE_PARTS = {
    ".env", "credentials", "secrets",
}
LOCAL_MIGRATION_SCOPE_PREFIX = ("supabase", "migrations")
QUEUE_NAMES = (
    "pending", "active", "review", "pending_codex", "approved",
    "blocked", "failed", "cancelled", "completed",
)


class CampaignError(RuntimeError):
    pass


def campaign_path_is_forbidden(raw_path: str) -> bool:
    """Allow only reviewed local SQL artifacts under supabase/migrations.

    This does not execute, apply, push, deploy, or connect to a database.
    Every other Supabase path and every credential-like path stays blocked.
    """
    path = PurePosixPath(raw_path)
    parts = tuple(part.casefold() for part in path.parts)

    if path.is_absolute() or ".." in path.parts:
        return True

    if any(part in FORBIDDEN_CAMPAIGN_SCOPE_PARTS for part in parts):
        return True

    contains_database_namespace = (
        "supabase" in parts or "migrations" in parts
    )
    if contains_database_namespace:
        return parts[:2] != LOCAL_MIGRATION_SCOPE_PREFIX

    return False


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def append_log(state_root: Path, campaign_id: str, message: str) -> None:
    path = state_root / "logs/campaigns" / f"{campaign_id}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = message  # task instructions are never passed to this logger
    safe = re.sub(r"(?i)(token|password|secret|api[_-]?key)\s*[:=]\s*\S+", r"\1=[REDACTED]", safe)
    line = f"{iso_utc(now_utc())} {safe.strip()}\n"
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    bounded = (previous + line).encode("utf-8")[-MAX_LOG_BYTES:].decode("utf-8", "ignore")
    path.write_text(bounded, encoding="utf-8")


def acquire_lock(state_root: Path, campaign_id: str):
    path = state_root / "run" / f"campaign-{campaign_id}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise
    return handle


def state_path(state_root: Path, campaign_id: str) -> Path:
    return state_root / "campaigns" / f"{campaign_id}.json"


def load_state(state_root: Path, campaign_id: str) -> dict:
    try:
        state = json.loads(state_path(state_root, campaign_id).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CampaignError(f"campaign not found or invalid: {campaign_id}") from exc
    if state.get("campaign_id") != campaign_id:
        raise CampaignError("campaign state identity mismatch")
    return state


def load_plan(path: Path) -> dict:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CampaignError(f"invalid plan file: {exc}") from exc
    if set(plan) != {"version", "tasks"} or plan.get("version") != 1:
        raise CampaignError("plan schema must contain exactly version=1 and tasks")
    tasks = plan["tasks"]
    if not isinstance(tasks, list) or not tasks:
        raise CampaignError("plan tasks must be a non-empty list")
    keys = set()
    for task in tasks:
        if not isinstance(task, dict) or set(task) != {"key", "title", "instructions", "scope"}:
            raise CampaignError("each plan task must contain exactly key, title, instructions, scope")
        if not isinstance(task["key"], str) or not task["key"] or task["key"] in keys:
            raise CampaignError("plan task keys must be unique non-empty strings")
        keys.add(task["key"])
        if not isinstance(task["scope"], list) or not all(isinstance(v, str) for v in task["scope"]):
            raise CampaignError("plan task scope must be a string array")
        if not isinstance(task["title"], str) or not isinstance(task["instructions"], str):
            raise CampaignError("plan title and instructions must be strings")
        submit_task.validate_text("title", task["title"], submit_task.TITLE_LIMIT)
        submit_task.validate_text("instructions", task["instructions"], submit_task.INSTRUCTION_LIMIT)
        for raw_scope in task["scope"]:
            if campaign_path_is_forbidden(raw_scope):
                raise CampaignError("campaign scope contains a forbidden path")
    return plan


def task_id_for(state: dict, index: int) -> str:
    slug = re.sub(r"[^A-Z0-9_-]", "_", state["campaign_id"].upper())
    return f"CAMPAIGN_{slug}_{index + 1:03d}"


def work_branch_for(state: dict, index: int) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]", "-", state["campaign_id"]).strip("-")
    return f"feature/campaign-{slug}-{index + 1:03d}"


def create_next_task(root: Path, state_root: Path, state: dict, project: dict, now: datetime) -> bool:
    index = state["current_index"]
    if index >= len(state["plan"]["tasks"]):
        state["state"] = "completed"
        state["current_task_id"] = None
        return False
    if now >= parse_time(state["deadline"]):
        state["state"] = "deadline_reached"
        state["current_task_id"] = None
        return False
    step = state["plan"]["tasks"][index]
    scope = submit_task.validate_scope(Path(project["path"]), step["scope"], project["allowed_scope"])
    task_id = task_id_for(state, index)
    branch = work_branch_for(state, index)
    content = submit_task.render_task(
        project, task_id, step["title"], step["instructions"], branch, scope,
        base_branch=state["integration_branch"],
        metadata=[
            ("Campaign-ID", state["campaign_id"]),
            ("Integration-Branch", state["integration_branch"]),
            ("Local-Auto-Merge-Approved", "yes"),
            ("Owner-Approval-Token", state["owner_approval_token"]),
        ],
    )
    destination = state_root / "queue/pending" / f"{task_id}.md"
    if destination.exists():
        if destination.read_text(encoding="utf-8") != content:
            raise CampaignError("existing campaign task content conflicts")
    else:
        if any((state_root / "queue" / queue / destination.name).exists() for queue in QUEUE_NAMES):
            raise CampaignError("campaign task already exists outside pending")
        submit_task.atomic_create(destination, content)
    state["current_task_id"] = task_id
    state["current_step"] = step["key"]
    append_log(state_root, state["campaign_id"], f"created step={step['key']} task={task_id}")
    return True


def start_campaign(
    root: Path, state_root: Path, campaign_id: str, project_id: str,
    integration_branch: str, duration_hours: float, plan_file: Path,
    owner_token: str, *, now: datetime | None = None,
) -> dict:
    if not CAMPAIGN_ID_RE.fullmatch(campaign_id):
        raise CampaignError("invalid campaign ID")
    if not owner_token or "\n" in owner_token or "\r" in owner_token:
        raise CampaignError("owner approval token is required")
    if duration_hours <= 0:
        raise CampaignError("duration-hours must be positive")
    plan = load_plan(plan_file)
    projects = load_projects(root)
    project = projects.get(project_id)
    if project is None:
        raise CampaignError("unknown project")
    if not project.get("allow_local_campaign_merge"):
        raise CampaignError("project does not allow local campaign merge")
    if any(project.get(flag) is not False for flag in ("allow_merge", "allow_push", "allow_deployment")):
        raise CampaignError("project global merge/push/deployment policy is unsafe")
    if integration_branch not in local_integration_branches(project):
        raise CampaignError("integration branch is outside local_integration_branches")
    if integration_branch not in allowed_base_branches(project):
        raise CampaignError("integration branch is not an allowed base branch")
    if subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{integration_branch}"],
        cwd=project["path"], capture_output=True,
    ).returncode:
        raise CampaignError("integration branch does not exist locally")
    immutable = {
        "campaign_id": campaign_id, "project_id": project_id,
        "integration_branch": integration_branch, "duration_hours": duration_hours,
        "plan": plan, "owner_approval_token": owner_token,
    }
    path = state_path(state_root, campaign_id)
    if path.exists():
        state = load_state(state_root, campaign_id)
        if any(state.get(key) != value for key, value in immutable.items()):
            raise CampaignError("conflicting campaign configuration")
        if state.get("state") in FINAL_STATES:
            return state
        return tick_campaign(root, state_root, campaign_id, now=now, lock_held=True)
    started = now or now_utc()
    state = {
        **immutable, "state": "active", "started_at": iso_utc(started),
        "deadline": iso_utc(started + timedelta(hours=duration_hours)),
        "current_index": 0, "completed_steps": 0, "current_step": None,
        "current_task_id": None, "evidence": [], "no_push": True, "no_deploy": True,
    }
    create_next_task(root, state_root, state, project, started)
    atomic_json(path, state)
    append_log(state_root, campaign_id, "campaign started no_push=true no_deploy=true")
    return state


def task_location(state_root: Path, task_id: str) -> tuple[str | None, Path | None]:
    found = [(queue, state_root / "queue" / queue / f"{task_id}.md") for queue in QUEUE_NAMES]
    found = [(queue, path) for queue, path in found if path.is_file()]
    if len(found) > 1:
        raise CampaignError("campaign task exists in multiple queues")
    return found[0] if found else (None, None)


def field(text: str, name: str) -> str:
    match = re.search(rf"(?mi)^{re.escape(name)}:\s*([^\r\n]+)$", text)
    return match.group(1).strip() if match else ""


def audit_passed(state_root: Path, task_id: str) -> bool:
    logs = sorted(
        (state_root / "logs/orchestrator").glob(f"{task_id}-01C-*.log"),
        key=lambda path: path.stat().st_mtime,
    )
    return bool(logs and "STAGE_01C_AUDIT_PASS" in logs[-1].read_text(encoding="utf-8"))


def git(project: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    if args and args[0] == "push":
        raise CampaignError("git push is forbidden")
    return subprocess.run(
        ["git", *args], cwd=project, text=True, capture_output=True, check=check,
    )


def changed_paths(project: Path) -> list[str]:
    result = git(project, "status", "--porcelain", "-z")
    records = result.stdout.split("\0")
    paths = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4:
            raise CampaignError("malformed git status")
        status, path = record[:2], record[3:]
        if status[0] in "RC" or status[1] in "RC":
            if index >= len(records) or not records[index]:
                raise CampaignError("malformed rename status")
            path = records[index]
            index += 1
        paths.append(path)
    return paths


def path_in_scope(path: str, scopes: list[str]) -> bool:
    candidate = PurePosixPath(path)
    return any(
        candidate == PurePosixPath(scope) or PurePosixPath(scope) in candidate.parents
        for scope in scopes
    )


def append_task_evidence(path: Path, lines: list[tuple[str, str]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for key, value in lines:
            handle.write(f"{key}: {value}\n")
        handle.flush()
        os.fsync(handle.fileno())


def complete_approved(root: Path, state_root: Path, state: dict, task_path: Path, project: dict) -> None:
    text = task_path.read_text(encoding="utf-8")
    expected = {
        "Campaign-ID": state["campaign_id"],
        "Integration-Branch": state["integration_branch"],
        "Local-Auto-Merge-Approved": "yes",
        "Owner-Approval-Token": state["owner_approval_token"],
    }
    if any(field(text, key) != value for key, value in expected.items()):
        raise CampaignError("campaign approval metadata mismatch")
    task_id = state["current_task_id"]
    if not audit_passed(state_root, task_id):
        raise CampaignError("latest Codex PASS evidence is required")
    project_path = Path(project["path"]).resolve()
    work_branch = field(text, "Work-Branch")
    if git(project_path, "branch", "--show-current").stdout.strip() != work_branch:
        raise CampaignError("repository is not on the exact work branch")
    scopes = [item.strip() for item in field(text, "Scope-Files").split(",") if item.strip()]
    changes = changed_paths(project_path)
    outside = [path for path in changes if not path_in_scope(path, scopes)]
    if outside:
        raise CampaignError("change outside Scope-Files")
    if any(campaign_path_is_forbidden(path) for path in changes):
        raise CampaignError("campaign produced a forbidden path change")
    integration = state["integration_branch"]
    if integration in {"main", "develop"} or integration not in local_integration_branches(project):
        raise CampaignError("unsafe integration branch")
    if git(project_path, "show-ref", "--verify", "--quiet", f"refs/heads/{integration}", check=False).returncode:
        raise CampaignError("integration branch does not exist locally")

    feature_commit = "none"
    integration_commit = git(project_path, "rev-parse", integration).stdout.strip()
    if changes:
        git(project_path, "add", "--", *scopes)
        git(project_path, "commit", "-m", f"campaign({state['campaign_id']}): {state['current_step']}")
        feature_commit = git(project_path, "rev-parse", "HEAD").stdout.strip()
        git(project_path, "checkout", integration)
        git(project_path, "merge", "--no-ff", work_branch, "-m", f"Merge {work_branch} for campaign {state['campaign_id']}")
        integration_commit = git(project_path, "rev-parse", "HEAD").stdout.strip()
    else:
        git(project_path, "checkout", integration)

    completed_at = iso_utc(now_utc())
    append_task_evidence(task_path, [
        ("Campaign-Feature-Commit", feature_commit),
        ("Campaign-Integration-Commit", integration_commit),
        ("Campaign-Completion-Evidence", f"local-only; no-push; no-deploy; completed={completed_at}"),
    ])
    destination = state_root / "queue/completed" / task_path.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.link(task_path, destination)
    task_path.unlink()
    state["evidence"].append({
        "step": state["current_step"], "task_id": task_id,
        "feature_commit": feature_commit, "integration_commit": integration_commit,
        "completed_at": completed_at,
    })
    state["completed_steps"] += 1
    state["current_index"] += 1
    state["current_task_id"] = None
    state["current_step"] = None
    append_log(state_root, state["campaign_id"], f"completed task={task_id} feature={feature_commit} integration={integration_commit}")


def tick_campaign(
    root: Path, state_root: Path, campaign_id: str, *, now: datetime | None = None,
    lock_held: bool = False,
) -> dict:
    lock = None
    if not lock_held:
        try:
            lock = acquire_lock(state_root, campaign_id)
        except BlockingIOError as exc:
            raise CampaignError("campaign tick already running") from exc
    try:
        state = load_state(state_root, campaign_id)
        if state["state"] in FINAL_STATES:
            return state
        project = load_projects(root).get(state["project_id"])
        if project is None:
            raise CampaignError("campaign project is no longer registered")
        current = now or now_utc()
        if state.get("current_task_id"):
            queue, path = task_location(state_root, state["current_task_id"])
            if queue == "approved" and path is not None:
                complete_approved(root, state_root, state, path, project)
                create_next_task(root, state_root, state, project, current)
        else:
            create_next_task(root, state_root, state, project, current)
        atomic_json(state_path(state_root, campaign_id), state)
        return state
    finally:
        if lock is not None:
            lock.close()


def tick_all(root: Path, state_root: Path) -> int:
    campaigns = state_root / "campaigns"
    if not campaigns.is_dir():
        return 0
    result = 0
    for path in sorted(campaigns.glob("*.json")):
        try:
            tick_campaign(root, state_root, path.stem)
        except CampaignError as exc:
            append_log(state_root, path.stem, f"tick blocked: {exc}")
            result = 1
    return result


def public_status(state: dict) -> dict:
    return {
        "campaign_id": state["campaign_id"], "state": state["state"],
        "completed_steps": state["completed_steps"],
        "total_steps": len(state["plan"]["tasks"]),
        "current_step": state.get("current_step"),
        "deadline": state["deadline"],
        "integration_branch": state["integration_branch"],
        "no_push": True, "no_deploy": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--state-root", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("--campaign-id", required=True)
    start.add_argument("--project", required=True)
    start.add_argument("--integration-branch", required=True)
    start.add_argument("--duration-hours", required=True, type=float)
    start.add_argument("--plan-file", required=True)
    start.add_argument("--owner-approval-token", required=True)
    status = commands.add_parser("status")
    status.add_argument("--campaign-id", required=True)
    status.add_argument("--json", action="store_true")
    tick = commands.add_parser("tick")
    tick.add_argument("--campaign-id", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    state_root = initialize(args.state_root)
    try:
        if args.command == "start":
            try:
                lock = acquire_lock(state_root, args.campaign_id)
            except BlockingIOError as exc:
                raise CampaignError("campaign start already running") from exc
            with lock:
                state = start_campaign(
                    root, state_root, args.campaign_id, args.project,
                    args.integration_branch, args.duration_hours, Path(args.plan_file),
                    args.owner_approval_token,
                )
        elif args.command == "tick":
            state = tick_campaign(root, state_root, args.campaign_id)
        else:
            state = load_state(state_root, args.campaign_id)
        output = public_status(state)
        print(json.dumps(output, sort_keys=True) if getattr(args, "json", False) else json.dumps(output, indent=2, sort_keys=True))
        return 0
    except (CampaignError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"CAMPAIGN_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
