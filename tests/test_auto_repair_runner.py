from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "orchestrator" / "auto_repair_runner.py"
SPEC = importlib.util.spec_from_file_location('ai_prof_auto_repair_test', MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError('Cannot load auto_repair_runner')
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


class AutoRepairRunnerTests(unittest.TestCase):
    def test_review_attempt_round_trip(self):
        text = 'Task-ID: X\nCodex-Review-Attempt: 2\n'
        self.assertEqual(runner.review_attempt(text), 2)
        self.assertEqual(runner.review_attempt(runner.set_review_attempt(text, 3)), 3)

    def test_review_attempt_is_added_when_missing(self):
        updated = runner.set_review_attempt('Task-ID: X\n', 1)
        self.assertEqual(runner.review_attempt(updated), 1)

    def test_feedback_is_replaced_not_duplicated(self):
        text = runner.set_auto_feedback('Task-ID: X\n', 'first')
        text = runner.set_auto_feedback(text, 'second')
        self.assertEqual(text.count(runner.AUTO_FEEDBACK_MARKER), 1)
        self.assertIn('second', text)
        self.assertNotIn('first', text)

    def test_check_command_must_be_exact_required_check(self):
        self.assertEqual(
            runner.validated_check_argv('npx tsc --noEmit', ['npx tsc --noEmit']),
            ['npx', 'tsc', '--noEmit'],
        )
        with self.assertRaises(runner.AutoRepairError):
            runner.validated_check_argv('bash -c whoami', ['npx tsc --noEmit'])

    def test_configured_cycle_limit_is_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'orchestrator').mkdir()
            (root / 'orchestrator' / 'config.json').write_text(
                json.dumps({'max_fix_cycles': 5}), encoding='utf-8'
            )
            self.assertEqual(runner.load_max_fix_cycles(root), 5)

    def test_secret_redaction(self):
        self.assertIn('[REDACTED]', runner.redact('api_key=secret-value'))

    def _git_repo(self, root: Path) -> Path:
        subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=root, check=True)
        subprocess.run(['git', 'config', 'user.name', 'test'], cwd=root, check=True)
        subprocess.run(['git', 'config', 'user.email', 'test@example.invalid'], cwd=root, check=True)
        (root / 'src').mkdir()
        (root / 'src' / 'tracked.txt').write_text('base\n', encoding='utf-8')
        subprocess.run(['git', 'add', '.'], cwd=root, check=True)
        subprocess.run(['git', 'commit', '-qm', 'base'], cwd=root, check=True)
        return root

    def test_failed_candidate_is_backed_up_and_scope_restored(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as backup_tmp:
            project = self._git_repo(Path(tmp))
            (project / 'src' / 'tracked.txt').write_text('candidate\n', encoding='utf-8')
            (project / 'src' / 'new.txt').write_text('new candidate\n', encoding='utf-8')
            backup = Path(backup_tmp)
            scope = ('src/tracked.txt', 'src/new.txt')

            runner.backup_failed_candidate(project, scope, backup)
            runner.restore_task_scope_to_head(project, scope)

            self.assertEqual((project / 'src' / 'tracked.txt').read_text(), 'base\n')
            self.assertFalse((project / 'src' / 'new.txt').exists())
            self.assertEqual(
                (backup / 'candidate/files/src/tracked.txt').read_text(), 'candidate\n'
            )
            self.assertEqual(
                (backup / 'candidate/files/src/new.txt').read_text(), 'new candidate\n'
            )
            self.assertIn('src/tracked.txt', (backup / 'candidate/tracked.patch').read_text())
            self.assertEqual(runner.dirty_paths(project), set())

    def test_scope_restore_refuses_unrelated_dirty_path_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._git_repo(Path(tmp))
            (project / 'outside.txt').write_text('outside\n', encoding='utf-8')
            subprocess.run(['git', 'add', 'outside.txt'], cwd=project, check=True)
            subprocess.run(['git', 'commit', '-qm', 'outside'], cwd=project, check=True)
            (project / 'src' / 'tracked.txt').write_text('candidate\n', encoding='utf-8')
            (project / 'outside.txt').write_text('owner work\n', encoding='utf-8')

            with self.assertRaises(runner.AutoRepairError):
                runner.restore_task_scope_to_head(project, ('src/tracked.txt',))

            self.assertEqual((project / 'src' / 'tracked.txt').read_text(), 'candidate\n')
            self.assertEqual((project / 'outside.txt').read_text(), 'owner work\n')

    def test_latest_check_failure_log_skips_newer_empty_diff_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = runner.build_paths(root, root / 'state')
            task_id = 'AK_BERMET_TEST'
            old = paths.logs / f'{task_id}-01B-20260828T010000Z.log'
            new = paths.logs / f'{task_id}-01B-20260828T020000Z.log'
            old.write_text('CODEX_STAGE01B_FAILED\ncheck failed: npm run test:inspection\n', encoding='utf-8')
            new.write_text('CODEX_STAGE01B_FAILED\nCodexExecutionError: BLOCKED_EMPTY_IMPLEMENTATION_DIFF\n', encoding='utf-8')
            old.touch()
            new.touch()
            import os
            os.utime(old, (1, 1))
            os.utime(new, (2, 2))
            found = runner.latest_check_failure_log(paths, task_id)
            self.assertIsNotNone(found)
            self.assertEqual(found[0], old)
            self.assertEqual(found[1].group(1), 'npm run test:inspection')

    def test_legacy_ak_bermet_check_is_normalized(self):
        text = (
            'Required-Checks: npm run lint, '
            + runner.LEGACY_AK_BERMET_INSPECTION_CHECK
            + ', npm run build\n'
        )
        updated, command = runner.normalize_legacy_ak_bermet_check(
            text, runner.AK_BERMET_PROJECT, runner.LEGACY_AK_BERMET_INSPECTION_CHECK
        )
        self.assertEqual(command, runner.AK_BERMET_INSPECTION_CHECK)
        self.assertIn(runner.AK_BERMET_INSPECTION_CHECK, updated)
        self.assertNotIn(runner.LEGACY_AK_BERMET_INSPECTION_CHECK, updated)


if __name__ == '__main__':
    unittest.main(verbosity=2)
