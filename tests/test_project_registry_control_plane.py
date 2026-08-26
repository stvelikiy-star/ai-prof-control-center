from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / "orchestrator"
REGISTRY = ORCHESTRATOR / "projects.json"
WORKFLOW = ROOT / ".github" / "workflows" / "control-center-ci.yml"

sys.path.insert(0, str(ORCHESTRATOR))

PROJECT_REGISTRY_SPEC = importlib.util.spec_from_file_location(
    "control_plane_project_registry", ORCHESTRATOR / "project_registry.py"
)
if PROJECT_REGISTRY_SPEC is None or PROJECT_REGISTRY_SPEC.loader is None:
    raise RuntimeError("Cannot load project_registry.py")
project_registry = importlib.util.module_from_spec(PROJECT_REGISTRY_SPEC)
PROJECT_REGISTRY_SPEC.loader.exec_module(project_registry)

CLAUDE_RUNNER_SPEC = importlib.util.spec_from_file_location(
    "control_plane_claude_runner", ORCHESTRATOR / "claude_runner.py"
)
if CLAUDE_RUNNER_SPEC is None or CLAUDE_RUNNER_SPEC.loader is None:
    raise RuntimeError("Cannot load claude_runner.py")
claude_runner = importlib.util.module_from_spec(CLAUDE_RUNNER_SPEC)
sys.modules[CLAUDE_RUNNER_SPEC.name] = claude_runner
CLAUDE_RUNNER_SPEC.loader.exec_module(claude_runner)


class ProjectRegistryControlPlaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
        cls.projects = payload["projects"]
        cls.by_id = {project["project_id"]: project for project in cls.projects}

    def test_project_ids_are_unique(self):
        ids = [project["project_id"] for project in self.projects]
        self.assertEqual(len(ids), len(set(ids)))

    def test_enabled_code_checks_are_executable_by_stage01b(self):
        allowed = set(claude_runner.ALLOWED_COMMANDS)
        for project in self.projects:
            if project.get("enabled", True) is not True:
                continue
            for check in project.get("code_required_checks", []):
                with self.subTest(project=project["project_id"], check=check):
                    self.assertIn(check, allowed)

    def test_resort_os_uses_allowlisted_repository_owned_monorepo_contract(self):
        project = self.by_id["resort-os"]
        self.assertIs(project["enabled"], True)
        self.assertNotIn("disabled_reason", project)
        self.assertEqual(project["code_required_checks"], ["npm test"])
        self.assertIn("npm test", claude_runner.ALLOWED_COMMANDS)
        self.assertNotIn("package.json", project["allowed_scope"])
        self.assertNotIn("control-center-verify.mjs", project["allowed_scope"])
        self.assertIn("package.json", project["forbidden_scope"])
        self.assertIn("control-center-verify.mjs", project["forbidden_scope"])
        resolved = project_registry.project_for_task(ROOT, project["path"])
        self.assertEqual(resolved["project_id"], "resort-os")

    def test_ak_bermet_registry_contains_no_removed_hold_tests(self):
        checks = self.by_id["ak-bermet"]["code_required_checks"]
        joined = "\n".join(checks)
        self.assertNotIn("holds-availability.test.ts", joined)
        self.assertNotIn("availability-hold-security.contract.test.mjs", joined)
        self.assertEqual(
            checks,
            [
                "npm run lint",
                "npx tsc --noEmit --incremental false",
                "node --test --experimental-strip-types src/lib/inspection-rules.test.ts",
                "npm run build",
            ],
        )

    def test_control_center_ci_covers_main_and_general_work_branches(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("      - main\n", workflow)
        self.assertIn("      - 'fix/**'\n", workflow)
        self.assertIn("      - 'feature/**'\n", workflow)
        self.assertIn("pull_request:\n    branches:\n      - main\n", workflow)


if __name__ == "__main__":
    unittest.main()
