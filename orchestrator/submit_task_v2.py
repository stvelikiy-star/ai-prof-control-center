#!/usr/bin/env python3
"""AI PROF task intake V2: allow missing scoped path tails safely.

The original intake validator remains authoritative for lexical checks,
allowlist containment and every existing filesystem component. V2 changes
only one proven incompatibility with the hardened Stage 01B runner: when the
original validator rejects an otherwise valid allowlisted scope solely because
a parent directory does not yet exist, the missing tail is allowed. Stage 01B
will later create that tail inside its isolated, scope-validated workspace.

All other intake errors remain fail-closed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import submit_task as legacy

_ORIGINAL_VALIDATE_SCOPE_PATH = legacy.validate_scope_path
MISSING_PARENT_PREFIX = "missing parent directory in scope path: "
SELF_TEST_MARKER = "TASK_INTAKE_V2_SELF_TEST_PASS"


def validate_scope_path_v2(project: Path, raw: str, allowed_patterns: list[str]) -> str:
    """Allow only the original validator's missing-parent failure class.

    Reaching that exact error proves the original validator already accepted
    the path syntax and allowlist and inspected every existing prefix component
    without encountering a symlink, non-directory parent, special file or
    filesystem access error.
    """
    try:
        return _ORIGINAL_VALIDATE_SCOPE_PATH(project, raw, allowed_patterns)
    except legacy.IntakeError as exc:
        if str(exc) == f"{MISSING_PARENT_PREFIX}{raw}":
            return raw
        raise


def run_self_test_v2() -> int:
    import tempfile

    legacy.run_self_test()
    with tempfile.TemporaryDirectory(prefix="ai-prof-intake-v2-") as temp:
        project = Path(temp) / "project"
        project.mkdir()

        accepted = validate_scope_path_v2(
            project,
            "docs/nested/new-file.md",
            ["docs/**"],
        )
        if accepted != "docs/nested/new-file.md":
            raise RuntimeError("SELF_TEST_V2_MISSING_TAIL_REJECTED")

        for bad in ("../escape.md", "/etc/passwd", r"docs\\escape.md"):
            try:
                validate_scope_path_v2(project, bad, ["docs/**"])
            except legacy.IntakeError:
                pass
            else:
                raise RuntimeError(f"SELF_TEST_V2_UNSAFE_PATH_ACCEPTED: {bad}")

        outside = Path(temp) / "outside"
        outside.write_text("x\n", encoding="utf-8")
        docs = project / "docs"
        docs.mkdir()
        (docs / "link").symlink_to(outside)
        try:
            validate_scope_path_v2(project, "docs/link/new.md", ["docs/**"])
        except legacy.IntakeError:
            pass
        else:
            raise RuntimeError("SELF_TEST_V2_SYMLINK_PREFIX_ACCEPTED")

    print(SELF_TEST_MARKER)
    return 0


def main() -> int:
    legacy.validate_scope_path = validate_scope_path_v2
    if "--self-test-v2" in sys.argv:
        sys.argv.remove("--self-test-v2")
        return run_self_test_v2()
    return legacy.main()


if __name__ == "__main__":
    raise SystemExit(main())
