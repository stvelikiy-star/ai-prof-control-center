from __future__ import annotations

import importlib.util
import os
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
    def test_frozen_sha_and_exact_18_migrations_are_immutable(self):
        self.assertEqual(release.FROZEN_SHA, "e26ced6a187a654baf858bc8b13044f4123f0a8a")
        self.assertEqual(len(release.EXPECTED_MIGRATIONS), 18)
        self.assertEqual(len(set(release.EXPECTED_MIGRATIONS)), 18)
        self.assertEqual(release.EXPECTED_MIGRATIONS[-1], "20260728000100")

    def test_subprocess_boundary_is_shell_false(self):
        completed = subprocess.CompletedProcess(["/usr/bin/git", "status"], 0, "", "")
        with mock.patch.object(release.subprocess, "run", return_value=completed) as runner:
            release.run(["/usr/bin/git", "status"], cwd=Path("/tmp"), env={})
        self.assertIs(runner.call_args.kwargs["shell"], False)
        self.assertEqual(runner.call_args.args[0], ["/usr/bin/git", "status"])

    def test_release_secret_file_requires_private_mode_and_only_release_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "release.env"
            secret.write_text(
                "\n".join(
                    f"{key}=value-{index}"
                    for index, key in enumerate(release.REQUIRED_RELEASE_ENV)
                )
                + "\nUNRELATED=value\n",
                encoding="utf-8",
            )
            secret.chmod(0o600)
            with mock.patch.object(release, "SECRET_FILE", secret):
                values = release.read_secret_environment()
            self.assertEqual(set(values), set(release.REQUIRED_RELEASE_ENV))
            secret.chmod(0o644)
            with mock.patch.object(release, "SECRET_FILE", secret):
                with self.assertRaisesRegex(release.PrepareBlocked, "MODE_INVALID"):
                    release.read_secret_environment()

    def test_app_env_is_separate_private_contract_and_sheets_must_be_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_env = Path(tmp) / ".env.local"
            values = {
                "GOOGLE_SHEETS_ENABLED": "true",
                "GOOGLE_SHEETS_SPREADSHEET_ID": "sheet-id",
                "GOOGLE_SERVICE_ACCOUNT_EMAIL": "service@example.invalid",
                "GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY": "private-key",
            }
            app_env.write_text(
                "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
                encoding="utf-8",
            )
            app_env.chmod(0o600)
            with mock.patch.object(release, "APP_ENV_FILE", app_env):
                actual = release.read_app_environment()
            self.assertEqual(actual, values)

            app_env.write_text(
                app_env.read_text(encoding="utf-8").replace(
                    "GOOGLE_SHEETS_ENABLED=true",
                    "GOOGLE_SHEETS_ENABLED=false",
                ),
                encoding="utf-8",
            )
            with mock.patch.object(release, "APP_ENV_FILE", app_env):
                with self.assertRaisesRegex(release.PrepareBlocked, "GOOGLE_SHEETS_NOT_ENABLED"):
                    release.read_app_environment()

    def test_read_only_v6_preflight_never_requires_backup_approval_gate(self):
        report = release.PrepareReport(release.FROZEN_SHA, str(release.PROJECT))
        completed = subprocess.CompletedProcess(["node"], 0, "RESULT: PASS\n", "")
        release_env = {key: "release-value" for key in release.REQUIRED_RELEASE_ENV}
        app_env = {
            "GOOGLE_SHEETS_ENABLED": "true",
            "GOOGLE_SHEETS_SPREADSHEET_ID": "sheet-id",
            "GOOGLE_SERVICE_ACCOUNT_EMAIL": "service@example.invalid",
            "GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY": "private-key",
        }
        with (
            mock.patch.object(release, "locate_node_bin", return_value=Path("/node22/bin")),
            mock.patch.object(release, "run", return_value=completed) as runner,
        ):
            _node, env = release.validate_preflight(report, release_env, app_env)
        self.assertEqual(runner.call_count, 1)
        argv = runner.call_args.args[0]
        self.assertEqual(argv[-1], "scripts/production-preflight.mjs")
        self.assertNotIn("preflight:production", " ".join(argv))
        self.assertNotIn("AK_BERMET_BACKUP_APPROVED", env)
        self.assertEqual(report.structural_preflight, "PASS")
        self.assertEqual(report.production_preflight, "PASS_V6_ENV_CONTRACT")

    def test_linked_ledger_must_be_exact_and_never_pushes(self):
        expected = set(release.EXPECTED_MIGRATIONS)
        rows = "\n".join(f"{item} | {item}" for item in release.EXPECTED_MIGRATIONS)
        completed = subprocess.CompletedProcess(["supabase"], 0, rows, "")
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            cli = project / "node_modules/.bin/supabase"
            cli.parent.mkdir(parents=True)
            cli.write_text("cli\n", encoding="utf-8")
            report = release.PrepareReport(release.FROZEN_SHA, str(project))
            with (
                mock.patch.object(release, "PROJECT", project),
                mock.patch.object(release, "local_migrations", return_value=expected),
                mock.patch.object(release, "run", return_value=completed) as runner,
            ):
                release.validate_migration_ledger(report, Path("/node"), {})
        argv = runner.call_args.args[0]
        self.assertEqual(argv[-3:], ["migration", "list", "--linked"])
        self.assertNotIn("push", argv)
        self.assertNotIn("reset", argv)
        self.assertEqual(report.migration_ledger, "PASS_18_OF_18")
        self.assertEqual(report.migration_action, "SKIPPED_ALREADY_RECONCILED")

    def test_backup_evidence_is_read_only_and_restore_remains_gated(self):
        with tempfile.TemporaryDirectory() as tmp:
            backup = Path(tmp) / "20260811T084222Z"
            backup.mkdir()
            for name, data in (("roles.sql", b"roles"), ("schema.sql", b"schema"), ("data.sql", b"data")):
                (backup / name).write_bytes(data)
            report = release.PrepareReport(release.FROZEN_SHA, "/repo")
            with mock.patch.object(release, "BACKUP_ROOT", Path(tmp)):
                release.validate_backup_evidence(report)
            self.assertEqual(report.backup_evidence, "PASS:20260811T084222Z")
            self.assertEqual(report.restore_smoke, "REQUIRED_BEFORE_PRODUCTION_CHANGE")
            self.assertEqual((backup / "roles.sql").read_bytes(), b"roles")

    def test_public_site_legacy_fingerprint_never_claims_deployment_target(self):
        report = release.PrepareReport(release.FROZEN_SHA, "/repo")
        with mock.patch.object(
            release.urllib.request,
            "urlopen",
            return_value=FakeResponse("Hotel Prime Make Booking Proceed to checkout"),
        ):
            release.inspect_public_site(report)
        self.assertTrue(report.public_site.startswith("LEGACY_CONFIRMED:"))
        self.assertEqual(report.deployment_target, "UNVERIFIED")

    def test_successful_read_only_prepare_still_blocks_production_change(self):
        with (
            mock.patch.object(release, "validate_repository"),
            mock.patch.object(
                release,
                "read_secret_environment",
                return_value={key: "x" for key in release.REQUIRED_RELEASE_ENV},
            ),
            mock.patch.object(
                release,
                "read_app_environment",
                return_value={key: "x" for key in release.REQUIRED_APP_ENV},
            ),
            mock.patch.object(release, "validate_preflight", return_value=(Path("/node"), {})),
            mock.patch.object(release, "validate_migration_ledger"),
            mock.patch.object(release, "validate_backup_evidence"),
            mock.patch.object(release, "inspect_public_site"),
        ):
            actual = release.prepare()
        self.assertFalse(actual.production_changed)
        self.assertIn("RESTORE_SMOKE_REQUIRED_BEFORE_PRODUCTION_CHANGE", actual.blockers)
        self.assertIn("DEPLOYMENT_TARGET_UNVERIFIED", actual.blockers)
        self.assertIn("ROLLBACK_SAFE_CUTOVER_UNVERIFIED", actual.blockers)

    def test_source_has_no_production_mutation_argv(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        prohibited = [
            '["db", "push"]',
            '["db", "reset"]',
            '"deploy",',
            '"migration", "repair"',
        ]
        for marker in prohibited:
            self.assertNotIn(marker, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
