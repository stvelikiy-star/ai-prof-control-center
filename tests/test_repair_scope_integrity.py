from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ORCHESTRATOR = Path(__file__).resolve().parents[1] / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

import repair_task_bridge as bridge


class RepairScopeIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        (self.project / "src").mkdir(parents=True)
        (self.project / "tests").mkdir()
        (self.project / "scripts").mkdir()
        (self.project / "supabase" / "migrations").mkdir(parents=True)
        (self.project / "automation" / "n8n").mkdir(parents=True)
        (self.project / "systemd").mkdir()
        (self.project / ".github" / "workflows").mkdir(parents=True)
        (self.project / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.project / "tests" / "test_app.py").write_text("def test_ok(): pass\n", encoding="utf-8")
        (self.project / "scripts" / "check-release.py").write_text("print('ok')\n", encoding="utf-8")
        (self.project / "package.json").write_text('{"scripts":{"test":"true"}}\n', encoding="utf-8")
        (self.project / "tsconfig.json").write_text("{}\n", encoding="utf-8")
        (self.project / "supabase" / "migrations" / "001.sql").write_text("select 1;\n", encoding="utf-8")
        (self.project / "automation" / "n8n" / "flow.json").write_text("{}\n", encoding="utf-8")
        (self.project / "systemd" / "demo.service").write_text("[Service]\n", encoding="utf-8")
        (self.project / ".github" / "workflows" / "ci.yml").write_text("name: ci\n", encoding="utf-8")
        self.config = {
            "path": str(self.project),
            "allowed_scope": [
                "src/**",
                "tests/**",
                "scripts/**",
                "package.json",
                "tsconfig.json",
                "supabase/migrations/**",
                "automation/n8n/**",
                "systemd/**",
                ".github/workflows/**",
            ],
            "max_scope_files": 20,
        }

    def test_safe_code_survives_while_test_and_authority_surfaces_are_removed(self):
        diagnosis = {
            "evidence": [
                {"source": "src/app.py"},
                {"source": "tests/test_app.py"},
                {"source": "scripts/check-release.py"},
                {"source": "package.json"},
                {"source": "tsconfig.json"},
                {"source": "supabase/migrations/001.sql"},
                {"source": "automation/n8n/flow.json"},
                {"source": "systemd/demo.service"},
                {"source": ".github/workflows/ci.yml"},
            ]
        }
        self.assertEqual(bridge._scope_from_evidence(self.config, diagnosis), ["src/app.py"])

    def test_only_test_or_toolchain_evidence_fails_closed(self):
        for source in (
            "tests/test_app.py",
            "scripts/check-release.py",
            "package.json",
            "tsconfig.json",
            "supabase/migrations/001.sql",
            "automation/n8n/flow.json",
            "systemd/demo.service",
            ".github/workflows/ci.yml",
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(
                    bridge.RepairBridgeError,
                    "no existing safe allowlisted code evidence path",
                ):
                    bridge._scope_from_evidence(self.config, {"evidence": [{"source": source}]})

    def test_repair_team_authority_files_are_never_inferred_from_evidence(self):
        for source in (
            "orchestrator/config.json",
            "orchestrator/projects.json",
            "orchestrator/project_test_contracts.json",
            "orchestrator/project_recovery_contracts.json",
            "orchestrator/repair_operation_bindings.json",
            "orchestrator/repair_policies.json",
            "orchestrator/repair_runbooks.json",
            "orchestrator/operations_runner.py",
            "orchestrator/release_flow.py",
            "orchestrator/submit_task.py",
            "orchestrator/control_loop.py",
            "scripts/activate_repair_team_v1.py",
        ):
            self.assertFalse(bridge._automatic_repair_scope_allowed(source), source)


if __name__ == "__main__":
    unittest.main()
