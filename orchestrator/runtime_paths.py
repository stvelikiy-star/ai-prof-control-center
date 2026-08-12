"""Shared source and runtime path policy for the Control Center."""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_STATE_ROOT = Path("/home/agent/.local/state/ai-prof-control-center")
QUEUE_NAMES = (
    "pending", "active", "review", "pending_codex", "approved",
    "blocked", "failed", "cancelled", "completed",
)


def initialize(value: str | Path | None = None) -> Path:
    configured = value if value is not None else os.environ.get(
        "AI_PROF_STATE_DIR", str(DEFAULT_STATE_ROOT),
    )
    root = Path(configured).expanduser().resolve()
    for name in QUEUE_NAMES:
        (root / "queue" / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    (root / "logs" / "orchestrator").mkdir(parents=True, exist_ok=True, mode=0o700)
    (root / "run").mkdir(parents=True, exist_ok=True, mode=0o700)
    return root
