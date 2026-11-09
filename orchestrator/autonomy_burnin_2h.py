#!/usr/bin/env python3
"""Bounded read-only AI PROF 2-hour burn-in. No code tasks, merges or deployment."""
from __future__ import annotations
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path("/home/agent/projects/ai-prof-control-center")
sys.path.insert(0, str(ROOT / "orchestrator"))
import telegram_bridge as bridge
import control_plane_backup_health as backups

STATE = bridge.STATE_DIR / "reports/autonomy-burnin-20261109.json"
REPORT = bridge.STATE_DIR / "reports/autonomy-burnin-20261109.md"
LOCK = bridge.STATE_DIR / "run/autonomy-burnin.lock"
SERVICES = ("ai-prof-control-center.service", "ai-prof-github-task-gateway.service",
            "ai-prof-telegram-bridge.service")
DURATION = 2 * 60 * 60
MAX_SAMPLES = 20


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, raw = tempfile.mkstemp(prefix=".autonomy-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, sort_keys=True, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.chmod(raw, 0o600)
        os.replace(raw, path)
    finally:
        if os.path.exists(raw):
            os.unlink(raw)


def read_state() -> dict:
    if STATE.is_symlink() or not STATE.is_file():
        raise RuntimeError("файл задания недоступен")
    state = json.loads(STATE.read_text(encoding="utf-8"))
    if state.get("version") != 1 or state.get("duration_seconds") != DURATION:
        raise RuntimeError("контракт задания изменён")
    return state


def service_state(name: str) -> tuple[bool, str]:
    try:
        p = subprocess.run(
            ["/usr/bin/systemctl", "show", name, "--no-pager",
             "-p", "ActiveState", "-p", "NRestarts", "-p", "MainPID"],
            capture_output=True, text=True, timeout=8, check=False,
        )
        values = dict(line.split("=", 1) for line in p.stdout.splitlines() if "=" in line)
        return (p.returncode == 0 and values.get("ActiveState") == "active"
                and int(values.get("MainPID", "0")) > 0), values.get("NRestarts", "?")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False, "?"


def available_memory_bytes() -> tuple[int, float]:
    rows = {}
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            rows[key] = int(val.strip().split()[0]) * 1024
    psi = Path("/proc/pressure/memory").read_text(encoding="ascii").splitlines()[0]
    avg60 = float(next(p.split("=", 1)[1] for p in psi.split() if p.startswith("avg60=")))
    return rows.get("MemAvailable", 0), avg60


def snapshot() -> dict:
    result = {"timestamp": int(time.time()), "services": {},
              "warnings": [], "critical": []}
    for service in SERVICES:
        active, restarts = service_state(service)
        result["services"][service] = {"active": active, "restarts": restarts}
        if not active:
            result["critical"].append("Служба недоступна: " + service)
    try:
        memory, psi = available_memory_bytes()
        result["available_ram_mib"] = memory // 1048576
        result["memory_psi_avg60"] = psi
        if memory < 800 * 1048576 or psi >= 10.0:
            result["critical"].append("Недостаточно памяти или перегрузка")
        elif memory < 1500 * 1048576 or psi >= 2.0:
            result["warnings"].append("Повышенная нагрузка памяти")
    except (OSError, ValueError, StopIteration):
        result["critical"].append("Показатели памяти недоступны")
    try:
        import shutil
        usage = shutil.disk_usage("/")
        disk = round(100 * usage.used / max(1, usage.used + usage.free))
        result["disk_percent"] = disk
        if disk >= 92:
            result["critical"].append("Диск заполнен более чем на 92%")
        elif disk >= 85:
            result["warnings"].append("Диск заполнен более чем на 85%")
    except OSError:
        result["critical"].append("Диск недоступен")
    status, reason = backups.evaluate()
    result["backup_status"] = status
    if status == "CRITICAL":
        result["critical"].append("Локальная зашифрованная копия: " + reason)
    elif status != "PASS":
        result["warnings"].append("Резервная копия: " + reason)
    try:
        locations = bridge._queue_locations(bridge.STATE_DIR)
        result["queue_count"] = len(locations)
        result["queue_duplicates"] = sum(len(v) != 1 for v in locations.values())
        result["queue_active"] = sum(len(v) == 1 and v[0][0] in ("active", "pending_codex")
                                     for v in locations.values())
        if result["queue_duplicates"]:
            result["critical"].append("Нарушена целостность очереди")
    except (OSError, ValueError):
        result["critical"].append("Очередь недоступна")
    return result


def report(state: dict) -> str:
    checks = state.get("samples", [])
    errors = [f"{item['timestamp']}: {text}" for item in checks
              for text in item.get("critical", [])]
    warnings = [f"{item['timestamp']}: {text}" for item in checks
                for text in item.get("warnings", [])]
    result = "PASS" if not errors else "FAIL"
    return ("# AI PROF — двухчасовая проверка автономности\n\n"
            f"Результат: {result}\n"
            f"Начало Unix UTC: {state['started_at']}\n"
            f"Окончание Unix UTC: {state['deadline']}\n"
            f"Количество снимков: {len(checks)}\n"
            f"Критических событий: {len(errors)}\n"
            f"Предупреждений: {len(warnings)}\n\n"
            "## Критические события\n" + ("\n".join("- " + x for x in errors) or "Нет") +
            "\n\n## Предупреждения\n" + ("\n".join("- " + x for x in warnings[:25]) or "Нет") +
            "\n\n## Ограничения\n"
            "Только чтение, не проверяет создание PR; код, проекты и production не изменяются.\n")


def send_result(state: dict) -> None:
    errors = sum(len(x.get("critical", [])) for x in state["samples"])
    warnings = sum(len(x.get("warnings", [])) for x in state["samples"])
    message = ("AI PROF — итог двухчасовой проверки на Ubuntu\n"
               f"Результат: {'✅ PASS' if errors == 0 else '❌ FAIL'}\n"
               f"Проверок: {len(state['samples'])}\n"
               f"Критических событий: {errors}\n"
               f"Предупреждений: {warnings}\n"
               "Проверены службы, очередь, память, диск и локальные копии.\n"
               "Изменений в коде и production не было.")
    cfg = bridge.load_config()
    bridge.TelegramClient(cfg).send(message)


def run(start: bool) -> int:
    LOCK.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LOCK.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("AUTONOMY_BURNIN_BUSY")
            return 11
        if start:
            if STATE.exists():
                print("AUTONOMY_BURNIN_ALREADY_CREATED")
                return 12
            now = int(time.time())
            state = {"version": 1, "duration_seconds": DURATION,
                     "started_at": now, "deadline": now + DURATION,
                     "samples": [], "finished": False, "notified": False}
        else:
            state = read_state()
        if not state["finished"]:
            if len(state["samples"]) < MAX_SAMPLES:
                state["samples"].append(snapshot())
            if int(time.time()) >= state["deadline"]:
                state["finished"] = True
                REPORT.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                REPORT.write_text(report(state), encoding="utf-8")
                REPORT.chmod(0o600)
        write_json(STATE, state)
        if state["finished"] and not state["notified"]:
            try:
                send_result(state)
            except (OSError, ValueError, bridge.BridgeError):
                print("AUTONOMY_BURNIN_TELEGRAM_RETRY")
                return 1
            state["notified"] = True
            write_json(STATE, state)
        last = state["samples"][-1] if state["samples"] else {}
        print("AUTONOMY_BURNIN_" + ("FINISHED" if state["finished"] else "ACTIVE"),
              "samples=" + str(len(state["samples"])),
              "critical=" + str(len(last.get("critical", []))),
              "deadline=" + str(state["deadline"]))
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "tick"])
    args = parser.parse_args()
    raise SystemExit(run(start=args.action == "start"))
