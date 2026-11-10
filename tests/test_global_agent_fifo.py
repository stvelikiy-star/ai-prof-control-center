from __future__ import annotations
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'orchestrator'))
import global_agent_admission as gate
import global_agent_fifo as fifo

class GlobalFIFOTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'queue'
        self.lock=Path(self.temp.name)/'agent.lock'
        self.registry_root=Path(__file__).resolve().parents[1]
        self.approved=lambda project,task,digest: True
        self.digest='a'*64

    def submit(self,project='ai-prof-control-center',task='T1',digest=None,guard=None):
        return fifo.submit(project,task,digest or self.digest,
                           owner_verify=guard if guard is not None else self.approved,
                           root=self.root,registry_root=self.registry_root)

    def claim(self):
        return fifo.claim(root=self.root,registry_root=self.registry_root,lease_lock=self.lock)

    def recover(self):
        return fifo.recover(root=self.root,registry_root=self.registry_root,lease_lock=self.lock)

    def finish(self,job,valid=True):
        return fifo.finish(job,remote_evidence_valid=valid,root=self.root)

    def test_fifo_across_two_authorized_projects(self):
        _,a=self.submit(task='T1')
        _,b=self.submit('kol-travel-platform','T2')
        _,c=self.submit(task='T3')
        self.assertLess(a,b);self.assertLess(b,c)
        for project in ('ai-prof-control-center','kol-travel-platform','ai-prof-control-center'):
            claimed=self.claim()
            self.assertEqual(claimed.item['project_id'],project)
            self.assertEqual(self.finish(claimed),'completed')
        self.assertIsNone(self.claim())

    def test_submission_idempotent_without_reordering(self):
        first=self.submit()
        self.assertEqual(first,self.submit())
        self.assertEqual(len(fifo.records(self.root,'pending')),1)

    def test_contract_tamper_denied(self):
        self.submit()
        with self.assertRaises(fifo.FIFOError):self.submit(digest='b'*64)

    def test_missing_owner_evidence_denied(self):
        with self.assertRaises(fifo.FIFOError):self.submit(guard=lambda *_: False)
        self.assertFalse(self.root.exists())

    def test_disabled_and_unregistered_project_denied(self):
        for project in ('resort-os','invented'):
            with self.subTest(project=project),self.assertRaises(fifo.FIFOError):
                self.submit(project=project)

    def test_unsafe_task_id_and_sha_denied(self):
        for task in ('../escape','x/y','', 'a'*100):
            with self.subTest(task=task),self.assertRaises(fifo.FIFOError):self.submit(task=task)
        with self.assertRaises(fifo.FIFOError):self.submit(digest='invalid')

    def test_global_lease_contention_leaves_ticket_pending(self):
        self.submit()
        with gate.acquire('kol-travel-platform',registry_root=self.registry_root,lock_path=self.lock):
            self.assertIsNone(self.claim())
            self.assertEqual(len(fifo.records(self.root,'pending')),1)
            self.assertEqual(len(fifo.records(self.root,'active')),0)
        self.assertIsNotNone(self.claim())

    def test_active_task_blocks_next_claim(self):
        self.submit(task='T1');self.submit(task='T2')
        claimed=self.claim()
        with self.assertRaises(fifo.FIFOError):self.claim()
        self.assertEqual(self.finish(claimed),'completed')
        second=self.claim();self.assertEqual(second.item['task_id'],'T2')
        self.finish(second)

    def test_negative_remote_evidence_blocks_without_retry(self):
        self.submit()
        self.assertEqual(self.finish(self.claim(),False),'blocked')
        self.assertIsNone(self.claim())
        self.assertEqual(len(fifo.records(self.root,'blocked')),1)

    def test_interrupted_task_recovery_never_replays(self):
        self.submit()
        job=self.claim()
        with self.assertRaises(fifo.FIFOError):self.recover()
        job.close()  # simulated worker/parent process exit
        self.assertEqual(self.recover(),1)
        self.assertEqual(len(fifo.records(self.root,'blocked')),1)
        self.assertIsNone(self.claim())

    def test_corrupted_ticket_blocks_claim(self):
        _,name=self.submit()
        (self.root/'pending'/name).write_text('garbage')
        with self.assertRaises(fifo.FIFOError):self.claim()

    def test_symlink_pending_directory_denied(self):
        self.submit()
        (self.root/'pending'/next(iter(fifo.records(self.root,'pending'))).name).unlink()
        (self.root/'pending').rmdir()
        (self.root/'pending').symlink_to(self.root/'completed',target_is_directory=True)
        with self.assertRaises(fifo.FIFOError):self.claim()

    def test_no_task_commands_in_saved_ticket(self):
        _,name=self.submit()
        raw=(self.root/'pending'/name).read_text()
        self.assertEqual(set(json.loads(raw)),{'version','sequence','project_id','task_id','contract_sha256'})
        self.assertNotIn('command',raw)
        self.assertEqual((self.root/'pending'/name).stat().st_mode & 0o777,0o600)

    def test_sequence_survives_empty_pending_history(self):
        _,first=self.submit()
        self.finish(self.claim())
        (self.root/'completed'/first).unlink()  # simulated retention
        _,second=self.submit(task='T2')
        self.assertGreater(int(second[:12]),int(first[:12]))

if __name__=='__main__':unittest.main()
