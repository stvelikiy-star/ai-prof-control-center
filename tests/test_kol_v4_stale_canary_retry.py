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

import retry_kol_v4_runtime_failure as first
import retry_kol_v4_stale_canary_failure as second

TASK_ID = "KOL_TRAVEL_PLATFORM_20260830T142940Z_C13938"


def run_git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    ).stdout.strip()


def task_text(*, attempt: int = 1, include_second: bool = False) -> str:
    lines = [
        f"Task-ID: {TASK_ID}",
        "Execution-Mode: code",
        "Operation-Profile: none",
        f"Project-Path: {first.KOL_PROJECT}",
        "Base-Branch: main",
        "Work-Branch: feature/chatgpt-issue-172",
        "Agent-Context: agents/kol",
        "Goal: Harden deployment safety self-test diagnostics",
        f"Required-Checks: {first.EXPECTED_REQUIRED_CHECKS}",
        "Scope-Files: scripts/check-deployment-env-selftest.mjs",
        "Publication-Contract-Version: 4",
        "Publication-Action: pull-request",
        "Publication-Source-Issue: 172",
        "Publication-Repository: stvelikiy-star/kol-travel-platform",
        "Publication-Allowed-Actions: code-edit, commit, pull-request, push, tests",
        "Publication-Forbidden-Actions: database-mutation, deployment, destructive-operations, merge, other-project-access, payment-activation, production-change, scope-widening, secrets, supabase-restore",
        "Publication-Contract-Digest: " + "a" * 64,
        f"Retry-Attempt: {attempt}",
        f"Retry-Reason: {second.FIRST_RETRY_REASON}",
        "Retry-Previous-Failure-SHA256: " + "b" * 64,
        "Retry-Evidence-Backup: /tmp/first-retry-evidence",
    ]
    if include_second:
        lines.append(f"Retry-Second-Reason: {second.SECOND_RETRY_REASON}")
    lines.append(
        "Failure-Reason: CODEX_STAGE01B_FAILED | CodexExecutionError: CODEX_STAGE01B_FAILED: "
        + first.EXPECTED_FAILURE_FRAGMENT
    )
    return "\n".join(lines) + "\n"


class KolV4StaleCanaryRetryTests(unittest.TestCase):
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
        for queue in (
            "pending", "active", "review", "pending_codex", "approved",
            "completed", "blocked", "failed", "cancelled",
        ):
            (runtime / "queue" / queue).mkdir(parents=True, exist_ok=True)
        run = runtime / "run"
        run.mkdir(parents=True)
        (run / "paused").write_text("paused\n", encoding="utf-8")
        (run / "heartbeat.json").write_text(
            json.dumps({"state": "paused"}) + "\n", encoding="utf-8"
        )
        task = runtime / "queue" / "failed" / f"{TASK_ID}.md"
        task.write_text(text, encoding="utf-8")
        logs = runtime / "logs" / "orchestrator"
        logs.mkdir(parents=True)
        (logs / f"{TASK_ID}-01B-20260830T163838Z.log").write_text(
            "CODEX_STAGE01B_FAILED\nCodexExecutionError: CODEX_STAGE01B_FAILED: "
            + first.EXPECTED_FAILURE_FRAGMENT + "\n",
            encoding="utf-8",
        )
        return runtime, task

    def make_canary_backup(self, root: Path) -> Path:
        backup = root / "canary-backup"
        backup.mkdir()
        text = (
            "[Service]\n"
            f"WorkingDirectory={second.STALE_WORKDIR}\n"
            "ExecStart=\n"
            f"ExecStart=/usr/bin/python3 {second.STALE_EXEC} --daemon\n"
        )
        disabled = backup / "90-night-watch-canary.conf.disabled"
        disabled.write_text(text, encoding="utf-8")
        digest = __import__("hashlib").sha256(disabled.read_bytes()).hexdigest()
        (backup / "metadata.txt").write_text(
            "reason=stale systemd canary overrode canonical Control Center ExecStart\n"
            f"sha256={digest}\n",
            encoding="utf-8",
        )
        return backup

    def test_second_retry_is_prepared_once_with_both_evidence_chains(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self.make_project(root)
            original = task_text()
            runtime, failed = self.make_runtime(root, original)
            canary = self.make_canary_backup(root)
            inactive = root / "inactive-dropin.conf"

            result = second.retry_task(
                runtime, TASK_ID, canary,
                project=project, active_dropin=inactive,
            )

            pending = runtime / "queue" / "pending" / failed.name
            self.assertEqual(result["retry_attempt"], "2")
            self.assertTrue(pending.is_file())
            self.assertFalse(failed.exists())
            updated = pending.read_text(encoding="utf-8")
            self.assertIn("Retry-Attempt: 2", updated)
            self.assertIn(
                f"Retry-Second-Reason: {second.SECOND_RETRY_REASON}", updated
            )
            self.assertIn("Retry-Stale-Canary-Backup:", updated)
            self.assertNotIn("Failure-Reason:", updated)
            self.assertIn(
                f"Retry-Reason: {second.FIRST_RETRY_REASON}", updated
            )
            backup = Path(result["backup"])
            self.assertEqual(
                (backup / failed.name).read_text(encoding="utf-8"), original
            )
            self.assertTrue((backup / "SHA256SUMS").is_file())

    def test_active_stale_dropin_blocks_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self.make_project(root)
            runtime, failed = self.make_runtime(root, task_text())
            canary = self.make_canary_backup(root)
            active = root / "90-night-watch-canary.conf"
            active.write_text("active\n", encoding="utf-8")
            with self.assertRaises(second.StaleCanaryRetryError):
                second.retry_task(
                    runtime, TASK_ID, canary,
                    project=project, active_dropin=active,
                )
            self.assertTrue(failed.is_file())

    def test_wrong_or_already_second_attempt_is_rejected(self):
        with self.assertRaises(second.StaleCanaryRetryError):
            second.validate_second_failure(task_text(attempt=2), TASK_ID)
        with self.assertRaises(second.StaleCanaryRetryError):
            second.validate_second_failure(
                task_text(include_second=True), TASK_ID
            )

    def test_missing_canary_backup_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(second.StaleCanaryRetryError):
                second.validate_stale_canary_evidence(
                    root / "missing",
                    active_dropin=root / "inactive",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
