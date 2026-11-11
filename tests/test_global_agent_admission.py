from __future__ import annotations
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'orchestrator'))
import global_agent_admission as gate


class GlobalAgentAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.registry_root = Path(__file__).resolve().parents[1]
        self.lock = self.root / 'lockdir' / 'agent.lock'

    def acquire(self, project='ai-prof-control-center'):
        return gate.acquire(project, registry_root=self.registry_root, lock_path=self.lock)

    def test_owner_enabled_ai_prof_admitted(self):
        with self.acquire() as lease:
            self.assertEqual(lease.project_id, 'ai-prof-control-center')
            self.assertGreaterEqual(lease.fd, 0)
        with self.acquire():
            pass

    def test_other_enabled_project_respects_the_same_lock(self):
        with self.acquire():
            self.assertIsNone(self.acquire('kol-travel-platform'))
        with self.acquire('kol-travel-platform'):
            self.assertIsNone(self.acquire())

    def test_disabled_project_denied_before_lock(self):
        with self.assertRaises(gate.AdmissionError):
            self.acquire('resort-os')
        self.assertFalse(self.lock.exists())

    def test_unknown_project_denied(self):
        with self.assertRaises(gate.AdmissionError):
            self.acquire('made-up-project')

    def test_empty_project_denied(self):
        with self.assertRaises(gate.AdmissionError):
            self.acquire('')

    def test_symlink_lock_denied(self):
        self.lock.parent.mkdir()
        self.lock.symlink_to(self.root / 'target')
        with self.assertRaises(gate.AdmissionError):
            self.acquire()

    def test_lock_file_permissions(self):
        with self.acquire():
            self.assertEqual(self.lock.stat().st_mode & 0o777, 0o600)

    def test_world_readable_lock_fails_closed(self):
        self.lock.parent.mkdir()
        self.lock.write_text('')
        self.lock.chmod(0o644)
        with self.assertRaises(gate.AdmissionError):
            self.acquire()

    def test_child_process_inherits_lease_across_parent_exit(self):
        # Real OS fork/exec proof. Only a harmless sleep child; never Codex.
        holder = self.acquire()
        child = subprocess.Popen(
            [sys.executable, '-c', 'import time;time.sleep(0.5)'],
            pass_fds=(holder.fd,), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            holder.close()
            self.assertIsNone(self.acquire('kol-travel-platform'))
        finally:
            child.wait(timeout=3)
        with self.acquire('kol-travel-platform'):
            self.assertIsNone(self.acquire())

    def test_exit_exception_releases_slot(self):
        with self.assertRaises(RuntimeError):
            with self.acquire():
                raise RuntimeError('test crash')
        with self.acquire():
            pass

    def test_corrupted_registry_fails_closed(self):
        invalid = self.root / 'invalid';(invalid / 'orchestrator').mkdir(parents=True)
        (invalid / 'orchestrator/projects.json').write_text('broken')
        with self.assertRaises(gate.AdmissionError):
            gate.acquire('ai-prof-control-center',registry_root=invalid,lock_path=self.lock)
        self.assertFalse(self.lock.exists())


if __name__=='__main__':
    unittest.main()
