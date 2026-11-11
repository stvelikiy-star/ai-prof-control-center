#!/usr/bin/env python3
"""One-time read-only GitHub CI watch for KÖL security PR #157."""
from __future__ import annotations
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
import telegram_bridge as bridge

PR = 157
REPOSITORY = "stvelikiy-star/kol-travel-platform"
SHA = "3059f24603dd239542be70b90aa8a078e0e43724"
DEADLINE = int(datetime(2026, 11, 10, 10, 40, tzinfo=timezone.utc).timestamp())
STATE = Path("/home/agent/.local/state/ai-prof-control-center/reports/kol-pr157-ci-watch.json")
LOCK = Path("/home/agent/.local/state/ai-prof-control-center/run/kol-pr157-ci-watch.lock")
REQUIRED = {
    "verify", "public-flows", "visual", "local-staging-smoke",
    "partner-catalog-write-runtime", "client-profile-runtime",
    "client-support-runtime", "admin-support-runtime",
    "partner-availability-runtime", "partner-order-runtime",
    "admin-catalog-moderation-runtime", "admin-catalog-governance-runtime",
    "Vercel Preview Comments",
}
EXPECTED = len(REQUIRED)


def github_get(resource: str) -> dict:
    command = ["/usr/bin/gh", "api", "-X", "GET", resource]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=35, check=False)
    if proc.returncode:
        raise RuntimeError("GitHub API недоступен")
    value = json.loads(proc.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Некорректный ответ GitHub API")
    return value


def evaluate(pr: dict, checks: dict) -> dict:
    if pr.get("number") != PR or pr.get("head", {}).get("sha") != SHA:
        return {"status": "blocked", "message": "PR или SHA изменились: требуется новый аудит"}
    if pr.get("merged") or pr.get("state") != "open":
        return {"status": "blocked", "message": "PR закрыт или объединён вне этого задания"}
    runs = checks.get("check_runs")
    if not isinstance(runs, list):
        return {"status": "pending", "message": "Ответ с проверками неполон"}
    by_name = {}
    for run in runs:
        if isinstance(run, dict) and isinstance(run.get("name"), str):
            by_name[run["name"]] = run
    if not REQUIRED.issubset(by_name) or len(by_name) < EXPECTED:
        return {"status": "pending", "message": "Ожидаем регистрацию всех обязательных проверок"}
    unfinished = [name for name, item in by_name.items() if item.get("status") != "completed"]
    failed = [name for name, item in by_name.items()
              if item.get("status") == "completed"
              and item.get("conclusion") != "success"]
    if unfinished:
        return {"status": "pending", "message": f"Выполняются: {len(unfinished)}; с ошибкой: {len(failed)}"}
    if failed:
        return {"status": "failed", "message": "Не прошли: " + ", ".join(sorted(failed))}
    return {"status": "passed", "message": f"Все {len(by_name)} GitHub-проверок завершены"}


def store(data: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".pr157-ci-", dir=STATE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as writer:
            json.dump(data, writer, ensure_ascii=False, sort_keys=True, indent=2)
            writer.write("\n")
            writer.flush()
            os.fsync(writer.fileno())
        os.chmod(name, 0o600)
        os.replace(name, STATE)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run() -> int:
    LOCK.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LOCK.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("CI_WATCH_BUSY")
            return 0
        data = {"samples": 0, "notified": False, "status": "pending"}
        if STATE.is_file():
            data = json.loads(STATE.read_text(encoding="utf-8"))
        if data.get("notified"):
            print("CI_WATCH_ALREADY_NOTIFIED", data.get("status"))
            return 0
        try:
            pr = github_get(f"repos/{REPOSITORY}/pulls/{PR}")
            checks = github_get(f"repos/{REPOSITORY}/commits/{SHA}/check-runs?per_page=100")
            decision = evaluate(pr, checks)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            decision = {"status": "pending", "message": "Временная ошибка связи с GitHub"}
        if decision["status"] == "pending" and time.time() >= DEADLINE:
            decision = {"status": "timeout", "message": "Отведённые четыре часа истекли; проверки не завершились"}
        data["samples"] = min(100, data.get("samples", 0) + 1)
        data.update(decision)
        data["updated_at"] = int(time.time())
        store(data)
        if decision["status"] != "pending":
            symbol = "✅" if decision["status"] == "passed" else "⚠️"
            message = (
                f"{symbol} AI PROF — GitHub CI KÖL PR №157\n"
                f"Итог: {decision['status'].upper()}\n"
                f"{decision['message']}\n"
                f"Проверок состояния: {data['samples']}\n"
                f"PR: https://github.com/{REPOSITORY}/pull/{PR}\n"
                "Слияние и деплой не выполнялись."
            )
            try:
                config = bridge.load_config()
                client = bridge.TelegramClient(config)
                client.call("sendMessage", {"chat_id": config.chat_id, "text": message})
            except (OSError, ValueError, RuntimeError, Exception) as exc:
                # Preserve retry state, not exception/credentials in logs.
                print("CI_WATCH_NOTIFY_RETRY", type(exc).__name__)
                return 1
            data["notified"] = True
            store(data)
        print("CI_WATCH_" + data["status"].upper(), "samples=" + str(data["samples"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
