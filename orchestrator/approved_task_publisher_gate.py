#!/usr/bin/env python3
"""Fail-closed control-loop adapter for the approved task publisher."""
from __future__ import annotations

import approved_task_publisher


def main() -> int:
    result = approved_task_publisher.main()
    # control_loop treats child rc=1 as a normal task result. Publishing is a
    # repository state gate, so any publisher failure must halt the cycle.
    return 0 if result == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
