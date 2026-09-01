#!/usr/bin/env python3
"""Night-safe operations entrypoint with explicit full test discovery.

The canonical live Control Center runs this wrapper. It preserves the legacy
allowlisted operation executor, upgrades the full unittest invocation to
explicit discovery, and routes queue processing through the Repair Team
execution-time authority gate before any incident-origin privileged profile is
resolved or executed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

try:  # Package import under unit tests.
    from orchestrator import guarded_operations_process as guarded
    from orchestrator import operations_runner as base
except ImportError:  # Direct script execution from orchestrator/.
    import guarded_operations_process as guarded  # type: ignore[no-redef]
    import operations_runner as base  # type: ignore[no-redef]

_ORIGINAL_RUN_ARGV = base.run_argv
LEGACY_FULL_TEST_ARGV = (
    str(base.PYTHON3_CLI),
    "-m",
    "unittest",
)
STRICT_FULL_TEST_ARGV = (
    str(base.PYTHON3_CLI),
    "-m",
    "unittest",
    "discover",
    "-s",
    "tests",
    "-p",
    "test_*.py",
)


def _upgrade_health_argv(argv: Sequence[str]) -> list[str]:
    if tuple(argv) == LEGACY_FULL_TEST_ARGV:
        return list(STRICT_FULL_TEST_ARGV)
    return list(argv)


def _night_run_argv(
    argv: list[str],
    repository: Path,
    environment: dict[str, str],
    *,
    timeout: int = base.COMMAND_TIMEOUT_SECONDS,
    retry_transient: bool = False,
):
    return _ORIGINAL_RUN_ARGV(
        _upgrade_health_argv(argv),
        repository,
        environment,
        timeout=timeout,
        retry_transient=retry_transient,
    )


def _guarded_process_one(paths) -> int:
    return guarded.process_one(base, paths)


def main() -> int:
    base.run_argv = _night_run_argv
    base.process_one = _guarded_process_one
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
