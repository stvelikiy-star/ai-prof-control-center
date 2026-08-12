from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "orchestrator"
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))

MODULE_PATH = ORCH / "github_task_gateway_service.py"
SPEC = importlib.util.spec_from_file_location("github_task_gateway_service_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load github_task_gateway_service")
service = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = service
SPEC.loader.exec_module(service)


class GatewayServiceTests(unittest.TestCase):
    def contract(self):
        return {
            "objective": "Create scoped evidence without broadening authority.",
            "priority": "normal",
            "allowed_actions": ["docs", "tests"],
            "forbidden_actions": [
                "commit", "push", "merge", "deployment", "secrets", "destructive-operations"
            ],
            "owner_approval_gates": ["production remains separately owner-gated"],
            "acceptance_criteria": ["task is enqueued exactly once", "required tests pass"],
        }

    def test_rendered_instructions_are_one_line_and_keep_issue_marker(self):
        text = service.render_one_line_instructions(13, self.contract())
        self.assertNotIn("\n", text)
        self.assertNotIn("\r", text)
        self.assertIn("Source: authorized private GitHub task issue #13.", text)
        self.assertIn("Forbidden actions:", text)
        self.assertIn("Acceptance criteria:", text)

    def test_multiline_contract_values_are_flattened_without_shell_execution(self):
        contract = self.contract()
        contract["objective"] = "first line\nsecond line"
        text = service.render_one_line_instructions(14, contract)
        self.assertEqual(text.count("\n"), 0)
        self.assertIn("first line second line", text)

    def test_oversized_render_blocks_fail_closed(self):
        contract = self.contract()
        contract["objective"] = "x" * service.gateway.MAX_TEXT
        with self.assertRaisesRegex(service.gateway.GatewayError, "one-line intake limit"):
            service.render_one_line_instructions(15, contract)


if __name__ == "__main__":
    unittest.main(verbosity=2)
