from __future__ import annotations
import hashlib
import json
import os
import time
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'orchestrator'))
import global_fifo_owner_adapter as bridge
import global_agent_fifo as fifo
import gateway_autonomous_intake as intake
import autonomous_shared_queue_worker as worker


class OwnerAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.legacy=self.root / 'legacy'
        self.fifo=self.root / 'fifo'
        self.number=800
        self.task='AI_PROF_AUTO_MAIN_800'
        self.pilot_body=intake.PROFILES[intake.PROFILE]['pilot_marker']+'\nfixture\n'
        self.pilot_digest=hashlib.sha256(self.pilot_body.encode()).hexdigest()
        self.cfg={**intake.PROFILES[intake.PROFILE], 'pilot_sha256':self.pilot_digest}
        cfg_patch=mock.patch.dict(intake.PROFILES,{intake.PROFILE:self.cfg})
        cfg_patch.start();self.addCleanup(cfg_patch.stop)
        payload={'version':1,'profile':intake.PROFILE,'pilot_repository':intake.PILOT_REPOSITORY,
                 'pilot_issue':intake.PILOT_ISSUE,'pilot_body_sha256':self.pilot_digest,'action':'draft-pr-only'}
        body=intake.MARKER+json.dumps(payload,sort_keys=True)
        self.main={'number':self.number,'state':'open','body':body,
                   'title':intake.PREFIX+'Pilot task counts via Codex','user':{'login':'stvelikiy-star'}}
        self.pilot={'number':intake.PILOT_ISSUE,'state':'open',
                    'body':self.pilot_body,'title':self.cfg['pilot_title'],
                    'user':{'login':'stvelikiy-star'}}
        self.data=intake.validate_contract(self.main)
        path=self.path('autonomous_pending');path.parent.mkdir(parents=True)
        path.write_text(json.dumps(self.data),encoding='utf-8')
        self.get_patch=mock.patch.object(worker,'get_issue',side_effect=self.get_issue)
        self.get_patch.start();self.addCleanup(self.get_patch.stop)

    def get_issue(self,repo,number):
        if number==self.number:return self.main
        if number==intake.PILOT_ISSUE:return self.pilot
        raise AssertionError('unexpected remote lookup')

    def path(self,state):
        return self.legacy / state / (self.task+'.json')

    def stage(self):
        return bridge.stage_isolated(self.task, legacy_root=self.legacy,fifo_root=self.fifo)

    def test_actual_owner_and_pilot_verification_stages_isolated(self):
        self.assertEqual(self.stage()[0],'pending')
        entry=fifo.read(fifo.records(self.fifo,'pending')[0])
        self.assertEqual(entry['contract_sha256'],self.data['owner_issue_sha256'])
        self.assertEqual(entry['project_id'],'ai-prof-control-center')
        self.assertEqual(bridge.recheck_before_execution(entry,legacy_root=self.legacy),self.data)

    def test_idempotent_submission_exact_digest(self):
        first=self.stage()
        self.assertEqual(self.stage(),first)
        self.assertEqual(len(fifo.records(self.fifo,'pending')),1)

    def test_wrong_owner_rejected(self):
        self.main['user']['login']='unknown'
        with self.assertRaises(bridge.OwnerBridgeError):self.stage()
        self.assertFalse(self.fifo.exists())

    def test_owner_issue_closed_rejected(self):
        self.main['state']='closed'
        with self.assertRaises(bridge.OwnerBridgeError):self.stage()

    def test_pilot_issue_changed_rejected(self):
        self.pilot['body']+='tampered'
        with self.assertRaises(bridge.OwnerBridgeError):self.stage()

    def test_owner_contract_changed_rejected(self):
        self.main['body']+='tampered'
        with self.assertRaises(bridge.OwnerBridgeError):self.stage()

    def test_pending_file_changed_rejected(self):
        saved=json.loads(self.path('autonomous_pending').read_text())
        saved['pilot_sha256']='0'*64
        self.path('autonomous_pending').write_text(json.dumps(saved))
        with self.assertRaises(bridge.OwnerBridgeError):self.stage()

    def test_stale_pending_task_cannot_reset_deadline(self):
        path = self.path('autonomous_pending')
        old = time.time() - 14401
        os.utime(path, (old, old))
        with self.assertRaises(bridge.OwnerBridgeError):self.stage()
        self.assertFalse(self.fifo.exists())

    def test_future_pending_timestamp_cannot_bypass_age(self):
        path = self.path('autonomous_pending')
        future = time.time() + 180
        os.utime(path, (future, future))
        with self.assertRaises(bridge.OwnerBridgeError):self.stage()

    def test_completed_task_cannot_be_replayed(self):
        dest=self.path('autonomous_completed');dest.parent.mkdir(parents=True)
        self.path('autonomous_pending').replace(dest)
        with self.assertRaises(bridge.OwnerBridgeError):self.stage()

    def test_active_task_cannot_be_migrated(self):
        dest=self.path('autonomous_active');dest.parent.mkdir(parents=True)
        self.path('autonomous_pending').replace(dest)
        with self.assertRaises(bridge.OwnerBridgeError):self.stage()

    def test_blocked_task_cannot_be_migrated(self):
        dest=self.path('autonomous_blocked');dest.parent.mkdir(parents=True)
        self.path('autonomous_pending').replace(dest)
        with self.assertRaises(bridge.OwnerBridgeError):self.stage()

    def test_duplicate_legacy_records_fail_closed(self):
        dest=self.path('autonomous_completed');dest.parent.mkdir(parents=True)
        dest.write_text(self.path('autonomous_pending').read_text())
        with self.assertRaises(bridge.OwnerBridgeError):self.stage()

    def test_symlink_queue_record_rejected(self):
        original=self.path('autonomous_pending');other=self.root/'fake';other.write_text(original.read_text())
        original.unlink();original.symlink_to(other)
        with self.assertRaises(bridge.OwnerBridgeError):self.stage()

    def test_unavailable_remote_evidence_denied(self):
        with mock.patch.object(worker,'get_issue',side_effect=OSError('offline')):
            with self.assertRaises(bridge.OwnerBridgeError):self.stage()

    def test_wrong_project_on_claim_rejected(self):
        item={'project_id':'kol-travel-platform','task_id':self.task,'contract_sha256':self.data['owner_issue_sha256']}
        with self.assertRaises(bridge.OwnerBridgeError):
            bridge.recheck_before_execution(item,legacy_root=self.legacy)

    def test_claimed_digest_changed_rejected(self):
        item={'project_id':'ai-prof-control-center','task_id':self.task,'contract_sha256':'b'*64}
        with self.assertRaises(bridge.OwnerBridgeError):
            bridge.recheck_before_execution(item,legacy_root=self.legacy)

    def test_invalid_task_id_denied(self):
        with self.assertRaises(bridge.OwnerBridgeError):
            bridge.stage_isolated('AI_PROF_AUTO_MAIN_../800',legacy_root=self.legacy,fifo_root=self.fifo)

    def test_live_fifo_destination_denied(self):
        with self.assertRaises(bridge.OwnerBridgeError):
            bridge.stage_isolated(self.task,legacy_root=self.legacy,fifo_root=fifo.ROOT)

    def test_live_legacy_source_denied(self):
        with self.assertRaises(bridge.OwnerBridgeError):
            bridge.stage_isolated(self.task,legacy_root=intake.QUEUE_ROOT,fifo_root=self.fifo)

    def test_unapproved_generic_profile_denied(self):
        self.main['body']=self.main['body'].replace('draft-pr-only','merge')
        with self.assertRaises(bridge.OwnerBridgeError):self.stage()

if __name__=='__main__':unittest.main()
