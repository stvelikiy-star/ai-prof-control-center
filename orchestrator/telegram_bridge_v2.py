#!/usr/bin/env python3
"""AI PROF Telegram Control Plane V2.

Extends the existing secure Telegram bridge with owner-only mobile diagnostics.
It deliberately does not expose arbitrary shell execution, secret values,
force-push, destructive git operations, production migration, or deployment.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import telegram_bridge as legacy

ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = legacy.STATE_DIR
PROJECTS_PATH = legacy.PROJECTS_PATH
TASK_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{5,159}$")
MAX_LINES = 70
MAX_LOG_CHARS = 9000

V2_HELP = """AI PROF Telegram Control Plane V2

Core:
/ai help
/ai status
/ai health
/ai queue [project]

Tasks:
/ai task <TASK_ID>            — task details and exact terminal reason
/ai logs <TASK_ID>            — recent redacted task logs
/ai blockers <project>        — current project blockers
/ai task <project> | <title> | <instructions>
/ai task <text>               — project: ak-bermet

Git diagnostics (read-only):
/ai git <project> status

Release:
/ai release <project> prepare

Safety:
No arbitrary shell, no secret values, no reset --hard, no clean -fd,
no force-push, no migration and no deploy from Telegram.
"""


def _projects() -> dict[str, dict]:
    return legacy.load_projects(PROJECTS_PATH)


def _project(project_id: str) -> dict:
    projects = _projects()
    item = projects.get(project_id)
    if not isinstance(item, dict):
        raise legacy.BridgeError(f"unknown project: {project_id}")
    return item


def _sanitize(value: object, limit: int = 1200) -> str:
    text = legacy.redact(value)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+", " ", text)
    return text[:limit]


def _run_readonly(argv: list[str], *, cwd: Path | None = None, timeout: int = 15) -> tuple[int, str]:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, _sanitize(exc)
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode, _sanitize(output, 12000).strip()


def _task_locations(task_id: str):
    return legacy._queue_locations(STATE_ROOT).get(task_id, [])


def task_details(task_id: str) -> str:
    if not TASK_ID_RE.fullmatch(task_id):
        return "Invalid Task-ID."
    locations = _task_locations(task_id)
    if not locations:
        return f"Task not found: {html.escape(task_id)}"
    queues = sorted({q for q, _p, _f in locations})
    queue, path, fields = locations[0]
    lines = [
        "AI PROF task",
        f"Task-ID: {html.escape(task_id)}",
        f"Queue: {html.escape(', '.join(queues))}",
        f"State: {html.escape(legacy.authoritative_task_state(STATE_ROOT, task_id))}",
        f"Goal: {html.escape(_sanitize(fields.get('Goal', 'untitled'), 240))}",
    ]
    project_path = fields.get("Project-Path", "")
    projects = _projects()
    project_id = next((pid for pid, cfg in projects.items() if cfg.get("path") == project_path), "unknown")
    lines.append(f"Project: {html.escape(project_id)}")
    reason = legacy._terminal_reason(queue, fields)
    if reason:
        lines.append(f"Reason: {html.escape(reason)}")
    if len(locations) > 1:
        lines.append("WARNING: QUEUE INCONSISTENCY — same task exists in multiple queues")
    lines.append(f"File: {html.escape(path.name)}")
    return legacy._telegram_truncate("\n".join(lines))


def _candidate_logs(task_id: str) -> list[Path]:
    candidates: list[Path] = []
    roots = [STATE_ROOT / "logs", ROOT / "logs"]
    for root in roots:
        if not root.exists():
            continue
        try:
            for path in root.rglob("*.log"):
                if task_id in path.name:
                    candidates.append(path)
        except OSError:
            continue
    return sorted(candidates, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)


def task_logs(task_id: str) -> str:
    if not TASK_ID_RE.fullmatch(task_id):
        return "Invalid Task-ID."
    paths = _candidate_logs(task_id)
    if not paths:
        locations = _task_locations(task_id)
        reason = ""
        if locations:
            queue, _path, fields = locations[0]
            reason = legacy._terminal_reason(queue, fields)
        suffix = f"\nTerminal reason: {reason}" if reason else ""
        return f"No task log file found for {task_id}.{suffix}"
    path = paths[0]
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-MAX_LINES:]
    except OSError as exc:
        return f"Unable to read task log: {_sanitize(exc)}"
    body = legacy.redact("\n".join(lines))[-MAX_LOG_CHARS:]
    return legacy._telegram_truncate(f"AI PROF logs\nTask-ID: {task_id}\nLog: {path.name}\n---\n{body}")


def git_status(project_id: str) -> str:
    try:
        cfg = _project(project_id)
    except legacy.BridgeError as exc:
        return str(exc)
    project = Path(cfg["path"])
    if not (project / ".git").exists():
        return f"Project repository unavailable: {project_id}"

    _rc, branch = _run_readonly(["git", "branch", "--show-current"], cwd=project)
    _rc, head = _run_readonly(["git", "rev-parse", "--short=12", "HEAD"], cwd=project)
    _rc, status = _run_readonly(["git", "status", "--short", "--branch"], cwd=project)
    _rc, upstream = _run_readonly(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=project)
    divergence = "upstream unavailable"
    if upstream and "fatal:" not in upstream.lower():
        _rc, counts = _run_readonly(["git", "rev-list", "--left-right", "--count", f"HEAD...{upstream.strip()}"], cwd=project)
        if counts:
            divergence = counts.replace("\t", " / ") + " (ahead / behind)"
    dirty_lines = [line for line in status.splitlines() if line and not line.startswith("##")]
    release_branch = str(cfg.get("release", {}).get("branch") or cfg.get("base_branch") or "unknown")
    return legacy._telegram_truncate("\n".join([
        f"AI PROF git status — {project_id}",
        f"Current branch: {branch or 'unknown'}",
        f"Release branch: {release_branch}",
        f"HEAD: {head or 'unknown'}",
        f"Upstream: {upstream or 'none'}",
        f"Divergence: {divergence}",
        f"Worktree: {'DIRTY' if dirty_lines else 'clean'}",
        "Changed files:",
        *(dirty_lines[:40] if dirty_lines else ["- none"]),
        *([f"... +{len(dirty_lines)-40} more"] if len(dirty_lines) > 40 else []),
    ]))


def queue_message(project_filter: str = "") -> str:
    projects = _projects()
    path_to_project = {str(cfg.get("path")): pid for pid, cfg in projects.items()}
    rows = []
    for task_id, locations in legacy._queue_locations(STATE_ROOT).items():
        queue, _path, fields = locations[0]
        project_id = path_to_project.get(fields.get("Project-Path", ""), "unknown")
        if project_filter and project_id != project_filter:
            continue
        rows.append((legacy._task_time(task_id, _path), task_id, project_id, queue, fields))
    rows.sort(reverse=True)
    counts: dict[str, int] = {}
    for _t, _id, _p, q, _f in rows:
        counts[q] = counts.get(q, 0) + 1
    lines = ["AI PROF queue"]
    if project_filter:
        lines.append(f"Project: {project_filter}")
    lines.append("Counts: " + (", ".join(f"{q}={n}" for q, n in sorted(counts.items())) or "empty"))
    lines.append("Recent:")
    for _t, task_id, project_id, queue, fields in rows[:15]:
        reason = legacy._terminal_reason(queue, fields)
        tail = f" | {reason}" if reason else ""
        lines.append(f"- {task_id} | {project_id} | {queue}{tail}")
    if not rows:
        lines.append("- none")
    return legacy._telegram_truncate("\n".join(lines))


def health_message() -> str:
    projects = _projects()
    total, used, free = shutil.disk_usage("/")
    mem = "unknown"
    swap = "unknown"
    try:
        data = {}
        for raw in Path("/proc/meminfo").read_text().splitlines():
            key, value = raw.split(":", 1)
            data[key] = int(value.strip().split()[0]) * 1024
        mem_total = data.get("MemTotal", 0)
        mem_available = data.get("MemAvailable", 0)
        swap_total = data.get("SwapTotal", 0)
        swap_free = data.get("SwapFree", 0)
        if mem_total:
            mem = f"{(mem_total-mem_available)/1024**3:.1f}/{mem_total/1024**3:.1f} GiB used"
        if swap_total:
            swap = f"{(swap_total-swap_free)/1024**3:.1f}/{swap_total/1024**3:.1f} GiB used"
        else:
            swap = "disabled"
    except (OSError, ValueError):
        pass
    command_checks = []
    for name in ("git", "python3", "node", "npm", "npx", "docker", "supabase"):
        command_checks.append(f"{name}={'OK' if shutil.which(name) else 'MISSING'}")
    project_health = []
    for pid, cfg in projects.items():
        path = Path(str(cfg.get("path", "")))
        project_health.append(f"{pid}={'OK' if (path / '.git').exists() else 'UNAVAILABLE'}")
    return legacy._telegram_truncate("\n".join([
        "AI PROF health",
        "Bridge: healthy (responding)",
        f"Control Center: {legacy.control_center_health(STATE_ROOT)}",
        f"Disk /: {used/1024**3:.1f}/{total/1024**3:.1f} GiB used ({free/1024**3:.1f} GiB free)",
        f"RAM: {mem}",
        f"Swap: {swap}",
        "Commands: " + ", ".join(command_checks),
        "Projects: " + ", ".join(project_health),
    ]))


def blockers_message(project_id: str) -> str:
    try:
        cfg = _project(project_id)
    except legacy.BridgeError as exc:
        return str(exc)
    project_path = str(cfg.get("path", ""))
    projects = _projects()
    recent = legacy.recent_tasks(STATE_ROOT, projects, limit=50)
    task_blockers = []
    for task in recent:
        if task.get("project") != project_id:
            continue
        if task.get("state") in {"blocked", "failed"} or str(task.get("state", "")).startswith("QUEUE INCONSISTENCY"):
            result = task.get("result") or "no terminal reason recorded"
            task_blockers.append(f"- {task['id']} | {task['state']} | {result}")
    git_info = git_status(project_id)
    release_branch = str(cfg.get("release", {}).get("branch") or cfg.get("base_branch") or "unknown")
    lines = [
        f"AI PROF blockers — {project_id}",
        f"Project path: {project_path}",
        f"Expected release branch: {release_branch}",
        "Recent blocked/failed tasks:",
        *(task_blockers[:12] if task_blockers else ["- none"]),
        "",
        git_info,
    ]
    return legacy._telegram_truncate("\n".join(lines))


def _send(client: legacy.TelegramClient, text: str) -> None:
    client.send(legacy._telegram_truncate(text))


def extended_handle_update(update: object, config: legacy.Config, client: legacy.TelegramClient) -> None:
    if not isinstance(update, dict):
        return
    message = update.get("message")
    if not legacy.authorized(message, config):
        return
    text = message.get("text") if isinstance(message, dict) else None
    if not isinstance(text, str):
        return legacy.handle_update_original(update, config, client)
    normalized = text.strip()
    prefix, sep, remainder = normalized.partition(" ")
    if prefix.split("@", 1)[0] != "/ai":
        return legacy.handle_update_original(update, config, client)
    remainder = remainder.strip() if sep else ""

    if remainder == "help":
        return _send(client, V2_HELP)
    if remainder == "health":
        return _send(client, health_message())
    if remainder == "queue" or remainder.startswith("queue "):
        project_id = remainder[5:].strip() if remainder.startswith("queue ") else ""
        if project_id and project_id not in _projects():
            return _send(client, f"Unknown project: {project_id}")
        return _send(client, queue_message(project_id))
    if remainder.startswith("logs "):
        return _send(client, task_logs(remainder[5:].strip()))
    if remainder.startswith("blockers "):
        return _send(client, blockers_message(remainder[9:].strip()))
    match = re.fullmatch(r"git\s+([a-z0-9-]+)\s+status", remainder)
    if match:
        return _send(client, git_status(match.group(1)))
    if remainder.startswith("task "):
        candidate = remainder[5:].strip()
        if TASK_ID_RE.fullmatch(candidate) and "_20" in candidate:
            return _send(client, task_details(candidate))

    return legacy.handle_update_original(update, config, client)


def main() -> int:
    legacy.handle_update_original = legacy.handle_update
    legacy.handle_update = extended_handle_update
    return legacy.main()


if __name__ == "__main__":
    raise SystemExit(main())
