from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "bootstrap_self_maintenance.py"
SPEC = importlib.util.spec_from_file_location("bootstrap_self_maintenance_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load bootstrap_self_maintenance")
bootstrap = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bootstrap
SPEC.loader.exec_module(bootstrap)


def git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


class BootstrapSelfMaintenanceTests(unittest.TestCase):
    def make_remote_and_source(self, root: Path) -> tuple[Path, Path]:
        remote = root / "remote.git"
        seed = root / "seed"
        source = root / "source"
        git("init", "--bare", "-q", str(remote))
        git("init", "-q", str(seed))
        git("config", "user.email", "ci@example.invalid", cwd=seed)
        git("config", "user.name", "CI", cwd=seed)
        (seed / "README.md").write_text("v1\n", encoding="utf-8")
        git("add", "README.md", cwd=seed)
        git("commit", "-q", "-m", "v1", cwd=seed)
        git("branch", "-M", "main", cwd=seed)
        git("remote", "add", "origin", str(remote), cwd=seed)
        git("push", "-q", "-u", "origin", "main", cwd=seed)
        git("symbolic-ref", "HEAD", "refs/heads/main", cwd=remote)
        git("clone", "-q", str(remote), str(source))
        return seed, source

    def test_create_and_fast_forward_isolated_clone(self):
        with tempfile.TemporaryDirectory(prefix="ai-prof-bootstrap-") as tmp:
            root = Path(tmp)
            seed, source = self.make_remote_and_source(root)
            target = root / "maintenance"

            first = bootstrap.bootstrap(source, target)
            self.assertTrue((target / ".git").is_dir())
            self.assertEqual(git("branch", "--show-current", cwd=target), "maintenance/base")
            self.assertEqual(git("status", "--porcelain", cwd=target), "")
            self.assertEqual(git("rev-parse", "HEAD", cwd=target), first)
            self.assertEqual(first, git("rev-parse", "origin/main", cwd=source))

            (seed / "README.md").write_text("v2\n", encoding="utf-8")
            git("add", "README.md", cwd=seed)
            git("commit", "-q", "-m", "v2", cwd=seed)
            git("push", "-q", "origin", "main", cwd=seed)

            second = bootstrap.bootstrap(source, target)
            self.assertNotEqual(first, second)
            self.assertTrue((target / ".git").is_dir())
            self.assertEqual(git("rev-parse", "HEAD", cwd=target), second)
            self.assertEqual((target / "README.md").read_text(encoding="utf-8"), "v2\n")

    def test_converts_clean_legacy_linked_worktree_to_clone(self):
        with tempfile.TemporaryDirectory(prefix="ai-prof-bootstrap-convert-") as tmp:
            root = Path(tmp)
            _seed, source = self.make_remote_and_source(root)
            target = root / "maintenance"
            git("branch", "maintenance/base", "origin/main", cwd=source)
            git("worktree", "add", str(target), "maintenance/base", cwd=source)
            self.assertTrue((target / ".git").is_file())

            result = bootstrap.bootstrap(source, target)

            self.assertTrue((target / ".git").is_dir())
            self.assertEqual(git("branch", "--show-current", cwd=target), "maintenance/base")
            self.assertEqual(git("rev-parse", "HEAD", cwd=target), result)
            self.assertEqual(git("status", "--porcelain", cwd=target), "")

    def test_dirty_maintenance_checkout_blocks_without_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="ai-prof-bootstrap-dirty-") as tmp:
            root = Path(tmp)
            _seed, source = self.make_remote_and_source(root)
            target = root / "maintenance"
            bootstrap.bootstrap(source, target)
            dirty = target / "README.md"
            dirty.write_text("local change\n", encoding="utf-8")

            with self.assertRaisesRegex(bootstrap.BootstrapError, "dirty"):
                bootstrap.bootstrap(source, target)
            self.assertEqual(dirty.read_text(encoding="utf-8"), "local change\n")

    def test_symlink_target_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="ai-prof-bootstrap-link-") as tmp:
            root = Path(tmp)
            _seed, source = self.make_remote_and_source(root)
            real = root / "real"
            real.mkdir()
            target = root / "maintenance"
            target.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(bootstrap.BootstrapError, "symlink"):
                bootstrap.bootstrap(source, target)


if __name__ == "__main__":
    unittest.main(verbosity=2)
