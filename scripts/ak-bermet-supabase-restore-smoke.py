#!/usr/bin/env python3
"""Ephemeral Supabase-compatible restore smoke for AK BERMET logical backups.

This script only mutates local temporary Docker resources. It never connects to
or changes the linked/production Supabase project.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path("/home/agent/projects/ak-bermet")
BACKUP_ROOT = Path("/home/agent/ai-prof-backups/ak-bermet")
CLI = PROJECT / "node_modules/.bin/supabase"
FILES = ("roles.sql", "schema.sql", "data.sql")
CORE_TABLES = (
    "rooms",
    "leads",
    "bookings",
    "cleaning_tasks",
    "maintenance_requests",
)


def fail(code: str, detail: str | None = None) -> int:
    payload = {"status": "FAIL", "code": code}
    if detail:
        payload["detail"] = detail[:300]
    print(json.dumps(payload, sort_keys=True))
    return 2


def run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        shell=False,
    )


def newest_backup() -> Path:
    candidates = []
    for path in BACKUP_ROOT.iterdir():
        if path.is_dir() and not path.is_symlink() and all((path / name).is_file() for name in FILES):
            candidates.append(path)
    if not candidates:
        raise RuntimeError("BACKUP_NOT_FOUND")
    return max(candidates, key=lambda item: item.stat().st_mtime)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def write_config(workdir: Path, project_id: str, db_port: int, shadow_port: int) -> None:
    supabase_dir = workdir / "supabase"
    supabase_dir.mkdir(parents=True)
    (supabase_dir / "config.toml").write_text(
        "\n".join(
            [
                f'project_id = "{project_id}"',
                "",
                "[db]",
                f"port = {db_port}",
                f"shadow_port = {shadow_port}",
                'health_timeout = "2m"',
                "major_version = 17",
                "",
                "[db.migrations]",
                "enabled = false",
                "",
                "[db.seed]",
                "enabled = false",
                'sql_paths = []',
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def db_container_name(project_id: str) -> str:
    return f"supabase_db_{project_id}"


def docker_psql(
    docker: str,
    container: str,
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    return run(
        [
            docker,
            "exec",
            "-e",
            "PGPASSWORD=postgres",
            container,
            "psql",
            "-U",
            "postgres",
            "-d",
            "postgres",
            *args,
        ],
        cwd=cwd,
        env=env,
        timeout=timeout,
    )


def main() -> int:
    if not CLI.is_file():
        return fail("SUPABASE_CLI_UNAVAILABLE")
    docker = shutil.which("docker")
    if not docker:
        return fail("DOCKER_CLI_UNAVAILABLE")
    try:
        backup = newest_backup()
    except Exception as exc:
        return fail("BACKUP_DISCOVERY_FAILED", type(exc).__name__)

    hashes = {name: hashlib.sha256((backup / name).read_bytes()).hexdigest() for name in FILES}
    if len(set(hashes.values())) != len(FILES):
        return fail("BACKUP_HASH_INVALID")

    env = os.environ.copy()
    for key in ("SUPABASE_ACCESS_TOKEN", "SUPABASE_DB_PASSWORD", "PGPASSWORD", "DATABASE_URL"):
        env.pop(key, None)
    env["GH_PROMPT_DISABLED"] = "1"

    with tempfile.TemporaryDirectory(prefix="ak-bermet-restore-smoke-") as tmp:
        workdir = Path(tmp)
        project_id = f"ak-bermet-restore-{os.getpid()}"
        db_port = free_port()
        shadow_port = free_port()
        while shadow_port == db_port:
            shadow_port = free_port()
        write_config(workdir, project_id, db_port, shadow_port)

        started = False
        container = db_container_name(project_id)
        try:
            start = run(
                [str(CLI), "--workdir", str(workdir), "db", "start"],
                cwd=PROJECT,
                env=env,
                timeout=600,
            )
            if start.returncode != 0:
                return fail("LOCAL_SUPABASE_DB_START_FAILED", start.stderr or start.stdout)
            started = True

            inspect = run(
                [docker, "inspect", "--format", "{{.State.Running}}", container],
                cwd=PROJECT,
                env=env,
                timeout=30,
            )
            if inspect.returncode != 0 or inspect.stdout.strip().lower() != "true":
                return fail("LOCAL_SUPABASE_DB_CONTAINER_UNAVAILABLE", inspect.stderr or inspect.stdout)

            target_root = "/tmp/ak-bermet-restore-smoke"
            mkdir = run(
                [docker, "exec", container, "mkdir", "-p", target_root],
                cwd=PROJECT,
                env=env,
                timeout=30,
            )
            if mkdir.returncode != 0:
                return fail("RESTORE_STAGING_DIR_FAILED", mkdir.stderr or mkdir.stdout)

            for name in FILES:
                copied = run(
                    [docker, "cp", str(backup / name), f"{container}:{target_root}/{name}"],
                    cwd=PROJECT,
                    env=env,
                    timeout=60,
                )
                if copied.returncode != 0:
                    return fail(f"RESTORE_COPY_FAILED:{name}", copied.stderr or copied.stdout)

            roles = docker_psql(
                docker,
                container,
                ["-v", "ON_ERROR_STOP=1", "-f", f"{target_root}/roles.sql"],
                cwd=PROJECT,
                env=env,
            )
            if roles.returncode != 0:
                return fail("LOGICAL_RESTORE_ROLES_FAILED", roles.stderr or roles.stdout)

            schema = docker_psql(
                docker,
                container,
                ["--single-transaction", "-v", "ON_ERROR_STOP=1", "-f", f"{target_root}/schema.sql"],
                cwd=PROJECT,
                env=env,
            )
            if schema.returncode != 0:
                return fail("LOGICAL_RESTORE_SCHEMA_FAILED", schema.stderr or schema.stdout)

            data = docker_psql(
                docker,
                container,
                [
                    "--single-transaction",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-c",
                    "SET session_replication_role = replica",
                    "-f",
                    f"{target_root}/data.sql",
                ],
                cwd=PROJECT,
                env=env,
            )
            if data.returncode != 0:
                return fail("LOGICAL_RESTORE_DATA_FAILED", data.stderr or data.stdout)

            table_query = (
                "select tablename from pg_tables where schemaname='public' and tablename in ("
                + ",".join("'%s'" % name for name in CORE_TABLES)
                + ") order by tablename;"
            )
            check = docker_psql(
                docker,
                container,
                ["-Atq", "-c", table_query],
                cwd=PROJECT,
                env=env,
                timeout=60,
            )
            if check.returncode != 0:
                return fail("RESTORE_VERIFY_QUERY_FAILED", check.stderr or check.stdout)
            restored = [line.strip() for line in check.stdout.splitlines() if line.strip()]
            missing = sorted(set(CORE_TABLES) - set(restored))
            if missing:
                return fail("RESTORE_CORE_TABLES_MISSING", ",".join(missing))

            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "backup": backup.name,
                        "core_tables": restored,
                        "hashes": hashes,
                        "production_changed": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        finally:
            if started:
                run(
                    [str(CLI), "--workdir", str(workdir), "stop", "--no-backup"],
                    cwd=PROJECT,
                    env=env,
                    timeout=180,
                )


if __name__ == "__main__":
    raise SystemExit(main())
