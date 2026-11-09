#!/usr/bin/env python3
"""One-time fail-closed quarantine of historical read-only task misrouted to code."""
from __future__ import annotations
import argparse
import importlib.util
import sys
from pathlib import Path

# Resolve the exact trusted sibling module. In full-suite tests the package
# name "orchestrator" may already refer to a different loaded namespace.
_SPEC = importlib.util.spec_from_file_location(
    "ai_prof_readonly_repair_orchestrator", Path(__file__).with_name("orchestrator.py")
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("trusted_orchestrator_unavailable")
orch = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = orch
_SPEC.loader.exec_module(orch)

TASK_ID = "AI_PROF_CONTROL_CENTER_20261108T151005Z_37A93D"
PROJECT_PATH = "/home/agent/projects/ai-prof-control-center-maintenance"
WORK_BRANCH = "feature/telegram-1907d742-651e86422ede"
REASON = "READONLY_AUDIT_INCORRECTLY_ROUTED_TO_CODE"
DEFAULT_STATE = Path("/home/agent/.local/state/ai-prof-control-center")

class QuarantineRefused(RuntimeError):
    pass

def quarantine(state: Path, *, apply: bool = False) -> str:
    src = state / "queue/approved" / f"{TASK_ID}.md"
    dest = state / "queue/blocked" / src.name
    if src.is_symlink() or not src.is_file() or src.stat().st_size > 1048576:
        raise QuarantineRefused("source_unavailable_or_unsafe")
    if dest.exists() or dest.is_symlink():
        raise QuarantineRefused("target_exists")
    body = src.read_text(encoding="utf-8")
    required = (
        f"Task-ID: {TASK_ID}",
        "Execution-Mode: code",
        "Operation-Profile: none",
        f"Project-Path: {PROJECT_PATH}",
        f"Work-Branch: {WORK_BRANCH}",
        "Owner-Approval-Required: yes",
        "Scope-Files: README.md",
        "Ничего не удаляй и не изменяй.",
    )
    if any(body.count(item) != 1 for item in required):
        raise QuarantineRefused("historical_identity_mismatch")
    if "Safety-Disposition:" in body:
        raise QuarantineRefused("already_dispositioned")
    if not apply:
        return "QUARANTINE_DRY_RUN_PASS " + TASK_ID
    # Write metadata after safely moving; original content and logs remain preserved.
    target = orch.safe_move(src, dest.parent)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(f"\nSafety-Disposition: {REASON}\n")
    return "QUARANTINE_APPLIED " + TASK_ID

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(quarantine(args.state, apply=args.apply))
    except (OSError, ValueError, QuarantineRefused, orch.AtomicMoveUnavailable) as exc:
        print("QUARANTINE_REFUSED:", type(exc).__name__, str(exc))
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
