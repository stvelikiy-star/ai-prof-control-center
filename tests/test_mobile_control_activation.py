from __future__ import annotations

import importlib.util
import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "activate_mobile_control_v1.py"
SPEC = importlib.util.spec_from_file_location(
    "ai_prof_mobile_control_activation", MODULE_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load mobile control activation")
activation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = activation
SPEC.loader.exec_module(activation)


class MobileControlActivationTests(unittest.TestCase):
    def test_scope_is_only_control_center_services(self):
        self.assertEqual(
            activation.SERVICES,
            (
                "ai-prof-control-center.service",
                "ai-prof-telegram-bridge.service",
                "ai-prof-github-task-gateway.service",
            ),
        )
        self.assertEqual(
            activation.EXPECTED_REPOSITORY,
            "stvelikiy-star/ai-prof-control-center",
        )

    def test_exact_remote_sha_and_ancestry_are_required(self):
        source = inspect.getsource(activation.verify_preconditions)
        self.assertIn('git("fetch", "--prune", "origin", "main"', source)
        self.assertIn('git("rev-parse", "origin/main")', source)
        self.assertIn("remote_sha != approved_sha", source)
        self.assertIn('"merge-base", "--is-ancestor"', source)
        self.assertIn("worktree is dirty", source)
        self.assertIn("sudo(\"true\")", source)

    def test_release_tests_and_isolated_bootstrap_are_mandatory(self):
        source = inspect.getsource(activation.run_release_tests)
        self.assertIn("test_control_center.py", source)
        self.assertIn('"-m", "unittest", "-v"', source)
        self.assertIn("bootstrap_self_maintenance.py", source)

    def test_rollback_checkpoint_contains_git_bundle_and_units(self):
        checkpoint = inspect.getsource(activation.create_checkpoint)
        rollback = inspect.getsource(activation.rollback)
        self.assertIn('"bundle", "create"', checkpoint)
        self.assertIn("PREVIOUS_SHA", checkpoint)
        self.assertIn("PREVIOUS_BRANCH", checkpoint)
        self.assertIn("restore_units", rollback)
        self.assertIn("restore_previous_checkout", rollback)

    def test_activation_failures_trigger_rollback(self):
        source = inspect.getsource(activation.activate)
        self.assertIn("rollback(meta, backup)", source)
        self.assertIn("automatic rollback ALSO failed", source)

    def test_no_destructive_git_or_customer_production_commands(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        forbidden_literals = (
            '"reset", "--hard"',
            '"clean", "-fd"',
            '"push", "--force"',
            "supabase db reset",
            "supabase db push",
            "npm run deploy",
        )
        for literal in forbidden_literals:
            self.assertNotIn(literal, source)

    def test_systemd_template_grants_only_maintenance_checkout_extra_write(self):
        unit = (
            ROOT / "systemd" / "ai-prof-control-center.service"
        ).read_text(encoding="utf-8")
        rw = next(
            line for line in unit.splitlines()
            if line.startswith("ReadWritePaths=")
        )
        self.assertIn(
            "/home/agent/projects/ai-prof-control-center-maintenance", rw
        )
        self.assertNotIn(
            "/home/agent/projects/ai-prof-control-center ", rw
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
