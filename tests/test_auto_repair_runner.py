from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "orchestrator" / "auto_repair_runner.py"
SPEC = importlib.util.spec_from_file_location("ai_prof_auto_repair_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load auto_repair_runner")
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


class AutoRepairRunnerTests(unittest.TestCase):
    def test_review_attempt_round_trip(self):
        text = "Task-ID: X\nCodex-Review-Attempt: 2\n"
        self.assertEqual(runner.review_attempt(text), 2)
        self.assertEqual(runner.review_attempt(runner.set_review_attempt(text, 3)), 3)

    def test_review_attempt_is_added_when_missing(self):
        updated = runner.set_review_attempt("Task-ID: X\n", 1)
        self.assertEqual(runner.review_attempt(updated), 1)

    def test_feedback_is_replaced_not_duplicated(self):
        text = runner.set_auto_feedback("Task-ID: X\n", "first")
        text = runner.set_auto_feedback(text, "second")
        self.assertEqual(text.count(runner.AUTO_FEEDBACK_MARKER), 1)
        self.assertIn("second", text)
        self.assertNotIn("first", text)

    def test_check_command_must_be_exact_required_check(self):
        self.assertEqual(
            runner.validated_check_argv("npx tsc --noEmit", ["npx tsc --noEmit"]),
            ["npx", "tsc", "--noEmit"],
        )
        with self.assertRaises(runner.AutoRepairError):
            runner.validated_check_argv("bash -c whoami", ["npx tsc --noEmit"])

    def test_configured_cycle_limit_is_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "orchestrator").mkdir()
            (root / "orchestrator" / "config.json").write_text(
                json.dumps({"max_fix_cycles": 5}), encoding="utf-8"
            )
            self.assertEqual(runner.load_max_fix_cycles(root), 5)

    def test_secret_redaction(self):
        self.assertIn("[REDACTED]", runner.redact("api_key=secret-value"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
