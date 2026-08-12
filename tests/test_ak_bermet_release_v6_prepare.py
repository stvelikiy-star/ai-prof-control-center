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
SPEC = importlib.util.spec_from_file_location(
    "ak_bermet_release_v6_prepare_test", MODULE_PATH
)
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
        self.assertEqual(
            release.FROZEN_SHA,
            "bd7912d4d5cd41603522c205e58b587d0063e6fe",
        )
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
            values = self.release_env()
            secret.write_text(
                "\n".join(f"{key}={values[key]}" for key in release.REQUIRED_RELEASE_ENV)
                + "\nUNRELATED=value\n",
                encoding="utf-8",
            )
            secret.chmod(0o600)
            with mock.patch.object(release, "SECRET_FILE", secret):
                actual = release.read_secret_environment()
            self.assertEqual(set(actual), set(release.REQUIRED_RELEASE_ENV))
            self.assertEqual(actual["SUPABASE_PROJECT_REF"], "ednqgzgjhnalsiiuekmw")
            secret.chmod(0o644)
            with mock.patch.object(release, "SECRET_FILE", secret):
                with self.assertRaisesRegex(release.PrepareBlocked, "MODE_INVALID"):
                    release.read_secret_environment()

    def test_read_only_v6_preflight_uses_supabase_primary_env_only(self):
        report = release.PrepareReport(release.FROZEN_SHA, str(release.PROJECT))
        completed = subprocess.CompletedProcess(["node"], 0, "RESULT: PASS\n", "")
        release_env = self.release_env()
        with (
            mock.patch.object(release, "locate_node_bin", return_value=Path("/node22/bin")),
            mock.patch.object(release, "run", return_value=completed) as runner,
        ):
            _node, env = release.validate_preflight(report, release_env)
        self.assertEqual(runner.call_count, 1)
        argv = runner.call_args.args[0]
        self.assertEqual(argv[-1], "scripts/production-preflight.mjs")
        self.assertNotIn("preflight:production", " ".join(argv))
        self.assertNotIn("AK_BERMET_BACKUP_APPROVED", env)
        self.assertEqual(report.structural_preflight, "PASS")
        self.assertEqual(
            report.production_preflight,
            "PASS_SUPABASE_PRIMARY_CONTRACT",
        )

    def test_remote_ledger_uses_read_only_management_api_without_db_password(self):
        expected = set(release.EXPECTED_MIGRATIONS)
        payload = json.dumps(
            [{"version": item, "name": f"migration_{i}"} for i, item in enumerate(release.EXPECTED_MIGRATIONS)]
        )
        report = release.PrepareReport(release.FROZEN_SHA, "/repo")
        release_env = self.release_env()
        with (
            mock.patch.object(release, "local_migrations", return_value=expected),
            mock.patch.object(
                release.urllib.request,
                "urlopen",
                return_value=FakeResponse(payload),
            ) as opener,
        ):
            release.validate_migration_ledger(report, release_env)

        request = opener.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(
            request.full_url,
            "https://api.supabase.com/v1/projects/ednqgzgjhnalsiiuekmw/database/migrations",
        )
        self.assertEqual(
            request.get_header("Authorization"),
            "Bearer sbp_test_secret_token",
        )
        self.assertNotIn(release_env["SUPABASE_DB_PASSWORD"], request.full_url)
        self.assertEqual(report.migration_ledger, "PASS_18_OF_18")
        self.assertEqual(
            report.migration_action,
            "SKIPPED_ALREADY_RECONCILED",
        )

    def test_remote_ledger_divergence_fails_closed(self):
        expected = set(release.EXPECTED_MIGRATIONS)
        payload = json.dumps(
            [{"version": item} for item in release.EXPECTED_MIGRATIONS[:-1]]
        )
        report = release.PrepareReport(release.FROZEN_SHA, "/repo")
        with (
            mock.patch.object(release, "local_migrations", return_value=expected),
            mock.patch.object(
                release.urllib.request,
                "urlopen",
                return_value=FakeResponse(payload),
            ),
        ):
            with self.assertRaisesRegex(
                release.PrepareBlocked,
                "LINKED_MIGRATION_LEDGER_DIVERGED",
            ):
                release.validate_migration_ledger(report, self.release_env())

    def test_management_api_failure_does_not_echo_token(self):
        with mock.patch.object(
            release.urllib.request,
            "urlopen",
            side_effect=OSError("network failed"),
        ):
            with self.assertRaisesRegex(
                release.PrepareBlocked,
                "SUPABASE_MIGRATION_HISTORY_UNAVAILABLE",
            ) as caught:
                release.remote_migrations(self.release_env())
        self.assertNotIn("sbp_test_secret_token", str(caught.exception))

    def test_backup_evidence_is_read_only_and_restore_remains_gated(self):
        with tempfile.TemporaryDirectory() as tmp:
            backup = Path(tmp) / "20260811T084222Z"
            backup.mkdir()
            for name, data in (
                ("roles.sql", b"roles"),
                ("schema.sql", b"schema"),
                ("data.sql", b"data"),
            ):
                (backup / name).write_bytes(data)
            report = release.PrepareReport(release.FROZEN_SHA, "/repo")
            with mock.patch.object(release, "BACKUP_ROOT", Path(tmp)):
                release.validate_backup_evidence(report)
            self.assertEqual(report.backup_evidence, "PASS:20260811T084222Z")
            self.assertEqual(
                report.restore_smoke,
                "REQUIRED_BEFORE_PRODUCTION_CHANGE",
            )

    def test_public_site_legacy_fingerprint_never_claims_deployment_target(self):
        report = release.PrepareReport(release.FROZEN_SHA, "/repo")
        with mock.patch.object(
            release.urllib.request,
            "urlopen",
            return_value=FakeResponse(
                "Hotel Prime Make Booking Proceed to checkout"
            ),
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
                return_value=self.release_env(),
            ),
            mock.patch.object(release, "validate_preflight"),
            mock.patch.object(release, "validate_migration_ledger"),
            mock.patch.object(release, "validate_backup_evidence"),
            mock.patch.object(release, "inspect_public_site"),
        ):
            actual = release.prepare()
        self.assertFalse(actual.production_changed)
        self.assertIn(
            "RESTORE_SMOKE_REQUIRED_BEFORE_PRODUCTION_CHANGE",
            actual.blockers,
        )
        self.assertIn("DEPLOYMENT_TARGET_UNVERIFIED", actual.blockers)
        self.assertIn("ROLLBACK_SAFE_CUTOVER_UNVERIFIED", actual.blockers)

    def test_source_has_no_remote_mutation_or_password_argv(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("database/migrations", source)
        self.assertIn('method="GET"', source)
        self.assertNotIn('"migration", "list"', source)
        self.assertNotIn('"--password"', source)
        self.assertNotIn('"-p",', source)
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
