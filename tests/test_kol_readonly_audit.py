"""Security and execution contract for the fixed KÖL readonly audit profile."""
from __future__ import annotations

import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orchestrator import kol_readonly_audit as audit


TASK = "KOL_TRAVEL_PLATFORM_20261114T125701Z_23B380"
HEAD = "a" * 40


def sample_archive() -> bytes:
    buff = io.BytesIO()
    with tarfile.open(fileobj=buff, mode="w") as archive:
        files = {f"src/example-{n}.ts": "export const x = 1;\n" for n in range(20)}
        files.update({
            ".env": "PASSWORD=secret",
            "src/key.pem": "PRIVATE KEY",
            "docs/../escape": "bad",
            "src/api/schema.ts": "export interface Schema { name: string }",
            "README.md": "# Kol",
        })
        for name, data in files.items():
            payload = data.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(payload))
    return buff.getvalue()


class KolReadonlyAuditTests(unittest.TestCase):
    def test_scope_never_includes_secrets_or_traversal(self):
        for filename in (".env", "src/.env.local", "supabase/secrets/token",
                         "src/key.pem", "src/../secrets", "/etc/passwd",
                         "src\x00/abc", "src/.git/config", "src/node_modules/x.js"):
            with self.subTest(filename=filename):
                self.assertFalse(audit._allowed(filename))
        for name in ("src/index.ts", "supabase/migrations/001.sql",
                     ".github/workflows/ci.yml", "package.json"):
            self.assertTrue(audit._allowed(name))

    def test_archive_extracts_only_regular_allowlisted_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            def fake(*args, **kwargs):
                return mock.Mock(returncode=0, stdout=sample_archive())
            with mock.patch.object(audit.subprocess, "run", side_effect=fake):
                count, _ = audit.build_snapshot(root, HEAD, snapshot)
            self.assertEqual(count, 22)
            self.assertTrue((snapshot / "src/api/schema.ts").is_file())
            self.assertFalse((snapshot / ".env").exists())
            self.assertFalse((snapshot / "src/key.pem").exists())
            self.assertFalse((root / "escape").exists())
            self.assertFalse((snapshot / "src/example-0.ts").stat().st_mode & 0o222)

    def test_rejects_invalid_task_id(self):
        with self.assertRaisesRegex(audit.AuditError, "invalid"):
            audit.run("../other", codex=Path("/bin/true"))

    def test_profile_is_only_kol_and_cannot_write_via_operation(self):
        from orchestrator.operation_profiles import resolve_profile
        profile = resolve_profile("kol-readonly-full-audit")
        self.assertEqual(profile.kind, "kol-readonly-full-audit")
        self.assertEqual(profile.repository, audit.PROJECT)

    def test_codex_command_is_read_only_and_output_outside_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            repo = home / "repo"
            state = home / "state"
            repo.mkdir()
            state.mkdir()
            (repo / ".git").mkdir()
            codex = home / "codex"
            codex.write_text("#!/bin/sh\nexit 0\n")
            codex.chmod(0o700)
            calls = []
            def fake_run(args, **kwargs):
                calls.append((args, kwargs))
                if args[0] != str(codex):
                    raise AssertionError("unexpected process")
                self.assertIn("read-only", args)
                self.assertNotIn("danger-full-access", args)
                self.assertNotIn("--add-dir", args)
                self.assertIn("-a", args)
                self.assertEqual(args[args.index("-a")+1], "never")
                output = Path(args[args.index("-o")+1])
                self.assertNotIn(str(repo), str(output))
                output.write_text(
                    "# KÖL обзор\n" + ("Supabase, P0, P1, NOT VERIFIED; "
                                        "src/api/schema.ts contains contracts.\n" * 120)
                )
                return mock.Mock(returncode=0, stdout="", stderr="")
            with mock.patch.object(audit, "metadata", return_value=("feature/test", HEAD, "?? old.md")), \
                 mock.patch.object(audit, "build_snapshot", return_value=(30, 1000)), \
                 mock.patch.object(audit.subprocess, "run", side_effect=fake_run):
                result = audit.run(TASK, repo=repo, state=state, codex=codex)
                self.assertTrue(result.is_file())
                self.assertEqual(result.stat().st_mode & 0o777, 0o600)
                self.assertIn("NOT VERIFIED", result.read_text())
                self.assertIn("feature/test", result.read_text())
            self.assertEqual(len(calls), 1)

    def test_failed_codex_does_not_claim_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            repo = home / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            codex = home / "codex"
            codex.write_text("mock")
            codex.chmod(0o700)
            with mock.patch.object(audit, "metadata", return_value=("feature/test", HEAD, "")), \
                 mock.patch.object(audit, "build_snapshot", return_value=(30, 1000)), \
                 mock.patch.object(audit.subprocess, "run", return_value=mock.Mock(returncode=1)):
                with self.assertRaisesRegex(audit.AuditError, "failed"):
                    audit.run(TASK, repo=repo, state=home / "state", codex=codex)
            self.assertFalse((home / "state" / "reports").exists())

    def test_changed_repository_blocks_report_even_after_successful_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            codex = root / "codex"
            codex.write_text("mock")
            codex.chmod(0o700)
            metadata = iter([("feature/test", HEAD, ""), ("feature/test", "b"*40, "")])
            def write_message(args, **kwargs):
                Path(args[args.index("-o")+1]).write_text(
                    ("## Supabase P0 P1 NOT VERIFIED src/index.ts\n") * 150
                )
                return mock.Mock(returncode=0)
            with mock.patch.object(audit, "metadata", side_effect=lambda *_: next(metadata)), \
                 mock.patch.object(audit, "build_snapshot", return_value=(30, 1000)), \
                 mock.patch.object(audit.subprocess, "run", side_effect=write_message):
                with self.assertRaisesRegex(audit.AuditError, "changed"):
                    audit.run(TASK, repo=repo, state=root / "state", codex=codex)


if __name__ == "__main__":
    unittest.main()
