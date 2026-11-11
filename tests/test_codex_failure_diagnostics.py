from __future__ import annotations
import subprocess
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
import claude_runner as runner

class CodexFailureDiagnosticTests(unittest.TestCase):
    def test_failure_tail_keeps_error_not_banner(self):
        result = subprocess.CompletedProcess(
            ["codex"], 1, stdout="",
            stderr="OpenAI Codex v0.144.6\n" + "instruction\n" * 100 +
                   "ERROR: model failed to process request\n",
        )
        actual = runner.summarize_failed_agent_output(result)
        self.assertIn("ERROR: model failed", actual)
        self.assertNotIn("OpenAI Codex", actual)
        self.assertNotIn("instruction", actual)

    def test_redacts_secret_from_failure(self):
        result = subprocess.CompletedProcess(
            ["codex"], 1, stdout="",
            stderr="ERROR: TOKEN=supersecret details\n",
        )
        actual = runner.summarize_failed_agent_output(result)
        self.assertNotIn("supersecret", actual)
        self.assertIn("REDACTED", actual)

    def test_stderr_preferred_over_prompt_in_stdout(self):
        result = subprocess.CompletedProcess(
            ["codex"], 1, stdout="this contains private task details",
            stderr="fatal: network connection failed",
        )
        actual = runner.summarize_failed_agent_output(result)
        self.assertIn("connection failed", actual)
        self.assertNotIn("private task details", actual)

if __name__ == "__main__":
    unittest.main()
