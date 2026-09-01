from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "systemd" / "ai-prof-repair-monitor.service"
TIMER = ROOT / "systemd" / "ai-prof-repair-monitor.timer"


class RepairMonitorSystemdTests(unittest.TestCase):
    def test_service_is_read_only_except_state(self):
        text = SERVICE.read_text(encoding="utf-8")
        self.assertIn("Type=oneshot", text)
        self.assertIn("incident_engine_canary.py", text)
        self.assertIn("diagnosis_packet.py", text)
        self.assertIn("SuccessExitStatus=0 1", text)
        self.assertIn("NoNewPrivileges=true", text)
        self.assertIn("ProtectSystem=strict", text)
        self.assertIn("ProtectHome=read-only", text)
        self.assertIn(
            "ReadWritePaths=/home/agent/.local/state/ai-prof-control-center",
            text,
        )
        self.assertNotIn("EnvironmentFile=", text)
        self.assertNotIn("sudo", text)

    def test_timer_is_bounded_to_one_minute_cadence(self):
        text = TIMER.read_text(encoding="utf-8")
        self.assertIn("OnBootSec=45s", text)
        self.assertIn("OnUnitActiveSec=60s", text)
        self.assertIn("AccuracySec=5s", text)
        self.assertIn("Persistent=true", text)
        self.assertIn("Unit=ai-prof-repair-monitor.service", text)


if __name__ == "__main__":
    unittest.main()
