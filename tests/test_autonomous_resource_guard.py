from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
import autonomous_shared_queue_worker as w

class ResourceGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "pressure").mkdir()
        self.set_state()

    def set_state(self, *, swap=90, ram=40, psi=0.0):
        (self.root / "meminfo").write_text(
            f"SwapTotal: 1000 kB\nSwapFree: {1000 - swap*10} kB\n"
            f"MemTotal: 1000 kB\nMemAvailable: {ram*10} kB\n")
        (self.root / "pressure/memory").write_text(
            f"some avg10=0.00 avg60={psi:.2f} avg300=0.00 total=0\n")

    def process(self, pid, argv):
        d=self.root / str(pid);d.mkdir()
        (d/'cmdline').write_bytes(b'\0'.join(x.encode() for x in argv)+b'\0')

    def test_idle_healthy_high_swap_without_pressure_passes(self):
        self.assertEqual(w.safe_codex_launch_gate(self.root), (True, "EXTERNAL_AGENTS_AND_PRESSURE_PASS"))

    def test_real_codex_exec_blocks(self):
        self.process(123, ['/home/agent/.local/bin/codex','exec','task'])
        self.assertEqual(w.safe_codex_launch_gate(self.root), (False, "OTHER_CODEX_ACTIVE"))

    def test_other_process_not_counted(self):
        self.process(124,['python3','worker.py','codex','exec'])
        self.assertTrue(w.safe_codex_launch_gate(self.root)[0])

    def test_codex_non_exec_command_not_counted(self):
        self.process(124,['codex','--version'])
        self.assertTrue(w.safe_codex_launch_gate(self.root)[0])

    def test_high_swap_with_low_available_ram_blocks(self):
        self.set_state(swap=90,ram=10)
        self.assertEqual(w.safe_codex_launch_gate(self.root), (False, "SWAP_MEMORY_PRESSURE"))

    def test_high_swap_with_high_psi_blocks(self):
        self.set_state(swap=90,ram=40,psi=7)
        self.assertEqual(w.safe_codex_launch_gate(self.root), (False, "SWAP_MEMORY_PRESSURE"))

    def test_missing_psi_blocks_at_high_swap(self):
        (self.root/'pressure/memory').unlink()
        self.assertEqual(w.safe_codex_launch_gate(self.root), (False, "SWAP_MEMORY_PRESSURE"))

    def test_missing_meminfo_fails_closed(self):
        (self.root/'meminfo').unlink()
        self.assertEqual(w.safe_codex_launch_gate(self.root), (False, "EXTERNAL_RESOURCE_UNVERIFIED"))

    def test_invalid_swap_metrics_fails_closed(self):
        (self.root/'meminfo').write_text('SwapTotal: 100 kB\nSwapFree: 200 kB\nMemTotal: 1000 kB\nMemAvailable: 400 kB\n')
        self.assertEqual(w.safe_codex_launch_gate(self.root), (False, "INVALID_MEMORY_METRICS"))

    def test_race_disappearing_process_is_ignored(self):
        (self.root/'432').mkdir()
        self.assertTrue(w.safe_codex_launch_gate(self.root)[0])

if __name__ == '__main__':
    unittest.main()
