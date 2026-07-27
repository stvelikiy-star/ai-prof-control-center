from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "orchestrator" / "submit_task.py"
SPEC = importlib.util.spec_from_file_location("ai_prof_submit_task", MODULE_PATH)
submit = importlib.util.module_from_spec(SPEC)
if SPEC.loader is None:
    raise RuntimeError("Cannot load submit_task")
sys.modules[SPEC.name] = submit
SPEC.loader.exec_module(submit)


def init_project(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "develop"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("pilot\n", encoding="utf-8")
    (path / "docs").mkdir()
    (path / "tests").mkdir()
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=path, check=True)


class SubmitTaskTests(unittest.TestCase):
    def test_production_registry_contains_real_and_pilot_ak_bermet_projects(self):
        root = MODULE_PATH.parents[1]
        registry = json.loads(
            (root / "orchestrator/projects.json").read_text(encoding="utf-8"),
        )
        self.assertEqual(registry["version"], 1)
        projects = {item["project_id"]: item for item in registry["projects"]}
        self.assertEqual(projects["ai-prof-pilot"], {
            "project_id": "ai-prof-pilot",
            "path": "/home/agent/projects/ai-prof-pilot-runtime",
            "base_branch": "develop",
            "work_prefixes": ["feature/", "fix/"],
            "allowed_scope": ["README.md", "docs/**", "tests/**"],
            "agent_context": "agents/ai-prof-pilot",
            "allow_commits": False,
            "allow_push": False,
            "allow_merge": False,
            "allow_deployment": False,
        })
        self.assertEqual(projects["ak-bermet"]["path"], "/home/agent/projects/ak-bermet")
        self.assertEqual(
            projects["ak-bermet-pilot"]["path"],
            "/home/agent/projects/ak-bermet-agent-pilot",
        )
        self.assertIn("src/**", projects["ak-bermet"]["allowed_scope"])
        self.assertIn("tests/**", projects["ak-bermet"]["allowed_scope"])
        self.assertIn("docs/**", projects["ak-bermet"]["allowed_scope"])
        self.assertIn("supabase/migrations/**", projects["ak-bermet"]["allowed_scope"])
        self.assertNotIn("**", projects["ak-bermet"]["allowed_scope"])
        self.assertEqual(
            projects["ak-bermet"]["code_required_commands"],
            ["git", "python3", "node", "npm", "npx"],
        )
        self.assertEqual(projects["ak-bermet"]["code_toolchain"], "nvm-node")

    def test_ak_bermet_task_metadata_declares_node_toolchain_and_checks(self):
        project = {
            "path": "/home/agent/projects/ak-bermet",
            "base_branch": "develop",
            "agent_context": "agents/ak-bermet",
            "code_required_commands": ["git", "python3", "node", "npm", "npx"],
            "code_required_checks": [
                "npm run lint", "npx tsc --noEmit", "npm test", "npm run build",
            ],
        }
        text = submit.render_task(
            project, "AK_BERMET_TEST", "Fix", "Fix safely",
            "fix/test", ["src"],
        )
        self.assertIn("Required-Commands: git, python3, node, npm, npx", text)
        self.assertIn(
            "Required-Checks: npm run lint, npx tsc --noEmit, npm test, npm run build",
            text,
        )
        self.assertEqual(submit.SCOPE_COUNT_LIMIT, 20)

    def make_root(self, parent: Path) -> tuple[Path, Path]:
        root = parent / "control"
        project = parent / "pilot"
        root.mkdir()
        init_project(project)
        (root / "orchestrator").mkdir()
        context = root / "agents/pilot"
        context.mkdir(parents=True)
        registry = {
            "version": 1,
            "projects": [{
                "project_id": "pilot",
                "path": str(project),
                "base_branch": "develop",
                "work_prefixes": ["feature/", "fix/"],
                "allowed_scope": ["README.md", "docs/**", "tests/**"],
                "agent_context": "agents/pilot",
                "allow_commits": False,
                "allow_push": False,
                "allow_merge": False,
                "allow_deployment": False,
            }],
        }
        (root / "orchestrator/projects.json").write_text(
            json.dumps(registry), encoding="utf-8",
        )
        return root, project

    def test_registry_validates_and_disables_all_release_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _project = self.make_root(Path(tmp))
            projects = submit.read_registry(root)
            self.assertEqual(set(projects), {"pilot"})
            item = projects["pilot"]
            for key in ("allow_commits", "allow_push", "allow_merge", "allow_deployment"):
                self.assertIs(item[key], False)

    def test_scope_security_rejections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, project = self.make_root(Path(tmp))
            outside = Path(tmp) / "outside"
            outside.write_text("x", encoding="utf-8")
            (project / "docs/link").symlink_to(outside)
            fifo = project / "tests/fifo"
            os.mkfifo(fifo)
            bad = [
                "../README.md", "/etc/passwd", r"docs\file.md", "docs/../README.md",
                "docs/link", "tests/fifo", "other.txt", "docs/missing/file.md",
                "docs/\x00bad",
            ]
            for value in bad:
                with self.subTest(value=value), self.assertRaises(submit.IntakeError):
                    submit.validate_scope_path(
                        project, value, ["README.md", "docs/**", "tests/**"],
                    )

    def test_scope_limits_and_text_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            _root, project = self.make_root(Path(tmp))
            with self.assertRaises(submit.IntakeError):
                submit.validate_scope(
                    project, ["README.md"] * (submit.SCOPE_COUNT_LIMIT + 1), ["README.md"],
                )
            with self.assertRaises(submit.IntakeError):
                submit.validate_text("title", "x" * 121, submit.TITLE_LIMIT)
            with self.assertRaises(submit.IntakeError):
                submit.validate_text("instructions", "line1\nline2", submit.INSTRUCTION_LIMIT)

    def test_atomic_create_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "pending/TASK_001.md"
            submit.atomic_create(destination, "first")
            with self.assertRaises(submit.IntakeError):
                submit.atomic_create(destination, "second")
            self.assertEqual(destination.read_text(encoding="utf-8"), "first")

    def test_rendered_task_is_stage_01a_schema_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, project = self.make_root(Path(tmp))
            item = submit.read_registry(root)["pilot"]
            text = submit.render_task(
                item, "PILOT_001", "Title", "Instruction", "feature/task", ["README.md"],
            )
            task = root / "task.md"
            task.write_text(text, encoding="utf-8")
            orchestrator_path = MODULE_PATH.parent / "orchestrator.py"
            spec = importlib.util.spec_from_file_location("intake_test_orch", orchestrator_path)
            orch = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = orch
            spec.loader.exec_module(orch)
            values, _ = orch.parse_task(task)
            self.assertEqual(values["Task-ID"], "PILOT_001")
            self.assertIn("Scope-Files: README.md", text)
            self.assertEqual(values["Scope"], "Only the approved Scope-Files listed below")
            self.assertIn("Instructions: Instruction", text)
            self.assertIn("Execution-Mode: code", text)
            self.assertIn("Operation-Profile: none", text)
            self.assertNotIn("Scope: Instruction\n", text)

    def test_create_dry_run_and_real_queue_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _project = self.make_root(Path(tmp))
            base = [
                "submit_task.py", "--root", str(root), "--state-root", str(root),
                "--json", "create",
                "--project", "pilot", "--title", "Title",
                "--instructions", "Instruction", "--work-branch", "feature/task",
                "--scope", "README.md",
            ]
            with mock.patch.object(sys, "argv", base + ["--dry-run"]):
                self.assertEqual(submit.main(), 0)
            self.assertFalse(list((root / "queue/pending").glob("*.md")))
            with mock.patch.object(sys, "argv", base):
                self.assertEqual(submit.main(), 0)
            tasks = list((root / "queue/pending").glob("*.md"))
            self.assertEqual(len(tasks), 1)

    def test_list_show_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _project = self.make_root(Path(tmp))
            task_id = "PILOT_TASK_001"
            path = root / "queue/pending" / f"{task_id}.md"
            submit.atomic_create(path, f"Task-ID: {task_id}\n")
            self.assertEqual(submit.list_tasks(root), [{"task_id": task_id, "queue": "pending"}])
            queue, found = submit.locate_task(root, task_id)
            self.assertEqual((queue, found), ("pending", path))
            submit.move_cancel(root, task_id)
            self.assertTrue((root / "queue/cancelled" / path.name).exists())

    def test_self_test(self):
        self.assertEqual(submit.run_self_test(), 0)

    def test_operations_intake_rejects_unknown_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _project = self.make_root(Path(tmp))
            argv = [
                "submit_task.py", "--root", str(root), "--state-root", str(root),
                "create", "--project", "pilot", "--title", "Title",
                "--instructions", "Instruction", "--work-branch", "feature/task",
                "--scope", "README.md", "--execution-mode", "operations",
                "--operation-profile", "unknown",
            ]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(submit.main(), 2)


if __name__ == "__main__":
    unittest.main()
