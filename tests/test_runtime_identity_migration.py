from __future__ import annotations

import importlib.util
import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RuntimeIdentityTests(unittest.TestCase):
    def test_system_units_are_agent_owned_and_share_agent_runtime(self):
        names = (
            "ai-prof-control-center.service",
            "ai-prof-telegram-bridge.service",
            "ai-prof-github-task-gateway.service",
        )
        for name in names:
            with self.subTest(name=name):
                text = (ROOT / "systemd" / name).read_text(encoding="utf-8")
                self.assertIn("User=agent", text)
                self.assertIn("Group=agent", text)
                self.assertIn("Environment=HOME=/home/agent", text)
                self.assertIn(
                    "AI_PROF_STATE_DIR=/home/agent/.local/state/ai-prof-control-center",
                    text,
                )
        gateway = (ROOT / "systemd/ai-prof-github-task-gateway.service").read_text(encoding="utf-8")
        self.assertIn("GH_CONFIG_DIR=/home/agent/.config/gh", gateway)

    def test_control_center_uses_dedicated_telegram_surface(self):
        unit = (ROOT / "systemd/ai-prof-control-center.service").read_text(encoding="utf-8")
        self.assertIn("orchestrator/control_loop_service_night.py --daemon", unit)
        night_wrapper = (ROOT / "orchestrator/control_loop_service_night.py").read_text(encoding="utf-8")
        self.assertIn("ai_prof_approved_task_publisher_gate_v2.py", night_wrapper)
        wrapper = (ROOT / "orchestrator/control_loop_service.py").read_text(encoding="utf-8")
        self.assertIn(
            "control_loop.supervise_telegram_bridge = _dedicated_telegram_service_only",
            wrapper,
        )

    def test_control_center_systemd_sandbox_allows_kol_project(self):
        unit = (ROOT / "systemd/ai-prof-control-center.service").read_text(encoding="utf-8")
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("ProtectHome=read-only", unit)
        self.assertIn("/home/agent/Загрузки/kol-travel-platform", unit)
        self.assertNotIn("/home/agent/projects/ai-prof-control-center-night-watch", unit)

    def test_migration_orders_preparation_cutover_and_rollback(self):
        module_path = ROOT / "scripts/migrate_runtime_identity_v1.py"
        spec = importlib.util.spec_from_file_location("runtime_identity_migration", module_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        source = inspect.getsource(module.migrate)
        self.assertLess(source.index("update_checkout_and_units"), source.index("stop_user_runtime"))
        self.assertLess(source.index("stop_user_runtime"), source.index("start_system_runtime"))
        self.assertLess(source.index("start_system_runtime"), source.index("disable_old_user_units"))
        self.assertIn("restore_user_runtime(meta)", source)

    def test_runtime_verification_rejects_legacy_bridge(self):
        text = (ROOT / "scripts/migrate_runtime_identity_v1.py").read_text(encoding="utf-8")
        self.assertIn("legacy Telegram process is still running", text)
        self.assertIn("expected one agent-owned Telegram V2 process", text)
        self.assertIn("expected one agent-owned GitHub gateway process", text)
        self.assertIn("expected one agent-owned Control Center service process", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
