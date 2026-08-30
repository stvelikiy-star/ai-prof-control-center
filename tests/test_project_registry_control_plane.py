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

import codex_stage01b_runner_v2


class ProjectRegistryControlPlaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
        cls.projects = payload["projects"]
        cls.by_id = {project["project_id"]: project for project in cls.projects}
        codex_stage01b_runner_v2.install_v2_required_check_allowlist()
        cls.stage01b_allowed = set(
            codex_stage01b_runner_v2.legacy.core.ALLOWED_COMMANDS
        )

    def test_project_ids_are_unique(self):
        ids = [project["project_id"] for project in self.projects]
        self.assertEqual(len(ids), len(set(ids)))

    def test_enabled_code_checks_are_executable_by_stage01b(self):
        for project in self.projects:
            if project.get("enabled", True) is not True:
                continue
            for check in project.get("code_required_checks", []):
                with self.subTest(project=project["project_id"], check=check):
                    self.assertIn(check, self.stage01b_allowed)

    def test_resort_os_uses_allowlisted_repository_owned_monorepo_contract(self):
        project = self.by_id["resort-os"]
        self.assertIs(project["enabled"], True)
        self.assertNotIn("disabled_reason", project)
        self.assertEqual(project["code_required_checks"], ["npm test"])
        self.assertIn("npm test", self.stage01b_allowed)
        self.assertNotIn("package.json", project["allowed_scope"])
        self.assertNotIn("control-center-verify.mjs", project["allowed_scope"])
        self.assertIn("package.json", project["forbidden_scope"])
        self.assertIn("control-center-verify.mjs", project["forbidden_scope"])
        resolved = project_registry.project_for_task(ROOT, project["path"])
        self.assertEqual(resolved["project_id"], "resort-os")

    def test_kol_registry_matches_current_checkout_shape(self):
        project = self.by_id["kol-travel-platform"]
        self.assertEqual(project["path"], "/home/agent/Загрузки/kol-travel-platform")
        self.assertIs(project["enabled"], True)
        self.assertEqual(
            project["allowed_scope"],
            [
                "README.md",
                "CHECK_LOCAL_RUN.md",
                "docs/**",
                "src/**",
                "supabase/**",
                "scripts/**",
                ".github/workflows/**",
                ".env.example",
                "eslint.config.mjs",
                "package.json",
                "postcss.config.js",
                "tailwind.config.ts",
                "tsconfig.json",
            ],
        )
        for stale in (
            "app/**",
            "components/**",
            "lib/**",
            "public/**",
            "tests/**",
            "next.config.mjs",
            "next.config.js",
            "postcss.config.mjs",
            "middleware.ts",
            "vercel.json",
        ):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, project["allowed_scope"])
        self.assertEqual(
            project["code_required_checks"],
            [
                "npm run lint",
                "npx tsc --noEmit --incremental false",
                "npm run check:release-source",
                "npm run build",
            ],
        )
        self.assertFalse(project["allow_commits"])
        self.assertFalse(project["allow_push"])
        self.assertFalse(project["allow_merge"])
        self.assertFalse(project["allow_deployment"])

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
                "npm run test:inspection",
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
