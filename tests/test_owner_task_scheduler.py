from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from contextlib import closing
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
import owner_task_scheduler as scheduler

TZ = ZoneInfo("Asia/Bishkek")
NOW = datetime(2026, 11, 14, 17, 52, tzinfo=TZ)
INSTRUCTIONS = (
    "Улучши интерфейс AI PROF.\n"
    "Начать: завтра в 18:00.\n"
    "Срок: 4 часа.\n"
    "Приоритет: высокий.\n"
    "Отчёт: каждый час."
)
BRANCH = "feature/telegram-12345678-0123456789ab"


class OwnerScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "state" / "scheduler.sqlite"
        self.state_root = Path(self.tmp.name) / "runtime"
        self.state_root.mkdir()
        self.plan = scheduler.OwnerPlan(100000, 100000 + 4 * 3600, 3600, "высокий")
        self.addCleanup(self.tmp.cleanup)

    def _new(self):
        with mock.patch.object(scheduler.time, "time", return_value=99900), mock.patch.object(scheduler.legacy, "load_projects", return_value={
            "ai-prof-control-center": {"allowed_scope": ["orchestrator/telegram_bridge.py"],
                                        "work_prefixes": ["feature/"]},
        }), mock.patch.object(scheduler.legacy, "make_work_branch", return_value=BRANCH):
            return scheduler.register(INSTRUCTIONS, "orchestrator/telegram_bridge.py",
                                      self.plan, path=self.db_path)

    def test_relative_bishkek_plan_four_hours_and_hourly_reports(self):
        plan = scheduler.parse_plan(INSTRUCTIONS, now=NOW)
        self.assertEqual(datetime.fromtimestamp(plan.start_at, TZ),
                         datetime(2026, 11, 15, 18, tzinfo=TZ))
        self.assertEqual(datetime.fromtimestamp(plan.deadline_at, TZ),
                         datetime(2026, 11, 15, 22, tzinfo=TZ))
        self.assertEqual(plan.priority, "высокий")
        self.assertEqual(plan.report_interval, 3600)

    def test_unambiguous_date_format(self):
        plan = scheduler.parse_plan(
            "Задача: исправить.\nНачать: 2026-11-15 18:00.\nСрок: 4 часа.",
            now=NOW,
        )
        self.assertEqual(datetime.fromtimestamp(plan.start_at, TZ).day, 15)

    def test_invalid_report_asks_clarification(self):
        with self.assertRaisesRegex(scheduler.ScheduleError, "неоднозначно"):
            scheduler.parse_plan(INSTRUCTIONS.replace("каждый час", "каждый день час"),
                                 now=NOW)

    def test_no_schedule_retains_old_immediate_task_behavior(self):
        self.assertIsNone(scheduler.parse_plan("Задача: исправь бота", now=NOW))

    def test_duration_without_start_rejected(self):
        with self.assertRaisesRegex(scheduler.ScheduleError, "Начать"):
            scheduler.parse_plan("Задача: исправь бота\nСрок: 4 часа", now=NOW)

    def test_past_start_rejected(self):
        with self.assertRaisesRegex(scheduler.ScheduleError, "уже прошло"):
            scheduler.parse_plan("Начать: сегодня в 16:00\nСрок: 4 часа", now=NOW)

    def test_invalid_schedule_values_fail_closed(self):
        for start in ("завтра в 25:00", "2026-11-31 18:00", "завтра в 18:98"):
            with self.subTest(start=start), self.assertRaises(scheduler.ScheduleError):
                scheduler.parse_plan(f"Начать: {start}\nСрок: 4 часа", now=NOW)
        for duration in ("1000 часов", "0 часов", "до пятницы"):
            with self.subTest(duration=duration), self.assertRaises(scheduler.ScheduleError):
                scheduler.parse_plan(f"Начать: завтра в 18:00\nСрок: {duration}", now=NOW)

    def test_register_is_durable_and_has_no_code_execution(self):
        sid = self._new()
        self.assertTrue(sid.startswith("S"))
        with closing(scheduler.connect(self.db_path)) as db, db:
            row = db.execute("SELECT * FROM owner_schedule").fetchone()
            self.assertEqual(row["state"], "scheduled")
            self.assertEqual(row["branch"], BRANCH)
            self.assertIsNone(row["task_id"])
        self.assertEqual(self.db_path.stat().st_mode & 0o777, 0o600)

    def test_same_confirmed_schedule_is_idempotent_across_restart(self):
        first = self._new()
        second = self._new()
        self.assertEqual(first, second)
        with closing(scheduler.connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM owner_schedule").fetchone()[0], 1)

    def test_not_due_keeps_schedule_without_submitting(self):
        self._new()
        client = mock.Mock()
        with mock.patch.object(scheduler, "_submit_at_due") as submit:
            scheduler.tick(client, db_path=self.db_path, state_root=self.state_root, now=99999)
        submit.assert_not_called()
        client.send.assert_not_called()

    def test_schedule_can_be_cancelled_only_before_start(self):
        sid = self._new()
        self.assertTrue(scheduler.cancel(sid, db_path=self.db_path))
        self.assertFalse(scheduler.cancel(sid, db_path=self.db_path))
        with closing(scheduler.connect(self.db_path)) as db, db:
            self.assertEqual(db.execute("SELECT state FROM owner_schedule").fetchone()[0], "cancelled")
        client = mock.Mock()
        with mock.patch.object(scheduler, "_submit_at_due") as submit:
            scheduler.tick(client, db_path=self.db_path, state_root=self.state_root, now=100010)
        submit.assert_not_called()

    def test_due_task_submits_once_and_persists_identifiers(self):
        self._new()
        client = mock.Mock()
        with mock.patch.object(scheduler, "_find_existing_task", return_value=None), mock.patch.object(
            scheduler, "_submit_at_due", return_value="AI_PROF_CONTROL_CENTER_TEST_001"
        ) as submit:
            scheduler.tick(client, db_path=self.db_path, state_root=self.state_root, now=100000)
            scheduler.tick(client, db_path=self.db_path, state_root=self.state_root, now=100010)
        submit.assert_called_once()
        with closing(scheduler.connect(self.db_path)) as db, db:
            row = db.execute("SELECT * FROM owner_schedule").fetchone()
            self.assertEqual(row["state"], "running")
            self.assertEqual(row["task_id"], "AI_PROF_CONTROL_CENTER_TEST_001")
        self.assertEqual(client.send.call_count, 1)

    def test_claim_in_progress_blocks_concurrent_duplicate_submission(self):
        self._new()
        with closing(scheduler.connect(self.db_path)) as db, db:
            db.execute("UPDATE owner_schedule SET state='dispatching',last_report=100000")
        client = mock.Mock()
        with mock.patch.object(scheduler, "_find_existing_task", return_value=None), mock.patch.object(
            scheduler, "_submit_at_due"
        ) as submit:
            scheduler.tick(client, db_path=self.db_path, state_root=self.state_root,
                           now=100030)
        submit.assert_not_called()
        client.send.assert_not_called()

    def test_crash_after_intake_reconciles_without_second_submit(self):
        self._new()
        with closing(scheduler.connect(self.db_path)) as db, db:
            db.execute("UPDATE owner_schedule SET state='dispatching'")
        client = mock.Mock()
        with mock.patch.object(scheduler, "_find_existing_task",
                               return_value=("AI_PROF_CONTROL_CENTER_TEST_001", "pending")), mock.patch.object(
            scheduler, "_submit_at_due"
        ) as submit:
            scheduler.tick(client, db_path=self.db_path, state_root=self.state_root, now=100010)
        submit.assert_not_called()
        with closing(scheduler.connect(self.db_path)) as db, db:
            self.assertEqual(db.execute("SELECT state FROM owner_schedule").fetchone()[0], "running")

    def test_overdue_unstarted_task_never_runs_late(self):
        self._new()
        client = mock.Mock()
        with mock.patch.object(scheduler, "_find_existing_task", return_value=None), mock.patch.object(
            scheduler, "_submit_at_due"
        ) as submit:
            scheduler.tick(client, db_path=self.db_path,
                           state_root=self.state_root, now=100000 + 5 * 3600)
        submit.assert_not_called()
        with closing(scheduler.connect(self.db_path)) as db, db:
            self.assertEqual(db.execute("SELECT state FROM owner_schedule").fetchone()[0], "missed")

    def test_hourly_reporting_and_deadline_alert_not_duplicated(self):
        self._new()
        with closing(scheduler.connect(self.db_path)) as db, db:
            db.execute("UPDATE owner_schedule SET state='running',task_id='TASK_001',last_report=100000")
        client = mock.Mock()
        with mock.patch.object(scheduler, "_find_existing_task", return_value=("TASK_001", "active")):
            scheduler.tick(client, db_path=self.db_path, state_root=self.state_root, now=100300)
            client.send.assert_not_called()
            scheduler.tick(client, db_path=self.db_path, state_root=self.state_root, now=103600)
            self.assertEqual(client.send.call_count, 1)
            scheduler.tick(client, db_path=self.db_path, state_root=self.state_root, now=114401)
            self.assertEqual(client.send.call_count, 2)
            scheduler.tick(client, db_path=self.db_path, state_root=self.state_root, now=114405)
            self.assertEqual(client.send.call_count, 2)

    def test_terminal_status_is_marked(self):
        self._new()
        with closing(scheduler.connect(self.db_path)) as db, db:
            db.execute("UPDATE owner_schedule SET state='running', task_id='TASK_001'")
        client = mock.Mock()
        with mock.patch.object(scheduler, "_find_existing_task", return_value=("TASK_001", "completed")):
            scheduler.tick(client, db_path=self.db_path, state_root=self.state_root, now=100010)
        with closing(scheduler.connect(self.db_path)) as db, db:
            self.assertEqual(db.execute("SELECT state FROM owner_schedule").fetchone()[0], "completed")

    def test_existing_branch_checked_from_queue(self):
        task_id = "AI_PROF_CONTROL_CENTER_20261114T123456Z_ABCDEF"
        pending = self.state_root / "queue" / "pending"
        pending.mkdir(parents=True)
        (pending / (task_id + ".md")).write_text(
            f"Task-ID: {task_id}\n"
            "Project-Path: /home/agent/projects/ai-prof-control-center-maintenance\n"
            f"Work-Branch: {BRANCH}\n"
        )
        self.assertEqual(scheduler._find_existing_task(BRANCH, state_root=self.state_root),
                         (task_id, "pending"))


if __name__ == "__main__":
    unittest.main()
