from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orchestrator import ai_prof_approved_task_publisher_gate as legacy
from orchestrator import ai_prof_approved_task_publisher_gate_v2 as gate
from orchestrator import control_loop_service_night as night_service


class AIProfNightTerminalReconcilerTests(unittest.TestCase):
    def setUp(self):
        self.task_id = "AI_PROF_CONTROL_CENTER_TEST"
        self.work_branch = "feature/chatgpt-issue-125"
        self.scope = ("reports/evidence.md",)
        self.base_sha = "a" * 40
        self.commit_sha = "c" * 40
        self.task = {
            "task_id": self.task_id,
            "lifecycle_state": "APPROVED",
            "project_id": legacy.PROJECT_ID,
            "project_path": legacy.PROJECT_PATH,
            "base_branch": legacy.BASE_BRANCH,
            "work_branch": self.work_branch,
            "agent_context": legacy.AGENT_CONTEXT,
            "scope_files": list(self.scope),
            "source": {
                "kind": "github_issue",
                "repository": legacy.SOURCE_REPOSITORY,
                "issue": 125,
            },
        }
        self.authorization = legacy.CommitAuthorization(
            self.task_id,
            self.work_branch,
            self.base_sha,
            self.scope,
        )

    def _common_patches(self):
        return (
            mock.patch.object(gate.legacy, "_load_profile", return_value={}),
            mock.patch.object(gate.legacy, "_select_approved_commit_task", return_value=self.task),
            mock.patch.object(
                gate.legacy,
                "_validate_task",
                return_value=(self.task_id, self.work_branch, self.scope),
            ),
            mock.patch.object(gate.legacy, "_validate_profile"),
            mock.patch.object(gate.legacy, "_verify_stage_evidence"),
            mock.patch.object(gate.legacy, "_repository_base_sha", return_value=self.base_sha),
        )

    def test_stale_exact_commit_is_terminalized_without_checkout_mutation(self):
        patches = self._common_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             mock.patch.object(gate.legacy, "_git", return_value="feature/chatgpt-issue-133"), \
             mock.patch.object(gate, "_exact_commit_from_branch", return_value=self.commit_sha) as exact, \
             mock.patch.object(gate, "_complete_queue_task") as complete, \
             mock.patch.object(gate.legacy, "commit_approved_change") as commit, \
             mock.patch.object(gate, "_restore_base_if_owned") as restore:
            decision = gate.run_once(Path("/root"), Path("/state"))

        self.assertEqual(decision.decision, "COMPLETED")
        self.assertEqual(decision.commit_sha, self.commit_sha)
        self.assertTrue(decision.committed)
        self.assertTrue(decision.complete)
        self.assertFalse(decision.published)
        self.assertEqual(
            decision.reason,
            "stale_exact_commit_terminalized_without_checkout_mutation",
        )
        exact.assert_called_once()
        complete.assert_called_once_with(Path("/state"), self.task_id)
        commit.assert_not_called()
        restore.assert_not_called()

    def test_owned_task_commit_restores_base_before_terminal_queue_move(self):
        patches = self._common_patches()
        order: list[str] = []

        def restore(project, authorization):
            self.assertEqual(authorization, self.authorization)
            order.append("restore")

        def complete(state_root, task_id):
            self.assertEqual(state_root, Path("/state"))
            self.assertEqual(task_id, self.task_id)
            order.append("complete")
            return Path("/state/queue/completed") / f"{task_id}.md"

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             mock.patch.object(gate.legacy, "_git", return_value=self.work_branch), \
             mock.patch.object(gate.legacy, "commit_approved_change", return_value=self.commit_sha) as commit, \
             mock.patch.object(gate, "_restore_base_if_owned", side_effect=restore), \
             mock.patch.object(gate, "_complete_queue_task", side_effect=complete):
            decision = gate.run_once(Path("/root"), Path("/state"))

        self.assertEqual(decision.decision, "COMPLETED")
        self.assertEqual(order, ["restore", "complete"])
        commit.assert_called_once_with(Path(legacy.PROJECT_PATH), self.authorization)

    def test_nonmatching_checkout_without_exact_commit_fails_closed(self):
        patches = self._common_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             mock.patch.object(gate.legacy, "_git", return_value="feature/chatgpt-issue-133"), \
             mock.patch.object(gate, "_exact_commit_from_branch", return_value=None), \
             mock.patch.object(gate, "_complete_queue_task") as complete:
            decision = gate.run_once(Path("/root"), Path("/state"))

        self.assertEqual(decision.decision, "BLOCKED")
        self.assertEqual(decision.reason, "work_branch_mismatch")
        complete.assert_not_called()

    def test_atomic_completion_uses_existing_safe_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            approved = state / "queue/approved"
            approved.mkdir(parents=True)
            source = approved / f"{self.task_id}.md"
            source.write_text("Task-ID: test\n", encoding="utf-8")
            moved = gate._complete_queue_task(state, self.task_id)
            self.assertEqual(moved, state / "queue/completed" / source.name)
            self.assertFalse(source.exists())
            self.assertTrue(moved.is_file())

    def test_single_flight_guard_detects_maintenance_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            review = runtime / "queue/review"
            review.mkdir(parents=True)
            (review / "TASK.md").write_text(
                "Execution-Mode: code\n"
                f"Project-Path: {night_service.MAINTENANCE_PROJECT_PATH}\n",
                encoding="utf-8",
            )
            self.assertTrue(night_service._maintenance_code_task_in_flight(runtime))

    def test_single_flight_guard_ignores_other_project_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            review = runtime / "queue/review"
            review.mkdir(parents=True)
            (review / "TASK.md").write_text(
                "Execution-Mode: code\n"
                "Project-Path: /home/agent/projects/other\n",
                encoding="utf-8",
            )
            self.assertFalse(night_service._maintenance_code_task_in_flight(runtime))

    def test_night_heartbeat_replaces_stale_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            heartbeat = Path(tmp) / "heartbeat.json"
            heartbeat.write_text(
                json.dumps(
                    {
                        "pid": 1723511,
                        "state": "idle",
                        "stage": None,
                        "timestamp": "old",
                        "consecutive_failures": 0,
                    }
                ) + "\n",
                encoding="utf-8",
            )
            paths = mock.Mock()
            paths.heartbeat = heartbeat
            with mock.patch.object(night_service.os, "getpid", return_value=113359), \
                 mock.patch.object(
                     night_service.base.control_loop,
                     "utc_now",
                     return_value="2026-08-27T14:53:29+00:00",
                 ):
                night_service._night_write_heartbeat(
                    paths,
                    state="running",
                    stage="operations",
                )
            payload = json.loads(heartbeat.read_text(encoding="utf-8"))
            self.assertEqual(payload["pid"], 113359)
            self.assertEqual(payload["state"], "running")
            self.assertEqual(payload["stage"], "operations")
            self.assertEqual(payload["timestamp"], "2026-08-27T14:53:29+00:00")

    def test_night_service_replaces_gate_and_skips_stage01a_during_maintenance_flight(self):
        root = Path("/repo")
        established = [
            ("kol_approved_publisher_pre", ["kol-pre"]),
            ("ak_bermet_approved_publisher_pre", ["ak-pre"]),
            ("operations", ["operations"]),
            ("stage_01a", ["stage-01a"]),
            ("codex", ["codex"]),
            ("kol_approved_publisher_post", ["kol-post"]),
            ("ak_bermet_approved_publisher_post", ["ak-post"]),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            review = runtime / "queue/review"
            review.mkdir(parents=True)
            (review / "TASK.md").write_text(
                "Execution-Mode: code\n"
                f"Project-Path: {night_service.MAINTENANCE_PROJECT_PATH}\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                night_service.base,
                "_commands_with_publishers",
                return_value=established,
            ), mock.patch.object(
                night_service.base,
                "_publisher_argv",
                return_value=["python", "ai_prof_approved_task_publisher_gate_v2.py"],
            ) as argv:
                commands = night_service._commands_with_night_safe_ai_prof_gate(root, runtime)

        argv.assert_called_once_with(
            root,
            runtime,
            "ai_prof_approved_task_publisher_gate_v2.py",
        )
        stages = [stage for stage, _ in commands]
        self.assertNotIn("stage_01a", stages)
        self.assertIn("operations", stages)
        self.assertIn("codex", stages)
        self.assertEqual(stages[0], "kol_approved_publisher_pre")
        self.assertEqual(stages[1], "ak_bermet_approved_publisher_pre")
        self.assertEqual(stages[2], "ai_prof_approved_publisher_pre")
        self.assertEqual(stages[-1], "ai_prof_approved_publisher_post")

    def test_night_service_keeps_stage01a_when_no_maintenance_task_in_flight(self):
        root = Path("/repo")
        runtime = Path("/state")
        established = [
            ("kol_approved_publisher_pre", ["kol-pre"]),
            ("ak_bermet_approved_publisher_pre", ["ak-pre"]),
            ("operations", ["operations"]),
            ("stage_01a", ["stage-01a"]),
            ("kol_approved_publisher_post", ["kol-post"]),
            ("ak_bermet_approved_publisher_post", ["ak-post"]),
        ]
        with mock.patch.object(
            night_service.base,
            "_commands_with_publishers",
            return_value=established,
        ), mock.patch.object(
            night_service.base,
            "_publisher_argv",
            return_value=["python", "ai_prof_approved_task_publisher_gate_v2.py"],
        ) as argv:
            commands = night_service._commands_with_night_safe_ai_prof_gate(root, runtime)

        argv.assert_called_once_with(
            root,
            runtime,
            "ai_prof_approved_task_publisher_gate_v2.py",
        )
        stages = [stage for stage, _ in commands]
        self.assertIn("stage_01a", stages)
        self.assertEqual(
            sum(1 for stage in stages if stage.startswith("ai_prof_approved_publisher_")),
            2,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
