#!/usr/bin/env python3
"""AI PROF Telegram Control Plane V4 exact-scope adapter.

V4 keeps V3 authorization, commands, polling, terminal notifications and
secret handling unchanged. It narrows task intake only when the owner names
exactly one project-relative file path that is already contained by the
project registry allowlist. Otherwise the proven V1/V2 heuristic remains in
force.

This prevents a request such as ``create docs/example.md`` from exposing the
whole ``docs`` tree to Stage 01B when one exact file is sufficient.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

import telegram_bridge as legacy
import telegram_bridge_v3 as v3

_ORIGINAL_SELECT_SCOPE = legacy.select_scope
_EXPLICIT_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9._/-])((?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+)(?![A-Za-z0-9._/-])"
)
SUBMIT_TASK_V2 = legacy.ROOT / "orchestrator/submit_task_v2.py"


def _safe_project_relative(path: str) -> bool:
    if not path or path.startswith("/") or "\\" in path or "\x00" in path:
        return False
    parts = PurePosixPath(path).parts
    return bool(parts) and all(part not in {"", ".", ".."} for part in parts)


def _inside_allowed_scope(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        root = pattern[:-3]
        return path.startswith(root + "/")
    return path == pattern


def explicit_allowed_paths(command, project: dict) -> list[str]:
    allowed = project.get("allowed_scope")
    if not isinstance(allowed, list):
        return []
    text = f"{command.title}\n{command.instructions}"
    found: list[str] = []
    for match in _EXPLICIT_PATH_RE.finditer(text):
        candidate = match.group(1)
        if not _safe_project_relative(candidate):
            continue
        if not any(
            isinstance(pattern, str) and _inside_allowed_scope(candidate, pattern)
            for pattern in allowed
        ):
            continue
        if candidate not in found:
            found.append(candidate)
    return found


def select_scope_v4(command, project_id: str, project: dict) -> str:
    """Prefer one explicit allowlisted file; otherwise preserve legacy selection."""
    exact = explicit_allowed_paths(command, project)
    if len(exact) == 1:
        return exact[0]
    return _ORIGINAL_SELECT_SCOPE(command, project_id, project)


def main() -> int:
    legacy.select_scope = select_scope_v4
    legacy.SUBMIT_TASK = SUBMIT_TASK_V2
    return v3.main()


if __name__ == "__main__":
    raise SystemExit(main())
