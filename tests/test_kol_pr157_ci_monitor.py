from __future__ import annotations
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"orchestrator"))
import kol_pr157_ci_monitor as m

class KOLPr157WatchTests(unittest.TestCase):
    def pr(self):
        return {"number":m.PR,"state":"open","merged":False,"head":{"sha":m.SHA}}
    def checks(self,status="completed",conclusion="success"):
        return {"check_runs":[
            {"name":name,"status":status,"conclusion":conclusion}
            for name in sorted(m.REQUIRED|{"Vercel Preview Comments"})
        ]}

    def test_success_only_when_all_terminal(self):
        self.assertEqual(m.evaluate(self.pr(),self.checks())["status"],"passed")
    def test_failed_check_is_not_accepted(self):
        checks=self.checks()
        checks["check_runs"][0]["conclusion"]="failure"
        self.assertEqual(m.evaluate(self.pr(),checks)["status"],"failed")
    def test_incomplete_is_pending(self):
        checks=self.checks()
        checks["check_runs"][0]["status"]="in_progress"
        self.assertEqual(m.evaluate(self.pr(),checks)["status"],"pending")
    def test_missing_required_is_pending(self):
        checks=self.checks()
        checks["check_runs"].pop()
        self.assertEqual(m.evaluate(self.pr(),checks)["status"],"pending")
    def test_mismatched_sha_blocks(self):
        pr=self.pr();pr["head"]["sha"]="1"*40
        self.assertEqual(m.evaluate(pr,self.checks())["status"],"blocked")
    def test_merged_pr_blocks(self):
        pr=self.pr();pr["merged"]=True
        self.assertEqual(m.evaluate(pr,self.checks())["status"],"blocked")
if __name__=="__main__":
    unittest.main()
