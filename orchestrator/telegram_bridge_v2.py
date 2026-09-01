#!/usr/bin/env python3
"""AI PROF Telegram Control Plane V2.

Extends the existing secure Telegram bridge with owner-only mobile diagnostics.
Repair Team views are read-only: they expose incidents, recovery readiness and
pipeline state but never execute repair, operations, deployment or restart.
The bridge deliberately does not expose arbitrary shell execution, secret
values, force-push, destructive git operations, production migration, or
deployment.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import project_recovery_gate as recovery_gate
import telegram_bridge as legacy
from incident_engine import summary as incident_summary

ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = legacy.STATE_DIR
PROJECTS_PATH = legacy.PROJECTS_PATH
TASK_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{5,159}$")
INCIDENT_ID_RE = re.compile(r"^INC-[A-Z0-9]{1,16}-[A-F0-9]{10}$")
MAX_LINES = 70
MAX_LOG_CHARS = 9000
MAX_STATE_RECORD_BYTES = 256 * 1024
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

V2_HELP = """AI PROF Telegram Control Plane V2

Core:
/ai help
/ai status
/ai health
/ai queue [project]

Repair Team (read-only):
/ai incidents [project]       — open incidents
/ai critical                  — critical open incidents
/ai recovery [project]        — rollback/recovery readiness
/ai repairs [project]         — repair pipeline state

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
Repair Team status commands are read-only. No arbitrary shell, no secret values,
no reset --hard, no clean -fd, no force-push, no migration, no restart and no
deploy from Repair Team Telegram commands.
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


def incidents_message(project_filter: str = "", severity_filter: str = "") -> str:
    """Render bounded redacted open-incident state without changing it."""
    try:
        state = incident_summary(STATE_ROOT)
    except Exception as exc:
        return f"Repair incidents unavailable: {_sanitize(exc, 300)}"
    rows = []
    for item in state.get("open_incidents", []):
        if not isinstance(item, dict):
            continue
        if project_filter and item.get("project_id") != project_filter:
            continue
        severity = str(item.get("severity") or "unknown").lower()
        if severity_filter and severity != severity_filter:
            continue
        rows.append(item)
    rows.sort(
        key=lambda item: (
            SEVERITY_ORDER.get(str(item.get("severity", "")).lower(), 9),
            str(item.get("updated_at") or ""),
            str(item.get("incident_id") or ""),
        )
    )
    title = "AI PROF critical incidents" if severity_filter == "critical" else "AI PROF incidents"
    lines = [title]
    if project_filter:
        lines.append(f"Project: {project_filter}")
    lines.append(f"Open: {len(rows)}")
    for item in rows[:15]:
        incident_id = _sanitize(item.get("incident_id", "unknown"), 80)
        project_id = _sanitize(item.get("project_id", "unknown"), 80)
        probe_id = _sanitize(item.get("probe_id", "unknown"), 100)
        severity = _sanitize(item.get("severity", "unknown"), 20)
        failures = item.get("failure_count", 0)
        detail = _sanitize(item.get("last_detail", "no detail"), 180)
        lines.append(
            f"- {incident_id} | {project_id} | {probe_id} | {severity} | failures={failures}"
        )
        lines.append(f"  {detail}")
    if not rows:
        lines.append("- none")
    if len(rows) > 15:
        lines.append(f"... +{len(rows) - 15} more")
    return legacy._telegram_truncate("\n".join(lines))


def critical_message() -> str:
    return incidents_message(severity_filter="critical")


def recovery_message(project_filter: str = "") -> str:
    """Show authority-neutral recovery readiness without exposing evidence paths."""
    try:
        contracts = recovery_gate.load_recovery_contracts(ROOT)
    except Exception as exc:
        return f"Repair recovery state unavailable: {_sanitize(exc, 300)}"
    project_ids = [project_filter] if project_filter else sorted(contracts)
    lines = ["AI PROF recovery readiness"]
    for project_id in project_ids:
        item = contracts.get(project_id)
        if item is None:
            continue
        try:
            ready, blockers = recovery_gate.recovery_readiness(ROOT, project_id)
        except Exception as exc:
            lines.append(f"- {project_id} | unavailable | {_sanitize(exc, 180)}")
            continue
        mode = _sanitize(item.get("recovery_mode", "unknown"), 40)
        level = _sanitize(item.get("verification_level", "unknown"), 40)
        lines.append(
            f"- {project_id} | mode={mode} | verification={level} | production={'READY' if ready else 'BLOCKED'}"
        )
        if blockers:
            lines.append("  blockers: " + ", ".join(_sanitize(value, 80) for value in blockers))
    if len(lines) == 1:
        lines.append("- none")
    return legacy._telegram_truncate("\n".join(lines))


def _read_state_record(path: Path) -> dict | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_STATE_RECORD_BYTES:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _incident_project_index() -> dict[str, str]:
    result: dict[str, str] = {}
    for bucket in ("open", "resolved"):
        directory = STATE_ROOT / "incidents" / bucket
        try:
            paths = list(directory.glob("*.json"))
        except OSError:
            paths = []
        for path in paths:
            payload = _read_state_record(path)
            if not payload:
                continue
            incident_id = payload.get("incident_id")
            project_id = payload.get("project_id")
            if isinstance(incident_id, str) and INCIDENT_ID_RE.fullmatch(incident_id) and isinstance(project_id, str):
                result[incident_id] = project_id
    return result


def _state_bucket_count(relative: str, project_filter: str, incident_projects: dict[str, str]) -> int:
    directory = STATE_ROOT / relative
    try:
        paths = list(directory.glob("*.json"))
    except OSError:
        return 0
    if not project_filter:
        return sum(1 for path in paths if path.is_file() and not path.is_symlink())
    count = 0
    for path in paths:
        payload = _read_state_record(path)
        if payload is None:
            continue
        project_id = payload.get("project_id")
        incident_id = payload.get("incident_id")
        if not isinstance(project_id, str) and isinstance(incident_id, str):
            project_id = incident_projects.get(incident_id)
        if project_id == project_filter:
            count += 1
    return count


def repairs_message(project_filter: str = "") -> str:
    """Render read-only Repair Team pipeline counters from existing state stores."""
    incident_projects = _incident_project_index()
    buckets = (
        ("diagnosis.pending", "diagnosis/pending"),
        ("diagnosis.results", "diagnosis/results"),
        ("diagnosis.blocked", "diagnosis/blocked"),
        ("code.tasks", "repair_bridge/tasks"),
        ("code.blocked", "repair_bridge/blocked"),
        ("operations.tasks", "operations_bridge/tasks"),
        ("operations.blocked", "operations_bridge/blocked"),
    )
    lines = ["AI PROF Repair Team pipeline"]
    if project_filter:
        lines.append(f"Project: {project_filter}")
    lines.append("Artifacts:")
    for label, relative in buckets:
        lines.append(f"- {label}={_state_bucket_count(relative, project_filter, incident_projects)}")

    projects = _projects()
    path_to_project = {str(cfg.get("path")): pid for pid, cfg in projects.items()}
    queue_counts: dict[str, int] = {}
    for _task_id, locations in legacy._queue_locations(STATE_ROOT).items():
        if not locations:
            continue
        queue, _path, fields = locations[0]
        project_id = path_to_project.get(fields.get("Project-Path", ""), "unknown")
        if project_filter and project_id != project_filter:
            continue
        queue_counts[queue] = queue_counts.get(queue, 0) + 1
    lines.append(
        "Queues: " + (", ".join(f"{key}={value}" for key, value in sorted(queue_counts.items())) or "empty")
    )
    try:
        open_incidents = incident_summary(STATE_ROOT).get("open_incidents", [])
        open_count = sum(
            1 for item in open_incidents
            if isinstance(item, dict) and (not project_filter or item.get("project_id") == project_filter)
        )
    except Exception:
        open_count = -1
    lines.append(f"Open incidents: {open_count if open_count >= 0 else 'unavailable'}")
    lines.append("Privileged execution authority: governed by current recovery + binding gates")
    return legacy._telegram_truncate("\n".join(lines))


def _send(client: legacy.TelegramClient, text: str) -> None:
    client.send(legacy._telegram_truncate(text))


def _optional_project(remainder: str, command: str) -> str | None:
    if remainder == command:
        return ""
    prefix = command + " "
    if not remainder.startswith(prefix):
        return None
    project_id = remainder[len(prefix):].strip()
    return project_id if project_id else None


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
    if remainder == "critical":
        return _send(client, critical_message())
    for command, renderer in (
        ("incidents", incidents_message),
        ("recovery", recovery_message),
        ("repairs", repairs_message),
    ):
        project_id = _optional_project(remainder, command)
        if project_id is not None:
            if project_id and project_id not in _projects():
                return _send(client, f"Unknown project: {project_id}")
            return _send(client, renderer(project_id))
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
