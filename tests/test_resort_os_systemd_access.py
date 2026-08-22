import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "systemd" / "ai-prof-control-center.service"


class ResortOsSystemdAccessTests(unittest.TestCase):
    def test_resort_os_is_explicitly_writable_in_control_center_unit(self):
        text = UNIT.read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if line.startswith("ReadWritePaths=")]
        self.assertEqual(len(lines), 1)
        paths = set(lines[0].split("=", 1)[1].split())
        self.assertIn("/home/agent/projects/resort-os", paths)

    def test_home_remains_read_only(self):
        text = UNIT.read_text(encoding="utf-8")
        self.assertIn("ProtectHome=read-only", text)


if __name__ == "__main__":
    unittest.main()
