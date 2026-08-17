#!/usr/bin/env python3
"""AI PROF Stage 01B Codex V2 compatibility adapter.

Keeps the hardened Stage 01B implementation runner unchanged and patches only
its model-facing instruction contract plus terminal diagnostic persistence.

V2 fixes two proven production defects:
- a directory Scope-Files entry is a bounded subtree, so Codex may create a
  requested regular file below that directory; and
- Stage 01B terminal failures must persist their sanitized reason on the task
  file, not only in a side log, so Telegram/status diagnostics stay useful.

No authority is widened: the underlying isolated workspace, scope validator,
required-check allowlist, branch handling, patch application and rollback are
still the original hardened implementation.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import codex_stage01b_runner as legacy

_ORIGINAL_BUILD_INPUT = legacy.build_codex_implementation_input
_ORIGINAL_PROCESS_ONE = legacy.process_one
_ORIGINAL_SELF_TEST = legacy.run_self_test

_DIRECTORY_SCOPE_RULE = (
    "2. Modify only paths allowed by Scope-Files. A Scope-Files entry may be "
    "either a file or a directory. When it is a directory, you may create, "
    "modify, or delete regular files beneath that directory only when the "
    "task requires it. Never write outside the declared scoped path."
)
_OLD_SCOPE_RULE = (
    "2. Modify only files already present in this workspace or explicitly "
    "named by Scope-Files."
)
_OLD_FINAL_DIRECTIVE = (
    "Resolve the requested defects in actual source files and executable tests."
)
_NEW_FINAL_DIRECTIVE = (
    "Implement the requested task in the appropriate scoped files: source, "
    "tests, configuration, or documentation as applicable. If the task "
    "explicitly requests a new file beneath a scoped directory, create that "
    "file."
)
_TASK_ID_FROM_LOG = re.compile(r"^(?P<task>.+)-01B-\d{8}T\d{6}Z\.log$")


def build_codex_implementation_input_v2(bundle: str) -> str:
    """Clarify directory-scope semantics without changing the security boundary."""
    prompt = _ORIGINAL_BUILD_INPUT(bundle)
    if _OLD_SCOPE_RULE not in prompt:
        raise legacy.CodexPolicyError(
            "BLOCKED_CODEX_POLICY: expected Stage 01B scope rule is missing"
        )
    if _OLD_FINAL_DIRECTIVE not in prompt:
        raise legacy.CodexPolicyError(
            "BLOCKED_CODEX_POLICY: expected Stage 01B execution directive is missing"
        )
    prompt = prompt.replace(_OLD_SCOPE_RULE, _DIRECTORY_SCOPE_RULE, 1)
    prompt = prompt.replace(_OLD_FINAL_DIRECTIVE, _NEW_FINAL_DIRECTIVE, 1)
    return prompt


def _reason_from_log(log_path: Path) -> str:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    detail = " | ".join(line.strip() for line in lines[:4] if line.strip())
    detail = legacy.core.orch.redact(detail)
    detail = re.sub(r"[\x00-\x1f\x7f]+", " ", detail).strip()
    return detail[:700]


def _write_terminal_reason(task_path: Path, field: str, reason: str) -> None:
    if not reason:
        return
    try:
        original = task_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        return
    if re.search(rf"(?mi)^\s*{re.escape(field)}\s*:", original):
        return
    text = original
    if text and not text.endswith("\n"):
        text += "\n"
    text += f"{field}: {reason}\n"

    fd, temporary = tempfile.mkstemp(prefix=".ai-prof-terminal-", dir=task_path.parent)
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, task_path)
    finally:
        temp.unlink(missing_ok=True)


def persist_new_terminal_reasons(paths, before_logs: set[Path]) -> None:
    """Copy new Stage 01B failure evidence into the matching terminal task."""
    new_logs = sorted(set(paths.logs.glob("*-01B-*.log")) - before_logs)
    for log_path in new_logs:
        match = _TASK_ID_FROM_LOG.fullmatch(log_path.name)
        if not match:
            continue
        task_id = match.group("task")
        reason = _reason_from_log(log_path)
        failed = paths.failed / f"{task_id}.md"
        blocked = paths.blocked / f"{task_id}.md"
        if failed.is_file():
            _write_terminal_reason(failed, "Failure-Reason", reason)
        elif blocked.is_file():
            _write_terminal_reason(blocked, "Blocked-Reason", reason)


def process_one_v2(paths) -> int:
    before_logs = set(paths.logs.glob("*-01B-*.log"))
    rc = _ORIGINAL_PROCESS_ONE(paths)
    if rc != 0:
        persist_new_terminal_reasons(paths, before_logs)
    return rc


def run_self_test_v2(root: Path) -> int:
    _ORIGINAL_SELF_TEST(root)
    prompt = build_codex_implementation_input_v2(
        "Scope-Files: docs\nInstructions: create docs/AI_PROF_E2E_SMOKE.md"
    )
    if "directory" not in prompt or "create that file" not in prompt:
        raise RuntimeError("SELF_TEST_CODEX_V2_DIRECTORY_SCOPE_PROMPT_FAILED")
    if _OLD_SCOPE_RULE in prompt or _OLD_FINAL_DIRECTIVE in prompt:
        raise RuntimeError("SELF_TEST_CODEX_V2_STALE_PROMPT_FAILED")
    print("STAGE_01B_CODEX_V2_SELF_TEST_PASS")
    return 0


def main() -> int:
    # Patch only the two V2 seams. The legacy module still owns CLI parsing,
    # locking, sandboxing and queue execution.
    legacy.build_codex_implementation_input = build_codex_implementation_input_v2
    legacy.process_one = process_one_v2
    legacy.run_self_test = run_self_test_v2
    return legacy.main()


if __name__ == "__main__":
    raise SystemExit(main())
