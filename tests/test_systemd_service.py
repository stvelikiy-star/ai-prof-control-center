from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "systemd" / "ai-prof-control-center.service"


class ControlCenterServiceTests(unittest.TestCase):
    def test_code_sandbox_namespaces_are_allowed_without_weakening_filesystem_policy(self):
        text = UNIT.read_text(encoding="utf-8")
        self.assertIn("NoNewPrivileges=false", text)
        self.assertIn("PrivateUsers=false", text)
        self.assertIn("RestrictNamespaces=yes", text)
        self.assertIn("PrivateTmp=true", text)
        self.assertIn("ProtectSystem=strict", text)
        self.assertIn("ProtectHome=read-only", text)
        self.assertNotIn("ProtectSystem=false", text)

    def test_only_approved_runtime_and_projects_are_service_writable(self):
        text = UNIT.read_text(encoding="utf-8")
        line = next(line for line in text.splitlines() if line.startswith("ReadWritePaths="))
        self.assertIn("/home/agent/.local/state/ai-prof-control-center", line)
        self.assertIn("/home/agent/projects/ak-bermet", line)
        self.assertNotIn("/home/agent/.nvm", line)
        self.assertNotIn("/home/agent/.claude", line)
        self.assertNotIn("/home/agent/.codex", line)


if __name__ == "__main__":
    unittest.main()
