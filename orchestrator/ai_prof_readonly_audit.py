#!/usr/bin/env python3
"""Bounded, read-only operational audit. No arbitrary command execution."""
from __future__ import annotations
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import shutil
import urllib.request

import telegram_bridge as bridge

STATE = Path("/home/agent/.local/state/ai-prof-control-center")
CONTAINERS = ("ai-prof-n8n", "ai-prof-postgres")
SERVICES = (
    "ai-prof-control-center.service",
    "ai-prof-github-task-gateway.service",
    "ai-prof-telegram-bridge.service",
)

def service_state(name: str) -> str:
    if name not in SERVICES:
        raise ValueError("service not on diagnostic allowlist")
    try:
        result = subprocess.run(
            ["/usr/bin/systemctl", "show", name, "-p", "ActiveState",
             "-p", "MainPID", "-p", "NRestarts", "--no-pager"],
            capture_output=True, text=True, timeout=4, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "UNKNOWN (systemctl unavailable)"
    if result.returncode:
        return "UNKNOWN (systemctl failed)"
    data = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if data.get("ActiveState") != "active":
        return "FAIL (" + data.get("ActiveState", "unknown") + ")"
    return "PASS (pid=" + data.get("MainPID", "?") + ", restarts=" + data.get("NRestarts", "?") + ")"

def container_state(name: str) -> str:
    if name not in CONTAINERS:
        raise ValueError("container not on diagnostic allowlist")
    try:
        result = subprocess.run(
            ["/usr/bin/docker", "inspect", "--format",
             "{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}unknown{{end}}", name],
            capture_output=True, text=True, timeout=4, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "UNKNOWN (docker unavailable)"
    if result.returncode:
        return "UNKNOWN (container unavailable)"
    status = result.stdout.strip()
    return ("PASS" if status == "running/healthy" else "FAIL") + " (" + status[:40] + ")"

def n8n_readiness() -> str:
    try:
        with urllib.request.urlopen("http://127.0.0.1:5678/healthz/readiness", timeout=4) as response:
            return "PASS (HTTP 200)" if response.status == 200 else "FAIL (HTTP response)"
    except (OSError, ValueError):
        return "FAIL (unreachable)"

def queue_overview(state_root: Path = STATE) -> tuple[dict[str, int], str]:
    counts: dict[str, int] = {}
    recent: tuple[float, str, str] | None = None
    for task_id, locations in bridge._queue_locations(state_root).items():
        if len(locations) != 1:
            counts["inconsistent"] = counts.get("inconsistent", 0) + 1
            continue
        queue, path, _fields = locations[0]
        counts[queue] = counts.get(queue, 0) + 1
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        if recent is None or stamp > recent[0]:
            recent = (stamp, task_id, queue)
    last = f"{recent[1]} ({recent[2]})" if recent else "none"
    return counts, last

def build_report(state_root: Path = STATE) -> str:
    lines = [
        "AI PROF — read-only operational audit",
        "UTC: " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "Mode: read-only; no code edits, deploy, database mutation or shell from chat.",
    ]
    for service in SERVICES:
        lines.append(service + ": " + service_state(service))
    for container in CONTAINERS:
        lines.append(container + ": " + container_state(container))
    lines.append("n8n readiness: " + n8n_readiness())
    try:
        hb = bridge.control_center_health(state_root)
    except (OSError, ValueError):
        hb = "UNKNOWN"
    lines.append("Heartbeat: " + bridge.redact(hb)[:180])
    try:
        counts, last = queue_overview(state_root)
        lines.append("Queue: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        lines.append("Last queue transition: " + last)
    except (OSError, ValueError):
        lines.append("Queue: UNKNOWN")
    total, used, free = shutil.disk_usage("/")
    usage = used * 100 // total if total else 100
    lines.append(f"Disk: {usage}% used; {free // (1024**3)} GiB free")
    lines.append("Disk: " + ("WARNING (>=80%)" if usage >= 80 else "PASS"))
    try:
        values = {}
        for raw in Path("/proc/meminfo").read_text().splitlines():
            k, _, v = raw.partition(":")
            values[k] = int(v.strip().split()[0])
        total_swap = values.get("SwapTotal", 0)
        used_swap = total_swap - values.get("SwapFree", 0)
        lines.append("Swap: " + (f"{used_swap * 100 // total_swap}% used" if total_swap else "disabled"))
    except (OSError, ValueError, ZeroDivisionError):
        lines.append("Swap: UNKNOWN")
    lines.append("Verdict: review warnings and task results; a healthy service is not proof all tasks succeeded.")
    return bridge._telegram_truncate(bridge.redact("\n".join(lines)))
