from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from orchestrator.universal_task_lifecycle import (
    Authority,
    AuthorityBinding,
    EvidenceBinding,
    LifecycleAction,
    LifecycleSnapshot,
    LifecycleState,
    TaskIdentity,
    apply_transition,
)
from orchestrator.universal_task_lifecycle_store import (
    InMemoryLifecycleStore,
    LifecycleIdentityMismatch,
)


MODULE_PATH = Path(__file__).resolve().parents[1] / "orchestrator" / "control_loop.py"
SPEC = importlib.util.spec_from_file_location("ai_prof_control_loop", MODULE_PATH)
loop = importlib.util.module_from_spec(SPEC)
if SPEC.loader is None:
    raise RuntimeError("Cannot load control_loop")
sys.modules[SPEC.name] = loop
SPEC.loader.exec_module(loop)

SERVICE_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "orchestrator" / "control_loop_service.py"
)
SERVICE_SPEC = importlib.util.spec_from_file_location(
    "ai_prof_control_loop_service", SERVICE_MODULE_PATH
)
service = importlib.util.module_from_spec(SERVICE_SPEC)
if SERVICE_SPEC.loader is None:
    raise RuntimeError("Cannot load control_loop_service")
sys.modules[SERVICE_SPEC.name] = service
with mock.patch.dict(sys.modules, {"control_loop": loop}):
    SERVICE_SPEC.loader.exec_module(service)


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
            self.assertEqual(
                seen,
                ["operations", "stage_01a", "auto_repair", "codex_stage_01b", "codex"],
            )
            self.assertEqual(maximum, 1)

    def test_infrastructure_result_stops_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = loop.build_paths(Path(tmp))
            with mock.patch.object(loop, "run_child", side_effect=[124, 0, 0, 0, 0]) as run:
                self.assertEqual(loop.run_cycle(paths, 1), 124)
            self.assertEqual(run.call_count, 1)

    def test_task_failure_continues_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = loop.build_paths(Path(tmp))
            with mock.patch.object(loop, "run_child", side_effect=[1, 1, 1, 1, 0]) as run:
                self.assertEqual(loop.run_cycle(paths, 1), 0)
            self.assertEqual(run.call_count, 5)

    def test_slice1_does_not_add_publisher_pr_or_merge_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = loop.build_paths(Path(tmp))
            stages = []

            def fake_run(_paths, stage, _argv, _timeout):
                stages.append(stage)
                return 0

            with mock.patch.object(loop, "run_child", side_effect=fake_run):
                self.assertEqual(loop.run_cycle(paths, 1), 0)
            self.assertEqual(
                stages,
                ["operations", "stage_01a", "auto_repair", "codex_stage_01b", "codex"],
            )
            self.assertTrue({"publish", "publisher", "pr", "merge"}.isdisjoint(stages))

    def test_slice1_shadow_transition_has_no_lifecycle_side_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identity = TaskIdentity(
                "TASK-SHADOW",
                "ai-prof-control-center",
                "a" * 64,
                "b" * 40,
            )
            snapshot = LifecycleSnapshot(
                identity=identity,
                authority=(
                    AuthorityBinding(LifecycleAction.VALIDATE, Authority.AUTONOMOUS),
                ),
            )
            before = tuple(root.rglob("*"))
            advanced = apply_transition(
                snapshot,
                LifecycleState.REVIEW,
                evidence=(EvidenceBinding("task_validated", "c" * 64, identity.task_id),),
                expected_identity=identity,
            )
            self.assertEqual(advanced.state, LifecycleState.REVIEW)
            self.assertEqual(snapshot.state, LifecycleState.PENDING)
            self.assertEqual(tuple(root.rglob("*")), before)

    def test_slice2_store_preserves_slice1_identity_and_sha(self):
        identity = TaskIdentity(
            "TASK-DURABLE",
            "ai-prof-control-center",
            "d" * 64,
            "e" * 40,
        )
        snapshot = LifecycleSnapshot(
            identity=identity,
            authority=(
                AuthorityBinding(LifecycleAction.VALIDATE, Authority.AUTONOMOUS),
            ),
        )
        store = InMemoryLifecycleStore()
        with store.transaction() as transaction:
            created = transaction.compare_and_swap(
                identity.task_id, None, identity, snapshot
            )
        advanced = apply_transition(
            snapshot,
            LifecycleState.REVIEW,
            evidence=(
                EvidenceBinding("task_validated", "f" * 64, identity.task_id),
            ),
            expected_identity=identity,
        )
        with store.transaction() as transaction:
            updated = transaction.compare_and_swap(
                identity.task_id, created.version, identity, advanced
            )
        self.assertEqual(updated.version, created.version + 1)
        self.assertEqual(updated.identity, identity)

        changed_sha = TaskIdentity(
            identity.task_id,
            "ai-prof-control-center",
            "0" * 64,
            "e" * 40,
        )
        with self.assertRaises(LifecycleIdentityMismatch):
            with store.transaction() as transaction:
                transaction.compare_and_swap(
                    identity.task_id,
                    updated.version,
                    changed_sha,
                    LifecycleSnapshot(
                        identity=changed_sha,
                        authority=snapshot.authority,
                    ),
                )

    def test_slice2_shadow_observation_is_optional_and_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = loop.build_paths(Path(tmp))
            with mock.patch.object(service, "_shadow_observation") as observation:
                self.assertFalse(service._observe_lifecycle_shadow(paths, None))
            observation.assert_not_called()

            class Observer:
                def __init__(self):
                    self.seen = []

                def observe(self, observation):
                    self.seen.append(observation)
                    return {"authority": "autonomous"}

            observer = Observer()
            for queue_name, count in (("pending", 2), ("completed", 5)):
                queue = Path(tmp) / "queue" / queue_name
                for index in range(count):
                    (queue / f"task-{index}.md").write_text("task", encoding="utf-8")
            paths.lock.write_text("fixture\n", encoding="utf-8")
            paths.heartbeat.write_text(
                json.dumps({"state": "running", "stage": "stage_01b"}),
                encoding="utf-8",
            )
            with mock.patch.object(
                service.control_loop,
                "status",
                side_effect=AssertionError("mutating status probe called"),
            ) as status:
                self.assertTrue(
                    service._observe_lifecycle_shadow(paths, observer)
                )
            status.assert_not_called()
            self.assertEqual(len(observer.seen), 1)
            observation = observer.seen[0]
            self.assertIn(("completed", 5), observation.queues)
            self.assertIn(("pending", 2), observation.queues)
            self.assertTrue(observation.running)
            self.assertEqual(observation.heartbeat_stage, "stage_01b")
            self.assertFalse(hasattr(observation, "paths"))
            self.assertFalse(hasattr(observation, "authority"))
            with self.assertRaises(FrozenInstanceError):
                observation.paused = True

            observer.seen.clear()
            before = tuple(
                sorted(path.relative_to(Path(tmp)) for path in Path(tmp).rglob("*"))
            )
            self.assertTrue(service._observe_lifecycle_shadow(paths, observer))
            after = tuple(
                sorted(path.relative_to(Path(tmp)) for path in Path(tmp).rglob("*"))
            )
            self.assertEqual(after, before)
            self.assertEqual(len(observer.seen), 1)

    def test_slice2_shadow_failure_cannot_affect_control_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = loop.build_paths(Path(tmp))

            class BrokenObserver:
                def observe(self, _observation):
                    raise RuntimeError("shadow fixture failure")

            self.assertFalse(
                service._observe_lifecycle_shadow(paths, BrokenObserver())
            )
            with mock.patch.object(
                service,
                "_shadow_observation",
                side_effect=OSError("shadow state unavailable"),
            ):
                self.assertFalse(
                    service._observe_lifecycle_shadow(paths, BrokenObserver())
                )

            stop_event = threading.Event()
            stop_event.set()
            self.assertIsNone(
                service._dedicated_telegram_service_only(
                    paths,
                    stop_event,
                    shadow_observer=BrokenObserver(),
                )
            )

    def test_slice2_shadow_service_adapter_is_strictly_opt_in(self):
        original_bridge = service.control_loop.supervise_telegram_bridge
        original_commands = service.control_loop.child_commands
        with mock.patch.object(
            service.control_loop, "supervise_telegram_bridge", original_bridge
        ), mock.patch.object(
            service.control_loop, "child_commands", original_commands
        ), mock.patch.object(service.control_loop, "main", return_value=0):
            self.assertEqual(service.main(), 0)
            self.assertIs(
                service.control_loop.supervise_telegram_bridge,
                service._dedicated_telegram_service_only,
            )

            class Observer:
                def observe(self, _observation):
                    return None

            self.assertEqual(service.main(shadow_observer=Observer()), 0)
            self.assertIsNot(
                service.control_loop.supervise_telegram_bridge,
                service._dedicated_telegram_service_only,
            )

    def test_slice2_shadow_fails_closed_on_symlinked_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = loop.build_paths(root)

            class Observer:
                def observe(self, _observation):
                    self.called = True

            observer = Observer()
            observer.called = False
            target = root / "heartbeat-target.json"
            target.write_text('{"state":"running"}', encoding="utf-8")
            paths.heartbeat.symlink_to(target)
            self.assertFalse(service._observe_lifecycle_shadow(paths, observer))
            self.assertFalse(observer.called)

    def test_slice2_adds_no_ai_prof_publisher_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            with mock.patch.object(
                service, "_ORIGINAL_CHILD_COMMANDS", return_value=[]
            ):
                stages = [
                    stage
                    for stage, _argv in service._commands_with_publishers(root, runtime)
                ]
            self.assertNotIn("ai_prof_publisher", stages)
            self.assertNotIn("ai_prof_approved_publisher", stages)
            self.assertEqual(
                stages,
                [
                    "kol_approved_publisher_pre",
                    "ak_bermet_approved_publisher_pre",
                    "kol_approved_publisher_post",
                    "ak_bermet_approved_publisher_post",
                ],
            )

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
