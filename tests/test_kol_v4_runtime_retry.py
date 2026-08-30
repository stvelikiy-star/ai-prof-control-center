from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / "orchestrator"
if str(ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR))

import retry_kol_v4_runtime_failure as retry

TASK_ID = "KOL_TRAVEL_PLATFORM_20260830T142940Z_C13938"


def run_git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=project,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()


def task_text(*, failure: str | None = None) -> str:
    reason = failure or (
        "CODEX_STAGE01B_FAILED | CodexExecutionError: CODEX_STAGE01B_FAILED: "
        + retry.EXPECTED_FAILURE_FRAGMENT
    )
    return "\n".join(
        [
            f"Task-ID: {TASK_ID}",
            "Execution-Mode: code",
            "Operation-Profile: none",
            f"Project-Path: {retry.KOL_PROJECT}",
            "Base-Branch: main",
            "Work-Branch: feature/chatgpt-issue-172",
            "Agent-Context: agents/kol",
            "Goal: Harden deployment safety self-test diagnostics",
            f"Required-Checks: {retry.EXPECTED_REQUIRED_CHECKS}",
            "Scope-Files: scripts/check-deployment-env-selftest.mjs",
            "Publication-Contract-Version: 4",
            "Publication-Action: pull-request",
            "Publication-Source-Issue: 172",
            "Publication-Repository: stvelikiy-star/kol-travel-platform",
            "Publication-Allowed-Actions: code-edit, commit, pull-request, push, tests",
            "Publication-Forbidden-Actions: database-mutation, deployment, destructive-operations, merge, other-project-access, payment-activation, production-change, scope-widening, secrets, supabase-restore",
            "Publication-Contract-Digest: " + "a" * 64,
            f"Failure-Reason: {reason}",
            "",
        ]
    )


class KolV4RuntimeRetryTests(unittest.TestCase):
    def make_project(self, root: Path) -> Path:
        project = root / "project"
        project.mkdir()
        run_git(project, "init", "-q", "-b", "main")
        run_git(project, "config", "user.name", "test")
        run_git(project, "config", "user.email", "test@example.invalid")
        (project / "README.md").write_text("base\n", encoding="utf-8")
        run_git(project, "add", "README.md")
        run_git(project, "commit", "-qm", "base")
        run_git(project, "update-ref", "refs/remotes/origin/main", "HEAD")
        return project

    def make_runtime(self, root: Path, text: str) -> tuple[Path, Path]:
        runtime = root / "state"
        failed = runtime / "queue" / "failed"
        failed.mkdir(parents=True)
        for queue in (
            "pending", "active", "review", "pending_codex", "approved",
            "completed", "blocked", "cancelled",
        ):
            (runtime / "queue" / queue).mkdir(parents=True)
        run = runtime / "run"
        run.mkdir(parents=True)
        (run / "paused").write_text("paused\n", encoding="utf-8")
        (run / "heartbeat.json").write_text(
            json.dumps({"state": "paused"}) + "\n",
            encoding="utf-8",
        )
        task = failed / f"{TASK_ID}.md"
        task.write_text(text, encoding="utf-8")
        logs = runtime / "logs" / "orchestrator"
        logs.mkdir(parents=True)
        (logs / f"{TASK_ID}-01B-20260830T153145Z.log").write_text(
            "CODEX_STAGE01B_FAILED\n"
            "CodexExecutionError: CODEX_STAGE01B_FAILED: "
            + retry.EXPECTED_FAILURE_FRAGMENT
            + "\n",
            encoding="utf-8",
        )
        return runtime, task

    def test_exact_live_failure_is_requeued_once_with_private_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self.make_project(root)
            original = task_text()
            runtime, failed = self.make_runtime(root, original)

            before = {
                name: retry.field(original, name)
                for name in retry.CRITICAL_FIELDS
            }
            result = retry.retry_task(runtime, TASK_ID, project=project)

            pending = runtime / "queue" / "pending" / failed.name
            self.assertEqual(result["queue"], "pending")
            self.assertFalse(failed.exists())
            self.assertTrue(pending.is_file())
            updated = pending.read_text(encoding="utf-8")
            self.assertNotIn("Failure-Reason:", updated)
            self.assertIn("Retry-Attempt: 1", updated)
            self.assertIn(
                "Retry-Reason: stage01b-required-check-runtime-repaired",
                updated,
            )
            after = {
                name: retry.field(updated, name)
                for name in retry.CRITICAL_FIELDS
            }
            self.assertEqual(after, before)

            backup = Path(result["backup"])
            self.assertTrue((backup / failed.name).is_file())
            self.assertEqual(
                (backup / failed.name).read_text(encoding="utf-8"),
                original,
            )
            self.assertTrue((backup / "SHA256SUMS").is_file())
            self.assertTrue(
                any(path.name.endswith("-01B-20260830T153145Z.log") for path in backup.iterdir())
            )

            with self.assertRaises(retry.RetryError):
                retry.retry_task(runtime, TASK_ID, project=project)

    def test_wrong_failure_class_is_rejected_without_queue_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self.make_project(root)
            runtime, failed = self.make_runtime(
                root,
                task_text(failure="CODEX_STAGE01B_FAILED | check failed: npm run build"),
            )
            with self.assertRaises(retry.RetryError):
                retry.retry_task(runtime, TASK_ID, project=project)
            self.assertTrue(failed.is_file())
            self.assertFalse(
                (runtime / "queue" / "pending" / failed.name).exists()
            )

    def test_dirty_or_non_main_project_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self.make_project(root)
            runtime, failed = self.make_runtime(root, task_text())
            (project / "README.md").write_text("dirty\n", encoding="utf-8")
            with self.assertRaises(retry.RetryError):
                retry.retry_task(runtime, TASK_ID, project=project)
            self.assertTrue(failed.is_file())

    def test_retry_contract_rejects_existing_retry_marker(self):
        text = task_text() + "Retry-Attempt: 1\n"
        with self.assertRaises(retry.RetryError):
            retry.validate_failed_task(text, TASK_ID)


if __name__ == "__main__":
    unittest.main(verbosity=2)
