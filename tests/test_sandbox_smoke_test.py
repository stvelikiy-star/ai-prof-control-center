from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts" / "sandbox_smoke_test.py"
RUNNER = ROOT / "orchestrator" / "claude_runner.py"


class SandboxSmokeTestContract(unittest.TestCase):
    def test_smoke_test_imports_and_uses_production_builder(self):
        text = SMOKE.read_text(encoding="utf-8")
        self.assertIn('RUNNER = ROOT / "orchestrator" / "claude_runner.py"', text)
        self.assertIn("cr.build_bwrap_argv(", text)
        self.assertNotIn('["bwrap"', text)
        self.assertNotIn("shell=True", text)

    def test_required_stages_and_exact_online_marker_are_present(self):
        text = SMOKE.read_text(encoding="utf-8")
        for stage in (
            "true", "git-version", "python-version", "node-version",
            "npm-version", "npx-version",
            "scope-write-remove", "host-isolation", "online-agent",
            "online-exact-response", "repository-clean-after",
        ):
            self.assertIn(stage, text)
        self.assertIn('EXPECTED = "AI_PROF_SANDBOX_OK"', text)
        self.assertIn(
            'ONLINE_REQUEST = "Reply with exactly AI_PROF_SANDBOX_OK and nothing else."',
            text,
        )

    def test_true_stage_deterministically_ends_in_true_and_never_claude(self):
        text = SMOKE.read_text(encoding="utf-8")
        self.assertIn('assert argv[-1] == "/bin/true"', text)
        self.assertIn("stage true must never mount or execute Claude", text)

    def test_no_host_root_bind_or_unsandboxed_agent_fallback(self):
        runner = RUNNER.read_text(encoding="utf-8")
        smoke = SMOKE.read_text(encoding="utf-8")
        self.assertNotIn('["--ro-bind", "/", "/"]', runner)
        self.assertNotIn('["--bind", "/", "/"]', runner)
        self.assertEqual(smoke.count("subprocess.run("), 3)
        self.assertIn("argv = cr.build_bwrap_argv(", smoke)


if __name__ == "__main__":
    unittest.main()
