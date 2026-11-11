from __future__ import annotations
import fcntl
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'orchestrator'))
import global_fifo_cutover_review as review
import global_agent_admission as gate


class CutoverReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.legacy=self.root/'legacy'
        self.fifo=self.root/'fifo'
        self.lock=self.root/'legacy.lock'
        self.glock=self.root/'global.lock'
        (self.legacy/'autonomous_pending').mkdir(parents=True)
        (self.legacy/'autonomous_active').mkdir()
        self.lock.write_text('')
        self.lock.chmod(0o600)
        self.reg=Path(__file__).resolve().parents[1]

    def check(self):
        return review.review(legacy_root=self.legacy,fifo_root=self.fifo,
                             legacy_lock=self.lock,global_lock=self.glock,registry_root=self.reg)

    def test_empty_snapshot_is_quiescent_not_cutover_authority(self):
        self.assertEqual(self.check(),{'ready':True,'reason':'QUIESCENT_SNAPSHOT_ONLY'})
        self.assertFalse(self.fifo.exists())

    def test_real_pending_task_denied(self):
        (self.legacy/'autonomous_pending'/'AI_PROF_AUTO_MAIN_999.json').write_text('{}')
        self.assertEqual(self.check()['reason'],'PENDING_TASK_REQUIRES_HANDOFF')

    def test_active_legacy_task_denied(self):
        (self.legacy/'autonomous_active'/'AI_PROF_AUTO_MAIN_999.json').write_text('{}')
        self.assertEqual(self.check()['reason'],'ACTIVE_TASK_REQUIRES_RECOVERY')

    def test_fifo_pending_requires_handoff(self):
        (self.fifo/'pending').mkdir(parents=True)
        (self.fifo/'pending'/'000000000001-0000000000000000.json').write_text('{}')
        self.assertEqual(self.check()['reason'],'PENDING_TASK_REQUIRES_HANDOFF')

    def test_fifo_active_requires_recovery(self):
        (self.fifo/'active').mkdir(parents=True)
        (self.fifo/'active'/'000000000001-0000000000000000.json').write_text('{}')
        self.assertEqual(self.check()['reason'],'ACTIVE_TASK_REQUIRES_RECOVERY')

    def test_legacy_worker_lock_contention_blocks(self):
        fd=os.open(self.lock,os.O_RDONLY)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual(self.check()['reason'],'LEGACY_WORKER_BUSY')
        finally:os.close(fd)

    def test_global_agent_lock_contention_blocks(self):
        with gate.acquire('kol-travel-platform',registry_root=self.reg,lock_path=self.glock):
            self.assertEqual(self.check()['reason'],'GLOBAL_AGENT_BUSY')

    def test_unsafe_legacy_symlink_rejected(self):
        outside=self.root/'outside';outside.write_text('')
        self.lock.unlink();self.lock.symlink_to(outside)
        with self.assertRaises(review.CutoverError):self.check()

    def test_legacy_lock_world_readable_rejected(self):
        self.lock.chmod(0o644)
        with self.assertRaises(review.CutoverError):self.check()

    def test_missing_legacy_lock_rejected_no_creation(self):
        self.lock.unlink()
        with self.assertRaises(review.CutoverError):self.check()
        self.assertFalse(self.lock.exists())

    def test_queue_symlink_rejected(self):
        (self.legacy/'autonomous_pending').rmdir()
        (self.legacy/'autonomous_pending').symlink_to(self.legacy/'autonomous_active')
        with self.assertRaises(review.CutoverError):self.check()

    def test_invalid_entry_rejected(self):
        (self.legacy/'autonomous_pending'/'wrong.txt').write_text('')
        with self.assertRaises(review.CutoverError):self.check()

    def test_does_not_start_worker_or_change_task_contents(self):
        task=self.legacy/'autonomous_pending'/'AI_PROF_AUTO_MAIN_999.json'
        task.write_text('{"cannot":"execute"}')
        before=task.read_bytes()
        with mock.patch('global_fifo_cutover_review.worker.run_pilot_process') as run:
            self.assertFalse(self.check()['ready'])
            run.assert_not_called()
        self.assertEqual(task.read_bytes(),before)

if __name__=='__main__':unittest.main()
