from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "orchestrator" / "control_loop.py"
SPEC = importlib.util.spec_from_file_location("ai_prof_control_loop", MODULE_PATH)
loop = importlib.util.module_from_spec(SPEC)
if SPEC.loader is None:
    raise RuntimeError("Cannot load control_loop")
sys.modules[SPEC.name] = loop
SPEC.loader.exec_module(loop)


class ControlLoopTests(unittest.TestCase):
    def test_fixed_stage_order_and_one_child_at_a_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = loop.build_paths(Path(tmp))
            active = 0
            maximum = 0
            seen = []

            def fake_run(_paths, stage, _argv, _timeout):
                nonlocal active, maximum
                active += 1
                maximum = max(maximum, active)
                seen.append(stage)
                active -= 1
                return 0

            with mock.patch.object(loop, "run_child", side_effect=fake_run):
                self.assertEqual(loop.run_cycle(paths, 1), 0)
            self.assertEqual(seen, ["stage_01a", "claude", "codex"])
            self.assertEqual(maximum, 1)

    def test_infrastructure_result_stops_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = loop.build_paths(Path(tmp))
            with mock.patch.object(loop, "run_child", side_effect=[124, 0, 0]) as run:
                self.assertEqual(loop.run_cycle(paths, 1), 124)
            self.assertEqual(run.call_count, 1)

    def test_task_failure_continues_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = loop.build_paths(Path(tmp))
            with mock.patch.object(loop, "run_child", side_effect=[1, 1, 0]) as run:
                self.assertEqual(loop.run_cycle(paths, 1), 0)
            self.assertEqual(run.call_count, 3)

    def test_child_timeout_and_redacted_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = loop.build_paths(Path(tmp))
            with mock.patch.object(
                loop, "run_process_with_heartbeat",
                side_effect=loop.subprocess.TimeoutExpired(["child"], 1, output="TOKEN=hidden"),
            ):
                self.assertEqual(loop.run_child(paths, "test", ["child"], 1), 124)
            self.assertNotIn("hidden", paths.log.read_text(encoding="utf-8"))

    def test_status_lock_pause_and_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = loop.build_paths(root)
            (root / "queue/pending").mkdir(parents=True, exist_ok=True)
            (root / "queue/pending/task.md").write_text("task", encoding="utf-8")
            loop.atomic_write(paths.pause, "yes\n")
            lock = loop.acquire_supervisor_lock(paths.lock)
            try:
                state = loop.status(paths)
            finally:
                lock.close()
            self.assertTrue(state["running"])
            self.assertTrue(state["paused"])
            self.assertEqual(state["queues"]["pending"], 1)

    def test_atomic_heartbeat_is_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = loop.build_paths(Path(tmp))
            loop.write_heartbeat(paths, state="running", stage="claude")
            data = json.loads(paths.heartbeat.read_text(encoding="utf-8"))
            self.assertEqual(data["stage"], "claude")

    def test_runtime_activity_does_not_dirty_source_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            source = parent / "source"
            runtime = parent / "runtime"
            source.mkdir()
            loop.subprocess.run(["git", "init", "-q", source], check=True)
            (source / "tracked.txt").write_text("source\n", encoding="utf-8")
            loop.subprocess.run(["git", "-C", source, "add", "tracked.txt"], check=True)
            before = loop.subprocess.run(
                ["git", "-C", source, "status", "--porcelain"],
                check=True, capture_output=True, text=True,
            ).stdout
            paths = loop.build_paths(source, runtime)
            loop.write_heartbeat(paths, state="running")
            loop.atomic_write(paths.pause, "paused\n")
            self.assertEqual(loop.queue_counts(runtime)["pending"], 0)
            status = loop.subprocess.run(
                ["git", "-C", source, "status", "--porcelain"],
                check=True, capture_output=True, text=True,
            )
            self.assertEqual(status.stdout, before)

    def test_self_test(self):
        self.assertEqual(loop.run_self_test(), 0)


if __name__ == "__main__":
    unittest.main()
