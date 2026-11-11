from __future__ import annotations
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'orchestrator'))
import atomic_route_guard as route
import autonomous_shared_queue_worker as worker
import gateway_autonomous_intake as intake
from tests.test_autonomous_shared_queue import owner_issue


class RouteAtomicTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.run=self.root/'run';self.run.mkdir(mode=0o700)
        self.rlock=self.run/'route.lock'
        self.mode=self.run/'route.json'
        self.worker=self.run/'worker.lock';self.worker.write_text('');self.worker.chmod(0o600)
        self.global_lock=self.run/'global.lock'
        self.legacy=self.root/'queue';self.legacy.mkdir(mode=0o700)
        for part in ('autonomous_pending','autonomous_active','autonomous_completed','autonomous_blocked'):
            (self.legacy/part).mkdir()
        self.fifo=self.legacy/'global-agent-fifo-v1'
        p=mock.patch.object(route,'ROUTE_LOCK',self.rlock);p.start();self.addCleanup(p.stop)
        p=mock.patch.object(route,'MODE_FILE',self.mode);p.start();self.addCleanup(p.stop)
        p=mock.patch.object(intake,'QUEUE_ROOT',self.legacy);p.start();self.addCleanup(p.stop)
        p=mock.patch.object(worker,'RUN_LOCK',self.worker);p.start();self.addCleanup(p.stop)

    def change(self,target):
        return route.transition(target,lock=self.rlock,file=self.mode,
                                worker_lock=self.worker,legacy=self.legacy,fifo=self.fifo,
                                global_lock=self.global_lock,
                                registry_root=Path(__file__).resolve().parents[1])

    def test_legacy_default_and_freeze_restore(self):
        self.assertEqual(route.mode(),'legacy')
        self.assertEqual(self.change('frozen'),'frozen')
        self.assertFalse(route.permitted())
        self.assertEqual(self.mode.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.change('legacy'),'legacy')
        self.assertTrue(route.permitted())

    def test_idempotent_freeze(self):
        self.assertEqual(self.change('frozen'),'frozen')
        self.assertEqual(self.change('frozen'),'frozen')

    def test_direct_fifo_activation_forbidden(self):
        with self.assertRaises(route.RouteError):self.change('fifo')
        self.assertFalse(self.mode.exists())

    def test_explicit_frozen_intermediary_required_for_fifo_and_rollback(self):
        self.assertEqual(self.change('frozen'),'frozen')
        self.assertEqual(self.change('fifo'),'fifo')
        with self.assertRaises(route.RouteError):self.change('legacy')
        self.assertEqual(self.change('frozen'),'frozen')
        self.assertEqual(self.change('legacy'),'legacy')

    def test_pending_legacy_blocks_freeze(self):
        (self.legacy/'autonomous_pending'/'AI_PROF_AUTO_MAIN_900.json').write_text('{}')
        with self.assertRaises(route.RouteError):self.change('frozen')
        self.assertFalse(self.mode.exists())

    def test_active_legacy_blocks_freeze(self):
        (self.legacy/'autonomous_active'/'AI_PROF_AUTO_MAIN_900.json').write_text('{}')
        with self.assertRaises(route.RouteError):self.change('frozen')

    def test_pending_fifo_blocks_freeze(self):
        (self.fifo/'pending').mkdir(parents=True)
        (self.fifo/'pending'/'000000000001-abcd1234abcd1234.json').write_text('{}')
        with self.assertRaises(route.RouteError):self.change('frozen')

    def test_active_fifo_blocks_freeze(self):
        (self.fifo/'active').mkdir(parents=True)
        (self.fifo/'active'/'000000000001-abcd1234abcd1234.json').write_text('{}')
        with self.assertRaises(route.RouteError):self.change('frozen')

    def test_running_worker_prevents_transition(self):
        fd=os.open(self.worker,os.O_RDWR)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(route.RouteError):self.change('frozen')
        finally:os.close(fd)

    def test_global_agent_busy_prevents_transition(self):
        import global_agent_admission as admission
        with admission.acquire('kol-travel-platform',lock_path=self.global_lock):
            with self.assertRaises(route.RouteError):self.change('frozen')

    def test_missing_worker_lock_fails_closed(self):
        self.worker.unlink()
        with self.assertRaises(route.RouteError):self.change('frozen')

    def test_unsafe_worker_lock_fails_closed(self):
        self.worker.chmod(0o644)
        with self.assertRaises(route.RouteError):self.change('frozen')

    def test_symlink_route_mode_fails_closed(self):
        self.mode.symlink_to(self.root/'other')
        with self.assertRaises(route.RouteError):route.mode()
        with self.assertRaises(route.RouteError):self.change('frozen')

    def test_corrupt_route_mode_fails_closed(self):
        self.mode.write_text('{invalid')
        self.mode.chmod(0o600)
        with self.assertRaises(route.RouteError):route.mode()
        with self.assertRaises(route.RouteError):self.change('frozen')

    def test_unapproved_route_mode_fails_closed(self):
        self.mode.write_text(json.dumps({'version':1,'mode':'unsupported'}))
        self.mode.chmod(0o600)
        with self.assertRaises(route.RouteError):route.mode()

    def test_gateway_does_not_enqueue_when_frozen(self):
        self.change('frozen')
        state={}
        self.assertFalse(intake.process(owner_issue(number=900),state))
        self.assertFalse(state)
        self.assertEqual(list((self.legacy/'autonomous_pending').iterdir()),[])

    def test_gateway_resumes_legacy_after_thaw(self):
        self.change('frozen')
        state={}
        self.assertFalse(intake.process(owner_issue(number=900),state))
        self.change('legacy')
        self.assertTrue(intake.process(owner_issue(number=900),state))
        self.assertEqual(state['900']['status'],'autonomous_queued')

    def test_worker_does_not_launch_when_frozen(self):
        self.change('frozen')
        with mock.patch.object(worker,'run_pilot_process') as runner:
            self.assertEqual(worker.tick(),'RESOURCE_DEFERRED_ROUTE_FROZEN')
            runner.assert_not_called()

    def test_worker_fails_closed_on_corrupt_route(self):
        self.mode.write_text('invalid');self.mode.chmod(0o600)
        with mock.patch.object(worker,'run_pilot_process') as runner:
            self.assertEqual(worker.tick(),'RESOURCE_DEFERRED_ROUTE_UNVERIFIED')
            runner.assert_not_called()

    def test_gateway_fails_closed_on_corrupt_route(self):
        self.mode.write_text('invalid');self.mode.chmod(0o600)
        with self.assertRaises(intake.AutoIntakeError):
            intake.process(owner_issue(number=900),{})

    def test_symlink_queue_blocks_freeze(self):
        (self.legacy/'autonomous_pending').rmdir()
        (self.legacy/'autonomous_pending').symlink_to(self.root)
        with self.assertRaises(route.RouteError):self.change('frozen')

    def test_no_queue_changes_during_freeze(self):
        self.change('frozen')
        self.assertFalse(self.fifo.exists())
        self.assertEqual(list((self.legacy/'autonomous_pending').iterdir()),[])


if __name__=='__main__':unittest.main()
