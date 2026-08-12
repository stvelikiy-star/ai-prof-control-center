from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "orchestrator" / "ak_bermet_release_v6_prepare.py"
SPEC = importlib.util.spec_from_file_location("ak_bermet_release_v6_prepare_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load AK BERMET V6 prepare runner")
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)


class FakeResponse:
    def __init__(self, body: str, url: str = "https://akbermet.kg/en/"):
        self.body = body.encode()
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int):
        return self.body

    def geturl(self):
        return self.url


class AkBermetReleaseV6PrepareTests(unittest.TestCase):
    def release_env(self) -> dict[str, str]:
        values = {key: f"value-{index}" for index, key in enumerate(release.REQUIRED_RELEASE_ENV)}
        values["SUPABASE_PROJECT_REF"] = "ednqgzgjhnalsiiuekmw"
        values["SUPABASE_ACCESS_TOKEN"] = "sbp_test_secret_token"
        return values

    def test_frozen_sha_and_exact_18_migrations_are_immutable(self):
        self.assertEqual(release.FROZEN_SHA, "bd7912d4d5cd41603522c205e58b587d0063e6fe")
        self.assertEqual(len(release.EXPECTED_MIGRATIONS), 18)
        self.assertEqual(len(set(release.EXPECTED_MIGRATIONS)), 18)

    def test_subprocess_boundary_is_shell_false(self):
        completed = subprocess.CompletedProcess(["/usr/bin/git", "status"], 0, "", "")
        with mock.patch.object(release.subprocess, "run", return_value=completed) as runner:
            release.run(["/usr/bin/git", "status"], cwd=Path("/tmp"), env={})
        self.assertIs(runner.call_args.kwargs["shell"], False)

    def test_release_secret_file_requires_private_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "release.env"
            values = self.release_env()
            secret.write_text(
                "\n".join(f"{key}={values[key]}" for key in release.REQUIRED_RELEASE_ENV) + "\n",
                encoding="utf-8",
            )
            secret.chmod(0o600)
            with mock.patch.object(release, "SECRET_FILE", secret):
                actual = release.read_secret_environment()
            self.assertEqual(actual["SUPABASE_PROJECT_REF"], "ednqgzgjhnalsiiuekmw")
            secret.chmod(0o644)
            with mock.patch.object(release, "SECRET_FILE", secret):
                with self.assertRaisesRegex(release.PrepareBlocked, "MODE_INVALID"):
                    release.read_secret_environment()

    def test_remote_ledger_uses_management_api_without_db_password(self):
        expected = set(release.EXPECTED_MIGRATIONS)
        payload = json.dumps([{"version": item} for item in release.EXPECTED_MIGRATIONS])
        report = release.PrepareReport(release.FROZEN_SHA, "/repo")
        release_env = self.release_env()
        with (
            mock.patch.object(release, "local_migrations", return_value=expected),
            mock.patch.object(release.urllib.request, "urlopen", return_value=FakeResponse(payload)) as opener,
        ):
            release.validate_migration_ledger(report, release_env)
        request = opener.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIn("/database/migrations", request.full_url)
        self.assertNotIn(release_env["SUPABASE_DB_PASSWORD"], request.full_url)
        self.assertEqual(report.migration_ledger, "PASS_18_OF_18")

    def test_backup_evidence_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            backup = Path(tmp) / "20260811T084222Z"
            backup.mkdir()
            for name, data in (("roles.sql", b"roles"), ("schema.sql", b"schema"), ("data.sql", b"data")):
                (backup / name).write_bytes(data)
            report = release.PrepareReport(release.FROZEN_SHA, "/repo")
            with mock.patch.object(release, "BACKUP_ROOT", Path(tmp)):
                release.validate_backup_evidence(report)
            self.assertEqual(report.backup_evidence, "PASS:20260811T084222Z")
            self.assertEqual(report.restore_smoke, "PENDING")

    def test_restore_smoke_strips_production_secrets(self):
        report = release.PrepareReport(release.FROZEN_SHA, "/repo")
        completed = subprocess.CompletedProcess(
            [sys.executable],
            0,
            json.dumps({"status": "PASS", "backup": "20260811T084222Z", "production_changed": False}) + "\n",
            "",
        )
        secret_env = {
            "SUPABASE_ACCESS_TOKEN": "token",
            "SUPABASE_DB_PASSWORD": "password",
            "SUPABASE_SERVICE_ROLE_KEY": "service",
            "DATABASE_URL": "postgresql://secret",
            "PATH": "/usr/bin",
        }
        with (
            mock.patch.object(release.RESTORE_HELPER, "is_file", return_value=True),
            mock.patch.object(release.RESTORE_HELPER, "is_symlink", return_value=False),
            mock.patch.object(release.os, "environ", secret_env),
            mock.patch.object(release, "run", return_value=completed) as runner,
        ):
            release.validate_restore_smoke(report, Path("/node/bin"))
        env = runner.call_args.kwargs["env"]
        for key in release.PRODUCTION_SECRET_NAMES:
            self.assertNotIn(key, env)
        self.assertEqual(report.restore_smoke, "PASS:20260811T084222Z")

    def test_public_site_legacy_fingerprint_never_claims_target(self):
        report = release.PrepareReport(release.FROZEN_SHA, "/repo")
        with mock.patch.object(
            release.urllib.request,
            "urlopen",
            return_value=FakeResponse("Hotel Prime Make Booking Proceed to checkout"),
        ):
            release.inspect_public_site(report)
        self.assertTrue(report.public_site.startswith("LEGACY_CONFIRMED:"))
        self.assertEqual(report.deployment_target, "UNVERIFIED")

    def test_successful_prepare_only_blocks_cutover_identity(self):
        report_node = Path("/node22/bin")
        with (
            mock.patch.object(release, "validate_repository"),
            mock.patch.object(release, "read_secret_environment", return_value=self.release_env()),
            mock.patch.object(release, "validate_preflight", return_value=(report_node, {})),
            mock.patch.object(release, "validate_migration_ledger"),
            mock.patch.object(release, "validate_backup_evidence"),
            mock.patch.object(release, "validate_restore_smoke"),
            mock.patch.object(release, "inspect_public_site"),
        ):
            actual = release.prepare()
        self.assertFalse(actual.production_changed)
        self.assertEqual(
            actual.blockers,
            ["DEPLOYMENT_TARGET_UNVERIFIED", "ROLLBACK_SAFE_CUTOVER_UNVERIFIED"],
        )

    def test_source_has_no_remote_mutation_argv(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("database/migrations", source)
        self.assertIn("validate_restore_smoke", source)
        prohibited = [
            '["db", "push"]',
            '["db", "reset"]',
            '"--linked"',
            '"migration", "repair"',
            '"deploy",',
        ]
        for marker in prohibited:
            self.assertNotIn(marker, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
