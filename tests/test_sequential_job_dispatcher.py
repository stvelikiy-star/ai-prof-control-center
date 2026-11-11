from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
import sequential_job_dispatcher as q


class SequentialDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p = mock.patch.object(q, "STATE", Path(self.temp.name) / "queue.json")
        p.start()
        self.addCleanup(p.stop)

    def test_fixed_three_jobs_order(self):
        data = q.seed(100)
        self.assertEqual([x["kind"] for x in data["jobs"]],
                         ["health", "pilot-issue-5", "health"])
        self.assertEqual(data["deadline"], 100 + 4 * 3600)

    def test_identity_tamper_fails_closed(self):
        data = q.seed(100)
        data["jobs"][1]["kind"] = "shell-command"
        with self.assertRaises(q.QueueError):
            q.validate(data)

    def test_budget_expiry_blocks_without_execution(self):
        data = q.seed(100)
        with mock.patch.object(q, "resource_gate") as gate, mock.patch.object(q, "perform") as execute:
            self.assertEqual(q.progress(data, now=100+q.TIME_BUDGET_SECONDS), "BLOCKED_TIMEOUT")
            gate.assert_not_called()
            execute.assert_not_called()

    def test_memory_guard_defers(self):
        data = q.seed(100)
        with mock.patch.object(q, "resource_gate", return_value=(False, "RAM")), mock.patch.object(q, "perform") as execute:
            self.assertEqual(q.progress(data, now=101), "DEFERRED_RAM")
            self.assertEqual(data["jobs"][0]["attempts"], 0)
            execute.assert_not_called()

    def test_one_task_per_tick_even_on_success(self):
        data = q.seed(100)
        with mock.patch.object(q, "resource_gate", return_value=(True, "OK")), mock.patch.object(q, "perform", return_value=(True, "PASS")) as execute:
            self.assertTrue(q.progress(data, now=101).startswith("PASS_"))
            self.assertEqual(execute.call_count, 1)
            self.assertEqual([x["phase"] for x in data["jobs"]], ["completed", "pending", "pending"])

    def test_no_parallel_resume_after_crash(self):
        data = q.seed(100)
        data["jobs"][0].update(phase="running", attempts=1)
        with mock.patch.object(q, "perform") as execute:
            self.assertEqual(q.progress(data, now=120), "INTERRUPTED_NEEDS_RECONCILIATION")
            execute.assert_not_called()
            self.assertEqual(data["jobs"][0]["phase"], "interrupted")

    def test_pilot_can_reconcile_only_verified_pass_after_crash(self):
        data = q.seed(100)
        data["jobs"][0]["phase"] = "completed"
        data["jobs"][1].update(phase="running", attempts=1)
        with mock.patch.object(q, "evidence_pass", return_value=(True, "PROOF")):
            self.assertEqual(q.progress(data, now=120), "RECOVERED_COMPLETED")
            self.assertEqual(data["jobs"][1]["phase"], "completed")

    def test_no_reconciliation_without_evidence(self):
        data = q.seed(100)
        data["jobs"][0]["phase"] = "completed"
        data["jobs"][1].update(phase="running", attempts=1)
        with mock.patch.object(q, "evidence_pass", return_value=(False, "NO")):
            self.assertEqual(q.progress(data, now=120), "INTERRUPTED_NEEDS_RECONCILIATION")
            self.assertEqual(data["jobs"][1]["phase"], "interrupted")

    def test_failed_task_blocks_remaining(self):
        data = q.seed(100)
        with mock.patch.object(q, "resource_gate", return_value=(True, "OK")), mock.patch.object(q, "perform", return_value=(False, "FAIL")):
            self.assertTrue(q.progress(data, now=101).startswith("BLOCKED_"))
            self.assertEqual(q.progress(data, now=102), "BLOCKED")
            self.assertEqual(data["jobs"][1]["phase"], "pending")

    def test_remote_evidence_rejects_local_only_forgery(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "fake.json"
            state.write_text(json.dumps({
                "issue": 5, "phase": "passed", "independent_tests": "PASS",
                "notified": True, "telegram_message_id": 100,
                "pull_request_number": 7,
                "pull_request": "https://github.com/stvelikiy-star/ai-prof-autonomy-pilot/pull/7",
                "commit": "a"*40,
            }))
            pr = {"state": "open", "draft": True, "merged": False,
                  "base": {"ref": "main"}, "head": {"sha": "b"*40}}
            files = [{"filename": x} for x in ("scripts/task_health.py", "tests/test_task_health.py")]
            results = [
                mock.Mock(returncode=0, stdout=json.dumps(pr)),
                mock.Mock(returncode=0, stdout=json.dumps(files)),
            ]
            with mock.patch.object(q, "ISSUE_STATE", state), mock.patch.object(q.subprocess, "run", side_effect=results):
                self.assertEqual(q.evidence_pass()[0], False)

    def test_remote_evidence_accepts_exact_draft_pr(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "realish.json"
            state.write_text(json.dumps({
                "issue": 5, "phase": "passed", "independent_tests": "PASS",
                "notified": True, "telegram_message_id": 100,
                "pull_request_number": 7,
                "pull_request": "https://github.com/stvelikiy-star/ai-prof-autonomy-pilot/pull/7",
                "commit": "a"*40,
            }))
            pr = {"state": "open", "draft": True, "merged": False,
                  "base": {"ref": "main"}, "head": {"sha": "a"*40}}
            files = [{"filename": x} for x in ("scripts/task_health.py", "tests/test_task_health.py")]
            results = [
                mock.Mock(returncode=0, stdout=json.dumps(pr)),
                mock.Mock(returncode=0, stdout=json.dumps(files)),
            ]
            with mock.patch.object(q, "ISSUE_STATE", state), mock.patch.object(q.subprocess, "run", side_effect=results):
                self.assertTrue(q.evidence_pass()[0])

    def test_worker_kills_process_group_on_timeout(self):
        child = mock.Mock()
        child.pid = 12345
        child.communicate.side_effect = [
            q.subprocess.TimeoutExpired("worker", 1), ("", "")
        ]
        with (mock.patch.object(q.subprocess, "Popen", return_value=child),
              mock.patch.object(q.os, "killpg") as kill):
            with self.assertRaises(q.subprocess.TimeoutExpired):
                q.run_worker_bounded()
        kill.assert_called_once_with(12345, q.signal.SIGKILL)
        self.assertEqual(child.communicate.call_count, 2)

    def test_worker_timeout_is_bounded(self):
        data = q.seed(100)
        with mock.patch.object(q, "resource_gate", return_value=(True, "OK")), mock.patch.object(q, "perform", side_effect=q.subprocess.TimeoutExpired("worker", 3)):
            self.assertTrue(q.progress(data, now=101).startswith("BLOCKED_"))
            self.assertEqual(data["jobs"][0]["result"], "WORKER_TIMEOUT")

    def test_terminal_no_requeue(self):
        data = q.seed(100)
        for x in data["jobs"]:
            x["phase"] = "completed"
            x["attempts"] = 1
        with mock.patch.object(q, "perform") as execute:
            self.assertEqual(q.progress(data, now=101), "ALL_JOBS_COMPLETED")
            execute.assert_not_called()

    def test_atomic_state_private(self):
        data = q.seed(100)
        q.persist(data)
        self.assertEqual(q.STATE.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(q.STATE.read_text())["version"], 1)


if __name__ == "__main__":
    unittest.main()
