from __future__ import annotations
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'orchestrator'))
import gateway_autonomous_intake as intake
import autonomous_shared_queue_worker as worker
import global_fifo_owner_runtime as runtime
from tests.test_autonomous_shared_queue import owner_issue
from tests.test_global_fifo_owner_runtime import OwnerFIFORuntimeTests

class FIFOStatusReconciliationTests(OwnerFIFORuntimeTests):
    def test_completed_fifo_reports_to_gateway_once(self):
        self.stage()
        self.assertEqual(self.run_tick()[0],'FIFO_OWNER_COMPLETED_PR_TELEGRAM_PASS')
        proof={'pull_request':'https://github.com/stvelikiy-star/ai-prof-autonomy-pilot/pull/8','commit':'a'*40}
        report=self.root/'proof.json';report.write_text(json.dumps(proof))
        with mock.patch.object(worker,'verify_published',return_value=True), \
             mock.patch.object(worker.core,'STATE',report), \
             mock.patch.object(intake.gateway,'run_gh',return_value=[]), \
             mock.patch.object(intake.gateway,'post_comment') as comment:
            self.assertTrue(self.stage())
            self.assertEqual(self.state['900']['status'],'autonomous_completed')
            self.assertTrue(self.state['900']['result_reported'])
            comment.assert_called_once()
            self.assertFalse(self.stage())
            comment.assert_called_once()

    def test_blocked_fifo_reports_terminal_to_gateway(self):
        self.stage();self.assertEqual(self.run_tick(success=False,remote=False)[0],'FIFO_OWNER_WORKER_BLOCKED')
        with mock.patch.object(intake.gateway,'run_gh',return_value=[]), \
             mock.patch.object(intake.gateway,'post_comment') as comment:
            self.assertTrue(self.stage())
            self.assertEqual(self.state['900']['status'],'autonomous_blocked')
            self.assertFalse(self.stage())
            comment.assert_called_once()

class ClosedIssueReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.issue=owner_issue(number=212);self.issue['state']='closed'
        self.record=intake.validate_contract(dict(self.issue,state='open'))
        patch=mock.patch.object(intake,'QUEUE_ROOT',self.root);patch.start();self.addCleanup(patch.stop)
        path=intake.queue_path('autonomous_blocked',212)
        intake._create_new(path,self.record)
        self.state={'212':{'status':'autonomous_queued','kind':intake.RECORD_MARKER,
                           'contract_sha256':self.record['owner_issue_sha256']}}

    def test_closed_terminal_updates_local_state_without_remote_write(self):
        with mock.patch.object(intake.gateway,'run_gh',return_value=self.issue) as get, \
             mock.patch.object(intake.gateway,'post_comment') as post, \
             mock.patch.object(worker,'run_pilot_process') as run:
            self.assertTrue(intake.reconcile_closed_terminal(self.state))
            self.assertEqual(self.state['212']['status'],'autonomous_blocked')
            self.assertFalse(self.state['212']['result_reported'])
            self.assertFalse(intake.reconcile_closed_terminal(self.state))
            get.assert_called_once();post.assert_not_called();run.assert_not_called()

    def test_changed_body_never_reconciles(self):
        self.issue['body']+='modified'
        with mock.patch.object(intake.gateway,'run_gh',return_value=self.issue):
            self.assertFalse(intake.reconcile_closed_terminal(self.state))
        self.assertEqual(self.state['212']['status'],'autonomous_queued')

    def test_wrong_owner_never_reconciles(self):
        self.issue['user']['login']='attacker'
        with mock.patch.object(intake.gateway,'run_gh',return_value=self.issue):
            self.assertFalse(intake.reconcile_closed_terminal(self.state))

    def test_missing_remote_proof_never_reconciles(self):
        with mock.patch.object(intake.gateway,'run_gh',side_effect=OSError('offline')):
            self.assertFalse(intake.reconcile_closed_terminal(self.state))

    def test_no_terminal_file_no_remote_query(self):
        path=intake.queue_path('autonomous_blocked',212);path.unlink()
        with mock.patch.object(intake.gateway,'run_gh') as query:
            self.assertFalse(intake.reconcile_closed_terminal(self.state))
            query.assert_not_called()

    def test_conflicting_digest_does_not_reconcile(self):
        self.state['212']['contract_sha256']='b'*64
        with mock.patch.object(intake.gateway,'run_gh') as query:
            self.assertFalse(intake.reconcile_closed_terminal(self.state))
            query.assert_not_called()

if __name__=='__main__':unittest.main()
