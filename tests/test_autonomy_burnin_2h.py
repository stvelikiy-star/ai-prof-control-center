from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"orchestrator"))
import autonomy_burnin_2h as b

class AutonomyBurninTests(unittest.TestCase):
    def test_report_stays_readonly(self):
        s={"started_at":1,"deadline":7201,"samples":[
            {"timestamp":100,"warnings":[],"critical":[]}]}
        report=b.report(s)
        self.assertIn("PASS",report)
        self.assertIn("Только чтение",report)
        self.assertNotIn("deploy=true",report)
    def test_error_results_in_fail(self):
        state={"started_at":1,"deadline":7201,"samples":[
            {"timestamp":4,"critical":["сервер не отвечает"],"warnings":[]}]}
        self.assertIn("FAIL",b.report(state))
    def test_service_uses_fixed_allowlist(self):
        self.assertEqual(len(b.SERVICES),3)
        self.assertNotIn("marina", " ".join(b.SERVICES))
    def test_atomic_state_and_permission(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/"state.json"
            b.write_json(p,{"version":1,"duration_seconds":b.DURATION})
            self.assertEqual(p.stat().st_mode & 0o777,0o600)
    def test_memory_critical_gate(self):
        with patch.object(b,"service_state",return_value=(True,"0")),\
             patch.object(b,"available_memory_bytes",return_value=(500*1048576,0.0)),\
             patch.object(b.backups,"evaluate",return_value=("PASS","ok")),\
             patch.object(b.bridge,"_queue_locations",return_value={}):
            snapshot=b.snapshot()
        self.assertTrue(any("памяти" in x for x in snapshot["critical"]))
if __name__=="__main__":unittest.main()
