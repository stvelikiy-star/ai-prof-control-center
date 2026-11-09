import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
import swap_pressure_gate as gate

class SwapPressureTests(unittest.TestCase):
    def test_old_high_swap_without_pressure_is_warning(self):
        self.assertEqual(gate.severity(88, 45, 0.0), "WARNING")
    def test_high_swap_with_ram_pressure_is_critical(self):
        self.assertEqual(gate.severity(88, 12, 0.0), "CRITICAL")
    def test_high_swap_with_psi_pressure_is_critical(self):
        self.assertEqual(gate.severity(88, 40, 7.0), "CRITICAL")
    def test_missing_psi_fails_closed(self):
        self.assertEqual(gate.severity(88, 40, None), "CRITICAL")
    def test_moderate_swap_is_warning(self):
        self.assertEqual(gate.severity(56, 40, 0.0), "WARNING")
    def test_low_swap_ok(self):
        self.assertEqual(gate.severity(20, 40, 0.0), "OK")
    def test_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"memory"
            path.write_text("some avg10=0.00 avg60=2.50 avg300=0.00 total=4\n")
            self.assertEqual(gate.psi_some_avg60(path), 2.5)
if __name__=="__main__":unittest.main()
