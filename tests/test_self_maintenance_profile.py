from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from orchestrator.universal_task_lifecycle import (
    AuditLifecycleAdapter,
    Authority,
    ExecuteLifecycleAdapter,
    LifecycleAction,
    StageRequest,
    TestLifecycleAdapter,
    intersect_authorities,
)
from orchestrator.universal_task_lifecycle_store import InMemoryLifecycleStore

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ROOT / "orchestrator" / "projects.json"
CONTEXT = ROOT / "agents" / "ai-prof-control-center"


def load_submit_task():
    module_path = ROOT / "orchestrator" / "submit_task.py"
    spec = importlib.util.spec_from_file_location("self_maintenance_submit_task", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load submit_task")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


submit_task = load_submit_task()


class SelfMaintenanceProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        payload = json.loads(PROJECTS.read_text(encoding="utf-8"))
        cls.project = next(
            item for item in payload["projects"]
            if item["project_id"] == "ai-prof-control-center"
        )

    def test_isolated_target_and_fail_closed_capabilities(self):
        self.assertEqual(
            self.project["path"],
            "/home/agent/projects/ai-prof-control-center-maintenance",
        )
        self.assertNotEqual(self.project["path"], "/home/agent/projects/ai-prof-control-center")
        self.assertEqual(self.project["base_branch"], "maintenance/base")
        self.assertEqual(self.project["allowed_base_branches"], ["maintenance/base"])
        for key in ("allow_commits", "allow_push", "allow_merge", "allow_deployment"):
            self.assertIs(self.project[key], False)

    def test_lifecycle_cannot_grant_publish_pr_or_merge_authority(self):
        for key in ("allow_commits", "allow_push", "allow_merge"):
            self.assertIs(self.project[key], False)

        declared = self.project.get("lifecycle_authority", {})
        self.assertIsInstance(declared, dict)
        capability_gate = {
            LifecycleAction.PUBLISH: self.project["allow_commits"]
            and self.project["allow_push"],
            LifecycleAction.PR: self.project["allow_push"],
            LifecycleAction.MERGE: self.project["allow_merge"],
        }
        for action, enabled in capability_gate.items():
            configured = declared.get(action.value, Authority.DENIED)
            self.assertIn(
                intersect_authorities(configured, Authority.AUTONOMOUS),
                (Authority.DENIED, Authority.OWNER_ONLY),
            )
            effective = intersect_authorities(
                configured,
                Authority.AUTONOMOUS if enabled else Authority.DENIED,
            )
            self.assertEqual(effective, Authority.DENIED, action.value)

    def test_slice2_store_exposes_no_release_or_production_capability(self):
        public_store_api = {
            name for name in dir(InMemoryLifecycleStore) if not name.startswith("_")
        }
        self.assertTrue(
            {
                "transaction",
                "transact_inbox_event",
                "ledger_entries",
                "projection_intents",
            }.issubset(public_store_api)
        )
        for forbidden in (
            "commit",
            "push",
            "merge",
            "deploy",
            "publish",
            "mutate_queue",
            "database",
            "secret",
            "systemd",
        ):
            self.assertNotIn(forbidden, public_store_api)

        service_source = (
            ROOT / "orchestrator" / "control_loop_service.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("ai_prof_approved_task_publisher", service_source)
        self.assertNotIn("allow_commits", service_source)
        self.assertNotIn("allow_push", service_source)
        self.assertNotIn("allow_merge", service_source)
        self.assertNotIn("allow_deployment", service_source)

    def test_slice3_contracts_expose_evidence_only_not_mutation_authority(self):
        self.assertTrue(hasattr(ExecuteLifecycleAdapter, "execute"))
        self.assertTrue(hasattr(TestLifecycleAdapter, "test"))
        self.assertTrue(hasattr(AuditLifecycleAdapter, "audit"))
        request_fields = set(StageRequest.__dataclass_fields__)
        self.assertEqual(request_fields, {"binding", "prior_evidence", "repair"})
        for forbidden in (
            "command",
            "shell",
            "github",
            "publish",
            "commit",
            "push",
            "merge",
            "deploy",
            "queue",
            "database",
            "secret",
            "systemd",
        ):
            self.assertNotIn(forbidden, request_fields)

        public_store_api = {
            name for name in dir(InMemoryLifecycleStore) if not name.startswith("_")
        }
        self.assertIn("stage_evidence", public_store_api)
        self.assertNotIn("mutate_queue", public_store_api)

    def test_core_authority_files_are_not_autonomous_scope(self):
        allowed = self.project["allowed_scope"]
        protected = {
            "orchestrator/config.json",
            "orchestrator/projects.json",
            "orchestrator/project_registry.py",
            "orchestrator/submit_task.py",
            "orchestrator/operation_profiles.py",
            "orchestrator/operations_runner.py",
            "orchestrator/release_flow.py",
            "orchestrator/claude_runner.py",
            "orchestrator/codex_runner.py",
            "orchestrator/codex_stage01b_runner.py",
            "orchestrator/control_loop.py",
        }
        self.assertTrue(protected.issubset(set(self.project["forbidden_scope"])))
        for path in protected:
            with self.assertRaises(submit_task.IntakeError):
                submit_task.validate_scope_path(ROOT, path, allowed)

    def test_mobile_control_surface_is_allowed(self):
        allowed = self.project["allowed_scope"]
        self.assertEqual(
            submit_task.validate_scope_path(
                ROOT, "orchestrator/telegram_bridge.py", allowed
            ),
            "orchestrator/telegram_bridge.py",
        )
        # Nonexistent leaf is allowed only when its parent already exists.
        self.assertEqual(
            submit_task.validate_scope_path(
                ROOT, "orchestrator/github_task_gateway.py", allowed
            ),
            "orchestrator/github_task_gateway.py",
        )

    def test_required_check_uses_existing_exact_allowlist(self):
        self.assertEqual(self.project["code_required_commands"], ["git", "python3"])
        self.assertEqual(self.project["code_required_checks"], ["python3 -m unittest"])

    def test_required_agent_context_is_complete(self):
        self.assertEqual(self.project["agent_context"], "agents/ai-prof-control-center")
        for name in (
            "SYSTEM_INSTRUCTIONS.md",
            "SOURCE_POLICY.md",
            "STATE.md",
            "APPROVAL_MATRIX.md",
            "DECISIONS.md",
        ):
            path = CONTEXT / name
            self.assertTrue(path.is_file(), f"missing self-maintenance context: {name}")
            self.assertTrue(path.read_text(encoding="utf-8").strip())

    def test_dry_run_task_contract_accepts_self_maintenance(self):
        with tempfile.TemporaryDirectory(prefix="ai-prof-self-maint-registry-") as tmp:
            fake_root = Path(tmp) / "control"
            fake_runtime = Path(tmp) / "runtime"
            maintenance = Path(tmp) / "maintenance"
            (fake_root / "orchestrator").mkdir(parents=True)
            context = fake_root / "agents" / "ai-prof-control-center"
            context.mkdir(parents=True)
            for name in (
                "SYSTEM_INSTRUCTIONS.md",
                "SOURCE_POLICY.md",
                "STATE.md",
                "APPROVAL_MATRIX.md",
                "DECISIONS.md",
            ):
                (context / name).write_text("test\n", encoding="utf-8")

            subprocess.run(["git", "init", "-q", maintenance], check=True)
            subprocess.run(["git", "-C", maintenance, "config", "user.email", "ci@example.invalid"], check=True)
            subprocess.run(["git", "-C", maintenance, "config", "user.name", "CI"], check=True)
            (maintenance / "orchestrator").mkdir()
            (maintenance / "orchestrator" / "telegram_bridge.py").write_text("# test\n", encoding="utf-8")
            subprocess.run(["git", "-C", maintenance, "add", "."], check=True)
            subprocess.run(["git", "-C", maintenance, "commit", "-q", "-m", "fixture"], check=True)
            subprocess.run(["git", "-C", maintenance, "branch", "-M", "maintenance/base"], check=True)

            project = dict(self.project)
            project["path"] = str(maintenance)
            payload = {"version": 1, "projects": [project]}
            (fake_root / "orchestrator" / "projects.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )

            env = os.environ.copy()
            env["AI_PROF_STATE_DIR"] = str(fake_runtime)
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "orchestrator" / "submit_task.py"),
                    "--root", str(fake_root),
                    "--state-root", str(fake_runtime),
                    "--json",
                    "create",
                    "--project", "ai-prof-control-center",
                    "--title", "Test mobile control maintenance",
                    "--instructions", "Update the explicitly scoped Telegram control surface safely.",
                    "--work-branch", "feature/self-maint-test",
                    "--scope", "orchestrator/telegram_bridge.py",
                    "--dry-run",
                ],
                env=env,
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            data = json.loads(result.stdout)
            self.assertEqual(data["queue"], "dry-run")
            self.assertIn("Owner-Approval-Required: yes", data["content"])
            self.assertIn(
                "Out-of-Scope: All files outside Scope-Files; commit, push, merge, deployment",
                data["content"],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
