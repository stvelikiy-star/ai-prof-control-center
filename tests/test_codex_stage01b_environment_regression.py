from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODULE = (
    Path(__file__).resolve().parents[1]
    / "orchestrator"
    / "codex_stage01b_runner.py"
)

SPEC = importlib.util.spec_from_file_location(
    "ai_prof_codex_stage01b_env_regression",
    MODULE,
)
runner = importlib.util.module_from_spec(SPEC)

if SPEC.loader is None:
    raise RuntimeError("Cannot load codex_stage01b_runner")

sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


class CodexStage01BEnvironmentRegressionTests(unittest.TestCase):

    def make_workspace(self, root: Path) -> Path:
        sandbox_root = root / "ai-prof-claude-regression"
        workspace = sandbox_root / "workspace"
        workspace.mkdir(parents=True)
        return workspace

    def test_codex_environment_disables_python_bytecode(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))

            env = runner.build_codex_environment(workspace)

            self.assertEqual(
                env.get("PYTHONDONTWRITEBYTECODE"),
                "1",
            )

    def test_python_import_does_not_create_pycache_in_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))

            (workspace / "probe.py").write_text(
                "VALUE = 42\n",
                encoding="utf-8",
            )

            env = runner.build_codex_environment(workspace)

            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys;"
                        f"sys.path.insert(0, {str(workspace)!r});"
                        "import probe;"
                        "assert probe.VALUE == 42"
                    ),
                ],
                cwd=workspace,
                env=env,
                text=True,
                capture_output=True,
                timeout=20,
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr,
            )

            self.assertFalse(
                any(workspace.rglob("__pycache__")),
                "Python generated __pycache__ inside Codex workspace",
            )

            self.assertFalse(
                any(workspace.rglob("*.pyc")),
                "Python generated .pyc inside Codex workspace",
            )

    def test_scope_guard_still_rejects_manual_pycache(self):
        with tempfile.TemporaryDirectory() as project_tmp, \
             tempfile.TemporaryDirectory() as workspace_tmp:

            project = Path(project_tmp)

            approved = project / "orchestrator" / "probe.py"
            approved.parent.mkdir(parents=True)
            approved.write_text(
                "VALUE = 42\n",
                encoding="utf-8",
            )

            entries = runner.core.resolve_scope_entries(
                project,
                ["orchestrator/probe.py"],
            )

            workspace = (
                Path(workspace_tmp)
                / "ai-prof-claude-regression"
                / "workspace"
            )
            workspace.mkdir(parents=True)

            runner.core.build_isolated_workspace(
                workspace,
                project,
                entries,
            )

            pycache = (
                workspace
                / "orchestrator"
                / "__pycache__"
            )
            pycache.mkdir()

            (pycache / "evil.cpython-312.pyc").write_bytes(
                b"outside-approved-scope"
            )

            with self.assertRaises(
                runner.core.ScopeAccessError
            ):
                runner.core.audit_workspace_integrity(
                    workspace,
                    entries,
                )

    def test_real_unexpected_file_still_rejected(self):
        with tempfile.TemporaryDirectory() as project_tmp, \
             tempfile.TemporaryDirectory() as workspace_tmp:

            project = Path(project_tmp)

            approved = project / "approved.py"
            approved.write_text("VALUE = 1\n", encoding="utf-8")

            entries = runner.core.resolve_scope_entries(
                project,
                ["approved.py"],
            )

            workspace = (
                Path(workspace_tmp)
                / "ai-prof-claude-regression"
                / "workspace"
            )
            workspace.mkdir(parents=True)

            runner.core.build_isolated_workspace(
                workspace,
                project,
                entries,
            )

            (workspace / "unexpected.txt").write_text(
                "must remain forbidden\n",
                encoding="utf-8",
            )

            with self.assertRaises(
                runner.core.ScopeAccessError
            ):
                runner.core.audit_workspace_integrity(
                    workspace,
                    entries,
                )


if __name__ == "__main__":
    unittest.main()
