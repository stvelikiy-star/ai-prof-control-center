#!/usr/bin/env python3
"""Real isolated Codex smoke test for creating one previously absent file.

This never opens the AK BERMET repository. It uses only a temporary Stage 01B
workspace and the production Codex CLI policy, so we can prove new-file tool
behavior before submitting another Telegram task.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import codex_stage01b_runner as base
import codex_stage01b_runner_v2 as v2

MARKER = "STAGE_01B_CODEX_NEW_FILE_SMOKE_PASS"
RELATIVE = "docs/AI_PROF_NEW_FILE_SMOKE.md"
EXPECTED = "AK BERMET Codex new-file smoke PASS\n"


def run() -> int:
    # Install only V2's model-facing prompt contract; all sandbox/security
    # functions remain the hardened Stage 01B implementation.
    base.build_codex_implementation_input = v2.build_codex_implementation_input_v2

    with tempfile.TemporaryDirectory(prefix="ai-prof-claude-") as temp_root:
        root = Path(temp_root)
        workspace = root / "workspace"
        scratch = root / "scratch"
        workspace.mkdir()
        scratch.mkdir()
        bundle = "\n".join(
            [
                "# TASK",
                "Task-ID: AK_BERMET_NEW_FILE_SMOKE",
                f"Scope-Files: {RELATIVE}",
                "Instructions: Create the exact scoped file "
                f"{RELATIVE} with the entire content exactly: "
                "AK BERMET Codex new-file smoke PASS followed by one newline. "
                "Do not create or modify any other task file.",
            ]
        )
        result = base.invoke_codex(bundle, workspace, scratch / "unused.json")
        if result.returncode != 0:
            detail = f"{result.stdout or ''}\n{result.stderr or ''}".strip()[:4000]
            raise RuntimeError(
                f"CODEX_NEW_FILE_SMOKE_FAILED: codex exited {result.returncode}: {detail}"
            )

        entry = base.core.ScopeEntry(
            relative=RELATIVE,
            absolute=Path("/nonexistent-smoke-target"),
            is_dir=False,
            exists=False,
        )
        base.audit_workspace_integrity_with_codex_normalization(workspace, [entry])

        target = workspace / RELATIVE
        if not target.is_file():
            stdout = (result.stdout or "").strip()[:4000]
            stderr = (result.stderr or "").strip()[:4000]
            raise RuntimeError(
                "CODEX_NEW_FILE_SMOKE_FAILED: Codex returned success but did not "
                f"create {RELATIVE}; stdout={stdout!r}; stderr={stderr!r}"
            )
        actual = target.read_text(encoding="utf-8")
        if actual != EXPECTED:
            raise RuntimeError(
                f"CODEX_NEW_FILE_SMOKE_FAILED: unexpected content: {actual!r}"
            )
        remaining = sorted(
            str(path.relative_to(workspace))
            for path in workspace.rglob("*")
            if path.is_file()
        )
        if remaining != [RELATIVE]:
            raise RuntimeError(
                f"CODEX_NEW_FILE_SMOKE_FAILED: unexpected files: {remaining!r}"
            )
    print(MARKER)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
