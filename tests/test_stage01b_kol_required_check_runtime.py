from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / "orchestrator"
if str(ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR))

import codex_stage01b_runner_v2 as v2
import control_loop


class Stage01BKolRequiredCheckRuntimeTests(unittest.TestCase):
    def test_live_kol_required_checks_resolve_to_exact_argv(self):
        # Reproduce the exact Required-Checks line from live V4 issue #172.
        v2.legacy.core.ALLOWED_COMMANDS.pop(
            "npm run check:release-source",
            None,
        )
        v2.verify_v2_required_check_contract()

        required = (
            "npm run lint, "
            "npx tsc --noEmit --incremental false, "
            "npm run check:release-source, "
            "npm run build"
        )
        self.assertEqual(
            v2.legacy.core.resolve_allowed_checks(required),
            [
                ["npm", "run", "lint"],
                ["npx", "tsc", "--noEmit", "--incremental", "false"],
                ["npm", "run", "check:release-source"],
                ["npm", "run", "build"],
            ],
        )

    def test_control_loop_stage01b_is_bound_to_v2_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            commands = control_loop.child_commands(root, root / "state")
        argv = dict(commands)["codex_stage_01b"]
        expected = str(root / "orchestrator/codex_stage01b_runner_v2.py")
        self.assertIn(expected, argv)
        self.assertFalse(
            any(
                str(item).endswith("orchestrator/codex_stage01b_runner.py")
                for item in argv
            )
        )

    def test_process_boundary_reinstalls_kol_check_before_delegation(self):
        class Paths:
            logs = Path("/tmp/ai-prof-stage01b-test-logs-do-not-create")

        v2.legacy.core.ALLOWED_COMMANDS.pop(
            "npm run check:release-source",
            None,
        )
        original = v2._ORIGINAL_PROCESS_ONE
        try:
            v2._ORIGINAL_PROCESS_ONE = lambda _paths: 0
            self.assertEqual(v2.process_one_v2(Paths()), 0)
        finally:
            v2._ORIGINAL_PROCESS_ONE = original

        self.assertEqual(
            v2.legacy.core.ALLOWED_COMMANDS.get("npm run check:release-source"),
            ["npm", "run", "check:release-source"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
