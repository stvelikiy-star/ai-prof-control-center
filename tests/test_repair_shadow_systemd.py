from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "systemd" / "ai-prof-repair-diagnosis.service"
TIMER = ROOT / "systemd" / "ai-prof-repair-diagnosis.timer"


class RepairShadowSystemdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = SERVICE.read_text(encoding="utf-8")
        cls.timer = TIMER.read_text(encoding="utf-8")

    def test_service_runs_only_diagnosis_and_task_bridge(self):
        exec_lines = [
            line for line in self.service.splitlines() if line.startswith("ExecStart=")
        ]
        self.assertEqual(len(exec_lines), 2)
        self.assertIn("orchestrator/incident_diagnosis_runner.py", exec_lines[0])
        self.assertIn("orchestrator/repair_task_bridge.py", exec_lines[1])
        joined = "\n".join(exec_lines).lower()
        for forbidden in (
            "release_flow",
            "operations_runner",
            "approved_task_publisher",
            "deploy",
            "migration",
            "rollback",
            "systemctl",
            "docker",
            "supabase",
        ):
            self.assertNotIn(forbidden, joined)

    def test_service_has_no_project_write_access(self):
        write_lines = [
            line for line in self.service.splitlines() if line.startswith("ReadWritePaths=")
        ]
        self.assertEqual(
            write_lines,
            ["ReadWritePaths=/home/agent/.local/state/ai-prof-control-center"],
        )
        writable = write_lines[0]
        for forbidden in (
            "/home/agent/projects/",
            "/home/agent/Загрузки/",
            "ak-bermet",
            "kol-travel-platform",
            "resort-os",
            "ai-prof-control-center-maintenance",
        ):
            self.assertNotIn(forbidden, writable)

    def test_service_is_hardened_and_bounded(self):
        required = {
            "Type=oneshot",
            "User=agent",
            "Group=agent",
            "SuccessExitStatus=0 1",
            "TimeoutStartSec=20min",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "PrivateDevices=true",
            "ProtectSystem=strict",
            "ProtectHome=read-only",
            "ProtectKernelTunables=true",
            "ProtectKernelModules=true",
            "ProtectControlGroups=true",
            "RestrictSUIDSGID=true",
            "UMask=0077",
        }
        lines = set(self.service.splitlines())
        self.assertTrue(required.issubset(lines), sorted(required - lines))
        self.assertNotIn("NoNewPrivileges=false", self.service)

    def test_timer_is_slow_enough_and_persistent(self):
        self.assertIn("Unit=ai-prof-repair-diagnosis.service", self.timer)
        self.assertIn("Persistent=true", self.timer)
        match = re.search(r"^OnUnitActiveSec=(\d+)s$", self.timer, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertGreaterEqual(int(match.group(1)), 120)
        boot = re.search(r"^OnBootSec=(\d+)s$", self.timer, re.MULTILINE)
        self.assertIsNotNone(boot)
        self.assertGreaterEqual(int(boot.group(1)), 60)

    def test_passive_monitor_remains_separate(self):
        monitor = (ROOT / "systemd" / "ai-prof-repair-monitor.service").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("incident_diagnosis_runner.py", monitor)
        self.assertNotIn("repair_task_bridge.py", monitor)
        self.assertIn("incident_engine_canary.py", monitor)
        self.assertIn("diagnosis_packet.py", monitor)


if __name__ == "__main__":
    unittest.main()
