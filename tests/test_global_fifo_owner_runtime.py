from __future__ import annotations
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'orchestrator'))

import atomic_route_guard as route
import autonomous_shared_queue_worker as worker
import gateway_autonomous_intake as intake
import global_agent_admission as admission
import global_agent_fifo as fifo
import global_fifo_owner_adapter as adapter
import global_fifo_owner_runtime as runtime
from tests.test_autonomous_shared_queue import owner_issue


class OwnerFIFORuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.legacy=self.root/'legacy'
        self.fifo=self.root/'fifo'
        self.run=self.root/'run';self.run.mkdir(mode=0o700)
        self.issue=owner_issue(number=900)
        self.data=intake.validate_contract(self.issue)
        self.state={}
        patches=(
            (intake,'QUEUE_ROOT',self.legacy),
            (fifo,'ROOT',self.fifo),
            (admission,'DEFAULT_LOCK',self.run/'global.lock'),
            (route,'ROUTE_LOCK',self.run/'route.lock'),
            (route,'MODE_FILE',self.run/'route.json'),
            (worker,'RUN_LOCK',self.run/'worker.lock'),
        )
        for obj,attr,value in patches:
            patcher=mock.patch.object(obj,attr,value)
            patcher.start();self.addCleanup(patcher.stop)
        route._write(route.MODE_FILE,'fifo')
        self.pending_mock=mock.patch.object(adapter,'pending_contract',return_value=self.data)
        self.pending_mock.start();self.addCleanup(self.pending_mock.stop)
        self.exec_recheck=mock.patch.object(adapter,'recheck_before_execution',return_value=self.data)
        self.exec_recheck.start();self.addCleanup(self.exec_recheck.stop)

    def stage(self):
        return intake.process(self.issue,self.state)

    def run_tick(self,*,success=True,remote=True):
        with mock.patch('sequential_job_dispatcher.resource_gate',return_value=(True,'PASS')), \
             mock.patch.object(worker,'safe_codex_launch_gate',return_value=(True,'PASS')), \
             mock.patch.object(worker,'run_pilot_process',return_value=0 if success else 1) as executor, \
             mock.patch.object(worker,'verify_published',return_value=remote):
            result=worker.tick()
        return result,executor

    def test_gateway_owner_issue_stages_one_fifo_ticket(self):
        self.assertTrue(self.stage())
        self.assertEqual(self.state['900']['status'],'autonomous_fifo_queued')
        self.assertEqual(len(fifo.records(self.fifo,'pending')),1)
        self.assertEqual(len(list((self.legacy/'autonomous_pending').glob('*.json'))),1)
        ticket=fifo.read(fifo.records(self.fifo,'pending')[0])
        self.assertEqual(ticket['contract_sha256'],self.data['owner_issue_sha256'])

    def test_duplicate_gateway_poll_no_repeat_ticket(self):
        self.assertTrue(self.stage())
        self.assertFalse(self.stage())
        self.assertEqual(len(fifo.records(self.fifo,'pending')),1)

    def test_staging_error_remains_deferred_without_executable_ticket(self):
        with mock.patch.object(adapter,'pending_contract',side_effect=adapter.OwnerBridgeError('offline')):
            self.assertTrue(self.stage())
        self.assertEqual(self.state['900']['status'],'autonomous_fifo_waiting')
        self.assertEqual(len(fifo.records(self.fifo,'pending')),0)
        self.assertTrue(self.stage())
        self.assertEqual(self.state['900']['status'],'autonomous_fifo_queued')
        self.assertEqual(len(fifo.records(self.fifo,'pending')),1)

    def test_full_fifo_success_single_attempt(self):
        self.stage()
        status,run=self.run_tick(success=True,remote=True)
        self.assertEqual(status,'FIFO_OWNER_COMPLETED_PR_TELEGRAM_PASS')
        run.assert_called_once_with(self.data,lease_fd=mock.ANY)
        self.assertEqual(len(fifo.records(self.fifo,'completed')),1)
        self.assertTrue(intake.queue_path('autonomous_completed',900).exists())
        self.assertEqual(self.run_tick()[0],'IDLE')

    def test_worker_failure_blocks_both_ledgers(self):
        self.stage()
        status,run=self.run_tick(success=False,remote=False)
        self.assertEqual(status,'FIFO_OWNER_WORKER_BLOCKED')
        run.assert_called_once()
        self.assertEqual(len(fifo.records(self.fifo,'blocked')),1)
        self.assertTrue(intake.queue_path('autonomous_blocked',900).exists())

    def test_remote_proof_missing_blocks_both_ledgers(self):
        self.stage()
        status,_=self.run_tick(success=True,remote=False)
        self.assertEqual(status,'FIFO_OWNER_WORKER_BLOCKED')
        self.assertTrue(intake.queue_path('autonomous_blocked',900).exists())

    def test_interrupted_after_claim_blocks_without_codex_replay(self):
        self.stage()
        claimed=fifo.claim(root=self.fifo,lease_lock=admission.DEFAULT_LOCK)
        claimed.close()
        with mock.patch.object(worker,'check_job',return_value=self.data), \
             mock.patch.object(worker,'verify_published',return_value=False), \
             mock.patch.object(worker,'run_pilot_process') as executor:
            self.assertEqual(worker.tick(),'FIFO_INTERRUPTED_BLOCKED')
            executor.assert_not_called()
        self.assertEqual(len(fifo.records(self.fifo,'blocked')),1)
        self.assertTrue(intake.queue_path('autonomous_blocked',900).exists())

    def test_crash_after_claim_reconciles_only_remote_proof(self):
        self.stage()
        claimed=fifo.claim(root=self.fifo,lease_lock=admission.DEFAULT_LOCK)
        claimed.close()
        with mock.patch.object(worker,'check_job',return_value=self.data), \
             mock.patch.object(worker,'verify_published',return_value=True), \
             mock.patch.object(worker,'run_pilot_process') as executor:
            self.assertEqual(worker.tick(),'FIFO_RECOVERED_REMOTE_PROOF_PASS')
            executor.assert_not_called()
        self.assertTrue(intake.queue_path('autonomous_completed',900).exists())
        self.assertEqual(len(fifo.records(self.fifo,'completed')),1)

    def test_global_slot_busy_does_not_claim_pending(self):
        self.stage()
        with admission.acquire('kol-travel-platform',lock_path=admission.DEFAULT_LOCK):
            status,_=self.run_tick()
        self.assertEqual(status,'RESOURCE_DEFERRED_GLOBAL_AGENT_BUSY')
        self.assertEqual(len(fifo.records(self.fifo,'pending')),1)

    def test_orphaned_legacy_active_blocks_queue_advancement(self):
        self.stage()
        path=intake.queue_path('autonomous_pending',900)
        worker.relocate(path,'autonomous_active')
        status,executor=self.run_tick()
        self.assertEqual(status,'FIFO_LEGACY_ORPHAN_REQUIRES_REVIEW')
        executor.assert_not_called()

    def test_other_enabled_project_at_head_not_executed(self):
        self.stage()
        # A future adapter may stage other projects; this owner-only consumer
        # must not claim or skip their head ticket.
        for path in fifo.records(self.fifo,'pending'):path.unlink()
        fifo.submit('kol-travel-platform','KOL_TASK_42','a'*64,
                    owner_verify=lambda *_:True,root=self.fifo)
        status,executor=self.run_tick()
        self.assertEqual(status,'FIFO_HEAD_UNSUPPORTED_PROJECT')
        executor.assert_not_called()

    def test_frozen_mode_stops_gateway_and_executor(self):
        route._write(route.MODE_FILE,'frozen')
        self.assertFalse(self.stage())
        with mock.patch.object(worker,'run_pilot_process') as executor:
            self.assertEqual(worker.tick(),'RESOURCE_DEFERRED_ROUTE_FROZEN')
            executor.assert_not_called()

    def test_legacy_mode_keeps_existing_queue(self):
        route._write(route.MODE_FILE,'legacy')
        self.assertTrue(self.stage())
        self.assertEqual(self.state['900']['status'],'autonomous_queued')
        self.assertFalse(self.fifo.exists())

    def test_changed_author_cannot_create_fifo_ticket(self):
        self.issue['user']['login']='untrusted-user'
        self.assertTrue(self.stage())
        self.assertEqual(self.state['900']['status'],'rejected')
        self.assertFalse(self.fifo.exists())

    def test_crash_after_legacy_record_before_fifo_retry(self):
        intake._create_new(intake.queue_path('autonomous_pending',900),self.data)
        self.assertTrue(self.stage())
        self.assertEqual(self.state['900']['status'],'autonomous_fifo_queued')
        self.assertEqual(len(fifo.records(self.fifo,'pending')),1)
        self.assertEqual(len(list((self.legacy/'autonomous_pending').glob('*.json'))),1)

    def test_remote_policy_change_at_claim_blocks_without_execution(self):
        self.stage()
        with mock.patch.object(adapter,'recheck_before_execution',side_effect=adapter.OwnerBridgeError('changed')), \
             mock.patch('sequential_job_dispatcher.resource_gate',return_value=(True,'PASS')), \
             mock.patch.object(worker,'safe_codex_launch_gate',return_value=(True,'PASS')), \
             mock.patch.object(worker,'run_pilot_process') as execute:
            self.assertEqual(worker.tick(),'FIFO_OWNER_WORKER_BLOCKED')
            execute.assert_not_called()
        self.assertTrue(intake.queue_path('autonomous_blocked',900).exists())
        self.assertEqual(len(fifo.records(self.fifo,'blocked')),1)

    def test_global_resource_gate_defers_without_claim(self):
        self.stage()
        with mock.patch('sequential_job_dispatcher.resource_gate',return_value=(False,'RAM_LOW')), \
             mock.patch.object(worker,'run_pilot_process') as execute:
            self.assertEqual(worker.tick(),'RESOURCE_DEFERRED_RAM_LOW')
            execute.assert_not_called()
        self.assertEqual(len(fifo.records(self.fifo,'pending')),1)

    def test_active_orphan_worker_lease_defers_recovery(self):
        self.stage()
        claimed=fifo.claim(root=self.fifo,lease_lock=admission.DEFAULT_LOCK)
        with mock.patch.object(worker,'run_pilot_process') as execute:
            self.assertEqual(worker.tick(),'RESOURCE_DEFERRED_ORPHANED_WORKER_MAY_BE_ACTIVE')
            execute.assert_not_called()
        self.assertEqual(len(fifo.records(self.fifo,'active')),1)
        claimed.close()
        with mock.patch.object(worker,'check_job',return_value=self.data), \
             mock.patch.object(worker,'verify_published',return_value=False):
            self.assertEqual(worker.tick(),'FIFO_INTERRUPTED_BLOCKED')

    def test_fifo_active_recovery_after_legacy_active_child_crash(self):
        self.stage()
        claimed=fifo.claim(root=self.fifo,lease_lock=admission.DEFAULT_LOCK)
        worker.relocate(intake.queue_path('autonomous_pending',900),'autonomous_active')
        claimed.close()
        with mock.patch.object(worker,'check_job',return_value=self.data), \
             mock.patch.object(worker,'verify_published',return_value=True), \
             mock.patch.object(worker,'run_pilot_process') as execute:
            self.assertEqual(worker.tick(),'FIFO_RECOVERED_REMOTE_PROOF_PASS')
            execute.assert_not_called()
        self.assertTrue(intake.queue_path('autonomous_completed',900).exists())

    def test_no_gateway_access_to_client_project(self):
        self.stage()
        ticket=fifo.read(fifo.records(self.fifo,'pending')[0])
        self.assertEqual(ticket['project_id'],'ai-prof-control-center')
        self.assertNotIn('command',ticket)
        self.assertNotIn('path',ticket)


if __name__=='__main__':unittest.main()
