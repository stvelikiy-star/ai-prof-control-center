#!/usr/bin/env python3
"""Night-safe operations entrypoint with explicit full test discovery.

The legacy Control Center health profile calls ``python -m unittest`` for its
"full" check. In this repository that invocation may discover only a tiny
subset of the suite. This wrapper changes exactly that argv to explicit unittest
discovery while preserving every other allowlisted operation and authority
boundary from ``operations_runner.py``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

try:  # Package import under unit tests.
    from orchestrator import operations_runner as base
except ImportError:  # Direct script execution from orchestrator/.
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


def main() -> int:
    base.run_argv = _night_run_argv
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
