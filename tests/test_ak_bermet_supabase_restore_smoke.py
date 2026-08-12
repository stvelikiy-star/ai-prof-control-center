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
MODULE_PATH = ROOT / "scripts" / "ak-bermet-supabase-restore-smoke.py"
SPEC = importlib.util.spec_from_file_location("ak_bermet_supabase_restore_smoke_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load restore smoke helper")
restore = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = restore
SPEC.loader.exec_module(restore)


class RestoreSmokeTests(unittest.TestCase):
    def test_config_is_postgres_17_and_disables_migrations_and_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            restore.write_config(workdir, "restore-test", 55432, 55430)
            text = (workdir / "supabase/config.toml").read_text(encoding="utf-8")
        self.assertIn('project_id = "restore-test"', text)
        self.assertIn("major_version = 17", text)
        self.assertIn("[db.migrations]", text)
        self.assertIn("enabled = false", text)
        self.assertIn("[db.seed]", text)

    def test_run_is_shell_false(self):
        completed = subprocess.CompletedProcess(["echo"], 0, "", "")
        with mock.patch.object(restore.subprocess, "run", return_value=completed) as runner:
            restore.run(["echo", "ok"], cwd=Path("/tmp"), env={})
        self.assertIs(runner.call_args.kwargs["shell"], False)

    def test_db_container_name_matches_supabase_cli_convention(self):
        self.assertEqual(
            restore.db_container_name("ak-bermet-restore-123"),
            "supabase_db_ak-bermet-restore-123",
        )

    def test_docker_psql_runs_psql_inside_container(self):
        completed = subprocess.CompletedProcess(["docker"], 0, "rooms\n", "")
        with mock.patch.object(restore, "run", return_value=completed) as runner:
            actual = restore.docker_psql(
                "/usr/bin/docker",
                "supabase_db_restore-test",
                ["-Atq", "-c", "select 1"],
                cwd=Path("/tmp"),
                env={},
            )
        self.assertIs(actual, completed)
        argv = runner.call_args.args[0]
        self.assertEqual(argv[0:2], ["/usr/bin/docker", "exec"])
        self.assertIn("supabase_db_restore-test", argv)
        self.assertIn("psql", argv)
        self.assertIn("PGPASSWORD=postgres", argv)

    def test_source_has_no_host_psql_dependency_and_never_links_remote(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for key in ("SUPABASE_ACCESS_TOKEN", "SUPABASE_DB_PASSWORD", "PGPASSWORD", "DATABASE_URL"):
            self.assertIn(key, source)
        self.assertIn("env.pop(key, None)", source)
        self.assertNotIn('shutil.which("psql")', source)
        self.assertIn('shutil.which("docker")', source)
        self.assertIn('return f"supabase_db_{project_id}"', source)
        self.assertNotIn('"--linked"', source)
        self.assertNotIn('"db", "push"', source)
        self.assertNotIn('"db", "reset"', source)
        self.assertNotIn('"migration", "repair"', source)

    def test_fail_output_is_structured(self):
        with mock.patch("builtins.print") as printer:
            rc = restore.fail("TEST_FAILURE", "detail")
        self.assertEqual(rc, 2)
        payload = json.loads(printer.call_args.args[0])
        self.assertEqual(payload["status"], "FAIL")
        self.assertEqual(payload["code"], "TEST_FAILURE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
