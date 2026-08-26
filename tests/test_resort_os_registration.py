import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "orchestrator" / "projects.json"
AGENT = ROOT / "agents" / "resort-os"


class ResortOsRegistrationTests(unittest.TestCase):
    def setUp(self):
        payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
        matches = [p for p in payload["projects"] if p.get("project_id") == "resort-os"]
        self.assertEqual(len(matches), 1)
        self.project = matches[0]

    def test_identity_and_branch_contract(self):
        self.assertEqual(self.project["path"], "/home/agent/projects/resort-os")
        self.assertIs(self.project["enabled"], True)
        self.assertNotIn("disabled_reason", self.project)
        self.assertEqual(self.project["base_branch"], "main")
        self.assertEqual(self.project["allowed_base_branches"], ["main"])
        self.assertEqual(self.project["agent_context"], "agents/resort-os")

    def test_no_privileged_authority(self):
        for key in ("allow_commits", "allow_push", "allow_merge", "allow_deployment"):
            self.assertIs(self.project[key], False, key)
        self.assertTrue(self.project["require_clean_repository"])
        self.assertLessEqual(self.project["max_scope_files"], 20)

    def test_monorepo_scope_matches_real_repository_shape(self):
        allowed = set(self.project["allowed_scope"])
        for path in (
            "apps/**",
            "services/**",
            "packages/**",
            "scripts/**",
            "automation/**",
            "data-intake/**",
            "compose.yaml",
            "compose.production.yaml",
        ):
            self.assertIn(path, allowed)

        for stale in ("src/**", "app/**", "components/**", "lib/**", "supabase/**"):
            self.assertNotIn(stale, allowed)

    def test_canonical_recovery_and_verification_boundaries(self):
        allowed = set(self.project["allowed_scope"])
        forbidden = set(self.project["forbidden_scope"])

        self.assertIn("knowledge/04_CURRENT_STATE.md", allowed)
        self.assertIn("knowledge/05_DECISIONS_AND_BACKLOG.md", allowed)

        for path in (
            "knowledge/00_PRODUCT_BIBLE.md",
            "knowledge/01_DOMAIN_BUSINESS_RULES.md",
            "knowledge/02_SYSTEM_ARCHITECTURE.md",
            "knowledge/03_AI_ADMIN.md",
            "knowledge/06_THREE_CROWNS_MASTER_SPEC.md",
            "knowledge/07_EXECUTION_PLAN_THREE_CROWNS.md",
            "knowledge/08_CLIENT_AUTOMATION_N8N_BOUNDARY.md",
            "recovery-artifacts/**",
            ".github/workflows/**",
            "package.json",
            "control-center-verify.mjs",
        ):
            self.assertIn(path, forbidden)
            self.assertNotIn(path, allowed)

    def test_required_agent_context_exists(self):
        required = (
            "SYSTEM_INSTRUCTIONS.md",
            "SOURCE_POLICY.md",
            "STATE.md",
            "APPROVAL_MATRIX.md",
            "DECISIONS.md",
        )
        for name in required:
            self.assertTrue((AGENT / name).is_file(), name)

    def test_repository_owned_check_is_exact_and_minimal(self):
        self.assertEqual(self.project["code_required_checks"], ["npm test"])
        self.assertIn("python3", self.project["code_required_commands"])
        self.assertIn("node", self.project["code_required_commands"])
        self.assertIn("npm", self.project["code_required_commands"])


if __name__ == "__main__":
    unittest.main()
