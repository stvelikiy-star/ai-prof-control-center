from pathlib import Path
import json
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "orchestrator" / "codex_runner.py"
CONFIG = ROOT / "orchestrator" / "config.json"


class Stage01CMediaPolicyTests(unittest.TestCase):
    def test_trusted_stage01c_prompt_contains_media_boundary(self):
        text = RUNNER.read_text(encoding="utf-8")
        self.assertEqual(text.count("STAGE01C_MEDIA_EVIDENCE_POLICY_V2"), 1)
        self.assertIn("does not validate every JPEG coding table", text)
        self.assertIn("Binary validation cannot prove", text)

    def test_fix_cycle_budget_allows_bounded_continuation(self):
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(config.get("max_fix_cycles"), 7)
        policy = config.get("stage01c_media_evidence_policy", {})
        self.assertEqual(policy.get("version"), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
