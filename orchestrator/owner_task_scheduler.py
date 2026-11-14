#!/usr/bin/env python3
"""Durable owner-only timed task intake through the existing AI PROF FIFO.

A schedule is not an execution authority outside the approved project registry:
when due, the same scoped validated intake contract is applied. Job branches
are persisted before dispatch so a crash cannot duplicate a queue task.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from contextlib import closing
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from orchestrator import telegram_bridge as legacy
    from orchestrator import telegram_bridge_v4 as v4
except ImportError:
    import telegram_bridge as legacy
    import telegram_bridge_v4 as v4

TZ = ZoneInfo("Asia/Bishkek")
STATE_DB = legacy.STATE_DIR / "run" / "owner-task-schedule-v1.sqlite3"
MAX_SCHEDULES = 100
START_RE = re.compile(r"(?im)^\s*Начать:\s*(.*?)\s*\.?\s*$")
DURATION_RE = re.compile(r"(?im)^\s*Срок:\s*(.*?)\s*\.?\s*$")
REPORT_RE = re.compile(r"(?im)^\s*Отч[её]т:\s*(.*?)\s*\.?\s*$")
PRIORITY_RE = re.compile(r"(?im)^\s*Приоритет:\s*(.*?)\s*\.?\s*$")
ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})$")
RELATIVE_RE = re.compile(r"^(сегодня|завтра)\s+в\s+(\d{1,2}):(\d{2})$")
DURATION_VALUE_RE = re.compile(r"^(\d{1,3})\s*(?:часа|часов|час)$")


class ScheduleError(ValueError):
    pass


@dataclass(frozen=True)
class OwnerPlan:
    start_at: int
    deadline_at: int
    report_interval: int
    priority: str


def parse_plan(text: str, *, now: datetime | None = None) -> OwnerPlan | None:
    """Strictly parse supported Russian scheduling fields or refuse the request."""
    start_match = START_RE.search(text)
    other_fields = (DURATION_RE.search(text), REPORT_RE.search(text),
                    PRIORITY_RE.search(text))
    if not start_match:
        if any(other_fields):
            raise ScheduleError("Укажите «Начать: завтра в 18:00» или дату YYYY-MM-DD HH:MM.")
        return None
    if now is None:
        now = datetime.now(TZ)
    now = now.astimezone(TZ)
    start_str = start_match.group(1).strip().rstrip(".").casefold()
    relative = RELATIVE_RE.fullmatch(start_str)
    absolute = ISO_RE.fullmatch(start_str)
    if relative:
        day = now.date() + timedelta(days=int(relative.group(1) == "завтра"))
        hour, minute = int(relative.group(2)), int(relative.group(3))
        if hour > 23 or minute > 59:
            raise ScheduleError("Некорректное время начала.")
        start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
    elif absolute:
        try:
            start = datetime(*(int(x) for x in absolute.groups()), tzinfo=TZ)
        except ValueError as exc:
            raise ScheduleError("Некорректная дата начала.") from exc
    else:
        raise ScheduleError("Напишите время: «Начать: завтра в 18:00» или «Начать: 2026-11-15 18:00».")
    if start.timestamp() <= now.timestamp():
        raise ScheduleError("Время начала уже прошло. Укажите будущее время.")
    if start.timestamp() > (now + timedelta(days=90)).timestamp():
        raise ScheduleError("Планировать разрешено не более чем на 90 дней вперёд.")

    duration = other_fields[0]
    if duration is None:
        raise ScheduleError("Укажите срок, например «Срок: 4 часа».")
    match = DURATION_VALUE_RE.fullmatch(duration.group(1).strip().rstrip(".").casefold())
    if not match or not 1 <= int(match.group(1)) <= 168:
        raise ScheduleError("Срок: от 1 до 168 часов, например «Срок: 4 часа».")
    hours = int(match.group(1))
    report = other_fields[1]
    interval = 0
    if report:
        val = report.group(1).strip().rstrip(".").casefold()
        if val in ("каждый час", "раз в час", "ежечасно"):
            interval = 3600
        elif val in ("без отчётов", "только результат"):
            interval = 0
        else:
            raise ScheduleError("Отчёт указан неоднозначно. Напишите «Отчёт: каждый час» или «Отчёт: только результат».")
    priority = other_fields[2]
    value = priority.group(1).strip().rstrip(".").casefold() if priority else "обычный"
    if value not in ("высокий", "обычный", "низкий"):
        raise ScheduleError("Приоритет: высокий, обычный или низкий.")
    return OwnerPlan(int(start.timestamp()), int(start.timestamp()) + hours * 3600,
                     interval, value)


def connect(path: Path = STATE_DB) -> sqlite3.Connection:
    if path.is_symlink():
        raise ScheduleError("unsafe schedule database symlink")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(path, timeout=5)
    os.chmod(path, 0o600)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS owner_schedule (
            schedule_id TEXT PRIMARY KEY,
            request_key TEXT UNIQUE NOT NULL,
            branch TEXT UNIQUE NOT NULL,
            instructions TEXT NOT NULL,
            scope TEXT NOT NULL,
            start_at INTEGER NOT NULL,
            deadline_at INTEGER NOT NULL,
            report_interval INTEGER NOT NULL,
            priority TEXT NOT NULL,
            state TEXT NOT NULL,
            task_id TEXT,
            last_report INTEGER NOT NULL DEFAULT 0,
            overdue_notified INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        )
    """)
    conn.commit()
    return conn


def register(instructions: str, scope: str, plan: OwnerPlan, *,
             path: Path = STATE_DB) -> str:
    """Persist scheduled scope + immutable work branch before any execution."""
    command = legacy.Command("task", "ai-prof-control-center",
                             instructions[:legacy.TASK_TITLE_LIMIT],
                             instructions + "\nРазрешённый файл: " + scope, True)
    projects = legacy.load_projects()
    pid, project = legacy.resolve_project(command.project, projects)
    exact = v4.explicit_allowed_paths(command, project)
    if pid != "ai-prof-control-center" or exact != [scope]:
        raise ScheduleError("Не удаётся однозначно подтвердить разрешённый файл.")
    branch = legacy.make_work_branch(command, pid, project)
    if not re.fullmatch(r"feature/telegram-[a-f0-9]{8}-[a-f0-9]{12}", branch):
        raise ScheduleError("Invalid scheduled work branch")
    if plan.start_at <= int(time.time()):
        raise ScheduleError("Время начала уже прошло — выберите новую дату.")
    request_key = hashlib.sha256(
        (instructions + "\0" + scope + "\0" + str(plan.start_at)).encode("utf-8")
    ).hexdigest()
    schedule_id = "S" + uuid.uuid4().hex[:12].upper()
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT schedule_id FROM owner_schedule WHERE request_key=?", (request_key,)
        ).fetchone()
        if existing:
            return existing["schedule_id"]
        count = db.execute("SELECT count(*) FROM owner_schedule WHERE state IN ('scheduled','dispatching','running')").fetchone()[0]
        if count >= MAX_SCHEDULES:
            raise ScheduleError("Слишком много ожидающих заданий.")
        db.execute("""
            INSERT INTO owner_schedule
            (schedule_id,request_key,branch,instructions,scope,start_at,deadline_at,
             report_interval,priority,state,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,'scheduled',?)
        """, (schedule_id, request_key, branch, instructions, scope, plan.start_at, plan.deadline_at,
              plan.report_interval, plan.priority, int(time.time())))
    return schedule_id


def _find_existing_task(branch: str, *, state_root: Path) -> tuple[str, str] | None:
    # The published queue record is the authoritative evidence of prior intake.
    matches = []
    for task_id, positions in legacy._queue_locations(state_root).items():
        if len(positions) != 1:
            continue
        queue, task_file, fields = positions[0]
        if (task_file.is_symlink() or not task_file.is_file()
                or task_file.stat().st_size > 250_000
                or fields.get("Project-Path") !=
                "/home/agent/projects/ai-prof-control-center-maintenance"):
            continue
        # The Telegram status reader intentionally excludes Work-Branch.
        # Read only its own validated queue record, never instruction text.
        try:
            content = task_file.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue
        found = re.search(r"(?m)^Work-Branch:\s*([^\r\n]+)$", content)
        if found and found.group(1).strip() == branch:
            matches.append((task_id, queue))
    if len(matches) > 1:
        raise ScheduleError("Повторная регистрация ветки задания: запуск заблокирован.")
    return matches[0] if matches else None


def _submit_at_due(row: sqlite3.Row) -> str:
    command = legacy.Command("task", "ai-prof-control-center",
                             row["instructions"][:legacy.TASK_TITLE_LIMIT],
                             row["instructions"] + "\nРазрешённый файл: " + row["scope"], True)
    args, project = legacy.submit_arguments(command, legacy.load_projects())
    if project != "ai-prof-control-center":
        raise ScheduleError("Project changed since confirmation")
    if args[args.index("--scope") + 1] != row["scope"]:
        raise ScheduleError("Scope changed since confirmation")
    args[args.index("--work-branch") + 1] = row["branch"]
    result = subprocess.run(args, cwd=legacy.ROOT, text=True, capture_output=True,
                            timeout=35, check=False)
    if result.returncode != 0:
        raise ScheduleError("Постановка задачи отклонена штатной очередью.")
    try:
        obj = json.loads(result.stdout)
        task_id = obj["task_id"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ScheduleError("Invalid task intake response") from exc
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{5,79}", task_id):
        raise ScheduleError("Invalid task identifier")
    return task_id


def tick(client, *, db_path: Path = STATE_DB, state_root: Path | None = None,
         now: int | None = None) -> None:
    """Reconcile one due entry, plus bounded progress/deadline reports.

    Fail closed if persistence is unavailable. Does not take any Git/publish
    authority and never calls Codex directly.
    """
    if state_root is None:
        state_root = legacy.STATE_DIR
    if now is None:
        now = int(time.time())
    with closing(connect(db_path)) as db, db:
        rows = db.execute("SELECT * FROM owner_schedule WHERE state IN ('scheduled','dispatching','running') ORDER BY start_at LIMIT 100").fetchall()
        for row in rows:
            sid, state = row["schedule_id"], row["state"]
            existing = _find_existing_task(row["branch"], state_root=state_root) if state != "scheduled" else None
            just_claimed = False
            if state == "scheduled" and row["start_at"] <= now:
                claim = db.execute(
                    "UPDATE owner_schedule SET state='dispatching',last_report=? "
                    "WHERE schedule_id=? AND state='scheduled'",
                    (now, sid),
                )
                db.commit()
                if claim.rowcount != 1:
                    continue
                just_claimed = True
                state = "dispatching"
                existing = _find_existing_task(row["branch"], state_root=state_root)
            if state == "dispatching":
                if not existing and not just_claimed:
                    if now - row["last_report"] < 90:
                        # Another worker may be posting the same immutable branch.
                        continue
                    db.execute("UPDATE owner_schedule SET last_report=? WHERE schedule_id=? AND state='dispatching'", (now, sid))
                    db.commit()
                if not existing and now >= row["deadline_at"]:
                    db.execute("UPDATE owner_schedule SET state='missed' WHERE schedule_id=?", (sid,))
                    db.commit()
                    client.send(f"⚠️ Время задания {sid} истекло до запуска. Автоматически запускать поздно не буду.")
                    continue
                if not existing:
                    try:
                        task_id = _submit_at_due(row)
                    except (ScheduleError, subprocess.SubprocessError, OSError):
                        # Same immutable branch on next tick; prior queue record is reconciled.
                        continue
                else:
                    task_id = existing[0]
                db.execute("UPDATE owner_schedule SET task_id=?, state='running', last_report=? WHERE schedule_id=?", (task_id, now, sid))
                db.commit()
                client.send(f"▶️ Время наступило. Задание {sid} поставлено в рабочую очередь AI PROF: {task_id}. Срок: {datetime.fromtimestamp(row['deadline_at'], TZ):%d.%m %H:%M}.")
                continue  # Avoid duplicate immediate hourly progress after kickoff.
            if state == "running":
                current = _find_existing_task(row["branch"], state_root=state_root)
                if current and current[1] in ("completed", "failed", "blocked", "cancelled"):
                    db.execute("UPDATE owner_schedule SET state=? WHERE schedule_id=?", (current[1], sid))
                    db.commit()
                    client.send(f"Задание по расписанию {sid}: {current[1]}. ID: {current[0]}. Детали — «задачи».")
                    continue
                if now >= row["deadline_at"]:
                    if not row["overdue_notified"]:
                        db.execute("UPDATE owner_schedule SET overdue_notified=1 WHERE schedule_id=?", (sid,))
                        db.commit()
                        client.send(f"⏰ Срок задания {sid} истёк. Это контрольный срок, не принудительная остановка Codex. Состояние можно проверить командой «задачи».")
                    continue
                if row["report_interval"] and now - row["last_report"] >= row["report_interval"]:
                    db.execute("UPDATE owner_schedule SET last_report=? WHERE schedule_id=?", (now, sid))
                    db.commit()
                    status = current[1] if current else "не найдено в очереди"
                    client.send(f"📋 Отчёт по расписанию {sid}. Задача: {row['task_id']}. Статус: {status}. Контрольный срок: {datetime.fromtimestamp(row['deadline_at'], TZ):%d.%m %H:%M}.")


def cancel(schedule_id: str, *, db_path: Path = STATE_DB) -> bool:
    """Cancel an unstarted schedule; running queue jobs retain existing authority."""
    if not re.fullmatch(r"S[0-9A-F]{12}", schedule_id):
        raise ScheduleError("Неверный номер расписания.")
    with closing(connect(db_path)) as db, db:
        changed = db.execute(
            "UPDATE owner_schedule SET state='cancelled' "
            "WHERE schedule_id=? AND state='scheduled'",
            (schedule_id,),
        )
        return changed.rowcount == 1


def summary(*, db_path: Path = STATE_DB) -> str:
    with closing(connect(db_path)) as db, db:
        rows = db.execute("SELECT schedule_id, start_at, deadline_at, state, priority FROM owner_schedule ORDER BY start_at DESC LIMIT 10").fetchall()
    if not rows:
        return "Запланированных задач нет."
    return "\n".join(
        f"{row['schedule_id']} — {row['state']}; начало {datetime.fromtimestamp(row['start_at'], TZ):%d.%m %H:%M}, срок {datetime.fromtimestamp(row['deadline_at'], TZ):%d.%m %H:%M}, приоритет {row['priority']}"
        for row in rows
    )
